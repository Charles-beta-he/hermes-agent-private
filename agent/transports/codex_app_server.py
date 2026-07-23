"""Codex app-server JSON-RPC client.

Speaks the protocol documented in codex-rs/app-server/README.md (codex 0.125+).
Transport is newline-delimited JSON-RPC 2.0 over stdio: spawn `codex app-server`,
do an `initialize` handshake, then drive `thread/start` + `turn/start` and
consume streaming `item/*` notifications until `turn/completed`.

This module is the wire-level speaker only. Higher-level concerns (event
projection into Hermes' display, approval bridging, transcript projection into
AIAgent.messages, plugin migration) live in sibling modules.

Status: optional opt-in runtime gated behind `model.openai_runtime ==
"codex_app_server"`. Hermes' default tool dispatch is unchanged when this
runtime is not selected.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from tools.environments.local import hermes_subprocess_env

# Default minimum codex version we test against. The PR sets this from the
# `codex --version` parsed at install time; bumping is a one-line change here.
MIN_CODEX_VERSION = (0, 125, 0)


@dataclass
class CodexAppServerError(RuntimeError):
    """Raised on JSON-RPC errors from the app-server."""

    code: int
    message: str
    data: Optional[Any] = None

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"codex app-server error {self.code}: {self.message}"


@dataclass
class _Pending:
    queue: queue.Queue
    method: str
    sent_at: float = field(default_factory=time.time)


_PENDING_CLOSE_CLIENTS: set[Any] = set()
_LIVE_CODEX_CLIENTS: set[Any] = set()
_PENDING_CLOSE_LOCK = threading.Lock()


def retry_pending_codex_clients() -> int:
    """Retry unconfirmed provider shutdowns retained by the module backstop."""
    with _PENDING_CLOSE_LOCK:
        pending = list(_PENDING_CLOSE_CLIENTS)
    closed = 0
    for client in pending:
        try:
            if client.close():
                closed += 1
        except Exception:
            pass
    return closed


def close_owned_codex_clients_at_exit() -> int:
    """Close every process-owning client, including normally live clients."""
    with _PENDING_CLOSE_LOCK:
        owned = list(_LIVE_CODEX_CLIENTS | _PENDING_CLOSE_CLIENTS)
    closed = 0
    for client in owned:
        try:
            if client.close():
                closed += 1
        except Exception:
            pass
    return closed


atexit.register(close_owned_codex_clients_at_exit)


class CodexAppServerClient:
    """Minimal JSON-RPC 2.0 client for `codex app-server` over stdio.

    Threading model:
      - Spawning thread (caller) drives request/response pairs synchronously.
      - One reader thread parses stdout, dispatches replies to the right
        pending future, and routes notifications + server-initiated requests
        to bounded queues that the caller drains on their own cadence.
      - One reader thread captures stderr for diagnostics; codex emits
        tracing logs there at RUST_LOG-controlled levels.

    Intentionally NOT async. AIAgent.run_conversation() is synchronous and
    runs on the main thread; layering asyncio just to drive a stdio child
    creates surprising interrupt semantics. We use blocking queues with
    timeouts and rely on `turn/interrupt` for cancellation.
    """

    def __init__(
        self,
        codex_bin: str = "codex",
        codex_home: Optional[str] = None,
        extra_args: Optional[list[str]] = None,
        env: Optional[dict[str, str]] = None,
    ) -> None:
        # A long-running gateway may evict the prior owner after a transient
        # kill failure. Retry module-retained handles before spawning another
        # provider process; atexit performs the same retry at process shutdown.
        retry_pending_codex_clients()
        self._codex_bin = codex_bin
        # codex app-server is a model-driving CLI executor: it runs a
        # model-chosen agentic loop that executes shell commands, so it
        # legitimately needs LLM provider credentials (inherit_credentials=True)
        # to authenticate against the model endpoint. But the previous
        # `os.environ.copy()` also handed it every Tier-1 Hermes secret — gateway
        # bot tokens, GitHub auth, Modal/Daytona infra tokens, the dashboard
        # session token, AUXILIARY_* side-LLM keys, GATEWAY_RELAY_* auth — none
        # of which a coding subprocess has any use for. Route through the
        # centralized helper so Tier-1 + dynamic-internal secrets are always
        # stripped while provider creds still flow, matching copilot_acp_client
        # (#29157 sibling spawn-site gap).
        spawn_env = hermes_subprocess_env(inherit_credentials=True)
        if env:
            spawn_env.update(env)
        if codex_home:
            spawn_env["CODEX_HOME"] = codex_home

        app_server_args = list(extra_args or [])
        # Kanban workers must be able to write their handoff/status back to
        # the board DB, which lives outside the per-task workspace. Keep the
        # Codex sandbox on, but add the Kanban root as the only extra writable
        # root. Without this, codex-runtime workers finish their actual work
        # but crash/block when kanban_complete/kanban_block writes SQLite.
        if spawn_env.get("HERMES_KANBAN_TASK"):
            kanban_db = spawn_env.get("HERMES_KANBAN_DB")
            kanban_root = (
                os.path.dirname(kanban_db)
                if kanban_db
                else spawn_env.get(
                    "HERMES_KANBAN_ROOT",
                    os.path.join(
                        spawn_env.get("HERMES_HOME", os.path.expanduser("~/.hermes")),
                        "kanban",
                    ),
                )
            )
            app_server_args.extend(
                [
                    "-c",
                    'sandbox_mode="workspace-write"',
                    "-c",
                    f'sandbox_workspace_write.writable_roots=["{kanban_root}"]',
                    "-c",
                    "sandbox_workspace_write.network_access=false",
                ]
            )

        cmd = [codex_bin, "app-server"] + app_server_args
        # Codex emits tracing to stderr; default WARN keeps it quiet for users.
        spawn_env.setdefault("RUST_LOG", "warn")

        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=spawn_env,
            start_new_session=True,
        )
        self._process_group_id = self._proc.pid if os.name != "nt" else None
        self._next_id = 1
        self._pending: dict[int, _Pending] = {}
        self._pending_lock = threading.Lock()
        self._notifications: queue.Queue = queue.Queue()
        self._server_requests: queue.Queue = queue.Queue()
        self._stderr_lines: list[str] = []
        self._stderr_lock = threading.Lock()
        self._close_lock = threading.RLock()
        self._closed = False
        self._initialized = False
        with _PENDING_CLOSE_LOCK:
            _LIVE_CODEX_CLIENTS.add(self)

        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._stderr_reader.start()

    # ---------- lifecycle ----------

    def initialize(
        self,
        client_name: str = "hermes",
        client_title: str = "Hermes Agent",
        client_version: str = "0.1",
        capabilities: Optional[dict] = None,
        timeout: float = 10.0,
    ) -> dict:
        """Send `initialize` + `initialized` handshake. Returns the server's
        InitializeResponse (userAgent, codexHome, platformFamily, platformOs)."""
        if self._initialized:
            raise RuntimeError("already initialized")
        params = {
            "clientInfo": {
                "name": client_name,
                "title": client_title,
                "version": client_version,
            },
            "capabilities": capabilities or {},
        }
        result = self.request("initialize", params, timeout=timeout)
        self.notify("initialized")
        self._initialized = True
        return result

    def close(self, timeout: float = 3.0) -> bool:
        """Serialize teardown so concurrent retry/backstop callers are idempotent."""
        close_lock = getattr(self, "_close_lock", None)
        if close_lock is None:
            # Tests and recovery code may reconstruct an owned client via
            # ``__new__``. Initialize the per-client lock once under the module
            # registry lock so concurrent first close callers cannot diverge.
            with _PENDING_CLOSE_LOCK:
                close_lock = getattr(self, "_close_lock", None)
                if close_lock is None:
                    close_lock = threading.RLock()
                    self._close_lock = close_lock
        with close_lock:
            return self._close_locked(timeout)

    def _close_locked(self, timeout: float = 3.0) -> bool:
        """Close the owned process group and confirm its leader exited.

        A failed escalation leaves the client retryable so its owning agent can
        retain the handle and attempt teardown again.
        """

        def _mark_closed() -> bool:
            self._closed = True
            with _PENDING_CLOSE_LOCK:
                _PENDING_CLOSE_CLIENTS.discard(self)
                _LIVE_CODEX_CLIENTS.discard(self)
            return True

        def _has_exited() -> bool:
            try:
                return self._proc.poll() is not None
            except Exception:
                return False

        raw_process_group_id = getattr(self, "_process_group_id", None)
        process_group_id = (
            int(raw_process_group_id) if raw_process_group_id is not None else None
        )
        owns_process_group = os.name != "nt" and process_group_id is not None

        def _signal_group(sig: int) -> bool:
            if not owns_process_group:
                return False
            assert process_group_id is not None
            try:
                os.killpg(process_group_id, sig)  # windows-footgun: ok
                return True
            except ProcessLookupError:
                return True
            except Exception:
                return False

        def _group_alive() -> bool:
            if not owns_process_group:
                return False
            assert process_group_id is not None
            try:
                os.killpg(process_group_id, 0)  # windows-footgun: ok
                return True
            except ProcessLookupError:
                return False
            except Exception:
                return True

        if self._closed:
            return _mark_closed()
        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except Exception:
            pass

        leader_confirmed = _has_exited()
        if leader_confirmed:
            if not owns_process_group or not _group_alive():
                return _mark_closed()

        try:
            if owns_process_group:
                _signal_group(signal.SIGTERM)
            else:
                self._proc.terminate()
            self._proc.wait(timeout=timeout)
            leader_confirmed = True
            if not owns_process_group or not _group_alive():
                return _mark_closed()
            # The leader has been reaped, but a successful group probe proves
            # original descendants still hold this PGID. POSIX cannot recycle
            # a non-empty process group, so escalation remains ownership-safe.
        except Exception:
            if _has_exited():
                leader_confirmed = True
                if not owns_process_group or not _group_alive():
                    return _mark_closed()

        try:
            group_kill_confirmed = False
            if owns_process_group:
                group_kill_confirmed = _signal_group(
                    getattr(signal, "SIGKILL", signal.SIGTERM)
                )
            else:
                self._proc.kill()
            self._proc.wait(timeout=1.0)
            leader_confirmed = True
            if owns_process_group and group_kill_confirmed:
                # Probe for observability, but do not treat a positive result
                # as live work after the leader is reaped and the hard-kill
                # was delivered: Linux may keep killed, orphaned descendants
                # as zombies in the PGID until PID 1 reaps them.
                _group_alive()
                return _mark_closed()
        except (OSError, subprocess.TimeoutExpired):
            leader_confirmed = _has_exited()

        group_confirmed = not owns_process_group or not _group_alive()
        if leader_confirmed and group_confirmed:
            return _mark_closed()

        with _PENDING_CLOSE_LOCK:
            _PENDING_CLOSE_CLIENTS.add(self)
        return False

    def __enter__(self) -> "CodexAppServerClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ---------- send/receive ----------

    def request(
        self,
        method: str,
        params: Optional[dict] = None,
        timeout: float = 30.0,
    ) -> dict:
        """Send a JSON-RPC request and block on the response. Returns `result`,
        raises CodexAppServerError on `error`."""
        rid = self._take_id()
        q: queue.Queue = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[rid] = _Pending(queue=q, method=method)
        self._send({"id": rid, "method": method, "params": params or {}})
        try:
            msg = q.get(timeout=timeout)
        except queue.Empty:
            with self._pending_lock:
                self._pending.pop(rid, None)
            raise TimeoutError(
                f"codex app-server method {method!r} timed out after {timeout}s"
            )
        if "error" in msg:
            err = msg["error"]
            raise CodexAppServerError(
                code=err.get("code", -1),
                message=err.get("message", ""),
                data=err.get("data"),
            )
        return msg.get("result", {})

    def notify(self, method: str, params: Optional[dict] = None) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        self._send({"method": method, "params": params or {}})

    def respond(self, request_id: Any, result: dict) -> None:
        """Reply to a server-initiated request (e.g. approval prompts)."""
        self._send({"id": request_id, "result": result})

    def respond_error(
        self, request_id: Any, code: int, message: str, data: Optional[Any] = None
    ) -> None:
        """Reply to a server-initiated request with an error."""
        err: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        self._send({"id": request_id, "error": err})

    def take_notification(self, timeout: float = 0.0) -> Optional[dict]:
        """Pop the next streaming notification, or return None on timeout.

        timeout=0.0 means non-blocking. Use small positive timeouts inside the
        AIAgent turn loop to interleave reads with interrupt checks."""
        try:
            if timeout <= 0:
                return self._notifications.get_nowait()
            return self._notifications.get(timeout=timeout)
        except queue.Empty:
            return None

    def take_server_request(self, timeout: float = 0.0) -> Optional[dict]:
        """Pop the next server-initiated request (e.g. exec/applyPatch approval)."""
        try:
            if timeout <= 0:
                return self._server_requests.get_nowait()
            return self._server_requests.get(timeout=timeout)
        except queue.Empty:
            return None

    # ---------- diagnostics ----------

    def stderr_tail(self, n: int = 20) -> list[str]:
        """Return last n lines of codex's stderr (for error reports)."""
        with self._stderr_lock:
            return list(self._stderr_lines[-n:])

    def is_alive(self) -> bool:
        return self._proc.poll() is None

    # ---------- internals ----------

    def _take_id(self) -> int:
        # JSON-RPC ids only need to be unique per-connection. A simple
        # monotonically increasing int is the common choice and matches what
        # codex's own clients use.
        rid = self._next_id
        self._next_id += 1
        return rid

    def _send(self, obj: dict) -> None:
        if self._closed:
            raise RuntimeError("codex app-server client is closed")
        if self._proc.stdin is None:
            raise RuntimeError("codex app-server stdin not available")
        try:
            self._proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise RuntimeError(
                f"codex app-server stdin closed unexpectedly: {exc}"
            ) from exc

    def _read_stdout(self) -> None:
        if self._proc.stdout is None:
            return
        try:
            for line in iter(self._proc.stdout.readline, b""):
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    # Non-JSON output is unexpected on stdout; tracing belongs
                    # on stderr. Surface it via stderr buffer for diagnostics.
                    with self._stderr_lock:
                        self._stderr_lines.append(
                            f"<non-json on stdout> {line[:200]!r}"
                        )
                    continue
                self._dispatch(msg)
        except Exception as exc:
            with self._stderr_lock:
                self._stderr_lines.append(f"<stdout reader error> {exc}")

    def _dispatch(self, msg: dict) -> None:
        # Reply (has id + result/error, no method)
        if "id" in msg and ("result" in msg or "error" in msg):
            with self._pending_lock:
                pending = self._pending.pop(msg["id"], None)
            if pending is not None:
                try:
                    pending.queue.put_nowait(msg)
                except queue.Full:  # pragma: no cover - defensive
                    pass
            return
        # Server-initiated request (has id + method)
        if "id" in msg and "method" in msg:
            self._server_requests.put(msg)
            return
        # Notification (no id)
        if "method" in msg:
            self._notifications.put(msg)

    def _read_stderr(self) -> None:
        if self._proc.stderr is None:
            return
        try:
            for line in iter(self._proc.stderr.readline, b""):
                if not line:
                    break
                with self._stderr_lock:
                    self._stderr_lines.append(
                        line.decode("utf-8", "replace").rstrip()
                    )
                    # Bound memory: keep last 500 lines.
                    if len(self._stderr_lines) > 500:
                        self._stderr_lines = self._stderr_lines[-500:]
        except Exception:  # pragma: no cover
            pass


def parse_codex_version(output: str) -> Optional[tuple[int, int, int]]:
    """Parse `codex --version` output. Returns (major, minor, patch) or None."""
    # Output format: "codex-cli 0.130.0" possibly followed by metadata.
    import re

    match = re.search(r"(\d+)\.(\d+)\.(\d+)", output or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def check_codex_binary(
    codex_bin: str = "codex", min_version: tuple[int, int, int] = MIN_CODEX_VERSION
) -> tuple[bool, str]:
    """Verify codex CLI is installed and meets minimum version.

    Returns (ok, message). Used by setup wizard and runtime startup."""
    try:
        proc = subprocess.run(
            [codex_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return False, (
            f"codex CLI not found at {codex_bin!r}. Install with: "
            f"npm i -g @openai/codex"
        )
    except subprocess.TimeoutExpired:
        return False, "codex --version timed out"
    if proc.returncode != 0:
        return False, f"codex --version exited {proc.returncode}: {proc.stderr.strip()}"
    version = parse_codex_version(proc.stdout)
    if version is None:
        return False, f"could not parse codex version from: {proc.stdout!r}"
    if version < min_version:
        return False, (
            f"codex {'.'.join(map(str, version))} is older than required "
            f"{'.'.join(map(str, min_version))}. Run: npm i -g @openai/codex"
        )
    return True, ".".join(map(str, version))
