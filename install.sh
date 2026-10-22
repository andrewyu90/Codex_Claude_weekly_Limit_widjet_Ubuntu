#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'HELP'
Usage: bash install.sh

Install Quota Dock for the current user, without sudo.
Requires Python 3.10+, python3-venv, and internet access for PySide6.
Uses XDG_DATA_HOME when set. First GUI launch enables login autostart once.
Claude automatic usage lookup requires Claude Code 2.1.306+ and its login.
The optional session bridge changes Claude settings only when selected.
HELP
  exit 0
fi
if [[ $# -ne 0 ]]; then
  echo "Unknown argument. Run bash install.sh --help for usage." >&2
  exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required: sudo apt install python3 python3-venv" >&2
  exit 1
fi

quota_source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
python3 - "$quota_source_dir" <<'PY'
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

APP_ID = "quota-widget-user-install-v1"
WRAPPER_MARK = "# quota-widget-owned-launcher-v1"
DESKTOP_MARK = "X-QuotaWidget-Owned=true"

def fail(message):
    print("Installation failed: " + message, file=sys.stderr)
    raise SystemExit(1)

def safe_owned_file(path, marker):
    if path.is_symlink():
        fail(f"Refusing to overwrite a symbolic link: {path}")
    if path.exists():
        if not path.is_file() or marker not in path.read_text(encoding="utf-8", errors="replace"):
            fail(f"A different file already exists. Stopping to preserve it: {path}")

def atomic_text(path, value, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".quota-widget-", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

def desktop_arg(value):
    # Desktop Entry quoting differs from shell quoting. Escape field-code '%'.
    text = str(value).replace("%", "%%")
    for char in ("\\", '"', "`", "$"):
        text = text.replace(char, "\\" + char)
    return '"' + text + '"'

if sys.version_info < (3, 10):
    fail("Python 3.10 or later is required.")
if os.environ.get("SUDO_USER"):
    fail("Run this installer as your regular user, without sudo.")

source = Path(sys.argv[1]).resolve()
for relative in ("pyproject.toml", "quota_widget/__main__.py", "README.md"):
    if not (source / relative).is_file():
        fail(f"A distribution file is missing: {relative}")

data_home = Path(os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")).expanduser()
if not data_home.is_absolute():
    data_home = Path.home() / data_home
data_home = Path(os.path.normpath(data_home))
root = data_home / "quota-widget"
if root.is_symlink():
    fail(f"The installation path is a symbolic link: {root}")
root = root.resolve()
if root.parent != data_home.resolve() or root == Path.home().resolve():
    fail("Cannot verify that the installation path is safe.")

marker_file = root / ".quota-widget-install.json"
if root.exists():
    if not root.is_dir():
        fail(f"The installation path is not a directory: {root}")
    if any(root.iterdir()):
        try:
            metadata = json.loads(marker_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            fail(f"Cannot verify ownership of the existing directory: {root}")
        if metadata.get("app_id") != APP_ID:
            fail(f"The existing directory is not a Quota Dock installation: {root}")

launcher = Path.home() / ".local/bin/quota-widget"
desktop = data_home / "applications/quota-widget.desktop"
safe_owned_file(launcher, WRAPPER_MARK)
safe_owned_file(desktop, DESKTOP_MARK)
for directory in (root / "app", root / "venv"):
    if directory.is_symlink():
        fail(f"Refusing to install into a symbolic link: {directory}")
for path in (root, launcher, desktop):
    if "\n" in str(path) or "\r" in str(path):
        fail("Installation paths must not contain line breaks.")

root.mkdir(parents=True, exist_ok=True, mode=0o700)
atomic_text(marker_file, json.dumps({"app_id": APP_ID, "version": "1.2.1"}, indent=2) + "\n", 0o600)
venv = root / "venv"
python = venv / "bin/python"
if not python.is_file():
    print("Preparing the Python virtual environment.", flush=True)
    result = subprocess.run([sys.executable, "-m", "venv", str(venv)])
    if result.returncode:
        fail("Could not create the virtual environment. Run sudo apt install python3-venv and try again.")

stage = Path(tempfile.mkdtemp(prefix=".source-", dir=root))
try:
    for name in ("quota_widget", "assets", "docs", "tests", "packaging"):
        if (source / name).is_dir():
            shutil.copytree(source / name, stage / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
    for name in ("pyproject.toml", "README.md", "LICENSE", "install.sh", "uninstall.sh",
                 "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md", "Makefile",
                 "requirements-dev.txt", "MANIFEST.in"):
        if (source / name).is_file():
            shutil.copy2(source / name, stage / name)
    print("Installing the widget and Qt dependencies. The first installation requires internet access.", flush=True)
    result = subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--upgrade", str(stage)])
    if result.returncode:
        fail("Package installation failed. Review the error above and docs/TROUBLESHOOTING.md.")
    app = root / "app"
    if app.exists():
        shutil.rmtree(app)
    stage.rename(app)
finally:
    if stage.exists():
        shutil.rmtree(stage)

wrapper = (
    "#!/bin/sh\n" + WRAPPER_MARK + "\n"
    'export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"\n'
    "export QUOTA_WIDGET_LAUNCHER=" + shlex.quote(str(launcher)) + "\n"
    "exec " + shlex.quote(str(python)) + ' -m quota_widget "$@"\n'
)
atomic_text(launcher, wrapper, 0o755)
desktop_text = (
    "[Desktop Entry]\nType=Application\nVersion=1.0\nName=Quota Dock\n"
    "Comment=Weekly Codex and Claude usage widget\n"
    "Exec=" + desktop_arg(launcher) + "\n"
    "Icon=" + str(root / "app/quota_widget/assets/icon.svg") + "\n"
    "Terminal=false\nCategories=Utility;Development;\nStartupNotify=true\n"
    "StartupWMClass=quota-dock\n" + DESKTOP_MARK + "\n"
)
atomic_text(desktop, desktop_text, 0o644)
updater = shutil.which("update-desktop-database")
if updater:
    subprocess.run([updater, str(desktop.parent)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

print("\nInstallation complete. Open Quota Dock from the application menu.")
print("Terminal command: " + shlex.quote(str(launcher)))
print("The first normal launch enables startup at login. You can turn it off in Settings.")
print("Automatic Claude queries require Claude Code 2.1.306 or later and a valid CLI login.")
print("Claude query settings and the optional session bridge are available in Settings.")
PY
