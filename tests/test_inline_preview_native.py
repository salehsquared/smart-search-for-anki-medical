from __future__ import annotations

import unittest

try:
    from aqt.editor import Editor
except ImportError as error:  # Plain-Python CI intentionally omits Anki.
    IMPORT_ERROR = error
else:
    IMPORT_ERROR = None


@unittest.skipIf(
    IMPORT_ERROR is not None,
    f"Anki runtime unavailable: {IMPORT_ERROR}",
)
class NativeEditorCompatibilityTests(unittest.TestCase):
    def test_supported_editor_contract_is_present(self) -> None:
        for name in ("set_note", "call_after_note_saved", "cleanup"):
            self.assertTrue(callable(getattr(Editor, name, None)), name)

        # Supported versions use one of these two internal state families.
        # InlineResultInspector deliberately tracks its own attachment ID so
        # it does not depend on either representation at runtime.
        names = set(Editor.__init__.__code__.co_names)
        self.assertTrue({"note", "nid"}.intersection(names))


if __name__ == "__main__":
    unittest.main()
