"""Offline state/upgrade checks; no real account or user settings are read."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quota_widget.claude_state import (
    clean_observation, merge_direct, read_observation, save_observation,
)
from quota_widget.models import display_snapshot, snapshot
from quota_widget.storage import config_dir, load_settings, read_json, save_settings, write_json


NOW = 1800000000.0


def observation(*, used=72.0, at=NOW, account="account-a", kind="direct", state="ok"):
    data = snapshot("claude", state)
    data.update(source_kind=kind, source="offline direct" if kind == "direct" else "offline session",
                account_key=account, account_label="Fixture subscription",
                received_at=at, observed_at=at, last_attempt_at=at,
                windows=[{"key": "claude:seven_day", "kind": "weekly", "bucket": "claude",
                          "label": "Weekly limit", "used_percent": used,
                          "resets_at": NOW + 86400, "duration_minutes": 10080}])
    return data


class ClaudeStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="quota-state-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "state"
        for context in (
            patch.object(Path, "home", return_value=self.root / "home"),
            patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config"),
                                    "QUOTA_WIDGET_DATA_DIR": str(self.data),
                                    "CLAUDE_CONFIG_DIR": str(self.root / "claude")}),
        ):
            context.start()
            self.addCleanup(context.stop)

    def test_failed_query_preserves_original_observation_without_mutating_it(self):
        previous = observation(at=NOW - 600)
        original = copy.deepcopy(previous)
        failure = snapshot("claude", "error", "Network timeout")
        failure.update(source_kind="direct", error_code="timeout", received_at=NOW,
                       observed_at=None, last_attempt_at=NOW, account_key="account-a", retry_after=120)
        merged = merge_direct(previous, failure)
        self.assertEqual(merged["state"], "error")
        self.assertEqual(merged["windows"], previous["windows"])
        self.assertEqual(merged["observed_at"], NOW - 600)
        self.assertEqual(merged["received_at"], NOW)
        self.assertEqual(merged["last_attempt_at"], NOW)
        self.assertEqual(merged["retry_after"], 120)
        merged["windows"][0]["used_percent"] = 1
        self.assertEqual(previous, original)

    def test_account_change_and_authentication_failure_do_not_reuse_old_quota(self):
        previous = observation(used=100)
        for fields in (
            {"error_code": "login_required"},
            {"error_code": "identity_changed"},
            {"error_code": "scope_missing"},
            {"error_code": "subscription_unsupported"},
            {"error_code": "query_failed", "account_key": "account-b"},
            {"error_code": "query_failed", "clear_cache": True},
        ):
            with self.subTest(fields=fields):
                failure = snapshot("claude", "error")
                failure.update(source_kind="direct", **fields)
                merged = merge_direct(previous, failure)
                self.assertEqual(merged["windows"], [])
                self.assertIsNone(merged["observed_at"])
                self.assertNotEqual(merged["account_key"], "account-a")

    def test_unknown_and_invalid_usage_do_not_become_zero(self):
        absent = read_observation(automatic=True, startup=True, data_dir=self.data)
        self.assertEqual(absent["windows"], [])
        self.assertNotEqual(absent["state"], "ok")
        for value in (None, True, "0", -1, 101, float("nan"), float("inf")):
            with self.subTest(value=value):
                cleaned = clean_observation(observation(used=value))
                self.assertEqual(cleaned["windows"], [])
                self.assertNotEqual(cleaned["state"], "ok")
        zero = clean_observation(observation(used=0))
        self.assertEqual(zero["state"], "ok")
        self.assertEqual(zero["windows"][0]["used_percent"], 0)

    def test_restart_cache_is_historical_until_a_new_query_succeeds(self):
        saved = observation(used=88, at=NOW - 10)
        save_observation(saved, self.data)
        restored = read_observation(automatic=True, startup=True, data_dir=self.data)
        self.assertEqual(restored["state"], "stale")
        self.assertEqual(restored["source_kind"], "direct")
        self.assertEqual(restored["observed_at"], NOW - 10)
        self.assertEqual(restored["windows"][0]["used_percent"], 88)
        fresh = merge_direct(restored, observation(used=88, at=NOW))
        self.assertEqual(fresh["state"], "ok")
        self.assertEqual(fresh["observed_at"], NOW)

    def test_legacy_cache_presentation_is_english_without_rewriting_observations(self):
        for automatic in (True, False):
            for state in ("ok", "waiting", "error", "stale", "unsupported"):
                with self.subTest(automatic=automatic, state=state):
                    legacy = observation(used=91, at=NOW - 600,
                                         kind="direct" if automatic else "session", state=state)
                    legacy.update(message="Lecture précédente de la session.", source="Frühere Abfrage",
                                  account_label="Abonnement précédent", received_at=NOW - 400,
                                  last_attempt_at=NOW - 50, retry_after=120, cli_version="2.1.306",
                                  error_code="timeout" if state == "error" else "")
                    legacy["windows"][0]["label"] = "Limite hebdomadaire"
                    legacy["windows"].append({
                        "key": "claude:five_hour", "kind": "short", "bucket": "claude",
                        "label": "Fünfstundenlimit", "used_percent": 12.5,
                        "resets_at": NOW + 3600, "duration_minutes": 300,
                    })
                    path = self.data / ("claude-direct.json" if automatic else "claude.json")
                    write_json(path, legacy)
                    original_bytes, original_stat = path.read_bytes(), path.stat()

                    restored = read_observation(automatic=automatic, data_dir=self.data)

                    self.assertEqual(restored["source"], "Claude Code usage query" if automatic else "Claude Code status line")
                    self.assertEqual(restored["account_label"], "Claude subscription" if automatic else
                                     "Claude subscription · latest observed session")
                    self.assertEqual(restored["windows"][0]["label"], "Weekly limit · all models" if automatic else "Weekly limit")
                    self.assertEqual(restored["windows"][1]["label"], "5-hour limit")
                    self.assertNotEqual(restored["message"], legacy["message"])
                    self.assertIn("Claude", restored["message"])
                    if state == "error":
                        self.assertIn("timed out", restored["message"])
                    for field in ("state", "source_kind", "account_key", "received_at", "observed_at",
                                  "last_attempt_at", "error_code", "retry_after", "cli_version"):
                        self.assertEqual(restored[field], legacy[field])
                    for original, current in zip(legacy["windows"], restored["windows"]):
                        self.assertEqual({key: value for key, value in current.items() if key != "label"},
                                         {key: value for key, value in original.items() if key != "label"})
                    self.assertEqual(path.read_bytes(), original_bytes)
                    self.assertEqual(path.stat().st_ino, original_stat.st_ino)
                    self.assertEqual(path.stat().st_mtime_ns, original_stat.st_mtime_ns)

    def test_current_provider_message_is_preserved_without_cache_rewording(self):
        incoming = observation()
        incoming["message"] = "Current CLI observation; one optional limit field was unavailable."
        self.assertEqual(clean_observation(incoming)["message"], incoming["message"])
        self.assertEqual(merge_direct(observation(at=NOW - 600), incoming)["message"], incoming["message"])

    def test_cached_direct_plan_label_preserves_only_supported_names(self):
        plans = ("Claude Pro", "Claude Max", "Claude Team", "Claude Enterprise")
        for label in (*plans, "Abonnement précédent", "private-account@example.invalid", "Claude max"):
            with self.subTest(label=label):
                cached = observation(at=NOW - 600)
                cached["account_label"] = label
                write_json(self.data / "claude-direct.json", cached)
                restored = read_observation(automatic=True, data_dir=self.data)
                self.assertEqual(restored["account_label"], label if label in plans else "Claude subscription")
                self.assertEqual(restored["account_key"], cached["account_key"])
                self.assertEqual(restored["observed_at"], cached["observed_at"])

    def test_session_files_cannot_override_direct_cache_or_fill_direct_failures(self):
        session = observation(used=0, at=NOW + 1, kind="session", account="session-key")
        direct = observation(used=91, at=NOW)
        write_json(self.data / "claude.json", session)
        save_observation(direct, self.data)
        automatic = read_observation(automatic=True, data_dir=self.data)
        legacy = read_observation(automatic=False, data_dir=self.data)
        self.assertEqual(automatic["source_kind"], "direct")
        self.assertEqual(automatic["windows"][0]["used_percent"], 91)
        self.assertEqual(legacy["source_kind"], "session")
        self.assertEqual(legacy["windows"][0]["used_percent"], 0)
        failure = snapshot("claude", "error")
        failure.update(source_kind="direct", error_code="timeout")
        self.assertEqual(merge_direct(legacy, failure)["windows"], [])
        self.assertEqual(read_json(self.data / "claude.json"), session)

    def test_missing_weekly_response_retains_only_explicit_historical_values(self):
        result = snapshot("claude", "ok")
        result.update(source_kind="direct", account_key="account-a", received_at=NOW, observed_at=NOW)
        merged = merge_direct(observation(used=100, at=NOW - 500), result)
        self.assertEqual(merged["state"], "stale")
        self.assertEqual(merged["observed_at"], NOW - 500)
        self.assertEqual(merged["windows"][0]["used_percent"], 100)

    def test_aging_direct_and_session_data_preserves_the_source_and_numbers(self):
        for kind, expected in (("direct", "No recent query"), ("session", "last session reading")):
            with self.subTest(kind=kind):
                displayed = display_snapshot(observation(used=100, kind=kind, at=NOW - 600), now=NOW)
                self.assertEqual(displayed["state"], "stale")
                self.assertEqual(displayed["source_kind"], kind)
                self.assertIn(expected, displayed["message"])
                self.assertEqual(displayed["windows"][0]["used_percent"], 100)

    def test_legacy_settings_default_to_automatic_claude_without_losing_preferences(self):
        legacy = {"poll_seconds": 120, "always_on_top": False, "notifications": False,
                  "reset_due_notifications": True, "codex_binary": "/fixture/codex"}
        write_json(config_dir() / "settings.json", legacy)
        migrated = load_settings()
        for key, value in legacy.items():
            self.assertEqual(migrated[key], value)
        self.assertIs(migrated["claude_auto_query"], True)
        self.assertEqual(migrated["claude_poll_seconds"], 60)
        self.assertEqual(migrated["claude_binary"], "claude")
        save_settings(migrated)
        self.assertEqual(load_settings(), migrated)

    def test_claude_interval_bounds_and_explicit_session_mode_survive_reload(self):
        for raw, expected in ((5, 60), (120, 120), (10000, 900), (True, 60), ("30", 60)):
            with self.subTest(interval=raw):
                write_json(config_dir() / "settings.json", {
                    "claude_auto_query": False, "claude_poll_seconds": raw, "poll_seconds": 45,
                })
                settings = load_settings()
                self.assertIs(settings["claude_auto_query"], False)
                self.assertEqual(settings["claude_poll_seconds"], expected)
                self.assertEqual(settings["poll_seconds"], 45)


if __name__ == "__main__":
    unittest.main()
