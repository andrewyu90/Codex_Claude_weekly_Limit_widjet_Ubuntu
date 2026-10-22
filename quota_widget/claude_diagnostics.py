"""Read-only, shareable Claude bridge diagnostics.

Only configuration ownership, local callback metadata and an optional
``claude --version`` are checked. No status-line command, authentication
command, network request or model turn is executed. User settings are not
necessarily the effective settings of an existing Claude Code session.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import time
from typing import Any

from .storage import claude_config_dir, state_dir


MAX_JSON_BYTES = 2_000_000
VERSION_TIMEOUT_SECONDS = 2.0
OBSERVATION_STALE_SECONDS = 180


def _object_file(path: Path) -> tuple[dict, str]:
    """Return a bounded object and a safe error category, never exception text."""
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_JSON_BYTES:
            return {}, "unreadable"
        with path.open("rb") as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            return {}, "unreadable"
        value = json.loads(raw)
        return (value, "ok") if isinstance(value, dict) else ({}, "invalid")
    except FileNotFoundError:
        return {}, "missing"
    except OSError:
        return {}, "unreadable"
    except (ValueError, UnicodeError, RecursionError):
        return {}, "invalid"


def _number(value: Any, maximum: float = 32503680000) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) and 0 <= number <= maximum else None


def _age(value: Any, now: float) -> int | None:
    timestamp = _number(value)
    if timestamp is None or timestamp <= 0 or timestamp > now + 60:
        return None
    return max(0, int(now - timestamp))


def _same_path(value: Any, expected: Path) -> bool | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            return None
        return Path(os.path.normpath(candidate)) == expected
    except (ValueError, RuntimeError):
        return None


def _command_metadata(command: Any, config: Path, data: Path) -> dict:
    """Inspect only our known command shape; never run a settings command."""
    result = {
        "command_recognized": False,
        "callback_data_path_matches": None,
        "callback_config_path_matches": None,
        "bridge_executable_available": None,
    }
    if not isinstance(command, str):
        return result
    try:
        parts = shlex.split(command)
    except ValueError:
        return result
    if not parts or parts[0] != "env":
        return result
    assignments: dict[str, str] = {}
    index = 1
    while index < len(parts) and "=" in parts[index]:
        name, value = parts[index].split("=", 1)
        if name in ("QUOTA_WIDGET_DATA_DIR", "CLAUDE_CONFIG_DIR"):
            assignments[name] = value
        index += 1
    invocation = parts[index:]
    if len(invocation) != 4 or invocation[1:] != ["-m", "quota_widget", "--claude-statusline"]:
        return result
    result["command_recognized"] = True
    result["callback_data_path_matches"] = _same_path(assignments.get("QUOTA_WIDGET_DATA_DIR"), data)
    result["callback_config_path_matches"] = _same_path(assignments.get("CLAUDE_CONFIG_DIR"), config)
    executable = Path(invocation[0])
    if executable.is_absolute():
        try:
            result["bridge_executable_available"] = executable.is_file() and os.access(executable, os.X_OK)
        except (OSError, ValueError):
            result["bridge_executable_available"] = False
    return result


def _locate_cli() -> str | None:
    executable = shutil.which("claude")
    if executable:
        return executable
    for candidate in (
        Path.home() / ".local/bin/claude",
        Path.home() / ".npm-global/bin/claude",
        Path("/usr/local/bin/claude"),
    ):
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue
    return None


def _cli_metadata(include_version: bool) -> dict:
    executable = _locate_cli()
    result = {"cli_found": executable is not None, "cli_version": None,
              "version_status": "not_checked" if executable else "not_found"}
    if not executable or not include_version:
        return result
    try:
        completed = subprocess.run(
            [executable, "--version"], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=VERSION_TIMEOUT_SECONDS, check=False,
            encoding="utf-8", errors="replace",
        )
        if completed.returncode != 0:
            result["version_status"] = "failed"
            return result
        # Return only a numeric version, never arbitrary CLI output or paths.
        match = re.match(r"\s*(\d{1,5}\.\d{1,5}\.\d{1,5})(?:\b|$)", completed.stdout[:256])
        result["cli_version"] = match.group(1) if match else None
        result["version_status"] = "ok" if match else "unrecognized"
    except subprocess.TimeoutExpired:
        result["version_status"] = "timeout"
    except (OSError, ValueError, UnicodeError):
        result["version_status"] = "failed"
    return result


def diagnosis(
    config_dir: Path | None = None,
    data_dir: Path | None = None,
    *,
    include_version: bool = True,
    now: float | None = None,
) -> dict:
    """Return sanitized flags, an English summary and concrete next actions.

    ``include_version=False`` performs only local filesystem reads. No paths,
    commands, session identifiers, account details or raw JSON are returned.
    Heartbeat and observation ages refer to the public snapshot selected by
    the bridge, which can be older than another session's latest callback.
    """
    config, data = claude_config_dir(config_dir), state_dir(data_dir)
    current_time = time.time() if now is None else float(now)
    settings, settings_status = _object_file(config / "settings.json")
    backup, backup_status = _object_file(data / "claude-bridge.json")
    cache, cache_status = _object_file(data / "claude.json")
    statusline = settings.get("statusLine")
    command = statusline.get("command") if isinstance(statusline, dict) else None
    registered = bool(
        isinstance(statusline, dict) and statusline.get("type") == "command"
        and isinstance(command, str) and command
        and isinstance(backup.get("command"), str) and command == backup["command"]
    )
    config_matches = _same_path(backup.get("config_dir"), config)
    command_metadata = _command_metadata(command, config, data)
    cache_present = cache_status == "ok" and cache.get("provider") == "claude"
    if cache_status == "ok" and (not cache_present or not isinstance(cache.get("windows"), list)):
        cache_status = "invalid"
        cache_present = False
    received = _number(cache.get("received_at")) if cache_present else None
    observed = _number(cache.get("observed_at")) if cache_present else None
    weekly = []
    for window in cache.get("windows", [])[:32] if cache_present else []:
        if not isinstance(window, dict) or window.get("kind") != "weekly":
            continue
        used, reset = _number(window.get("used_percent"), 100), _number(window.get("resets_at"))
        if used is not None and reset is not None and reset > 0:
            weekly.append(reset)
    result = {
        "state": "not_connected", "summary": "", "actions": [],
        "bridge_registered": registered,
        "callback_received": received is not None and received > 0,
        "cache_present": cache_present,
        "weekly_present": bool(weekly),
        "weekly_expired": bool(weekly) and all(reset <= current_time for reset in weekly),
        "observation_age_seconds": _age(observed, current_time),
        "heartbeat_age_seconds": _age(received, current_time),
        "settings_status": settings_status, "bridge_record_status": backup_status,
        "cache_status": cache_status, "config_path_matches": config_matches,
        "settings_scope": "user", "effective_settings_verified": False,
        "heartbeat_scope": "selected_session",
        **command_metadata, **_cli_metadata(include_version),
        "notes": [
            "Registration is checked against user settings only. Project settings, managed settings, and command-line overrides are not checked.",
            "Timestamps refer to the most recent observed session selected by the widget. Repeating a callback does not refresh server usage.",
        ],
    }

    def explain(state: str, summary: str, *actions: str) -> dict:
        result.update(state=state, summary=summary, actions=list(actions))
        return result

    if settings_status in ("invalid", "unreadable"):
        return explain("settings_error", "Cannot read Claude user settings.",
                       "Check the Claude Code settings JSON and read permissions. Existing files were not changed.")
    if backup_status in ("invalid", "unreadable"):
        return explain("bridge_record_error", "Cannot read the Claude bridge restore record.",
                       "Check the widget state directory permissions and existing installation path. Keep the restore record.")
    if config_matches is False or any(command_metadata[key] is False for key in (
            "callback_data_path_matches", "callback_config_path_matches")):
        return explain("path_mismatch", "The paths saved in the callback differ from the current widget paths.",
                       "Run the widget and Claude bridge with the same QUOTA_WIDGET_DATA_DIR and CLAUDE_CONFIG_DIR environment.",
                       "Use the original installation environment to remove the bridge. User settings were not overwritten.")
    if backup_status == "ok" and not registered:
        return explain("settings_changed", "The restore record differs from the current Claude status line settings.",
                       "Check changes to statusLine since installation and the selected Claude configuration directory. Existing settings were preserved.")
    if not registered:
        if command_metadata["command_recognized"]:
            return explain("bridge_record_missing", "A status line command exists, but this widget has no restore record for it.",
                           "Check that the widget state directory and environment match the original bridge installation. Keep the existing status line.")
        return explain("not_connected", "No Claude bridge was found in the current user settings.",
                       "Open the widget settings and connect the Claude session bridge, or run quota-widget --install-claude in a terminal.",
                       "Run claude --version to check whether Claude Code is installed.")
    if command_metadata["bridge_executable_available"] is False:
        return explain("executable_missing", "The registered Claude callback executable is missing or cannot be executed.",
                       "Open the widget settings and connect the Claude session bridge to update its executable path for this installation.")
    if cache_status in ("invalid", "unreadable"):
        return explain("cache_error", "Cannot read the Claude callback cache.",
                       "Check read permissions on the widget state directory, then diagnose again after the next successful Claude Code response.")
    if not result["callback_received"]:
        return explain("callback_pending", "The bridge is registered in user settings, but no callback has been received yet.",
                       "Check again after Claude Code returns a new response during your normal work.",
                       "If it remains empty, check the configuration directory used by Claude Code and statusLine in project or managed settings.")
    if not weekly:
        return explain("weekly_missing", "A callback was received, but it contains no valid weekly usage field.",
                       "Check again after the next successful Claude Code response, and confirm that the account and sign-in method provide weekly limits.",
                       "Values may be absent after a reset until new limit fields arrive. Missing values are not treated as 0%.")
    if result["weekly_expired"]:
        return explain("weekly_expired", "The scheduled reset time for the received weekly limit has passed.",
                       "Wait for new usage data from Claude Code. Elapsed time alone does not confirm a completed reset.")
    if (result["observation_age_seconds"] is None or result["heartbeat_age_seconds"] is None):
        return explain("timestamp_invalid", "A weekly value is available, but its observation timestamp cannot be trusted.",
                       "Check the system clock, then diagnose again after the next successful Claude Code response.")
    if result["observation_age_seconds"] > OBSERVATION_STALE_SECONDS:
        return explain("stale", "A weekly value is available, but there has been no new usage observation for over 3 minutes.",
                       "The displayed number is the last observation. It will update when a new Claude Code response reports usage.")
    return explain("ok", "Weekly usage was received from a recent Claude Code session.",
                   "Usage on other devices may not appear immediately. Updates follow new responses from the active Claude Code session.")
