"""Compact, network-free Qt presentation for Quota Dock.

The controller supplies observations. Missing capacity and elapsed reset times
never become an invented zero or a freshly refilled allowance.
"""

from __future__ import annotations

import copy
import html
import math
import time
from datetime import datetime

from PySide6.QtCore import QPoint, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QMouseEvent, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QComboBox, QDialog, QFrame, QHBoxLayout,
    QHeaderView, QLabel, QPushButton, QScrollArea, QSizeGrip, QSizePolicy,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)


COLORS = {
    "background": "#0d141c", "card": "#17212b", "line": "#293541",
    "text": "#f1f5f8", "muted": "#a3b3c2", "quiet": "#8295a6",
    "codex": "#7de1bf", "claude": "#efb191", "warning": "#f3cc85",
}

DIRECT_ERROR_LABELS = {
    "login_required": "Sign-in required",
    "cli_not_found": "Install required", "cli_missing": "Install required",
    "launch_failed": "Launch failed",
    "version_unknown": "Check for updates", "version_unsupported": "Check for updates",
    "cli_unsupported": "Unsupported",
}

STYLE = """
QWidget { font-family: 'Noto Sans', 'DejaVu Sans', sans-serif;
          color: #f1f5f8; font-size: 12px; }
QFrame#shell { background: #0d141c; border: 1px solid #33424f; border-radius: 12px; }
QFrame#providerCard { background: #17212b; border: 1px solid #293541; border-radius: 9px; }
QLabel { background: transparent; border: none; }
QPushButton { background: #202c37; border: 1px solid #334553; border-radius: 6px;
              padding: 5px 10px; color: #d8e3eb; }
QPushButton:hover { background: #2b3a47; border-color: #526777; }
QPushButton:pressed { background: #16232e; }
QPushButton:disabled { color: #6e8294; border-color: #25333f; }
QPushButton#compactAction { padding: 2px 5px; font-size: 11px; }
QPushButton#connect { background: #3a2e29; color: #f4c4aa; border-color: #775847;
                     padding: 0px 7px; font-size: 10px; }
QPushButton#connect:hover { background: #4c382e; }
QLineEdit, QSpinBox, QPlainTextEdit { background: #111a23; color: #e2ebf2;
    border: 1px solid #405363; border-radius: 5px; padding: 5px 7px;
    selection-background-color: #355f69; selection-color: #ffffff;
    placeholder-text-color: #8295a6; }
QLineEdit:focus, QSpinBox:focus, QPlainTextEdit:focus { border-color: #7de1bf; }
QLineEdit:disabled, QSpinBox:disabled, QPlainTextEdit:disabled {
    background: #19222b; color: #8192a2; border-color: #2d3a47; }
QPlainTextEdit { font-family: 'DejaVu Sans Mono', monospace; font-size: 11px; }
QSpinBox { padding-right: 20px; }
QSpinBox::up-button, QSpinBox::down-button { width: 17px; background: #283946;
    border-left: 1px solid #405363; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #3c5362; }
QCheckBox { spacing: 7px; color: #dde7ee; }
QCheckBox::indicator { width: 14px; height: 14px; border-radius: 3px;
    border: 1px solid #688090; background: #111a23; }
QCheckBox::indicator:checked { background: #7de1bf; border: 2px solid #41725f; }
QCheckBox::indicator:hover { border-color: #b2c7d5; }
QCheckBox:disabled { color: #8192a2; }
QCheckBox::indicator:disabled { background: #263540; border-color: #425463; }
QComboBox { background: #111a23; border: 1px solid #334553; border-radius: 6px;
            padding: 5px 8px; color: #bed0df; }
QComboBox::drop-down { border: 0; width: 22px; }
QComboBox QAbstractItemView { background: #192631; color: #e1eaf0;
                            selection-background-color: #354957; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 5px; margin: 4px 0; }
QScrollBar::handle:vertical { background: #364958; border-radius: 2px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QToolTip { color: #e7edf1; background: #25333f; border: 1px solid #4d6474; padding: 6px; }
QDialog { background: #111b24; }
QTableWidget { background: #17212b; border: 1px solid #334553;
               gridline-color: #334553; selection-background-color: #304754; }
QHeaderView::section { background: #22313d; color: #cfdee9; padding: 7px;
                       border: 0; border-bottom: 1px solid #334553; }
"""


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) else None


def _used(window: dict | None) -> float | None:
    value = _number(window.get("used_percent")) if window else None
    return value if value is not None and 0 <= value <= 100 else None


def _percentage(value: float) -> str:
    # Avoid rounding near exhaustion into a misleading zero or full allowance.
    if 0 < value < 0.1:
        return "<0.1"
    if 99.9 < value < 100:
        return ">99.9"
    return str(int(value)) if value.is_integer() else f"{value:.1f}"


def _age(value: object, now: float) -> str:
    timestamp = _number(value)
    if timestamp is None:
        return "No reading"
    elapsed = max(0, int(now - timestamp))
    if elapsed < 10:
        return "just now"
    if elapsed < 60:
        return f"{elapsed}s ago"
    if elapsed < 3600:
        return f"{elapsed // 60}m ago"
    if elapsed < 86400:
        return f"{elapsed // 3600}h ago"
    return f"{elapsed // 86400}d ago"


def _local_time(value: object) -> str:
    timestamp = _number(value)
    if timestamp is None:
        return "Not provided"
    try:
        when = datetime.fromtimestamp(timestamp).astimezone()
    except (ValueError, OSError, OverflowError):
        return "Check time"
    return when.strftime("%m/%d %H:%M %Z")


def _countdown(value: object, now: float) -> str:
    timestamp = _number(value)
    if timestamp is None:
        return "Not provided"
    seconds = max(0, int(timestamp - now))
    if not seconds:
        return "Awaiting update"
    days, hours = seconds // 86400, (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    if days:
        return f"{days}d {hours:02d}h"
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes:02d}m {seconds % 60:02d}s"


def _tooltip(text: str) -> str:
    # QToolTip auto-detects rich text. Escape provider data before opting in.
    return "<qt>" + html.escape(text).replace("\n", "<br>") + "</qt>"


def _label(text: str = "", size: int = 12, color: str = "#a3b3c2", weight: int = 400) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setStyleSheet(f"color:{color};font-size:{size}px;font-weight:{weight};")
    return label


class IconButton(QAbstractButton):
    """Vector controls independent of the installed icon fonts."""

    def __init__(self, glyph: str, tooltip: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.glyph = glyph
        self.setFixedSize(24, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)

    def sizeHint(self) -> QSize:
        return QSize(24, 24)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self.width() / 30, self.height() / 30)
        if self.underMouse() or self.isChecked():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#34404b" if self.glyph != "close" else "#603b3b"))
            painter.drawRoundedRect(QRectF(1, 1, 28, 28), 7, 7)
        painter.setPen(QPen(QColor("#d3e0e9" if self.isEnabled() else "#617584"), 1.7,
                            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        if self.glyph == "close":
            painter.drawLine(10, 10, 20, 20)
            painter.drawLine(20, 10, 10, 20)
        elif self.glyph == "minimize":
            painter.drawLine(9, 16, 21, 16)
        elif self.glyph == "pin":
            painter.drawLine(11, 8, 19, 8)
            painter.drawLine(12, 8, 12, 13)
            painter.drawLine(18, 8, 18, 13)
            painter.drawLine(12, 13, 9, 17)
            painter.drawLine(18, 13, 21, 17)
            painter.drawLine(9, 17, 21, 17)
            painter.drawLine(15, 17, 15, 23)
        elif self.glyph == "external":
            painter.drawLine(12, 8, 22, 8)
            painter.drawLine(22, 8, 22, 18)
            painter.drawLine(22, 8, 12, 18)
            path = QPainterPath()
            path.moveTo(9, 10)
            path.lineTo(7, 10)
            path.lineTo(7, 23)
            path.lineTo(20, 23)
            path.lineTo(20, 21)
            painter.drawPath(path)


class BrandMark(QWidget):
    def __init__(self, provider: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.provider = provider
        self.setFixedSize(21, 21)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(self.width() / 34, self.height() / 34)
        color = QColor(COLORS[self.provider])
        background = QColor(color)
        background.setAlpha(25)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(0, 0, 34, 34), 9, 9)
        painter.setPen(QPen(color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.provider == "codex":
            painter.drawLine(12, 10, 6, 17)
            painter.drawLine(6, 17, 12, 24)
            painter.drawLine(22, 10, 28, 17)
            painter.drawLine(28, 17, 22, 24)
            painter.drawLine(19, 10, 15, 24)
        else:
            for ray in range(10):
                angle = ray * math.pi / 5
                painter.drawLine(QPoint(round(17 + 4 * math.cos(angle)), round(17 + 4 * math.sin(angle))),
                                 QPoint(round(17 + 11 * math.cos(angle)), round(17 + 11 * math.sin(angle))))


class CapacityBar(QWidget):
    def __init__(self, accent: str, height: int = 5, parent: QWidget | None = None):
        super().__init__(parent)
        self.accent = accent
        self.remaining: float | None = None
        self.muted = False
        self.setFixedHeight(height)
        self.setMinimumWidth(50)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, remaining: float | None, muted: bool = False):
        self.remaining = remaining
        self.muted = muted
        self.setAccessibleName("Weekly remaining unknown" if remaining is None else f"Weekly remaining: {_percentage(remaining)}%")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        bounds = QRectF(self.rect())
        painter.setBrush(QColor("#2b3946"))
        painter.drawRoundedRect(bounds, self.height() / 2, self.height() / 2)
        if self.remaining is not None and self.remaining > 0:
            fill = QRectF(0, 0, max(2, bounds.width() * min(100, self.remaining) / 100), bounds.height())
            color = QColor(self.accent)
            if self.muted:
                color.setAlpha(100)
            painter.setBrush(color)
            painter.drawRoundedRect(fill, self.height() / 2, self.height() / 2)


class ProviderCard(QFrame):
    action = Signal(str)

    def __init__(self, provider: str, parent: QWidget | None = None, demo: bool = False):
        super().__init__(parent)
        self.provider = provider
        self.demo = demo
        self.accent = COLORS[provider]
        self.snapshot: dict = {}
        self._options: tuple = ()
        self.setObjectName("providerCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(104)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 8)
        layout.setSpacing(3)

        heading = QHBoxLayout()
        heading.setSpacing(6)
        heading.addWidget(BrandMark(provider))
        heading.addWidget(_label("Codex" if provider == "codex" else "Claude", 13, COLORS["text"], 650))
        self.badge = _label("Waiting", 9, COLORS["warning"], 500)
        heading.addWidget(self.badge)
        heading.addStretch(1)
        self.number = _label("—", 25, COLORS["text"], 650)
        self.number.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.percent = _label("", 12, COLORS["muted"], 500)
        heading.addWidget(self.number)
        heading.addWidget(self.percent)
        layout.addLayout(heading)

        metric = QHBoxLayout()
        metric.setSpacing(4)
        self.metric_label = _label("Weekly remaining", 10, self.accent, 500)
        metric.addWidget(self.metric_label)
        metric.addStretch(1)
        self.used = _label("Not available", 10)
        metric.addWidget(self.used)
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("connect")
        self.connect_button.setFixedHeight(20)
        self.connect_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.connect_button.setAccessibleName("Connect Claude Code")
        self.connect_button.clicked.connect(lambda: self.action.emit(
            "claude_diagnose" if self.direct_query else "claude_connect"))
        self.connect_button.hide()
        metric.addWidget(self.connect_button)
        layout.addLayout(metric)
        self.bar = CapacityBar(self.accent)
        layout.addWidget(self.bar)

        meta = QHBoxLayout()
        meta.setSpacing(4)
        self.reset_label = _label("Reset", 10, COLORS["quiet"])
        self.reset_countdown = _label("Not provided", 10, COLORS["muted"])
        meta.addWidget(self.reset_label)
        meta.addWidget(self.reset_countdown)
        meta.addStretch(1)
        self.freshness = _label("No reading", 9, COLORS["quiet"])
        meta.addWidget(self.freshness)
        layout.addLayout(meta)

        # Selection lives in the detail dialog; keeping its model here lets a
        # selected weekly bucket survive incoming observations and dialog close.
        self.selector = QComboBox(self)
        self.selector.currentIndexChanged.connect(lambda *_: self.render())
        self.selector.hide()
        self.message = _label()
        self.message.setParent(self)
        self.message.hide()
        self.render()

    def set_snapshot(self, snapshot: dict):
        self.snapshot = copy.deepcopy(snapshot)
        weekly = self._weekly()
        options = tuple((str(w.get("key", "")), str(w.get("bucket", "")), str(w.get("label", ""))) for w in weekly)
        if options != self._options:
            old = self.selector.currentData()
            self.selector.blockSignals(True)
            self.selector.clear()
            for window in weekly:
                bucket = str(window.get("bucket") or window.get("label") or "Weekly limit")
                self.selector.addItem(bucket, window.get("key"))
            selected = self.selector.findData(old)
            if selected >= 0:
                self.selector.setCurrentIndex(selected)
            self.selector.blockSignals(False)
            self._options = options
        self.render()

    def _weekly(self) -> list[dict]:
        windows = [w for w in self.snapshot.get("windows", []) if isinstance(w, dict) and w.get("kind") == "weekly"]
        return sorted(windows, key=lambda w: (w.get("bucket") != "codex", str(w.get("key", ""))))

    def selected_window(self) -> dict | None:
        weekly = self._weekly()
        selected = self.selector.currentData()
        return next((w for w in weekly if w.get("key") == selected), weekly[0] if weekly else None)

    @property
    def direct_query(self) -> bool:
        return self.provider == "claude" and self.snapshot.get("source_kind") == "direct"

    @property
    def attention_label(self) -> str | None:
        code = self.snapshot.get("error_code")
        return DIRECT_ERROR_LABELS.get(code) if self.direct_query and isinstance(code, str) else None

    def render(self):
        now = time.time()
        state = self.snapshot.get("state", "waiting")
        direct = self.direct_query
        weekly = self._weekly()
        window = self.selected_window()
        used = _used(window)
        reset = _number(window.get("resets_at")) if window else None
        expired = reset is not None and reset <= now
        historical = state in ("error", "stale", "unsupported") or expired or (direct and state == "waiting")
        remaining = 100.0 - used if used is not None else None
        self.number.setText(_percentage(remaining) if remaining is not None else "—")
        self.percent.setText("%" if remaining is not None else "")
        self.number.setStyleSheet(f"color:{'#9daebc' if historical else COLORS['text']};font-size:25px;font-weight:650;")
        metric = "Previous weekly remaining" if historical and remaining is not None else "Weekly remaining"
        if len(weekly) > 1:
            metric += f" · {self.selector.currentIndex() + 1}/{len(weekly)}"
        self.metric_label.setText(metric)
        self.metric_label.setToolTip(_tooltip(
            f"Weekly limit: {window.get('bucket') or window.get('label') or 'weekly'}\n"
            "Separate limits are not combined. Choose the displayed limit in Details."
        ) if window else "Weekly usage is not available yet.")
        self.used.setText(f"Used {_percentage(used)}%" if used is not None else "Not available")
        self.bar.set_value(remaining, historical)
        self.reset_countdown.setText(_countdown(reset, now))
        self.reset_countdown.setToolTip(_tooltip(f"Scheduled reset: {_local_time(reset)}\nRemaining capacity is not refilled until a new reading confirms it."))

        badges = {
            "ok": ("Automatic" if self.provider == "codex" or direct else "Session", self.accent),
            "waiting": ("Checking" if direct else "Waiting", COLORS["warning"]),
            "error": ("Query failed" if direct else "Check connection", "#efa49e"),
            "stale": ("Previous reading", COLORS["warning"]),
            "unsupported": ("Unsupported" if direct else "No limit provided", COLORS["warning"]),
        }
        badge_text, badge_color = badges.get(state, badges["waiting"])
        if self.attention_label:
            badge_text = self.attention_label
            badge_color = "#efa49e" if self.snapshot.get("error_code") in ("login_required", "launch_failed") else COLORS["warning"]
        elif expired and state not in ("error", "unsupported"):
            badge_text, badge_color = "Awaiting update", COLORS["warning"]
        elif direct and self.snapshot.get("fetching") and state == "ok":
            badge_text = "Checking"
        self.badge.setText(badge_text)
        self.badge.setStyleSheet(f"color:{badge_color};font-size:9px;font-weight:500;")

        message = str(self.snapshot.get("message") or "")
        if self.demo and state == "ok":
            message = ""
        if expired:
            message = (f"{message}\n" if message else "") + "The reset time has passed. Waiting for a new usage reading."
        elif not weekly and not message:
            if direct:
                message = ("Checking Claude usage." if state == "waiting" and not self.attention_label else
                           "Use Check connection to verify the Claude installation and sign-in status.")
            else:
                message = ("Sign in to Codex, then select Refresh." if self.provider == "codex"
                           else "Connect Claude Code to see weekly usage.")
        self.message.setText(message)
        badge_tip = message or badge_text
        if direct and self.snapshot.get("fetching"):
            badge_tip += "\nA new query is running. The displayed time is the last successful query."
        self.badge.setToolTip(_tooltip(badge_tip))
        connect = self.provider == "claude" and not weekly and state in ("waiting", "unsupported", "error")
        self.connect_button.setText("Check connection" if direct else "Connect")
        self.connect_button.setAccessibleName("Check Claude usage connection" if direct else "Connect Claude Code")
        self.connect_button.setVisible(connect)
        self.used.setVisible(not connect)
        observed = self.snapshot.get("observed_at")
        age = _age(observed, now)
        age_label = "Queried" if direct else "Seen"
        self.freshness.setText(("No query" if direct else "No reading") if age == "No reading" else f"{age_label} {age}")
        if direct:
            freshness_tip = (f"Source: {self.snapshot.get('source') or 'Claude usage query'}\n"
                             f"Last successful query: {_local_time(observed)}\n"
                             f"Response received: {_local_time(self.snapshot.get('received_at'))}\n"
                             "Failed queries and retries do not update the last successful query time.")
        else:
            freshness_tip = (f"Source: {self.snapshot.get('source') or 'Awaiting connection'}\n"
                             f"Observed: {_local_time(observed)}\n"
                             f"Heartbeat: {_local_time(self.snapshot.get('received_at'))}\n"
                             + ("A Claude heartbeat does not indicate a new server query." if self.provider == "claude" else ""))
        self.freshness.setToolTip(_tooltip(freshness_tip))


class DragHeader(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._offset = QPoint()
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._offset = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & Qt.MouseButton.LeftButton:
            if QApplication.platformName() not in ("wayland", "wayland-egl"):
                self.window().move(event.globalPosition().toPoint() - self._offset)
        super().mouseMoveEvent(event)


class MainWindow(QWidget):
    refresh_requested = Signal()
    settings_requested = Signal()
    provider_action = Signal(str)
    pin_changed = Signal(bool)

    def __init__(self, demo: bool = False):
        super().__init__()
        self.demo = demo
        self._pinned = False
        self._notice: tuple[str, str] | None = None
        self.setWindowTitle("Quota Dock · Codex & Claude")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(340, 316)
        self.resize(340, 316)
        self.setStyleSheet(STYLE)
        self.setWindowIcon(self._icon())
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.shell = QFrame()
        self.shell.setObjectName("shell")
        outer.addWidget(self.shell)
        shell_layout = QVBoxLayout(self.shell)
        shell_layout.setContentsMargins(9, 7, 9, 7)
        shell_layout.setSpacing(6)

        header = DragHeader()
        header.setFixedHeight(24)
        header_layout = QHBoxLayout(header)
        header_layout.setSpacing(3)
        header_layout.setContentsMargins(3, 0, 0, 0)
        header_layout.addWidget(_label("Quota Dock", 13, COLORS["text"], 650))
        if demo:
            demo_label = _label("DEMO · Example", 9, "#f2cf8d", 550)
            demo_label.setToolTip("Preview mode · All values are example data.")
            demo_label.setStyleSheet("background:#322b1e;color:#f2cf8d;border-radius:4px;padding:2px 5px;font-size:9px;")
            header_layout.addWidget(demo_label)
        header_layout.addStretch(1)
        self.pin_button = IconButton("pin", "Always on top · Support depends on the desktop environment")
        self.pin_button.setCheckable(True)
        self.pin_button.clicked.connect(self._toggle_pin)
        header_layout.addWidget(self.pin_button)
        minimize = IconButton("minimize", "Minimize")
        minimize.clicked.connect(self.showMinimized)
        header_layout.addWidget(minimize)
        close = IconButton("close", "Quit widget")
        close.clicked.connect(self.close)
        header_layout.addWidget(close)
        shell_layout.addWidget(header)

        self.cards: dict[str, ProviderCard] = {}
        for provider in ("codex", "claude"):
            card = ProviderCard(provider, demo=demo)
            card.action.connect(self.provider_action.emit)
            shell_layout.addWidget(card)
            self.cards[provider] = card
        shell_layout.addStretch(1)

        actions = QHBoxLayout()
        actions.setSpacing(5)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        settings = QPushButton("Settings")
        settings.clicked.connect(self.settings_requested.emit)
        details = QPushButton("Details")
        details.clicked.connect(self.show_details)
        details.setToolTip("All limits, sources, status and official usage pages")
        for button in (self.refresh_button, settings, details):
            button.setObjectName("compactAction")
            button.setFixedHeight(25)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            actions.addWidget(button, 1)
        shell_layout.addLayout(actions)

        footer = QHBoxLayout()
        footer.setSpacing(3)
        footer.setContentsMargins(3, 0, 0, 0)
        self.interval_label = _label("Codex every 60s · Claude session", 9, COLORS["quiet"])
        footer.addWidget(self.interval_label, 1)
        self.toast = _label("", 9, COLORS["warning"])
        self.toast.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.toast.setFixedHeight(14)
        self.toast.hide()
        footer.addWidget(self.toast, 1)
        grip = QSizeGrip(self)
        grip.setFixedSize(12, 12)
        footer.addWidget(grip)
        shell_layout.addLayout(footer)
        self.toast_timer = QTimer(self)
        self.toast_timer.setSingleShot(True)
        self.toast_timer.timeout.connect(self._hide_notice)
        self.clock = QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self._tick)
        self.clock.start()

    @staticmethod
    def _icon() -> QIcon:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(COLORS["background"]))
        painter.drawRoundedRect(QRectF(0, 0, 64, 64), 15, 15)
        painter.setBrush(QColor(COLORS["codex"]))
        painter.drawRoundedRect(QRectF(13, 16, 15, 35), 5, 5)
        painter.setBrush(QColor(COLORS["claude"]))
        painter.drawRoundedRect(QRectF(36, 28, 15, 23), 5, 5)
        painter.end()
        return QIcon(pixmap)

    def _tick(self):
        for card in self.cards.values():
            card.render()

    def update_provider(self, snapshot: dict):
        card = self.cards.get(snapshot.get("provider"))
        if card is not None:
            card.set_snapshot(snapshot)

    def set_refreshing(self, refreshing: bool):
        self.refresh_button.setEnabled(not refreshing)
        self.refresh_button.setText("Checking…" if refreshing else "Refresh")

    def set_refresh_interval(self, seconds: int, claude_seconds: int | None = None):
        claude = "Claude session" if claude_seconds is None else f"Claude every {claude_seconds}s"
        self.interval_label.setText(f"Codex every {seconds}s · {claude}")

    def _toggle_pin(self, checked: bool):
        self.set_pinned(checked)
        self.pin_changed.emit(checked)

    def set_pinned(self, pinned: bool):
        pinned = bool(pinned)
        self.pin_button.setChecked(pinned)
        if pinned == self._pinned:
            return
        self._pinned = pinned
        was_visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, pinned)
        if was_visible:
            self.show()

    def notify(self, title: str, body: str):
        """Keep the compact window size; retain full notices in Details."""
        self._notice = (str(title), str(body))
        self.toast.setText(f"{title} · See Details")
        self.toast.setToolTip(_tooltip(f"{title}\n{body}"))
        self.interval_label.hide()
        self.toast.show()
        self.toast_timer.start(10000)

    def _hide_notice(self):
        self.toast.hide()
        self.interval_label.show()

    def build_details_dialog(self) -> QDialog:
        """Build a read-only detail snapshot; the controller API is unchanged."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Quota Dock · Details")
        dialog.resize(760, 520)
        dialog.setStyleSheet(STYLE)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 14, 16, 14)
        heading = _label("Usage details" + (" · DEMO example data" if self.demo else ""), 17, COLORS["text"], 650)
        layout.addWidget(heading)
        note = _label("Readings captured when Details was opened. Limits are separate; remaining capacity is not refilled without a new reading.", 11)
        note.setWordWrap(True)
        layout.addWidget(note)

        for provider, card in self.cards.items():
            if card.selector.count() <= 1:
                continue
            selection = QHBoxLayout()
            selection.addWidget(_label(f"{'Codex' if provider == 'codex' else 'Claude'} weekly limit shown in the widget", 11))
            chooser = QComboBox()
            chooser.setObjectName(f"{provider}BucketSelector")
            for index in range(card.selector.count()):
                chooser.addItem(card.selector.itemText(index), card.selector.itemData(index))
            chooser.setCurrentIndex(card.selector.currentIndex())
            chooser.currentIndexChanged.connect(lambda index, c=card, box=chooser: self._select_bucket(c, box.itemData(index)))
            selection.addWidget(chooser, 1)
            layout.addLayout(selection)

        table = QTableWidget(0, 7)
        table.setObjectName("quotaDetails")
        table.setHorizontalHeaderLabels(["Service", "Group", "Limit", "Used", "Remaining", "Reset time", "Status"])
        table.verticalHeader().hide()
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        now = time.time()
        for provider, card in self.cards.items():
            for window in card.snapshot.get("windows", []):
                if not isinstance(window, dict):
                    continue
                used = _used(window)
                reset = _number(window.get("resets_at"))
                expired = reset is not None and reset <= now
                provider_state = card.snapshot.get("state")
                historical = expired or provider_state in ("error", "stale", "unsupported") or (card.direct_query and provider_state == "waiting")
                # Do not advertise expired short-window capacity as available.
                remaining = None if used is None or (expired and window.get("kind") == "short") else 100.0 - used
                if provider_state in ("error", "unsupported") or card.attention_label:
                    state = card.badge.text() + " · previous"
                elif expired:
                    state = "Awaiting update"
                elif historical:
                    state = "Previous reading"
                else:
                    state = "Query reading" if card.direct_query else "Reading"
                if used is None:
                    state = "Not provided"
                row = table.rowCount()
                table.insertRow(row)
                values = ["Codex" if provider == "codex" else "Claude", str(window.get("bucket") or "—"),
                          str(window.get("label") or window.get("kind") or "—"),
                          f"{_percentage(used)}%" if used is not None else "—",
                          f"{_percentage(remaining)}%" if remaining is not None else "—",
                          _local_time(reset), state]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    if historical:
                        item.setForeground(QColor(COLORS["muted"]))
                    table.setItem(row, column, item)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(205)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        detail_content = QWidget()
        detail_content.setStyleSheet("background:transparent;")
        detail_layout = QVBoxLayout(detail_content)
        detail_layout.setContentsMargins(0, 0, 6, 0)
        if self._notice:
            notice = _label(f"{self._notice[0]}\n{self._notice[1]}", 11, COLORS["warning"])
            notice.setWordWrap(True)
            detail_layout.addWidget(notice)
        for provider, card in self.cards.items():
            status = card.snapshot
            name = "Codex" if provider == "codex" else "Claude"
            observation_name = "Last successful query" if card.direct_query else "Usage observed"
            received_name = "Response received" if card.direct_query else "Heartbeat"
            line = _label(f"{name} · {card.badge.text()}\n"
                          f"Source: {status.get('source') or 'Awaiting connection'}\n"
                          f"{observation_name}: {_local_time(status.get('observed_at'))} · {received_name}: {_local_time(status.get('received_at'))}", 11)
            line.setWordWrap(True)
            detail_layout.addWidget(line)
            if card.message.text():
                message = _label(card.message.text(), 11)
                message.setWordWrap(True)
                detail_layout.addWidget(message)
        claude_explanation = (
            "Queries use the Claude Code usage command. A failed query preserves the last successful value and its query time."
            if self.cards["claude"].direct_query else
            "Claude values come from a Claude Code session. Heartbeats do not update the observation time without a new usage response from the server."
        )
        claude_note = _label(claude_explanation, 10, COLORS["quiet"])
        claude_note.setWordWrap(True)
        detail_layout.addWidget(claude_note)
        scroll.setWidget(detail_content)
        layout.addWidget(scroll)

        actions = QHBoxLayout()
        for provider in ("codex", "claude"):
            button = QPushButton(f"{'Codex' if provider == 'codex' else 'Claude'} usage page")
            button.clicked.connect(lambda checked=False, p=provider: self.provider_action.emit(f"{p}_dashboard"))
            actions.addWidget(button)
        actions.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        actions.addWidget(close)
        layout.addLayout(actions)
        return dialog

    @staticmethod
    def _select_bucket(card: ProviderCard, key):
        index = card.selector.findData(key)
        if index >= 0:
            card.selector.setCurrentIndex(index)

    def show_details(self):
        dialog = self.build_details_dialog()
        dialog.exec()
        dialog.deleteLater()
