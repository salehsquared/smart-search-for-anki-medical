from __future__ import annotations

import os
from pathlib import Path
import platform
import tempfile
import unittest

from scripts.perf.benchmark_semantic_worker import run_benchmark


_MODEL_ENVIRONMENT_VARIABLE = "SMART_SEARCH_REAL_MODEL_DIR"
_SUPPORTED_PLATFORM = (
    platform.system() == "Darwin"
    and platform.machine().casefold() in {"arm64", "aarch64"}
)


@unittest.skipUnless(_SUPPORTED_PLATFORM, "real worker requires Apple silicon")
@unittest.skipUnless(
    os.environ.get(_MODEL_ENVIRONMENT_VARIABLE),
    f"set {_MODEL_ENVIRONMENT_VARIABLE} to run the real worker integration test",
)
class RealSemanticWorkerIntegrationTests(unittest.TestCase):
    def test_real_worker_is_deterministic_isolated_bounded_and_reaped(self) -> None:
        model_dir = Path(os.environ[_MODEL_ENVIRONMENT_VARIABLE])
        cycles = max(2, int(os.environ.get("SMART_SEARCH_REAL_CYCLES", "2")))
        with tempfile.TemporaryDirectory(prefix="smart-search-real-test-") as root:
            result = run_benchmark(
                model_dir=model_dir,
                data_root=Path(root) / "data",
                cycles=cycles,
                idle_seconds=0.2,
                host_reference_mib=300.0,
                max_worker_mib=192.0,
                max_host_overhead_percent=15.0,
                vector_index_dir=(
                    Path(os.environ["SMART_SEARCH_REAL_VECTOR_INDEX_DIR"])
                    if os.environ.get("SMART_SEARCH_REAL_VECTOR_INDEX_DIR")
                    else None
                ),
            )

        self.assertTrue(result["passed"], result)
        self.assertTrue(result["checks"]["all_workers_reaped"])
        self.assertTrue(
            result["checks"]["native_inference_modules_not_loaded_in_host"]
        )
        self.assertTrue(result["checks"]["host_vector_runtime_is_numpy_only"])
        self.assertTrue(result["checks"]["host_vector_search_round_trip"])
        self.assertLessEqual(result["deterministic_max_abs_difference"], 1e-6)


if __name__ == "__main__":
    unittest.main()
