"""Qt scheduler tests with controlled local workers; no real CLI is launched."""
from __future__ import annotations

from collections import deque
import copy
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QObject, QTimer, Signal
    from PySide6.QtWidgets import QApplication, QDialog, QWidget
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False

if QT_AVAILABLE:
    from quota_widget import app as application

from quota_widget.claude_state import save_observation
from quota_widget.models import snapshot
from quota_widget.storage import DEFAULT_SETTINGS, save_settings, write_json


def observation(provider, used=38.0, at=None, *, kind="direct"):
    now = time.time() if at is None else at
    result = snapshot(provider, "ok")
    result.update(source_kind=kind, source="offline fixture", account_key=provider + "-account",
                  observed_at=now, received_at=now,
                  windows=[{"key": provider + ":weekly", "kind": "weekly", "bucket": provider,
                            "label": "Weekly limit", "used_percent": used,
                            "resets_at": now + 86400, "duration_minutes": 10080}])
    return result


class FakeProvider:
    def __init__(self, name, *, blocked=False, slow_close=False, replies=()):
        self.name = name
        self.binary = None
        self.replies = deque(replies)
        self.calls = 0
        self.abort_calls = 0
        self.active = 0
        self.started = threading.Event()
        self.gate = threading.Event()
        if not blocked:
            self.gate.set()
        self.close_started = threading.Event()
        self.close_release = threading.Event()
        if not slow_close:
            self.close_release.set()
        self.close_done = threading.Event()
        self.close_daemon = None
        self.lock = threading.Lock()

    def fetch(self):
        with self.lock:
            self.calls += 1
            self.active += 1
            reply = self.replies.popleft() if self.replies else None
        self.started.set()
        try:
            if not self.gate.wait(3):
                raise RuntimeError("Offline fixture was not released")
            return copy.deepcopy(reply) if reply is not None else observation(self.name)
        finally:
            with self.lock:
                self.active -= 1

    def abort(self):
        self.abort_calls += 1
        self.gate.set()

    def close(self):
        self.close_daemon = threading.current_thread().daemon
        self.close_started.set()
        self.close_release.wait(3)
        self.close_done.set()


if QT_AVAILABLE:
    class FakeWindow(QWidget):
        refresh_requested = Signal()
        settings_requested = Signal()
        provider_action = Signal(str)
        pin_changed = Signal(bool)

        def __init__(self):
            super().__init__()
            self.updates = {}
            self.update_threads = set()
            self.refreshing = False
            self.intervals = None
            self.notices = []

        def update_provider(self, value):
            self.update_threads.add(threading.get_ident())
            self.updates[value["provider"]] = copy.deepcopy(value)

        def set_refreshing(self, value):
            self.refreshing = value

        def set_refresh_interval(self, seconds, claude_seconds=None):
            self.intervals = (seconds, claude_seconds)

        def set_pinned(self, value):
            self.pinned = value

        def notify(self, title, body):
            self.notices.append((title, body))

    class AcceptedSettings(QObject):
        connection_action = Signal(str)

        def __init__(self, values, parent):
            super().__init__(parent)
            self.new_values = values
            self.start_at_login = SimpleNamespace(isChecked=lambda: False)

        def exec(self):
            return QDialog.DialogCode.Accepted

        def values(self):
            return dict(self.new_values)


@unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed; controller GUI checks skipped")
class ControllerConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="quota-controller-test-")
        self.root = Path(self.temporary.name)
        self.created = []
        self.planned = {"codex": deque(), "claude": deque()}
        self.controller = None
        self.window = None
        self.monotonic = 1000.0
        self.patches = [
            patch.object(Path, "home", return_value=self.root / "home"),
            patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root / "config"),
                                    "QUOTA_WIDGET_DATA_DIR": str(self.root / "state"),
                                    "CLAUDE_CONFIG_DIR": str(self.root / "claude")}),
            patch.object(application, "time", SimpleNamespace(
                monotonic=lambda: self.monotonic, time=time.time)),
            patch.object(application, "CodexProvider", side_effect=lambda binary: self.create_provider("codex", binary)),
            patch.object(application, "ClaudeProvider", side_effect=lambda binary: self.create_provider("claude", binary)),
            patch.object(application, "desktop_notify", side_effect=AssertionError("Unexpected external notification")),
        ]
        for item in self.patches:
            item.start()
        settings = dict(DEFAULT_SETTINGS)
        settings.update(always_on_top=False, notifications=False, reset_due_notifications=False,
                        poll_seconds=60, claude_poll_seconds=60, claude_auto_query=True)
        save_settings(settings)

    def tearDown(self):
        try:
            # Release every fixture before waiting for non-daemon cleanup.
            for provider in self.created:
                provider.gate.set()
                provider.close_release.set()
            if self.controller is not None:
                self.controller.close()
            self.wait_until(lambda: all(p.active == 0 for p in self.created))
            self.wait_until(lambda: all(p.close_done.is_set() for p in self.created if p.abort_calls))
            self.app.processEvents()
            if self.window is not None:
                self.window.close()
                self.window.deleteLater()
            self.app.processEvents()
        finally:
            for item in reversed(self.patches):
                item.stop()
            self.temporary.cleanup()

    def create_provider(self, name, binary):
        provider = self.planned[name].popleft() if self.planned[name] else FakeProvider(name)
        provider.binary = binary
        self.created.append(provider)
        return provider

    def make_controller(self, *, codex=None, claude=None):
        if codex is not None:
            self.planned["codex"].append(codex)
        if claude is not None:
            self.planned["claude"].append(claude)
        self.window = FakeWindow()
        self.controller = application.Controller(self.window)
        # Startup tick remains real; later ticks are explicit and deterministic.
        self.controller.timer.stop()
        return self.controller

    def wait_until(self, condition, timeout=1.5):
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            self.app.processEvents()
            if condition():
                return
            threading.Event().wait(0.002)
        self.fail("Timed out waiting for an offline worker/event-loop condition")

    def check_independent_startup(self, blocked_name):
        blocked = FakeProvider(blocked_name, blocked=True)
        controller = self.make_controller(**{blocked_name: blocked})
        other = "codex" if blocked_name == "claude" else "claude"
        self.wait_until(lambda: blocked.started.is_set() and controller.raw[other]["state"] == "ok")
        self.assertTrue(controller.busy[blocked_name])
        self.assertFalse(controller.busy[other])
        self.assertEqual(controller.providers[other].calls, 1)
        self.assertEqual(self.window.update_threads, {threading.get_ident()})
        self.assertTrue(self.window.refreshing)
        blocked.gate.set()
        self.wait_until(lambda: not any(controller.busy.values()))
        self.assertFalse(self.window.refreshing)

    def test_blocked_claude_does_not_delay_codex_startup_result(self):
        self.check_independent_startup("claude")

    def test_blocked_codex_does_not_delay_claude_startup_result(self):
        self.check_independent_startup("codex")

    def test_obsolete_result_cannot_clear_a_new_worker_or_replace_its_state(self):
        controller = self.make_controller()
        self.wait_until(lambda: not any(controller.busy.values()))
        old = controller.providers["claude"]
        replacement = FakeProvider("claude", blocked=True)
        self.created.append(replacement)
        controller.providers["claude"] = replacement
        controller.last_attempt["claude"] = None
        controller._retire(old)
        controller._start_fetch("claude")
        self.wait_until(replacement.started.is_set)
        before = copy.deepcopy(controller.raw["claude"])
        deadline = controller.next_poll["claude"]
        controller.failures["claude"] = 2
        controller._result("claude", old, observation("claude", used=0))
        self.assertTrue(controller.busy["claude"])
        self.assertEqual(controller.raw["claude"], before)
        self.assertEqual(controller.next_poll["claude"], deadline)
        self.assertEqual(controller.failures["claude"], 2)
        replacement.gate.set()
        self.wait_until(lambda: not controller.busy["claude"])
        self.assertEqual(controller.failures["claude"], 0)

    def test_manual_claude_refresh_honors_sixty_seconds_and_server_cooldown(self):
        failure = snapshot("claude", "error", "Rate limited")
        failure.update(source_kind="direct", error_code="rate_limited", retry_after=180)
        claude = FakeProvider("claude", replies=[observation("claude"), observation("claude"), failure])
        controller = self.make_controller(codex=FakeProvider("codex", blocked=True), claude=claude)
        self.wait_until(lambda: not controller.busy["claude"])
        for moment in (1010, 1050):
            self.monotonic = moment
            controller.refresh()
            self.app.processEvents()
            self.assertEqual(claude.calls, 1)
        self.monotonic = 1060
        controller.refresh()
        self.wait_until(lambda: claude.calls == 2 and not controller.busy["claude"])
        self.monotonic = 1120
        controller.refresh()
        self.wait_until(lambda: claude.calls == 3 and not controller.busy["claude"])
        self.assertEqual(controller.raw["claude"]["state"], "error")
        self.assertEqual(controller.retry_not_before["claude"], 1300)
        for moment in (1180, 1290):
            self.monotonic = moment
            controller.refresh()
            controller.tick()
            self.app.processEvents()
            self.assertEqual(claude.calls, 3)
        self.monotonic = 1300
        controller.refresh()
        self.wait_until(lambda: claude.calls == 4 and not controller.busy["claude"])
        self.assertEqual(controller.raw["claude"]["state"], "ok")
        self.assertEqual(controller.retry_not_before["claude"], 0)

    def test_direct_mode_ignores_session_cache_during_startup_tick_and_refresh(self):
        previous = observation("claude", used=82, at=time.time() - 600)
        save_observation(previous)
        session_path = self.root / "state" / "claude.json"
        write_json(session_path, observation("claude", used=0, kind="session"))
        claude = FakeProvider("claude", blocked=True, replies=[observation("claude", used=49)])
        controller = self.make_controller(claude=claude)
        self.wait_until(claude.started.is_set)
        self.assertEqual(controller.raw["claude"]["state"], "stale")
        self.assertEqual(controller.raw["claude"]["observed_at"], previous["observed_at"])
        write_json(session_path, observation("claude", used=1, kind="session"))
        self.monotonic = 1010
        controller.tick()
        controller.refresh()
        self.assertEqual(controller.raw["claude"]["windows"][0]["used_percent"], 82)
        claude.gate.set()
        self.wait_until(lambda: not controller.busy["claude"])
        direct_time = controller.raw["claude"]["observed_at"]
        write_json(session_path, observation("claude", used=2, kind="session"))
        self.monotonic = 1020
        controller.tick()
        controller.refresh()
        self.assertEqual(controller.raw["claude"]["source_kind"], "direct")
        self.assertEqual(controller.raw["claude"]["windows"][0]["used_percent"], 49)
        self.assertEqual(controller.raw["claude"]["observed_at"], direct_time)

    def test_close_aborts_inflight_fetches_without_waiting_for_cleanup(self):
        providers = {name: FakeProvider(name, blocked=True, slow_close=True) for name in ("codex", "claude")}
        controller = self.make_controller(**providers)
        self.wait_until(lambda: all(p.started.is_set() for p in providers.values()))
        started = time.perf_counter()
        controller.close()
        self.assertLess(time.perf_counter() - started, 0.25)
        self.wait_until(lambda: all(p.close_started.is_set() for p in providers.values()))
        self.assertTrue(controller.closed)
        self.assertFalse(controller.timer.isActive())
        for provider in providers.values():
            self.assertEqual(provider.abort_calls, 1)
            self.assertFalse(provider.close_done.is_set())
            self.assertIs(provider.close_daemon, False)
        marker = []
        QTimer.singleShot(0, lambda: marker.append("responsive"))
        self.wait_until(lambda: bool(marker))
        controller.close()
        self.assertTrue(all(p.abort_calls == 1 for p in providers.values()))

    def test_settings_switch_modes_and_replace_binaries_without_interrupting_other_provider(self):
        codex, claude = FakeProvider("codex", blocked=True), FakeProvider("claude", blocked=True)
        controller = self.make_controller(codex=codex, claude=claude)
        self.wait_until(lambda: codex.started.is_set() and claude.started.is_set())
        write_json(self.root / "state" / "claude.json", observation("claude", used=8, kind="session"))

        def change(**fields):
            values = dict(controller.settings)
            values.update(fields)
            with patch.object(application, "SettingsDialog", side_effect=lambda settings, parent: AcceptedSettings(values, parent)), \
                 patch.object(application, "autostart"):
                controller.settings_dialog()

        change(claude_auto_query=False)
        self.assertNotIn("claude", controller.providers)
        self.assertEqual(claude.abort_calls, 1)
        self.assertEqual(controller.raw["claude"]["source_kind"], "session")
        self.assertEqual(controller.raw["claude"]["windows"][0]["used_percent"], 8)
        self.assertEqual(codex.abort_calls, 0)
        self.assertTrue(controller.busy["codex"])
        self.assertEqual(self.window.intervals, (60, None))
        controller._result("claude", claude, observation("claude", used=99))
        self.assertEqual(controller.raw["claude"]["windows"][0]["used_percent"], 8)

        change(claude_auto_query=True, claude_binary="/fixture/new-claude")
        new_claude = controller.providers["claude"]
        self.assertEqual(new_claude.binary, "/fixture/new-claude")
        self.wait_until(lambda: not controller.busy["claude"])
        self.assertEqual(controller.raw["claude"]["source_kind"], "direct")
        self.assertEqual(codex.calls, 1)
        self.assertEqual(codex.abort_calls, 0)
        self.assertEqual(self.window.intervals, (60, 60))

        change(codex_binary="/fixture/new-codex")
        self.assertIsNot(controller.providers["codex"], codex)
        self.assertEqual(controller.providers["codex"].binary, "/fixture/new-codex")
        self.assertEqual(codex.abort_calls, 1)
        self.assertEqual(new_claude.abort_calls, 0)
        self.wait_until(lambda: not controller.busy["codex"])


if __name__ == "__main__":
    unittest.main()
