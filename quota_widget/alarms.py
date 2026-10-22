"""Persistent observation-based alarms; elapsed time never means verified recovery."""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from .models import number, timestamp
from .storage import read_json, write_json


_STORED_PERCENT = re.compile(
    r"(?<![\w.,+\-])([+\-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+\-]?[0-9]+)?)\s*%"
)


def _stored_remaining(body):
    """Recover only an unambiguous historical percentage from an old message."""
    if not isinstance(body, str) or body.count("%") != 1:
        return None
    matches = _STORED_PERCENT.findall(body)
    if len(matches) != 1:
        return None
    value = number(float(matches[0]), maximum=100)
    # Keep the original token rather than rounding or using newer window data.
    return matches[0] if value is not None else None


class AlarmStore:
    def __init__(self, path: Path):
        self.path = path
        state = read_json(path, {})
        self.state = state if isinstance(state, dict) else {}
        for key, default in (("windows", {}), ("events", []), ("active", {})):
            if not isinstance(self.state.get(key), type(default)):
                self.state[key] = default
        # Presentation-only upgrade, including pending desktop-delivery retries.
        # Loading must not write state, create events, or alter delivery history.
        for event in self.state["events"]:
            if not isinstance(event, dict) or event.get("kind") not in ("due", "recovery"):
                continue
            identity = event.get("identity")
            provider = identity.partition("|")[0] if isinstance(identity, str) else ""
            name = {"codex": "Codex", "claude": "Claude"}.get(provider, "Service")
            if event["kind"] == "due":
                event["title"] = f"{name}: Reset due"
                event["body"] = "The scheduled reset time has passed. Recovery will be confirmed when a new usage reading arrives."
            else:
                remaining = _stored_remaining(event.get("body"))
                event["title"] = f"{name}: Recovery confirmed"
                event["body"] = (f"Weekly remaining: {remaining}%. Other usage limits may still apply."
                    if remaining is not None else
                    "Weekly capacity recovery was confirmed by an earlier reading. Other usage limits may still apply.")

    def _event(self, identity, kind, reset, title, body, now):
        event_id = hashlib.sha256(f"{identity}|{kind}|{reset}".encode()).hexdigest()[:24]
        if any(e.get("id") == event_id for e in self.state["events"]):
            return
        self.state["events"].append({"id": event_id, "identity": identity, "kind": kind, "title": title,
                                     "body": body, "created_at": now, "sent_at": None,
                                     "attempts": 0, "next_attempt": now})

    def observe(self, data: dict, now=None, recovery_enabled=True, due_enabled=True):
        now = time.time() if now is None else now
        provider = data.get("provider", "")
        account_key = data.get("account_key", "")
        if provider in ("codex", "claude") and self.state["active"].get(provider) != account_key:
            self.state["active"][provider] = account_key
            self._save()
        if data.get("state") != "ok" or not data.get("account_key"):
            return
        observed = timestamp(data.get("observed_at"))
        if observed is None or now - observed > 180 or observed > now + 60:
            return
        name = "Codex" if provider == "codex" else "Claude"
        changed = False
        for window in data.get("windows", []):
            if window.get("kind") != "weekly":
                continue
            used = number(window.get("used_percent"), maximum=100)
            reset = timestamp(window.get("resets_at"))
            if used is None:
                continue
            identity = f"{provider}|{data['account_key']}|{window['key']}"
            old = self.state["windows"].get(identity)
            if isinstance(old, dict) and observed < old.get("observed_at", 0):
                continue
            if isinstance(old, dict) and observed > old.get("observed_at", 0):
                old_used, old_reset = old.get("used_percent"), old.get("resets_at")
                valid_now = reset is None or reset > now
                replenished = old_used is not None and used < old_used and valid_now
                was_blocked = old_used is not None and old_used >= 100 and used < 100
                period_advanced = old_reset is not None and reset is not None and reset > old_reset + 60
                if recovery_enabled and replenished and (was_blocked or period_advanced):
                    self._event(identity, "recovery", old_reset or old.get("observed_at"),
                                f"{name}: Recovery confirmed",
                                f"Weekly remaining: {100 - used:g}%. Other usage limits may still apply.", now)
            record = {"provider": provider, "account_key": account_key, "name": name,
                "used_percent": used, "resets_at": reset, "observed_at": observed,
                "due_enabled": due_enabled}
            if old != record:
                self.state["windows"][identity] = record
                changed = True
        if changed:
            self._save()

    def due(self, now=None, enabled=True):
        if not enabled:
            return
        now = time.time() if now is None else now
        before = len(self.state["events"])
        for identity, window in self.state["windows"].items():
            if self.state["active"].get(window.get("provider")) != window.get("account_key"):
                continue
            reset = timestamp(window.get("resets_at"))
            if not window.get("due_enabled") or reset is None or not 0 <= now - reset <= 3600:
                continue
            self._event(identity, "due", reset,
                        f"{window.get('name', 'Service')}: Reset due",
                        "The scheduled reset time has passed. Recovery will be confirmed when a new usage reading arrives.", now)
        if len(self.state["events"]) != before:
            self._save()

    def pending(self, now=None):
        now = time.time() if now is None else now
        return [e for e in self.state["events"] if not e.get("sent_at")
                and e.get("attempts", 0) < 3 and e.get("next_attempt", 0) <= now
                and now - e.get("created_at", 0) < 3600
                and self._is_active_event(e)]

    def _is_active_event(self, event):
        parts = event.get("identity", "").split("|", 2)
        return len(parts) == 3 and self.state["active"].get(parts[0]) == parts[1]

    def record_delivery(self, event_id, success, now=None):
        now = time.time() if now is None else now
        for event in self.state["events"]:
            if event.get("id") == event_id:
                event["attempts"] = event.get("attempts", 0) + 1
                event["next_attempt"] = now + 300
                if success:
                    event["sent_at"] = now
        self._save()

    def _save(self):
        self.state["events"] = self.state["events"][-200:]
        windows = self.state["windows"]
        if len(windows) > 100:
            self.state["windows"] = dict(sorted(windows.items(), key=lambda item: item[1].get("observed_at", 0))[-100:])
        write_json(self.path, self.state)
