# Validation results and limitations

## 1.2.1 English interface and upgrade compatibility — 2026-10-22

This section records the checks performed after translating the application, installers, documentation and repository templates into English.

| Item | Result |
| --- | --- |
| Complete test suite | **136 tests** under Python **3.12.14**, PySide6/Qt **6.12.0**, and `offscreen`; **0 failures, 0 errors, 0 skips**. `Ran 136 tests in 5.899s`, `OK` |
| Cached Claude readings | Legacy display text becomes English while usage values, account keys, state, error codes, reset times and observation times are retained. Loading does not change cache bytes, inode or modification time. The four supported English plan labels are preserved. |
| Session observation fingerprints | An older fingerprint containing translated labels retains its original observation time when the usage and response counters are unchanged. Real counter, usage and reset-time changes still produce a new observation. |
| Pending notifications | English text retains the original recovery percentage, event identity, timestamps, delivery status and retry schedule. Loading does not write the alarm file or create events. Ambiguous historical percentages use a generic recovery message. |
| English text review | No Hangul found in **58 text files**, including decoded Python string literals. The demo preview is the remaining non-text project file. |
| Python 3.10 syntax | Parsed **28 Python files** with `ast.parse(..., feature_version=(3, 10))`. This is a syntax check, not execution under Python 3.10. |
| Shell and whitespace checks | Both installation scripts pass `bash -n`; `git diff --check` passes. |
| Visual review | Rendered and inspected the English main window, Settings, Details, error state and connection diagnostics using actual Qt with synthetic data. The main window remains **340 × 316** logical pixels; the Settings view is **510 × 432**. |

The seven added regression tests cover the language upgrade of cached readings, legacy session fingerprints, supported plan labels and saved notification text. The existing provider, controller, installation, startup and interface tests also pass. These checks use synthetic data and mock CLIs; they do not query real subscription accounts.

Version **1.2.1** gives the Debian launcher a new application version to install over a 1.2.0 runtime. Packaging and release checks can be reproduced with `make artifacts` as documented in [RELEASING.md](RELEASING.md). The historical results below describe their stated versions and are not additional 1.2.1 test runs.

## GitHub project preparation — 2026-10-22

This section records the checks performed when the GitHub source project was prepared from the existing `quota-widget-ubuntu-1.2.0.zip` release, before the English-language update. It is separate from the earlier release records below. At this stage, the app version remained **1.2.0** and the runtime implementation in `quota_widget/` matched the original ZIP.

| Item | Result recorded at this stage |
| --- | --- |
| Complete test suite | **129 tests** under Python **3.12.14**, PySide6/Qt **6.12.0**, and `offscreen`; 0 failures, 0 errors, 0 skips. `Ran 129 tests in 5.850s`, `OK` |
| Installer regression | Confirmed use of the actual package icon instead of the root `assets/` directory absent from the original ZIP. The same test checked that contributor, security, changelog, and development files were retained after installation. |
| Python 3.10 syntax | Checked **28 files**—15 app modules, 3 packaging modules, and 10 test files—with `ast.parse(..., feature_version=(3, 10))`. This was not execution under Python 3.10. |
| Package builds | Created the wheel, sdist, and `.deb`, then **rebuilt the wheel and `.deb` using only the sdist**. Verified app-source equality and the `.deb`'s wheel, hashes, documents, executable permissions, and absence of maintainer scripts. |
| Workflow configuration | Passed `actionlint` 1.7.12, YAML parsing, and shell syntax checks. Actions were pinned to full commit SHAs from official releases. |
| Public-file review | Found no real tokens, personal account data, or candidate personal absolute paths. Test emails used the synthetic `example.invalid` domain. Demo images contained no account data. |
| Remote operations | Did not create a GitHub repository, push code, run Actions remotely, or publish a Release. |

The full test run imported Qt first and confirmed zero skipped tests. After the installer documentation-copy list was completed, the single installer test and shell syntax checks passed again. No actual service CLI, user login, or model request was used in these checks.

GitHub Actions was configured to run the same tests and wheel, sdist, and `.deb` builds and verification on Ubuntu 22.04/Python 3.10 and Ubuntu 24.04/Python 3.12. **Reviewing workflow files and passing local checks does not establish that remote CI has passed.** Both runner results must be checked after uploading the repository. Build and release commands are documented in [RELEASING.md](RELEASING.md).

## Earlier release validation records

The records below are dated **2026-10-21**. They include separate native-CLI, packaging, and upgrade experiments performed at that time. They are not all automated tests reproducible through the current `unittest discover` command.

## 1.2.0 independent automatic Claude queries

Version 1.2.0 introduced independent queries through the installed Claude CLI as the default. It controls the process using the Python standard library, without adding a required Python dependency. The earlier status-line bridge remains available as an optional session mode.

| Check | Recorded result |
| --- | --- |
| Actual native Claude CLI | Used **2.1.306** |
| Isolated logged-out environment | Correctly produced `waiting` / `login_required` |
| Synthetic recent CLI cache | The actual CLI's safe-mode `initialize` and `get_usage(skip_behaviors: true)` path returned `ok` and the prepared **67.5%** value |
| One-hour-old cache with connection failure | Produced `error` / `usage_unavailable`; merging with an earlier successful observation preserved its value and observation time |
| Model work | Used control requests without sending a user prompt or conversation turn to obtain usage |
| Authentication owner | The Claude CLI handled OAuth and usage network requests. The app did not read authentication files or browser cookies directly. |
| Complete 1.2.0 regression suite | **All 128 tests passed** — `Ran 128 tests in 5.708s`, `OK` |
| Python 3.10 syntax compatibility | AST syntax checks passed for 17 app and packaging Python modules. This did not replace execution under Python 3.10. |
| Rendering | Rendered and visually inspected the **340×316** main window and **500×440** settings window using Python 3.12.14 and Qt/PySide6 6.12.0 with the offscreen platform |
| Final wheel integrity | All **15** app Python modules in the wheel matched the final source byte for byte |
| Installed-app upgrade | Successfully upgraded the existing QA virtual environment from **1.1.0 to 1.2.0** |
| Installed-app execution | Confirmed version **1.2.0** and opened a **340×316** Qt GUI from outside the source directory |
| CLI diagnostics without Qt | Loaded only the wheel in the system Python and successfully ran `--check` without importing PySide6 |
| Actual subscription accounts and the user's Ubuntu desktop | Not tested |

These checks provided isolated local configuration and synthetic cache data to the actual native CLI. They **did not demonstrate a successful server usage query from a real logged-in account**. No authentication tokens or real subscription accounts were used. They established logged-out behavior and preservation of cached values and timestamps within that test scope. Future compatibility of the experimental `get_usage` request, server responses and retry delays for an authenticated account, long-running operation, and behavior across an actual reset require further validation.

The wheel's Qt-free diagnostic check combined a successful mock Codex result with an isolated logged-out result from the actual native Claude 2.1.306 CLI. Claude was correctly classified as `login_required`, and the output contained no tokens or email addresses. The diagnostic flow was also checked for the absence of user prompts and model requests. This does not replace system-package installation or execution on the user's Ubuntu desktop.

The `docs/preview.png` used at this stage showed the 1.2.0 demo with example usage data. The 84-, 58-, and 51-test results below belong to earlier versions and should not be treated as the final 1.2.0 regression result.

## Earlier 1.1.0 compact UI, application launch, and Claude diagnostics

**All 84 automated app tests passed.** They ran under Python 3.12.14 and PySide6 6.12.0 with the offscreen platform. No real subscription accounts were used.

| Test file | Count | Main coverage |
| --- | ---: | --- |
| `test_claude_bridge.py` | 25 | The original 21 bridge tests and 4 path-normalization tests |
| `test_claude_diagnostics.py` | 14 | Connection stages, missing or expired data, path errors, read-only GUI diagnostics, and safe version queries |
| `test_core.py` | 24 | Usage, alarms, storage, and a mock Codex read protocol |
| `test_desktop.py` | 6 | Application-menu registration, startup defaults, preservation of opt-out and deletion choices, unrelated-file preservation, and legacy menu entries |
| `test_gui.py` | 8 | The 340×316 layout, fixed size with long messages, unknown and expired data, multiple quotas, and demo labels |
| `test_startup.py` | 7 | Existing XKB corrections and diagnostics |
| **Total** | **84** | **All passed** |

A separate integration check ran two actual Python processes and confirmed that a second launch requested the first window to appear, then exited immediately. Codex used only an offline mock provider. Creation of the application-menu and startup entries on first launch was also checked. Interprocess activation uses a small file in the private state directory, with a new identifier for each process to avoid processing stale requests after a restart.

The 340×316 main window, disconnected and expired states, Details, Settings, and Claude connection diagnostics were rendered using actual Qt. Demo and diagnostic runs did not access real accounts or send model requests. Values in the images were examples.

Creating or disabling startup files and testing two processes **does not replace an actual logout/login test on the user's Ubuntu system**. Bringing a window forward and keeping it on top still require validation against the desktop's X11 or Wayland window-management policy.

The `.deb` preparation program and packaging passed 11 separate checks: ownership and symbolic-link protection, distribution-file hash verification, diagnostics without a GUI, XDG path handling, retry after partial preparation, app-only upgrades, skipped reinstallation when already current, error messages without sensitive data, and subprocess termination on cancellation. A temporary virtual environment shared the existing QA Qt installation, installed the actual 1.1.0 wheel, and verified its version and runtime-reuse path. The `.deb` had no maintainer scripts that changed user home directories.

Python 3.10 syntax compatibility was checked for 15 app and packaging Python modules. **The actual Tk setup window, initial Qt download over the internet, system-package installation, and physical desktops for each Ubuntu release were not tested.**

## Earlier 1.0.0 validation environment and results

| Item | Environment |
| --- | --- |
| Python | 3.12.14 |
| Qt / PySide6 | 6.12.0 |
| GUI rendering | Qt `offscreen` platform |
| Service integration | Synthetic usage data and a local fake Codex App Server |
| Authentication data | No real accounts, tokens, or cookies used |

**All 51 automated tests for 1.0.0 passed.** The final result at that time was `Ran 51 tests in 3.478s` and `OK`. This validated local behavior and the boundary conditions below; it did not establish successful service-account integration or operation on a physical Ubuntu desktop. These results are separate from the 1.0.1 XKB checks.

| Test file | Count | Main coverage |
| --- | ---: | --- |
| `tests/test_claude_bridge.py` | 21 | Usage-field parsing, repeated callbacks versus observation time, latest valid weekly observations across sessions, missing/expired/invalid values, settings preservation and restoration, existing status-line output, and subprocess timeout cleanup |
| `tests/test_core.py` | 24 | Weekly-duration identification, changed slot order, separate quota buckets, stale observations, confirmed-recovery versus scheduled-reset alerts, restart deduplication, suppressed alerts after account changes, read-only Codex protocol, process shutdown, and atomic storage |
| `tests/test_gui.py` | 6 | Unknown values, selecting multiple quota entries, expired weekly and short windows, demo labels, and refresh-interval display |
| **Total** | **51** | **All passed** |

## Additional 1.0.1 XKB validation

Version 1.0.1 added XKB data-path discovery and environment correction before GUI creation, a default platform theme, and `--check-gui` diagnostics that do not create a GUI or connect to service accounts. The existing 51 tests and 7 new XKB tests ran together: **all 58 passed** (`Ran 58 tests in 3.615s`, `OK`).

| Check | Recorded result |
| --- | --- |
| Preserve a valid existing `XKB_CONFIG_ROOT`, explicit Qt theme, and Wayland selection | Regression tests passed |
| Search versioned data directories and `/usr/share/X11/xkb` | Checked with temporary data roots and the actual system root |
| Invalid or missing data paths | Correction, installation guidance, and headless exceptions passed tests |
| Supplement `QT_XKB_CONFIG_ROOT` only within the widget process | Regression tests passed; system files were not changed |
| Run `--check-gui` without a GUI or account connection | Confirmed in a new process that Qt and the Codex provider were not imported |
| Actual libxkbcommon context creation | Reproduced failure with an invalid path, then successfully created a context after correction |
| Existing feature regressions | All 51 existing and 7 new tests passed, 58 in total |
| Final 1.0.1 wheel installation and GUI startup | Installed in a separate environment, checked `--check-gui`, and rendered the GUI offscreen with invalid XKB environment variables supplied |
| Reproduce and resolve the same XKB error on the user's Ubuntu system | Not performed |

Offscreen rendering and synthetic path tests do not prove that XKB context creation succeeds on the user's desktop. Resolution of `Gdk-ERROR: Failed to create XKB context` still needs confirmation in that environment.

## 1.0.0 Codex communication and notification validation

The fake App Server recorded JSON-RPC requests and returned only synthetic responses. A successful read used `initialize`, `initialized`, `account/read`, and `account/rateLimits/read`. It did not send model-execution requests such as `thread/start` or `turn/start`.

The same process was reused for multiple queries, and request responses were correctly distinguished from unrelated notifications or responses. Subscription usage was not queried for API-key accounts. Child processes were cleaned up on timeout, and even a process ignoring SIGTERM was cleaned up within four seconds when an in-progress query was closed.

The first observation was not mistaken for a reset event. A new recovery observation could be detected even when the remaining allowance was not exactly 100%. Scheduled-reset notifications and confirmed-recovery notifications remained separate. Checks also covered repeated observations, restarts, failed observations, stale data, and account changes.

## Separate 1.0.0 integration and visual checks

In addition to the 51 automated tests, the following paths were checked:

- Delivery of mock Codex responses and local Claude observations from the controller to the UI.
- Observation and display updates after manual refresh.
- Cleanup of active queries and child processes when closing the app.
- Actual offscreen rendering with visual inspection of the then-Korean labels, numbers, cards, and controls.
- Shell syntax and embedded Python syntax in the install and uninstall scripts.
- Refusal to use an unowned installation directory or uninstall without an ownership marker, using temporary paths.
- Stopping uninstallation and preserving app files when Claude settings could not be restored.
- Installing the final wheel in a separate virtual environment, then checking version output and demo rendering outside the source directory.
- Python 3.10 syntax compatibility for 11 app modules; this did not replace execution under that Python version.

Preview images used **demo data**. Their usage values and reset times did not represent the user's actual account.

## Reproduction commands

Run from the project directory using a Python environment with Qt dependencies installed.

```bash
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests -v
```

In a Python environment without Qt, the bridge and core tests can be run separately.

```bash
python3 -m unittest discover -s tests -p test_claude_bridge.py -v
python3 -m unittest discover -s tests -p test_core.py -v
```

View the installed app's demo:

```bash
"$HOME/.local/bin/quota-widget" --demo
```

Check the local GUI environment:

```bash
"$HOME/.local/bin/quota-widget" --check-gui
```

Check independent Claude usage queries, introduced in 1.2.0, with automatic queries enabled:

```bash
/usr/bin/quota-widget --check-claude
```

The last command uses the `.deb` installation path. For a source installation, use `"$HOME/.local/bin/quota-widget"`. When automatic queries are enabled, `--check-claude` attempts an actual CLI usage query and may use the network. If they are disabled, it diagnoses only the session bridge and observations. GUI connection diagnostics inspect local settings and recent in-memory state; `--check-gui` does not create a GUI or connect to service accounts.

## Areas not yet validated

- Connections to the user's actual ChatGPT/Codex and Claude subscription accounts.
- Long-running operation across an actual account's weekly reset.
- Physical Ubuntu 22.04, 24.04, and 26.04 systems using GNOME or other desktop environments.
- Window pinning, placement, and OS notifications in actual X11 or Wayland sessions.
- Reproduction of the reported XKB initialization error in the same environment and confirmation of the fix there.
- Actual startup at login and long-running recovery after suspend or network loss.

The optional Claude status-line mode passes on session observations and does not guarantee independent server polling. Automatic queries, introduced as the default in 1.2.0, use a separate CLI control request while respecting the CLI's recent one-minute cache and retry delays. In either mode, a widget query attempt is not equivalent to a new server observation. In the final user environment, compare the query mode, source, observation time, `quota-widget --check` output, and the account's official usage page.
