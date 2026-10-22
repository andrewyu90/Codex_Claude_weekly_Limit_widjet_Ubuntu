# UI and observation interface — 1.2.1

The native PySide6 window presents normalized provider observations. Network
access, scheduling, settings, persistence and OS notifications belong to the
application controller in `quota_widget/app.py`. The UI does not read
authentication files or make network requests.

Claude defaults to independent usage queries through the installed Claude CLI.
The application sends stream-json control requests, while the CLI owns OAuth
authentication and usage network access. The optional status-line bridge stays
available as a separate session mode. Neither mode sends a user prompt to obtain
usage data.

## Main window

`quota_widget.ui.MainWindow(demo: bool = False)` starts at **340 × 316 logical
pixels**, which is also the minimum size. Both 104-pixel provider cards, the
controls and the observation footer fit without scrolling. The window can be
enlarged with its lower-right resize grip. The small header supports moving,
pinning, minimizing and closing the window. Closing exits the widget; it does
not depend on a system tray.

Each card shows the weekly remaining percentage, used percentage, a remaining
capacity bar, reset countdown, observation age and a concise state. Claude's
missing-data card offers an actionable connection button. Long explanations,
source information, exact local timestamps and short-window quotas live in
Details or escaped tooltips. The main cards never expand to accommodate a
provider message. Demo mode has an always-visible `DEMO · Example` header badge
and occupies the same dimensions as the regular widget.

| Qt signal | Purpose |
| --- | --- |
| `refresh_requested()` | Request a manual observation refresh |
| `settings_requested()` | Open connection and appearance settings |
| `provider_action(str)` | Handle `codex_dashboard`, `claude_dashboard`, `claude_connect` or `claude_diagnose` |
| `pin_changed(bool)` | Persist the user's always-on-top selection |

| Method | Purpose |
| --- | --- |
| `update_provider(snapshot: dict)` | Update one provider card |
| `set_refreshing(bool)` | Show the manual refresh operation's state |
| `set_refresh_interval(seconds: int, claude_seconds: int \| None = None)` | Display both polling intervals, or show Claude session mode when its interval is `None` |
| `set_pinned(bool)` | Apply always-on-top state and synchronize the pin button |
| `notify(title: str, body: str)` | Briefly replace the footer with a notice title; keep the full text in its tooltip and Details without expanding the window; OS delivery is controlled separately |
| `show_details()` | Open the detail dialog with all quota windows, provider messages and official usage-page actions |

`build_details_dialog()` returns an unshown `QDialog` for local GUI validation.
It is not needed by the controller. The detail table represents observations at
the time the dialog was opened. A weekly-bucket selector in this dialog updates
the main card immediately; that choice survives incoming observations while
the same bucket remains available.

## Provider observations

Normalized observations use the following core fields. Additional bridge
metadata may be present without changing the UI contract.

```json
{
  "provider": "codex",
  "state": "ok",
  "message": "",
  "source": "Codex account API",
  "account_label": "ChatGPT subscription",
  "account_key": "opaque non-secret hash",
  "received_at": 1800000000.0,
  "observed_at": 1800000000.0,
  "windows": [
    {
      "key": "codex:secondary",
      "label": "Weekly limit",
      "bucket": "codex",
      "kind": "weekly",
      "used_percent": 38.0,
      "resets_at": 1800200000.0,
      "duration_minutes": 10080
    }
  ]
}
```

`provider`: `codex` or `claude`. `state`: `ok`, `waiting`, `error`,
`stale`, or `unsupported`. `received_at` and `observed_at` may be null.
Window values are validated; unknown/absent windows are omitted, never 0.
`remaining = 100 - used_percent`. `observed_at` is distinct from a file
heartbeat: repeated Claude callbacks do not make cached data fresh.

For independent Claude queries, a successful control response is validated before
it becomes an observation. The CLI's recent one-minute cache and retry backoff
are respected. A transient failed query retains any previous successful value's
source and observation timestamp; it must not make that value appear fresh.
An account change, missing login or incompatible subscription clears old values
instead of attributing them to the currently selected account.
Manual refresh is a query request, not a cache-bypass guarantee. Source metadata
must continue to distinguish CLI observations from session observations.

The main card emphasizes weekly remaining percentage, used percentage, reset
countdown and observation age. Exact local reset times are available in a
tooltip and Details. Missing percentages display an em dash and an unfilled
bar, not zero or a full allowance. Past weekly reset timestamps leave the last
percentage explicitly historical and awaiting a new observation. An expired
short-window quota has no available remaining percentage in Details. The UI
never refills quota simply because a clock reaches the reset time.

Multiple Codex weekly buckets remain separate and can be selected in Details.
The main card indicates the selected bucket's index when more than one exists;
its tooltip identifies that bucket. A Codex
window is weekly when its reported duration is 10,080 minutes; its primary or
secondary position does not determine this classification. In optional Claude
session mode, the bridge uses the latest observation from a session with valid
weekly information. It does not aggregate sessions or provide complete account
identity across them. Independent CLI observations and session observations are
not summed into a combined allowance.
All labels containing provider-supplied text use Qt plain-text mode. Tooltip
content is escaped before presentation; table and combo-box items remain plain
text. Demo mode is visibly labelled and uses example values.

## Claude scheduling and settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `claude_auto_query` | `true` | Enable independent CLI usage polling |
| `claude_binary` | `claude` | CLI command or configured executable path |
| `claude_poll_seconds` | `60` | Normal polling interval, clamped to 60–900 seconds |

The controller requests a lookup at startup and then follows the configured
interval. Failures and server/CLI backoff can delay later attempts. Turning off
automatic queries stops that periodic path and allows the optional session mode
to display observations. Installing or removing the status-line bridge is an
explicit, separate operation.

The minimum CLI version is 2.1.306. The experimental control sequence is
`initialize` followed by `get_usage` with `skip_behaviors: true`; user messages
and model turns are outside this protocol. Unsupported requests and malformed
responses fail safely. A newer version number alone is not a promise that this
experimental interface remains compatible.

## Diagnostic boundaries

The GUI diagnosis dialog reads local configuration and the controller's latest
in-memory result. Opening it does not launch an extra usage request. By
contrast, `--check-claude` follows `claude_auto_query`: it attempts an independent
Claude lookup when enabled and otherwise diagnoses the session bridge and its
observations. `--check` runs the same Claude diagnosis alongside a Codex lookup.
These commands may cause the CLIs to use the network without starting model work.

`--help`, `--version` and `--check-gui` remain usable with the standard library
before Qt initialization. The last checks local display and XKB configuration
without connecting to a provider. Diagnostics must not include credentials,
cookies, raw authentication responses or email addresses.
