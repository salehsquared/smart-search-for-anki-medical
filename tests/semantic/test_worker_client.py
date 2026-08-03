from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

from semantic.errors import SemanticRuntimeError, SemanticWorkerError
from semantic.manifest import MODEL_DIMENSION, MODEL_REVISION
from semantic.worker_client import SemanticWorkerClient
from semantic.worker_protocol import MAX_TEXT_UTF8_BYTES


_FAKE_WORKER = r'''import argparse
import base64
import json
import os
import signal
import socket
import struct
import time

parser = argparse.ArgumentParser()
parser.add_argument("--fd", type=int, required=True)
parser.add_argument("--nonce", required=True)
parser.add_argument("--revision", required=True)
parser.add_argument("--dimension", type=int, required=True)
parser.add_argument("--mode", required=True)
args = parser.parse_args()
sock = socket.socket(fileno=args.fd)
if args.mode == "ignore_term":
    signal.signal(signal.SIGTERM, lambda *_args: None)

def exact(size):
    output = bytearray()
    while len(output) < size:
        chunk = sock.recv(size - len(output))
        if not chunk:
            return None
        output.extend(chunk)
    return bytes(output)

def receive():
    header = exact(4)
    if header is None:
        return None
    size = struct.unpack(">I", header)[0]
    body = exact(size)
    return json.loads(body.decode("utf-8")) if body is not None else None

def send(payload, fragmented=False):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    frame = struct.pack(">I", len(body)) + body
    if fragmented:
        for byte in frame:
            sock.send(bytes((byte,)))
    else:
        sock.sendall(frame)

hello = {
    "type": "hello",
    "protocol": True if args.mode == "bool_hello" else 1,
    "nonce": "wrong" if args.mode == "wrong_hello" else args.nonce,
    "model_revision": args.revision,
    "dimension": args.dimension,
}
send(hello, fragmented=args.mode == "fragmented")
if args.mode == "wrong_hello":
    time.sleep(30)

while True:
    request = receive()
    if request is None:
        break
    if args.mode == "crash":
        os._exit(7)
    if args.mode == "hang":
        time.sleep(30)
    request_id = request["request_id"] + (1 if args.mode == "stale" else 0)
    dimension = args.dimension + (1 if args.mode == "wrong_dim" else 0)
    vectors = []
    for _text in request.get("texts", []):
        values = [0.0] * args.dimension
        if args.mode == "nan":
            values[0] = float("nan")
        elif args.mode != "zero":
            values[0] = 1.0
        raw = struct.pack("<" + ("f" * args.dimension), *values)
        vectors.append(base64.b64encode(raw).decode("ascii"))
    if args.mode == "bad_count":
        vectors = []
    elif args.mode == "bad_base64":
        vectors = ["not base64!"]
    response = {
        "protocol": 1,
        "nonce": args.nonce,
        "request_id": request_id,
        "ok": args.mode != "error",
        "dimension": dimension,
        "vectors": vectors,
    }
    if args.mode == "error":
        response["error"] = "deliberate failure"
    elif args.mode == "runtime_error":
        response["ok"] = False
        response["error"] = "model could not load"
        response["error_kind"] = "runtime"
    send(response, fragmented=args.mode == "fragmented")
'''


class SemanticWorkerClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        worker_entry = self.root / "semantic" / "worker_main.py"
        worker_entry.parent.mkdir(parents=True)
        worker_entry.write_text("# test entrypoint\n", encoding="utf-8")
        self.helper = self.root / "fake_worker.py"
        self.helper.write_text(_FAKE_WORKER, encoding="utf-8")

    def _client(self, mode: str = "good") -> SemanticWorkerClient:
        manager = types.SimpleNamespace(
            runtime_ready=lambda: True,
            worker_python_path=Path(sys.executable),
            data_root=self.root / "data",
        )
        client = SemanticWorkerClient(manager, self.root)
        selected_mode = [mode]
        policies = []

        def command(file_descriptor, _worker_script, *, background):
            policies.append(bool(background))
            return [
                sys.executable,
                "-u",
                str(self.helper),
                "--fd",
                str(file_descriptor),
                "--nonce",
                client._nonce,
                "--revision",
                MODEL_REVISION,
                "--dimension",
                str(MODEL_DIMENSION),
                "--mode",
                selected_mode[0],
            ]

        client._worker_command = command
        client._test_mode = selected_mode
        client._test_policies = policies
        self.addCleanup(client.terminate_and_wait)
        return client

    def test_good_and_fragmented_workers_return_normalized_vectors(self) -> None:
        for mode in ("good", "fragmented"):
            with self.subTest(mode=mode):
                client = self._client(mode)
                vectors = client.embed(["one", "two"], background=False)
                self.assertEqual(len(vectors), 2)
                self.assertTrue(all(len(vector) == MODEL_DIMENSION for vector in vectors))
                self.assertEqual(vectors[0][0], 1.0)
                self.assertTrue(client.running)
                client.terminate_and_wait()
                self.assertFalse(client.running)

    def test_terminate_and_wait_reaps_the_exact_child(self) -> None:
        client = self._client()
        client.embed(["hello"], background=False)
        process = client._process
        self.assertIsNotNone(process)

        self.assertTrue(client.terminate_and_wait())

        assert process is not None
        self.assertIsNotNone(process.poll())
        self.assertFalse(client.running)
        self.assertFalse(client.terminate_and_wait())

    def test_cancel_kills_a_hung_inference_without_waiting_for_timeout(self) -> None:
        client = self._client("hang")
        started = time.monotonic()

        def cancel_check() -> None:
            if time.monotonic() - started > 0.15:
                raise RuntimeError("cancelled")

        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            client.embed(
                ["hang"],
                background=True,
                cancel_check=cancel_check,
            )

        self.assertLess(time.monotonic() - started, 2.0)
        self.assertFalse(client.running)

    def test_crashed_worker_is_discarded_and_next_request_restarts(self) -> None:
        client = self._client("crash")
        with self.assertRaisesRegex(
            SemanticWorkerError,
            "(?:exited unexpectedly|connection closed)",
        ):
            client.embed(["first"], background=False)
        self.assertFalse(client.running)

        client._test_mode[0] = "good"
        vectors = client.embed(["second"], background=False)
        self.assertEqual(vectors[0][0], 1.0)

    def test_large_public_batch_is_chunked_to_worker_protocol_limit(self) -> None:
        client = self._client()

        vectors = client.embed([str(index) for index in range(65)], background=True)

        self.assertEqual(len(vectors), 65)
        self.assertEqual(client._request_id, 3)

    def test_pathological_text_is_bounded_before_worker_ipc(self) -> None:
        client = self._client()
        captured = []
        original_request = client._request

        def request(payload, **kwargs):
            captured.extend(payload["texts"])
            return original_request(payload, **kwargs)

        client._request = request
        text = ("\u2603" * (MAX_TEXT_UTF8_BYTES * 4)) + "tail"

        vectors = client.embed([text], background=False)

        self.assertEqual(len(vectors), 1)
        self.assertEqual(len(captured), 1)
        self.assertLessEqual(
            len(captured[0].encode("utf-8")),
            MAX_TEXT_UTF8_BYTES,
        )
        self.assertNotIn("tail", captured[0])

    def test_handshake_and_response_identity_are_fail_closed(self) -> None:
        client = self._client("wrong_hello")
        with self.assertRaisesRegex(SemanticWorkerError, "incompatible"):
            client.embed(["hello"], background=False)
        self.assertFalse(client.running)

        client = self._client("stale")
        with self.assertRaisesRegex(SemanticWorkerError, "stale response"):
            client.embed(["hello"], background=False)
        self.assertFalse(client.running)

        client = self._client("bool_hello")
        with self.assertRaisesRegex(SemanticWorkerError, "incompatible"):
            client.embed(["hello"], background=False)
        self.assertFalse(client.running)

    def test_invalid_worker_vectors_and_errors_are_fail_closed(self) -> None:
        cases = {
            "wrong_dim": "wrong vector size",
            "bad_count": "wrong vector count",
            "bad_base64": "invalid vector",
            "zero": "unnormalized vector",
            "nan": "non-finite vector",
            "error": "deliberate failure",
        }
        for mode, message in cases.items():
            with self.subTest(mode=mode):
                client = self._client(mode)
                with self.assertRaisesRegex(SemanticWorkerError, message):
                    client.embed(["hello"], background=False)
                client.terminate_and_wait()
                self.assertFalse(client.running)

    def test_worker_runtime_failure_is_distinct_from_transient_child_failure(self) -> None:
        client = self._client("runtime_error")

        with self.assertRaisesRegex(SemanticRuntimeError, "model could not load"):
            client.embed(["hello"], background=False)

        self.assertFalse(client.running)

    def test_worker_environment_does_not_copy_arbitrary_host_variables(self) -> None:
        environment = SemanticWorkerClient._worker_environment()
        self.assertEqual(environment["OMP_NUM_THREADS"], "1")
        self.assertEqual(environment["OPENBLAS_NUM_THREADS"], "1")
        self.assertEqual(environment["TOKENIZERS_PARALLELISM"], "false")
        self.assertNotIn("PYTHONPATH", environment)
        self.assertNotIn("VIRTUAL_ENV", environment)

    def test_macos_taskpolicy_enforces_qos_and_hard_memory_ceiling(self) -> None:
        manager = types.SimpleNamespace(
            runtime_ready=lambda: True,
            worker_python_path=Path(sys.executable),
            data_root=self.root / "data",
        )
        client = SemanticWorkerClient(manager, self.root)
        with patch("semantic.worker_client.Path.is_file", return_value=True):
            command = client._worker_command(
                9,
                self.root / "semantic" / "worker_main.py",
                background=True,
            )

        self.assertEqual(command[:4], ["/usr/sbin/taskpolicy", "-m", "256", "-d"])
        self.assertIn("background", command)
        self.assertIn("-b", command)

    def test_policy_change_restarts_worker_instead_of_reusing_wrong_qos(self) -> None:
        client = self._client()
        client.embed(["background"], background=True)
        first = client._process
        self.assertIsNotNone(first)

        client.embed(["interactive"], background=False)
        second = client._process

        self.assertIsNotNone(second)
        self.assertIsNot(first, second)
        assert first is not None
        self.assertIsNotNone(first.poll())
        self.assertEqual(client._test_policies, [True, False])

    def test_abort_during_popen_prevents_late_worker_publication(self) -> None:
        client = self._client()
        real_popen = subprocess.Popen
        entered = threading.Event()
        release = threading.Event()
        spawned = []
        errors = []

        def blocked_popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            spawned.append(process)
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test did not release Popen")
            return process

        def request() -> None:
            try:
                client.embed(["race"], background=True)
            except BaseException as error:
                errors.append(error)

        with patch("semantic.worker_client.subprocess.Popen", blocked_popen):
            thread = threading.Thread(target=request)
            thread.start()
            self.assertTrue(entered.wait(timeout=5))
            self.assertTrue(client.terminate_now())
            release.set()
            thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertTrue(errors)
        self.assertFalse(client.running)
        self.assertEqual(len(spawned), 1)
        self.assertIsNotNone(spawned[0].poll())

    def test_close_joins_a_prior_async_reaper(self) -> None:
        client = self._client("ignore_term")
        client.embed(["hello"], background=False)
        process = client._process
        self.assertIsNotNone(process)

        reaper_entered = threading.Event()
        release_reaper = threading.Event()
        real_reap = client._reap

        def blocked_reap(worker) -> None:
            reaper_entered.set()
            self.assertTrue(release_reaper.wait(timeout=5))
            real_reap(worker)

        with patch.object(client, "_reap", side_effect=blocked_reap):
            self.assertTrue(client.terminate_now())
            self.assertTrue(reaper_entered.wait(timeout=5))
            with client._state_lock:
                reapers = tuple(client._reaper_threads)
            self.assertEqual(len(reapers), 1)
            reaper = reapers[0]
            real_join = reaper.join

            def release_then_join(timeout=None) -> None:
                release_reaper.set()
                real_join(timeout=timeout)

            try:
                with patch.object(reaper, "join", side_effect=release_then_join):
                    self.assertTrue(client.terminate_and_wait())
            finally:
                release_reaper.set()

        assert process is not None
        self.assertIsNotNone(process.poll())
        with client._state_lock:
            self.assertEqual(client._reaper_threads, set())


if __name__ == "__main__":
    unittest.main()
