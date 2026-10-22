#!/usr/bin/python3
"""Visible, per-user runtime preparation for the Ubuntu package.

Only the bundled app wheel and its public Python dependencies are installed.
No package-manager hook executes this file or changes a user's home directory.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import queue
import runpy
import signal
import subprocess
import sys
import tempfile
import threading
import time

SHARE_DIR = Path("/usr/share/quota-widget")
LAUNCHER = "/usr/bin/quota-widget"
OWNER = "quota-dock-user-runtime-v1"
MARKER = ".quota-dock-runtime.json"
PROBE = """import importlib.metadata as m, json
from PySide6 import QtCore, QtGui, QtWidgets, QtNetwork
try:
    from quota_widget import __main__
    import quota_widget
    app = m.version('quota-widget')
    if quota_widget.__version__ != app:
        app = None
except (m.PackageNotFoundError, ImportError):
    app = None
print(json.dumps({'app': app, 'qt': m.version('PySide6-Essentials')}))
"""


class PreparationError(Exception):
    pass


class Cancelled(Exception):
    pass


def read_release(share_dir: Path = SHARE_DIR) -> tuple[dict, Path]:
    try:
        release = json.loads((share_dir / "release.json").read_text(encoding="utf-8"))
        name = release["wheel"]
        if not isinstance(name, str) or Path(name).name != name or not name.endswith(".whl"):
            raise ValueError("invalid wheel name")
        wheel = share_dir / name
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != release["sha256"]:
            raise ValueError("wheel checksum mismatch")
        if not isinstance(release["version"], str):
            raise ValueError("invalid version")
        return release, wheel
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise PreparationError("Could not verify the installation files. Reinstall the Quota Dock .deb package.") from exc


def runtime_root() -> Path:
    raw = os.environ.get("XDG_DATA_HOME", "")
    folder = Path(raw).expanduser() if raw else Path.home() / ".local/share"
    if not folder.is_absolute():
        folder = Path.home() / folder
    return Path(os.path.normpath(folder)) / "quota-dock"


def ensure_owned_root(root: Path) -> None:
    if root.is_symlink():
        raise PreparationError("The runtime directory is a symbolic link. Setup stopped to protect existing files.")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = root / MARKER
    if marker.is_symlink():
        raise PreparationError("Cannot verify ownership of the runtime environment.")
    if marker.exists():
        try:
            owned = json.loads(marker.read_text(encoding="utf-8")).get("app_id") == OWNER
        except (OSError, ValueError, AttributeError):
            owned = False
        if not owned:
            raise PreparationError("A different runtime already exists at this location. Existing files were preserved.")
    else:
        # Two concurrent first launches may both reach here. O_EXCL avoids
        # replacing the other process's marker, and its content is rechecked.
        if any(root.iterdir()):
            raise PreparationError("The runtime directory contains unrecognized files. Existing files were preserved.")
        try:
            descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return ensure_owned_root(root)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"app_id": OWNER}, stream)
            stream.write("\n")
    for child in (root / "runtime", root / "runtime/bin", root / "prepare.lock"):
        if child.is_symlink():
            raise PreparationError("The runtime path is a symbolic link. Existing files were preserved.")


def probe_runtime(python: Path) -> dict:
    if not python.is_file():
        return {}
    try:
        result = subprocess.run(
            [str(python), "-c", PROBE], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            timeout=3, cwd=str(python.parent.parent),
        )
        value = json.loads(result.stdout) if result.returncode == 0 else {}
        if not isinstance(value, dict):
            return {}
        qt = tuple(int(part) for part in str(value.get("qt", "")).split(".")[:2])
        return value if (6, 8) <= qt < (7, 0) else {}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


class Preparer:
    def __init__(self, root: Path, release: dict, wheel: Path, report):
        self.root, self.release, self.wheel, self.report = root, release, wheel, report
        self.cancelled = threading.Event()
        self.process = None
        self.process_lock = threading.Lock()

    def cancel(self, force=False):
        self.cancelled.set()
        with self.process_lock:
            process = self.process
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            except ProcessLookupError:
                pass

    def run_command(self, command, error_message):
        if self.cancelled.is_set():
            raise Cancelled()
        environment = os.environ.copy()
        environment.update(PIP_NO_INPUT="1", PIP_DISABLE_PIP_VERSION_CHECK="1", PIP_DEFAULT_TIMEOUT="25")
        # Do not record pip output: configured package indexes may contain
        # credentials. The preparation window reports safe stage descriptions.
        with self.process_lock:
            self.process = subprocess.Popen(
                command, cwd=str(self.root), env=environment,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, start_new_session=True,
            )
            process = self.process
        if self.cancelled.is_set():
            self.cancel()
        try:
            status = process.wait()
        finally:
            with self.process_lock:
                self.process = None
        if self.cancelled.is_set():
            raise Cancelled()
        if status:
            raise PreparationError(f"{error_message}\nError code: {status}")

    def prepare(self):
        ensure_owned_root(self.root)
        lock_path = self.root / "prepare.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(lock_path, flags, 0o600), "a+") as lock:
            while True:
                if self.cancelled.is_set():
                    raise Cancelled()
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    self.report("Waiting for another Quota Dock setup to finish.")
                    self.cancelled.wait(0.25)
            python = self.root / "runtime/bin/python"
            current = probe_runtime(python)
            if current.get("app") == self.release["version"]:
                return python
            if not current:
                self.report("1 / 3  ·  Creating a runtime for this user…")
                self.run_command(
                    [sys.executable, "-m", "venv", str(self.root / "runtime")],
                    "Could not create the Python runtime. Check available disk space and whether python3-venv is installed.",
                )
            if current:
                self.report("2 / 3  ·  Updating Quota Dock…")
                extras = ["--no-deps"]
            else:
                self.report("2 / 3  ·  Downloading display components…\nThis may take a moment, depending on your connection.")
                extras = ["--only-binary=:all:"]
            self.run_command(
                [str(python), "-m", "pip", "install", "--upgrade", "--no-input",
                 "--disable-pip-version-check", "--progress-bar", "off", *extras, str(self.wheel)],
                "Could not install the components. Check your internet connection and disk space, then try again.\nSetup also requires a PySide6 binary package compatible with this computer.",
            )
            self.report("3 / 3  ·  Checking the runtime…")
            if probe_runtime(python).get("app") != self.release["version"]:
                raise PreparationError("Could not verify that the components run correctly. Reinstall the .deb package or try again.")
            if self.cancelled.is_set():
                raise Cancelled()
            descriptor, temporary = tempfile.mkstemp(prefix=".ready-", dir=self.root)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump({"app_id": OWNER, "version": self.release["version"]}, stream)
                    stream.write("\n")
                os.replace(temporary, self.root / "ready.json")
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return python


def preparation_window(root: Path, release: dict, wheel: Path) -> Path | None:
    import tkinter as tk
    from tkinter import ttk

    window = tk.Tk(className="QuotaDock")
    window.title("Quota Dock · Setup")
    window.resizable(False, False)
    container = ttk.Frame(window, padding=24)
    container.grid(sticky="nsew")
    ttk.Label(container, text="Setting up Quota Dock", font=("sans", 17, "bold")).grid(sticky="w")
    ttk.Label(container, text="Preparing Quota Dock. The widget will open when setup is complete.",
              wraplength=470).grid(sticky="w", pady=(10, 18))
    message = tk.StringVar(value="Checking the runtime…")
    label = ttk.Label(container, textvariable=message, wraplength=470)
    label.grid(sticky="w", pady=(0, 14))
    progress = ttk.Progressbar(container, mode="indeterminate", length=470)
    progress.grid(sticky="ew", pady=(0, 18))
    buttons = ttk.Frame(container)
    buttons.grid(sticky="e")
    events = queue.Queue()
    state = {"worker": None, "preparer": None, "python": None, "closing": False}

    def worker(preparer):
        try:
            events.put(("ready", preparer.prepare()))
        except Cancelled:
            events.put(("cancelled", None))
        except PreparationError as exc:
            events.put(("error", str(exc)))
        except (OSError, subprocess.SubprocessError):
            events.put(("error", "Could not prepare the runtime. Check directory permissions and available disk space, then try again."))

    def start():
        retry.configure(state="disabled")
        close.configure(text="Cancel", state="normal")
        message.set("Checking the runtime…")
        progress.start(12)
        preparer = Preparer(root, release, wheel, lambda value: events.put(("progress", value)))
        thread = threading.Thread(target=worker, args=(preparer,), daemon=True)
        state.update(preparer=preparer, worker=thread)
        thread.start()

    def finish_close():
        thread = state["worker"]
        if thread is not None and thread.is_alive():
            if time.monotonic() - state["close_at"] > 2:
                state["preparer"].cancel(force=True)
            window.after(100, finish_close)
        else:
            window.destroy()

    def cancel():
        if state["closing"]:
            return
        state.update(closing=True, close_at=time.monotonic())
        message.set("Stopping setup…")
        close.configure(state="disabled")
        retry.configure(state="disabled")
        if state["preparer"] is not None:
            state["preparer"].cancel()
        finish_close()

    def poll():
        try:
            while True:
                kind, value = events.get_nowait()
                if state["closing"]:
                    continue
                if kind == "progress":
                    message.set(value)
                elif kind == "ready":
                    state["python"] = value
                    window.destroy()
                    return
                elif kind == "error":
                    progress.stop()
                    message.set(value)
                    retry.configure(state="normal")
                    close.configure(text="Close")
        except queue.Empty:
            pass
        if not state["closing"]:
            window.after(100, poll)

    retry = ttk.Button(buttons, text="Retry", command=start, state="disabled")
    retry.grid(row=0, column=0, padx=(0, 8))
    close = ttk.Button(buttons, text="Cancel", command=cancel)
    close.grid(row=0, column=1)
    window.protocol("WM_DELETE_WINDOW", cancel)
    window.after(80, start)
    window.after(100, poll)
    window.mainloop()
    return state["python"]


def main(argv=None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    os.environ["QUOTA_WIDGET_LAUNCHER"] = LAUNCHER
    try:
        release, wheel = read_release()
        # Diagnostics remain usable before Qt is installed, including over SSH.
        if arguments and arguments[0] in ("--version", "--help", "-h", "--check-gui", "--check", "--check-claude"):
            sys.path.insert(0, str(wheel))
            sys.argv = [LAUNCHER, *arguments]
            runpy.run_module("quota_widget", run_name="__main__")
            return 0
        if os.geteuid() == 0:
            raise PreparationError("Open Quota Dock from the application menu as your regular user.")
        root = runtime_root()
        # Before writing anything, the fast path must also verify ownership.
        ensure_owned_root(root)
        python = root / "runtime/bin/python"
        if probe_runtime(python).get("app") != release["version"]:
            python = preparation_window(root, release, wheel)
            if python is None:
                return 0
        os.execv(str(python), [str(python), "-m", "quota_widget", *arguments])
        return 0
    except PreparationError as exc:
        message = str(exc)
    except ImportError:
        message = "Could not open the setup window. Reinstall the .deb package to provide python3-tk."
    except Exception as exc:
        # Tcl errors may contain local display paths. Do not persist any logs.
        message = "Could not start Quota Dock. Try opening its application icon from your Ubuntu graphical desktop."
    print(message, file=sys.stderr)
    try:
        import tkinter as tk
        from tkinter import messagebox
        window = tk.Tk()
        window.withdraw()
        messagebox.showerror("Quota Dock startup error", message, parent=window)
        window.destroy()
    except Exception:
        pass
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
