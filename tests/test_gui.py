"""Offline GUI behaviour checks; no providers, credentials or network are used."""

from __future__ import annotations

import os
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QTableWidget
    from quota_widget.ui import MainWindow
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, "PySide6 is not installed; native GUI checks skipped")
class NativeWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        font = os.environ.get("QUOTA_WIDGET_FONT")
        if font and Path(font).is_file():
            QFontDatabase.addApplicationFont(font)

    def setUp(self):
        self.now = time.time()
        self.window = MainWindow()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def observation(self, windows, provider="codex", state="ok"):
        return {
            "provider": provider, "state": state, "message": "", "source": "offline fixture",
            "account_label": "Test subscription", "account_key": "test-account",
            "received_at": self.now, "observed_at": self.now, "windows": windows,
        }

    def quota(self, used=38.0, bucket="codex", kind="weekly", expired=False):
        return {
            "key": f"{bucket}:{kind}", "label": "Weekly limit" if kind == "weekly" else "5-hour limit",
            "bucket": bucket, "kind": kind, "used_percent": used,
            "resets_at": self.now - 30 if expired else self.now + 3600,
            "duration_minutes": 10080 if kind == "weekly" else 300,
        }

    def test_unknown_allowance_is_not_zero_or_full(self):
        self.window.update_provider(self.observation([], state="waiting"))
        card = self.window.cards["codex"]
        self.assertEqual(card.number.text(), "—")
        self.assertEqual(card.percent.text(), "")
        self.assertIsNone(card.bar.remaining)
        self.assertEqual(card.reset_countdown.text(), "Not provided")

    def test_multiple_buckets_are_selected_in_details_not_combined(self):
        self.window.update_provider(self.observation([
            self.quota(used=38.0), self.quota(used=85.0, bucket="separate-model"),
        ]))
        card = self.window.cards["codex"]
        self.assertEqual(card.number.text(), "62")
        self.assertTrue(card.selector.isHidden())
        dialog = self.window.build_details_dialog()
        try:
            chooser = dialog.findChild(QComboBox, "codexBucketSelector")
            self.assertEqual(chooser.count(), 2)
            chooser.setCurrentIndex(1)
            self.assertEqual(card.number.text(), "15")
            self.assertEqual(card.bar.remaining, 15.0)
            self.assertIn("2/2", card.metric_label.text())
        finally:
            dialog.deleteLater()
        self.window.update_provider(self.observation([
            self.quota(used=40.0), self.quota(used=86.0, bucket="separate-model"),
        ]))
        self.assertEqual(card.number.text(), "14")

    def test_expired_weekly_remains_explicitly_historical(self):
        self.window.update_provider(self.observation([self.quota(used=80.0, expired=True)]))
        card = self.window.cards["codex"]
        self.assertEqual(card.number.text(), "20")
        self.assertEqual(card.badge.text(), "Awaiting update")
        self.assertIn("Previous weekly remaining", card.metric_label.text())
        self.assertTrue(card.bar.muted)
        self.assertEqual(card.reset_countdown.text(), "Awaiting update")

    def test_expired_short_window_is_unknown_in_details(self):
        self.window.update_provider(self.observation([
            self.quota(used=38.0), self.quota(used=20.0, kind="short", expired=True),
        ]))
        self.assertEqual(self.window.cards["codex"].number.text(), "62")
        dialog = self.window.build_details_dialog()
        try:
            table = dialog.findChild(QTableWidget, "quotaDetails")
            self.assertEqual(table.rowCount(), 2)
            self.assertEqual(table.item(1, 2).text(), "5-hour limit")
            self.assertEqual(table.item(1, 3).text(), "20%")
            self.assertEqual(table.item(1, 4).text(), "—")
            self.assertEqual(table.item(1, 6).text(), "Awaiting update")
        finally:
            dialog.deleteLater()

    def test_demo_has_a_visible_example_label_without_extra_height(self):
        demo = MainWindow(demo=True)
        try:
            data = self.observation([self.quota()])
            data["message"] = "Example data · No real account connected."
            demo.update_provider(data)
            demo.show()
            self.app.processEvents()
            self.assertTrue(demo.demo)
            labels = [label.text() for label in demo.findChildren(QLabel) if label.isVisible()]
            self.assertTrue(any("DEMO" in label and "Example" in label for label in labels))
            self.assertEqual(demo.size(), self.window.size())
            self.assertTrue(demo.cards["codex"].message.isHidden())
        finally:
            demo.close()
            demo.deleteLater()

    def test_footer_reflects_configured_poll_interval(self):
        self.window.set_refresh_interval(120)
        self.assertIn("Codex every 120s", self.window.interval_label.text())
        self.assertIn("Claude session", self.window.interval_label.text())

    def test_compact_layout_survives_long_messages_and_notice(self):
        size = self.window.size()
        self.assertEqual((size.width(), size.height()), (340, 316))
        message = "<b>Connection status explanation</b> " * 150
        for provider in ("codex", "claude"):
            snapshot = self.observation([self.quota(used=99.95, bucket=provider)], provider, "error")
            snapshot["message"] = message
            self.window.update_provider(snapshot)
        self.window.notify("Review notification", "Long notification body " * 100)
        self.app.processEvents()
        self.assertEqual(self.window.size(), size)
        for card in self.window.cards.values():
            self.assertTrue(card.message.isHidden())
            self.assertEqual(card.message.textFormat(), Qt.TextFormat.PlainText)
            self.assertIn("&lt;b&gt;", card.badge.toolTip())
            self.assertTrue(card.rect().contains(card.bar.geometry()))
            self.assertTrue(card.rect().contains(card.freshness.geometry()))
        dialog = self.window.build_details_dialog()
        try:
            self.assertTrue(any(label.text() == message for label in dialog.findChildren(QLabel)))
        finally:
            dialog.deleteLater()

    def test_claude_waiting_connection_and_stale_observation_are_distinct(self):
        actions = []
        self.window.provider_action.connect(actions.append)
        waiting = self.observation([], "claude", "waiting")
        waiting["observed_at"] = None
        self.window.update_provider(waiting)
        card = self.window.cards["claude"]
        self.assertTrue(card.connect_button.isVisible())
        card.connect_button.click()
        self.assertEqual(actions, ["claude_connect"])
        self.assertEqual(card.badge.text(), "Waiting")
        self.assertEqual(card.freshness.text(), "No reading")
        stale = self.observation([self.quota(used=60.0)], "claude", "stale")
        stale["observed_at"] = self.now - 4000
        stale["received_at"] = self.now
        self.window.update_provider(stale)
        self.assertFalse(card.connect_button.isVisible())
        self.assertEqual(card.badge.text(), "Previous reading")
        self.assertEqual(card.freshness.text(), "Seen 1h ago")
        self.assertEqual(card.number.text(), "40")
        self.assertTrue(card.bar.muted)

    def test_claude_direct_success_uses_query_labels_and_both_intervals(self):
        data = self.observation([self.quota(used=67.5, bucket="claude")], "claude")
        data.update(source_kind="direct", source="Claude Code usage command", observed_at=self.now - 120)
        self.window.update_provider(data)
        self.window.set_refresh_interval(60, 120)
        card = self.window.cards["claude"]
        self.assertEqual(card.badge.text(), "Automatic")
        self.assertEqual(card.number.text(), "32.5")
        self.assertEqual(card.freshness.text(), "Queried 2m ago")
        self.assertIn("Last successful query", card.freshness.toolTip())
        self.assertEqual(self.window.interval_label.text(), "Codex every 60s · Claude every 120s")
        dialog = self.window.build_details_dialog()
        try:
            labels = [label.text() for label in dialog.findChildren(QLabel)]
            self.assertTrue(any("Claude Code usage command" in text for text in labels))
            self.assertFalse(any("Claude values come from a Claude Code session" in text for text in labels))
        finally:
            dialog.deleteLater()
        data["fetching"] = True
        self.window.update_provider(data)
        self.assertEqual(card.badge.text(), "Checking")
        self.assertEqual(card.freshness.text(), "Queried 2m ago")
        data["state"] = "stale"
        self.window.update_provider(data)
        self.assertEqual(card.badge.text(), "Previous reading")
        self.assertTrue(card.bar.muted)

    def test_claude_direct_failure_outweighs_expiry_without_freshening_cache(self):
        card = self.window.cards["claude"]
        for state, code, label in (("error", "query_failed", "Query failed"),
                                   ("error", "login_required", "Sign-in required"),
                                   ("waiting", "login_required", "Sign-in required"),
                                   ("unsupported", "cli_unsupported", "Unsupported")):
            with self.subTest(state=state, code=code):
                data = self.observation([self.quota(used=80.0, bucket="claude", expired=True)], "claude", state)
                data.update(source_kind="direct", error_code=code, message="Query error explanation",
                            observed_at=self.now - 600, received_at=self.now, fetching=True)
                self.window.update_provider(data)
                self.assertEqual(card.badge.text(), label)
                self.assertEqual(card.number.text(), "20")
                self.assertEqual(card.freshness.text(), "Queried 10m ago")
                self.assertEqual(card.reset_countdown.text(), "Awaiting update")
                self.assertIn("Previous weekly remaining", card.metric_label.text())
                self.assertTrue(card.bar.muted)
                self.assertTrue(card.message.text().startswith("Query error explanation"))
                self.assertEqual(card.snapshot["observed_at"], self.now - 600)
                dialog = self.window.build_details_dialog()
                try:
                    table = dialog.findChild(QTableWidget, "quotaDetails")
                    self.assertEqual(table.item(0, 6).text(), label + " · previous")
                finally:
                    dialog.deleteLater()

    def test_claude_direct_waiting_and_session_fallback_keep_their_source(self):
        actions = []
        self.window.provider_action.connect(actions.append)
        data = self.observation([], "claude", "waiting")
        data.update(source_kind="direct", observed_at=None)
        self.window.update_provider(data)
        card = self.window.cards["claude"]
        self.assertEqual(card.badge.text(), "Checking")
        self.assertEqual(card.number.text(), "—")
        self.assertEqual(card.freshness.text(), "No query")
        self.assertEqual(card.connect_button.text(), "Check connection")
        card.connect_button.click()
        self.assertEqual(actions, ["claude_diagnose"])
        for code, expected in (("login_required", "Sign-in required"),
                               ("cli_not_found", "Install required"),
                               ("launch_failed", "Launch failed"),
                               ("version_unknown", "Check for updates"),
                               ("version_unsupported", "Check for updates")):
            with self.subTest(waiting_code=code):
                data["error_code"] = code
                self.window.update_provider(data)
                self.assertEqual(card.badge.text(), expected)
                self.assertEqual(card.number.text(), "—")
                self.assertEqual(card.freshness.text(), "No query")
                self.assertTrue(card.connect_button.isVisible())
                self.assertNotIn("Checking Claude usage", card.message.text())
        for source_kind in (None, "session"):
            with self.subTest(source_kind=source_kind):
                fallback = self.observation([self.quota(used=60.0, bucket="claude")], "claude", "stale")
                fallback.update(observed_at=self.now - 900, received_at=self.now)
                if source_kind:
                    fallback["source_kind"] = source_kind
                self.window.update_provider(fallback)
                self.assertEqual(card.badge.text(), "Previous reading")
                self.assertEqual(card.freshness.text(), "Seen 15m ago")
                self.assertFalse(card.direct_query)
        fallback.update(state="waiting", windows=[], observed_at=None)
        self.window.update_provider(fallback)
        self.assertEqual(card.connect_button.text(), "Connect")
        card.connect_button.click()
        self.assertEqual(actions, ["claude_diagnose", "claude_connect"])


if __name__ == "__main__":
    unittest.main()
