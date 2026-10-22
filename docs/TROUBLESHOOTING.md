# Troubleshooting

Start with **Settings → Connection diagnostics → Copy diagnostics**. This dialog shows local settings and the latest query state held in memory; opening it does not send an additional server request. Claude uses independent automatic queries by default. The earlier status-line bridge remains available as an optional session mode.

The terminal examples below use the source-install path. If you installed the `.deb`, use **`/usr/bin/quota-widget`** instead of `"$HOME/.local/bin/quota-widget"`. If both installations are present, make sure you are diagnosing the new version.

To check the Codex connection and the current Claude mode without opening the GUI:

```bash
"$HOME/.local/bin/quota-widget" --check
```

Use `--check-claude` to check only Claude. Both commands attempt an independent usage query when automatic Claude queries are enabled, or diagnose the session bridge and its observations when they are disabled. The CLIs may make network requests, but the commands do not send prompts or start model work. This differs from the GUI's local diagnostics.

If an XKB or display error occurs before a window appears, run the local GUI-environment diagnostic first. It does not create a GUI or connect to service accounts.

```bash
"$HOME/.local/bin/quota-widget" --check-gui
```

## Installation problems

### Opening the `.deb` launches an archive manager

Use **Open With** to select a software installer that supports `.deb` packages. Available installers vary by Ubuntu environment. **Quota Dock 1.2.1** in the application menu is the new package's initial launcher. Close the previous widget, then open this entry or the existing **Quota Dock** entry.

### The old installation still displays Korean

Close the running widget, install the **1.2.1** package, and reopen it from the application menu. Version 1.2.1 lets the launcher recognize that the existing 1.2.0 runtime needs an update. The running process must be restarted to use the English interface.

If a previous source installation is also present, its shortcut can hide the new package's entry or open the old version. In GNOME, press **Alt+F2** and run `/usr/bin/quota-widget` once. This launches the package's setup or upgrade path and updates the desktop and autostart entries the app owns. `/usr/bin/quota-widget --version` reports the version of the bundled package; it does not prepare or verify the per-user runtime. Launch the app normally to complete that update.

### First-launch setup fails

The setup window downloads a PySide6 binary package compatible with the computer. Check internet access, certificates, proxy settings, available disk space, and PySide6 support for the CPU, then choose **Retry**. Canceling also stops the setup subprocess. The per-user runtime is preserved so the next launch can retry preparation. This process does not invoke Claude or Codex models.

### `unzip: command not found`

Install the extraction tool and extract the downloaded archive again.

```bash
sudo apt install unzip
```

### `No module named venv` or `ensurepip is not available`

Install Ubuntu's virtual-environment package.

```bash
sudo apt install python3-venv
bash install.sh
```

If you installed Python separately, check that the matching Python version has a working venv package or equivalent virtual-environment support.

### PySide6 download fails

Installation requires access to the Python package index. Check network, proxy, and certificate settings. You do not need `sudo pip` or `--break-system-packages` with the system Python.

### Ownership of an existing file or directory cannot be verified

The installer stopped to avoid overwriting an unrelated file with the same name. Check the path in the error. Do not delete user files indiscriminately or fabricate an installation marker. Preserve any files you need separately, then use the correct installation path.

## The window does not open

### `(python): Gdk-ERROR: Failed to create XKB context`

This message means GUI initialization could not create an XKB keyboard context. Possible causes include a missing XKB data package, incorrect environment variables, or a mismatch between the path expected by the runtime and the installed data location. **The message alone does not identify one specific cause. The reported error has not been reproduced in the user's Ubuntu environment.**

Since 1.0.1, the app searches for a valid XKB path before creating `QApplication`; version 1.2.1 retains this correction. A valid existing `XKB_CONFIG_ROOT` takes priority, and `QT_XKB_CONFIG_ROOT` is supplemented as needed. The search checks `/usr/share/xkeyboard-config-<VERSION>` and `/usr/share/X11/xkb`. Actual locations can differ between Ubuntu releases and packages.

The correction applies **only to the widget process's environment**. It does not require editing shell configuration files or changing system keyboard-data directories or symbolic links.

If no platform theme was explicitly selected, the app uses Qt's `generic` theme to avoid GTK theme initialization. An explicit `QT_QPA_PLATFORMTHEME` value and the X11/Wayland selection are preserved. The temporary command below sets `generic` only for that launch so you can compare the result.

#### Launch temporarily with the installed data path

This command finds `rules/evdev` in the `xkb-data` package's file list, derives the data root, and checks that the required files and directories exist. It sets both environment variables and launches the widget only if it finds a valid path.

```bash
quota_xkb_root="$(
  dpkg-query -L xkb-data 2>/dev/null |
  while IFS= read -r quota_xkb_path; do
    case "$quota_xkb_path" in
      */rules/evdev)
        quota_xkb_candidate="${quota_xkb_path%/rules/evdev}"
        if [ -r "$quota_xkb_candidate/rules/evdev" ] &&
           [ -d "$quota_xkb_candidate/keycodes" ] &&
           [ -d "$quota_xkb_candidate/symbols" ] &&
           [ -d "$quota_xkb_candidate/types" ] &&
           [ -d "$quota_xkb_candidate/compat" ]; then
          printf '%s\n' "$quota_xkb_candidate"
          break
        fi
        ;;
    esac
  done
)"

if [ -n "$quota_xkb_root" ]; then
  env XKB_CONFIG_ROOT="$quota_xkb_root" \
      QT_XKB_CONFIG_ROOT="$quota_xkb_root" \
      QT_QPA_PLATFORMTHEME=generic \
      "$HOME/.local/bin/quota-widget"
else
  printf '%s\n' 'No valid XKB data path was found. See the package reinstall instructions below.'
fi
```

If no valid path is found, reinstall the required data package, then repeat the path check and launch command.

```bash
sudo apt install --reinstall xkb-data
```

If the error continues, check the output of `quota-widget --check-gui`, the Ubuntu version, whether the session uses X11 or Wayland, and the complete error message. Reinstalling packages or setting an environment variable does not guarantee that every XKB error will be resolved.

### Qt `xcb` plugin error

Install the required Ubuntu runtime libraries, then try again.

```bash
sudo apt install xkb-data libxkbcommon0 libxcb-cursor0 libegl1 libgl1 libxkbcommon-x11-0
"$HOME/.local/bin/quota-widget"
```

If the error names another missing library, check that library too. Launch the app from a terminal in a graphical login session. An SSH session without a display cannot show the window.

### Korean or other CJK characters appear as squares or blanks

The system may lack a font that provides the required glyphs. Install the following package, close the widget, and reopen it.

```bash
sudo apt install fonts-noto-cjk
```

If the problem persists, use `fc-match sans:lang=ko` to check the font selected for Korean text. Usage queries and font rendering are separate concerns. The app interface in 1.2.1 is English; this information also applies to older versions or non-Latin text supplied by the environment.

### Pinning or moving the window behaves differently on Wayland

Always-on-top behavior and window placement depend on compositor policies, independently of usage queries. You can use the desktop's window menu or select an X11 session at login to compare behavior. The app does not require a GNOME Shell extension.

## Codex data is missing

### `codex` cannot be found when launched from the application menu

PATH changes made by terminal startup scripts may not apply to the application menu, especially with installations managed by tools such as nvm.

```bash
command -v codex
```

Enter the resulting absolute path in the widget's Codex executable setting. Check that both the executable and any Node.js environment it requires are accessible from the graphical session.

### Login is required

```bash
codex login
```

The terminal login and widget must run as the same user. Do not run the widget with `sudo`. If you use a custom `CODEX_HOME`, the widget needs the same environment.

### You use an API key or a local model

The widget displays Codex quota windows associated with a ChatGPT subscription. It does not convert API charges, API organization limits, or local Ollama usage into a remaining weekly subscription allowance. Check the login type.

### No weekly quota is available

The quota durations returned for the account do not identify a weekly window. This does not mean usage is zero. Check the Codex version, account plan, and official usage page.

### Errors after a Codex upgrade

The app uses the account and usage read methods described in the official App Server documentation. If an upgrade causes an error, check the message and `codex --version`, then compare the response with the [current protocol](https://learn.chatgpt.com/docs/app-server). Unknown responses produce an error or waiting state instead of an estimated usage value.

## Automatic Claude queries are not working

In **Settings**, confirm that **Automatically check Claude usage** is enabled, then open **Connection diagnostics**. Automatic queries require **Claude Code 2.1.306 or later** and a subscription login in that CLI. An active conversation terminal and the status-line bridge are not required.

| State to check | Meaning and next step |
| --- | --- |
| CLI not found | Set the absolute path of the Claude executable. |
| Outdated CLI or unsupported control request | Check and update the CLI version. A future protocol change can make a version unsupported even when it meets the minimum version requirement. |
| Login required | Check the subscription login using the same user and configuration path as the widget. |
| Usage or weekly quota missing | Check whether the login type and subscription account provide this data. Missing information does not mean 0% used. |
| Error or retry delay | Check the internet connection and CLI network settings, then wait for the next attempt. If an earlier value is displayed, check its source and observation time. |
| Scheduled reset time has passed | Wait for a new usage observation. The widget does not change the value to 100% simply because the scheduled time has passed. |

With automatic queries enabled, run this command to check an independent query:

```bash
"$HOME/.local/bin/quota-widget" --check-claude
```

The widget sends only `initialize` and `get_usage(skip_behaviors: true)` to the CLI. **It does not send a test prompt.** You do not need to send an arbitrary Claude message to verify usage queries. The app also does not read authentication files or cookies directly, or send OAuth requests through its own HTTP client.

### Claude still shows a waiting state after updating from 1.1.0

Updating the widget and updating the Claude CLI are separate operations. Check the CLI version first.

```bash
claude --version
```

For a native installation older than 2.1.306, update it with the following command. If a package manager installed the CLI, use that installation method's update procedure.

```bash
claude update
```

A valid existing login does not need to be repeated. Only if the widget requests a login, run the following as the same user, then refresh the widget.

```bash
claude auth login
```

See Claude's [official CLI documentation](https://code.claude.com/docs/en/cli-reference) and [update instructions](https://code.claude.com/docs/en/setup). The widget does not install or update the CLI in the background or perform the login procedure for you.

### `claude` cannot be found when launched from the application icon

The terminal and application menu may have different PATH values.

```bash
command -v claude
```

Enter the resulting path in the **Claude executable** setting. Check that it does not point to a CLI logged in under a different user or `CLAUDE_CONFIG_DIR`. Do not run the widget with `sudo`.

### Refresh leaves the numbers unchanged

In automatic mode, the widget requests usage from the CLI while respecting its recent **one-minute cache** and retry delays. The numbers may remain unchanged if usage has not changed or the CLI returns a cached response. Refresh does not clear the cache or bypass server limits.

Network failures and server-requested waits increase the retry interval. The last successful value retains its source and observation time; a failed attempt does not become a fresh successful observation. A 60-second setting does not guarantee that new data arrives within 60 seconds in every situation.

### Automatic queries fail after a CLI update

`get_usage` is an **experimental stream-json control request** verified against Claude Code 2.1.306. It is not a stable public API contract. If a new version changes the request or response, the widget retains an error or unsupported state. Check the CLI version and widget diagnostics. The widget does not present invalid values as normal data or substitute a model prompt for the usage query.

You can disable automatic queries and use the session mode below if needed. The two modes have different update conditions.

## Optional Claude session-mode problems

This section applies when automatic queries are disabled and you use the status-line bridge. **Registering a bridge and receiving a real callback are separate steps.**

| Session diagnostic state | Meaning and next step |
| --- | --- |
| `not_connected` | Install the bridge with **Connect session** in Settings or `--install-claude`. |
| `callback_pending` | Registration was found, but no callback has arrived. Check that Claude Code has applied the setting. |
| `weekly_missing` | A callback arrived without weekly fields. Check the subscription, login type, and whether normal work has produced the first successful response. |
| `path_mismatch` | The widget and callback use different data or configuration locations. Check the environment used for the initial connection. |
| `weekly_expired` | The scheduled reset time has passed. A new observation is needed. |
| `stale` | The displayed value is the last observation. Rerunning the status line alone does not advance its observation time. |

### The bridge is installed but the widget keeps waiting

Check the following:

1. Does `claude` run correctly as the same user?
2. Is it logged in to a supported Pro/Max subscription?
3. Has the current Claude session received its first successful API response?
4. Do you use `CLAUDE_CONFIG_DIR`, and was the bridge installed in the same location?
5. Do project-level or managed Claude settings override the user's `statusLine`?

The normal connection command is:

```bash
"$HOME/.local/bin/quota-widget" --install-claude
```

Restart Claude Code or confirm that it has applied the setting. An older Claude version without the required usage fields may also need a normal CLI update.

### Refresh leaves the numbers unchanged in session mode

When automatic queries are disabled, Refresh rereads locally delivered Claude session data. `statusLine.refreshInterval` can also deliver the same session information again. For an independent check, enable automatic queries or consult Claude's official usage page or `/usage`.

### Which Claude session supplies the displayed value?

The widget displays the latest observation from a session with valid weekly information. An older repeated callback from another session, or a new session without weekly information, does not immediately replace a valid observation. Session values are not added together, and the bridge does not provide complete identification or consolidation of multiple Claude accounts. If you use several accounts at once, compare against each account's official usage page.

### The reset time passed, but the value did not become 100%

This is expected. A scheduled time alone cannot confirm recovery; the widget waits for a new usage observation. If Claude removes an expired quota window from its data, the widget does not reinterpret that as 100% remaining.

### The original Claude status line becomes slow

Check whether the original status-line command itself takes a long time. The bridge forwards its input and preserves its output. If that command fails or exceeds its timeout, the displayed status line may differ. You can disconnect the bridge to compare behavior.

```bash
"$HOME/.local/bin/quota-widget" --uninstall-claude
```

### Disconnecting reports a settings conflict

You or another tool may have changed `statusLine` after installing the bridge. Restoration stops to avoid overwriting that change. Compare the backup and current settings named in the error, then choose the status line you want to keep. Resolve restoration before removing the app, because uninstalling first may remove the command referenced by the existing connection.

Disconnecting the session bridge does not necessarily stop independent automatic queries. To stop all recurring Claude queries, disable **Automatically check Claude usage** or close the widget.

## Notifications and startup at login

### Notifications do not appear

Check Ubuntu's notification permissions for the app and whether Do Not Disturb is enabled. For desktop notifications, install the package that provides `notify-send`.

```bash
sudo apt install libnotify-bin
```

If desktop notifications are unavailable, check the notice shown inside the widget. In Settings, scheduled-reset notifications and confirmed-recovery notifications have separate controls.

### The widget does not start after login

Open the new app once, enable **Settings → Start at login**, and choose **Save**. If you disabled startup or deleted its entry, reopening the app does not override that choice. Demo and diagnostic runs do not register startup at login.

```bash
"$HOME/.local/bin/quota-widget" --autostart on
```

Check `~/.config/autostart/quota-widget.desktop` or the equivalent path under `XDG_CONFIG_HOME`. Moving the virtual environment or installation directory may have changed the executable path. Some desktops apply separate startup-application settings.

## Useful information when requesting support

- Ubuntu version and whether the session uses X11 or Wayland.
- `python3 --version`, `codex --version`, and `claude --version`.
- The output of **Connection diagnostics → Copy diagnostics**, and the active automatic-query or session mode.
- For usage-query problems, the output of `quota-widget --check-claude` or `quota-widget --check`.
- For GUI startup problems, the output of `quota-widget --check-gui` and the terminal error.
- Whether you use custom `CODEX_HOME`, `CLAUDE_CONFIG_DIR`, or XDG paths.

There is no need to share login tokens, cookies, complete authentication files, prompts, or conversation content.
