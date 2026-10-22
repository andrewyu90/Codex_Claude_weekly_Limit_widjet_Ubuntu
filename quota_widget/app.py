"""Qt application controller: asynchronous reads, aging, settings and alarms."""
from __future__ import annotations

import fcntl
import json
import os
import secrets
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QFontDatabase, QIcon
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
    QSpinBox, QVBoxLayout)

from .alarms import AlarmStore
from .codex_provider import CodexProvider, ProviderError
from .claude_provider import ClaudeProvider
from .claude_state import automatic_diagnosis, merge_direct, read_observation, save_observation
from .demo import demo_snapshots
from .desktop import (DESKTOP_ID, autostart, autostart_enabled, desktop_notify,
                      ensure_desktop_integration)
from .models import display_snapshot, number, snapshot
from .storage import load_settings, private_directory, read_json, save_settings, state_dir, write_json
from .ui import MainWindow, STYLE


class FetchSignals(QObject):
    result = Signal(str, object, dict)
    delivery = Signal(str, bool)


def claude_observation() -> dict:
    return read_observation(automatic=False)


class SettingsDialog(QDialog):
    connection_action = Signal(str)

    def __init__(self, settings, parent):
        super().__init__(parent)
        self.setWindowTitle("Quota Dock Settings")
        self.setMinimumWidth(510)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.binary = QLineEdit(settings["codex_binary"])
        self.binary.setPlaceholderText("codex or the full executable path")
        self.poll = QSpinBox()
        self.poll.setRange(30, 900)
        self.poll.setSuffix(" s")
        self.poll.setValue(settings["poll_seconds"])
        self.claude_binary = QLineEdit(settings.get("claude_binary", "claude"))
        self.claude_binary.setPlaceholderText("claude or the full executable path")
        self.claude_auto = QCheckBox("Automatically check Claude usage")
        self.claude_auto.setChecked(settings.get("claude_auto_query", True))
        self.claude_poll = QSpinBox()
        self.claude_poll.setRange(60, 900)
        self.claude_poll.setSuffix(" s")
        self.claude_poll.setValue(settings.get("claude_poll_seconds", 60))
        self.claude_poll.setEnabled(self.claude_auto.isChecked())
        self.claude_auto.toggled.connect(self.claude_poll.setEnabled)
        self.pin = QCheckBox("Always on top")
        self.pin.setChecked(settings["always_on_top"])
        self.notify = QCheckBox("Notify when weekly recovery is confirmed")
        self.notify.setChecked(settings["notifications"])
        self.due = QCheckBox("Notify when a weekly reset is due")
        self.due.setChecked(settings["reset_due_notifications"])
        self.start_at_login = QCheckBox("Start at login")
        self.start_at_login.setChecked(autostart_enabled())
        form.addRow("Codex executable", self.binary)
        form.addRow("Codex interval", self.poll)
        form.addRow("Claude executable", self.claude_binary)
        form.addRow(self.claude_auto)
        form.addRow("Claude interval", self.claude_poll)
        form.addRow(self.pin)
        form.addRow(self.notify)
        form.addRow(self.due)
        form.addRow(self.start_at_login)
        layout.addLayout(form)
        info = QLabel("When signed in to Claude Code 2.1.306 or later, usage is checked at startup and at the selected interval.\n"
                      "No prompts are sent to the model. The CLI may reuse a query from the last minute.\n"
                      "Turn off automatic queries to show readings from the session connection below.\n"
                      "Reset-due alerts are separate from recovery alerts confirmed by a new reading.")
        info.setTextFormat(Qt.TextFormat.PlainText)
        info.setWordWrap(True)
        layout.addWidget(info)
        row = QHBoxLayout()
        for label, action in (("Connect session", "claude_connect"),
                              ("Connection diagnostics", "claude_diagnose"),
                              ("Disconnect session", "claude_disconnect")):
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, value=action: self.connection_action.emit(value))
            row.addWidget(button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Save")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return {"codex_binary": self.binary.text().strip() or "codex",
                "claude_binary": self.claude_binary.text().strip() or "claude",
                "claude_auto_query": self.claude_auto.isChecked(),
                "claude_poll_seconds": self.claude_poll.value(),
                "poll_seconds": self.poll.value(), "always_on_top": self.pin.isChecked(),
                "notifications": self.notify.isChecked(), "reset_due_notifications": self.due.isChecked()}


class Controller(QObject):
    def __init__(self, window, demo=False):
        super().__init__(window)
        self.window, self.demo = window, demo
        self.settings = load_settings()
        self.providers = {"codex": CodexProvider(self.settings["codex_binary"])}
        if self.settings["claude_auto_query"]:
            self.providers["claude"] = ClaudeProvider(self.settings["claude_binary"])
        self.raw = {"codex": snapshot("codex", "waiting", "Checking Codex usage."),
                    "claude": (snapshot("claude") if demo else read_observation(
                        automatic=self.settings["claude_auto_query"], startup=True))}
        self.busy = {name: False for name in ("codex", "claude")}
        self.closed = False
        self.failures = {name: 0 for name in self.busy}
        self.next_poll = {name: 0.0 for name in self.busy}
        self.retry_not_before = {name: 0.0 for name in self.busy}
        self.last_attempt = {name: None for name in self.busy}
        self.last_wall = time.time()
        self.last_manual = 0.0
        self.delivery_inflight = set()
        self.signals = FetchSignals(self)
        self.signals.result.connect(self._result)
        self.signals.delivery.connect(self._delivery)
        self.window.refresh_requested.connect(self.refresh)
        self.window.settings_requested.connect(self.settings_dialog)
        self.window.provider_action.connect(self.action)
        if hasattr(self.window, "pin_changed"):
            self.window.pin_changed.connect(self.pin_changed)
        self.window.set_pinned(self.settings["always_on_top"])
        self._update_interval_label()
        self.alarms = None if demo else AlarmStore(state_dir() / "alarms.json")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        if demo:
            self.raw = {data["provider"]: data for data in demo_snapshots()}
        self.tick()

    def pin_changed(self, enabled):
        self.settings["always_on_top"] = enabled
        if not self.demo:
            try:
                save_settings(self.settings)
            except OSError:
                self.window.notify("Settings could not be saved", "The change applies to this window, but could not be saved for the next launch.")

    def _interval(self, name):
        return self.settings["claude_poll_seconds" if name == "claude" else "poll_seconds"]

    def _update_interval_label(self):
        self.window.set_refresh_interval(self.settings["poll_seconds"],
            self.settings["claude_poll_seconds"] if self.settings["claude_auto_query"] else None)

    @staticmethod
    def _retire(provider):
        provider.abort()
        # The window remains responsive; non-daemon cleanup reaps the child
        # process even if the Qt event loop exits before the grace period ends.
        threading.Thread(target=provider.close, name="quota-provider-close", daemon=False).start()

    def _start_fetch(self, name="codex"):
        now = time.monotonic()
        if name not in self.providers or self.busy[name] or self.closed or self.demo:
            return
        if now < self.retry_not_before[name]:
            return
        # Manual refreshes and repeated settings saves share the same minimum
        # interval as automatic Claude reads. They do not bypass backoff.
        attempted = self.last_attempt[name]
        if name == "claude" and attempted is not None and now - attempted < 60:
            return
        self.busy[name] = True
        self.last_attempt[name] = now
        self.raw[name]["last_attempt_at"] = time.time()
        self.window.set_refreshing(True)
        provider = self.providers[name]
        def work():
            try:
                result = provider.fetch()
            except Exception as exc:
                message = str(exc) if isinstance(exc, ProviderError) else "Usage could not be read. The next scheduled query will try again."
                result = snapshot(name, "error", message)
                result.update(source_kind="direct", error_code="query_failed")
            if not self.closed:
                self.signals.result.emit(name, provider, result)
        threading.Thread(target=work, daemon=True, name=f"{name}-quota-read").start()

    def _result(self, name, provider, result):
        if self.closed or self.providers.get(name) is not provider:
            # An obsolete worker must not clear a newer worker's busy flag,
            # retry deadline, data, or account identity.
            return
        result = dict(result)
        result["last_attempt_at"] = self.raw[name].get("last_attempt_at")
        if name == "claude":
            current = merge_direct(self.raw[name], result)
        elif result.get("state") == "error":
            current = dict(self.raw[name])
            current.update(state="error", message=result.get("message", "Usage query failed"))
        else:
            current = result
        self.raw[name] = current
        self.busy[name] = False
        if current.get("state") == "ok":
            self.failures[name] = 0
            delay = self._interval(name)
            self.retry_not_before[name] = 0.0
        else:
            self.failures[name] += 1
            delay = min(900, self._interval(name) * 2 ** min(4, self.failures[name] - 1))
            advised = number(current.get("retry_after"), maximum=86400) or 0
            delay = max(delay, advised)
            self.retry_not_before[name] = time.monotonic() + delay
        self.next_poll[name] = time.monotonic() + delay
        if name == "claude":
            try:
                save_observation(current)
            except (OSError, ValueError):
                pass  # An unwritable cache must not hide a valid live result.
        self.window.set_refreshing(any(self.busy.values()))
        self._render()

    def refresh(self):
        if self.demo:
            self.raw = {data["provider"]: data for data in demo_snapshots()}
            self._render()
            return
        if time.monotonic() - self.last_manual < 10:
            return
        self.last_manual = time.monotonic()
        if not self.settings["claude_auto_query"]:
            self.raw["claude"] = claude_observation()
        if not self.busy["codex"]:
            # Re-read the CLI's managed sign-in after an external account change.
            old = self.providers["codex"]
            self.providers["codex"] = CodexProvider(self.settings["codex_binary"])
            self._retire(old)
        for name in self.providers:
            self._start_fetch(name)
        self._render()

    def tick(self):
        if self.closed:
            return
        now = time.time()
        if not self.demo:
            # A suspended machine does not replay missed polls on wake.
            if now-self.last_wall > max(15, min(self._interval(name) for name in self.providers)):
                for name in self.providers:
                    self.next_poll[name] = 0.0
            if not self.settings["claude_auto_query"]:
                self.raw["claude"] = claude_observation()
            for name in self.providers:
                if time.monotonic() >= self.next_poll[name]:
                    self._start_fetch(name)
            try:
                for name, observation in self.raw.items():
                    self.alarms.observe(self._display(name, observation), now,
                        recovery_enabled=self.settings["notifications"],
                        due_enabled=self.settings["reset_due_notifications"])
                self.alarms.due(now, enabled=self.settings["reset_due_notifications"])
                for event in self.alarms.pending(now):
                    if event["kind"] == "recovery" and not self.settings["notifications"]:
                        continue
                    if event["kind"] == "due" and not self.settings["reset_due_notifications"]:
                        continue
                    if event["id"] not in self.delivery_inflight:
                        self.delivery_inflight.add(event["id"])
                        def deliver(pending=event):
                            success = desktop_notify(pending["title"], pending["body"])
                            if not self.closed:
                                self.signals.delivery.emit(pending["id"], success)
                        threading.Thread(target=deliver, daemon=True, name="quota-notification").start()
            except OSError:
                pass  # Quota display must remain usable if local state becomes unwritable.
        self.last_wall = now
        self._render()

    def _delivery(self, event_id, success):
        self.delivery_inflight.discard(event_id)
        if self.closed or self.alarms is None:
            return
        if not success:
            event = next((e for e in self.alarms.state["events"] if e["id"] == event_id), None)
            if event:
                self.window.notify(event["title"], event["body"] + "\nDesktop notification delivery failed. Retrying in 5 minutes; check that libnotify-bin is installed.")
        try:
            self.alarms.record_delivery(event_id, success)
        except OSError:
            pass

    def _render(self):
        for name, data in self.raw.items():
            shown = self._display(name, data)
            shown["fetching"] = self.busy[name]
            self.window.update_provider(shown)

    def _display(self, name, data):
        stale_after = (180 if name == "claude" and data.get("source_kind") != "direct"
                       else max(180, 3*self._interval(name)))
        return display_snapshot(data, stale_after=stale_after)

    def settings_dialog(self):
        if self.demo:
            self.window.notify("Demo mode", "Change real settings in normal mode.")
            return
        dialog = SettingsDialog(self.settings, self.window)
        dialog.connection_action.connect(self.action)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new = dialog.values()
            try:
                autostart(dialog.start_at_login.isChecked())
                save_settings(new)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self.window, "Settings could not be saved", str(exc))
                return
            previous = self.settings
            self.settings = new
            if new["codex_binary"] != previous["codex_binary"]:
                self._retire(self.providers["codex"])
                self.providers["codex"] = CodexProvider(new["codex_binary"])
                self.busy["codex"] = False
                self.retry_not_before["codex"] = 0.0
            changed_claude = (new["claude_binary"] != previous["claude_binary"] or
                             new["claude_auto_query"] != previous["claude_auto_query"])
            if changed_claude:
                old = self.providers.pop("claude", None)
                if old:
                    self._retire(old)
                self.busy["claude"] = False
                self.failures["claude"] = 0
                self.retry_not_before["claude"] = 0.0
                self.last_attempt["claude"] = None
                if new["claude_auto_query"]:
                    self.providers["claude"] = ClaudeProvider(new["claude_binary"])
                self.raw["claude"] = read_observation(automatic=new["claude_auto_query"], startup=True)
            self.window.set_pinned(new["always_on_top"])
            self._update_interval_label()
            for name in self.providers:
                self.next_poll[name] = 0.0
            self.tick()

    def action(self, action):
        urls = {"codex_dashboard": "https://chatgpt.com/codex/settings/usage",
                "claude_dashboard": "https://claude.ai/settings/usage"}
        if action in urls:
            QDesktopServices.openUrl(QUrl(urls[action]))
        elif action == "claude_diagnose":
            self.claude_diagnostics()
        elif action in ("claude_connect", "claude_disconnect"):
            if self.demo:
                self.window.notify("Demo mode", "Install the Claude session connection in normal mode.")
                return
            try:
                from .claude_bridge import install_bridge, uninstall_bridge
                message = install_bridge() if action == "claude_connect" else uninstall_bridge()
                QMessageBox.information(self.window, "Claude connection", message)
            except (OSError, ValueError, RuntimeError) as exc:
                QMessageBox.warning(self.window, "Claude connection", str(exc))

    def claude_diagnostics(self):
        def report_now():
            remaining = max(0, self.next_poll["claude"] - time.monotonic())
            return automatic_diagnosis(self.settings, self.raw["claude"],
                fetching=self.busy["claude"], next_retry_seconds=remaining)
        result = report_now()
        dialog = QDialog(self.window)
        dialog.setWindowTitle("Claude connection diagnostics")
        dialog.setStyleSheet(STYLE)
        dialog.resize(470, 400)
        layout = QVBoxLayout(dialog)
        message = QLabel(result["summary"])
        message.setTextFormat(Qt.TextFormat.PlainText)
        message.setWordWrap(True)
        layout.addWidget(message)
        steps = QLabel("\n".join(result["actions"]))
        steps.setTextFormat(Qt.TextFormat.PlainText)
        steps.setWordWrap(True)
        layout.addWidget(steps)
        report = json.dumps(result, ensure_ascii=False, indent=2)
        text = QPlainTextEdit(report)
        text.setReadOnly(True)
        layout.addWidget(text, 1)
        buttons = QHBoxLayout()
        copy = QPushButton("Copy diagnostics")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(text.toPlainText()))
        buttons.addWidget(copy)
        def refresh_report():
            current = report_now()
            message.setText(current["summary"])
            steps.setText("\n".join(current["actions"]))
            text.setPlainText(json.dumps(current, ensure_ascii=False, indent=2))
        refresh = QPushButton("Check again")
        refresh.clicked.connect(refresh_report)
        buttons.addWidget(refresh)
        connect = QPushButton("Query now" if self.settings["claude_auto_query"] else "Connect session")
        def connect_claude():
            if self.settings["claude_auto_query"]:
                self._start_fetch("claude")
            else:
                self.action("claude_connect")
            refresh_report()
        connect.clicked.connect(connect_claude)
        buttons.addWidget(connect)
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        updates = QTimer(dialog)
        updates.setInterval(1000)
        updates.timeout.connect(refresh_report)
        updates.start()
        dialog.exec()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.timer.stop()
        for provider in self.providers.values():
            self._retire(provider)


def _activate_existing(directory: Path) -> bool:
    # File-based activation also works in desktops that restrict local sockets.
    # A random per-process token makes a leftover request harmless after restart.
    for _ in range(10):
        owner = read_json(directory / "widget.lock", {})
        token = owner.get("activation_token") if isinstance(owner, dict) else None
        if isinstance(token, str) and len(token) == 32:
            try:
                write_json(directory / "activate.json", {"activation_token": token})
                return True
            except (OSError, ValueError):
                return False
        time.sleep(0.05)
    return False


def run(demo=False, screenshot=None, from_autostart=False):
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("Quota Dock")
    app.setOrganizationName("QuotaDock")
    app.setDesktopFileName(DESKTOP_ID)
    lockfile = None
    notices = []
    if not demo:
        directory = private_directory(state_dir())
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        lockfile = os.fdopen(os.open(directory / "widget.lock", flags, 0o600), "r+")
        os.fchmod(lockfile.fileno(), 0o600)
        try:
            fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lockfile.close()
            if not from_autostart and not _activate_existing(directory):
                QMessageBox.information(None, "Quota Dock", "Quota Dock is already running.\n"
                    "If an older version is open, close its window and launch again.")
            return 0
        activation_token = secrets.token_hex(16)
        lockfile.seek(0)
        lockfile.truncate()
        json.dump({"activation_token": activation_token}, lockfile)
        lockfile.flush()
        notices.extend(ensure_desktop_integration())
        # Refresh only an already-owned bridge when an upgrade moves Python.
        from .claude_diagnostics import diagnosis
        if diagnosis(include_version=False)["bridge_registered"]:
            try:
                from .claude_bridge import install_bridge
                install_bridge()
            except (OSError, ValueError, RuntimeError) as exc:
                notices.append("Check the Claude connection path: " + str(exc))
    font = os.environ.get("QUOTA_WIDGET_FONT")
    if font and Path(font).is_file():
        QFontDatabase.addApplicationFont(font)
    window = MainWindow(demo=demo)
    controller = Controller(window, demo=demo)
    if not demo:
        def activate():
            request = read_json(directory / "activate.json", {})
            if not isinstance(request, dict) or request.get("activation_token") != activation_token:
                return
            try:
                (directory / "activate.json").unlink()
            except OSError:
                return
            window.showNormal()
            window.raise_()
            window.activateWindow()
        activation_timer = QTimer(window)
        activation_timer.setInterval(250)
        activation_timer.timeout.connect(activate)
        activation_timer.start()
    window.show()
    if notices:
        QTimer.singleShot(100, lambda: QMessageBox.warning(window, "Startup settings need attention", "\n\n".join(notices)))
    app.aboutToQuit.connect(controller.close)
    if screenshot:
        output = Path(screenshot).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        def save():
            if not window.grab().save(str(output)):
                print("The screenshot could not be saved.")
                app.exit(1)
            else:
                print(str(output))
                app.quit()
        QTimer.singleShot(500, save)
    status = app.exec()
    if lockfile:
        lockfile.close()
    return status
