"""Read Claude plan usage through the installed, unmodified Claude Code CLI.

Only initialize and get_usage control requests are sent. No user turn, prompt,
login, credential read, or direct OAuth request is performed by this module.
The usage control API is experimental; unfamiliar responses fail closed.
"""
from __future__ import annotations

import json
import math
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from .models import identity_hash, number, snapshot, timestamp
from .storage import claude_config_dir, private_directory, state_dir

MIN_CLAUDE_VERSION = (2, 1, 306)
CACHE_FRESH_SECONDS = 60
MAX_MESSAGE_BYTES = 1_048_576
MAX_OUTPUT_BYTES = 4_194_304
MAX_METADATA_BYTES = 2_000_000
MAX_MESSAGES = 1024

_FLAGS = [
    "--safe-mode", "--print", "--input-format", "stream-json",
    "--output-format", "stream-json", "--verbose", "--no-session-persistence",
    "--tools", "", "--max-turns", "0", "--no-chrome",
    "--setting-sources", "user", "--strict-mcp-config", "--mcp-config",
    '{"mcpServers":{}}',
]


class _Failure(Exception):
    def __init__(self, code: str, message: str, state: str = "error"):
        super().__init__(message)
        self.code, self.message, self.state = code, message, state


def locate_claude(binary: str = "claude") -> str | None:
    """Find the user's CLI, including common desktop-session install paths."""
    if not isinstance(binary, str) or not binary.strip():
        return None
    try:
        if "/" in binary:
            path = Path(binary).expanduser().absolute()
            return str(path) if path.is_file() and os.access(path, os.X_OK) else None
        found = shutil.which(binary)
        if found:
            return found
        if binary == "claude":
            for path in (Path.home() / ".local/bin/claude",
                         Path.home() / ".npm-global/bin/claude",
                         Path("/usr/local/bin/claude")):
                if path.is_file() and os.access(path, os.X_OK):
                    return str(path)
    except (OSError, ValueError, RuntimeError):
        pass
    return None


def _text(value, limit: int = 320) -> str:
    return value.strip() if isinstance(value, str) and len(value) <= limit else ""


def _reset(value) -> float | None:
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return timestamp(parsed.timestamp())
    except (ValueError, OverflowError, OSError):
        return None


def _windows(limits: dict) -> tuple[list[dict], bool]:
    windows, invalid = [], False
    for slot, kind, label, minutes in (
        ("seven_day", "weekly", "Weekly limit · all models", 10080),
        ("five_hour", "short", "5-hour limit", 300),
    ):
        raw = limits.get(slot)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            invalid = True
            continue
        used = number(raw.get("utilization"), maximum=100)
        reset = _reset(raw.get("resets_at"))
        if used is None or (raw.get("resets_at") is not None and reset is None):
            invalid = True
            continue
        windows.append({
            "key": "claude:" + slot, "label": label, "bucket": "claude",
            "kind": kind, "used_percent": used, "resets_at": reset,
            "duration_minutes": minutes,
        })
    return windows, invalid


def _read_metadata(path: Path) -> dict:
    """Read only allowlisted account/usage metadata, never credential files.

    The global .claude.json can also contain unrelated private preferences.
    They are discarded immediately and never returned, logged, or persisted.
    A symlink is refused so this read cannot be redirected to a token file.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_METADATA_BYTES:
                return {"status": "unreadable"}
            raw = stream.read(MAX_METADATA_BYTES + 1)
        if len(raw) > MAX_METADATA_BYTES:
            return {"status": "unreadable"}
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {"status": "invalid"}
        account = data.get("oauthAccount")
        cached = data.get("cachedUsageUtilization")
        account = account if isinstance(account, dict) else {}
        cached = cached if isinstance(cached, dict) else {}
        usage = cached.get("utilization")
        windows, invalid = _windows(usage) if isinstance(usage, dict) else ([], True)
        milliseconds = number(cached.get("fetchedAtMs"), minimum=1,
                              maximum=253402300799000)
        return {
            "status": "ok",
            "account_uuid": _text(account.get("accountUuid"), 200),
            "email": _text(account.get("emailAddress")).casefold(),
            "organizations": tuple(filter(None, (
                _text(account.get("organizationUuid"), 200),
                _text(account.get("organizationName"), 200),
            ))),
            "cache_account_uuid": _text(cached.get("accountUuid"), 200),
            "observed_at": milliseconds / 1000 if milliseconds is not None else None,
            "windows": windows if not invalid else [],
        }
    except FileNotFoundError:
        return {"status": "missing"}
    except (OSError, ValueError, UnicodeError, RecursionError, OverflowError):
        return {"status": "unreadable"}


def _profile(config: Path, explicit: bool) -> tuple[dict, Path, bool]:
    env = os.environ.copy()
    default = Path.home() / ".claude"
    if not explicit:
        env.pop("CLAUDE_CONFIG_DIR", None)
        return env, Path.home() / ".claude.json", False
    env["CLAUDE_CONFIG_DIR"] = str(config)
    metadata_path = config / ".claude.json"
    if config == default:
        configured = _read_metadata(metadata_path)
        if (configured.get("status") in ("ok", "missing")
                and not configured.get("account_uuid")):
            original = Path.home() / ".claude.json"
            if _read_metadata(original).get("account_uuid"):
                # Older widget launchers pinned the default directory, which
                # changes Claude's global metadata location. Keep its original
                # login profile without copying or editing either file.
                env.pop("CLAUDE_CONFIG_DIR", None)
                return env, original, True
    return env, metadata_path, False


def _account_key(account: dict, config: Path) -> str:
    email = _text(account.get("email")).casefold()
    organization = _text(account.get("organization"), 200)
    if not email and not organization:
        return ""
    return identity_hash({"provider": "claude", "config": str(config),
                          "email": email, "organization": organization,
                          "api_provider": _text(account.get("apiProvider"), 40)})


def _base(account: dict, config: Path, now: float) -> dict:
    result = snapshot("claude")
    result.update(source_kind="direct", source="Claude Code usage query",
                  account_label="Claude subscription", account_key=_account_key(account, config),
                  received_at=now)
    return result


def _cache_time(metadata: dict, account: dict, windows: list[dict], now: float) -> float | None:
    email = _text(account.get("email")).casefold()
    if (not email or email != metadata.get("email")
            or not metadata.get("account_uuid")
            or metadata.get("account_uuid") != metadata.get("cache_account_uuid")
            or metadata.get("windows") != windows):
        return None
    organization = _text(account.get("organization"), 200)
    if organization and organization not in metadata.get("organizations", ()):
        return None
    observed = timestamp(metadata.get("observed_at"))
    if observed is None or observed > now + 5:
        return None
    return min(observed, now)


def _identity_conflict(metadata: dict, account: dict, windows: list[dict]) -> bool:
    """A known account conflict is different from unavailable cache metadata."""
    email = _text(account.get("email")).casefold()
    stored_email = metadata.get("email")
    if email and stored_email and email != stored_email:
        return True
    organization = _text(account.get("organization"), 200)
    organizations = metadata.get("organizations", ())
    if organization and organizations and organization not in organizations:
        return True
    # If the returned numbers are precisely a different account's recorded
    # cache, do not rescue them as fresh just because a limits[] field exists.
    current_uuid, cached_uuid = metadata.get("account_uuid"), metadata.get("cache_account_uuid")
    return bool(current_uuid and cached_uuid and current_uuid != cached_uuid
                and metadata.get("windows") == windows)


def _live_rows(limits: dict, windows: list[dict]) -> bool:
    """The tested CLI drops server limits[] when returning seeded fallback."""
    rows = limits.get("limits")
    if not isinstance(rows, list) or len(rows) > 128:
        return False
    weekly = next((window for window in windows if window["kind"] == "weekly"), None)
    matches = [row for row in rows if isinstance(row, dict) and row.get("kind") == "weekly_all"]
    if weekly is None or len(matches) != 1:
        return False
    row = matches[0]
    used, reset = number(row.get("percent"), maximum=100), _reset(row.get("resets_at"))
    return bool(
        used is not None and used == weekly["used_percent"]
        and (row.get("resets_at") is None or reset is not None)
        and reset == weekly["resets_at"]
        and isinstance(row.get("severity"), str)
        and isinstance(row.get("is_active"), bool)
    )


def _normalize(payload: dict, account: dict, config: Path, metadata: dict, now: float) -> dict:
    result = _base(account, config, now)
    available = payload.get("rate_limits_available")
    subscription = payload.get("subscription_type")
    if not isinstance(available, bool) or not (subscription is None or isinstance(subscription, str)):
        raise _Failure("invalid_schema", "The Claude usage response format has changed or is invalid.")
    plan = subscription.lower() if subscription in ("pro", "max", "team", "enterprise") else None
    result.update(rate_limits_available=available, subscription_type=plan)
    if plan:
        result["account_label"] = "Claude " + ("Max" if plan == "max" else plan.title())
    if not available:
        provider = _text(account.get("apiProvider"), 40)
        api_key = _text(account.get("apiKeySource"), 80)
        known_subscription = plan or any(word in _text(account.get("subscriptionType"), 80).lower()
                                         for word in ("pro", "max", "team", "enterprise"))
        if provider not in ("", "firstParty") or api_key not in ("", "none"):
            code, state = "subscription_unsupported", "unsupported"
            message = "The current Claude sign-in method does not provide weekly subscription limits. Check the account signed in to Claude Code."
        elif known_subscription:
            code, state = "scope_missing", "unsupported"
            message = "A Claude subscription was found, but usage access was not granted. Check your sign-in status in Claude Code."
        elif _text(account.get("email")) or _text(account.get("organization")):
            code, state = "subscription_unsupported", "unsupported"
            message = "The current account does not provide Claude weekly subscription limits. Check that you are signed in with a subscription account."
        else:
            code, state = "login_required", "waiting"
            message = "Sign in to Claude Code with claude auth login in a terminal, then refresh."
        result.update(state=state, message=message, error_code=code,
                      clear_cache=True, retry_after=60)
        return result
    limits = payload.get("rate_limits")
    if limits is None:
        result.update(state="error", error_code="usage_unavailable", retry_after=60,
                      message="Claude did not return usage data. Check your sign-in status or temporary query limits.")
        return result
    if not isinstance(limits, dict):
        raise _Failure("invalid_schema", "Cannot read the Claude usage response format.")
    windows, invalid = _windows(limits)
    result["windows"] = windows
    weekly = next((window for window in windows if window["kind"] == "weekly"), None)
    if weekly is None:
        result.update(state="error" if invalid else "waiting", retry_after=60,
                      error_code="invalid_schema" if invalid else "weekly_missing",
                      message="The Claude response has no valid weekly usage for all models. Missing values are not treated as 0%.")
        return result
    if _identity_conflict(metadata, account, windows):
        result.update(state="error", windows=[], clear_cache=True, retry_after=60,
                      error_code="account_changed",
                      message="The Claude account changed during the query. The next query will check the signed-in account again.")
        return result
    observed = _cache_time(metadata, account, windows, now)
    freshness_source = "cli_cache_timestamp" if observed is not None else "unknown"
    if observed is None and _live_rows(limits, windows):
        observed, freshness_source = now, "cli_live_rows"
    result.update(observed_at=observed, freshness_source=freshness_source)
    if weekly["resets_at"] is not None and weekly["resets_at"] <= now:
        result.update(state="stale", error_code="weekly_expired", retry_after=60,
                      message="This is a previous value whose scheduled weekly reset time has passed. Waiting for a new usage query.")
    elif observed is None or now - observed > CACHE_FRESH_SECONDS:
        result.update(state="stale", error_code="usage_stale", retry_after=60,
                      message="This is previous Claude Code usage. A fresh observation could not be verified; waiting for the next query.")
    else:
        result.update(state="ok", message="Usage reported by Claude Code. The CLI may include its recent 1-minute cache.")
        if invalid:
            result["message"] += " Some other limit fields could not be read."
    return result


class _PipeReader:
    def __init__(self, proc, stopped: threading.Event, max_buffer=MAX_MESSAGE_BYTES):
        self.proc, self.stopped = proc, stopped
        self.buffer = bytearray()
        self.total, self.messages = 0, 0
        self.stdout_eof = False
        self.max_buffer = max_buffer
        self.selector = selectors.DefaultSelector()
        self.selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
        self.selector.register(proc.stderr, selectors.EVENT_READ, "stderr")

    def close(self):
        self.selector.close()

    def pump(self, deadline):
        if self.stopped.is_set():
            raise _Failure("cancelled", "Claude usage query stopped.")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _Failure("timeout", "The Claude usage query timed out. It will retry on the next poll.")
        for key, _ in self.selector.select(min(remaining, 0.1)):
            try:
                chunk = os.read(key.fileobj.fileno(), 65536)
            except (OSError, ValueError) as exc:
                raise _Failure("connection_closed", "The Claude query connection closed.") from exc
            if not chunk:
                self.selector.unregister(key.fileobj)
                if key.data == "stdout":
                    self.stdout_eof = True
                continue
            self.total += len(chunk)
            if self.total > MAX_OUTPUT_BYTES:
                raise _Failure("output_limit", "The Claude response exceeded the size limit, so the query was stopped.")
            if key.data == "stdout":
                self.buffer.extend(chunk)
                first_line = self.buffer.find(b"\n")
                if ((first_line < 0 and len(self.buffer) > self.max_buffer)
                        or first_line > self.max_buffer):
                    raise _Failure("output_limit", "The Claude response exceeded the size limit, so the query was stopped.")

    def next_json(self, deadline) -> dict:
        while True:
            if self.stopped.is_set():
                raise _Failure("cancelled", "Claude usage query stopped.")
            end = self.buffer.find(b"\n")
            if end >= 0 or (self.stdout_eof and self.buffer):
                if end < 0:
                    end = len(self.buffer)
                if end > self.max_buffer:
                    raise _Failure("output_limit", "The Claude response exceeded the size limit.")
                line = bytes(self.buffer[:end])
                del self.buffer[:end + 1]
                if not line.strip():
                    continue
                self.messages += 1
                if self.messages > MAX_MESSAGES:
                    raise _Failure("output_limit", "Claude returned more responses than allowed.")
                try:
                    value = json.loads(line)
                except (ValueError, UnicodeError, RecursionError) as exc:
                    raise _Failure("invalid_schema", "Cannot read the structured Claude response.") from exc
                if not isinstance(value, dict):
                    raise _Failure("invalid_schema", "The structured Claude response format is invalid.")
                return value
            if self.stdout_eof:
                raise _Failure("connection_closed", "Claude closed the connection before returning usage data.")
            self.pump(deadline)


class ClaudeProvider:
    def __init__(self, binary="claude", timeout=25, config_dir=None, cwd=None, command=None):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        if command is not None and (not isinstance(command, (list, tuple)) or not command
                                    or not all(isinstance(part, str) for part in command)):
            raise ValueError("command must be an argument list, never shell text")
        self.binary, self.timeout = binary, float(timeout)
        self.config_dir, self.cwd = config_dir, cwd
        self.command = list(command) if command is not None else None
        self.proc = None
        self.stopped = threading.Event()
        self._fetch_lock, self._proc_lock = threading.RLock(), threading.Lock()

    @staticmethod
    def _signal_group(proc, sig):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _cleanup(self, proc, graceful=False):
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except (OSError, ValueError):
            pass
        if graceful and not self.stopped.is_set():
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
        self._signal_group(proc, signal.SIGTERM)
        try:
            proc.wait(timeout=0.25)
        except subprocess.TimeoutExpired:
            pass
        # Kill the group even when its parent exited: a hook's child can still
        # hold pipes open or ignore TERM. This provider never leaves a daemon.
        self._signal_group(proc, signal.SIGKILL)
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream:
                    stream.close()
            except (OSError, ValueError):
                pass
        with self._proc_lock:
            if self.proc is proc:
                self.proc = None

    def _spawn(self, command, directory, env):
        if self.stopped.is_set():
            raise _Failure("cancelled", "Claude usage query stopped.")
        try:
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, bufsize=0, cwd=directory,
                                    env=env, start_new_session=True)
        except (OSError, ValueError) as exc:
            raise _Failure("launch_failed", "Could not start the Claude CLI. Check the installation and executable path.") from exc
        with self._proc_lock:
            self.proc = proc
        if self.stopped.is_set():
            self._signal_group(proc, signal.SIGTERM)
            raise _Failure("cancelled", "Claude usage query stopped.")
        return proc

    def _version(self, executable, directory, env, deadline):
        proc = self._spawn([executable, "--version"], directory, env)
        reader = _PipeReader(proc, self.stopped, max_buffer=4096)
        try:
            version_deadline = min(deadline, time.monotonic() + 5)
            while not reader.stdout_eof or proc.poll() is None:
                reader.pump(version_deadline)
                if len(reader.buffer) > 4096:
                    raise _Failure("output_limit", "The Claude version response exceeded the size limit.")
            match = re.match(rb"\s*(\d{1,5})\.(\d{1,5})\.(\d{1,5})(?:\s|$)", bytes(reader.buffer))
            if proc.returncode != 0 or match is None:
                raise _Failure("version_unknown", "Cannot determine the Claude Code version. Check the CLI installation.", "unsupported")
            version = tuple(int(part) for part in match.groups())
            if version < MIN_CLAUDE_VERSION:
                raise _Failure("version_unsupported", "Independent usage queries require Claude Code 2.1.306 or later. Update Claude Code.", "unsupported")
            return ".".join(str(part) for part in version)
        finally:
            reader.close()
            self._cleanup(proc)

    def _send(self, proc, message):
        if self.stopped.is_set():
            raise _Failure("cancelled", "Claude usage query stopped.")
        try:
            encoded = (json.dumps(message, allow_nan=False, separators=(",", ":")) + "\n").encode()
            proc.stdin.write(encoded)
            proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise _Failure("connection_closed", "Could not send the usage query to Claude.") from exc

    def _call(self, proc, reader, request_id, request, deadline):
        self._send(proc, {"type": "control_request", "request_id": request_id, "request": request})
        while True:
            message = reader.next_json(deadline)
            kind = message.get("type")
            if kind == "control_request":
                incoming_id = message.get("request_id")
                if isinstance(incoming_id, str) and len(incoming_id) <= 128:
                    self._send(proc, {"type": "control_response", "response": {
                        "subtype": "error", "request_id": incoming_id,
                        "error": "Quota Dock only reads usage and cannot approve requests.",
                    }})
                raise _Failure("approval_required", "Claude requested approval, so the usage query was stopped. Check the status in Claude Code.")
            if kind == "control_response":
                response = message.get("response")
                if not isinstance(response, dict):
                    raise _Failure("invalid_schema", "Cannot read the Claude control response format.")
                if response.get("request_id") != request_id:
                    continue
                if response.get("subtype") != "success":
                    raise _Failure("query_rejected", "Claude could not process the usage query. Check the CLI version and sign-in status.")
                payload = response.get("response")
                if not isinstance(payload, dict):
                    raise _Failure("invalid_schema", "Cannot read the Claude usage response format.")
                return payload
            if kind in ("assistant", "user", "stream_event", "result"):
                raise _Failure("unexpected_turn", "An unexpected Claude response outside the usage query was detected. The connection was closed.")
            if kind not in ("system", "keep_alive", "auth_status", "rate_limit_event"):
                raise _Failure("invalid_schema", "An unsupported Claude response was received, so the query was stopped.")

    def fetch(self) -> dict:
        """Return a sanitized snapshot; errors are values, never raw CLI text."""
        with self._fetch_lock:
            account, version, compatible = {}, None, False
            config = claude_config_dir(self.config_dir)
            proc, reader, completed = None, None, False
            try:
                if self.stopped.is_set():
                    raise _Failure("cancelled", "Claude usage query stopped.")
                deadline = time.monotonic() + self.timeout
                directory = self.cwd or private_directory(state_dir() / "claude-runtime")
                explicit = self.config_dir is not None or bool(os.environ.get("CLAUDE_CONFIG_DIR"))
                env, metadata_path, compatible = _profile(config, explicit)
                command = self.command
                if command is None:
                    executable = locate_claude(self.binary)
                    if executable is None:
                        raise _Failure("cli_not_found", "Claude Code was not found. Install the CLI, sign in, and refresh.", "waiting")
                    version = self._version(executable, directory, env, deadline)
                    command = [executable, *_FLAGS]
                proc = self._spawn(command, directory, env)
                reader = _PipeReader(proc, self.stopped)
                initialized = self._call(proc, reader, "quota-widget-init", {"subtype": "initialize"}, deadline)
                raw_account = initialized.get("account")
                account = raw_account if isinstance(raw_account, dict) else {}
                payload = self._call(proc, reader, "quota-widget-usage",
                                     {"subtype": "get_usage", "skip_behaviors": True}, deadline)
                result = _normalize(payload, account, config, _read_metadata(metadata_path), time.time())
                completed = True
            except _Failure as exc:
                result = _base(account, config, time.time())
                result.update(state=exc.state, error_code=exc.code, message=exc.message, retry_after=60)
            except Exception:
                result = _base(account, config, time.time())
                result.update(state="error", error_code="read_failed", retry_after=60,
                              message="Could not read Claude usage. It will retry on the next poll.")
            finally:
                if reader is not None:
                    reader.close()
                # _spawn can publish a process just before cancellation.
                self._cleanup(proc if proc is not None else self.proc, graceful=completed)
            if version is not None:
                result["cli_version"] = version
            if compatible:
                result["default_profile_compatibility"] = True
            return result

    def abort(self):
        """Permanently cancel this provider without waiting for its fetch lock."""
        self.stopped.set()
        with self._proc_lock:
            proc = self.proc
        if proc is not None:
            self._signal_group(proc, signal.SIGTERM)

    def close(self):
        self.abort()
        with self._fetch_lock:
            self._cleanup(self.proc)
