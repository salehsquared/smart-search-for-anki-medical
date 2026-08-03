"""Entrypoint for disposable local Semantic inference.

This file is executed by the pinned standalone Python runtime, never imported
by Anki. It deliberately knows nothing about Anki, the collection, or vector
index publication.
"""

from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path
import socket
import sys
from typing import Any


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--fd", type=int, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--nonce", required=True)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    bundle_root = arguments.bundle_root.resolve()
    expected_root = Path(__file__).resolve().parent.parent
    if bundle_root != expected_root:
        return 2
    sys.path.insert(0, str(bundle_root))

    from semantic.embedder import OnnxMedicalEmbedder
    from semantic.manifest import MODEL_DIMENSION, MODEL_REVISION
    from semantic.model_manager import ModelManager
    from semantic.worker_protocol import (
        MAX_TEXTS_PER_REQUEST,
        MAX_TEXT_UTF8_BYTES,
        MAX_TOTAL_TEXT_UTF8_BYTES,
        PROTOCOL_VERSION,
        WorkerProtocolError,
        receive_frame,
        send_frame,
    )

    manager = ModelManager(arguments.data_root.resolve(), bundle_root)
    if not manager.worker_runtime_ready() or not manager.model_ready():
        return 3

    connection = socket.socket(fileno=int(arguments.fd))
    embedder: OnnxMedicalEmbedder | None = None
    try:
        send_frame(
            connection,
            {
                "type": "hello",
                "protocol": PROTOCOL_VERSION,
                "nonce": arguments.nonce,
                "model_revision": MODEL_REVISION,
                "dimension": MODEL_DIMENSION,
                "pid": os.getpid(),
            },
        )
        while True:
            try:
                request = receive_frame(connection)
            except WorkerProtocolError:
                return 4
            if request is None:
                return 0
            request_id = request.get("request_id")
            request_protocol = request.get("protocol")
            if (
                not isinstance(request_protocol, int)
                or isinstance(request_protocol, bool)
                or request_protocol != PROTOCOL_VERSION
                or request.get("nonce") != arguments.nonce
                or not isinstance(request_id, int)
                or isinstance(request_id, bool)
            ):
                return 4
            op = request.get("op")
            if op == "shutdown":
                send_frame(
                    connection,
                    _response(
                        PROTOCOL_VERSION,
                        arguments.nonce,
                        request_id,
                        ok=True,
                    ),
                )
                return 0
            if op != "embed":
                send_frame(
                    connection,
                    _response(
                        PROTOCOL_VERSION,
                        arguments.nonce,
                        request_id,
                        ok=False,
                        error="Unsupported Semantic worker operation.",
                    ),
                )
                continue
            texts = request.get("texts")
            batch_size = request.get("batch_size")
            if (
                not isinstance(texts, list)
                or not texts
                or len(texts) > MAX_TEXTS_PER_REQUEST
                or any(not isinstance(text, str) for text in texts)
                or not isinstance(batch_size, int)
                or isinstance(batch_size, bool)
                or batch_size != 1
            ):
                send_frame(
                    connection,
                    _response(
                        PROTOCOL_VERSION,
                        arguments.nonce,
                        request_id,
                        ok=False,
                        error="Invalid Semantic embedding request.",
                    ),
                )
                continue
            text_sizes = [len(text.encode("utf-8")) for text in texts]
            if (
                any(size > MAX_TEXT_UTF8_BYTES for size in text_sizes)
                or sum(text_sizes) > MAX_TOTAL_TEXT_UTF8_BYTES
            ):
                send_frame(
                    connection,
                    _response(
                        PROTOCOL_VERSION,
                        arguments.nonce,
                        request_id,
                        ok=False,
                        error="Semantic embedding text is too large.",
                    ),
                )
                continue
            try:
                if embedder is None:
                    embedder = OnnxMedicalEmbedder(manager, intra_op_threads=1)
                vectors = embedder.embed(texts, batch_size=1)
                encoded = [
                    base64.b64encode(
                        vectors[index].astype("<f4", copy=False).tobytes(order="C")
                    ).decode("ascii")
                    for index in range(len(texts))
                ]
                send_frame(
                    connection,
                    _response(
                        PROTOCOL_VERSION,
                        arguments.nonce,
                        request_id,
                        ok=True,
                        dimension=MODEL_DIMENSION,
                        vectors=encoded,
                    ),
                )
            except Exception as error:
                send_frame(
                    connection,
                    _response(
                        PROTOCOL_VERSION,
                        arguments.nonce,
                        request_id,
                        ok=False,
                        error=f"Semantic inference failed: {error}",
                        error_kind="runtime",
                    ),
                )
                return 5
    finally:
        try:
            connection.close()
        except OSError:
            pass


def _response(
    protocol_version: int,
    nonce: str,
    request_id: int,
    *,
    ok: bool,
    **payload: Any,
) -> dict[str, Any]:
    return {
        "protocol": int(protocol_version),
        "nonce": nonce,
        "request_id": request_id,
        "ok": bool(ok),
        **payload,
    }


if __name__ == "__main__":
    raise SystemExit(main())
