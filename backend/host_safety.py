"""Narrow containment for a poisoned Anki Rust collection backend.

PyO3 deliberately exposes a Rust panic as ``PanicException(BaseException)``.
Anki's background-operation wrappers re-raise that value on the GUI thread
instead of sending it to an add-on's ordinary failure callback.  Smart Search
recognizes only that exact host exception and converts it to a normal,
restart-required error.  Other ``BaseException`` subclasses keep their normal
process semantics.

The quarantine is process-wide and sticky.  A poisoned Rust mutex cannot be
made safe by retrying from another Smart Search component, so no add-on
collection access is allowed again until Anki starts a fresh process.
"""

from __future__ import annotations

from collections.abc import Callable
import threading
from typing import TypeVar


HOST_BACKEND_RESTART_MESSAGE = (
    "Anki's collection service stopped. Restart Anki before using Smart Search."
)


class HostBackendUnavailable(RuntimeError):
    """A prior Rust panic made Anki's collection unsafe for this process."""


_HOST_BACKEND_QUARANTINED = threading.Event()
_Result = TypeVar("_Result")


def is_pyo3_panic(error: BaseException) -> bool:
    """Return whether ``error`` is PyO3's exact Rust-panic wrapper."""

    error_type = type(error)
    return (
        error_type.__module__ == "pyo3_runtime"
        and error_type.__name__ == "PanicException"
    )


def host_backend_quarantined() -> bool:
    """Return whether Smart Search must avoid all collection access."""

    return _HOST_BACKEND_QUARANTINED.is_set()


def require_host_backend() -> None:
    """Fail normally when a prior PyO3 panic opened the circuit breaker."""

    if host_backend_quarantined():
        raise HostBackendUnavailable(HOST_BACKEND_RESTART_MESSAGE)


def contain_host_backend_panic(callback: Callable[[], _Result]) -> _Result:
    """Run ``callback`` without letting PyO3's panic wrapper reach Anki UI.

    A newly observed PyO3 panic opens the process-wide circuit and becomes an
    ordinary ``RuntimeError``.  SystemExit, KeyboardInterrupt, GeneratorExit,
    and unknown host ``BaseException`` values are deliberately re-raised.
    """

    require_host_backend()
    try:
        return callback()
    except BaseException as error:
        if not is_pyo3_panic(error):
            raise
        _HOST_BACKEND_QUARANTINED.set()
        raise HostBackendUnavailable(HOST_BACKEND_RESTART_MESSAGE) from None


def _reset_host_backend_quarantine_for_tests() -> None:
    """Reset process state only for isolated unit tests."""

    _HOST_BACKEND_QUARANTINED.clear()
