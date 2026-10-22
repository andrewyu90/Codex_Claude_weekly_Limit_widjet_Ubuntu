#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'HELP'
Usage: bash uninstall.sh

Restore this widget's optional Claude status-line integration, then remove its user install.
Close the widget first. Usage state and preferences are kept.
Run with the same XDG paths and CLAUDE_CONFIG_DIR used during installation.
HELP
  exit 0
fi
if [[ $# -ne 0 ]]; then
  echo "Unknown argument. Run bash uninstall.sh --help for usage." >&2
  exit 2
fi

python3 - <<'PY'
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

APP_ID = "quota-widget-user-install-v1"

def fail(message):
    print("Uninstall stopped: " + message, file=sys.stderr)
    raise SystemExit(1)

if os.environ.get("SUDO_USER"):
    fail("Run this as the user who installed the app, without sudo.")
def user_path(name, default):
    path = Path(os.environ.get(name) or str(default)).expanduser()
    if not path.is_absolute():
        path = Path.home() / path
    return Path(os.path.normpath(path))

data_home = user_path("XDG_DATA_HOME", Path.home() / ".local/share")
config_home = user_path("XDG_CONFIG_HOME", Path.home() / ".config")
root = data_home / "quota-widget"
if not root.exists():
    print("No per-user Quota Dock installation was found.")
    raise SystemExit(0)
if root.is_symlink() or root.resolve().parent != data_home.resolve():
    fail("Cannot verify that the installation path is safe.")
marker = root / ".quota-widget-install.json"
try:
    metadata = json.loads(marker.read_text(encoding="utf-8"))
except (OSError, ValueError):
    fail("Cannot verify the installation ownership marker. No files were deleted.")
if metadata.get("app_id") != APP_ID:
    fail("This directory is not a verified Quota Dock installation.")

python = root / "venv/bin/python"
if not python.is_file():
    fail("The installed Python interpreter is missing, so Claude settings cannot be restored safely. Reinstall the app before uninstalling it.")
print("Restoring the previous Claude status-line configuration.", flush=True)
result = subprocess.run([str(python), "-m", "quota_widget", "--uninstall-claude"])
if result.returncode:
    fail("Claude settings could not be restored. Resolve the settings conflict, then try uninstalling again. Installation files were preserved.")

owned_files = (
    (Path.home() / ".local/bin/quota-widget", "# quota-widget-owned-launcher-v1"),
    (data_home / "applications/quota-widget.desktop", "X-QuotaWidget-Owned=true"),
    (data_home / "applications/quota-dock.desktop", "X-QuotaWidget-Owned=true"),
    (config_home / "autostart/quota-widget.desktop", "X-QuotaWidget-Owned=true"),
)
for path, expected in owned_files:
    if path.is_symlink():
        fail(f"Cannot verify ownership of this symbolic link: {path}")
    if path.exists():
        if not path.is_file() or expected not in path.read_text(encoding="utf-8", errors="replace"):
            fail(f"This file was changed or belongs to another application. Stopping to preserve it: {path}")
for directory in (root / "app", root / "venv"):
    if directory.is_symlink():
        fail(f"Stopping because the installation directory is a symbolic link: {directory}")

for path, _ in owned_files:
    path.unlink(missing_ok=True)
for directory in (root / "app", root / "venv"):
    if directory.exists():
        shutil.rmtree(directory)
marker.unlink()
try:
    root.rmdir()
except OSError:
    print(f"Additional files outside the installation were preserved: {root}")
print("Quota Dock was removed. Usage state and preferences were preserved.")
PY
