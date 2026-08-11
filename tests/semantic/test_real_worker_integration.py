from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest

_MODEL_ENVIRONMENT_VARIABLE = "SMART_SEARCH_REAL_MODEL_DIR"
_BENCHMARK = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "perf"
    / "benchmark_semantic_worker.py"
)
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
            output = Path(root) / "result.json"
            command = [
                sys.executable,
                "-I",
                str(_BENCHMARK),
                "--model-dir",
                str(model_dir),
                "--data-root",
                str(Path(root) / "data"),
                "--cycles",
                str(cycles),
                "--idle-seconds",
                "0.2",
                "--host-reference-mib",
                "300",
                "--max-worker-mib",
                "224",
                "--max-host-overhead-percent",
                "15",
                "--json-output",
                str(output),
            ]
            vector_index = os.environ.get(
                "SMART_SEARCH_REAL_VECTOR_INDEX_DIR"
            )
            if vector_index:
                command.extend(("--vector-index-dir", vector_index))
            environment = os.environ.copy()
            environment.pop("PYTHONPATH", None)
            environment["PYTHONNOUSERSITE"] = "1"
            completed = subprocess.run(
                command,
                cwd=Path(root),
                env=environment,
                capture_output=True,
                text=True,
                timeout=900,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + "\n" + completed.stderr,
            )
            result = json.loads(output.read_text(encoding="utf-8"))

        self.assertTrue(result["passed"], result)
        self.assertTrue(result["checks"]["all_workers_reaped"])
        self.assertTrue(
            result["checks"]["native_semantic_modules_not_loaded_in_host"]
        )
        self.assertTrue(result["checks"]["worker_vector_round_trip"])
        self.assertFalse(result["native_modules_after"]["numpy"])
        self.assertLessEqual(result["deterministic_max_abs_difference"], 1e-6)


if __name__ == "__main__":
    unittest.main()
