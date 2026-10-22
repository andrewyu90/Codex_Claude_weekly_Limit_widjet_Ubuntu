"""Read subscription metadata via the documented local Codex app-server.

This transport never starts a model turn, reads token files, accepts approvals,
or invokes a quota-reset redemption method.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from . import __version__
from .models import parse_codex, snapshot
from .storage import private_directory, state_dir


class ProviderError(RuntimeError):
    pass


def locate_codex(binary: str = "codex") -> str | None:
    if "/" in binary:
        path = Path(binary).expanduser()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    found = shutil.which(binary)
    if found:
        return found
    if binary == "codex":
        for path in (Path.home() / ".local/bin/codex", Path.home() / ".npm-global/bin/codex", Path("/usr/local/bin/codex")):
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
    return None


class CodexProvider:
    def __init__(self, binary="codex", timeout=20.0, command=None, cwd=None):
        self.binary = binary
        self.timeout = timeout
        self.command = command  # A list override for protocol tests, never shell text.
        self.cwd = cwd
        self.proc = None
        self.incoming = queue.Queue(maxsize=256)
        self.lock = threading.RLock()
        self.serial = 0
        self.stopped = threading.Event()

    def _reader(self, proc, incoming):
        try:
            while True:
                line = proc.stdout.readline(1_048_577)
                if not line:
                    break
                if len(line) > 1_048_576:
                    break
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if isinstance(message, dict):
                    incoming.put(message, timeout=1)
        except (OSError, ValueError, queue.Full):
            pass
        finally:
            try:
                incoming.put_nowait(None)
            except queue.Full:
                pass

    def _send(self, message):
        if self.stopped.is_set():
            raise ProviderError("Codex usage query stopped.")
        if self.proc is None or self.proc.poll() is not None:
            raise ProviderError("The Codex connection closed. It will reconnect on the next query.")
        try:
            self.proc.stdin.write(json.dumps(message, allow_nan=False) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise ProviderError("Cannot communicate with Codex.") from exc

    def _call(self, method, params=None):
        self.serial += 1
        request_id = self.serial
        request = {"id": request_id, "method": method}
        if params is not None:
            request["params"] = params
        self._send(request)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                message = self.incoming.get(timeout=max(0.001, deadline - time.monotonic()))
            except queue.Empty as exc:
                raise ProviderError("The Codex query timed out. Check the connection and sign-in status.") from exc
            if message is None:
                raise ProviderError("The Codex app-server exited. Check the CLI version and sign-in status.")
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    # Never surface raw service error text: it can contain private URLs/data.
                    code = message.get("error", {}).get("code") if isinstance(message.get("error"), dict) else None
                    if code == -32601:
                        raise ProviderError("The installed Codex version does not support usage queries. Update the CLI.")
                    raise ProviderError("Codex rejected the usage query. Check your codex login status or temporary limits.")
                return message.get("result")
            if "method" in message and "id" in message:
                self._send({"id": message["id"], "error": {"code": -32601, "message": "This read-only client does not handle server requests"}})
            if time.monotonic() >= deadline:
                raise ProviderError("The Codex usage query timed out.")

    def _connect(self):
        if self.stopped.is_set():
            raise ProviderError("Codex usage query stopped.")
        if self.proc is not None and self.proc.poll() is None:
            return
        executable = locate_codex(self.binary) if self.command is None else None
        if self.command is None and executable is None:
            raise ProviderError("The Codex CLI was not found. Install it and set the executable path in Settings.")
        command = self.command or [executable, "app-server", "--listen", "stdio://"]
        directory = self.cwd or str(private_directory(state_dir() / "codex-runtime"))
        self.incoming = queue.Queue(maxsize=256)
        try:
            self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                         errors="replace", bufsize=1, cwd=directory,
                                         start_new_session=True)
        except OSError as exc:
            raise ProviderError("Could not start the Codex process. Check the executable path.") from exc
        threading.Thread(target=self._reader, args=(self.proc, self.incoming), daemon=True).start()
        if self.stopped.is_set():
            self.abort()
            raise ProviderError("Codex usage query stopped.")
        self._call("initialize", {"clientInfo": {"name": "quota_dock", "title": "Quota Dock", "version": __version__}})
        self._send({"method": "initialized", "params": {}})

    def fetch(self) -> dict:
        with self.lock:
            try:
                self._connect()
                account_response = self._call("account/read", {"refreshToken": False})
                account = account_response.get("account") if isinstance(account_response, dict) else None
                if not isinstance(account, dict):
                    self._close_unlocked()
                    return snapshot("codex", "waiting", "Run codex login in a terminal to sign in with your ChatGPT account.")
                if account.get("type") != "chatgpt":
                    self._close_unlocked()
                    return snapshot("codex", "unsupported", "Weekly limits are available when signed in with a ChatGPT subscription. API key and local model limits are separate.")
                response = self._call("account/rateLimits/read")
                return parse_codex(response, account)
            except Exception as exc:
                self._close_unlocked()
                if isinstance(exc, (ProviderError, ValueError)):
                    raise
                raise ProviderError("Could not read Codex usage. It will retry on the next poll.") from exc

    def _close_unlocked(self):
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=2)
        except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
            pass
        for stream in (proc.stdin, proc.stdout):
            if stream:
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass

    def close(self):
        self.abort()
        with self.lock:
            self._close_unlocked()

    def abort(self):
        """Cancel an in-flight read immediately, without waiting for its lock."""
        self.stopped.set()
        try:
            self.incoming.put_nowait(None)
        except queue.Full:
            pass
        proc = self.proc
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
