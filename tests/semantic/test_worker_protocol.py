from __future__ import annotations

import json
import socket
import struct
import unittest

from semantic.worker_protocol import (
    MAX_FRAME_BYTES,
    MAX_REQUEST_FRAME_BYTES,
    MAX_RESPONSE_FRAME_BYTES,
    MAX_TEXTS_PER_REQUEST,
    MAX_TEXT_UTF8_BYTES,
    MAX_TOTAL_TEXT_UTF8_BYTES,
    WorkerProtocolError,
    decode_from_buffer,
    encode_frame,
    receive_frame,
    send_frame,
)


class WorkerProtocolTests(unittest.TestCase):
    def test_memory_bounds_are_deliberately_small(self) -> None:
        self.assertEqual(MAX_TEXTS_PER_REQUEST, 32)
        self.assertLessEqual(MAX_TEXT_UTF8_BYTES, 16 * 1024)
        self.assertEqual(
            MAX_TOTAL_TEXT_UTF8_BYTES,
            MAX_TEXTS_PER_REQUEST * MAX_TEXT_UTF8_BYTES,
        )
        self.assertLessEqual(MAX_REQUEST_FRAME_BYTES, 4 * 1024 * 1024)
        self.assertLessEqual(MAX_RESPONSE_FRAME_BYTES, 1024 * 1024)

    def test_round_trip_preserves_unicode_and_multiple_buffered_frames(self) -> None:
        first = {"query": "β-blocker — ضغط", "value": 1}
        second = {"ok": True}
        buffer = bytearray(encode_frame(first) + encode_frame(second))

        self.assertEqual(decode_from_buffer(buffer), first)
        self.assertEqual(decode_from_buffer(buffer), second)
        self.assertIsNone(decode_from_buffer(buffer))

    def test_incremental_decoder_waits_for_a_complete_frame(self) -> None:
        frame = encode_frame({"text": "bupropion"})
        buffer = bytearray()
        for byte in frame[:-1]:
            buffer.append(byte)
            self.assertIsNone(decode_from_buffer(buffer))
        buffer.append(frame[-1])
        self.assertEqual(decode_from_buffer(buffer), {"text": "bupropion"})

    def test_socket_round_trip(self) -> None:
        left, right = socket.socketpair()
        try:
            send_frame(left, {"hello": "world"})
            self.assertEqual(receive_frame(right), {"hello": "world"})
            left.close()
            self.assertIsNone(receive_frame(right))
        finally:
            try:
                left.close()
            except OSError:
                pass
            right.close()

    def test_invalid_sizes_and_non_object_json_fail_closed(self) -> None:
        for size in (0, MAX_FRAME_BYTES + 1):
            with self.subTest(size=size), self.assertRaises(WorkerProtocolError):
                decode_from_buffer(bytearray(struct.pack(">I", size)))

        body = json.dumps(["not", "an", "object"]).encode("utf-8")
        with self.assertRaises(WorkerProtocolError):
            decode_from_buffer(bytearray(struct.pack(">I", len(body)) + body))

    def test_partial_header_and_body_are_reported_as_protocol_errors(self) -> None:
        for payload in (
            b"\x00\x00",
            struct.pack(">I", 10) + b"{}",
        ):
            left, right = socket.socketpair()
            try:
                left.sendall(payload)
                left.close()
                with self.assertRaisesRegex(WorkerProtocolError, "partial"):
                    receive_frame(right)
            finally:
                try:
                    left.close()
                except OSError:
                    pass
                right.close()

    def test_nan_and_oversized_payloads_cannot_be_encoded(self) -> None:
        with self.assertRaises(WorkerProtocolError):
            encode_frame({"value": float("nan")})
        with self.assertRaises(WorkerProtocolError):
            encode_frame({"value": "x" * (MAX_FRAME_BYTES + 1)})


if __name__ == "__main__":
    unittest.main()
