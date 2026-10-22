# Contributing

Quota Dock is a Python/PySide6 desktop application for Ubuntu. Contributions can address bugs, documentation or changes in the services' response formats. This guide does not promise a maintenance response time or platform-specific support.

## Development environment

See the installation section of [README](README.md) for Ubuntu system dependencies. From the project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e . -r requirements-dev.txt
make test
python -m quota_widget --demo
```

Use `--demo` when developing the interface. It needs no subscription login and does not perform normal first-run registration of application-menu or autostart entries. Disconnect any Claude session bridge that uses a development environment before removing that environment.

## Behaviour to preserve

- Usage monitoring must not create model prompts or conversation turns.
- Missing values must not become 0% used or 100% remaining.
- Keep accounts, allowance periods and observation timestamps distinct. Do not sum percentages from separate limits.
- Distinguish a scheduled reset from recovery established by a new observation.
- Failed requests and repeated cached callbacks must not appear as fresh server observations.
- Never add real tokens, cookies or account data to source files, fixtures or logs.
- Verify ownership before changing user settings and preserve the ability to restore earlier settings.

Read [How Claude usage checks work](docs/CLAUDE_AUTO_QUERY.md) before changing the experimental control request. Preserve response validation and subprocess cleanup.

## Language and interface

Application text, diagnostics, notifications, installer messages, comments and project documentation are written in English. Use clear, short labels that fit the compact interface. Keep protocol method names, JSON keys and existing data formats unchanged when editing wording.

Some tests intentionally use non-ASCII Latin characters in temporary paths to exercise Unicode handling. Preserve that coverage when editing fixtures. Use demo data for screenshots and keep the example-data label visible.

## Tests

```bash
make test
```

This uses the same offscreen GUI mode as CI. The tests rely on synthetic responses, temporary files and mock CLI processes rather than real accounts. Install PySide6 and check the full result; GUI tests can otherwise be skipped when run directly without the Makefile's Qt preflight.

For a change affecting providers, notifications, account switching or process cleanup, add a test that reproduces the relevant failure condition. A wording-only edit does not need a redundant test of the same literal string. Separate mock verification from any authenticated, real-service checks that a compatibility change requires.

## Builds

```bash
make build
make deb
```

Wheels, source distributions and Debian packages are generated in `dist/`. Do not commit those outputs. Attach reviewed packages to Releases instead. Building a Debian package requires `dpkg-deb`, but does not install the app or change user settings.

See the [release guide](docs/RELEASING.md) for version synchronisation and rebuilding from a source distribution. GitHub Actions tests and builds without service accounts and keeps the resulting packages as artifacts.

## Issues and pull requests

Include the operating system, app/Python/CLI versions, reproduction steps, expected result and actual result in a bug report. Do not attach authentication files, raw usage caches, tokens or a copy of your home directory. Review diagnostic output for personal paths and account identifiers, and share only the necessary sections.

A pull request should explain the reason for the change, the resulting behaviour, the checks performed and the environments that remain untested. Include a demo screenshot if the interface changes. Follow [SECURITY.md](SECURITY.md) for sensitive vulnerability reports.

## Licence

Contributions to the project are covered by its [MIT License](LICENSE). When adding external code, check the source, licence compatibility and required copyright notices. Do not add Qt or service CLI binaries to the source repository.
