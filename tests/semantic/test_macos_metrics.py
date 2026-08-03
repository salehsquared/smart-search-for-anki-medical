from __future__ import annotations

import os
import platform
import unittest

from scripts.perf.macos_metrics import sample_for, sample_process, summarize


@unittest.skipUnless(platform.system() == "Darwin", "macOS metrics only")
class MacOSMetricsTests(unittest.TestCase):
    def test_current_process_has_a_kernel_physical_footprint(self) -> None:
        sample = sample_process(os.getpid())

        self.assertEqual(sample.pid, os.getpid())
        self.assertGreater(sample.physical_footprint_bytes, 0)
        self.assertGreater(sample.resident_bytes, 0)
        self.assertGreaterEqual(sample.total_cpu_seconds, 0.0)

    def test_bounded_sampling_produces_a_consistent_summary(self) -> None:
        samples = sample_for(
            os.getpid(),
            duration_seconds=0.03,
            interval_seconds=0.01,
        )
        result = summarize(samples)

        self.assertGreaterEqual(result["samples"], 2)
        self.assertGreaterEqual(
            result["physical_footprint_max_bytes"],
            result["physical_footprint_median_bytes"],
        )
        self.assertGreaterEqual(result["cpu_percent_one_core"], 0.0)

    def test_missing_process_is_reported(self) -> None:
        with self.assertRaises(ProcessLookupError):
            sample_process(2**30)


if __name__ == "__main__":
    unittest.main()
