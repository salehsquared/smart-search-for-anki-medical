from __future__ import annotations

import unittest

from backend.host_safety import (
    HOST_BACKEND_RESTART_MESSAGE,
    HostBackendUnavailable,
    _reset_host_backend_quarantine_for_tests,
    contain_host_backend_panic,
    host_backend_quarantined,
    is_pyo3_panic,
    require_host_backend,
)


PanicException = type(
    "PanicException",
    (BaseException,),
    {"__module__": "pyo3_runtime"},
)


class HostSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_host_backend_quarantine_for_tests()
        self.addCleanup(_reset_host_backend_quarantine_for_tests)

    def test_exact_pyo3_panic_is_contained_and_opens_sticky_circuit(self) -> None:
        calls = []

        def panic() -> None:
            calls.append("panic")
            raise PanicException("poisoned")

        with self.assertRaisesRegex(
            HostBackendUnavailable,
            "Restart Anki",
        ):
            contain_host_backend_panic(panic)

        self.assertEqual(calls, ["panic"])
        self.assertTrue(host_backend_quarantined())
        with self.assertRaisesRegex(
            HostBackendUnavailable,
            "Restart Anki",
        ):
            require_host_backend()

        later_calls = []
        with self.assertRaises(HostBackendUnavailable):
            contain_host_backend_panic(lambda: later_calls.append(True))
        self.assertEqual(later_calls, [])

    def test_unrelated_base_exceptions_are_never_swallowed(self) -> None:
        for error in (KeyboardInterrupt(), SystemExit(7), GeneratorExit()):
            with self.subTest(error=type(error).__name__):
                with self.assertRaises(type(error)):
                    contain_host_backend_panic(
                        lambda current=error: (_ for _ in ()).throw(current)
                    )
                self.assertFalse(host_backend_quarantined())

    def test_only_exact_pyo3_runtime_type_is_classified(self) -> None:
        same_name_wrong_module = type(
            "PanicException",
            (BaseException,),
            {"__module__": "other_runtime"},
        )
        same_module_wrong_name = type(
            "OtherPanic",
            (BaseException,),
            {"__module__": "pyo3_runtime"},
        )

        self.assertTrue(is_pyo3_panic(PanicException("panic")))
        self.assertFalse(is_pyo3_panic(same_name_wrong_module("panic")))
        self.assertFalse(is_pyo3_panic(same_module_wrong_name("panic")))

    def test_normal_result_and_exception_behavior_is_unchanged(self) -> None:
        self.assertEqual(contain_host_backend_panic(lambda: 42), 42)
        expected = RuntimeError("ordinary failure")
        with self.assertRaises(RuntimeError) as raised:
            contain_host_backend_panic(
                lambda: (_ for _ in ()).throw(expected)
            )
        self.assertIs(raised.exception, expected)
        self.assertFalse(host_backend_quarantined())
        self.assertIn("Restart Anki", HOST_BACKEND_RESTART_MESSAGE)


if __name__ == "__main__":
    unittest.main()
