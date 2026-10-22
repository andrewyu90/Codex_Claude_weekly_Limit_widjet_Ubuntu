"""Read-only diagnostics tests using temporary settings and synthetic callbacks."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from quota_widget import claude_bridge as bridge
from quota_widget import claude_diagnostics as diagnostics


class ClaudeDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / "private config"
        self.data = self.root / "private state"
        self.now = 1800000000.0
        for context in (
            patch.dict(os.environ, {
                "HOME": str(self.root), "CLAUDE_CONFIG_DIR": str(self.config),
                "QUOTA_WIDGET_DATA_DIR": str(self.data),
            }),
            patch.object(bridge.time, "time", return_value=self.now),
            patch.object(diagnostics.shutil, "which", return_value=None),
        ):
            context.start()
            self.addCleanup(context.stop)

    def diagnose(self, **kwargs):
        return diagnostics.diagnosis(self.config, self.data, include_version=False, now=self.now, **kwargs)

    def receive(self, *, used=25, reset=None, observed_age=0, heartbeat_age=0):
        snapshot = bridge.process_statusline({
            "session_id": "private-session-id",
            "rate_limits": {"seven_day": {"used_percentage": used, "resets_at": reset or self.now + 86400}},
        }, self.data)
        snapshot["observed_at"] = self.now - observed_age
        snapshot["received_at"] = self.now - heartbeat_age
        (self.data / "claude.json").write_text(json.dumps(snapshot), encoding="utf-8")

    def test_unconnected_diagnosis_does_not_create_directories_or_start_commands(self):
        with patch.object(diagnostics.subprocess, "run") as run:
            result = self.diagnose()
        run.assert_not_called()
        self.assertEqual(result["state"], "not_connected")
        self.assertFalse(result["bridge_registered"])
        self.assertFalse(result["callback_received"])
        self.assertFalse(self.config.exists())
        self.assertFalse(self.data.exists())

    def test_registered_configuration_is_not_reported_as_a_received_callback(self):
        bridge.install_bridge(self.config, self.data)
        result = self.diagnose()
        self.assertEqual(result["state"], "callback_pending")
        self.assertTrue(result["bridge_registered"])
        self.assertFalse(result["callback_received"])
        self.assertEqual(result["settings_scope"], "user")
        self.assertFalse(result["effective_settings_verified"])
        self.assertTrue(result["callback_data_path_matches"])

    def test_received_callback_without_weekly_window_stays_unknown(self):
        bridge.install_bridge(self.config, self.data)
        bridge.process_statusline({"session_id": "fixture-session"}, self.data)
        result = self.diagnose()
        self.assertEqual(result["state"], "weekly_missing")
        self.assertTrue(result["callback_received"])
        self.assertFalse(result["weekly_present"])
        self.assertFalse(result["weekly_expired"])

    def test_zero_used_is_a_valid_weekly_value(self):
        bridge.install_bridge(self.config, self.data)
        self.receive(used=0)
        result = self.diagnose()
        self.assertEqual(result["state"], "ok")
        self.assertTrue(result["weekly_present"])
        self.assertEqual(result["observation_age_seconds"], 0)

    def test_expired_weekly_value_is_distinct_from_missing_and_recovered(self):
        bridge.install_bridge(self.config, self.data)
        self.receive(used=100, reset=self.now - 1)
        result = self.diagnose()
        self.assertEqual(result["state"], "weekly_expired")
        self.assertTrue(result["weekly_present"])
        self.assertTrue(result["weekly_expired"])

    def test_fresh_heartbeat_does_not_freshen_old_observation(self):
        bridge.install_bridge(self.config, self.data)
        self.receive(observed_age=600, heartbeat_age=5)
        result = self.diagnose()
        self.assertEqual(result["state"], "stale")
        self.assertEqual(result["observation_age_seconds"], 600)
        self.assertEqual(result["heartbeat_age_seconds"], 5)
        self.assertEqual(result["heartbeat_scope"], "selected_session")

    def test_callback_and_gui_using_different_data_paths_are_reported(self):
        bridge.install_bridge(self.config, self.data)
        other_data = self.root / "other state"
        other_data.mkdir()
        (other_data / "claude-bridge.json").write_bytes((self.data / "claude-bridge.json").read_bytes())
        result = diagnostics.diagnosis(self.config, other_data, include_version=False, now=self.now)
        self.assertEqual(result["state"], "path_mismatch")
        self.assertTrue(result["bridge_registered"])
        self.assertFalse(result["callback_data_path_matches"])

    def test_missing_interpreter_is_detected_without_executing_it(self):
        with patch.object(bridge.sys, "executable", str(self.root / "removed-venv/python")):
            bridge.install_bridge(self.config, self.data)
        result = self.diagnose()
        self.assertEqual(result["state"], "executable_missing")
        self.assertFalse(result["bridge_executable_available"])

    def test_changed_settings_are_reported_and_not_overwritten(self):
        bridge.install_bridge(self.config, self.data)
        path = self.config / "settings.json"
        settings = json.loads(path.read_text())
        settings["statusLine"]["command"] = "printf user-edited-command"
        path.write_text(json.dumps(settings))
        original = path.read_bytes()
        self.assertEqual(self.diagnose()["state"], "settings_changed")
        self.assertEqual(path.read_bytes(), original)

    def test_invalid_cache_and_timestamps_are_safely_reported(self):
        bridge.install_bridge(self.config, self.data)
        self.receive()
        path = self.data / "claude.json"
        snapshot = json.loads(path.read_text())
        snapshot["observed_at"] = self.now + 600
        path.write_text(json.dumps(snapshot))
        self.assertEqual(self.diagnose()["state"], "timestamp_invalid")
        path.write_text("[invalid SECRET JSON")
        result = self.diagnose()
        self.assertEqual(result["state"], "cache_error")
        self.assertNotIn("SECRET", json.dumps(result))

    def test_result_does_not_expose_paths_settings_commands_or_sessions(self):
        self.config.mkdir()
        (self.config / "settings.json").write_text(json.dumps({
            "env": {"PRIVATE_TEST_VALUE": "DO_NOT_RETURN_THIS"},
            "statusLine": {"type": "command", "command": "printf PRIVATE_COMMAND"},
        }))
        bridge.install_bridge(self.config, self.data)
        self.receive()
        before = {str(path): (path.read_bytes(), path.stat().st_mode)
                  for path in self.root.rglob("*") if path.is_file()}
        with patch.object(diagnostics.subprocess, "run") as run:
            result = self.diagnose()
        run.assert_not_called()
        rendered = json.dumps(result, ensure_ascii=False)
        for value in ("DO_NOT_RETURN_THIS", "PRIVATE_COMMAND", "private-session-id", str(self.root)):
            self.assertNotIn(value, rendered)
        after = {str(path): (path.read_bytes(), path.stat().st_mode)
                 for path in self.root.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_version_check_runs_only_version_and_returns_only_numeric_version(self):
        completed = subprocess.CompletedProcess(["claude", "--version"], 0, "2.1.268 (Claude Code)\nPRIVATE_OUTPUT")
        with patch.object(diagnostics.shutil, "which", return_value="/mock/bin/claude"), \
                patch.object(diagnostics.subprocess, "run", return_value=completed) as run:
            result = diagnostics.diagnosis(self.config, self.data, now=self.now)
        self.assertEqual(run.call_args.args[0], ["/mock/bin/claude", "--version"])
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["timeout"], diagnostics.VERSION_TIMEOUT_SECONDS)
        self.assertEqual(result["cli_version"], "2.1.268")
        self.assertEqual(result["version_status"], "ok")
        self.assertNotIn("PRIVATE_OUTPUT", json.dumps(result))

    def test_version_timeout_is_bounded_and_does_not_expose_process_output(self):
        with patch.object(diagnostics.shutil, "which", return_value="/mock/bin/claude"), \
                patch.object(diagnostics.subprocess, "run", side_effect=subprocess.TimeoutExpired(
                    ["claude", "--version"], 2, output="PRIVATE_OUTPUT")):
            result = diagnostics.diagnosis(self.config, self.data, now=self.now)
        self.assertEqual(result["version_status"], "timeout")
        self.assertIsNone(result["cli_version"])
        self.assertNotIn("PRIVATE_OUTPUT", json.dumps(result))

    def test_desktop_environment_finds_common_home_installations_outside_path(self):
        for relative in (".local/bin/claude", ".npm-global/bin/claude"):
            with self.subTest(relative=relative):
                executable = self.root / relative
                executable.parent.mkdir(parents=True, exist_ok=True)
                executable.write_text("#!/bin/sh\nexit 0\n")
                executable.chmod(0o700)
                with patch.object(diagnostics.subprocess, "run") as run:
                    result = self.diagnose()
                run.assert_not_called()
                self.assertTrue(result["cli_found"])
                self.assertEqual(result["version_status"], "not_checked")
                self.assertEqual(diagnostics._locate_cli(), str(executable))
                executable.unlink()


if __name__ == "__main__":
    unittest.main()
