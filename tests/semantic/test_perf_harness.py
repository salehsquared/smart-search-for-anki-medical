from __future__ import annotations

from pathlib import Path
import types
import unittest

from scripts.perf.benchmark_semantic_worker import (
    _apply_taskpolicy_memory_limit,
    _linear_slope,
)


class PerformanceHarnessTests(unittest.TestCase):
    def test_linear_slope_reports_change_per_cycle(self) -> None:
        self.assertEqual(_linear_slope([]), 0.0)
        self.assertEqual(_linear_slope([7.0]), 0.0)
        self.assertAlmostEqual(_linear_slope([10.0, 12.0, 14.0]), 2.0)
        self.assertAlmostEqual(_linear_slope([3.0, 3.0, 3.0]), 0.0)

    def test_memory_limit_override_replaces_the_production_cap(self) -> None:
        client = types.SimpleNamespace()

        def command(
            _file_descriptor: int,
            _worker_script: Path,
            *,
            background: bool,
        ) -> list[str]:
            self.assertFalse(background)
            return [
                "/usr/sbin/taskpolicy",
                "-d",
                "throttle",
                "-m",
                "256",
                "/worker/python3",
            ]

        client._worker_command = command
        _apply_taskpolicy_memory_limit(client, 224)

        result = client._worker_command(4, Path("worker.py"), background=False)
        self.assertEqual(result.count("-m"), 1)
        self.assertEqual(result[result.index("-m") + 1], "224")


if __name__ == "__main__":
    unittest.main()
