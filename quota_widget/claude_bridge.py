"""Credential-free Claude Code status-line bridge.

Only the documented ``rate_limits`` portion of a status-line callback is
exported. A callback is a local observation, not a fresh server query. Repeated
callbacks therefore update the heartbeat but never refresh an unchanged quota
observation. No network, credentials, transcripts, or model calls are used.
"""

from __future__ import annotations

import contextlib
import copy
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator

from .storage import claude_config_dir, state_dir


MAX_INPUT_BYTES = 4 * 1024 * 1024
DELEGATE_TIMEOUT_SECONDS = 2.0
_MISSING = object()


class BridgeError(RuntimeError):
    """A safe, user-readable setup error that contains no input payload."""


def _data_dir(data_dir: Path | None = None) -> Path:
    return state_dir(data_dir)


def _config_dir(config_dir: Path | None = None) -> Path:
    return claude_config_dir(config_dir)


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


@contextlib.contextmanager
def _lock(path: Path) -> Iterator[None]:
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _atomic_json(path: Path, value: Any) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_object(path: Path, *, strict: bool = False) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError("too large")
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError("expected JSON object")
        return result
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        if strict:
            raise BridgeError("Cannot read the settings or bridge record. The original files were not changed.") from exc
        return {}


def _finite_number(value: Any, *, maximum: float | None = None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    if maximum is not None and number > maximum:
        return None
    return number


def _safe_text(value: Any, length: int = 256) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(char for char in value[:length] if char.isprintable()).strip()


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _response_signature(payload: dict[str, Any]) -> dict[str, Any]:
    """Allow-list response counters; never include elapsed wall-clock time."""
    result: dict[str, Any] = {}
    context = payload.get("context_window")
    if isinstance(context, dict):
        for name in ("total_input_tokens", "total_output_tokens"):
            value = _finite_number(context.get(name))
            if value is not None:
                result[name] = value
        current = context.get("current_usage")
        if isinstance(current, dict):
            for name in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
                value = _finite_number(current.get(name))
                if value is not None:
                    result[f"current_{name}"] = value
    cost = payload.get("cost")
    if isinstance(cost, dict):
        for name in ("total_api_duration_ms", "total_cost_usd"):
            value = _finite_number(cost.get(name))
            if value is not None:
                result[name] = value
    return result


def _window_signature(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep quota semantics independent of translated display labels."""
    fields = ("key", "bucket", "kind", "used_percent", "resets_at", "duration_minutes")
    return [{field: window[field] for field in fields} for window in windows]


def _session_records(saved: dict[str, Any], directory: Path) -> dict[str, dict[str, Any]]:
    """Read only our sanitised, bounded per-session observation records."""
    records: dict[str, dict[str, Any]] = {}
    raw = saved.get("sessions")
    if not isinstance(raw, dict):
        # Migrate the first release's single-session sidecar without making its
        # observation newer merely because the application was upgraded.
        legacy_snapshot = _read_object(directory / "claude.json")
        key = saved.get("account_key")
        raw = {key: {**saved, "snapshot": legacy_snapshot, "sequence": 0, "identified": True}} if isinstance(key, str) else {}
    fields = ("provider", "state", "message", "source", "account_label", "account_key", "received_at", "observed_at", "windows")
    for key, record in list(raw.items())[:100]:
        if not isinstance(key, str) or not isinstance(record, dict):
            continue
        snapshot = record.get("snapshot")
        observed = _finite_number(record.get("observed_at"))
        fingerprint = record.get("fingerprint")
        if (not isinstance(snapshot, dict) or snapshot.get("account_key") != key
                or not isinstance(snapshot.get("windows"), list) or observed is None
                or not isinstance(fingerprint, str) or len(fingerprint) != 64
                or any(field not in snapshot for field in fields)):
            continue
        clean = {field: copy.deepcopy(snapshot[field]) for field in fields}
        clean["account_label"] = "Claude subscription · latest observed session"
        clean["windows"] = [
            {field: window[field] for field in ("key", "label", "bucket", "kind", "used_percent", "resets_at", "duration_minutes")}
            for window in snapshot["windows"][:2]
            if isinstance(window, dict)
            and all(field in window for field in ("key", "label", "bucket", "kind", "used_percent", "resets_at", "duration_minutes"))
            and window.get("kind") in ("weekly", "short")
            and _finite_number(window.get("used_percent"), maximum=100) is not None
            and _finite_number(window.get("resets_at"), maximum=32503680000) not in (None, 0)
        ]
        records[key] = {
            "fingerprint": fingerprint,
            "observed_at": observed,
            "snapshot": clean,
            "sequence": int(_finite_number(record.get("sequence"), maximum=10**15) or 0),
            "identified": record.get("identified") is True,
        }
    return records


def _has_current_weekly(record: dict[str, Any], now: float) -> bool:
    return record.get("identified") is True and any(
        window["kind"] == "weekly" and window["resets_at"] > now
        for window in record["snapshot"]["windows"]
    )


def process_statusline(payload: dict, data_dir: Path | None = None) -> dict:
    """Validate, atomically save, and return a sanitised provider snapshot.

    ``claude.json`` is the public widget snapshot. ``claude-observation.json``
    retains at most 20 hashed session records and their sanitised snapshots.
    The public snapshot is the most recently observed session with a valid
    weekly window. Interleaved cached callbacks cannot freshen an older session
    or replace a newer observation. Raw callbacks and session IDs are not saved.
    A missing or expired window remains unknown; this function never refills it.
    """
    if not isinstance(payload, dict):
        raise ValueError("Claude status-line input must be a JSON object")
    directory = _data_dir(data_dir)
    _private_dir(directory)
    now = time.time()
    session = _safe_text(payload.get("session_id"))
    account_key = _digest([str(_config_dir()), session or "unknown-session"])
    rates = payload.get("rate_limits")
    rates = rates if isinstance(rates, dict) else {}
    windows: list[dict[str, Any]] = []
    for name, label, kind, duration in (
        ("seven_day", "Weekly limit", "weekly", 10080),
        ("five_hour", "5-hour limit", "short", 300),
    ):
        window = rates.get(name)
        if not isinstance(window, dict):
            continue
        used = _finite_number(window.get("used_percentage"), maximum=100)
        reset = _finite_number(window.get("resets_at"), maximum=32503680000)
        if used is None or reset is None or reset == 0:
            continue
        windows.append({
            "key": f"claude:{name}",
            "label": label,
            "bucket": "claude",
            "kind": kind,
            "used_percent": used,
            "resets_at": reset,
            "duration_minutes": duration,
        })

    weekly = next((window for window in windows if window["kind"] == "weekly"), None)
    if not session:
        state = "waiting"
        message = "Waiting for weekly usage because session identification is missing."
    elif weekly is None:
        state = "waiting"
        message = "Waiting for new weekly usage from a Claude session. Missing limits are not treated as 0%."
    elif any(window["resets_at"] <= now for window in windows):
        state = "waiting"
        message = "The scheduled reset time has passed. New usage data is needed."
    else:
        state = "ok"
        message = "Usage reported by a Claude session. Usage on other devices may not appear immediately."

    response = _response_signature(payload)
    semantic_windows = _window_signature(windows)
    fingerprint = _digest({"account_key": account_key, "windows": semantic_windows, "response": response})
    with _lock(directory / ".claude.lock"):
        saved = _read_object(directory / "claude-observation.json")
        records = _session_records(saved, directory)
        previous = records.get(account_key, {})
        previous_time = _finite_number(previous.get("observed_at"))
        unchanged = previous.get("fingerprint") == fingerprint
        if not unchanged and previous:
            # Older releases hashed translated labels too. Rebuild that exact
            # input using the stored labels and CURRENT response counters. A
            # match proves that only presentation changed during the upgrade.
            previous_windows = previous["snapshot"]["windows"]
            if _window_signature(previous_windows) == semantic_windows:
                try:
                    unchanged = previous["fingerprint"] == _digest({
                        "account_key": account_key, "windows": previous_windows, "response": response,
                    })
                except (TypeError, ValueError, RecursionError):
                    unchanged = False
        # A clock correction must not leave an observation dated in the future.
        observed = previous_time if unchanged and previous_time is not None and previous_time <= now else now
        snapshot = {
            "provider": "claude",
            "state": state,
            "message": message,
            "source": "Claude Code status line",
            "account_label": "Claude subscription · latest observed session",
            "account_key": account_key,
            "received_at": now,
            "observed_at": observed,
            "windows": windows,
        }
        records[account_key] = {
            "fingerprint": fingerprint,
            "observed_at": observed,
            "snapshot": snapshot,
            "sequence": max((record["sequence"] for record in records.values()), default=0) + 1,
            "identified": bool(session),
        }
        # Prefer retaining usable weekly observations over brand-new sessions
        # that have not received their first usage-bearing response yet.
        ranked = sorted(records.items(), key=lambda item: (
            _has_current_weekly(item[1], now), item[1]["observed_at"], item[1]["sequence"]
        ), reverse=True)
        records = dict(ranked[:20])
        eligible = [record for record in records.values() if _has_current_weekly(record, now)]
        selected = max(eligible or list(records.values()), key=lambda record: (record["observed_at"], record["sequence"]))
        public_snapshot = copy.deepcopy(selected["snapshot"])
        if any(window["resets_at"] <= now for window in public_snapshot["windows"]):
            public_snapshot["state"] = "waiting"
            public_snapshot["message"] = "The scheduled reset time has passed. New usage data is needed."
        _atomic_json(directory / "claude-observation.json", {"schema_version": 2, "sessions": records})
        _atomic_json(directory / "claude.json", public_snapshot)
    return public_snapshot


def _bridge_command(config: Path, data: Path) -> str:
    return shlex.join([
        "env",
        f"QUOTA_WIDGET_DATA_DIR={data}",
        f"CLAUDE_CONFIG_DIR={config}",
        str(Path(sys.executable).absolute()),
        "-m", "quota_widget", "--claude-statusline",
    ])


def _looks_like_bridge(command: Any) -> bool:
    return isinstance(command, str) and "quota_widget" in command and "--claude-statusline" in command


def install_bridge(config_dir: Path | None = None, data_dir: Path | None = None) -> str:
    """Install an idempotent, reversible status-line wrapper.

    Existing formatting and valid refresh intervals are retained. Only the old
    ``statusLine`` field is backed up, never the entire user settings file.
    """
    config, data = _config_dir(config_dir), _data_dir(data_dir)
    config.mkdir(parents=True, exist_ok=True, mode=0o700)
    _private_dir(data)
    with _lock(config / ".quota-widget-settings.lock"), _lock(data / ".claude.lock"):
        settings_path = config / "settings.json"
        backup_path = data / "claude-bridge.json"
        settings = _read_object(settings_path, strict=True)
        backup = _read_object(backup_path, strict=True)
        current = settings.get("statusLine", _MISSING)
        current_command = current.get("command") if isinstance(current, dict) else None
        command = _bridge_command(config, data)

        if backup:
            if backup.get("config_dir") != str(config):
                raise BridgeError("This storage location is linked to a different Claude configuration. Choose a separate data directory.")
            if current_command != backup.get("command"):
                raise BridgeError("The Claude status line changed after installation. Installation was stopped to preserve your changes.")
            if current_command == command:
                return "The Claude bridge is already installed. Claude Code will report usage as you use it."
            # An application upgrade can move the interpreter. Update only our command.
            updated = copy.deepcopy(current)
            updated["command"] = command
            updated_backup = copy.deepcopy(backup)
            updated_backup["command"] = command
            updated_backup["installed_status_line"] = updated
            _atomic_json(backup_path, updated_backup)
            settings["statusLine"] = updated
            try:
                _atomic_json(settings_path, settings)
            except Exception:
                _atomic_json(backup_path, backup)
                raise
            return "Updated the Claude bridge executable path."

        if _looks_like_bridge(current_command):
            raise BridgeError("The existing bridge has no restore record. Check the status line settings manually before reconnecting.")
        if current is not _MISSING and (not isinstance(current, dict) or current.get("type") != "command" or not isinstance(current.get("command"), str)):
            raise BridgeError("The existing statusLine format is not recognized. The original settings were not changed.")
        installed = copy.deepcopy(current) if isinstance(current, dict) else {}
        installed["type"] = "command"
        installed["command"] = command
        interval = _finite_number(installed.get("refreshInterval"))
        if interval is None or interval < 1:
            installed["refreshInterval"] = 60
        record = {
            "schema_version": 1,
            "config_dir": str(config),
            "command": command,
            "previous_existed": current is not _MISSING,
            "previous_status_line": copy.deepcopy(current) if current is not _MISSING else None,
            "installed_status_line": installed,
        }
        # Persist restoration information before changing the existing settings.
        _atomic_json(backup_path, record)
        settings["statusLine"] = installed
        try:
            _atomic_json(settings_path, settings)
        except Exception:
            backup_path.unlink(missing_ok=True)
            raise
    return "The Claude bridge is installed. Weekly usage will appear after the next Claude Code response."


def uninstall_bridge(config_dir: Path | None = None, data_dir: Path | None = None) -> str:
    """Restore the previous status line while preserving subsequent settings edits."""
    config, data = _config_dir(config_dir), _data_dir(data_dir)
    backup_path = data / "claude-bridge.json"
    if not backup_path.exists():
        return "No installed Claude bridge record was found."
    config.mkdir(parents=True, exist_ok=True, mode=0o700)
    _private_dir(data)
    with _lock(config / ".quota-widget-settings.lock"), _lock(data / ".claude.lock"):
        backup = _read_object(backup_path, strict=True)
        if backup.get("config_dir") != str(config):
            raise BridgeError("The Claude configuration directory in the restore record differs from the current selection.")
        settings_path = config / "settings.json"
        settings = _read_object(settings_path, strict=True)
        current = settings.get("statusLine")
        if not isinstance(current, dict) or current.get("command") != backup.get("command"):
            raise BridgeError("The Claude status line command changed after installation. The current user settings were not overwritten.")
        previous = backup.get("previous_status_line")
        installed = backup.get("installed_status_line")
        if not isinstance(installed, dict):
            raise BridgeError("The restore record format is invalid. The current settings were preserved.")
        if not backup.get("previous_existed"):
            if current != installed:
                raise BridgeError("New status line settings were added. Reconcile statusLine manually to preserve those changes.")
            settings.pop("statusLine", None)
        else:
            if not isinstance(previous, dict):
                raise BridgeError("Cannot restore the previous status line record. The current settings were preserved.")
            restored = copy.deepcopy(current)
            for key in set(installed) | set(previous):
                if current.get(key, _MISSING) == installed.get(key, _MISSING):
                    if key in previous:
                        restored[key] = copy.deepcopy(previous[key])
                    else:
                        restored.pop(key, None)
            settings["statusLine"] = restored
        _atomic_json(settings_path, settings)
        backup_path.unlink()
    return "Removed the Claude bridge and restored the previous status line. Other settings were preserved."


def _stop_process_group(process: subprocess.Popen) -> bytes:
    try:
        for sig, timeout in ((signal.SIGTERM, 0.3), (signal.SIGKILL, 0.5)):
            try:
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                pass
            try:
                output, _ = process.communicate(timeout=timeout)
                return output or b""
            except subprocess.TimeoutExpired:
                continue
        return b""
    finally:
        # A descendant may close its inherited pipes and ignore SIGTERM. Even
        # after the shell exits, kill any remaining member of our process group.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _delegate(raw_input: bytes, data: Path) -> bytes | None:
    backup = _read_object(data / "claude-bridge.json")
    previous = backup.get("previous_status_line")
    command = previous.get("command") if isinstance(previous, dict) and previous.get("type") == "command" else None
    if not isinstance(command, str) or not command.strip() or _looks_like_bridge(command):
        return None
    try:
        process = subprocess.Popen(
            command,
            shell=True,
            executable="/bin/sh",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            output, _ = process.communicate(input=raw_input, timeout=DELEGATE_TIMEOUT_SECONDS)
            return output
        except subprocess.TimeoutExpired:
            return _stop_process_group(process) or None
    except (OSError, ValueError):
        return None


def run_statusline() -> int:
    """Read one callback and preserve an existing status line's exact output."""
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        sys.stdout.write("Claude · usage input is too large\n")
        return 1
    snapshot: dict[str, Any] | None = None
    try:
        payload = json.loads(raw)
        snapshot = process_statusline(payload)
    except (ValueError, TypeError, OSError, UnicodeError):
        # Never print raw callbacks or errors from private configuration.
        pass
    delegated = _delegate(raw, _data_dir())
    if delegated is not None:
        sys.stdout.buffer.write(delegated)
        sys.stdout.buffer.flush()
        return 0
    parts = []
    if snapshot:
        for window in snapshot["windows"]:
            if window["resets_at"] <= snapshot["received_at"]:
                continue
            label = "Weekly" if window["kind"] == "weekly" else "5-hour"
            parts.append(f"{label} {window['used_percent']:.0f}% used")
    sys.stdout.write("Claude · " + (" · ".join(parts) if parts else "waiting for new usage") + "\n")
    return 0
