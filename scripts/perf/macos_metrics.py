#!/usr/bin/env python3
"""Small macOS process-footprint sampler with no third-party dependencies.

``ps`` RSS is a poor proxy for the memory pressure attributed to a macOS
process.  This module reads ``ri_phys_footprint`` from ``proc_pid_rusage()``,
which is the kernel accounting value used for memory-footprint comparisons.
It is intentionally standalone so it can measure Anki, the Semantic worker,
or any other PID without importing the add-on or its native dependencies.
"""

from __future__ import annotations

import argparse
import ctypes
from dataclasses import asdict, dataclass
import json
import platform
import statistics
import time
from typing import Sequence


_RUSAGE_INFO_V0 = 0


class _RusageInfoV0(ctypes.Structure):
    _fields_ = [
        ("ri_uuid", ctypes.c_uint8 * 16),
        ("ri_user_time", ctypes.c_uint64),
        ("ri_system_time", ctypes.c_uint64),
        ("ri_pkg_idle_wkups", ctypes.c_uint64),
        ("ri_interrupt_wkups", ctypes.c_uint64),
        ("ri_pageins", ctypes.c_uint64),
        ("ri_wired_size", ctypes.c_uint64),
        ("ri_resident_size", ctypes.c_uint64),
        ("ri_phys_footprint", ctypes.c_uint64),
        ("ri_proc_start_abstime", ctypes.c_uint64),
        ("ri_proc_exit_abstime", ctypes.c_uint64),
    ]


@dataclass(frozen=True)
class ProcessMetrics:
    pid: int
    monotonic_seconds: float
    user_cpu_seconds: float
    system_cpu_seconds: float
    resident_bytes: int
    physical_footprint_bytes: int
    wired_bytes: int
    pageins: int

    @property
    def total_cpu_seconds(self) -> float:
        return self.user_cpu_seconds + self.system_cpu_seconds

    def as_dict(self) -> dict[str, int | float]:
        return asdict(self)


def _libproc() -> ctypes.CDLL:
    if platform.system() != "Darwin":
        raise RuntimeError("proc_pid_rusage metrics are available only on macOS.")
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    library.proc_pid_rusage.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
    ]
    library.proc_pid_rusage.restype = ctypes.c_int
    return library


def sample_process(pid: int) -> ProcessMetrics:
    """Return one kernel-accounted resource sample for a live process."""

    process_id = int(pid)
    if process_id <= 0:
        raise ValueError("pid must be positive")
    usage = _RusageInfoV0()
    result = _libproc().proc_pid_rusage(
        process_id,
        _RUSAGE_INFO_V0,
        ctypes.byref(usage),
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise ProcessLookupError(error_number, f"could not sample pid {process_id}")
    # Darwin reports rusage CPU duration in nanoseconds.
    nanoseconds = 1_000_000_000.0
    return ProcessMetrics(
        pid=process_id,
        monotonic_seconds=time.monotonic(),
        user_cpu_seconds=float(usage.ri_user_time) / nanoseconds,
        system_cpu_seconds=float(usage.ri_system_time) / nanoseconds,
        resident_bytes=int(usage.ri_resident_size),
        physical_footprint_bytes=int(usage.ri_phys_footprint),
        wired_bytes=int(usage.ri_wired_size),
        pageins=int(usage.ri_pageins),
    )


def sample_for(
    pid: int,
    *,
    duration_seconds: float,
    interval_seconds: float = 0.05,
) -> list[ProcessMetrics]:
    """Sample a PID for a bounded interval, stopping if the process exits."""

    duration = max(0.0, float(duration_seconds))
    interval = max(0.005, float(interval_seconds))
    deadline = time.monotonic() + duration
    output: list[ProcessMetrics] = []
    while True:
        try:
            output.append(sample_process(pid))
        except ProcessLookupError:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval, remaining))
    return output


def summarize(samples: Sequence[ProcessMetrics]) -> dict[str, int | float]:
    """Summarize footprint and CPU use across ordered samples."""

    if not samples:
        raise ValueError("at least one process sample is required")
    footprints = [sample.physical_footprint_bytes for sample in samples]
    residents = [sample.resident_bytes for sample in samples]
    first = samples[0]
    last = samples[-1]
    wall_seconds = max(0.0, last.monotonic_seconds - first.monotonic_seconds)
    cpu_seconds = max(0.0, last.total_cpu_seconds - first.total_cpu_seconds)
    cpu_percent_one_core = (
        (cpu_seconds / wall_seconds) * 100.0 if wall_seconds > 0 else 0.0
    )
    return {
        "pid": first.pid,
        "samples": len(samples),
        "physical_footprint_min_bytes": min(footprints),
        "physical_footprint_median_bytes": int(statistics.median(footprints)),
        "physical_footprint_max_bytes": max(footprints),
        "resident_min_bytes": min(residents),
        "resident_median_bytes": int(statistics.median(residents)),
        "resident_max_bytes": max(residents),
        "wall_seconds": wall_seconds,
        "cpu_seconds": cpu_seconds,
        "cpu_percent_one_core": cpu_percent_one_core,
    }


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Measure a process with macOS proc_pid_rusage()."
    )
    parser.add_argument("pid", type=int)
    parser.add_argument("--duration", type=float, default=1.0)
    parser.add_argument("--interval", type=float, default=0.05)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    result = summarize(
        sample_for(
            arguments.pid,
            duration_seconds=arguments.duration,
            interval_seconds=arguments.interval,
        )
    )
    if arguments.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        mib = 1024 * 1024
        print(
            f"pid {result['pid']}: physical footprint "
            f"median {result['physical_footprint_median_bytes'] / mib:.1f} MiB, "
            f"max {result['physical_footprint_max_bytes'] / mib:.1f} MiB; "
            f"CPU {result['cpu_percent_one_core']:.2f}% of one core"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
