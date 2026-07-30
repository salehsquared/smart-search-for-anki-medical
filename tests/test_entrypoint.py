from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]


class _Signal:
    def __init__(self) -> None:
        self.callback = None

    def connect(self, callback) -> None:
        self.callback = callback


class _Action:
    def __init__(self, text, parent) -> None:
        self.text = text
        self.parent = parent
        self.triggered = _Signal()
        self.shortcut = None

    def setObjectName(self, value) -> None:
        self.object_name = value

    def setToolTip(self, value) -> None:
        self.tool_tip = value

    def setShortcut(self, value) -> None:
        self.shortcut = value

    def setShortcutContext(self, value) -> None:
        self.shortcut_context = value


class _KeySequence:
    def __init__(self, value) -> None:
        self.value = value

    def isEmpty(self) -> bool:
        return not self.value


class _Menu:
    def __init__(self) -> None:
        self.actions = []

    def addAction(self, action) -> None:
        self.actions.append(action)


class _Hook(list):
    pass


class _Toolbar:
    def __init__(self) -> None:
        self.created = []

    def create_link(self, cmd, label, func, tip=None, id=None):
        self.created.append(
            {
                "cmd": cmd,
                "label": label,
                "func": func,
                "tip": tip,
                "id": id,
            }
        )
        return (
            f'<a class="hitem" aria-label="{label}" title="{tip}" '
            f'id="{id}" href="#" onclick="return pycmd(\'{cmd}\')">'
            f"{label}</a>"
        )


class _AddonManager:
    def getConfig(self, _module):
        return {}


class EntrypointTests(unittest.TestCase):
    def test_import_registers_tools_action_and_compact_toolbar_link(self) -> None:
        package_name = "_smart_search_entrypoint_test"
        for key in tuple(sys.modules):
            if key == package_name or key.startswith(package_name + "."):
                del sys.modules[key]

        opened = []
        controller = types.SimpleNamespace(
            show_search=lambda: opened.append("opened")
        )
        controller_module = types.ModuleType(package_name + ".controller")
        controller_module.create_controller = lambda *_args, **_kwargs: controller
        sys.modules[controller_module.__name__] = controller_module

        menu = _Menu()
        main_window = types.SimpleNamespace(
            addonManager=_AddonManager(),
            form=types.SimpleNamespace(menuTools=menu),
        )
        aqt = types.ModuleType("aqt")
        aqt.mw = main_window
        aqt.gui_hooks = types.SimpleNamespace(
            top_toolbar_did_init_links=_Hook()
        )
        qt = types.ModuleType("aqt.qt")
        qt.QAction = _Action
        qt.QKeySequence = _KeySequence
        qt.Qt = types.SimpleNamespace(
            ShortcutContext=types.SimpleNamespace(ApplicationShortcut=1)
        )
        old_aqt = sys.modules.get("aqt")
        old_qt = sys.modules.get("aqt.qt")
        sys.modules["aqt"] = aqt
        sys.modules["aqt.qt"] = qt
        try:
            spec = importlib.util.spec_from_file_location(
                package_name,
                ROOT / "__init__.py",
                submodule_search_locations=[str(ROOT)],
            )
            assert spec and spec.loader
            module = importlib.util.module_from_spec(spec)
            sys.modules[package_name] = module
            spec.loader.exec_module(module)
        finally:
            if old_aqt is None:
                sys.modules.pop("aqt", None)
            else:
                sys.modules["aqt"] = old_aqt
            if old_qt is None:
                sys.modules.pop("aqt.qt", None)
            else:
                sys.modules["aqt.qt"] = old_qt

        self.assertEqual(len(menu.actions), 1)
        action = menu.actions[0]
        self.assertEqual(action.text, "Smart Search…")
        self.assertEqual(action.shortcut.value, "Ctrl+K")
        self.assertIsNotNone(action.triggered.callback)

        self.assertEqual(len(aqt.gui_hooks.top_toolbar_did_init_links), 1)
        add_toolbar_link = aqt.gui_hooks.top_toolbar_did_init_links[0]
        toolbar = _Toolbar()
        links = [
            '<a id="decks">Decks</a>',
            '<a id="add">Add</a>',
            '<a id="browse">Browse</a>',
            '<a id="stats">Stats</a>',
            '<a id="sync">Sync</a>',
        ]
        add_toolbar_link(links, toolbar)

        self.assertEqual(len(toolbar.created), 1)
        created = toolbar.created[0]
        self.assertEqual(created["cmd"], "smart_search_medical_toolbar")
        self.assertEqual(created["label"], "Smart")
        self.assertEqual(created["id"], "smart-search-medical")
        self.assertEqual(
            [self._link_id(link) for link in links],
            ["decks", "add", "browse", "smart-search-medical", "stats", "sync"],
        )
        smart_link = links[3]
        self.assertIn('aria-label="Smart"', smart_link)
        self.assertIn("<svg", smart_link)
        self.assertIn('aria-hidden="true"', smart_link)
        self.assertIn(">Smart</a>", smart_link)

        created["func"]()
        self.assertEqual(opened, ["opened"])

        # Repeated toolbar draws or duplicate hook registration must not add a
        # second entry.
        add_toolbar_link(links, toolbar)
        self.assertEqual(
            sum("smart-search-medical" in link for link in links),
            1,
        )
        self.assertEqual(len(toolbar.created), 1)

    @staticmethod
    def _link_id(link: str) -> str:
        marker = 'id="'
        start = link.index(marker) + len(marker)
        return link[start : link.index('"', start)]


if __name__ == "__main__":
    unittest.main()
