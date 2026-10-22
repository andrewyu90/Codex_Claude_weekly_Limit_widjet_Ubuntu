"""Find keyboard data before Qt or its GTK theme creates an XKB context.

Some Qt wheels use a build-time XKB prefix that is absent on the host.
Only this process's environment is changed; system configuration is untouched.
"""
from __future__ import annotations

import importlib.metadata
import os
from pathlib import Path
import re
import sys


_XKB_DIRECTORIES = ("rules", "keycodes", "types", "compat", "symbols")
_INSTALL_HINT = "sudo apt install --reinstall xkb-data"


def usable_xkb_root(value: str | Path | None) -> bool:
    if not value:
        return False
    try:
        root = Path(value)
        return (root.is_absolute()
                and all((root / name).is_dir() and os.access(root / name, os.R_OK | os.X_OK)
                        for name in _XKB_DIRECTORIES)
                and any((root / "rules" / name).is_file()
                        and os.access(root / "rules" / name, os.R_OK)
                        for name in ("evdev", "base")))
    except (OSError, ValueError, TypeError):
        return False


def system_xkb_roots() -> list[Path]:
    # Newer xkeyboard-config installations can use a versioned directory.
    # Exclude *.d extension directories, which are not complete keymap roots.
    versioned = []
    try:
        for path in Path("/usr/share").glob("xkeyboard-config-*"):
            match = re.fullmatch(r"xkeyboard-config-(\d+(?:\.\d+)*)", path.name)
            if match:
                versioned.append((tuple(int(n) for n in match[1].split(".")), path))
    except OSError:
        pass
    return ([path for _, path in sorted(versioned, reverse=True)]
            + [Path("/usr/share/X11/xkb"), Path("/usr/share/xkeyboard-config")])


def find_xkb_root(environ=None, roots=None) -> str | None:
    env = os.environ if environ is None else environ
    candidates = [env.get("XKB_CONFIG_ROOT", "")]
    candidates.extend(env.get("QT_XKB_CONFIG_ROOT", "").split(os.pathsep))
    candidates.extend(system_xkb_roots() if roots is None else roots)
    for candidate in candidates:
        if usable_xkb_root(candidate):
            return str(Path(candidate))
    return None


def prepare_gui_environment(environ=None, roots=None) -> str | None:
    """Repair missing/invalid XKB roots, preserving usable explicit settings."""
    if not sys.platform.startswith("linux"):
        return None
    env = os.environ if environ is None else environ
    # This custom-painted widget does not need GTK theme initialization.
    # Keep an explicit platform theme and the chosen X11/Wayland backend.
    if not env.get("QT_QPA_PLATFORMTHEME"):
        env["QT_QPA_PLATFORMTHEME"] = "generic"
    root = find_xkb_root(env, roots)
    if root is None:
        platform = env.get("QT_QPA_PLATFORM", "").split(":", 1)[0]
        if platform in ("offscreen", "minimal"):
            return None
        raise RuntimeError(
            "XKB keyboard configuration data was not found. On Ubuntu, repair it with "
            "the following command, then try again:\n" + _INSTALL_HINT
            + "\nDiagnostics: quota-widget --check-gui"
        )
    if not usable_xkb_root(env.get("XKB_CONFIG_ROOT")):
        env["XKB_CONFIG_ROOT"] = root
    if not any(usable_xkb_root(p) for p in env.get("QT_XKB_CONFIG_ROOT", "").split(os.pathsep)):
        env["QT_XKB_CONFIG_ROOT"] = root
    return root


def gui_diagnostics() -> dict:
    """Read-only diagnostics; no QApplication, authentication or network calls."""
    from . import __version__
    try:
        qt_package = importlib.metadata.version("PySide6-Essentials")
    except importlib.metadata.PackageNotFoundError:
        qt_package = None
    return {
        "app_version": __version__,
        "python_version": sys.version.split()[0],
        "pyside6_essentials_version": qt_package,
        "session_type": os.environ.get("XDG_SESSION_TYPE"),
        "display_present": bool(os.environ.get("DISPLAY")),
        "wayland_display_present": bool(os.environ.get("WAYLAND_DISPLAY")),
        "xkb_config_root": os.environ.get("XKB_CONFIG_ROOT"),
        "qt_xkb_config_root": os.environ.get("QT_XKB_CONFIG_ROOT"),
        "detected_xkb_root": find_xkb_root(),
        "qt_qpa_platform": os.environ.get("QT_QPA_PLATFORM"),
        "qt_qpa_platformtheme": os.environ.get("QT_QPA_PLATFORMTHEME"),
        "qt_plugin_path_override": bool(os.environ.get("QT_PLUGIN_PATH")),
        "library_path_override": bool(os.environ.get("LD_LIBRARY_PATH")),
        "install_hint_if_missing": _INSTALL_HINT,
    }
