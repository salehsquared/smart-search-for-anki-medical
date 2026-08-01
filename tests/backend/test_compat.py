from __future__ import annotations

import types
import unittest
from unittest.mock import patch
from dataclasses import FrozenInstanceError

from backend import compat


class _Hook(list):
    pass


class CompatibilityTests(unittest.TestCase):
    def test_dataclass_slots_degrade_cleanly_on_python_39(self) -> None:
        with patch.object(compat.sys, "version_info", (3, 9, 6)):

            @compat.dataclass(frozen=True, slots=True)
            class Example:
                value: int

        item = Example(7)
        self.assertEqual(item.value, 7)
        self.assertFalse(hasattr(Example, "__slots__"))
        with self.assertRaises(FrozenInstanceError):
            item.value = 8

    def test_point_version_is_safe_outside_or_during_host_transition(self) -> None:
        self.assertEqual(compat.anki_point_version(lambda: "260800"), 260800)
        self.assertEqual(compat.anki_point_version(lambda: -1), 0)
        self.assertEqual(compat.anki_point_version(lambda: None), 0)

        def unavailable():
            raise RuntimeError("profile is closing")

        self.assertEqual(compat.anki_point_version(unavailable), 0)

    def test_optional_hook_helpers_do_not_assume_new_hook_names(self) -> None:
        callback = lambda: None
        hooks = types.SimpleNamespace(profile_did_open=_Hook())

        self.assertTrue(
            compat.append_optional_hook(hooks, "profile_did_open", callback)
        )
        self.assertEqual(hooks.profile_did_open, [callback])
        self.assertFalse(
            compat.append_optional_hook(hooks, "future_hook", callback)
        )

    def test_host_probe_reports_independent_optional_features(self) -> None:
        hooks = types.SimpleNamespace(
            profile_did_open=_Hook(),
            profile_will_close=_Hook(),
            operation_did_execute=_Hook(),
            top_toolbar_did_init_links=_Hook(),
            collection_will_temporarily_close=_Hook(),
            collection_did_temporarily_close=_Hook(),
        )
        modules = {
            "aqt": types.SimpleNamespace(
                dialogs=types.SimpleNamespace(open=lambda *_args: None)
            ),
            "aqt.operations": types.SimpleNamespace(
                QueryOp=object,
                CollectionOp=object,
            ),
            "aqt.browser.previewer": types.SimpleNamespace(
                MultiCardPreviewer=object
            ),
            "aqt.editor": types.SimpleNamespace(Editor=object, EditorMode=object),
            "aqt.webview": types.SimpleNamespace(
                AnkiWebView=object,
                AnkiWebViewKind=object,
            ),
            "aqt.mediasrv": types.SimpleNamespace(PageContext=object),
        }

        def importer(name: str):
            try:
                return modules[name]
            except KeyError as error:
                raise ImportError(name) from error

        features = compat.probe_anki_host(
            point_version=250904,
            gui_hooks=hooks,
            importer=importer,
        )

        self.assertEqual(features.point_version, 250904)
        self.assertTrue(features.core_search_available)
        self.assertTrue(features.card_actions_available)
        self.assertTrue(features.toolbar_hook)
        self.assertTrue(features.temporary_collection_hooks)
        self.assertTrue(features.browser_dialog)
        self.assertTrue(features.native_previewer)
        self.assertTrue(features.inline_editor)

        del modules["aqt.browser.previewer"]
        without_preview = compat.probe_anki_host(
            point_version=260800,
            gui_hooks=hooks,
            importer=importer,
        )
        self.assertTrue(without_preview.core_search_available)
        self.assertFalse(without_preview.native_previewer)


if __name__ == "__main__":
    unittest.main()
