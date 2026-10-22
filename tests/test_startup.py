"""Regression cases for the Linux XKB startup failure."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from quota_widget.startup import find_xkb_root, prepare_gui_environment, usable_xkb_root


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux startup handling")
class XkbStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.make_root("xkeyboard-config-2")

    def make_root(self, name):
        root = self.base / name
        for directory in ("rules", "keycodes", "types", "compat", "symbols"):
            (root / directory).mkdir(parents=True)
        (root / "rules/evdev").write_text("test rules\n")
        return root

    def test_invalid_inherited_paths_are_repaired_before_gui(self):
        env = {"XKB_CONFIG_ROOT": "/absent/build/prefix", "QT_XKB_CONFIG_ROOT": "/absent/qt"}
        prepare_gui_environment(env, [self.root])
        self.assertEqual(env["XKB_CONFIG_ROOT"], str(self.root))
        self.assertEqual(env["QT_XKB_CONFIG_ROOT"], str(self.root))
        self.assertEqual(env["QT_QPA_PLATFORMTHEME"], "generic")

    def test_complete_custom_root_and_explicit_qt_settings_are_preserved(self):
        custom = self.make_root("custom")
        env = {"XKB_CONFIG_ROOT": str(custom), "QT_XKB_CONFIG_ROOT": str(custom),
               "QT_QPA_PLATFORMTHEME": "qt6ct", "QT_QPA_PLATFORM": "wayland",
               "XKB_DEFAULT_LAYOUT": "kr"}
        before = env.copy()
        self.assertEqual(prepare_gui_environment(env, [self.root]), str(custom))
        self.assertEqual(env, before)

    def test_valid_qt_path_also_repairs_gdk_root(self):
        env = {"XKB_CONFIG_ROOT": "", "QT_XKB_CONFIG_ROOT": "/absent:" + str(self.root)}
        old_qt_value = env["QT_XKB_CONFIG_ROOT"]
        prepare_gui_environment(env, [])
        self.assertEqual(env["XKB_CONFIG_ROOT"], str(self.root))
        self.assertEqual(env["QT_XKB_CONFIG_ROOT"], old_qt_value)

    def test_incomplete_root_and_extensions_are_not_selected(self):
        incomplete = self.base / "xkeyboard-config-2.d"
        (incomplete / "rules").mkdir(parents=True)
        (incomplete / "rules/evdev").write_text("incomplete")
        self.assertFalse(usable_xkb_root(incomplete))
        self.assertEqual(find_xkb_root({}, [incomplete, self.root]), str(self.root))

    def test_missing_data_has_actionable_error_and_no_fabricated_root(self):
        env = {}
        with self.assertRaisesRegex(RuntimeError, "xkb-data"):
            prepare_gui_environment(env, [])
        self.assertNotIn("XKB_CONFIG_ROOT", env)

    def test_headless_screenshots_remain_available_without_keyboard_data(self):
        env = {"QT_QPA_PLATFORM": "offscreen"}
        self.assertIsNone(prepare_gui_environment(env, []))
        self.assertEqual(env["QT_QPA_PLATFORM"], "offscreen")

    def test_gui_diagnostics_never_imports_qt_or_starts_account_providers(self):
        script = (
            "import json, sys\n"
            "from quota_widget.startup import gui_diagnostics\n"
            "value = gui_diagnostics()\n"
            "assert not any(n.startswith('PySide6.') for n in sys.modules)\n"
            "assert 'quota_widget.codex_provider' not in sys.modules\n"
            "print(json.dumps(value))\n"
        )
        run = subprocess.run([sys.executable, "-c", script], text=True, capture_output=True,
                             timeout=10, check=True)
        self.assertIn("detected_xkb_root", json.loads(run.stdout))


if __name__ == "__main__":
    unittest.main()
