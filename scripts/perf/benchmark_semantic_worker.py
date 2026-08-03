#!/usr/bin/env python3
"""Exercise and measure the real isolated Semantic worker on macOS.

This benchmark never opens an Anki collection.  It copies an already verified
model into a temporary data directory, installs the bundled pinned runtime
there, runs real embeddings through the worker protocol, and removes the
temporary directory on exit.  The source model directory is read-only.
"""

from __future__ import annotations

import argparse
import gc
import importlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from typing import Any, Callable


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.perf.macos_metrics import (  # noqa: E402
    ProcessMetrics,
    sample_for,
    sample_process,
    summarize,
)
from semantic.manifest import MODEL_ARTIFACTS, MODEL_NAME  # noqa: E402
from semantic.model_manager import ModelManager, sha256_file  # noqa: E402
from semantic.worker_client import SemanticWorkerClient  # noqa: E402
from semantic.vector_index import VectorIndex  # noqa: E402


MIB = 1024 * 1024
_NATIVE_INFERENCE_MODULES = ("onnxruntime", "tokenizers")


def _copy_verified_model(source: Path, manager: ModelManager) -> None:
    source = source.resolve()
    manager.model_dir.mkdir(parents=True, exist_ok=True)
    for artifact in MODEL_ARTIFACTS:
        original = source / artifact.local_path
        if not original.is_file():
            raise RuntimeError(f"model artifact is missing: {original}")
        if original.stat().st_size != artifact.size:
            raise RuntimeError(f"model artifact has the wrong size: {original}")
        if sha256_file(original) != artifact.sha256:
            raise RuntimeError(f"model artifact checksum failed: {original}")
        destination = manager.model_dir / artifact.local_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, destination)
    if not manager.model_ready(verify=True):
        raise RuntimeError("the temporary model copy did not verify")


def _module_loaded(name: str) -> bool:
    return any(module == name or module.startswith(name + ".") for module in sys.modules)


def _vector_runtime_is_numpy_only(manager: ModelManager) -> bool:
    root = manager.host_vector_runtime_dir
    forbidden = ("onnxruntime", "tokenizers", "flatbuffers")
    return (
        (root / "numpy" / "__init__.py").is_file()
        and all(not (root / package).exists() for package in forbidden)
        and manager.worker_site_packages != root
    )


def _load_host_numpy(
    manager: ModelManager,
) -> tuple[Any, str, bool]:
    """Load host NumPy and prove it came from the isolated vector runtime."""

    manager.activate_vector_runtime()
    numpy = importlib.import_module("numpy")
    module_path = Path(numpy.__file__).resolve()
    try:
        module_path.relative_to(manager.host_vector_runtime_dir.resolve())
        isolated_numpy = True
    except ValueError:
        isolated_numpy = False

    return numpy, str(module_path), isolated_numpy


def _copy_vector_index(source: Path, destination: Path) -> None:
    """Snapshot a derived vector index without modifying its source."""

    source = source.resolve()
    source_database = source / "semantic.sqlite3"
    source_vectors = source / "vectors.f16"
    if not source_database.is_file() or not source_vectors.is_file():
        raise RuntimeError(f"vector index is incomplete: {source}")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_vectors, destination / "vectors.f16")
    source_uri = source_database.as_uri() + "?mode=ro"
    source_connection = sqlite3.connect(source_uri, uri=True)
    destination_connection = sqlite3.connect(destination / "semantic.sqlite3")
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


def _prepare_vector_index(
    *,
    data_root: Path,
    numpy: Any,
    source: Path | None,
) -> tuple[VectorIndex, int]:
    root = data_root / "performance-vector-index"
    if source is not None:
        _copy_vector_index(source, root)
        index = VectorIndex(root)
        return index, index.count()

    index = VectorIndex(root, dimension=3)
    index.upsert_many(
        [101, 202],
        ["first", "second"],
        numpy.asarray(
            [
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
            ],
            dtype=numpy.float32,
        ),
    )
    return index, index.count()


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vectors must have equal dimensions")
    return sum(a * b for a, b in zip(left, right))


def _linear_slope(values: list[float]) -> float:
    """Return the least-squares change per sample index."""

    if len(values) < 2:
        return 0.0
    midpoint = (len(values) - 1) / 2.0
    mean = sum(values) / len(values)
    denominator = sum((index - midpoint) ** 2 for index in range(len(values)))
    if denominator <= 0:
        return 0.0
    return sum(
        (index - midpoint) * (value - mean)
        for index, value in enumerate(values)
    ) / denominator


def _sample_while(
    client: SemanticWorkerClient,
    operation: Callable[[], Any],
) -> tuple[Any, list[ProcessMetrics]]:
    samples: list[ProcessMetrics] = []
    stop = threading.Event()

    def sampler() -> None:
        while not stop.is_set():
            with client._state_lock:
                process = client._process
            if process is not None and process.poll() is None:
                try:
                    samples.append(sample_process(process.pid))
                except ProcessLookupError:
                    pass
            stop.wait(0.005)

    thread = threading.Thread(
        target=sampler,
        name="smart-search-worker-metrics",
        daemon=True,
    )
    thread.start()
    try:
        return operation(), samples
    finally:
        stop.set()
        thread.join(timeout=2.0)


def _sample_process_while(
    pid: int,
    operation: Callable[[], Any],
) -> tuple[Any, list[ProcessMetrics]]:
    samples: list[ProcessMetrics] = []
    stop = threading.Event()

    def sampler() -> None:
        while not stop.is_set():
            try:
                samples.append(sample_process(pid))
            except ProcessLookupError:
                return
            stop.wait(0.002)

    thread = threading.Thread(
        target=sampler,
        name="smart-search-host-metrics",
        daemon=True,
    )
    thread.start()
    try:
        return operation(), samples
    finally:
        stop.set()
        thread.join(timeout=2.0)


def _process_is_gone(process: Any) -> bool:
    if process.poll() is None:
        return False
    try:
        sample_process(process.pid)
    except ProcessLookupError:
        return True
    return False


def _footprint_samples(pid: int, duration: float = 0.15) -> list[ProcessMetrics]:
    return sample_for(
        pid,
        duration_seconds=duration,
        interval_seconds=0.025,
    )


def _apply_taskpolicy_memory_limit(
    client: SemanticWorkerClient,
    limit_mib: int,
) -> None:
    """Wrap this benchmark client's launch with a macOS memory ledger cap."""

    normalized = int(limit_mib)
    if normalized <= 0:
        raise ValueError("worker memory limit must be positive")
    original = client._worker_command

    def limited_command(
        file_descriptor: int,
        worker_script: Path,
        *,
        background: bool,
    ) -> list[str]:
        command = original(
            file_descriptor,
            worker_script,
            background=background,
        )
        taskpolicy = "/usr/sbin/taskpolicy"
        if command and command[0] == taskpolicy:
            existing = list(command[1:])
            try:
                memory_flag = existing.index("-m")
            except ValueError:
                pass
            else:
                del existing[memory_flag : memory_flag + 2]
            return [taskpolicy, "-m", str(normalized)] + existing
        return [taskpolicy, "-m", str(normalized)] + command

    client._worker_command = limited_command


def run_benchmark(
    *,
    model_dir: Path,
    data_root: Path,
    cycles: int = 5,
    idle_seconds: float = 1.0,
    host_reference_mib: float = 300.0,
    max_worker_mib: float = 192.0,
    max_host_overhead_percent: float = 15.0,
    vector_index_dir: Path | None = None,
    worker_memory_limit_mib: int | None = None,
    stress_text_bytes: int = 16 * 1024,
) -> dict[str, Any]:
    """Run a bounded real-worker benchmark and return JSON-safe results."""

    if platform.system() != "Darwin" or platform.machine().casefold() not in {
        "arm64",
        "aarch64",
    }:
        raise RuntimeError("the real Semantic worker benchmark requires Apple silicon")
    if cycles < 1:
        raise ValueError("cycles must be at least one")

    manager = ModelManager(data_root, REPOSITORY_ROOT)
    _copy_verified_model(Path(model_dir), manager)
    manager.install_runtime()
    if not manager.runtime_ready():
        raise RuntimeError("the isolated runtime did not verify after installation")

    vector_numpy_only = _vector_runtime_is_numpy_only(manager)
    native_before = {
        name: _module_loaded(name) for name in _NATIVE_INFERENCE_MODULES
    }
    parent_before_samples = _footprint_samples(os.getpid())
    parent_before = summarize(parent_before_samples)
    numpy, host_numpy_path, isolated_numpy = _load_host_numpy(manager)
    gc.collect()
    parent_numpy_ready_samples = _footprint_samples(os.getpid(), duration=0.4)
    parent_numpy_ready = summarize(parent_numpy_ready_samples)
    vector_index, vector_index_count = _prepare_vector_index(
        data_root=data_root,
        numpy=numpy,
        source=Path(vector_index_dir) if vector_index_dir is not None else None,
    )
    query_vector = numpy.zeros((vector_index.dimension,), dtype=numpy.float32)
    query_vector[0] = 1.0
    gc.collect()
    parent_vector_pre_search_samples = _footprint_samples(
        os.getpid(),
        duration=0.4,
    )
    parent_vector_pre_search = summarize(parent_vector_pre_search_samples)

    def real_vector_search() -> Any:
        # Repeat enough times for the kernel sampler to observe the active
        # mmap/matrix high-water mark even on fast Apple-silicon machines.
        hits = None
        for _ in range(3):
            hits = vector_index.search(query_vector, limit=50)
        return hits

    vector_hits, vector_search_samples = _sample_process_while(
        os.getpid(),
        real_vector_search,
    )
    if not vector_search_samples:
        raise RuntimeError("host vector-search sampling produced no data")
    parent_vector_search = summarize(vector_search_samples)
    vector_round_trip = bool(
        vector_hits
        and len(vector_hits) == min(50, vector_index_count)
        and all(math.isfinite(hit.score) for hit in vector_hits)
    )
    del vector_hits
    gc.collect()
    parent_vector_released_samples = _footprint_samples(os.getpid(), duration=0.6)
    parent_vector_released = summarize(parent_vector_released_samples)

    cycle_results: list[dict[str, Any]] = []
    all_worker_samples: list[ProcessMetrics] = []
    all_exit_seconds: list[float] = []
    deterministic_max_abs_difference = 0.0
    semantic_similarity: dict[str, float] = {}
    all_workers_gone = True

    phrases = [
        "treatment of essential hypertension",
        "management of high blood pressure",
        "fracture of the distal radius",
    ]
    stress_seed = "hypertension medication renal cardiovascular monitoring "
    stress_length = max(1, int(stress_text_bytes))
    stress_texts = [
        ((stress_seed * ((stress_length // len(stress_seed)) + 1))[:stress_length])
        + str(index)
        for index in range(4)
    ]

    for cycle in range(cycles):
        client = SemanticWorkerClient(manager, REPOSITORY_ROOT)
        if worker_memory_limit_mib is not None:
            _apply_taskpolicy_memory_limit(client, worker_memory_limit_mib)
        process = None
        try:
            first, startup_samples = _sample_while(
                client,
                lambda: client.embed(phrases, background=False),
            )
            with client._state_lock:
                process = client._process
            if process is None or process.poll() is not None:
                raise RuntimeError("worker was not live after a real embedding request")

            second = client.embed(phrases, background=False)
            if len(first) != len(second):
                raise RuntimeError("worker returned an inconsistent vector count")
            for left, right in zip(first, second):
                if len(left) != len(right):
                    raise RuntimeError("worker returned an inconsistent dimension")
                for left_value, right_value in zip(left, right):
                    deterministic_max_abs_difference = max(
                        deterministic_max_abs_difference,
                        abs(float(left_value) - float(right_value)),
                    )
            for vector in first:
                norm = math.sqrt(sum(float(value) ** 2 for value in vector))
                if abs(norm - 1.0) > 1e-3:
                    raise RuntimeError("worker returned a non-normalized real vector")

            if cycle == 0:
                semantic_similarity = {
                    "related": _cosine(list(first[0]), list(first[1])),
                    "unrelated": _cosine(list(first[0]), list(first[2])),
                }

            _, active_samples = _sample_while(
                client,
                lambda: client.embed(stress_texts, background=False),
            )
            resident_samples = _footprint_samples(process.pid)
            idle_samples = sample_for(
                process.pid,
                duration_seconds=max(0.1, idle_seconds),
                interval_seconds=0.05,
            )
            worker_samples = startup_samples + active_samples + resident_samples
            if not worker_samples:
                raise RuntimeError("worker footprint sampling produced no data")
            all_worker_samples.extend(worker_samples)
            worker_summary = summarize(worker_samples)
            idle_summary = summarize(idle_samples)

            started_exit = time.monotonic()
            client.terminate_and_wait()
            exit_seconds = time.monotonic() - started_exit
            all_exit_seconds.append(exit_seconds)
            gone = _process_is_gone(process)
            all_workers_gone = all_workers_gone and gone
            host_after_termination = sample_process(os.getpid())
            cycle_results.append(
                {
                    "cycle": cycle + 1,
                    "pid": process.pid,
                    "worker": worker_summary,
                    "idle": idle_summary,
                    "exit_seconds": exit_seconds,
                    "gone_after_terminate": gone,
                    "host_after_termination": host_after_termination.as_dict(),
                }
            )
        finally:
            client.terminate_and_wait()

    gc.collect()
    parent_after_samples = _footprint_samples(os.getpid(), duration=0.4)
    parent_after = summarize(parent_after_samples)
    parent_idle_samples = sample_for(
        os.getpid(),
        duration_seconds=max(0.1, idle_seconds),
        interval_seconds=0.05,
    )
    parent_idle = summarize(parent_idle_samples)
    native_after = {
        name: _module_loaded(name) for name in _NATIVE_INFERENCE_MODULES
    }

    before_median = int(parent_before["physical_footprint_median_bytes"])
    after_median = int(parent_after["physical_footprint_median_bytes"])
    host_retained_growth = max(0, after_median - before_median)
    reference_bytes = max(1.0, float(host_reference_mib) * MIB)
    projected_host_overhead_percent = (host_retained_growth / reference_bytes) * 100.0
    vector_peak_growth = max(
        0,
        int(parent_vector_search["physical_footprint_max_bytes"]) - before_median,
    )
    vector_peak_overhead_percent = (vector_peak_growth / reference_bytes) * 100.0
    vector_released_growth = max(
        0,
        int(parent_vector_released["physical_footprint_median_bytes"])
        - before_median,
    )
    vector_released_overhead_percent = (
        vector_released_growth / reference_bytes
    ) * 100.0
    worker_max_bytes = max(
        sample.physical_footprint_bytes for sample in all_worker_samples
    )
    steady_worker_peaks = [
        float(cycle["worker"]["physical_footprint_max_bytes"])
        for cycle in cycle_results[1:]
    ]
    worker_steady_peak_slope = _linear_slope(steady_worker_peaks)
    host_cycle_footprints = [
        float(cycle["host_after_termination"]["physical_footprint_bytes"])
        for cycle in cycle_results
    ]
    host_cycle_footprint_slope = _linear_slope(host_cycle_footprints)

    checks = {
        "runtime_verified": manager.runtime_ready(),
        "host_vector_runtime_is_numpy_only": vector_numpy_only,
        "host_numpy_loaded_from_isolated_runtime": isolated_numpy,
        "host_vector_search_round_trip": vector_round_trip,
        "native_inference_modules_not_loaded_in_host": all(
            not native_after[name] for name in _NATIVE_INFERENCE_MODULES
        ),
        "native_inference_module_state_unchanged": native_before == native_after,
        "real_embeddings_are_deterministic": deterministic_max_abs_difference <= 1e-6,
        "all_workers_reaped": all_workers_gone,
        "worker_peak_within_budget": worker_max_bytes <= max_worker_mib * MIB,
        "worker_idle_cpu_below_one_percent": all(
            float(cycle["idle"]["cpu_percent_one_core"]) <= 1.0
            for cycle in cycle_results
        ),
        "host_idle_cpu_below_one_percent": (
            float(parent_idle["cpu_percent_one_core"]) <= 1.0
        ),
        "worker_exit_within_two_seconds": max(all_exit_seconds) <= 2.0,
        "projected_host_overhead_within_budget": (
            projected_host_overhead_percent <= max_host_overhead_percent
        ),
        "host_vector_peak_within_budget": (
            vector_peak_overhead_percent <= max_host_overhead_percent
        ),
        "host_vector_released_within_budget": (
            vector_released_overhead_percent <= max_host_overhead_percent
        ),
    }
    return {
        "platform": platform.platform(),
        "python": sys.version,
        "cycles": cycles,
        "model_name": MODEL_NAME,
        "limits": {
            "host_reference_mib": host_reference_mib,
            "max_host_overhead_percent": max_host_overhead_percent,
            "max_worker_mib": max_worker_mib,
            "worker_memory_limit_mib": worker_memory_limit_mib,
            "stress_text_bytes": stress_length,
        },
        "parent_before": parent_before,
        "parent_numpy_ready": parent_numpy_ready,
        "parent_vector_pre_search": parent_vector_pre_search,
        "parent_vector_search": parent_vector_search,
        "parent_vector_released": parent_vector_released,
        "parent_after": parent_after,
        "parent_idle": parent_idle,
        "host_retained_growth_bytes": host_retained_growth,
        "projected_host_overhead_percent": projected_host_overhead_percent,
        "host_vector_peak_growth_bytes": vector_peak_growth,
        "host_vector_peak_overhead_percent": vector_peak_overhead_percent,
        "host_vector_released_growth_bytes": vector_released_growth,
        "host_vector_released_overhead_percent": vector_released_overhead_percent,
        "vector_index_count": vector_index_count,
        "worker_peak_bytes": worker_max_bytes,
        "worker_steady_peak_slope_bytes_per_cycle": worker_steady_peak_slope,
        "host_cycle_footprint_slope_bytes_per_cycle": host_cycle_footprint_slope,
        "worker_exit_max_seconds": max(all_exit_seconds),
        "deterministic_max_abs_difference": deterministic_max_abs_difference,
        "semantic_similarity": semantic_similarity,
        "native_modules_before": native_before,
        "native_modules_after": native_after,
        "host_numpy_path": host_numpy_path,
        "cycle_results": cycle_results,
        "checks": checks,
        "passed": all(checks.values()),
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark the real isolated Semantic worker without Anki data."
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        required=True,
        help="Read-only directory containing the pinned model artifacts.",
    )
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--cycles", type=int, default=5)
    parser.add_argument("--idle-seconds", type=float, default=1.0)
    parser.add_argument("--host-reference-mib", type=float, default=300.0)
    parser.add_argument("--max-worker-mib", type=float, default=192.0)
    parser.add_argument("--worker-memory-limit-mib", type=int)
    parser.add_argument("--stress-text-bytes", type=int, default=16 * 1024)
    parser.add_argument("--max-host-overhead-percent", type=float, default=15.0)
    parser.add_argument(
        "--vector-index-dir",
        type=Path,
        help="Optional read-only existing vector index to snapshot and search.",
    )
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args()


def _execute(arguments: argparse.Namespace, data_root: Path) -> int:
    result = run_benchmark(
        model_dir=arguments.model_dir,
        data_root=data_root,
        cycles=arguments.cycles,
        idle_seconds=arguments.idle_seconds,
        host_reference_mib=arguments.host_reference_mib,
        max_worker_mib=arguments.max_worker_mib,
        max_host_overhead_percent=arguments.max_host_overhead_percent,
        vector_index_dir=arguments.vector_index_dir,
        worker_memory_limit_mib=arguments.worker_memory_limit_mib,
        stress_text_bytes=arguments.stress_text_bytes,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if arguments.json_output:
        arguments.json_output.parent.mkdir(parents=True, exist_ok=True)
        arguments.json_output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["passed"] else 1


def main() -> int:
    arguments = _arguments()
    if arguments.data_root is not None:
        arguments.data_root.mkdir(parents=True, exist_ok=True)
        return _execute(arguments, arguments.data_root)
    with tempfile.TemporaryDirectory(prefix="smart-search-worker-benchmark-") as root:
        return _execute(arguments, Path(root) / "data")


if __name__ == "__main__":
    raise SystemExit(main())
