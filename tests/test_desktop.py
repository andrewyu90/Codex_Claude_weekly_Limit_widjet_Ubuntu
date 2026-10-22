"""Desktop integration in isolated folders; never alters a real login session."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from quota_widget.desktop import (MARKER, autostart, autostart_enabled,
                                 autostart_path, ensure_desktop_integration)


class DesktopIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quota-desktop-test-")
        self.root = Path(self.temp.name)
        self.env = mock.patch.dict(os.environ, {
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_DATA_HOME": str(self.root / "data"),
            "QUOTA_WIDGET_DATA_DIR": str(self.root / "state"),
            "CLAUDE_CONFIG_DIR": str(self.root / "claude"),
            "QUOTA_WIDGET_LAUNCHER": "",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_first_start_registers_menu_and_login_without_a_terminal(self):
        self.assertEqual(ensure_desktop_integration(), [])
        self.assertTrue(autostart_enabled())
        menu = self.root / "data/applications/quota-dock.desktop"
        content = menu.read_text()
        self.assertIn("Terminal=false", content)
        self.assertIn("QUOTA_WIDGET_DATA_DIR=" + str(self.root / "state"), content)
        self.assertIn("CLAUDE_CONFIG_DIR=" + str(self.root / "claude"), content)
        self.assertIn("--from-autostart", autostart_path().read_text())

    def test_opt_out_survives_later_launch_and_upgrade(self):
        ensure_desktop_integration()
        autostart(False)
        self.assertFalse(autostart_enabled())
        with mock.patch("quota_widget.desktop.sys.executable", "/opt/new/python"):
            self.assertEqual(ensure_desktop_integration(), [])
        self.assertFalse(autostart_enabled())
        self.assertIn("/opt/new/python", autostart_path().read_text())

    def test_removing_autostart_after_setup_does_not_reenable_it(self):
        ensure_desktop_integration()
        autostart_path().unlink()
        ensure_desktop_integration()
        self.assertFalse(autostart_path().exists())

    def test_unrelated_autostart_is_preserved_and_reported(self):
        path = autostart_path()
        path.parent.mkdir(parents=True)
        original = "[Desktop Entry]\nName=Another app\nExec=/bin/true\n"
        path.write_text(original)
        self.assertTrue(ensure_desktop_integration())
        self.assertEqual(path.read_text(), original)

    def test_symlink_launcher_is_not_overwritten(self):
        target = self.root / "keep.desktop"
        target.write_text("leave unchanged")
        menu = self.root / "data/applications/quota-dock.desktop"
        menu.parent.mkdir(parents=True)
        menu.symlink_to(target)
        self.assertTrue(ensure_desktop_integration())
        self.assertTrue(menu.is_symlink())
        self.assertEqual(target.read_text(), "leave unchanged")

    def test_owned_legacy_menu_is_hidden_after_successful_registration(self):
        legacy = self.root / "data/applications/quota-widget.desktop"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("[Desktop Entry]\nName=Quota Dock\n" + MARKER + "\n")
        self.assertEqual(ensure_desktop_integration(), [])
        self.assertIn("Hidden=true", legacy.read_text())
        self.assertTrue((legacy.parent / "quota-dock.desktop").is_file())


if __name__ == "__main__":
    unittest.main()
