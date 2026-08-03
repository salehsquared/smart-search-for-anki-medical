"""Small authenticated protocol for the local Semantic helper process."""

from __future__ import annotations

import json
import socket
import struct
from typing import Any


PROTOCOL_VERSION = 1
# The model truncates every input to 512 tokens. Keeping at most 16 KiB of
# UTF-8 per input therefore leaves a generous margin for medical text while
# preventing one pathological card from making either process allocate an
# unbounded JSON request. JSON escaping can expand control characters, so the
# frame limit remains larger than the raw-text budget.
MAX_TEXT_UTF8_BYTES = 16 * 1024
MAX_TEXTS_PER_REQUEST = 32
MAX_TOTAL_TEXT_UTF8_BYTES = MAX_TEXT_UTF8_BYTES * MAX_TEXTS_PER_REQUEST
MAX_REQUEST_FRAME_BYTES = 4 * 1024 * 1024
MAX_RESPONSE_FRAME_BYTES = 1024 * 1024
# Backward-compatible name for callers/tests that mean the largest legal
# request. Responses are deliberately much smaller.
MAX_FRAME_BYTES = MAX_REQUEST_FRAME_BYTES
_HEADER = struct.Struct(">I")


class WorkerProtocolError(RuntimeError):
    pass


def encode_frame(
    payload: dict[str, Any],
    *,
    max_frame_bytes: int = MAX_REQUEST_FRAME_BYTES,
) -> bytes:
    try:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise WorkerProtocolError(f"Could not encode worker request: {error}") from error
    if len(body) > max_frame_bytes:
        raise WorkerProtocolError("Semantic worker message is too large.")
    return _HEADER.pack(len(body)) + body


def send_frame(
    sock: socket.socket,
    payload: dict[str, Any],
    *,
    max_frame_bytes: int = MAX_RESPONSE_FRAME_BYTES,
) -> None:
    sock.sendall(encode_frame(payload, max_frame_bytes=max_frame_bytes))


def receive_frame(
    sock: socket.socket,
    *,
    max_frame_bytes: int = MAX_REQUEST_FRAME_BYTES,
) -> dict[str, Any] | None:
    header = _receive_exact(sock, _HEADER.size)
    if header is None:
        return None
    if len(header) != _HEADER.size:
        raise WorkerProtocolError("Semantic worker closed a partial message.")
    (size,) = _HEADER.unpack(header)
    if size <= 0 or size > max_frame_bytes:
        raise WorkerProtocolError("Semantic worker sent an invalid message size.")
    body = _receive_exact(sock, size)
    if body is None or len(body) != size:
        raise WorkerProtocolError("Semantic worker closed a partial message.")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise WorkerProtocolError("Semantic worker sent invalid JSON.") from error
    if not isinstance(payload, dict):
        raise WorkerProtocolError("Semantic worker response must be an object.")
    return payload


def decode_from_buffer(
    buffer: bytearray,
    *,
    max_frame_bytes: int = MAX_RESPONSE_FRAME_BYTES,
) -> dict[str, Any] | None:
    """Decode one complete frame without blocking, leaving any remainder."""

    if len(buffer) < _HEADER.size:
        return None
    (size,) = _HEADER.unpack(buffer[: _HEADER.size])
    if size <= 0 or size > max_frame_bytes:
        raise WorkerProtocolError("Semantic worker sent an invalid message size.")
    frame_size = _HEADER.size + size
    if len(buffer) < frame_size:
        return None
    body = bytes(buffer[_HEADER.size : frame_size])
    del buffer[:frame_size]
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError) as error:
        raise WorkerProtocolError("Semantic worker sent invalid JSON.") from error
    if not isinstance(payload, dict):
        raise WorkerProtocolError("Semantic worker response must be an object.")
    return payload


def _receive_exact(sock: socket.socket, size: int) -> bytes | None:
    output = bytearray()
    while len(output) < size:
        chunk = sock.recv(size - len(output))
        if not chunk:
            return None if not output else bytes(output)
        output.extend(chunk)
    return bytes(output)
