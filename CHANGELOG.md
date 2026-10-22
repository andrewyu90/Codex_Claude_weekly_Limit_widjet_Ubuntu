# Changelog

Application releases and repository-preparation changes are recorded separately. Undated historical entries describe the original distribution; they do not imply that public GitHub Releases already exist.

## 1.2.1 — English interface and documentation

- Translate application labels, settings, notifications, diagnostics and error messages into English.
- Translate installation, removal, desktop-entry and first-run preparation text.
- Rewrite the README, supporting documentation and contribution templates in English.
- Update text assertions and replace Korean path fixtures with other Unicode examples, preserving their original coverage.
- Regenerate the demo preview from the English application and retain the compact interface.
- Increment the package version so existing 1.2.0 runtimes are upgraded by the Debian launcher.
- Rebuild cached Claude display text and queued notification text in English without changing usage values, observation times or notification delivery history.
- Exclude display labels from new session observation fingerprints and recognise older fingerprints, so a label translation alone does not refresh an observation.
- Preserve provider protocols, quota calculations, settings keys and cache JSON structure.

## Repository preparation based on 1.2.0

- Organise the original 1.2.0 source ZIP as a GitHub-ready project.
- Expand the README with installation, autostart, reading interpretation, monitoring costs, resource use and development instructions.
- Add first-upload, release, contribution and security guides, plus Issue and Pull Request templates.
- Add Git exclusions, line-ending rules and editor configuration.
- Add GitHub Actions tests and package builds that need no real service login.
- Add development commands and source-distribution rules for wheel, sdist and Debian builds.
- Correct the source installer's icon path to point to the SVG actually included in the package.
- Preserve the new root documents and development files in source installations.

## 1.2.0

- Add independent Claude usage checks at startup and at regular intervals through the installed CLI.
- Add automatic-check settings, executable selection, a separate interval and connection diagnostics.
- Validate account identity and cache timestamps, retain older readings on failure and respect retry delays.
- Retain the status-line bridge as an optional session mode.
- Extend verification of subprocess shutdown, account changes and observation merging.

## 1.1.0

- Add the compact 340 × 316 main window, details and settings views.
- Add application-menu launch, single-instance handling and login-autostart controls.
- Add Debian packaging, visible first-run preparation and per-user runtime upgrades.
- Improve Claude session-connection diagnostics.

## 1.0.1

- Locate and prepare XKB keyboard data before GUI creation.
- Add `--check-gui` diagnostics that run without opening the GUI.

## 1.0.0

- Read Codex App Server limits and observe Claude status-line usage.
- Display weekly allowance and reset times, with scheduled and recovery notifications.
- Add per-user installation/removal, demo mode and local state storage.
