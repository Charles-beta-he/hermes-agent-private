"""Owned POSIX subprocess supervisor for durable task workers.

This module owns one process session from spawn through reap and residual-process
readback.  It deliberately does not decide whether a Task claim may be retried;
callers persist the returned receipt through the task-runtime store.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
from typing import Mapping, Sequence
import uuid

import psutil


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    create_time: float


@dataclass(frozen=True)
class ProcessGroupReceipt:
    schema: str
    supervisor_run_id: str
    leader_pid: int
    leader_start_time: float
    pgid: int
    exit_code: int
    timed_out: bool
    term_sent: bool
    kill_sent: bool
    leader_reaped: bool
    residual_probe_succeeded: bool
    residual_pids: tuple[int, ...]
    observed_descendants: tuple[ProcessIdentity, ...]
    observed_at: str

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["residual_pids"] = list(self.residual_pids)
        payload["observed_descendants"] = [
            asdict(item) for item in self.observed_descendants
        ]
        return payload


@dataclass(frozen=True)
class OwnedProcessResult:
    stdout: str
    stderr: str
    receipt: ProcessGroupReceipt


_ATTESTATION_ISSUER = object()


@dataclass(frozen=True)
class TaskCleanupBinding:
    task_id: str
    slice_id: str
    worker_session_id: str
    packet_id: str
    lease_token: str


class VerifiedTaskCleanup:
    """In-process capability minted only by the owned process controller."""

    __slots__ = ("binding", "receipt")

    def __init__(
        self,
        binding: TaskCleanupBinding,
        receipt: ProcessGroupReceipt,
        *,
        _issuer: object,
    ) -> None:
        if _issuer is not _ATTESTATION_ISSUER:
            raise TypeError("verified_cleanup_must_be_controller_issued")
        self.binding = binding
        self.receipt = receipt


@dataclass(frozen=True)
class OwnedTaskProcessResult:
    stdout: str
    stderr: str
    receipt: ProcessGroupReceipt
    cleanup: VerifiedTaskCleanup | None


def _identity(process: psutil.Process) -> ProcessIdentity | None:
    try:
        return ProcessIdentity(process.pid, process.create_time())
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
        return None


def _same_process(identity: ProcessIdentity) -> bool:
    try:
        process = psutil.Process(identity.pid)
        return (
            abs(process.create_time() - identity.create_time) < 0.001
            and process.is_running()
            and process.status() != psutil.STATUS_ZOMBIE
        )
    except (psutil.NoSuchProcess, psutil.ZombieProcess, psutil.AccessDenied):
        return False


def _bounded_file_text(handle, max_bytes: int) -> str:
    handle.flush()
    size = handle.tell()
    handle.seek(max(0, size - max_bytes))
    return handle.read(max_bytes).decode("utf-8", errors="replace")


def run_owned_process(
    command: Sequence[str],
    *,
    stdin_bytes: bytes = b"",
    cwd: str | Path,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float,
    term_grace_seconds: float = 0.5,
    max_output_bytes: int = 256_000,
) -> OwnedProcessResult:
    """Run an exact argv under a new POSIX session and prove cleanup.

    The argv must identify the target executable/module exactly; shell execution
    is intentionally unsupported.  Any uncertain residual probe is represented
    as ``residual_probe_succeeded=False`` and must be treated as quarantine.
    """
    if os.name != "posix":
        raise RuntimeError("task_runtime_process_supervisor_requires_posix")
    if not command or not str(command[0]).strip():
        raise ValueError("process_command_required")
    workspace = Path(cwd).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError("process_workspace_required")
    run_id = f"task-run-{uuid.uuid4().hex}"
    observed: dict[tuple[int, float], ProcessIdentity] = {}
    observed_lock = threading.Lock()
    stop_observer = threading.Event()
    term_sent = False
    kill_sent = False
    timed_out = False
    probe_uncertain = False

    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            [str(item) for item in command],
            cwd=str(workspace),
            env=dict(env) if env is not None else None,
            stdin=subprocess.PIPE,
            stdout=stdout_file,
            stderr=stderr_file,
            start_new_session=True,
            close_fds=True,
        )
        leader = psutil.Process(process.pid)
        leader_identity = ProcessIdentity(process.pid, leader.create_time())
        pgid = os.getpgid(process.pid)

        def observe_descendants() -> None:
            nonlocal probe_uncertain
            while not stop_observer.is_set():
                try:
                    descendants = leader.children(recursive=True)
                except (psutil.NoSuchProcess, psutil.ZombieProcess):
                    descendants = []
                except psutil.AccessDenied:
                    probe_uncertain = True
                    descendants = []
                with observed_lock:
                    for child in descendants:
                        identity = _identity(child)
                        if identity is not None:
                            observed[(identity.pid, identity.create_time)] = identity
                stop_observer.wait(0.01)

        observer = threading.Thread(
            target=observe_descendants,
            name=f"task-process-observer-{process.pid}",
            daemon=True,
        )
        observer.start()
        try:
            process.communicate(input=stdin_bytes, timeout=float(timeout_seconds))
        except subprocess.TimeoutExpired:
            timed_out = True
        except BaseException:
            timed_out = True
            raise
        finally:
            if process.poll() is None or timed_out:
                try:
                    os.killpg(pgid, signal.SIGTERM)  # windows-footgun: ok — POSIX-only supervisor
                    term_sent = True
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + max(0.0, float(term_grace_seconds))
                while process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.01)
                if process.poll() is None:
                    try:
                        os.killpg(pgid, signal.SIGKILL)  # windows-footgun: ok — POSIX-only supervisor
                        kill_sent = True
                    except ProcessLookupError:
                        pass
            # Kill every descendant identity observed before or during shutdown,
            # including descendants that escaped the process group via setsid().
            with observed_lock:
                identities = list(observed.values())
            for identity in identities:
                if _same_process(identity):
                    try:
                        os.kill(identity.pid, signal.SIGKILL)  # windows-footgun: ok — POSIX-only supervisor
                        kill_sent = True
                    except (ProcessLookupError, PermissionError):
                        if _same_process(identity):
                            probe_uncertain = True
            try:
                process.wait(timeout=max(1.0, float(term_grace_seconds) + 0.5))
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                    kill_sent = True
                except ProcessLookupError:
                    pass
                process.wait(timeout=2.0)
            stop_observer.set()
            observer.join(timeout=1.0)
            if observer.is_alive():
                probe_uncertain = True

        # Give delivered signals a bounded interval to become observable before
        # the final PID+create-time readback. Detached descendants are reaped by
        # the host init process rather than by this supervisor.
        residual_deadline = time.monotonic() + max(
            0.2, float(term_grace_seconds) + 0.5
        )
        while time.monotonic() < residual_deadline:
            with observed_lock:
                identities = list(observed.values())
            if not any(_same_process(identity) for identity in identities):
                break
            time.sleep(0.01)

        # Final process-group and PID+create-time readback after leader reap.
        residual: set[int] = set()
        with observed_lock:
            identities = list(observed.values())
        for identity in identities:
            if _same_process(identity):
                residual.add(identity.pid)
        for candidate in psutil.process_iter(["pid"]):
            try:
                if candidate.pid != os.getpid() and os.getpgid(candidate.pid) == pgid:
                    residual.add(candidate.pid)
            except (ProcessLookupError, PermissionError, psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        leader_reaped = process.poll() is not None
        if _same_process(leader_identity):
            residual.add(leader_identity.pid)
            leader_reaped = False
        receipt = ProcessGroupReceipt(
            schema="hermes_task_process_receipt_v1",
            supervisor_run_id=run_id,
            leader_pid=leader_identity.pid,
            leader_start_time=leader_identity.create_time,
            pgid=pgid,
            exit_code=int(process.returncode if process.returncode is not None else -999),
            timed_out=timed_out,
            term_sent=term_sent,
            kill_sent=kill_sent,
            leader_reaped=leader_reaped,
            residual_probe_succeeded=not probe_uncertain and not residual and leader_reaped,
            residual_pids=tuple(sorted(residual)),
            observed_descendants=tuple(
                sorted(identities, key=lambda item: (item.pid, item.create_time))
            ),
            observed_at=datetime.now(timezone.utc).isoformat(),
        )
        return OwnedProcessResult(
            stdout=_bounded_file_text(stdout_file, int(max_output_bytes)),
            stderr=_bounded_file_text(stderr_file, int(max_output_bytes)),
            receipt=receipt,
        )


def run_owned_task_process(
    command: Sequence[str],
    *,
    binding: TaskCleanupBinding,
    stdin_bytes: bytes = b"",
    cwd: str | Path,
    env: Mapping[str, str] | None = None,
    timeout_seconds: float,
    term_grace_seconds: float = 0.5,
    max_output_bytes: int = 256_000,
) -> OwnedTaskProcessResult:
    result = run_owned_process(
        command,
        stdin_bytes=stdin_bytes,
        cwd=cwd,
        env=env,
        timeout_seconds=timeout_seconds,
        term_grace_seconds=term_grace_seconds,
        max_output_bytes=max_output_bytes,
    )
    receipt = result.receipt
    cleanup = None
    if (
        receipt.leader_reaped
        and receipt.residual_probe_succeeded
        and not receipt.residual_pids
    ):
        cleanup = VerifiedTaskCleanup(
            binding,
            receipt,
            _issuer=_ATTESTATION_ISSUER,
        )
    return OwnedTaskProcessResult(
        stdout=result.stdout,
        stderr=result.stderr,
        receipt=receipt,
        cleanup=cleanup,
    )
