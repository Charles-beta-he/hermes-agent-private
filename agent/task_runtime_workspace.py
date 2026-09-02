"""Managed linked-worktree allocation, renewable ownership, and safe reaping."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import io
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import time
from typing import Iterator
from contextlib import contextmanager

import psutil

from agent.task_runtime_process import ProcessGroupReceipt, ProcessIdentity

_MARKER_SCHEMA = "hermes_task_workspace_lease_v1"


@dataclass(frozen=True)
class WorkspaceLease:
    schema: str
    task_id: str
    slice_id: str
    packet_id: str
    lease_token: str
    source_repo: str
    workspace_path: str
    base_commit: str
    created_at: str
    expires_at: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class WorkspaceCandidateReceipt:
    schema: str
    task_id: str
    slice_id: str
    packet_id: str
    lease_token: str
    source_repo: str
    base_commit: str
    candidate_digest: str
    changed_artifacts: tuple[str, ...]
    archive_path: str
    archive_sha256: str
    workspace_reap_receipt: dict
    observed_at: str

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["changed_artifacts"] = list(self.changed_artifacts)
        return payload


@dataclass(frozen=True)
class WorkspaceReapReceipt:
    schema: str
    packet_id: str
    lease_token: str
    workspace_path: str
    removed: bool
    residual_probe_succeeded: bool
    residual_writer_pids: tuple[int, ...]
    observed_at: str

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["residual_writer_pids"] = list(self.residual_writer_pids)
        return payload


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")[:36]
    return slug or "item"


def _run_git(repo: Path, *args: str, timeout: float = 30) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("workspace_git_operation_failed") from exc
    return completed.stdout.strip()


def _marker_path(root: Path, name: str) -> Path:
    return root / "leases" / f"{name}.json"


def _lease_name(task_id: str, slice_id: str, packet_id: str) -> str:
    digest = hashlib.sha256(packet_id.encode("utf-8")).hexdigest()[:16]
    return f"{_slug(task_id)}--{_slug(slice_id)}--{digest}"


@contextmanager
def _allocator_lock(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".allocator.lock"
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _candidate_lock(root: Path, name: str) -> Iterator[None]:
    lock_dir = root / "candidate-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{name}.lock"
    with lock_path.open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_lease(path: Path, lease: WorkspaceLease) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".tmp-{os.getpid()}")
    payload = json.dumps(lease.to_dict(), sort_keys=True, separators=(",", ":"))
    temporary.write_text(payload, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _read_lease(path: Path) -> WorkspaceLease:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("workspace_lease_invalid") from exc
    allowed = set(WorkspaceLease.__dataclass_fields__)
    if not isinstance(payload, dict) or set(payload) != allowed:
        raise ValueError("workspace_lease_invalid")
    lease = WorkspaceLease(**payload)
    if lease.schema != _MARKER_SCHEMA:
        raise ValueError("workspace_lease_schema_invalid")
    return lease


def allocate_workspace(
    *,
    managed_root: str | Path,
    source_repo: str | Path,
    task_id: str,
    slice_id: str,
    packet_id: str,
    lease_token: str,
    base_commit: str,
    lease_seconds: int,
    now: float | None = None,
) -> WorkspaceLease:
    root = Path(managed_root).expanduser().resolve()
    source = Path(source_repo).expanduser().resolve()
    if not source.is_dir() or not (_run_git(source, "rev-parse", "--is-inside-work-tree") == "true"):
        raise ValueError("workspace_source_repo_invalid")
    resolved_commit = _run_git(source, "rev-parse", f"{base_commit}^{{commit}}")
    if resolved_commit != base_commit:
        raise ValueError("workspace_base_commit_not_exact")
    name = _lease_name(task_id, slice_id, packet_id)
    workspace = (root / "worktrees" / name).resolve()
    marker = _marker_path(root, name)
    expected_parent = (root / "worktrees").resolve()
    if workspace.parent != expected_parent:
        raise ValueError("workspace_path_outside_managed_root")
    now_value = time.time() if now is None else float(now)
    with _allocator_lock(root):
        if marker.exists():
            existing = _read_lease(marker)
            expected = (packet_id, lease_token, str(source), base_commit, str(workspace))
            actual = (
                existing.packet_id,
                existing.lease_token,
                existing.source_repo,
                existing.base_commit,
                existing.workspace_path,
            )
            if actual != expected or not workspace.is_dir():
                raise ValueError("workspace_lease_conflict")
            return existing
        if workspace.exists():
            raise ValueError("workspace_path_unowned")
        workspace.parent.mkdir(parents=True, exist_ok=True)
        _run_git(source, "worktree", "add", "--detach", str(workspace), base_commit)
        lease = WorkspaceLease(
            schema=_MARKER_SCHEMA,
            task_id=task_id,
            slice_id=slice_id,
            packet_id=packet_id,
            lease_token=lease_token,
            source_repo=str(source),
            workspace_path=str(workspace),
            base_commit=base_commit,
            created_at=datetime.now(timezone.utc).isoformat(),
            expires_at=now_value + int(lease_seconds),
        )
        try:
            _write_lease(marker, lease)
        except BaseException:
            _run_git(source, "worktree", "remove", "--force", str(workspace))
            raise
        return lease


def renew_workspace_lease(
    lease: WorkspaceLease,
    *,
    managed_root: str | Path,
    lease_seconds: int,
    now: float | None = None,
) -> WorkspaceLease:
    root = Path(managed_root).expanduser().resolve()
    name = _lease_name(lease.task_id, lease.slice_id, lease.packet_id)
    marker = _marker_path(root, name)
    with _allocator_lock(root):
        current = _read_lease(marker)
        if current != lease:
            raise ValueError("workspace_lease_fence_mismatch")
        renewed = WorkspaceLease(
            **{
                **lease.to_dict(),
                "expires_at": (time.time() if now is None else float(now))
                + int(lease_seconds),
            }
        )
        _write_lease(marker, renewed)
        return renewed


def residual_workspace_writers(
    workspace_path: str | Path,
    identities: tuple[ProcessIdentity, ...],
) -> tuple[bool, tuple[int, ...]]:
    """Probe only the supervisor-owned PID identities, never unrelated host PIDs."""
    workspace = Path(workspace_path).resolve()
    writers: set[int] = set()
    certain = True
    for identity in identities:
        try:
            process = psutil.Process(identity.pid)
            if abs(process.create_time() - identity.create_time) >= 0.001:
                continue
            if process.status() == psutil.STATUS_ZOMBIE:
                continue
            paths: list[Path] = []
            try:
                paths.append(Path(process.cwd()).resolve())
            except (FileNotFoundError, psutil.ZombieProcess):
                pass
            paths.extend(Path(item.path).resolve() for item in process.open_files())
            if any(path == workspace or workspace in path.parents for path in paths):
                writers.add(process.pid)
        except (psutil.NoSuchProcess, psutil.ZombieProcess, FileNotFoundError):
            continue
        except (psutil.AccessDenied, PermissionError, OSError):
            certain = False
    return certain, tuple(sorted(writers))


def workspace_changed_paths(workspace_path: str | Path) -> set[str]:
    workspace = Path(workspace_path).resolve()
    try:
        completed = subprocess.run(
            [
                "git", "-C", str(workspace), "status", "--porcelain=v1", "-z",
                "--untracked-files=all",
            ],
            check=True,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("workspace_git_operation_failed") from exc
    fields = completed.stdout.split(b"\0")
    paths: set[str] = set()
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if not field:
            continue
        if len(field) < 4 or field[2:3] != b" ":
            raise ValueError("workspace_git_status_invalid")
        status = field[:2]
        paths.add(field[3:].decode("utf-8", errors="surrogateescape"))
        if b"R" in status or b"C" in status:
            if index >= len(fields) or not fields[index]:
                raise ValueError("workspace_git_status_invalid")
            index += 1
    return paths


def snapshot_workspace_candidate(
    lease: WorkspaceLease,
    changed_artifacts: list[str],
) -> str:
    """Verify the exact write set and compute the controller-owned candidate digest."""
    workspace = Path(lease.workspace_path).resolve()
    paths: list[str] = []
    for raw in changed_artifacts:
        relative = Path(str(raw))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise ValueError("workspace_changed_path_invalid")
        paths.append(relative.as_posix())
    if len(set(paths)) != len(paths):
        raise ValueError("workspace_changed_path_duplicate")
    paths.sort()
    actual = sorted(workspace_changed_paths(workspace))
    if actual != paths:
        raise ValueError(
            f"workspace_write_set_mismatch:actual={actual!r}:declared={paths!r}"
        )
    digest = hashlib.sha256()
    deleted: list[str] = []
    for relative in paths:
        path = workspace / relative
        if not path.exists() and not path.is_symlink():
            deleted.append(relative)
            continue
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            mode = "0o120000"
            content = os.readlink(path).encode("utf-8")
        elif stat.S_ISREG(metadata.st_mode):
            mode = "0o755" if metadata.st_mode & stat.S_IXUSR else "0o644"
            content = path.read_bytes()
        else:
            raise ValueError("workspace_changed_artifact_not_file")
        digest.update(mode.encode("ascii") + b"\0")
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(str(len(content)).encode("ascii") + b"\0")
        digest.update(content)
    for relative in deleted:
        digest.update(b"D\0")
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(b"0\0")
    return "sha256:" + digest.hexdigest()


def freeze_workspace_candidate(
    lease: WorkspaceLease,
    *,
    managed_root: str | Path,
    changed_artifacts: list[str],
    process_receipt: ProcessGroupReceipt,
) -> WorkspaceCandidateReceipt:
    """Serialize an exact candidate freeze and replay for one packet."""
    root = Path(managed_root).expanduser().resolve()
    name = _lease_name(lease.task_id, lease.slice_id, lease.packet_id)
    with _candidate_lock(root, name):
        return _freeze_workspace_candidate_locked(
            lease,
            managed_root=root,
            changed_artifacts=changed_artifacts,
            process_receipt=process_receipt,
        )


def _freeze_workspace_candidate_locked(
    lease: WorkspaceLease,
    *,
    managed_root: str | Path,
    changed_artifacts: list[str],
    process_receipt: ProcessGroupReceipt,
) -> WorkspaceCandidateReceipt:
    """Freeze a deterministic candidate archive, then reap its terminated workspace."""
    root = Path(managed_root).expanduser().resolve()
    workspace = Path(lease.workspace_path).resolve()
    paths = sorted(str(item) for item in changed_artifacts)
    name = _lease_name(lease.task_id, lease.slice_id, lease.packet_id)
    candidate_dir = root / "candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    archive = candidate_dir / f"{name}.tar"
    receipt_path = candidate_dir / f"{name}.json"
    if receipt_path.exists():
        try:
            persisted = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt = WorkspaceCandidateReceipt(
                schema=str(persisted["schema"]),
                task_id=str(persisted["task_id"]),
                slice_id=str(persisted["slice_id"]),
                packet_id=str(persisted["packet_id"]),
                lease_token=str(persisted["lease_token"]),
                source_repo=str(persisted["source_repo"]),
                base_commit=str(persisted["base_commit"]),
                candidate_digest=str(persisted["candidate_digest"]),
                changed_artifacts=tuple(str(item) for item in persisted["changed_artifacts"]),
                archive_path=str(persisted["archive_path"]),
                archive_sha256=str(persisted["archive_sha256"]),
                workspace_reap_receipt=dict(persisted["workspace_reap_receipt"]),
                observed_at=str(persisted["observed_at"]),
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("workspace_candidate_receipt_invalid") from exc
        if not (
            receipt.schema == "hermes_workspace_candidate_receipt_v1"
            and receipt.task_id == lease.task_id
            and receipt.slice_id == lease.slice_id
            and receipt.packet_id == lease.packet_id
            and receipt.lease_token == lease.lease_token
            and receipt.source_repo == lease.source_repo
            and receipt.base_commit == lease.base_commit
            and receipt.changed_artifacts == tuple(paths)
            and Path(receipt.archive_path).resolve() == archive.resolve()
            and archive.is_file()
            and hashlib.sha256(archive.read_bytes()).hexdigest()
            == receipt.archive_sha256
            and receipt.workspace_reap_receipt.get("removed") is True
        ):
            raise ValueError("workspace_candidate_receipt_binding_mismatch")
        return receipt
    if archive.exists():
        raise ValueError("workspace_candidate_incomplete_freeze")
    candidate_digest = snapshot_workspace_candidate(lease, paths)
    temporary = candidate_dir / f".{name}.tmp-{os.getpid()}.tar"
    deletions: list[str] = []
    try:
        with tarfile.open(temporary, mode="w", format=tarfile.PAX_FORMAT) as bundle:
            for relative in paths:
                source = workspace / relative
                if not source.exists() and not source.is_symlink():
                    deletions.append(relative)
                    continue
                metadata = source.lstat()
                info = tarfile.TarInfo(name=f"files/{relative}")
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                if stat.S_ISLNK(metadata.st_mode):
                    info.type = tarfile.SYMTYPE
                    info.mode = 0o777
                    info.linkname = os.readlink(source)
                    info.size = 0
                    bundle.addfile(info)
                elif stat.S_ISREG(metadata.st_mode):
                    content = source.read_bytes()
                    info.type = tarfile.REGTYPE
                    info.mode = 0o755 if metadata.st_mode & stat.S_IXUSR else 0o644
                    info.size = len(content)
                    bundle.addfile(info, io.BytesIO(content))
                else:
                    raise ValueError("workspace_changed_artifact_not_file")
            manifest = json.dumps(
                {
                    "schema": "hermes_workspace_candidate_manifest_v1",
                    "task_id": lease.task_id,
                    "slice_id": lease.slice_id,
                    "packet_id": lease.packet_id,
                    "lease_token": lease.lease_token,
                    "source_repo": lease.source_repo,
                    "base_commit": lease.base_commit,
                    "candidate_digest": candidate_digest,
                    "changed_artifacts": paths,
                    "deleted_artifacts": deletions,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            info = tarfile.TarInfo(name="MANIFEST.json")
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = 0
            info.mode = 0o600
            info.size = len(manifest)
            bundle.addfile(info, io.BytesIO(manifest))
        os.chmod(temporary, 0o600)
        os.replace(temporary, archive)
        archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
        try:
            reaped = reap_workspace(
                lease,
                managed_root=root,
                process_receipt=process_receipt,
            )
        except BaseException:
            archive.unlink(missing_ok=True)
            raise
        receipt = WorkspaceCandidateReceipt(
            schema="hermes_workspace_candidate_receipt_v1",
            task_id=lease.task_id,
            slice_id=lease.slice_id,
            packet_id=lease.packet_id,
            lease_token=lease.lease_token,
            source_repo=lease.source_repo,
            base_commit=lease.base_commit,
            candidate_digest=candidate_digest,
            changed_artifacts=tuple(paths),
            archive_path=str(archive),
            archive_sha256=archive_sha256,
            workspace_reap_receipt=reaped.to_dict(),
            observed_at=datetime.now(timezone.utc).isoformat(),
        )
        receipt_tmp = receipt_path.with_suffix(f".tmp-{os.getpid()}")
        receipt_tmp.write_text(
            json.dumps(receipt.to_dict(), sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.chmod(receipt_tmp, 0o600)
        os.replace(receipt_tmp, receipt_path)
        return receipt
    finally:
        temporary.unlink(missing_ok=True)


def reap_unstarted_workspace(
    lease: WorkspaceLease,
    *,
    managed_root: str | Path,
) -> WorkspaceReapReceipt:
    """Remove an allocation that was never exposed to a started runner."""
    root = Path(managed_root).expanduser().resolve()
    name = _lease_name(lease.task_id, lease.slice_id, lease.packet_id)
    marker = _marker_path(root, name)
    workspace = Path(lease.workspace_path).resolve()
    if workspace != (root / "worktrees" / name).resolve():
        raise ValueError("workspace_path_outside_managed_root")
    with _allocator_lock(root):
        current = _read_lease(marker)
        if current != lease:
            raise ValueError("workspace_lease_fence_mismatch")
        if _run_git(workspace, "status", "--porcelain", "--untracked-files=all"):
            raise ValueError("unstarted_workspace_not_clean")
        _run_git(Path(lease.source_repo), "worktree", "remove", "--force", str(workspace))
        marker.unlink()
        _run_git(Path(lease.source_repo), "worktree", "prune")
    return WorkspaceReapReceipt(
        schema="hermes_task_workspace_reap_receipt_v1",
        packet_id=lease.packet_id,
        lease_token=lease.lease_token,
        workspace_path=lease.workspace_path,
        removed=not workspace.exists() and not marker.exists(),
        residual_probe_succeeded=True,
        residual_writer_pids=(),
        observed_at=datetime.now(timezone.utc).isoformat(),
    )


def reap_workspace(
    lease: WorkspaceLease,
    *,
    managed_root: str | Path,
    process_receipt: ProcessGroupReceipt,
) -> WorkspaceReapReceipt:
    root = Path(managed_root).expanduser().resolve()
    name = _lease_name(lease.task_id, lease.slice_id, lease.packet_id)
    marker = _marker_path(root, name)
    workspace = Path(lease.workspace_path).resolve()
    expected_workspace = (root / "worktrees" / name).resolve()
    if workspace != expected_workspace:
        raise ValueError("workspace_path_outside_managed_root")
    if not (
        process_receipt.leader_reaped
        and process_receipt.residual_probe_succeeded
        and not process_receipt.residual_pids
    ):
        raise ValueError("workspace_process_termination_unproven")
    with _allocator_lock(root):
        current = _read_lease(marker)
        if current != lease:
            raise ValueError("workspace_lease_fence_mismatch")
        identities = (
            ProcessIdentity(
                process_receipt.leader_pid, process_receipt.leader_start_time
            ),
            *process_receipt.observed_descendants,
        )
        certain, writers = residual_workspace_writers(workspace, identities)
        if not certain:
            raise ValueError("workspace_residual_writer_probe_uncertain")
        if writers:
            raise ValueError("workspace_residual_writers_present")
        _run_git(Path(lease.source_repo), "worktree", "remove", "--force", str(workspace))
        marker.unlink()
        _run_git(Path(lease.source_repo), "worktree", "prune")
    return WorkspaceReapReceipt(
        schema="hermes_task_workspace_reap_receipt_v1",
        packet_id=lease.packet_id,
        lease_token=lease.lease_token,
        workspace_path=lease.workspace_path,
        removed=not workspace.exists() and not marker.exists(),
        residual_probe_succeeded=True,
        residual_writer_pids=(),
        observed_at=datetime.now(timezone.utc).isoformat(),
    )
