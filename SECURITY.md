# Security and privacy

## Data boundaries

Quota Dock asks the installed Codex and Claude CLIs for subscription usage. The widget does not read separate OAuth-token files or browser cookies, and does not collect account passwords. The CLIs handle authentication refresh and service-network requests.

To match the active Claude account and validate cache freshness, the widget reads selected account metadata and usage-cache fields from `.claude.json`. Persisted observations are reduced to the relevant limits, timestamps and hashed identifiers. The widget does not copy raw authentication files or complete CLI output into its state files. Usage and observation records can still reveal activity and need not be published.

The widget creates its state directory and JSON files with modes 0700 and 0600 respectively. These permissions do not isolate it from other programs running as the same user. Each CLI's own logs, caches and policies are separate.

The optional Claude session bridge changes the user's `statusLine` command. It can run a saved previous command to preserve its output, so use trusted executables and your own Claude configuration. Keep the existing ownership, symlink and restoration-conflict checks intact.

## Files that do not belong in a public repository

- Real `.claude/`, `.claude.json`, `.codex/`, authentication JSON, tokens, cookies or private keys.
- Personal widget settings, usage caches, notification history or logs.
- Screenshots or raw diagnostic output containing account information.

The `.gitignore` reduces accidental commits of common local files. It is not a complete secret detector. Review `git diff --cached` before uploading, and use synthetic test fixtures.

## Reporting a vulnerability

Do not publish sensitive reproduction material in an Issue when reporting credential exposure, arbitrary command execution or bypasses of path/ownership checks. If the repository enables GitHub **Private vulnerability reporting**, use **Security → Report a vulnerability**. Otherwise, use the maintainer's published contact channel to establish a private reporting route first.

This project template does not invent a maintainer email address or response deadline. The repository owner can add their actual contact details and support policy when publishing it. Ordinary usage errors can be reported in Issues after removing personal information.

Assess security-related changes against the scope and limitations in the [validation record](docs/VALIDATION.md). If Claude's experimental control interface changes, check that the app fails safely. The widget does not establish the trustworthiness of an old or arbitrary CLI executable.
