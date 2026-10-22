"""Validated provider observations, separate from display and reset inference."""
from __future__ import annotations

import hashlib
import json
import math
import time

WEEK_MINUTES = 7 * 24 * 60


def number(value, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    try:
        value = float(value)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(value) or value < minimum or (maximum is not None and value > maximum):
        return None
    return value


def timestamp(value):
    return number(value, minimum=1, maximum=253402300799)


def snapshot(provider: str, state: str = "waiting", message: str = "") -> dict:
    return {
        "provider": provider,
        "state": state,
        "message": message,
        "source": "Codex account API" if provider == "codex" else "Claude Code status line",
        "source_kind": "direct" if provider == "codex" else "session",
        "account_label": "ChatGPT subscription" if provider == "codex" else "Claude Code session",
        "account_key": "",
        "received_at": None,
        "observed_at": None,
        "windows": [],
    }


def identity_hash(data: dict) -> str:
    # An email may participate in the local hash but is never stored/displayed.
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:24]


def parse_codex(payload: dict, account: dict, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    result = snapshot("codex", "ok")
    result.update(received_at=now, observed_at=now,
                  account_key=identity_hash(account),
                  account_label="ChatGPT " + str(account.get("planType") or "subscription").title())
    if not isinstance(payload, dict):
        raise ValueError("Unable to read the usage response.")
    buckets = payload.get("rateLimitsByLimitId")
    if not isinstance(buckets, dict) or not buckets:
        single = payload.get("rateLimits")
        buckets = {str(single.get("limitId") or "codex"): single} if isinstance(single, dict) else {}
    invalid = False
    for bucket_id, bucket in sorted(buckets.items(), key=lambda item: (item[0] != "codex", str(item[0]))):
        if not isinstance(bucket, dict):
            invalid = True
            continue
        for slot in ("primary", "secondary"):
            window = bucket.get(slot)
            if window is None:
                continue
            if not isinstance(window, dict):
                invalid = True
                continue
            used = number(window.get("usedPercent"), maximum=100)
            duration = number(window.get("windowDurationMins"), minimum=1)
            reset = timestamp(window.get("resetsAt"))
            if used is None or (window.get("resetsAt") is not None and reset is None):
                invalid = True
                continue
            minutes = int(duration) if duration is not None and duration.is_integer() else None
            kind = "weekly" if minutes == WEEK_MINUTES else "short" if minutes is not None and minutes < WEEK_MINUTES else "other"
            label = "Weekly limit" if kind == "weekly" else f"{minutes // 60}-hour limit" if minutes and minutes % 60 == 0 else f"{minutes}-minute limit" if minutes else "Period not provided"
            result["windows"].append({
                "key": f"{bucket_id}:{slot}", "label": label,
                "bucket": str(bucket_id), "kind": kind,
                "used_percent": used, "resets_at": reset,
                "duration_minutes": minutes,
            })
    if not result["windows"]:
        result.update(state="unsupported", message="The account did not provide a weekly subscription limit. Check its sign-in method and available limits.")
    elif not any(w["kind"] == "weekly" for w in result["windows"]):
        result["message"] = "No weekly limit was included for this account. Only the reported limits are shown."
    if invalid:
        result["message"] = "Some limits could not be read. Only valid entries are shown."
    return result


def display_snapshot(data: dict, now: float | None = None, stale_after: int = 180) -> dict:
    """Age data without ever filling a quota simply because a timer elapsed."""
    now = time.time() if now is None else now
    result = dict(data)
    windows = [w for w in data.get("windows", []) if isinstance(w, dict)]
    result["windows"] = windows
    if result.get("state") not in ("ok", "stale"):
        return result
    weekly = [w for w in windows if w.get("kind") == "weekly"]
    expired = [w for w in weekly if timestamp(w.get("resets_at")) is not None and w["resets_at"] <= now]
    observed = timestamp(result.get("observed_at"))
    if expired:
        result.update(state="stale", message="The reset time has passed. Waiting for a new usage reading.")
    elif observed is None or now - observed > stale_after or observed > now + 60:
        if result.get("provider") == "claude" and result.get("source_kind") != "direct":
            message = "This is the last session reading. Waiting for a new Claude Code response."
        else:
            message = "No recent query. Showing the last confirmed reading."
        result.update(state="stale", message=message)
    return result
