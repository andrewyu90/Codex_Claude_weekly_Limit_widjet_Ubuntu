"""Check source-install output without network, package installs, or a real home."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


class SourceInstallTests(unittest.TestCase):
    def test_source_install_preserves_desktop_icon_and_project_files(self):
        project = Path(__file__).resolve().parents[1]
        script = (project / "install.sh").read_text(encoding="utf-8")
        match = re.search(r"^python3 - \"\$quota_source_dir\" <<'PY'\n(.*)\nPY\s*$",
                          script, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match)
        with tempfile.TemporaryDirectory(prefix="quota-source-install-") as temporary:
            root = Path(temporary)
            source, user_home, data_home = root / "source", root / "user", root / "data"
            source.mkdir()
            user_home.mkdir()
            for filename in ("pyproject.toml", "README.md", "LICENSE"):
                shutil.copy2(project / filename, source / filename)
            project_files = ("CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md", "Makefile",
                             "requirements-dev.txt", "MANIFEST.in")
            for filename in project_files:
                (source / filename).write_text(f"# Fixture for {filename}\n", encoding="utf-8")
            package = source / "quota_widget"
            (package / "assets").mkdir(parents=True)
            (package / "__main__.py").write_text("# Fixture; the app is not started.\n")
            shutil.copy2(project / "quota_widget/assets/icon.svg", package / "assets/icon.svg")
            self.assertFalse((source / "assets").exists())

            def fake_run(command, **kwargs):
                if command[1:3] == ["-m", "venv"]:
                    python = Path(command[3]) / "bin/python"
                    python.parent.mkdir(parents=True)
                    python.write_text("# Mock interpreter; never executed.\n")
                elif command[1:3] == ["-m", "pip"]:
                    staged_icon = Path(command[-1]) / "quota_widget/assets/icon.svg"
                    self.assertTrue(staged_icon.is_file())
                else:
                    self.fail("Unexpected installer subprocess")
                return subprocess.CompletedProcess(command, 0)

            # Path.home is mocked only in this process; HOME is not reassigned.
            with (mock.patch.object(Path, "home", return_value=user_home),
                  mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(data_home), "SUDO_USER": ""}),
                  mock.patch.object(sys, "argv", ["-", str(source)]),
                  mock.patch.object(subprocess, "run", side_effect=fake_run) as run,
                  mock.patch.object(shutil, "which", return_value=None),
                  contextlib.redirect_stdout(io.StringIO())):
                exec(compile(match.group(1), "install.sh/<python>", "exec"), {"__name__": "__main__"})

            desktop = data_home / "applications/quota-widget.desktop"
            icon_line = next(line for line in desktop.read_text().splitlines() if line.startswith("Icon="))
            installed_icon = Path(icon_line.split("=", 1)[1])
            self.assertTrue(installed_icon.is_file())
            self.assertEqual(installed_icon.read_bytes(), (package / "assets/icon.svg").read_bytes())
            for filename in project_files:
                installed = data_home / "quota-widget/app" / filename
                self.assertEqual(installed.read_bytes(), (source / filename).read_bytes())
            self.assertEqual(run.call_count, 2)
            self.assertTrue((user_home / ".local/bin/quota-widget").is_file())


if __name__ == "__main__":
    unittest.main()
