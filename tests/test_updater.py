from __future__ import annotations

import types
import unittest

from updater import (
    ANKIWEB_ADDON_ID,
    is_public_ankiweb_install,
    native_update_available,
    native_update_was_installed,
    request_native_update,
)


class _Meta:
    def __init__(self, identifier):
        self.identifier = identifier

    def ankiweb_id(self):
        return self.identifier


class _Manager:
    def __init__(self, identifier=ANKIWEB_ADDON_ID):
        self.identifier = identifier
        self.requests = []

    def addon_meta(self, module):
        self.requests.append(module)
        return _Meta(self.identifier)


class UpdaterTests(unittest.TestCase):
    def test_only_the_running_numeric_public_copy_is_eligible(self) -> None:
        manager = _Manager()
        self.assertTrue(
            is_public_ankiweb_install(manager, str(ANKIWEB_ADDON_ID))
        )
        self.assertFalse(
            is_public_ankiweb_install(manager, "smart_search_medical")
        )
        self.assertFalse(
            is_public_ankiweb_install(_Manager(123), str(ANKIWEB_ADDON_ID))
        )

    def test_targeted_native_installer_receives_the_public_id(self) -> None:
        manager = _Manager()
        mw = types.SimpleNamespace(addonManager=manager)
        calls = []

        def install(parent, seen_manager, addon_id, on_done):
            calls.append((parent, seen_manager, addon_id))
            on_done([])

        module = types.SimpleNamespace(install_or_update_addon=install)
        completed = []
        strategy = request_native_update(
            mw,
            mw,
            str(ANKIWEB_ADDON_ID),
            lambda log: completed.append(log),
            importer=lambda _name: module,
        )

        self.assertEqual(strategy, "targeted")
        self.assertEqual(calls, [(mw, manager, ANKIWEB_ADDON_ID)])
        self.assertEqual(completed, [[]])

    def test_manual_copy_never_invokes_the_public_installer(self) -> None:
        manager = _Manager()
        mw = types.SimpleNamespace(addonManager=manager)
        invoked = []
        module = types.SimpleNamespace(
            install_or_update_addon=lambda *_args: invoked.append(True)
        )

        self.assertFalse(
            native_update_available(
                mw,
                "smart_search_medical",
                importer=lambda _name: module,
            )
        )
        with self.assertRaisesRegex(RuntimeError, "AnkiWeb installation"):
            request_native_update(
                mw,
                mw,
                "smart_search_medical",
                lambda _log: None,
                importer=lambda _name: module,
            )
        self.assertEqual(invoked, [])

    def test_missing_targeted_updater_fails_closed(self) -> None:
        manager = _Manager()
        mw = types.SimpleNamespace(
            addonManager=manager,
            check_for_addon_updates=lambda **_kwargs: self.fail(
                "all-add-on fallback must not run"
            ),
        )
        self.assertFalse(
            native_update_available(
                mw,
                str(ANKIWEB_ADDON_ID),
                importer=lambda _name: types.SimpleNamespace(),
            )
        )
        with self.assertRaisesRegex(RuntimeError, "cannot check"):
            request_native_update(
                mw,
                mw,
                str(ANKIWEB_ADDON_ID),
                lambda _log: None,
                importer=lambda _name: types.SimpleNamespace(),
            )

    def test_successful_native_result_is_detected_by_anki_type(self) -> None:
        class InstallOk:
            pass

        module = types.SimpleNamespace(InstallOk=InstallOk)
        self.assertTrue(
            native_update_was_installed(
                [(ANKIWEB_ADDON_ID, InstallOk())],
                importer=lambda _name: module,
            )
        )
        self.assertFalse(
            native_update_was_installed(
                [(ANKIWEB_ADDON_ID, object())],
                importer=lambda _name: module,
            )
        )


if __name__ == "__main__":
    unittest.main()
