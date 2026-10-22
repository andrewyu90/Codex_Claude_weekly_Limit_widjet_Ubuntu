"""Small, private, atomic local state store. Never stores authentication material."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _user_path(value: str | Path | None, default: Path) -> Path:
    """Expand a user path without depending on the launcher's working directory.

    Empty environment values use the default. Relative values are anchored to
    the user's home, so desktop launches and status-line callbacks agree. Do
    not resolve symlinks: a configured directory keeps its chosen identity.
    """
    path = Path(value).expanduser() if value is not None and str(value) else Path(default).expanduser()
    if not path.is_absolute():
        path = Path.home() / path
    return Path(os.path.normpath(path))


def environment_path(name: str, default: Path) -> Path:
    """Return an absolute, home-relative path from an optional environment value."""
    return _user_path(os.environ.get(name), default)


def state_dir(data_dir: Path | None = None) -> Path:
    """Shared cache location for the widget, launcher and Claude callback."""
    default = Path.home() / ".local/state/quota-widget"
    if data_dir is not None:
        return _user_path(data_dir, default)
    if os.environ.get("QUOTA_WIDGET_DATA_DIR"):
        return environment_path("QUOTA_WIDGET_DATA_DIR", default)
    return environment_path("XDG_STATE_HOME", Path.home() / ".local/state") / "quota-widget"


def config_dir() -> Path:
    return environment_path("XDG_CONFIG_HOME", Path.home() / ".config") / "quota-widget"


def claude_config_dir(config_dir: Path | None = None) -> Path:
    """Claude's user settings location; never reads authentication files."""
    default = Path.home() / ".claude"
    if config_dir is not None:
        return _user_path(config_dir, default)
    return environment_path("CLAUDE_CONFIG_DIR", default)


def private_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("The state directory is a symbolic link. Choose a different directory.")
    path.chmod(0o700)
    return path


def read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.is_symlink() or path.stat().st_size > 2_000_000:
            return default
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError, UnicodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    private_directory(path.parent)
    fd, name = tempfile.mkstemp(prefix=".quota-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


DEFAULT_SETTINGS = {
    "poll_seconds": 60,
    "always_on_top": True,
    "notifications": True,
    "reset_due_notifications": True,
    "codex_binary": "codex",
    "claude_binary": "claude",
    "claude_auto_query": True,
    "claude_poll_seconds": 60,
}


def load_settings() -> dict:
    data = read_json(config_dir() / "settings.json", {})
    if not isinstance(data, dict):
        data = {}
    settings = DEFAULT_SETTINGS.copy()
    settings.update({k: v for k, v in data.items() if k in settings})
    if isinstance(settings["poll_seconds"], bool) or not isinstance(settings["poll_seconds"], int):
        settings["poll_seconds"] = 60
    settings["poll_seconds"] = max(30, min(900, settings["poll_seconds"]))
    if isinstance(settings["claude_poll_seconds"], bool) or not isinstance(settings["claude_poll_seconds"], int):
        settings["claude_poll_seconds"] = 60
    settings["claude_poll_seconds"] = max(60, min(900, settings["claude_poll_seconds"]))
    for key in ("always_on_top", "notifications", "reset_due_notifications", "claude_auto_query"):
        settings[key] = settings[key] if isinstance(settings[key], bool) else DEFAULT_SETTINGS[key]
    for provider in ("codex", "claude"):
        key = provider + "_binary"
        if not isinstance(settings[key], str) or not settings[key].strip():
            settings[key] = provider
    return settings


def save_settings(settings: dict) -> None:
    write_json(config_dir() / "settings.json", {key: settings.get(key, value) for key, value in DEFAULT_SETTINGS.items()})
