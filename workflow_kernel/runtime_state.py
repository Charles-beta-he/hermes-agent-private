"""Profile-scoped mutable storage for Workflow Kernel runtime artifacts.

Repository ``docs/private`` files are frozen design/evidence snapshots. Recurring
ledgers and latest runtime reports live under ``HERMES_HOME/state`` so scheduled
runs do not mutate the source checkout.
"""
from __future__ import annotations

import hashlib
import os
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from hermes_constants import get_hermes_home

PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def _no_follow_flag() -> int:
    flag = int(getattr(os, "O_NOFOLLOW", 0))
    if not flag:
        raise OSError("required no-follow semantics unsupported")
    return flag


def _absolute_without_parent_refs(path: Path) -> Path:
    if ".." in path.parts:
        raise ValueError(f"parent path components are not allowed for private runtime paths: {path}")
    return path.absolute()


def _open_directory_chain(path: Path) -> int:
    """Open/create a directory through descriptor-relative no-follow traversal."""
    absolute = _absolute_without_parent_refs(path)
    flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0)) | _no_follow_flag()
    descriptor = os.open(absolute.anchor, flags)
    try:
        for part in absolute.parts[1:]:
            created = False
            try:
                os.mkdir(part, PRIVATE_DIRECTORY_MODE, dir_fd=descriptor)
                created = True
            except FileExistsError:
                pass
            next_descriptor = None
            try:
                next_descriptor = os.open(part, flags, dir_fd=descriptor)
                if not stat.S_ISDIR(os.fstat(next_descriptor).st_mode):
                    raise OSError(f"refusing non-directory ancestor: {absolute}")
                if created:
                    os.fsync(descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
                next_descriptor = None
            except OSError as exc:
                raise OSError(f"refusing unsafe directory ancestor: {absolute}") from exc
            finally:
                if next_descriptor is not None:
                    os.close(next_descriptor)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _open_regular_file(path: Path, flags: int, mode: int = PRIVATE_FILE_MODE) -> int:
    descriptor = os.open(path, flags | _no_follow_flag(), mode)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError(f"refusing non-regular or multiply-linked file: {path}")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


@dataclass(frozen=True)
class LedgerAnchor:
    directory_descriptor: int
    parent: Path
    name: str


def _open_regular_file_at(
    directory_descriptor: int,
    name: str,
    flags: int,
    mode: int = PRIVATE_FILE_MODE,
) -> int:
    open_flags = flags | _no_follow_flag()
    if flags & os.O_CREAT and not flags & os.O_EXCL:
        try:
            descriptor = os.open(name, open_flags | os.O_EXCL, mode, dir_fd=directory_descriptor)
        except FileExistsError:
            descriptor = os.open(
                name,
                open_flags & ~(os.O_CREAT | os.O_EXCL),
                mode,
                dir_fd=directory_descriptor,
            )
    else:
        descriptor = os.open(name, open_flags, mode, dir_fd=directory_descriptor)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError(f"refusing non-regular or multiply-linked file: {name}")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def open_ledger_file(
    path: Path,
    flags: int,
    *,
    private_root: Path | None = None,
    anchor: LedgerAnchor | None = None,
    mode: int = PRIVATE_FILE_MODE,
) -> int:
    if private_root is not None and anchor is None:
        raise ValueError("private ledger operations require an anchored parent descriptor")
    if anchor is not None:
        if private_root is None or anchor.parent != _absolute_without_parent_refs(path.parent) or anchor.name != path.name:
            raise ValueError("ledger anchor does not match private ledger path")
        return _open_regular_file_at(anchor.directory_descriptor, anchor.name, flags, mode)
    return _open_regular_file(path, flags, mode)


def read_ledger_descriptor_bytes(descriptor: int) -> bytes:
    """Read an opened ledger descriptor from the beginning without reopening its path."""
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def read_ledger_bytes(
    path: Path,
    *,
    private_root: Path | None = None,
    anchor: LedgerAnchor | None = None,
) -> bytes:
    descriptor = open_ledger_file(path, os.O_RDONLY, private_root=private_root, anchor=anchor)
    try:
        return read_ledger_descriptor_bytes(descriptor)
    finally:
        os.close(descriptor)


def ensure_private_directory(path: Path) -> Path:
    descriptor = _open_directory_chain(path)
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError(f"refusing non-directory: {path}")
        os.fchmod(descriptor, PRIVATE_DIRECTORY_MODE)
    finally:
        os.close(descriptor)
    return path


def ensure_private_file(path: Path) -> Path:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return path
    descriptor = _open_regular_file(path, os.O_RDONLY)
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
    finally:
        os.close(descriptor)
    return path


def validate_private_file_target(path: Path) -> None:
    """Reject an existing target that cannot be safely replaced as a private file."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise OSError(f"refusing unsafe private file target: {path}")


def _validate_private_file_target_at(directory_descriptor: int, name: str) -> None:
    try:
        info = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise OSError(f"refusing unsafe private file target: {name}")


def write_private_file_atomic(path: Path, data: bytes, *, private_root: Path) -> dict[str, str]:
    """Crash-safe private write anchored to a no-follow parent descriptor."""
    ensure_runtime_artifact_parent(path, private_root=private_root)
    directory_descriptor = _open_directory_chain(path.parent)
    temporary_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor: int | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _no_follow_flag()
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        _validate_private_file_target_at(directory_descriptor, path.name)
        descriptor = os.open(temporary_name, flags, PRIVATE_FILE_MODE, dir_fd=directory_descriptor)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError(f"refusing unsafe private temporary file: {path}")
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(f"failed to write private artifact: {path}")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        _validate_private_file_target_at(directory_descriptor, path.name)
        os.replace(
            temporary_name,
            path.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        os.fsync(directory_descriptor)
    finally:
        try:
            if descriptor is not None:
                os.close(descriptor)
        finally:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except OSError:
                # Cleanup is best-effort; never mask the primary operation result.
                pass
            finally:
                os.close(directory_descriptor)
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()}


def ensure_runtime_artifact_parent(path: Path, *, private_root: Path | None = None) -> bool:
    """Create an artifact parent and tighten it only for an explicitly supplied runtime root."""
    if private_root is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        return False
    parent = _absolute_without_parent_refs(path.parent)
    root = _absolute_without_parent_refs(private_root)
    if parent != root and root not in parent.parents:
        raise ValueError(f"artifact parent is outside private runtime root: {path}")
    ensure_private_directory(root)
    current = root
    for part in parent.relative_to(root).parts:
        current = ensure_private_directory(current / part)
    return True


@contextmanager
def exclusive_ledger_lock(
    ledger_path: Path,
    *,
    private_root: Path | None = None,
) -> Iterator[LedgerAnchor | None]:
    """Serialize a ledger transaction on one anchored private parent descriptor."""
    ensure_runtime_artifact_parent(ledger_path, private_root=private_root)
    lock_path = ledger_path.with_name(f".{ledger_path.name}.lock")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    anchor: LedgerAnchor | None = None
    parent_descriptor: int | None = None
    if private_root is not None:
        parent = _absolute_without_parent_refs(ledger_path.parent)
        parent_descriptor = _open_directory_chain(parent)
        anchor = LedgerAnchor(parent_descriptor, parent, ledger_path.name)
        try:
            descriptor = _open_regular_file_at(parent_descriptor, lock_path.name, flags, PRIVATE_FILE_MODE)
        except BaseException:
            os.close(parent_descriptor)
            raise
    else:
        descriptor = _open_regular_file(lock_path, flags, PRIVATE_FILE_MODE)
    locked = False
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        if os.name == "nt":  # pragma: no cover - Windows locking fallback; execute fails closed there
            import msvcrt

            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = True
        yield anchor
    finally:
        try:
            if locked:
                if os.name == "nt":  # pragma: no cover - Windows locking fallback
                    import msvcrt

                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            try:
                os.close(descriptor)
            finally:
                if parent_descriptor is not None:
                    os.close(parent_descriptor)


def runtime_root(home: str | Path | None = None) -> Path:
    base = Path(home) if home is not None else get_hermes_home()
    return base / "state" / "workflow-kernel"


def limited_unattended_ledger_path(home: str | Path | None = None) -> Path:
    return runtime_root(home) / "limited-unattended" / "receipts.jsonl"


def unattended_dry_run_dir(home: str | Path | None = None) -> Path:
    return runtime_root(home) / "unattended-dry-run-scheduler"


def unattended_dry_run_ledger_path(home: str | Path | None = None) -> Path:
    return unattended_dry_run_dir(home) / "receipts.jsonl"


def unattended_dry_run_report_path(home: str | Path | None = None) -> Path:
    return unattended_dry_run_dir(home) / "workflow_kernel_unattended_dry_run_scheduler_20260613_phase55.json"


def seed_jsonl_once(
    legacy_path: Path,
    runtime_path: Path,
    *,
    private_root: Path | None = None,
    anchor: LedgerAnchor | None = None,
) -> dict[str, object]:
    """Atomically seed a runtime ledger without overwriting runtime history."""
    owned_parent_descriptor: int | None = None
    if private_root is not None and anchor is None:
        ensure_runtime_artifact_parent(runtime_path, private_root=private_root)
        parent = _absolute_without_parent_refs(runtime_path.parent)
        owned_parent_descriptor = _open_directory_chain(parent)
        anchor = LedgerAnchor(owned_parent_descriptor, parent, runtime_path.name)
    try:
        if anchor is not None:
            try:
                existing = os.stat(anchor.name, dir_fd=anchor.directory_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
        else:
            try:
                existing = os.lstat(runtime_path)
            except FileNotFoundError:
                existing = None
        if existing is not None:
            if not stat.S_ISREG(existing.st_mode) or stat.S_ISLNK(existing.st_mode) or existing.st_nlink != 1:
                raise OSError(f"refusing non-regular runtime ledger: {runtime_path}")
            if anchor is not None:
                descriptor = _open_regular_file_at(anchor.directory_descriptor, anchor.name, os.O_RDONLY)
                try:
                    os.fchmod(descriptor, PRIVATE_FILE_MODE)
                finally:
                    os.close(descriptor)
            return {"status": "already_exists", "path": str(runtime_path)}
        if not legacy_path.exists():
            return {"status": "legacy_missing", "path": str(runtime_path)}

        payload = legacy_path.read_bytes()
        if anchor is None:
            ensure_runtime_artifact_parent(runtime_path, private_root=private_root)
        temporary_name = f".{runtime_path.name}.seed-{os.getpid()}-{uuid.uuid4().hex}"
        temporary: Path | None = None
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | _no_follow_flag()
        if anchor is not None:
            descriptor = _open_regular_file_at(
                anchor.directory_descriptor,
                temporary_name,
                flags,
                PRIVATE_FILE_MODE,
            )
        else:
            temporary = runtime_path.parent / temporary_name
            descriptor = _open_regular_file(temporary, flags, 0o666)
        try:
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError(f"failed to seed runtime ledger: {runtime_path}")
                view = view[written:]
            if anchor is not None:
                os.fchmod(descriptor, PRIVATE_FILE_MODE)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            if anchor is not None:
                os.link(
                    temporary_name,
                    anchor.name,
                    src_dir_fd=anchor.directory_descriptor,
                    dst_dir_fd=anchor.directory_descriptor,
                    follow_symlinks=False,
                )
                os.fsync(anchor.directory_descriptor)
            else:
                assert temporary is not None
                os.link(temporary, runtime_path, follow_symlinks=False)
            status_value = "seeded"
        except FileExistsError:
            status_value = "already_exists"
        finally:
            try:
                if anchor is not None:
                    os.unlink(temporary_name, dir_fd=anchor.directory_descriptor)
                else:
                    assert temporary is not None
                    temporary.unlink()
            except OSError:
                pass
        return {
            "status": status_value,
            "path": str(runtime_path),
            "source": str(legacy_path),
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    finally:
        if owned_parent_descriptor is not None:
            os.close(owned_parent_descriptor)


def prefer_runtime_artifact(runtime_path: Path, snapshot_path: Path) -> Path:
    return runtime_path if runtime_path.exists() else snapshot_path
