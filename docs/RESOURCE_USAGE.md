# Resource usage and SSD writes

This document describes Quota Dock 1.2.1. Polling and storage behavior are unchanged from 1.2.0. The default polling interval is **60 seconds** for each of Codex and Claude. The on-screen countdown, service usage queries, and file saves are separate operations.

## When files are written

| Operation | Processing and storage |
| --- | --- |
| Screen update every second | Calculates remaining time and observation age from data in memory. A countdown change alone does not save JSON. |
| Codex query | Requests data from the local app-server every 60 seconds by default, reusing the connection. A new valid observation can update alarm records. |
| Automatic Claude query | Runs separate CLI processes for the version check and usage query every 60 seconds by default. Saves the merged result to `claude-direct.json`, including failure results. |
| Alarm evaluation and delivery | Saves `alarms.json` when the account, a valid observation, a scheduled alarm, or a delivery result changes. Repeating the same observation does not save it again. |
| Optional Claude session bridge | Saves the observation cache and session records when status line callbacks arrive. Leaving the bridge connected alongside automatic queries causes additional writes. |
| Settings changes or another app launch | Saves small files such as settings or a window activation request. |

In session mode, the local observation file is read every second. Reads and file existence checks are separate from writes. The app retains the current observation and records needed for alarms, rather than continually appending a complete usage time series.

JSON saving currently **writes the entire file to a temporary file, calls `flush` and `fsync`, then replaces the destination with `os.replace`**. It does not update only the changed field. Alarm records are limited to 100 limit entries and the most recent 200 events, but changing one entry still rewrites the entire JSON file. An unchanged usage percentage can still change a record when a new observation timestamp arrives. See the [storage implementation](../quota_widget/storage.py), [query scheduler](../quota_widget/app.py), and [alarm records](../quota_widget/alarms.py).

## Additional Claude CLI resources

In addition to the widget's small state files, the Claude CLI may write its own caches, metadata, or logs. [`--no-session-persistence`](https://code.claude.com/docs/en/cli-reference) disables session persistence; it does not guarantee that all local file writes are disabled. Usage queries send no user prompts or model turns, but still involve process startup, authentication checks, networking, and local state management.

The following development observations used **native Claude Code 2.1.306 on 2026-10-22**, with isolated temporary settings, a synthetic account and usage cache, and an unreachable proxy. They are not measurements of a real account or a user's SSD.

- On the first query, a metadata file grew from 795 to 1,782 bytes and a 795-byte backup appeared. The **net increase in retained file sizes was 1,782 bytes**.
- Three further queries using the same fresh cache and one failed connection with an old cache showed no additional changes to file contents or sizes. No conversation files or debug logs appeared under these conditions.
- Successful query processes took about 250–288 ms and reached a peak RSS of about 252–254 MiB. The separate version check took about 6–19 ms. These numbers describe temporary execution costs in this synthetic test environment and do not predict another computer's performance.

**Net file growth is not cumulative writes.** It does not capture rewrites of the same files, filesystem metadata and journaling, or storage-device write amplification. Instrumentation restrictions prevented measurement of cumulative host writes and actual SSD NAND writes. Cache updates from real server responses, authentication refreshes, and extended operation were also outside the experiment's scope. The byte counts above must therefore not be extrapolated into daily or annual SSD writes.

## Increasing the interval

Changing each service's interval to **300 seconds** reduces its nominal scheduled query count from 1,440 to 288 per day, an **80% reduction**. This calculation assumes continuous operation for 24 hours and excludes errors, sleep, and manual refreshes. Actual execution also depends on request duration and retry delays.

This can reduce process starts and state saves associated with polling. It does not mean total SSD writes or wear will fall by exactly 80%. Settings saves, alarm delivery, and separate status line callbacks have their own triggers. A longer interval also delays detection of usage changes and confirmed limit recovery.

The current code and isolated experiment cannot determine SSD lifespan or daily NAND writes. If minute-by-minute subscription checks are unnecessary, a 300-second interval reduces query frequency. See [CLAUDE_AUTO_QUERY.md](CLAUDE_AUTO_QUERY.md) for automatic query caching and failure handling.
