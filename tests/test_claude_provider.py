"""Usage-only protocol tests; all accounts, subprocesses and files are fixtures."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from quota_widget import claude_provider as provider


MOCK_CLI = r'''
import json, os, signal, subprocess, sys, time
from pathlib import Path
fixture = json.loads(Path(os.environ["QUOTA_TEST_FIXTURE"]).read_text())
log = Path(fixture["log"])
def record(value):
    with log.open("a") as out:
        out.write(json.dumps(value) + "\n")
record({"argv": sys.argv[1:], "config_env": os.environ.get("CLAUDE_CONFIG_DIR"), "pid": os.getpid()})
if sys.argv[1:] == ["--version"]:
    print(fixture.get("version", "2.1.306 (Claude Code)"), flush=True)
    sys.exit(0)
def read():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    value = json.loads(line)
    record(value)
    return value
def respond(request, payload):
    print(json.dumps({"type": "control_response", "response": {
        "subtype": "success", "request_id": request["request_id"], "response": payload}}), flush=True)
init = read()
assert init["type"] == "control_request" and init["request"] == {"subtype": "initialize"}
respond(init, {"account": fixture.get("account", {})})
usage = read()
assert usage["type"] == "control_request" and usage["request"] == {"subtype": "get_usage", "skip_behaviors": True}
mode = fixture.get("mode", "ok")
if mode == "approval":
    print(json.dumps({"type":"control_request", "request_id":"approval-1", "request": {
        "subtype":"can_use_tool", "tool_name":"Bash", "input":{"command":"DO_NOT_RUN"}}}), flush=True)
    reply = read()
    assert reply["type"] == "control_response" and reply["response"]["subtype"] == "error"
    sys.exit(0)
if mode == "error":
    print(json.dumps({"type":"control_response", "response": {
        "subtype":"error", "request_id":usage["request_id"], "error":"PRIVATE_SERVER_SECRET"}}), flush=True)
    sys.exit(0)
if mode == "unexpected_turn":
    print(json.dumps({"type":"assistant", "message":{"content":"PRIVATE_CONTENT"}}), flush=True)
    time.sleep(10)
if mode == "stdout_limit":
    sys.stdout.write("X" * 1_048_577)
    sys.stdout.flush()
    time.sleep(10)
if mode == "stderr_limit":
    sys.stderr.write("PRIVATE_STDERR" * 400_000)
    sys.stderr.flush()
    time.sleep(10)
if mode == "messages_limit":
    for _ in range(1100):
        print('{"type":"system","subtype":"keep_alive"}', flush=True)
    time.sleep(10)
if mode in ("hang", "child"):
    if mode == "child":
        child = subprocess.Popen([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"])
        record({"child":child.pid})
    record({"waiting":True})
    time.sleep(30)
if mode == "malformed":
    print("THIS_IS_NOT_JSON_PRIVATE", flush=True)
    sys.exit(0)
respond(usage, fixture["payload"])
# A proper client closes stdin without submitting a user turn.
tail = sys.stdin.read()
if tail:
    record({"unexpected_tail":tail})
'''


class ClaudeProviderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = self.root / "custom profile"
        self.config.mkdir()
        self.data = self.root / "state"
        self.fixture_path = self.root / "fixture.json"
        self.log = self.root / "wire.jsonl"
        self.script = self.root / "mock-claude"
        self.script.write_text("#!" + sys.executable + "\n" + MOCK_CLI)
        self.script.chmod(0o700)
        self.now = 1800000000.0
        self.reset = "2027-01-16T12:00:00+00:00"
        self.account = {"email": "fixture@example.invalid", "subscriptionType": "Claude Max",
                        "apiProvider": "firstParty"}
        self.limits = {
            "seven_day": {"utilization": 67.5, "resets_at": self.reset},
            "five_hour": {"utilization": 12, "resets_at": self.reset},
            "seven_day_opus": {"utilization": 99, "resets_at": self.reset},
            "limits": [{"kind": "weekly_all", "group": "weekly", "percent": 67.5,
                        "resets_at": self.reset, "severity": "normal", "is_active": True}],
        }
        self.payload = {"session": {"total_cost_usd": 0, "model_usage": {}},
                        "subscription_type": "max", "rate_limits_available": True,
                        "rate_limits": copy.deepcopy(self.limits), "behaviors": None}
        self.fixture = {"log": str(self.log), "account": self.account, "payload": self.payload}
        for context in (
            patch.dict(os.environ, {"HOME": str(self.root), "CLAUDE_CONFIG_DIR": str(self.config),
                                    "QUOTA_WIDGET_DATA_DIR": str(self.data),
                                    "QUOTA_TEST_FIXTURE": str(self.fixture_path)}),
            patch.object(provider.time, "time", side_effect=lambda: self.now),
        ):
            context.start()
            self.addCleanup(context.stop)
        self.write_fixture()

    def write_fixture(self):
        self.fixture_path.write_text(json.dumps(self.fixture))

    def metadata(self, *, age=10, path=None, email=None, cache_uuid=None, limits=None):
        target = path or self.config / ".claude.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "oauthAccount": {"accountUuid": "fixture-account", "emailAddress": email or self.account["email"]},
            "cachedUsageUtilization": {"accountUuid": cache_uuid or "fixture-account",
                                       "fetchedAtMs": (self.now - age) * 1000,
                                       "utilization": copy.deepcopy(limits if limits is not None else self.limits)},
            "unrelated": "PRIVATE_UNRELATED_VALUE",
        }
        target.write_text(json.dumps(value))
        return target

    def make_provider(self, **kwargs):
        options = {"command": [sys.executable, str(self.script)], "timeout": 3}
        options.update(kwargs)
        instance = provider.ClaudeProvider(**options)
        self.addCleanup(instance.close)
        return instance

    def records(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_only_usage_control_requests_are_sent_and_private_data_is_not_returned(self):
        metadata = self.metadata()
        before = metadata.read_bytes()
        credential = self.config / ".credentials.json"
        credential.write_text('"PRIVATE_CREDENTIAL_MUST_NOT_BE_READ"')
        original_open = provider.os.open

        def guarded_open(path, *args, **kwargs):
            self.assertNotEqual(Path(path).name, ".credentials.json")
            return original_open(path, *args, **kwargs)

        with patch.object(provider.os, "open", side_effect=guarded_open):
            result = self.make_provider().fetch()
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["source_kind"], "direct")
        self.assertEqual(result["observed_at"], self.now - 10)
        self.assertEqual([w["used_percent"] for w in result["windows"]], [67.5, 12.0])
        self.assertEqual([w["kind"] for w in result["windows"]], ["weekly", "short"])
        requests = [item for item in self.records() if item.get("type") == "control_request"]
        self.assertEqual([item["request"] for item in requests], [
            {"subtype": "initialize"}, {"subtype": "get_usage", "skip_behaviors": True}])
        self.assertFalse(any("unexpected_tail" in item for item in self.records()))
        rendered = json.dumps(result)
        for secret in (self.account["email"], "fixture-account", "PRIVATE_", str(self.config)):
            self.assertNotIn(secret, rendered)
        self.assertEqual(metadata.read_bytes(), before)
        self.assertFalse((self.data / "claude.json").exists())
        self.assertEqual((self.data / "claude-runtime").stat().st_mode & 0o777, 0o700)

    def test_old_seeded_data_keeps_original_time_even_when_requested_again(self):
        self.metadata(age=600)
        self.payload["rate_limits"].pop("limits")
        self.write_fixture()
        instance = self.make_provider()
        first = instance.fetch()
        self.now += 90
        second = instance.fetch()
        self.assertEqual(first["state"], "stale")
        self.assertEqual(second["observed_at"], first["observed_at"])
        self.assertEqual(second["windows"], first["windows"])
        self.assertEqual(second["retry_after"], 60)
        self.assertEqual(len([r for r in self.records() if "argv" in r]), 2)

    def test_unknown_seeded_timestamp_is_not_freshened(self):
        self.payload["rate_limits"].pop("limits")
        self.write_fixture()
        result = self.make_provider().fetch()
        self.assertEqual(result["state"], "stale")
        self.assertIsNone(result["observed_at"])
        self.assertEqual(result["windows"][0]["used_percent"], 67.5)

    def test_valid_live_rows_can_supply_recent_time_without_readable_metadata(self):
        result = self.make_provider().fetch()
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["observed_at"], self.now)
        self.assertEqual(result["freshness_source"], "cli_live_rows")
        self.assertIn("1-minute cache", result["message"])

    def test_mismatched_live_row_does_not_prove_freshness(self):
        self.payload["rate_limits"]["limits"][0]["percent"] = 12
        self.write_fixture()
        result = self.make_provider().fetch()
        self.assertEqual(result["state"], "stale")
        self.assertIsNone(result["observed_at"])

    def test_cache_identity_and_values_must_match_the_current_response(self):
        self.payload["rate_limits"].pop("limits")
        self.write_fixture()
        for changes in ({"email": "other@example.invalid"}, {"cache_uuid": "other-account"},
                        {"limits": {**self.limits, "seven_day": {"utilization": 42, "resets_at": self.reset}}},
                        {"age": -600}):
            with self.subTest(changes=changes):
                self.metadata(**changes)
                result = self.make_provider().fetch()
                self.assertIsNone(result["observed_at"])
                if "email" in changes or "cache_uuid" in changes:
                    self.assertEqual(result["error_code"], "account_changed")
                    self.assertTrue(result["clear_cache"])
                    self.assertEqual(result["windows"], [])
                else:
                    self.assertEqual(result["state"], "stale")

    def test_live_rows_cannot_rescue_a_known_account_mismatch(self):
        for changes in ({"email": "other@example.invalid"}, {"cache_uuid": "other-account"}):
            with self.subTest(changes=changes):
                self.metadata(**changes)
                result = self.make_provider().fetch()
                self.assertEqual(result["error_code"], "account_changed")
                self.assertTrue(result["clear_cache"])
                self.assertTrue(result["account_key"])
                self.assertEqual(result["windows"], [])

    def test_new_live_values_may_replace_older_cache_for_the_same_account(self):
        self.metadata(limits={**self.limits, "seven_day": {"utilization": 42, "resets_at": self.reset}})
        result = self.make_provider().fetch()
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["freshness_source"], "cli_live_rows")
        self.assertEqual(result["windows"][0]["used_percent"], 67.5)

    def test_account_change_changes_key_and_auth_failure_explicitly_clears_history(self):
        self.metadata()
        instance = self.make_provider()
        old = instance.fetch()
        self.account["email"] = "new-account@example.invalid"
        self.payload.update(rate_limits_available=False, rate_limits=None)
        self.write_fixture()
        new = instance.fetch()
        self.assertNotEqual(old["account_key"], new["account_key"])
        self.assertTrue(new["account_key"])
        self.assertTrue(new["clear_cache"])
        self.assertEqual(new["error_code"], "scope_missing")
        self.assertEqual(new["windows"], [])

    def test_login_subscription_and_scope_absence_are_distinct(self):
        cases = [
            ({}, None, "login_required", "waiting"),
            ({"email": "api@example.invalid", "apiKeySource": "ANTHROPIC_API_KEY"}, None,
             "subscription_unsupported", "unsupported"),
            (self.account, "max", "scope_missing", "unsupported"),
        ]
        for account, plan, code, state in cases:
            with self.subTest(code=code):
                self.fixture["account"] = account
                self.payload.update(rate_limits_available=False, subscription_type=plan, rate_limits=None)
                self.write_fixture()
                result = self.make_provider().fetch()
                self.assertEqual((result["error_code"], result["state"]), (code, state))
                self.assertTrue(result["clear_cache"])
                self.assertIsNone(result["observed_at"])

    def test_missing_weekly_never_becomes_zero_or_model_scoped_sum(self):
        self.payload["rate_limits"].pop("seven_day")
        self.write_fixture()
        result = self.make_provider().fetch()
        self.assertEqual(result["error_code"], "weekly_missing")
        self.assertFalse(any(w["kind"] == "weekly" for w in result["windows"]))

    def test_expired_window_is_historical_even_with_fresh_read(self):
        self.payload["rate_limits"]["seven_day"] = {"utilization": 100, "resets_at": "2026-01-01T00:00:00Z"}
        self.payload["rate_limits"]["limits"][0].update(percent=100, resets_at="2026-01-01T00:00:00Z")
        self.write_fixture()
        result = self.make_provider().fetch()
        self.assertEqual(result["state"], "stale")
        self.assertEqual(result["error_code"], "weekly_expired")
        self.assertEqual(result["windows"][0]["used_percent"], 100)

    def test_iso_timezone_conversion_and_zero_used_are_valid(self):
        self.payload["rate_limits"]["seven_day"] = {"utilization": 0, "resets_at": "2027-01-16T21:00:00+09:00"}
        self.payload["rate_limits"]["limits"][0].update(percent=0, resets_at=self.reset)
        self.write_fixture()
        result = self.make_provider().fetch()
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["windows"][0]["used_percent"], 0)
        self.assertEqual(result["windows"][0]["resets_at"], provider._reset(self.reset))

    def test_nonfinite_boolean_out_of_range_and_naive_dates_are_rejected(self):
        for value in (True, -1, 101, float("nan"), float("inf"), "42"):
            with self.subTest(value=value):
                self.payload["rate_limits"]["seven_day"]["utilization"] = value
                self.write_fixture()
                result = self.make_provider().fetch()
                self.assertEqual(result["error_code"], "invalid_schema")
                self.assertFalse(any(w["kind"] == "weekly" for w in result["windows"]))
        self.payload["rate_limits"]["seven_day"] = {"utilization": 42, "resets_at": "2027-01-16T12:00:00"}
        self.write_fixture()
        self.assertEqual(self.make_provider().fetch()["error_code"], "invalid_schema")

    def test_unknown_payload_and_raw_errors_are_sanitized(self):
        for mode in ("error", "malformed", "unexpected_turn"):
            with self.subTest(mode=mode):
                self.fixture["mode"] = mode
                self.write_fixture()
                result = self.make_provider().fetch()
                self.assertEqual(result["state"], "error")
                self.assertTrue(result["account_key"])
                self.assertNotIn("PRIVATE", json.dumps(result))
        self.fixture["mode"] = "ok"
        self.payload["rate_limits_available"] = "yes"
        self.write_fixture()
        self.assertEqual(self.make_provider().fetch()["error_code"], "invalid_schema")

    def test_inbound_approval_is_rejected_without_any_user_turn(self):
        self.fixture["mode"] = "approval"
        self.write_fixture()
        result = self.make_provider().fetch()
        self.assertEqual(result["error_code"], "approval_required")
        # The error is written to the pipe before the process group is stopped.
        self.assertFalse(any(item.get("type") == "user" for item in self.records()))
        self.assertFalse(any(item.get("type") == "control_response"
                             and item.get("response", {}).get("subtype") == "success"
                             for item in self.records()))

    def test_json_line_stderr_and_message_count_are_bounded(self):
        for mode in ("stdout_limit", "stderr_limit", "messages_limit"):
            with self.subTest(mode=mode):
                self.fixture["mode"] = mode
                self.write_fixture()
                instance = self.make_provider()
                result = instance.fetch()
                self.assertEqual(result["error_code"], "output_limit")
                self.assertIsNone(instance.proc)
                self.assertNotIn("PRIVATE", json.dumps(result))

    def test_timeout_and_abort_terminate_the_process_group(self):
        self.fixture["mode"] = "hang"
        self.write_fixture()
        timed = self.make_provider(timeout=0.3)
        self.assertEqual(timed.fetch()["error_code"], "timeout")
        self.assertIsNone(timed.proc)
        self.fixture["mode"] = "child"
        self.write_fixture()
        instance = self.make_provider(timeout=10)
        results = []
        thread = threading.Thread(target=lambda: results.append(instance.fetch()))
        thread.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not any("child" in r for r in self.records()):
            time.sleep(0.01)
        children = [r["child"] for r in self.records() if "child" in r]
        self.assertTrue(children)
        instance.abort()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0]["error_code"], "cancelled")
        self.assertIsNone(instance.proc)
        status = Path(f"/proc/{children[-1]}/stat")
        self.assertTrue(not status.exists() or status.read_text().split()[2] == "Z")
        self.assertEqual(instance.fetch()["error_code"], "cancelled")

    def test_version_preflight_checks_supported_version_and_exact_safe_flags(self):
        self.metadata()
        result = self.make_provider(command=None, binary=str(self.script)).fetch()
        self.assertEqual(result["state"], "ok")
        self.assertEqual(result["cli_version"], "2.1.306")
        boots = [r["argv"] for r in self.records() if "argv" in r]
        self.assertEqual(boots, [["--version"], provider._FLAGS])
        self.fixture["version"] = "2.1.305 (Claude Code)"
        self.write_fixture()
        result = self.make_provider(command=None, binary=str(self.script)).fetch()
        self.assertEqual(result["error_code"], "version_unsupported")
        self.assertEqual(result["state"], "unsupported")
        self.assertEqual(len([r for r in self.records() if "argv" in r]), 3)

    def test_unrecognized_version_and_missing_binary_have_safe_errors(self):
        self.fixture["version"] = "PRIVATE_VERSION_BANNER"
        self.write_fixture()
        result = self.make_provider(command=None, binary=str(self.script)).fetch()
        self.assertEqual(result["error_code"], "version_unknown")
        self.assertNotIn("PRIVATE", json.dumps(result))
        result = self.make_provider(command=None, binary=str(self.root / "missing")).fetch()
        self.assertEqual(result["error_code"], "cli_not_found")

    def test_default_profile_compatibility_does_not_copy_or_modify_metadata(self):
        default = self.root / ".claude"
        default.mkdir()
        global_file = self.metadata(path=self.root / ".claude.json")
        before = global_file.read_bytes()
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(default)}):
            result = self.make_provider().fetch()
        self.assertEqual(result["state"], "ok")
        self.assertTrue(result["default_profile_compatibility"])
        self.assertIsNone(self.records()[0]["config_env"])
        self.assertFalse((default / ".claude.json").exists())
        self.assertEqual(global_file.read_bytes(), before)

    def test_custom_profile_never_falls_back_to_global_account(self):
        self.metadata(path=self.root / ".claude.json")
        self.payload["rate_limits"].pop("limits")
        self.write_fixture()
        result = self.make_provider().fetch()
        self.assertEqual(result["state"], "stale")
        self.assertIsNone(result["observed_at"])
        self.assertNotIn("default_profile_compatibility", result)
        self.assertEqual(self.records()[0]["config_env"], str(self.config))

    def test_explicit_default_profile_with_identity_is_preserved(self):
        default = self.root / ".claude"
        self.metadata(path=default / ".claude.json")
        self.metadata(path=self.root / ".claude.json", email="different@example.invalid")
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(default)}):
            result = self.make_provider().fetch()
        self.assertEqual(result["state"], "ok")
        self.assertNotIn("default_profile_compatibility", result)
        self.assertEqual(self.records()[0]["config_env"], str(default))

    def test_empty_configuration_env_uses_original_default_metadata(self):
        self.metadata(path=self.root / ".claude.json")
        with patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": ""}):
            result = self.make_provider().fetch()
        self.assertEqual(result["observed_at"], self.now - 10)
        self.assertIsNone(self.records()[0]["config_env"])

    def test_metadata_symlink_is_not_followed(self):
        private = self.root / "private.json"
        self.metadata(path=private)
        (self.config / ".claude.json").symlink_to(private)
        self.payload["rate_limits"].pop("limits")
        self.write_fixture()
        result = self.make_provider().fetch()
        self.assertEqual(result["state"], "stale")
        self.assertIsNone(result["observed_at"])


if __name__ == "__main__":
    unittest.main()
