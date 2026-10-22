# Building and releasing

This repository automates tests and package creation. Publishing a GitHub Release or uploading to PyPI remains a separate, manual step. The current app version is **1.2.1**.

## Local development environment

Use Python 3.10 or later on Ubuntu. Follow the development instructions in the [README](../README.md) to prepare a Python virtual environment and the required Qt system libraries. Building a `.deb` also requires `dpkg-deb` and `make`.

```bash
python3 -m venv .venv
. .venv/bin/activate
make dev
make test
```

`make dev` installs the app in editable mode and installs `requirements-dev.txt` into the current Python environment. The only additional development dependency is PyPA's `build`; tests use the standard-library `unittest` module. Instead of activating a virtual environment, you can select its interpreter explicitly, for example with `make test PYTHON=/path/to/venv/bin/python`.

`make test` first imports Qt and creates an offscreen `QApplication`. It fails if Qt is unavailable, so missing dependencies cannot silently skip the GUI tests. Test data and child CLI processes use synthetic fixtures. The tests do not require Codex or Claude to be installed, service logins, API keys, or real subscription accounts.

## Building local release files

```bash
make artifacts
```

The available commands have the following scope.

| Command | Result |
| --- | --- |
| `make build` | Creates a wheel and source distribution (`.tar.gz`) in `dist/` |
| `make deb` | Creates those two files and an Ubuntu `.deb` package |
| `make verify-dist` | Checks the existing three files, their versions and checksums, then extracts the source distribution into a separate temporary directory and rebuilds the wheel and `.deb` |
| `make artifacts` | Runs the complete build and verification sequence and writes `dist/SHA256SUMS` |

The current version produces these files:

```text
dist/
  quota_widget-1.2.1-py3-none-any.whl
  quota_widget-1.2.1.tar.gz
  quota-dock_1.2.1_all.deb
  SHA256SUMS
```

`MANIFEST.in` includes the app, tests, documentation, packaging files, install and uninstall scripts, and Makefile in the source distribution. `packaging/verify_dist.py` compares the source archive with the original files and verifies the wheel, `release.json` hash, and documentation inside the `.deb`. Verification does not install the `.deb` or change user settings.

The default build prepares the tools declared in `pyproject.toml` in an isolated environment, which may require internet access. If those tools are already available in a restricted environment, you can reuse them with the following command. These options do not install the required dependencies themselves.

```bash
make artifacts BUILD_OPTIONS=--no-isolation VERIFY_OPTIONS=--no-isolation
```

The wheel contains the app's Python files. The `.deb` bundles that wheel and the first-launch setup program; Qt is installed later into the launching user's environment. See the [README](../README.md) for first-launch preparation and account requirements, and [VALIDATION.md](VALIDATION.md) for the scope of completed checks.

## GitHub Actions

| Workflow | Trigger | Work performed |
| --- | --- | --- |
| `CI` — `.github/workflows/ci.yml` | Branch pushes, pull requests, and calls from another workflow | Offscreen tests, package builds, source-distribution rebuilds, and artifact uploads in both environments |
| `Build packages` — `.github/workflows/build.yml` | Manual execution from Actions or a pushed version tag beginning with `v` | Calls the same CI jobs to generate verified packages |

The two environments are **Ubuntu 22.04 / Python 3.10** and **Ubuntu 24.04 / Python 3.12**. Each stores its own results. Tag builds check that the tag equals `v` followed by the app version. For the current version, the tag must be `v1.2.1`.

The workflows request `contents: read` repository permissions, and checkout does not persist Git credentials. They do not use service account secrets. External network access is needed to obtain GitHub Actions code, public system packages, and Python dependencies. The tests do not query usage from real accounts.

If the repository has not yet been uploaded to GitHub, these workflows have not run there. A successful local packaging check and successful runs on both GitHub-hosted environments are separate results.

### Downloading build files

1. In the GitHub repository, open **Actions → Build packages → Run workflow**. The workflow must exist on the default branch for manual execution to be available.
2. Select the target branch and start the workflow. Confirm that checks succeed in both environments.
3. In the run summary's **Artifacts** section, download the `quota-dock-...` artifact for the desired environment.
4. Extract it and locate the `.whl`, `.tar.gz`, `.deb`, and `SHA256SUMS` files. Run `sha256sum -c SHA256SUMS` in the downloaded directory to verify them.

Artifacts are configured for **14 days** of retention, subject to repository policy. Downloading them requires a GitHub login and read access to the repository, so files intended for general distribution should also be attached to a Release as described below. See GitHub's instructions for [manual workflow execution](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-running-a-workflow) and [artifact downloads](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts).

## Creating a Release manually

Perform these steps when you decide to distribute a release. Preparing the project or building it in Actions does not publish a Release.

1. Check CI for the commit to release. If changing the version, keep `pyproject.toml` and `quota_widget/__init__.py` consistent, and update the installation marker, application-menu version label, and `CHANGELOG.md`.
2. Create and push a version tag on that commit, then wait for **Build packages** to finish. The current version uses `v1.2.1`. Do not reuse a published tag for different code.
3. Download the packages and `SHA256SUMS` from one environment in the same run. Keep those files together rather than mixing outputs from different environments, and confirm that both environments passed.
4. Open **Releases → Draft a new release** and select that tag. Describe the changes, installation steps, experimental Claude control interface, and limits of real-account and desktop validation.
5. Attach the `.deb`, wheel, source `.tar.gz`, and `SHA256SUMS`, then save the draft.
6. Review the files and confirm that the tag points to the built commit before choosing **Publish release**.

GitHub's automatically generated **Source code (zip)** contains the repository source, but the `.deb` and Python wheel must be attached separately. See the [official GitHub Release documentation](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository) for draft and publication details.

## Maintaining pinned Actions

The following releases and full commit SHAs were checked against their official repositories on **2026-10-22**.

| Action | Verified release | Pinned SHA |
| --- | --- | --- |
| `actions/checkout` | [v7.0.1](https://github.com/actions/checkout/releases/tag/v7.0.1) | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `actions/setup-python` | [v7.0.0](https://github.com/actions/setup-python/releases/tag/v7.0.0) | `5fda3b95a4ea91299a34e894583c3862153e4b97` |
| `actions/upload-artifact` | [v7.0.2](https://github.com/actions/upload-artifact/releases/tag/v7.0.2) | `cf430e030ddbb5b0abf93d22962f4752f3646cd9` |

When updating an Action, inspect the official release and the code referenced by its full SHA, then update the workflow SHA and version comment together. GitHub also recommends referencing the full commit SHA of a released Action. See the [workflow syntax reference](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax) and [supported runner list](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

For Python distribution configuration, see [PyPA build](https://build.pypa.io/en/stable/) and the [setuptools MANIFEST.in guide](https://setuptools.pypa.io/en/latest/userguide/miscellaneous.html).
