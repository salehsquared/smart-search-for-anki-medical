"""Entrypoint for disposable local Semantic native operations.

This file is executed by the pinned standalone Python runtime, never imported
by Anki. It owns inference and NumPy-backed vector operations, while remaining
independent of Anki and its collection.
"""

from __future__ import annotations

import argparse
from array import array
import base64
import math
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
        MAX_NOTE_ID,
        MAX_NOTE_IDS_PER_REQUEST,
        MAX_SEARCH_HITS_PER_RESPONSE,
        MAX_TEXTS_PER_REQUEST,
        MAX_TEXT_UTF8_BYTES,
        MAX_TOTAL_TEXT_UTF8_BYTES,
        MAX_VECTORS_PER_REQUEST,
        PROTOCOL_VERSION,
        WorkerProtocolError,
        receive_frame,
        send_frame,
    )
    from semantic.vector_index import VectorIndex

    manager = ModelManager(arguments.data_root.resolve(), bundle_root)
    if not manager.worker_runtime_ready() or not manager.model_ready():
        return 3
    # Only this disposable process can see native Semantic packages. Vector
    # operations may be the first request after a worker restart, so make the
    # private runtime importable before dispatch begins.
    manager.activate_runtime()

    connection = socket.socket(fileno=int(arguments.fd))
    embedder: OnnxMedicalEmbedder | None = None
    vector_index: VectorIndex | None = None
    vector_root: Path | None = None
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
            if op == "embed":
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
                            vectors[index]
                            .astype("<f4", copy=False)
                            .tobytes(order="C")
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
                            error=_operation_error("Semantic inference failed", error),
                            error_kind="runtime",
                        ),
                    )
                    return 5
                continue

            if op == "upsert_vectors":
                note_ids = request.get("note_ids")
                content_hashes = request.get("content_hashes")
                vector_payloads = request.get("vectors")
                requested_root = _index_root(
                    request.get("index_root"),
                    arguments.data_root.resolve(),
                )
                valid_ids = _note_ids(
                    note_ids,
                    maximum=MAX_VECTORS_PER_REQUEST,
                    maximum_note_id=MAX_NOTE_ID,
                    allow_empty=False,
                )
                if (
                    requested_root is None
                    or valid_ids is None
                    or not isinstance(content_hashes, list)
                    or len(content_hashes) != len(valid_ids)
                    or any(not _valid_content_hash(value) for value in content_hashes)
                    or not isinstance(vector_payloads, list)
                    or len(vector_payloads) != len(valid_ids)
                ):
                    send_frame(
                        connection,
                        _response(
                            PROTOCOL_VERSION,
                            arguments.nonce,
                            request_id,
                            ok=False,
                            error="Invalid Semantic vector upsert request.",
                        ),
                    )
                    continue
                try:
                    decoded_vectors = [
                        _decode_vector(payload, MODEL_DIMENSION)
                        for payload in vector_payloads
                    ]
                except ValueError:
                    send_frame(
                        connection,
                        _response(
                            PROTOCOL_VERSION,
                            arguments.nonce,
                            request_id,
                            ok=False,
                            error="Invalid Semantic vector upsert request.",
                        ),
                    )
                    continue
                try:
                    if vector_index is None or vector_root != requested_root:
                        vector_index = VectorIndex(requested_root)
                        vector_root = requested_root
                    vector_index.upsert_many(
                        valid_ids,
                        content_hashes,
                        decoded_vectors,
                    )
                    send_frame(
                        connection,
                        _response(
                            PROTOCOL_VERSION,
                            arguments.nonce,
                            request_id,
                            ok=True,
                            indexed=len(valid_ids),
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
                            error=_operation_error(
                                "Semantic index update failed", error
                            ),
                            error_kind="index",
                        ),
                    )
                continue

            if op == "search_vector":
                requested_root = _index_root(
                    request.get("index_root"),
                    arguments.data_root.resolve(),
                )
                query_payload = request.get("query_vector")
                limit = request.get("limit")
                raw_allowed = request.get("allowed_note_ids")
                allowed = (
                    None
                    if raw_allowed is None
                    else _note_ids(
                        raw_allowed,
                        maximum=MAX_NOTE_IDS_PER_REQUEST,
                        maximum_note_id=MAX_NOTE_ID,
                        allow_empty=False,
                    )
                )
                if (
                    requested_root is None
                    or not isinstance(query_payload, str)
                    or not isinstance(limit, int)
                    or isinstance(limit, bool)
                    or limit <= 0
                    or limit > MAX_SEARCH_HITS_PER_RESPONSE
                    or raw_allowed is not None and allowed is None
                ):
                    send_frame(
                        connection,
                        _response(
                            PROTOCOL_VERSION,
                            arguments.nonce,
                            request_id,
                            ok=False,
                            error="Invalid Semantic vector search request.",
                        ),
                    )
                    continue
                try:
                    query_vector = _decode_vector(query_payload, MODEL_DIMENSION)
                except ValueError:
                    send_frame(
                        connection,
                        _response(
                            PROTOCOL_VERSION,
                            arguments.nonce,
                            request_id,
                            ok=False,
                            error="Invalid Semantic vector search request.",
                        ),
                    )
                    continue
                try:
                    if vector_index is None or vector_root != requested_root:
                        vector_index = VectorIndex(requested_root)
                        vector_root = requested_root
                    hits = vector_index.search(
                        query_vector,
                        limit=limit,
                        allowed_note_ids=None if allowed is None else set(allowed),
                    )
                    plain_hits = _plain_hits(
                        hits,
                        limit=limit,
                        maximum_note_id=MAX_NOTE_ID,
                    )
                    send_frame(
                        connection,
                        _response(
                            PROTOCOL_VERSION,
                            arguments.nonce,
                            request_id,
                            ok=True,
                            hits=plain_hits,
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
                            error=_operation_error(
                                "Semantic index search failed", error
                            ),
                            error_kind="index",
                        ),
                    )
                continue

            if op not in {"embed", "upsert_vectors", "search_vector"}:
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


def _index_root(value: object, data_root: Path) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        root = Path(value).resolve()
        relative = root.relative_to(data_root)
    except (OSError, RuntimeError, ValueError):
        return None
    return root if relative.parts else None


def _note_ids(
    value: object,
    *,
    maximum: int,
    maximum_note_id: int,
    allow_empty: bool,
) -> list[int] | None:
    if (
        not isinstance(value, list)
        or len(value) > maximum
        or not allow_empty
        and not value
        or any(
            not isinstance(note_id, int)
            or isinstance(note_id, bool)
            or note_id < 0
            or note_id > maximum_note_id
            for note_id in value
        )
        or len(set(value)) != len(value)
    ):
        return None
    return value


def _valid_content_hash(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _decode_vector(payload: object, dimension: int) -> array[float]:
    if not isinstance(payload, str):
        raise ValueError("vector payload must be text")
    try:
        raw = base64.b64decode(payload.encode("ascii"), validate=True)
    except Exception as error:
        raise ValueError("vector payload is invalid") from error
    if len(raw) != dimension * 4:
        raise ValueError("vector size is invalid")
    vector = array("f")
    vector.frombytes(raw)
    if sys.byteorder != "little":
        vector.byteswap()
    norm_squared = 0.0
    for value in vector:
        if not math.isfinite(value):
            raise ValueError("vector contains a non-finite value")
        norm_squared += float(value) * float(value)
    if norm_squared <= 1e-12:
        raise ValueError("vector has zero norm")
    return vector


def _plain_hits(
    hits: object,
    *,
    limit: int,
    maximum_note_id: int,
) -> list[dict[str, int | float]]:
    if not isinstance(hits, list) or len(hits) > limit:
        raise ValueError("vector search returned invalid results")
    output: list[dict[str, int | float]] = []
    seen: set[int] = set()
    for hit in hits:
        note_id = getattr(hit, "note_id", None)
        score = getattr(hit, "score", None)
        if (
            not isinstance(note_id, int)
            or isinstance(note_id, bool)
            or note_id < 0
            or note_id > maximum_note_id
            or not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not math.isfinite(float(score))
            or not -1.01 <= float(score) <= 1.01
            or note_id in seen
        ):
            raise ValueError("vector search returned invalid results")
        seen.add(note_id)
        output.append({"note_id": note_id, "score": float(score)})
    expected = sorted(
        output,
        key=lambda item: (-float(item["score"]), int(item["note_id"])),
    )
    if output != expected:
        raise ValueError("vector search returned unsorted results")
    return output


def _operation_error(prefix: str, error: Exception) -> str:
    detail = " ".join(str(error).split())[:512]
    return f"{prefix}: {detail}" if detail else f"{prefix}."


if __name__ == "__main__":
    raise SystemExit(main())
