"""Sanitized Claude observations and local diagnostics, with no network calls."""
from __future__ import annotations

import copy
import re
import time
from pathlib import Path

from .models import display_snapshot, number, snapshot, timestamp
from .storage import read_json, state_dir, write_json


STATES = {"ok", "waiting", "error", "stale", "unsupported"}
CLEAR_ERRORS = {"login_required", "subscription_unsupported", "scope_missing", "identity_changed"}


def clean_observation(raw, *, source_kind="direct") -> dict:
    result = snapshot("claude", "waiting")
    result.update(source_kind=source_kind,
                  source="Claude Code usage query" if source_kind == "direct" else "Claude Code status line")
    if not isinstance(raw, dict) or raw.get("provider") != "claude":
        result["message"] = ("Checking Claude usage." if source_kind == "direct" else
                             "Usage will appear when a connected Claude Code session reports it.")
        return result
    state = raw.get("state")
    result["state"] = state if isinstance(state, str) and state in STATES else "waiting"
    for key in ("message", "source", "account_label", "account_key"):
        if isinstance(raw.get(key), str):
            result[key] = raw[key][:500]
    for key in ("received_at", "observed_at", "last_attempt_at"):
        result[key] = timestamp(raw.get(key))
    code = raw.get("error_code")
    result["error_code"] = code if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,64}", code) else ""
    version = raw.get("cli_version")
    result["cli_version"] = version if isinstance(version, str) and re.fullmatch(r"\d{1,5}\.\d{1,5}\.\d{1,5}", version) else None
    delay = number(raw.get("retry_after"), maximum=86400)
    if delay is not None:
        result["retry_after"] = delay
    if raw.get("clear_cache") is True:
        result["clear_cache"] = True
    windows = raw.get("windows")
    for item in windows[:32] if isinstance(windows, list) else []:
        if not isinstance(item, dict) or item.get("kind") not in ("weekly", "short", "other"):
            continue
        used = number(item.get("used_percent"), maximum=100)
        reset = timestamp(item.get("resets_at"))
        if used is None or (item.get("resets_at") is not None and reset is None):
            continue
        result["windows"].append({
            "key": str(item.get("key", ""))[:100], "kind": item["kind"],
            "bucket": str(item.get("bucket", "claude"))[:100],
            "label": str(item.get("label", "Weekly limit"))[:100],
            "used_percent": used, "resets_at": reset,
            "duration_minutes": number(item.get("duration_minutes"), maximum=525600),
        })
    if result["state"] == "ok" and not any(w["kind"] == "weekly" for w in result["windows"]):
        result.update(state="waiting", message="Claude did not provide a weekly limit.")
    return result


def _cached_presentation(data: dict) -> None:
    """Rebuild app-owned display text without changing cached observations.

    Saved text may come from any earlier language. Stable metadata supplies
    English presentation without translating arbitrary strings or writing the
    cache back. Live provider messages still pass through clean_observation.
    """
    direct = data["source_kind"] == "direct"
    data["source"] = "Claude Code usage query" if direct else "Claude Code status line"
    if direct:
        if data.get("account_label") not in ("Claude Pro", "Claude Max", "Claude Team", "Claude Enterprise"):
            data["account_label"] = "Claude subscription"
    else:
        data["account_label"] = "Claude subscription · latest observed session"
    for window in data["windows"]:
        if window["kind"] == "weekly" or window["key"] == "claude:seven_day":
            label = "Weekly limit · all models" if direct else "Weekly limit"
        elif window["key"] == "claude:five_hour" or window["duration_minutes"] == 300:
            label = "5-hour limit"
        elif window["kind"] == "short":
            label = "Short-term limit"
        else:
            label = "Usage limit"
        window["label"] = label

    messages = {
        "login_required": "Sign in to Claude Code with claude auth login, then refresh.",
        "subscription_unsupported": "This Claude sign-in does not provide weekly subscription limits.",
        "scope_missing": "Claude usage access was not granted. Check the CLI sign-in status.",
        "identity_changed": "The Claude account changed. Waiting for usage from the current account.",
        "account_changed": "The Claude account changed. Waiting for usage from the current account.",
        "version_unsupported": "Independent usage queries require Claude Code 2.1.306 or later.",
        "version_unknown": "The Claude Code version could not be verified. Check the CLI installation.",
        "cli_not_found": "Claude Code was not found. Check the CLI installation and executable path.",
        "launch_failed": "The Claude CLI could not be started. Check its executable path.",
        "approval_required": "Claude requested approval. Review the request in Claude Code before refreshing.",
        "timeout": "The last Claude usage query timed out. Waiting for the next attempt.",
        "weekly_missing": "Claude did not provide weekly usage. Missing values are not treated as 0%.",
        "weekly_expired": "The recorded weekly reset time has passed. Waiting for new usage data.",
        "usage_stale": "The stored Claude usage could not be verified as recent. Waiting for a new observation.",
    }
    defaults = {
        "ok": ("Usage from the last recorded Claude Code query. The CLI may reuse its recent 1-minute cache."
               if direct else "Usage from the last recorded Claude session. Other devices may not be reflected immediately."),
        "waiting": "Waiting for Claude usage." if direct else "Waiting for usage from a connected Claude Code session.",
        "error": "The last Claude usage query failed. Waiting for the next attempt.",
        "stale": "This is a previous Claude observation. Waiting for updated usage.",
        "unsupported": "The current Claude account or CLI version does not support this usage query.",
    }
    data["message"] = messages.get(data.get("error_code"), defaults[data["state"]])


def read_observation(*, automatic: bool, startup: bool = False, data_dir: Path | None = None) -> dict:
    kind = "direct" if automatic else "session"
    filename = "claude-direct.json" if automatic else "claude.json"
    raw = read_json(state_dir(data_dir) / filename)
    result = clean_observation(raw, source_kind=kind)
    if isinstance(raw, dict) and raw.get("provider") == "claude":
        _cached_presentation(result)
    if startup and automatic and result["windows"]:
        result.update(state="stale", message="This is a previous query result. Checking usage for the currently signed-in account.")
    return result


def merge_direct(previous: dict, incoming: dict) -> dict:
    """Retain historical numbers on failure without merging accounts or sources."""
    result = clean_observation(incoming)
    if result["windows"] or result["state"] == "ok":
        return result
    if result.get("clear_cache") or result.get("error_code") in CLEAR_ERRORS:
        return result
    old_key, new_key = previous.get("account_key"), result.get("account_key")
    if old_key and new_key and old_key != new_key:
        return result
    if previous.get("source_kind") != "direct" or not previous.get("windows"):
        return result
    result["windows"] = copy.deepcopy(previous["windows"])
    result["observed_at"] = previous.get("observed_at")
    result["account_key"] = new_key or old_key
    result["account_label"] = previous.get("account_label", "Claude subscription")
    # A previous successful query must never become a new success by reuse.
    if result["state"] == "waiting":
        result["state"] = "stale"
    return result


def save_observation(value: dict, data_dir: Path | None = None) -> None:
    write_json(state_dir(data_dir) / "claude-direct.json", clean_observation(value))


def automatic_diagnosis(settings: dict, observation: dict | None = None, *,
                        fetching=False, next_retry_seconds=None, now=None) -> dict:
    from .claude_diagnostics import diagnosis
    from .claude_provider import MIN_CLAUDE_VERSION, locate_claude

    current = time.time() if now is None else now
    bridge = diagnosis(include_version=False, now=current)
    if not settings.get("claude_auto_query", True):
        return {"mode": "session", **bridge}
    data = clean_observation(observation) if observation is not None else read_observation(automatic=True)
    data = display_snapshot(data, now=current, stale_after=max(180, 3 * settings.get("claude_poll_seconds", 60)))
    code = data.get("error_code", "")
    actions = []
    if code == "login_required":
        actions.append("Run claude auth login in a terminal as your regular user, then refresh.")
    elif code in {"cli_unsupported", "version_unsupported", "version_unknown"}:
        actions.append("Run claude update in a terminal as your regular user, then refresh.")
    elif code in {"cli_missing", "cli_not_found", "launch_failed"}:
        actions.append("Check the Claude Code installation and set the full Claude executable path in Settings.")
    elif code == "approval_required":
        actions.append("Open Claude Code, review any required approvals, and refresh.")
    elif code in {"scope_missing", "subscription_unsupported"} or data["state"] == "unsupported":
        actions.append("Check that you are signed in with a Claude subscription. API key usage is separate from weekly subscription limits.")
    elif data["state"] in {"error", "stale", "waiting"}:
        actions.append("Check your internet connection and Claude sign-in status. The query will retry automatically.")
    if fetching:
        summary = "Querying Claude usage. The displayed previous value keeps its original timestamp."
    else:
        summary = data.get("message") or ("Claude automatic queries are working." if data["state"] == "ok" else "Claude usage needs attention.")
    weekly = next((item for item in data["windows"] if item["kind"] == "weekly"), None)

    def age(key):
        value = timestamp(data.get(key))
        return int(max(0, current - value)) if value and value <= current + 60 else None

    return {
        "mode": "automatic", "state": data["state"], "summary": summary, "actions": actions,
        "error_code": code, "fetching": bool(fetching),
        "cli_found": locate_claude(settings.get("claude_binary", "claude")) is not None,
        "cli_version": data.get("cli_version"),
        "minimum_claude_version": ".".join(map(str, MIN_CLAUDE_VERSION)),
        "poll_seconds": settings.get("claude_poll_seconds", 60),
        "observation_age_seconds": age("observed_at"),
        "last_attempt_age_seconds": age("last_attempt_at"),
        "retry_after_seconds": max(0, int(next_retry_seconds)) if next_retry_seconds is not None else None,
        "weekly_used_percent": weekly["used_percent"] if weekly else None,
        "weekly_resets_at": weekly["resets_at"] if weekly else None,
        "account_identified": bool(data.get("account_key")),
        "notes": ["Claude Code handles authentication and queries. The widget does not send model prompts.",
                  "The CLI may reuse a query from the last minute or delay retries because of query limits."],
        "session_bridge": bridge,
    }
