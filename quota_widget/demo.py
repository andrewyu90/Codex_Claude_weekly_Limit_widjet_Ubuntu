"""Explicit demonstration data. Never imported as a live provider fallback."""
import time

from .models import snapshot


def demo_snapshots(now=None):
    now = time.time() if now is None else now
    result = []
    for provider, weekly, short, reset in (("codex", 38.0, 21.0, now + 2*86400+3*3600+18*60),
                                           ("claude", 67.5, 44.0, now + 86400+8*3600+42*60)):
        data = snapshot(provider, "ok", "Example data · No real account connected.")
        data.update(account_key="demo-" + provider, received_at=now, observed_at=now,
                    account_label="DEMO · Example account", source_kind="direct")
        if provider == "claude":
            data["source"] = "Claude Code usage query · Example"
        data["windows"] = [
            {"key": provider+":weekly", "label": "Weekly limit", "bucket": provider,
             "kind": "weekly", "used_percent": weekly, "resets_at": reset, "duration_minutes": 10080},
            {"key": provider+":short", "label": "5-hour limit", "bucket": provider,
             "kind": "short", "used_percent": short, "resets_at": now+2*3600+5*60, "duration_minutes": 300},
        ]
        result.append(data)
    return result
