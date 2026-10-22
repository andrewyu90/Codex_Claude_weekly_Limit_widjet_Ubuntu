"""User-level application-menu and login-start integration."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .storage import (claude_config_dir, config_dir, environment_path, read_json,
                      state_dir, write_json)

MARKER = "X-QuotaWidget-Owned=true"
DESKTOP_ID = "quota-dock"


def _desktop_quote(value: str) -> str:
    """Encode one Exec argument, including both Desktop Entry escape layers."""
    if any(char in value for char in ("\n", "\r", "\0")):
        raise ValueError("Executable paths cannot contain newlines or NUL characters.")
    escaped = value.replace("\\", "\\\\")
    for char in ('"', "`", "$"):
        escaped = escaped.replace(char, "\\" + char)
    return '"' + escaped.replace("\\", "\\\\").replace("%", "%%") + '"'


def _owned_text(path: Path) -> str | None:
    if path.is_symlink():
        raise ValueError(f"Cannot modify a launch entry that is a symbolic link: {path.name}")
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    if MARKER not in text.splitlines():
        raise ValueError(f"Another launch entry already uses this name: {path.name}")
    return text


def _write_owned(path: Path, text: str) -> None:
    _owned_text(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".quota-dock-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o644)
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def launch_argv() -> list[str]:
    """Use the current installation, never an older user installation by chance."""
    launcher = os.environ.get("QUOTA_WIDGET_LAUNCHER")
    if launcher:
        path = Path(launcher).expanduser()
        if path.is_absolute() and path.is_file() and os.access(path, os.X_OK):
            return [str(path)]
    return [sys.executable, "-m", "quota_widget"]


def _entry(*, login: bool = False, enabled: bool = True) -> str:
    # A desktop session need not inherit variables from the user's shell.
    # Pin only this app's paths, never tokens or the full environment.
    paths = {
        "QUOTA_WIDGET_DATA_DIR": state_dir(),
        "CLAUDE_CONFIG_DIR": claude_config_dir(),
        "XDG_CONFIG_HOME": config_dir().parent,
        "XDG_DATA_HOME": environment_path("XDG_DATA_HOME", Path.home() / ".local/share"),
    }
    invocation = launch_argv()
    command = ["/usr/bin/env", *(f"{key}={value}" for key, value in paths.items()), *invocation]
    if login:
        command.append("--from-autostart")
    icon = str(Path(__file__).parent / "assets/icon.svg")
    if any(char in icon for char in ("\n", "\r", "\0")):
        raise ValueError("Icon paths cannot contain newlines or NUL characters.")
    lines = ["[Desktop Entry]", "Type=Application", "Version=1.0", "Name=Quota Dock",
             "Comment=Weekly usage for Codex and Claude", "Exec=" + " ".join(map(_desktop_quote, command)),
             "TryExec=" + invocation[0].replace("\\", "\\\\"),
             "Icon=" + icon.replace("\\", "\\\\"), "Terminal=false",
             "Categories=Utility;Development;", "StartupNotify=false", "StartupWMClass=quota-dock",
             MARKER]
    if login:
        lines.extend(["X-GNOME-Autostart-enabled=" + str(enabled).lower(),
                      "Hidden=" + str(not enabled).lower()])
    return "\n".join(lines) + "\n"


def autostart_path() -> Path:
    return config_dir().parent / "autostart/quota-widget.desktop"


def autostart_enabled() -> bool:
    try:
        text = _owned_text(autostart_path())
    except (OSError, ValueError, UnicodeError):
        return False
    if text is None:
        return False
    values = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    return values.get("Hidden", "false").lower() != "true" and values.get(
        "X-GNOME-Autostart-enabled", "true").lower() != "false"


def autostart(enabled: bool) -> Path:
    target = autostart_path()
    # Keep a disabled entry so a later launch cannot undo the user's choice.
    _write_owned(target, _entry(login=True, enabled=enabled))
    return target


def ensure_desktop_integration() -> list[str]:
    """Register at first GUI launch, preserve later opt-out, migrate owned entries."""
    warnings = []
    data_home = environment_path("XDG_DATA_HOME", Path.home() / ".local/share")
    applications = data_home / "applications"
    try:
        _write_owned(applications / f"{DESKTOP_ID}.desktop", _entry())
        legacy = applications / "quota-widget.desktop"
        if _owned_text(legacy) is not None:
            # Hide the obsolete menu entry without affecting unrelated launchers.
            _write_owned(legacy, "[Desktop Entry]\nType=Application\nName=Quota Dock\n"
                         "Hidden=true\n" + MARKER + "\n")
    except (OSError, ValueError, UnicodeError) as exc:
        warnings.append("Application menu registration failed: " + str(exc))
    preferences = config_dir() / "desktop-integration.json"
    initialized = read_json(preferences, {})
    initialized = isinstance(initialized, dict) and initialized.get("autostart_initialized") is True
    try:
        if not initialized:
            previous = _owned_text(autostart_path())
            autostart(autostart_enabled() if previous is not None else True)
            write_json(preferences, {"autostart_initialized": True})
        elif _owned_text(autostart_path()) is not None:
            # Refresh installation paths after an upgrade; retain enabled/disabled state.
            autostart(autostart_enabled())
    except (OSError, ValueError, UnicodeError) as exc:
        warnings.append("Login autostart registration failed: " + str(exc))
    return warnings


def desktop_notify(title: str, body: str) -> bool:
    executable = shutil.which("notify-send")
    if not executable:
        return False
    try:
        import html
        result = subprocess.run([executable, "--app-name=Quota Dock", "--icon=dialog-information",
                                 "--expire-time=12000", "--", html.escape(title), html.escape(body)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
