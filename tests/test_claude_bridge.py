"""Behaviour tests for the credential-free Claude bridge."""

from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from quota_widget import claude_bridge as bridge
from quota_widget import storage


class ClaudeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / "Claude config with spaces"
        self.data = self.root / "widget state with spaces"
        self.environment = patch.dict(os.environ, {
            "CLAUDE_CONFIG_DIR": str(self.config),
            "QUOTA_WIDGET_DATA_DIR": str(self.data),
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.now = 1800000000.0
        self.clock = patch.object(bridge.time, "time", return_value=self.now)
        self.mock_time = self.clock.start()
        self.addCleanup(self.clock.stop)

    def payload(self):
        return {
            "session_id": "test-session",
            "rate_limits": {
                "seven_day": {"used_percentage": 41.2, "resets_at": self.now + 86400},
                "five_hour": {"used_percentage": 23.5, "resets_at": self.now + 3600},
            },
            "context_window": {"total_input_tokens": 1200, "total_output_tokens": 500},
            "cost": {"total_api_duration_ms": 600, "total_duration_ms": 5000},
        }

    def write_settings(self, settings):
        self.config.mkdir(parents=True, exist_ok=True)
        (self.config / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    def read_settings(self):
        return json.loads((self.config / "settings.json").read_text(encoding="utf-8"))

    def write_legacy_session(self):
        """Store the old, label-inclusive hash format using synthetic text."""
        self.mock_time.return_value = self.now
        payload = self.payload()
        result = bridge.process_statusline(payload, self.data)
        sidecar_path = self.data / "claude-observation.json"
        saved = json.loads(sidecar_path.read_text())
        record = saved["sessions"][result["account_key"]]
        for window, label in zip(record["snapshot"]["windows"], ("Limite hebdomadaire", "Fünfstundenlimit")):
            window["label"] = label
        legacy_input = {
            "account_key": result["account_key"],
            "windows": record["snapshot"]["windows"],
            "response": {"total_input_tokens": 1200.0, "total_output_tokens": 500.0,
                         "total_api_duration_ms": 600.0},
        }
        record["fingerprint"] = hashlib.sha256(json.dumps(
            legacy_input, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest()
        sidecar_path.write_text(json.dumps(saved), encoding="utf-8")
        (self.data / "claude.json").write_text(json.dumps(record["snapshot"]), encoding="utf-8")
        return payload, record["fingerprint"]

    def test_weekly_and_short_schema_and_zero_usage(self):
        payload = self.payload()
        payload["rate_limits"]["seven_day"]["used_percentage"] = 0
        result = bridge.process_statusline(payload, self.data)
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["windows"][0]["used_percent"], 0)
        self.assertEqual(result["windows"][0]["duration_minutes"], 10080)
        self.assertEqual(result["windows"][1]["duration_minutes"], 300)
        self.assertEqual(result["windows"][1]["kind"], "short")
        self.assertEqual(json.loads((self.data / "claude.json").read_text()), result)

    def test_heartbeat_does_not_refresh_unchanged_observation(self):
        payload = self.payload()
        first = bridge.process_statusline(payload, self.data)
        self.mock_time.return_value = self.now + 120
        payload["cost"]["total_duration_ms"] += 120000
        second = bridge.process_statusline(payload, self.data)
        self.assertEqual(first["observed_at"], second["observed_at"])
        self.assertEqual(second["received_at"], self.now + 120)

    def test_legacy_label_only_upgrade_preserves_observation_time(self):
        payload, legacy_fingerprint = self.write_legacy_session()
        self.mock_time.return_value = self.now + 600
        result = bridge.process_statusline(payload, self.data)
        self.assertEqual(result["observed_at"], self.now)
        self.assertEqual(result["received_at"], self.now + 600)
        self.assertEqual([window["label"] for window in result["windows"]], ["Weekly limit", "5-hour limit"])
        saved = json.loads((self.data / "claude-observation.json").read_text())
        record = saved["sessions"][result["account_key"]]
        self.assertNotEqual(record["fingerprint"], legacy_fingerprint)
        migrated_fingerprint = record["fingerprint"]

        self.mock_time.return_value = self.now + 1200
        repeated = bridge.process_statusline(payload, self.data)
        self.assertEqual(repeated["observed_at"], self.now)
        saved = json.loads((self.data / "claude-observation.json").read_text())
        self.assertEqual(saved["sessions"][result["account_key"]]["fingerprint"], migrated_fingerprint)

    def test_legacy_hash_migration_still_observes_counter_usage_and_reset_changes(self):
        for change in ("counter", "usage", "reset"):
            with self.subTest(change=change):
                payload, _ = self.write_legacy_session()
                if change == "counter":
                    payload["context_window"]["total_output_tokens"] += 50
                elif change == "usage":
                    payload["rate_limits"]["seven_day"]["used_percentage"] += 2
                else:
                    payload["rate_limits"]["seven_day"]["resets_at"] += 3600
                self.mock_time.return_value = self.now + 600
                result = bridge.process_statusline(payload, self.data)
                self.assertEqual(result["observed_at"], self.now + 600)

    def test_new_response_counter_refreshes_observation(self):
        payload = self.payload()
        bridge.process_statusline(payload, self.data)
        self.mock_time.return_value = self.now + 120
        payload["context_window"]["total_output_tokens"] += 50
        result = bridge.process_statusline(payload, self.data)
        self.assertEqual(result["observed_at"], self.now + 120)

    def test_rate_change_refreshes_observation(self):
        payload = self.payload()
        bridge.process_statusline(payload, self.data)
        self.mock_time.return_value = self.now + 120
        payload["rate_limits"]["seven_day"]["used_percentage"] = 44
        result = bridge.process_statusline(payload, self.data)
        self.assertEqual(result["observed_at"], self.now + 120)

    def test_session_switch_changes_identity(self):
        first = bridge.process_statusline(self.payload(), self.data)
        payload = self.payload()
        payload["session_id"] = "another-session"
        second = bridge.process_statusline(payload, self.data)
        self.assertNotEqual(first["account_key"], second["account_key"])

    def test_old_session_callbacks_do_not_replace_newer_observation(self):
        older = self.payload()
        older["rate_limits"]["seven_day"]["used_percentage"] = 100
        first = bridge.process_statusline(older, self.data)
        newer = self.payload()
        newer["session_id"] = "newer-session"
        newer["rate_limits"]["seven_day"]["used_percentage"] = 20
        self.mock_time.return_value = self.now + 60
        second = bridge.process_statusline(newer, self.data)
        self.mock_time.return_value = self.now + 120
        selected = bridge.process_statusline(older, self.data)
        self.assertEqual(selected["account_key"], second["account_key"])
        self.assertEqual(selected["windows"][0]["used_percent"], 20)
        self.assertEqual(selected["observed_at"], self.now + 60)
        saved = json.loads((self.data / "claude-observation.json").read_text())
        self.assertEqual(saved["sessions"][first["account_key"]]["observed_at"], self.now)

    def test_interleaved_unchanged_session_keeps_its_original_observation(self):
        older = self.payload()
        bridge.process_statusline(older, self.data)
        newer = self.payload()
        newer["session_id"] = "newer-session"
        self.mock_time.return_value = self.now + 60
        bridge.process_statusline(newer, self.data)
        self.mock_time.return_value = self.now + 120
        bridge.process_statusline(older, self.data)
        self.mock_time.return_value = self.now + 360
        selected = bridge.process_statusline(newer, self.data)
        self.assertEqual(selected["observed_at"], self.now + 60)
        self.assertEqual(selected["received_at"], self.now + 360)

    def test_new_session_without_weekly_data_does_not_discard_valid_data(self):
        first = bridge.process_statusline(self.payload(), self.data)
        self.mock_time.return_value = self.now + 120
        selected = bridge.process_statusline({"session_id": "just-started"}, self.data)
        self.assertEqual(selected["account_key"], first["account_key"])
        self.assertEqual(selected["observed_at"], self.now)
        self.assertEqual(selected["windows"][0]["used_percent"], 41.2)
        self.assertIn("latest observed session", selected["account_label"])

    def test_missing_window_remains_unknown(self):
        payload = self.payload()
        del payload["rate_limits"]["seven_day"]
        result = bridge.process_statusline(payload, self.data)
        self.assertEqual(result["state"], "waiting")
        self.assertFalse(any(window["kind"] == "weekly" for window in result["windows"]))

    def test_expired_window_is_not_refilled(self):
        payload = self.payload()
        payload["rate_limits"]["seven_day"] = {"used_percentage": 100, "resets_at": self.now - 1}
        result = bridge.process_statusline(payload, self.data)
        self.assertEqual(result["state"], "waiting")
        self.assertEqual(result["windows"][0]["used_percent"], 100)

    def test_invalid_percentages_do_not_become_zero(self):
        for value in (True, -1, 101, "40", None, float("nan"), float("inf"), 10 ** 1000):
            with self.subTest(value=str(value)[:30]):
                payload = self.payload()
                payload["rate_limits"]["seven_day"]["used_percentage"] = value
                result = bridge.process_statusline(payload, self.data)
                self.assertEqual(result["state"], "waiting")
                self.assertFalse(any(window["kind"] == "weekly" for window in result["windows"]))

    def test_non_object_payload_is_rejected(self):
        for value in (None, [], "hello", 12):
            with self.assertRaises(ValueError):
                bridge.process_statusline(value, self.data)

    def test_cache_is_sanitized_and_private(self):
        payload = self.payload()
        payload.update({"transcript_path": "/secret/transcript.json", "access_token": "VERY_SECRET", "workspace": {"cwd": "/private"}})
        payload["rate_limits"]["secret"] = "VERY_SECRET"
        bridge.process_statusline(payload, self.data)
        for path in self.data.iterdir():
            contents = path.read_text()
            self.assertNotIn("VERY_SECRET", contents)
            self.assertNotIn("/secret", contents)
            self.assertNotIn("test-session", contents)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.data.stat().st_mode), 0o700)

    def test_install_preserves_settings_and_is_idempotent(self):
        original = {"model": "user-model", "permissions": {"deny": ["Read(secrets)"]}, "statusLine": {"type": "command", "command": "printf original", "padding": 4, "refreshInterval": 10}}
        self.write_settings(original)
        bridge.install_bridge(self.config, self.data)
        installed = self.read_settings()
        self.assertEqual(installed["model"], original["model"])
        self.assertEqual(installed["permissions"], original["permissions"])
        self.assertEqual(installed["statusLine"]["padding"], 4)
        self.assertEqual(installed["statusLine"]["refreshInterval"], 10)
        self.assertIn("--claude-statusline", shlex.split(installed["statusLine"]["command"]))
        backup_before = (self.data / "claude-bridge.json").read_bytes()
        bridge.install_bridge(self.config, self.data)
        self.assertEqual(backup_before, (self.data / "claude-bridge.json").read_bytes())
        self.assertEqual(self.read_settings(), installed)

    def test_uninstall_preserves_later_unrelated_and_padding_changes(self):
        original = {"model": "old-model", "statusLine": {"type": "command", "command": "printf original", "padding": 2}}
        self.write_settings(original)
        bridge.install_bridge(self.config, self.data)
        modified = self.read_settings()
        modified["model"] = "new-model"
        modified["userPreference"] = True
        modified["statusLine"]["padding"] = 8
        self.write_settings(modified)
        bridge.uninstall_bridge(self.config, self.data)
        restored = self.read_settings()
        self.assertEqual(restored["statusLine"], {"type": "command", "command": "printf original", "padding": 8})
        self.assertEqual(restored["model"], "new-model")
        self.assertTrue(restored["userPreference"])
        self.assertFalse((self.data / "claude-bridge.json").exists())

    def test_uninstall_new_statusline_removes_only_our_field(self):
        self.write_settings({"model": "unchanged"})
        bridge.install_bridge(self.config, self.data)
        modified = self.read_settings()
        modified["laterSetting"] = 9
        self.write_settings(modified)
        bridge.uninstall_bridge(self.config, self.data)
        self.assertEqual(self.read_settings(), {"model": "unchanged", "laterSetting": 9})

    def test_conflicting_command_is_never_overwritten(self):
        permissions = {"deny": ["Read(secret)"], "allow": ["Bash(git status)"]}
        self.write_settings({"permissions": permissions})
        bridge.install_bridge(self.config, self.data)
        modified = self.read_settings()
        modified["statusLine"]["command"] = "printf my-new-statusline"
        self.write_settings(modified)
        original_bytes = (self.config / "settings.json").read_bytes()
        original_mode = stat.S_IMODE((self.config / "settings.json").stat().st_mode)
        for action in (bridge.install_bridge, bridge.uninstall_bridge):
            with self.assertRaises(bridge.BridgeError):
                action(self.config, self.data)
            self.assertEqual(self.read_settings(), modified)
            self.assertEqual(self.read_settings()["permissions"], permissions)
            self.assertEqual((self.config / "settings.json").read_bytes(), original_bytes)
            self.assertEqual(stat.S_IMODE((self.config / "settings.json").stat().st_mode), original_mode)

    def test_malformed_settings_are_preserved(self):
        self.config.mkdir()
        path = self.config / "settings.json"
        path.write_text("{bad json")
        with self.assertRaises(bridge.BridgeError):
            bridge.install_bridge(self.config, self.data)
        self.assertEqual(path.read_text(), "{bad json")

    def test_delegation_preserves_original_bytes(self):
        script = self.root / "echo_input.py"
        script.write_text("import sys\nsys.stdout.buffer.write(b'\\x1b[32m' + sys.stdin.buffer.read() + b'\\x1b[0m')\n")
        command = shlex.join([sys.executable, str(script)])
        self.write_settings({"statusLine": {"type": "command", "command": command}})
        bridge.install_bridge(self.config, self.data)
        raw = b'{ "session_id": "unchanged bytes", "rate_limits": {} }\n'
        self.assertEqual(bridge._delegate(raw, self.data), b"\x1b[32m" + raw + b"\x1b[0m")

    def test_run_statusline_records_data_and_keeps_original_display(self):
        self.write_settings({"statusLine": {"type": "command", "command": "printf '\\033[32mexisting display\\033[0m'"}})
        bridge.install_bridge(self.config, self.data)
        raw = json.dumps(self.payload()).encode("utf-8")
        input_buffer, output_buffer = io.BytesIO(raw), io.BytesIO()
        input_stream = io.TextIOWrapper(input_buffer, encoding="utf-8")
        output_stream = io.TextIOWrapper(output_buffer, encoding="utf-8", write_through=True)
        with patch.object(sys, "stdin", input_stream), patch.object(sys, "stdout", output_stream):
            self.assertEqual(bridge.run_statusline(), 0)
        self.assertEqual(output_buffer.getvalue(), b"\x1b[32mexisting display\x1b[0m")
        snapshot = json.loads((self.data / "claude.json").read_text())
        self.assertEqual(snapshot["windows"][0]["used_percent"], 41.2)

    def test_delegate_timeout_terminates_children(self):
        marker = self.root / "child_survived"
        child = "import time,pathlib; time.sleep(0.6); pathlib.Path(" + repr(str(marker)) + ").write_text('bad')"
        parent = self.root / "slow_statusline.py"
        parent.write_text("import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c'," + repr(child) + "])\ntime.sleep(10)\n")
        self.write_settings({"statusLine": {"type": "command", "command": shlex.join([sys.executable, str(parent)])}})
        bridge.install_bridge(self.config, self.data)
        with patch.object(bridge, "DELEGATE_TIMEOUT_SECONDS", 0.08):
            self.assertIsNone(bridge._delegate(b"{}", self.data))
        time.sleep(0.8)
        self.assertFalse(marker.exists(), "a timed-out delegated status line left a child running")


class SharedPathTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        environment = patch.dict(os.environ, {
            "HOME": str(self.home), "XDG_STATE_HOME": "", "XDG_CONFIG_HOME": "",
            "QUOTA_WIDGET_DATA_DIR": "", "CLAUDE_CONFIG_DIR": "",
        })
        environment.start()
        self.addCleanup(environment.stop)

    def test_empty_environment_uses_the_same_default_directories(self):
        self.assertEqual(storage.state_dir(), self.home / ".local/state/quota-widget")
        self.assertEqual(bridge._data_dir(), storage.state_dir())
        self.assertEqual(storage.config_dir(), self.home / ".config/quota-widget")
        self.assertEqual(storage.claude_config_dir(), self.home / ".claude")
        self.assertEqual(bridge._config_dir(), storage.claude_config_dir())

    def test_tilde_environment_is_expanded_for_widget_and_callback(self):
        with patch.dict(os.environ, {
            "XDG_STATE_HOME": "~/widget-state", "XDG_CONFIG_HOME": "~/widget-config",
            "CLAUDE_CONFIG_DIR": "~/claude-config",
        }):
            self.assertEqual(storage.state_dir(), self.home / "widget-state/quota-widget")
            self.assertEqual(storage.config_dir(), self.home / "widget-config/quota-widget")
            self.assertEqual(bridge._data_dir(), storage.state_dir())
            self.assertEqual(bridge._config_dir(), self.home / "claude-config")

    def test_relative_paths_are_home_based_independent_of_working_directory(self):
        with patch.dict(os.environ, {
            "XDG_STATE_HOME": "state-base", "XDG_CONFIG_HOME": "config-base",
            "QUOTA_WIDGET_DATA_DIR": "relative-cache", "CLAUDE_CONFIG_DIR": "relative-claude",
        }):
            for working_directory in ("/tmp/project-a", "/tmp/project-b"):
                with patch("os.getcwd", return_value=working_directory):
                    self.assertEqual(storage.state_dir(), self.home / "relative-cache")
                    self.assertEqual(bridge._data_dir(), storage.state_dir())
                    self.assertEqual(storage.claude_config_dir(), self.home / "relative-claude")
                    self.assertEqual(storage.config_dir(), self.home / "config-base/quota-widget")
                    self.assertEqual(storage.state_dir(Path("explicit")), self.home / "explicit")
            with patch.dict(os.environ, {"QUOTA_WIDGET_DATA_DIR": ""}):
                self.assertEqual(storage.state_dir(), self.home / "state-base/quota-widget")

    def test_chosen_symlink_path_is_not_resolved_to_another_identity(self):
        target = self.home / "actual-directory"
        target.mkdir()
        chosen = self.home / "chosen-directory"
        chosen.symlink_to(target, target_is_directory=True)
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(chosen)}):
            self.assertEqual(storage.claude_config_dir(), chosen)


if __name__ == "__main__":
    unittest.main()
