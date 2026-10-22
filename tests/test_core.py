"""Behavioural checks for quota observations, alarms and a fake Codex transport.

No test reads a real authentication file, connects to a provider, or starts a
model turn. The subprocess fixture implements only the documented read flow.
"""
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
from unittest import mock

from quota_widget.alarms import AlarmStore
from quota_widget.codex_provider import CodexProvider, ProviderError
from quota_widget.models import display_snapshot, parse_codex, snapshot
from quota_widget.storage import read_json, write_json


NOW = 1_900_000_000.0
ACCOUNT = {"type": "chatgpt", "email": "fixture@example.invalid", "planType": "pro"}


def quota(used=100, reset=NOW + 100, duration=10080):
    return {"usedPercent": used, "windowDurationMins": duration, "resetsAt": reset}


def observation(used=100, reset=NOW + 100, at=NOW, account="account-a", state="ok"):
    result = snapshot("codex", state)
    result.update(account_key=account, observed_at=at, received_at=at)
    result["windows"] = [{"key": "codex:secondary", "kind": "weekly", "bucket": "codex",
                          "label": "Weekly limit", "used_percent": used, "resets_at": reset,
                          "duration_minutes": 10080}]
    return result


class QuotaObservationTests(unittest.TestCase):
    def test_weekly_is_identified_by_duration_in_either_slot(self):
        for weekly_slot, short_slot in (("primary", "secondary"), ("secondary", "primary")):
            with self.subTest(weekly_slot=weekly_slot):
                data = parse_codex({"rateLimits": {weekly_slot: quota(31), short_slot: quota(7, duration=300)}}, ACCOUNT, NOW)
                weekly = [w for w in data["windows"] if w["kind"] == "weekly"]
                self.assertEqual(len(weekly), 1)
                self.assertEqual(weekly[0]["used_percent"], 31)
                self.assertTrue(weekly[0]["key"].endswith(weekly_slot))

    def test_multiple_buckets_remain_separate(self):
        payload = {"rateLimitsByLimitId": {"codex_other": {"primary": quota(79)}, "codex": {"secondary": quota(21)}}}
        data = parse_codex(payload, ACCOUNT, NOW)
        self.assertEqual([(w["bucket"], w["used_percent"]) for w in data["windows"]],
                         [("codex", 21), ("codex_other", 79)])
        self.assertEqual(len({w["key"] for w in data["windows"]}), 2)

    def test_missing_window_does_not_mean_zero_used(self):
        for payload in ({}, {"rateLimits": {}}, {"rateLimits": {"primary": None, "secondary": None}}):
            with self.subTest(payload=payload):
                data = parse_codex(payload, ACCOUNT, NOW)
                self.assertEqual(data["windows"], [])
                self.assertNotEqual(data["state"], "ok")

    def test_invalid_percentages_are_not_accepted_as_capacity(self):
        for bad in (None, "0", True, -1, 101, float("nan"), float("inf")):
            with self.subTest(bad=bad):
                data = parse_codex({"rateLimits": {"secondary": quota(bad)}}, ACCOUNT, NOW)
                self.assertEqual(data["windows"], [])

    def test_invalid_reset_does_not_create_a_valid_window(self):
        data = parse_codex({"rateLimits": {"secondary": quota(10, reset="tomorrow")}}, ACCOUNT, NOW)
        self.assertEqual(data["windows"], [])

    def test_unknown_duration_is_not_assumed_to_be_weekly(self):
        data = parse_codex({"rateLimits": {"secondary": {"usedPercent": 30, "resetsAt": NOW + 60}}}, ACCOUNT, NOW)
        self.assertEqual(data["windows"][0]["kind"], "other")
        self.assertIsNone(data["windows"][0]["duration_minutes"])

    def test_stale_data_preserves_historical_percentage(self):
        original = observation(used=96, reset=NOW + 1000)
        before = copy.deepcopy(original)
        displayed = display_snapshot(original, NOW + 181)
        self.assertEqual(displayed["state"], "stale")
        self.assertEqual(displayed["windows"][0]["used_percent"], 96)
        self.assertEqual(original, before)

    def test_elapsed_reset_does_not_refill_or_confirm_recovery(self):
        displayed = display_snapshot(observation(used=100, reset=NOW - 1), NOW)
        self.assertEqual(displayed["state"], "stale")
        self.assertEqual(displayed["windows"][0]["used_percent"], 100)
        self.assertIn("Waiting", displayed["message"])

    def test_private_account_fields_not_in_snapshot(self):
        data = parse_codex({"rateLimits": {"secondary": quota(15)}}, ACCOUNT, NOW)
        self.assertNotIn(ACCOUNT["email"], json.dumps(data))
        self.assertTrue(data["account_key"])


class AlarmBehaviourTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quota-alarms-test-")
        self.path = Path(self.temp.name) / "alarms.json"
        self.store = AlarmStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_first_observation_never_claims_a_reset(self):
        self.store.observe(observation(used=0), NOW)
        self.assertEqual(self.store.pending(NOW), [])

    def test_recovery_does_not_require_full_capacity(self):
        self.store.observe(observation(used=100), NOW)
        self.store.observe(observation(used=98, at=NOW + 1), NOW + 1)
        events = self.store.pending(NOW + 1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "recovery")
        self.assertIn("2%", events[0]["body"])

    def test_next_period_recovery_detected_below_full_remaining(self):
        self.store.observe(observation(used=73), NOW)
        self.store.observe(observation(used=12, reset=NOW + 604800, at=NOW + 2), NOW + 2)
        self.assertEqual([e["kind"] for e in self.store.pending(NOW + 2)], ["recovery"])

    def test_minor_decrease_in_same_period_does_not_claim_reset(self):
        self.store.observe(observation(used=73), NOW)
        self.store.observe(observation(used=72, at=NOW + 1), NOW + 1)
        self.assertEqual(self.store.pending(NOW + 1), [])

    def test_same_observation_does_not_write_disk_or_duplicate(self):
        self.store.observe(observation(), NOW)
        with mock.patch("quota_widget.alarms.write_json", wraps=write_json) as writer:
            self.store.observe(observation(), NOW + 1)
            self.assertEqual(writer.call_count, 0)
        self.assertEqual(self.store.pending(NOW + 1), [])

    def test_restart_keeps_sent_recovery_deduplicated(self):
        self.store.observe(observation(), NOW)
        recovered = observation(used=80, at=NOW + 1)
        self.store.observe(recovered, NOW + 1)
        event = self.store.pending(NOW + 1)[0]
        self.store.record_delivery(event["id"], True, NOW + 1)
        reloaded = AlarmStore(self.path)
        reloaded.observe(recovered, NOW + 2)
        self.assertEqual(reloaded.pending(NOW + 2), [])
        self.assertEqual(len(reloaded.state["events"]), 1)

    def test_legacy_notifications_use_english_without_changing_history_or_writing(self):
        self.store.observe(observation(), NOW)
        self.store.observe(observation(used=37.5, at=NOW + 1), NOW + 1)
        # A later reading must not replace the original recovery percentage.
        latest = observation(used=93, at=NOW + 2)
        self.store.observe(latest, NOW + 2)
        self.store.due(NOW + 100)
        recovery = next(e for e in self.store.state["events"] if e["kind"] == "recovery")
        self.store.record_delivery(recovery["id"], False, NOW + 100)
        legacy = read_json(self.path)
        for event in legacy["events"]:
            event["title"] = "Ancienne notification de Claude"
            event["body"] = ("Quota hebdomadaire restant : 62.5 %. Vérifiez les autres limites."
                             if event["kind"] == "recovery" else "L'heure de réinitialisation est arrivée.")
        write_json(self.path, legacy)
        original_file = self.path.read_bytes()

        with mock.patch("quota_widget.alarms.write_json", wraps=write_json) as writer:
            reloaded = AlarmStore(self.path)
            self.assertEqual([e["kind"] for e in reloaded.pending(NOW + 399)], ["due"])
            pending = reloaded.pending(NOW + 400)
            self.assertEqual({e["kind"] for e in pending}, {"due", "recovery"})
            recovered = next(e for e in pending if e["kind"] == "recovery")
            due = next(e for e in pending if e["kind"] == "due")
            self.assertEqual(recovered["title"], "Codex: Recovery confirmed")
            self.assertEqual(recovered["body"],
                "Weekly remaining: 62.5%. Other usage limits may still apply.")
            self.assertNotIn("7%", recovered["body"])
            self.assertEqual(due["title"], "Codex: Reset due")
            self.assertEqual(due["body"],
                "The scheduled reset time has passed. Recovery will be confirmed when a new usage reading arrives.")
            # Repeated observations and an already-recorded due time stay deduplicated.
            reloaded.observe(latest, NOW + 150)
            reloaded.due(NOW + 401)
            self.assertEqual(len(reloaded.state["events"]), len(legacy["events"]))
            self.assertEqual([e["kind"] for e in reloaded.pending(NOW + 3602)], ["due"])
            writer.assert_not_called()

        for old, current in zip(legacy["events"], reloaded.state["events"]):
            self.assertEqual({k: v for k, v in old.items() if k not in ("title", "body")},
                             {k: v for k, v in current.items() if k not in ("title", "body")})
        self.assertEqual(reloaded.state["windows"], legacy["windows"])
        self.assertEqual(reloaded.state["active"], legacy["active"])
        self.assertEqual(self.path.read_bytes(), original_file)

    def test_legacy_recovery_keeps_only_an_unambiguous_valid_percentage(self):
        generic = "Weekly capacity recovery was confirmed by an earlier reading. Other usage limits may still apply."
        for body, percentage in (
            ("Capacité rétablie : 0%.", "0"),
            ("Capacité rétablie : 100 %.", "100"),
            ("Capacité rétablie : 1.23456789e-06%.", "1.23456789e-06"),
            ("Capacité rétablie.", None),
            ("Capacité rétablie : 20% ou 40%.", None),
            ("Capacité rétablie : -1%.", None),
            ("Capacité rétablie : 100.1%.", None),
            ("Capacité rétablie : 12,5%.", None),
            ("Capacité rétablie : 20% et 120%.", None),
            ("Capacité rétablie : 1e999%.", None),
        ):
            with self.subTest(body=body):
                event = {"id": "original-event-id", "identity": "claude|account-a|claude:weekly",
                         "kind": "recovery", "title": "Ancienne notification de Codex", "body": body,
                         "created_at": NOW - 10, "sent_at": None, "attempts": 2, "next_attempt": NOW}
                write_json(self.path, {"active": {"claude": "account-a"}, "events": [event]})
                reloaded = AlarmStore(self.path)
                pending = reloaded.pending(NOW)
                self.assertEqual(len(pending), 1)
                self.assertEqual(pending[0]["title"], "Claude: Recovery confirmed")
                expected = (f"Weekly remaining: {percentage}%. Other usage limits may still apply."
                            if percentage is not None else generic)
                self.assertEqual(pending[0]["body"], expected)
                self.assertEqual({k: v for k, v in pending[0].items() if k not in ("title", "body")},
                                 {k: v for k, v in event.items() if k not in ("title", "body")})

    def test_unknown_and_old_data_cannot_trigger_recovery(self):
        self.store.observe(observation(), NOW)
        for item in (observation(used=0, state="error", at=NOW + 1),
                     observation(used=0, state="waiting", at=NOW + 1),
                     observation(used=0, at=NOW - 200),
                     observation(used=0, at=NOW + 1000)):
            with self.subTest(item=item):
                self.store.observe(item, NOW + 1)
                self.assertEqual(self.store.pending(NOW + 1), [])

    def test_changed_active_account_suppresses_old_pending_and_due(self):
        self.store.observe(observation(), NOW)
        self.store.observe(observation(used=90, at=NOW + 1), NOW + 1)
        self.assertEqual(len(self.store.pending(NOW + 1)), 1)
        self.store.observe(observation(used=5, reset=NOW + 500, at=NOW + 2, account="account-b"), NOW + 2)
        self.assertEqual(self.store.pending(NOW + 2), [])
        self.store.due(NOW + 101)
        self.assertEqual(self.store.pending(NOW + 101), [])

    def test_due_alarm_is_distinct_from_confirmed_recovery(self):
        self.store.observe(observation(), NOW)
        self.store.due(NOW + 100)
        first = self.store.pending(NOW + 100)
        self.assertEqual([e["kind"] for e in first], ["due"])
        self.assertIn("Reset due", first[0]["title"])
        self.store.due(NOW + 101)
        self.assertEqual(len(self.store.pending(NOW + 101)), 1)
        self.store.observe(observation(used=10, reset=NOW + 604800, at=NOW + 102), NOW + 102)
        self.assertEqual({e["kind"] for e in self.store.pending(NOW + 102)}, {"due", "recovery"})


FAKE_SERVER = r'''
import json, os, signal, sys
mode, log_path = sys.argv[1:]
if mode == "ignore-term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
def send(value):
    print(json.dumps(value), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    with open(log_path, "a", encoding="utf-8") as output:
        output.write(json.dumps({"pid": os.getpid(), "request": request}) + "\n")
        output.flush()
    method = request.get("method")
    if method == "initialized":
        continue
    if method == "initialize":
        print("an ignorable non-JSON diagnostic", flush=True)
        send({"method": "account/rateLimits/updated", "params": {"rateLimits": {}}})
        send({"id": 987654, "result": {"old": True}})
        result = {"userAgent": "fake-test-server"}
    elif method == "account/read":
        result = {"account": {"type": "apiKey"} if mode == "apikey" else
                  {"type": "chatgpt", "email": "fixture@example.invalid", "planType": "pro"}}
    elif method == "account/rateLimits/read":
        if mode in ("timeout", "ignore-term"):
            continue
        result = {"rateLimits": {"limitId": "codex", "primary":
                  {"usedPercent": 22, "windowDurationMins": 10080, "resetsAt": 2100000000}}}
    else:
        raise RuntimeError("unexpected method: " + str(method))
    send({"id": request["id"], "result": result})
'''


class CodexTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quota-codex-test-")
        self.directory = Path(self.temp.name)
        self.script = self.directory / "fake_server.py"
        self.script.write_text(FAKE_SERVER, encoding="utf-8")
        self.log = self.directory / "requests.jsonl"
        self.providers = []

    def tearDown(self):
        for provider in self.providers:
            provider.close()
        self.temp.cleanup()

    def provider(self, mode="normal", timeout=1):
        value = CodexProvider(command=[sys.executable, str(self.script), mode, str(self.log)],
                              cwd=str(self.directory), timeout=timeout)
        self.providers.append(value)
        return value

    def records(self):
        if not self.log.exists():
            return []
        rows = []
        for line in self.log.read_text().splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue  # A writer may currently be appending its last line.
        return rows

    def wait_for_request(self, method, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rows = self.records()
            if any(r["request"].get("method") == method for r in rows):
                return rows
            time.sleep(0.01)
        self.fail("fake server did not receive " + method)

    def assert_process_gone(self, pid):
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_persistent_process_uses_only_read_protocol_and_tolerates_notifications(self):
        provider = self.provider()
        first = provider.fetch()
        pid = provider.proc.pid
        second = provider.fetch()
        self.assertEqual(provider.proc.pid, pid)
        self.assertEqual(first["windows"][0]["used_percent"], 22)
        self.assertEqual(second["state"], "ok")
        methods = [r["request"].get("method") for r in self.records()]
        self.assertEqual(methods, ["initialize", "initialized", "account/read", "account/rateLimits/read",
                                   "account/read", "account/rateLimits/read"])
        self.assertNotIn("turn/start", methods)
        self.assertNotIn("thread/start", methods)
        provider.close()
        self.assert_process_gone(pid)

    def test_api_key_account_never_queries_subscription_quota(self):
        provider = self.provider("apikey")
        self.assertEqual(provider.fetch()["state"], "unsupported")
        methods = [r["request"].get("method") for r in self.records()]
        self.assertEqual(methods, ["initialize", "initialized", "account/read"])

    def test_timeout_cleans_up_child_process(self):
        provider = self.provider("timeout", timeout=0.2)
        with self.assertRaises(ProviderError):
            provider.fetch()
        rows = self.records()
        self.assertTrue(rows)
        self.assertIsNone(provider.proc)
        self.assert_process_gone(rows[0]["pid"])

    def test_close_cancels_inflight_fetch_even_when_child_ignores_sigterm(self):
        provider = self.provider("ignore-term", timeout=30)
        failures = []
        def fetch():
            try:
                provider.fetch()
            except Exception as error:
                failures.append(error)
        thread = threading.Thread(target=fetch, daemon=True)
        thread.start()
        records = self.wait_for_request("account/rateLimits/read")
        start = time.monotonic()
        provider.close()
        thread.join(timeout=0.5)
        elapsed = time.monotonic() - start
        self.assertFalse(thread.is_alive())
        self.assertLess(elapsed, 4.0)
        self.assertTrue(failures)
        self.assertIsNone(provider.proc)
        self.assert_process_gone(records[0]["pid"])


class StorageBehaviourTests(unittest.TestCase):
    def test_atomic_state_is_private_and_invalid_replacement_keeps_old_value(self):
        with tempfile.TemporaryDirectory(prefix="quota-storage-test-") as directory:
            path = Path(directory) / "private" / "state.json"
            write_json(path, {"value": 7})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            with self.assertRaises(ValueError):
                write_json(path, {"value": float("nan")})
            self.assertEqual(read_json(path), {"value": 7})
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_corrupted_state_returns_unknown_default(self):
        with tempfile.TemporaryDirectory(prefix="quota-storage-test-") as directory:
            path = Path(directory) / "state.json"
            path.write_text('{"unfinished":', encoding="utf-8")
            self.assertEqual(read_json(path, {"state": "unknown"}), {"state": "unknown"})


if __name__ == "__main__":
    unittest.main()
