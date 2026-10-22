# Independent Claude usage queries

## Default behavior in 1.2.1

Quota Dock checks subscription usage through a separate process of the installed Claude Code CLI. Automatic queries are enabled by default at a 60-second interval, adjustable from 60 to 900 seconds in Settings. An active Claude conversation or status line callback is not required. This feature was introduced in 1.2.0; the English translation in 1.2.1 does not change its query behavior.

The settings are `claude_auto_query`, `claude_binary`, and `claude_poll_seconds`. The CLI handles authentication. The widget does not directly read Claude credential files, OAuth tokens, or browser cookies. This feature adds no required Python dependency.

## Scope of the control requests

The tested native Claude Code version is **2.1.306**, which is also the minimum supported version. Compatibility with future versions is not guaranteed.

1. Start a stream-json process in safe mode.
2. Send an `initialize` control request to prepare the connection.
3. Send a `get_usage` control request with `skip_behaviors: true`.
4. Validate the usage and reset fields, then normalize them into the widget's observation format.

No user message, prompt, or model turn is sent. The signed-in Claude CLI performs the usage request; the widget does not reimplement authentication requests through its own HTTP client. A failed query is not replaced with a model call.

To check account consistency and the actual cache observation time, the widget reads account and usage-cache metadata from the CLI's `.claude.json`. It uses only the required fields and discards unrelated settings. It does not read token storage files or browser cookies. Account identifiers are stored as hashes in widget state, and email addresses are excluded from diagnostic output. The CLI continues to handle authentication.

`get_usage` is an **experimental CLI control interface**. The official TypeScript Agent SDK's [0.3.169 changelog](https://github.com/anthropics/claude-agent-sdk-typescript/blob/main/CHANGELOG.md#03169) introduces the related method as `usage_EXPERIMENTAL_MAY_CHANGE_DO_NOT_RELY_ON_THIS_API_YET()`. The public CLI update, sign-in, and status line documentation does not establish a stable API contract for this request. If the response changes or the feature disappears, the widget reports an error or an unsupported state.

## Caches, failures, and observation times

The app requests usage at startup and then at the configured interval. It respects the CLI's recent one-minute cache and retry delays. Manual refresh does not force the cache to be cleared.

The official Claude Code changelog describes [shared usage queries from the last minute in 2.1.275](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md#21275) and [retry delays after usage request limits or rejected sign-ins in 2.1.284](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md#21284). Sending a new widget request does not, by itself, establish a new server observation.

An unchanged usage value and a failed request are handled separately. After a temporary query failure, the widget retains the last successful numbers, source, and observation time, and increases the retry interval. It clears the numbers when a sign-out or account change means they cannot be shown as usage for the current account. Passing the displayed reset time alone does not confirm a reset or change the remaining amount to 100%. Missing limits are not treated as 0% used.

A 60-second interval does not guarantee a persistent server connection or a new server response every minute. Sleep, loss of internet access, CLI caching, authentication state, server responses, and retry delays can postpone updates.

## Cost and local resources

Usage checks do not create model tasks, so they do not generate model tokens by repeatedly asking a model about usage. The query CLI can still use CPU and memory, make authentication and usage network requests, and write cache or metadata files. Service query limits still apply.

`--no-session-persistence` disables conversation session persistence; it does not eliminate all disk writes. The default 60-second interval can be increased in Settings. See [Resource usage and SSD writes](RESOURCE_USAGE.md) for the file-saving behavior and the limits of the isolated experiment.

## Optional session mode

The existing `statusLine` bridge remains available. With automatic queries disabled and the bridge connected, the widget can display observations supplied by a Claude session. The bridge is not installed automatically, and existing user status line settings are not automatically replaced.

In this mode, subscription usage fields are supplied after the first successful API response for supported accounts. Periodic callbacks from an idle session do not force a server usage refresh. Values from multiple sessions are not added together. The bridge selects the most recent observation among sessions with valid weekly information.

Removing the bridge and disabling automatic queries are separate actions. Automatic queries work without removing an existing 1.1.0 bridge. An unnecessary session connection can be removed in Settings, restoring the previous status line.

## Diagnostic commands

| Entry point | Behavior |
| --- | --- |
| Connection diagnostics in the GUI | Checks local settings and the most recent query state held in memory. Opening the dialog does not trigger an extra request. |
| `--check-claude` | Attempts an independent usage query when automatic queries are enabled; otherwise diagnoses the session bridge and its observations. |
| `--check` | Queries Codex usage and runs the Claude diagnosis for the selected mode, as above. |
| `--check-gui` | Checks the XKB and display environment only. Does not start the GUI or connect to service accounts. |
| `--help`, `--version` | Prints help or the app version without querying services. |

Usage diagnostic commands can cause network requests through the CLI, but do not create model tasks. Diagnostic output excludes tokens, cookies, and email addresses.

## What was verified, and what remains to be tested

The actual native CLI 2.1.306 was run with isolated local settings. A signed-out environment returned `waiting` / `login_required`; a synthetic fresh cache returned `ok` with the prepared value of 67.5%; and a one-hour-old cache with a failed connection returned `error` / `usage_unavailable`. Merging the failure with a previous valid observation preserved the earlier numbers and observation time.

These checks used a local synthetic cache and an unauthenticated environment. They did not retrieve server usage for a real signed-in account. The original 1.2.0 release passed all 128 regression tests. See [VALIDATION.md](VALIDATION.md) for detailed results and the current distribution validation status.

Real accounts, an Ubuntu desktop, login autostart, extended operation, and behavior across an actual reset still require testing on a real device.

## Public references

- [Claude Code CLI: update, sign-in, and execution options](https://code.claude.com/docs/en/cli-reference)
- [Claude Code installation and updates](https://code.claude.com/docs/en/setup)
- [Claude Code status line: session-mode data](https://code.claude.com/docs/en/statusline)
- [Claude Agent SDK TypeScript changelog: experimental usage interface](https://github.com/anthropics/claude-agent-sdk-typescript/blob/main/CHANGELOG.md)
- [Claude Code changelog: caching and retry policies](https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md)

These references document public CLI behavior, the session bridge, and the reviewed release changes. They do not guarantee long-term support for the experimental `get_usage` control request.
