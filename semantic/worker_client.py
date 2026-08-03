"""Lifecycle-safe client for the disposable Semantic inference worker."""

from __future__ import annotations

from array import array
import base64
from collections.abc import Callable, Sequence
import math
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import threading
import time
from typing import Any
import uuid

from .errors import SemanticRuntimeError, SemanticWorkerError
from .manifest import MODEL_DIMENSION, MODEL_REVISION
from .model_manager import ModelManager
from .worker_protocol import (
    MAX_TEXTS_PER_REQUEST,
    MAX_TEXT_UTF8_BYTES,
    PROTOCOL_VERSION,
    WorkerProtocolError,
    decode_from_buffer,
    encode_frame,
)


_START_TIMEOUT_SECONDS = 30.0
_REQUEST_TIMEOUT_SECONDS = 180.0
_POLL_SECONDS = 0.05
_REAP_GRACE_SECONDS = 0.5
_CLOSE_WAIT_SECONDS = 35.0
_WORKER_MEMORY_LIMIT_MIB = 256


class SemanticWorkerClient:
    """Serialize embedding requests through a killable helper process.

    The host Anki interpreter never imports ONNX Runtime or tokenizers. Native
    inference memory belongs exclusively to the helper and is reclaimed by the
    operating system when :meth:`terminate_now` ends it.
    """

    def __init__(self, manager: ModelManager, bundle_root: Path) -> None:
        self.manager = manager
        self.bundle_root = Path(bundle_root)
        self._request_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._state_changed = threading.Condition(self._state_lock)
        self._process: subprocess.Popen[bytes] | None = None
        self._socket: socket.socket | None = None
        self._worker_policy: str | None = None
        self._buffer = bytearray()
        self._request_id = 0
        self._nonce = uuid.uuid4().hex
        self._lifecycle_epoch = 0
        self._spawns_in_flight = 0
        self._reaper_threads: set[threading.Thread] = set()

    @property
    def running(self) -> bool:
        with self._state_lock:
            process = self._process
            return process is not None and process.poll() is None

    def embed(
        self,
        texts: Sequence[str],
        *,
        background: bool,
        cancel_check: Callable[[], None] | None = None,
    ) -> list[array[float]]:
        normalized = tuple(_bounded_text(text) for text in texts)
        if not normalized:
            return []
        output: list[array[float]] = []
        for start in range(0, len(normalized), MAX_TEXTS_PER_REQUEST):
            chunk = normalized[start : start + MAX_TEXTS_PER_REQUEST]
            response = self._request(
                {
                    "op": "embed",
                    "texts": chunk,
                    # One sequence per inference bounds the native ONNX
                    # high-water allocation without changing embeddings.
                    "batch_size": 1,
                },
                background=background,
                cancel_check=cancel_check,
            )
            try:
                output.extend(
                    self._decode_vectors(response, expected_count=len(chunk))
                )
            except BaseException:
                self.terminate_now()
                raise
        return output

    def warmup(
        self,
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> None:
        self.embed(
            ["medical semantic search"],
            background=True,
            cancel_check=cancel_check,
        )

    def terminate_now(self) -> bool:
        """Detach and terminate without waiting on an in-flight request lock."""

        process, sock, had_activity = self._invalidate_and_detach()
        self._stop_process(process, sock)
        if process is not None:
            self._start_reaper(process)
        return had_activity

    def terminate_and_wait(self) -> bool:
        """Terminate and reap before replacing files used by the worker."""

        process, sock, had_activity = self._invalidate_and_detach()
        self._stop_process(process, sock)
        if process is not None:
            self._reap(process)

        deadline = time.monotonic() + _CLOSE_WAIT_SECONDS
        while True:
            with self._state_changed:
                if not self._spawns_in_flight:
                    reapers = tuple(self._reaper_threads)
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    reapers = tuple(self._reaper_threads)
                    break
                self._state_changed.wait(timeout=min(0.1, remaining))
        for thread in reapers:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        return bool(had_activity or reapers)

    def _invalidate_and_detach(
        self,
    ) -> tuple[subprocess.Popen[bytes] | None, socket.socket | None, bool]:
        with self._state_changed:
            self._lifecycle_epoch += 1
            process = self._process
            sock = self._socket
            had_activity = bool(
                process is not None
                or self._spawns_in_flight
                or self._reaper_threads
            )
            self._process = None
            self._socket = None
            self._worker_policy = None
            self._buffer.clear()
            self._state_changed.notify_all()
        return process, sock, had_activity

    @staticmethod
    def _stop_process(
        process: subprocess.Popen[bytes] | None,
        sock: socket.socket | None,
    ) -> None:
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def _start_reaper(self, process: subprocess.Popen[bytes]) -> None:
        def tracked_reap() -> None:
            try:
                self._reap(process)
            finally:
                with self._state_changed:
                    self._reaper_threads.discard(threading.current_thread())
                    self._state_changed.notify_all()

        thread = threading.Thread(
            target=tracked_reap,
            name="smart-search-semantic-reaper",
            daemon=True,
        )
        with self._state_changed:
            self._reaper_threads.add(thread)
        thread.start()

    close = terminate_now

    def _request(
        self,
        payload: dict[str, Any],
        *,
        background: bool,
        cancel_check: Callable[[], None] | None,
    ) -> dict[str, Any]:
        with self._request_lock:
            self._ensure_policy(background=background)
            with self._state_lock:
                request_epoch = self._lifecycle_epoch
            if cancel_check is not None:
                cancel_check()
            self._ensure_started(
                background=background,
                cancel_check=cancel_check,
                expected_epoch=request_epoch,
            )
            with self._state_lock:
                process = self._process
                sock = self._socket
                self._request_id += 1
                request_id = self._request_id
            if process is None or sock is None:
                raise SemanticWorkerError("Semantic worker is unavailable.")
            request = dict(payload)
            request.update(
                {
                    "protocol": PROTOCOL_VERSION,
                    "nonce": self._nonce,
                    "request_id": request_id,
                }
            )
            try:
                self._send_with_poll(
                    sock,
                    encode_frame(request),
                    process=process,
                    cancel_check=cancel_check,
                    deadline=time.monotonic() + _REQUEST_TIMEOUT_SECONDS,
                )
                response = self._wait_frame(
                    sock,
                    process=process,
                    cancel_check=cancel_check,
                    deadline=time.monotonic() + _REQUEST_TIMEOUT_SECONDS,
                )
                with self._state_lock:
                    if self._process is not process or self._socket is not sock:
                        raise SemanticWorkerError("Semantic worker was stopped.")
                self._validate_response(response, request_id=request_id)
                if response.get("ok") is not True:
                    message = str(response.get("error") or "Semantic worker failed.")
                    if response.get("error_kind") == "runtime":
                        raise SemanticRuntimeError(message)
                    raise SemanticWorkerError(message)
            except BaseException:
                self.terminate_now()
                raise
            return response

    def _ensure_policy(self, *, background: bool) -> None:
        desired = "background" if background else "utility"
        with self._state_lock:
            process = self._process
            current = self._worker_policy
            stale = process is not None and process.poll() is not None
            mismatched = (
                process is not None
                and process.poll() is None
                and current != desired
            )
        if stale or mismatched:
            self.terminate_and_wait()

    def _ensure_started(
        self,
        *,
        background: bool,
        cancel_check: Callable[[], None] | None,
        expected_epoch: int,
    ) -> None:
        desired_policy = "background" if background else "utility"
        with self._state_lock:
            process = self._process
            if (
                process is not None
                and process.poll() is None
                and self._worker_policy == desired_policy
            ):
                return
        if not self.manager.runtime_ready():
            raise SemanticWorkerError("The local semantic runtime is not installed.")
        worker_script = self.bundle_root / "semantic" / "worker_main.py"
        if not worker_script.is_file():
            raise SemanticWorkerError("The Semantic worker entry point is missing.")

        with self._state_changed:
            if self._lifecycle_epoch != expected_epoch:
                raise SemanticWorkerError("Semantic worker was stopped.")
            self._spawns_in_flight += 1
        parent_socket: socket.socket | None = None
        child_socket: socket.socket | None = None
        process = None
        try:
            parent_socket, child_socket = socket.socketpair()
            child_socket.set_inheritable(True)
            command = self._worker_command(
                child_socket.fileno(),
                worker_script,
                background=background,
            )
            environment = self._worker_environment()
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                    pass_fds=(child_socket.fileno(),),
                    start_new_session=True,
                    env=environment,
                    cwd=str(self.bundle_root),
                )
            except Exception as error:
                raise SemanticWorkerError(
                    f"Semantic worker could not start: {error}"
                ) from error
            child_socket.close()
            child_socket = None
            with self._state_changed:
                publish = self._lifecycle_epoch == expected_epoch
                if publish:
                    self._process = process
                    self._socket = parent_socket
                    self._worker_policy = desired_policy
                    self._buffer.clear()
            if not publish:
                self._stop_process(process, parent_socket)
                self._reap(process)
                process = None
                parent_socket = None
                raise SemanticWorkerError("Semantic worker was stopped.")
        except Exception as error:
            if child_socket is not None:
                child_socket.close()
            if parent_socket is not None and process is None:
                parent_socket.close()
            raise
        finally:
            with self._state_changed:
                self._spawns_in_flight = max(0, self._spawns_in_flight - 1)
                self._state_changed.notify_all()
        assert process is not None and parent_socket is not None
        try:
            hello = self._wait_frame(
                parent_socket,
                process=process,
                cancel_check=cancel_check,
                deadline=time.monotonic() + _START_TIMEOUT_SECONDS,
            )
            if (
                hello.get("type") != "hello"
                or not _is_plain_int(hello.get("protocol"), PROTOCOL_VERSION)
                or hello.get("nonce") != self._nonce
                or hello.get("model_revision") != MODEL_REVISION
                or not _is_plain_int(hello.get("dimension"), MODEL_DIMENSION)
            ):
                raise SemanticWorkerError(
                    "Semantic worker is incompatible with this add-on version."
                )
        except BaseException:
            self.terminate_now()
            raise

    def _worker_command(
        self,
        file_descriptor: int,
        worker_script: Path,
        *,
        background: bool,
    ) -> list[str]:
        command = [
            str(self.manager.worker_python_path),
            "-I",
            "-u",
            str(worker_script),
            "--fd",
            str(int(file_descriptor)),
            "--data-root",
            str(self.manager.data_root),
            "--bundle-root",
            str(self.bundle_root),
            "--nonce",
            self._nonce,
        ]
        taskpolicy = Path("/usr/sbin/taskpolicy")
        if taskpolicy.is_file():
            clamp = "background" if background else "utility"
            disk_policy = "throttle" if background else "default"
            # This is a second line of defense behind single-sequence
            # inference and bounded IPC. If a native dependency regresses,
            # macOS ends the disposable helper instead of allowing it to grow
            # into Anki's memory budget.
            prefix = [
                str(taskpolicy),
                "-m",
                str(_WORKER_MEMORY_LIMIT_MIB),
                "-d",
                disk_policy,
                "-c",
                clamp,
            ]
            if background:
                prefix.append("-b")
            command = prefix + command
        return command

    @staticmethod
    def _worker_environment() -> dict[str, str]:
        environment = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONNOUSERSITE": "1",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1",
            "TOKENIZERS_PARALLELISM": "false",
        }
        for name in ("HOME", "LANG", "LC_ALL", "TMPDIR"):
            value = os.environ.get(name)
            if value:
                environment[name] = value
        return environment

    def _wait_frame(
        self,
        sock: socket.socket,
        *,
        process: subprocess.Popen[bytes],
        cancel_check: Callable[[], None] | None,
        deadline: float,
    ) -> dict[str, Any]:
        while True:
            if cancel_check is not None:
                cancel_check()
            try:
                decoded = decode_from_buffer(self._buffer)
            except WorkerProtocolError as error:
                raise SemanticWorkerError(str(error)) from error
            if decoded is not None:
                return decoded
            if process.poll() is not None:
                raise SemanticWorkerError(
                    f"Semantic worker exited unexpectedly ({process.returncode})."
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SemanticWorkerError("Semantic worker timed out.")
            try:
                readable, _, _ = select.select(
                    [sock],
                    [],
                    [],
                    min(_POLL_SECONDS, remaining),
                )
                if not readable:
                    continue
                chunk = sock.recv(64 * 1024)
            except OSError as error:
                raise SemanticWorkerError("Semantic worker connection closed.") from error
            if not chunk:
                raise SemanticWorkerError("Semantic worker connection closed.")
            self._buffer.extend(chunk)

    def _send_with_poll(
        self,
        sock: socket.socket,
        frame: bytes,
        *,
        process: subprocess.Popen[bytes],
        cancel_check: Callable[[], None] | None,
        deadline: float,
    ) -> None:
        view = memoryview(frame)
        sent = 0
        while sent < len(view):
            if cancel_check is not None:
                cancel_check()
            if process.poll() is not None:
                raise SemanticWorkerError(
                    f"Semantic worker exited unexpectedly ({process.returncode})."
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SemanticWorkerError("Semantic worker timed out.")
            try:
                _, writable, _ = select.select(
                    [],
                    [sock],
                    [],
                    min(_POLL_SECONDS, remaining),
                )
                if not writable:
                    continue
                written = sock.send(view[sent:])
            except OSError as error:
                raise SemanticWorkerError("Semantic worker connection closed.") from error
            if written <= 0:
                raise SemanticWorkerError("Semantic worker connection closed.")
            sent += written

    def _validate_response(
        self,
        response: dict[str, Any],
        *,
        request_id: int,
    ) -> None:
        if (
            not _is_plain_int(response.get("protocol"), PROTOCOL_VERSION)
            or response.get("nonce") != self._nonce
            or not _is_plain_int(response.get("request_id"), request_id)
        ):
            raise SemanticWorkerError("Semantic worker returned a stale response.")

    @staticmethod
    def _decode_vectors(
        response: dict[str, Any],
        *,
        expected_count: int,
    ) -> list[array[float]]:
        if response.get("dimension") != MODEL_DIMENSION:
            raise SemanticWorkerError("Semantic worker returned the wrong vector size.")
        payloads = response.get("vectors")
        if not isinstance(payloads, list) or len(payloads) != expected_count:
            raise SemanticWorkerError("Semantic worker returned the wrong vector count.")
        output: list[array[float]] = []
        expected_bytes = MODEL_DIMENSION * 4
        for payload in payloads:
            if not isinstance(payload, str):
                raise SemanticWorkerError("Semantic worker returned an invalid vector.")
            try:
                raw = base64.b64decode(payload.encode("ascii"), validate=True)
            except (UnicodeError, ValueError) as error:
                raise SemanticWorkerError(
                    "Semantic worker returned an invalid vector."
                ) from error
            if len(raw) != expected_bytes:
                raise SemanticWorkerError("Semantic worker returned the wrong vector size.")
            vector = array("f")
            vector.frombytes(raw)
            if sys.byteorder != "little":
                vector.byteswap()
            norm_squared = 0.0
            for value in vector:
                if not math.isfinite(value):
                    raise SemanticWorkerError(
                        "Semantic worker returned a non-finite vector."
                    )
                norm_squared += float(value) * float(value)
            if norm_squared <= 1e-12 or abs(math.sqrt(norm_squared) - 1.0) > 1e-3:
                raise SemanticWorkerError(
                    "Semantic worker returned an unnormalized vector."
                )
            output.append(vector)
        return output

    @staticmethod
    def _reap(process: subprocess.Popen[bytes]) -> None:
        try:
            process.wait(timeout=_REAP_GRACE_SECONDS)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass
        try:
            process.wait(timeout=2.0)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _is_plain_int(value: Any, expected: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == expected


def _bounded_text(value: object) -> str:
    """Bound worker IPC without changing the model's effective token window."""

    text = str(value or "")
    # UTF-8 uses at most four bytes per code point. Slice first so even a
    # massive source field cannot force a massive temporary encoding.
    candidate = text[:MAX_TEXT_UTF8_BYTES]
    encoded = candidate.encode("utf-8")
    if len(encoded) <= MAX_TEXT_UTF8_BYTES:
        return candidate
    return encoded[:MAX_TEXT_UTF8_BYTES].decode("utf-8", errors="ignore")
