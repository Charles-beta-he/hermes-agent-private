"""Subordinate durable workflow effect ledger (HGWK F4).

Owns claim/fence/intent/observation/reconcile/terminal receipt/compensation
facts only. Never owns workflow-run status. One effect ledger is authoritative
for effect facts; workflow_execution_ledger remains the sole workflow lifecycle
SSOT.

Durability model:
- operations pin private_root and child directory identities (locks/effects/fences/indexes)
  by descriptor/inode for the ownership lifetime and re-verify on every mutation
- target-scoped cross-process locks cover claim through optional held ownership
- idempotency key path is the transactional/journal-derived SSOT: pending owner record
  is published before claim, then bound to the exact claim event; both crash directions
  repair from that key record
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, NoReturn, Sequence

from workflow_kernel import runtime_state
from workflow_kernel.effect_contracts import (
    EffectContractError,
    canonical_digest_v1,
    derive_workflow_effect_ledger_event_id_v1,
    effect_receipt_to_dict,
    freeze_admitted_json_v1,
    normalize_admitted_json_v1,
    parse_effect_observation_v1,
    parse_effect_receipt_v1,
    parse_workflow_effect_ledger_event_v1,
    thaw_admitted_json_v1,
)
from workflow_kernel.runtime_state import (
    PRIVATE_FILE_MODE,
    LedgerAnchor,
    exclusive_ledger_lock,
    open_ledger_file,
    read_existing_private_file_bytes,
    read_ledger_descriptor_bytes,
    write_private_file_atomic,
)


_EVENT_SCHEMA_VERSION = "workflow_effect_ledger_event_v1"
_FENCE_SCHEMA_VERSION = "workflow_effect_target_fence_v1"
_INDEX_SCHEMA_VERSION = "workflow_effect_idempotency_index_v1"
# Exact durable idempotency SSOT keys (pending and bound). No extras permitted.
_INDEX_EXACT_KEYS = frozenset(
    {
        "schema_version",
        "state",
        "idempotency_key",
        "request_digest",
        "effect_id",
        "target_digest",
        "event_id",
        "fence",
        "command_digest",
        "adapter_id",
        "operation",
        "payload_digest",
    }
)
_INDEX_IDENTITY_KEYS = frozenset(
    {
        "idempotency_key",
        "request_digest",
        "effect_id",
        "target_digest",
        "event_id",
        "fence",
        "command_digest",
        "adapter_id",
        "operation",
        "payload_digest",
    }
)

_EVENT_TYPES = frozenset(
    {
        "claimed",
        "intent_recorded",
        "observation_recorded",
        "reconciled",
        "receipt_terminal",
        "compensation_intent",
        "compensation_observation",
        "compensation_receipt",
    }
)

# Complete allowed transition table: current_status -> allowed next event_types.
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "absent": frozenset({"claimed"}),
    "claimed": frozenset({"intent_recorded"}),
    "intent_recorded": frozenset({"observation_recorded", "reconciled"}),
    "observed": frozenset({"receipt_terminal"}),
    # unknown covers observation(classification=unknown) and projected intent-without-observation
    "unknown": frozenset({"reconciled"}),
    "reconciled": frozenset({"receipt_terminal"}),
    "applied": frozenset({"compensation_intent"}),
    "not_applied": frozenset(),  # compensation only from proven applied terminal receipt
    "compensation_pending": frozenset({"compensation_observation"}),
    "compensation_observed": frozenset({"compensation_receipt"}),
    "compensated": frozenset(),
}

_ROOT_LOCKS: dict[str, threading.RLock] = {}
_ROOT_LOCKS_GUARD = threading.Lock()


class WorkflowEffectLedgerError(ValueError):
    def __init__(self, code: str, message: str, *, phase: str = "pre_event_fsync"):
        super().__init__(message)
        self.code = code
        self.phase = phase


CRASH_EXIT_POST_PENDING_FSYNC = 86
CRASH_EXIT_POST_CLAIM_FSYNC_PRE_BOUND = 87


class _LedgerWriteFailure(Exception):
    def __init__(self, bytes_written: int, cause: OSError):
        super().__init__(str(cause))
        self.bytes_written = bytes_written
        self.cause = cause


def _fail(code: str, message: str, *, phase: str = "pre_event_fsync") -> NoReturn:
    raise WorkflowEffectLedgerError(code, message, phase=phase)


def _parse_receipt(value: object) -> Any:
    try:
        return parse_effect_receipt_v1(value)
    except EffectContractError as exc:
        _fail(getattr(exc, "code", "effect_contract_invalid"), str(exc))


def _parse_observation(value: object) -> Any:
    try:
        return parse_effect_observation_v1(value)
    except EffectContractError as exc:
        _fail(getattr(exc, "code", "effect_contract_invalid"), str(exc))


def _compact_json(value: object) -> bytes:
    """Use the shared admitted-domain canonicalizer (integer-only, UTF-16 keys)."""
    from workflow_kernel.effect_contracts import canonical_json_v1

    try:
        # Explicitly thaw frozen MappingProxyType/tuple projections before serialize.
        return canonical_json_v1(thaw_admitted_json_v1(value)).encode("utf-8")
    except EffectContractError as exc:
        _fail(getattr(exc, "code", "effect_contract_not_canonical"), str(exc))


def _fact_digest(value: object) -> str:
    """Canonical digest over thawed admitted JSON (reusable equality helper)."""
    return canonical_digest_v1(thaw_admitted_json_v1(value))


def _require_equal_readback_fact(
    *,
    actual: object,
    expected: object | None,
    expected_digest: object | None,
    label: str,
) -> None:
    """Bind one readback value to durable fact content + digest (one helper, no free mutation).

    An independently mutated readback that only recomputes its own digest and outer wel_
    event id must still fail when it diverges from the durable expected fact/digest pair.
    """
    if not isinstance(actual, Mapping):
        _fail("payload_invalid", f"{label} must be an object")
    actual_digest = _fact_digest(actual)
    if expected is not None:
        if not isinstance(expected, Mapping):
            _fail("payload_invalid", f"{label} durable fact must be an object")
        if actual_digest != _fact_digest(expected):
            _fail("payload_invalid", f"{label} does not match durable fact")
    if expected_digest is not None:
        if not isinstance(expected_digest, str) or not expected_digest:
            _fail("payload_invalid", f"{label} durable digest missing")
        if actual_digest != expected_digest:
            _fail("payload_invalid", f"{label} digest does not match durable fact")
    if expected is None and expected_digest is None:
        _fail("payload_invalid", f"{label} has no durable fact to bind")

def _fsync_descriptor(descriptor: int) -> None:
    os.fsync(descriptor)


def _root_lock(private_root: Path) -> threading.RLock:
    # private_root is already canonicalized/pinned at transaction admission.
    # Never repin its pathname from inside an owned transaction.
    key = str(private_root)
    with _ROOT_LOCKS_GUARD:
        lock = _ROOT_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _ROOT_LOCKS[key] = lock
        return lock


def _name_for(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _RootIdentity:
    path: Path
    dev: int
    ino: int
    fd: int
    mode: int
    nlink: int


@dataclass(frozen=True)
class _DirPin:
    name: str
    root_fd: int
    fd: int
    dev: int
    ino: int
    mode: int
    nlink: int


@dataclass(frozen=True)
class _NestedDirPin:
    name: str
    parent_fd: int
    parent_dev: int
    parent_ino: int
    fd: int
    dev: int
    ino: int


_CHILD_DIR_NAMES = ("locks", "effects", "fences", "indexes")
_LAYOUT_SEAL_NAME = ".hgwk_effect_layout_v1.json"
_LAYOUT_SEAL_SCHEMA = "workflow_effect_layout_seal_v1"


@dataclass(frozen=True)
class TargetOwnershipV1:
    """Cross-process ownership token held through claim/intent/dispatch/observation."""

    private_root: Path
    target_digest: str
    root_identity: _RootIdentity
    child_pins: Mapping[str, _DirPin]
    _lock_fd: int
    _nested_pins: Mapping[str, _NestedDirPin]


def _dir_open_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags | int(getattr(os, "O_NOFOLLOW", 0))


def _pin_root(private_root: Path | str) -> tuple[Path, _RootIdentity]:
    root = runtime_state.pin_runtime_path(Path(private_root))
    runtime_state.ensure_private_directory(root)
    try:
        path_info = os.lstat(root)
    except OSError as exc:
        _fail("unsafe_path", f"cannot stat private_root: {exc}")
    if stat.S_ISLNK(path_info.st_mode) or not stat.S_ISDIR(path_info.st_mode):
        _fail("unsafe_path", "private_root must be a real directory")
    try:
        fd = os.open(root, _dir_open_flags())
    except OSError as exc:
        _fail("unsafe_path", f"cannot open private_root descriptor: {exc}")
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            _fail("unsafe_path", "private_root descriptor is not a directory")
        if info.st_dev != path_info.st_dev or info.st_ino != path_info.st_ino:
            _fail("root_replaced", "private_root identity raced during pin")
        identity = _RootIdentity(
            path=root,
            dev=info.st_dev,
            ino=info.st_ino,
            fd=fd,
            mode=info.st_mode,
            nlink=info.st_nlink,
        )
        return root, identity
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def _close_root_identity(identity: _RootIdentity | None) -> None:
    if identity is None:
        return
    try:
        os.close(identity.fd)
    except OSError:
        pass


def _verify_root_identity(identity: _RootIdentity) -> None:
    try:
        fd_info = os.fstat(identity.fd)
    except OSError as exc:
        _fail("root_replaced", f"private_root descriptor lost: {exc}")
    if not stat.S_ISDIR(fd_info.st_mode):
        _fail("root_replaced", "private_root descriptor is not a directory")
    if fd_info.st_dev != identity.dev or fd_info.st_ino != identity.ino:
        _fail("root_replaced", "private_root descriptor identity changed")
    # Mode/nlink are recorded at admission; only fail closed on non-directory transitions.
    if not stat.S_ISDIR(fd_info.st_mode):
        _fail("root_replaced", "private_root descriptor is not a directory")
    try:
        path_info = os.lstat(identity.path)
    except OSError as exc:
        _fail("root_replaced", f"private_root disappeared or is unreadable: {exc}")
    if stat.S_ISLNK(path_info.st_mode) or not stat.S_ISDIR(path_info.st_mode):
        _fail("root_replaced", "private_root is no longer a real directory")
    if path_info.st_dev != identity.dev or path_info.st_ino != identity.ino:
        _fail("root_replaced", "private_root identity changed during transaction")


def _open_dir_pin(root: Path, identity: _RootIdentity, name: str) -> _DirPin:
    path = root / name
    try:
        os.mkdir(name, 0o700, dir_fd=identity.fd)
        os.fsync(identity.fd)
    except FileExistsError:
        pass
    except OSError as exc:
        _fail("unsafe_path", f"cannot create child directory {name}: {exc}")
    try:
        fd = os.open(name, _dir_open_flags(), dir_fd=identity.fd)
    except OSError as exc:
        _fail("unsafe_path", f"cannot open child directory {name}: {exc}")
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            _fail("unsafe_path", f"child {name} is not a directory")
        # Also verify path still resolves to same inode (detect replace races).
        path_info = os.stat(name, dir_fd=identity.fd, follow_symlinks=False)
        if path_info.st_dev != info.st_dev or path_info.st_ino != info.st_ino:
            _fail("root_replaced", f"child directory {name} identity raced during pin")
        return _DirPin(
            name=name,
            root_fd=identity.fd,
            fd=fd,
            dev=info.st_dev,
            ino=info.st_ino,
            mode=info.st_mode,
            nlink=info.st_nlink,
        )
    except Exception:
        os.close(fd)
        raise


def _close_dir_pins(pins: Mapping[str, _DirPin]) -> None:
    for pin in pins.values():
        try:
            os.close(pin.fd)
        except OSError:
            pass


def _pin_child_directories(root: Path, identity: _RootIdentity) -> dict[str, _DirPin]:
    pins: dict[str, _DirPin] = {}
    try:
        for name in _CHILD_DIR_NAMES:
            pins[name] = _open_dir_pin(root, identity, name)
        return pins
    except Exception:
        _close_dir_pins(pins)
        raise


def _verify_child_pins(pins: Mapping[str, _DirPin] | None) -> None:
    if not pins:
        return
    for name, pin in pins.items():
        try:
            fd_info = os.fstat(pin.fd)
        except OSError as exc:
            _fail("root_replaced", f"child directory {name} descriptor lost: {exc}")
        if not stat.S_ISDIR(fd_info.st_mode):
            _fail("root_replaced", f"child directory {name} descriptor is not a directory")
        if fd_info.st_dev != pin.dev or fd_info.st_ino != pin.ino:
            _fail("root_replaced", f"child directory {name} descriptor identity changed")
        try:
            path_info = os.stat(pin.name, dir_fd=pin.root_fd, follow_symlinks=False)
        except OSError as exc:
            _fail("root_replaced", f"child directory {name} path unreadable: {exc}")
        if stat.S_ISLNK(path_info.st_mode) or not stat.S_ISDIR(path_info.st_mode):
            _fail("root_replaced", f"child directory {name} path is no longer a real directory")
        if path_info.st_dev != pin.dev or path_info.st_ino != pin.ino:
            _fail(
                "root_replaced",
                f"child directory {name} was replaced under ownership",
            )


def _verify_transaction_identity(
    identity: _RootIdentity,
    child_pins: Mapping[str, _DirPin] | None = None,
    nested_pins: Mapping[str, _NestedDirPin] | None = None,
) -> None:
    _verify_root_identity(identity)
    _verify_child_pins(child_pins)
    if nested_pins:
        for label, pin in nested_pins.items():
            try:
                parent_info = os.fstat(pin.parent_fd)
                fd_info = os.fstat(pin.fd)
                path_info = os.stat(pin.name, dir_fd=pin.parent_fd, follow_symlinks=False)
            except OSError as exc:
                _fail("root_replaced", f"nested directory {label} identity unreadable: {exc}")
            if (parent_info.st_dev, parent_info.st_ino) != (pin.parent_dev, pin.parent_ino):
                _fail("root_replaced", f"nested directory {label} parent identity changed")
            if not stat.S_ISDIR(fd_info.st_mode) or not stat.S_ISDIR(path_info.st_mode):
                _fail("root_replaced", f"nested directory {label} is no longer a real directory")
            if (fd_info.st_dev, fd_info.st_ino) != (pin.dev, pin.ino):
                _fail("root_replaced", f"nested directory {label} descriptor identity changed")
            if (path_info.st_dev, path_info.st_ino) != (pin.dev, pin.ino):
                _fail("root_replaced", f"nested directory {label} was replaced under ownership")


def _close_nested_fds(nested: Mapping[str, _NestedDirPin] | None) -> None:
    if not nested:
        return
    for pin in nested.values():
        try:
            os.close(pin.fd)
        except OSError:
            pass


@contextmanager
def _exclusive_lock_at_dirfd(
    dir_fd: int,
    lock_name: str,
    *,
    verify_identity: Callable[[], None] | None = None,
) -> Iterator[int]:
    """Acquire a descriptor-relative lock with identity barriers around every stage."""
    flags = os.O_RDWR
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    nofollow = int(getattr(os, "O_NOFOLLOW", 0))
    lock_fd: int | None = None
    locked = False
    created = False
    identity_failed = False

    def verify() -> None:
        nonlocal identity_failed
        if verify_identity is not None:
            try:
                verify_identity()
            except BaseException:
                identity_failed = True
                raise

    def remove_created_if_still_owned() -> None:
        if not created or lock_fd is None:
            return
        try:
            fd_info = os.fstat(lock_fd)
            name_info = os.stat(lock_name, dir_fd=dir_fd, follow_symlinks=False)
            if (
                stat.S_ISREG(name_info.st_mode)
                and name_info.st_nlink == 1
                and (name_info.st_dev, name_info.st_ino) == (fd_info.st_dev, fd_info.st_ino)
            ):
                os.unlink(lock_name, dir_fd=dir_fd)
                os.fsync(dir_fd)
        except OSError:
            # Never unlink an entry whose name/fd identity cannot be proven.
            pass

    try:
        verify()
        try:
            lock_fd = os.open(
                lock_name,
                flags | os.O_CREAT | os.O_EXCL | nofollow,
                PRIVATE_FILE_MODE,
                dir_fd=dir_fd,
            )
            created = True
        except FileExistsError:
            lock_fd = os.open(lock_name, flags | nofollow, dir_fd=dir_fd)
        except OSError as exc:
            _fail("root_replaced", f"descriptor-relative lock open failed for {lock_name}: {exc}")
        try:
            verify()
        except BaseException:
            remove_created_if_still_owned()
            raise
        info = os.fstat(lock_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _fail("unsafe_path", f"refusing unsafe lock file {lock_name}")
        os.fchmod(lock_fd, PRIVATE_FILE_MODE)
        verify()
        runtime_state._acquire_posix_lock(lock_fd)
        locked = True
        verify()
        yield lock_fd
        verify()
    finally:
        release_error: BaseException | None = None
        try:
            if locked and lock_fd is not None:
                try:
                    verify()
                except BaseException as exc:
                    release_error = exc
                runtime_state._release_posix_lock(lock_fd)
                try:
                    verify()
                except BaseException as exc:
                    if release_error is None:
                        release_error = exc
        finally:
            if lock_fd is not None:
                try:
                    if identity_failed:
                        remove_created_if_still_owned()
                    os.close(lock_fd)
                except OSError:
                    pass
        if release_error is not None:
            raise release_error


def _pin_nested_transaction_dirs(
    child_pins: Mapping[str, _DirPin]
) -> dict[str, _NestedDirPin]:
    """Hold nested canonical dirs for the transaction (idempotency index + lock dirs)."""
    nested: dict[str, _NestedDirPin] = {}
    try:
        nested["indexes_idempotency"] = _open_nested_dir_from_pin(
            child_pins["indexes"], "idempotency", create=True
        )
        nested["locks_idempotency"] = _open_nested_dir_from_pin(
            child_pins["locks"], "idempotency", create=True
        )
        return nested
    except Exception:
        _close_nested_fds(nested)
        raise


def _target_lock_name(target_digest: str) -> str:
    return f"{_name_for(target_digest)}.lock"


def _idempotency_lock_name(idempotency_key: str) -> str:
    return f"{_name_for(idempotency_key)}.lock"


def _layout_seal_document(pins: Mapping[str, _DirPin]) -> dict[str, Any]:
    children = {
        name: {"dev": pin.dev, "ino": pin.ino}
        for name, pin in sorted(pins.items(), key=lambda item: item[0])
    }
    return {"schema_version": _LAYOUT_SEAL_SCHEMA, "children": children}


def _write_layout_seal(root: Path, pins: Mapping[str, _DirPin], identity: _RootIdentity) -> None:
    _verify_transaction_identity(identity, pins)
    document = _layout_seal_document(pins)
    # Seal lives in the held root directory itself (not a replaceable child).
    try:
        _write_bytes_at_dirfd(
            identity.fd, _LAYOUT_SEAL_NAME, _compact_json(document) + b"\n"
        )
    except (OSError, WorkflowEffectLedgerError) as exc:
        _fail("unsafe_path", f"cannot persist layout seal: {exc}")
    _verify_transaction_identity(identity, pins)


def _read_layout_seal(root: Path, identity: _RootIdentity) -> dict[str, Any] | None:
    del root
    try:
        raw = _read_bytes_at_dirfd(identity.fd, _LAYOUT_SEAL_NAME)
    except FileNotFoundError:
        return None
    except (OSError, WorkflowEffectLedgerError) as exc:
        _fail("layout_corrupt", f"layout seal unreadable: {exc}")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("layout_corrupt", f"layout seal invalid json: {exc}")
    if not isinstance(value, dict) or value.get("schema_version") != _LAYOUT_SEAL_SCHEMA:
        _fail("layout_corrupt", "layout seal schema mismatch")
    children = value.get("children")
    if not isinstance(children, dict):
        _fail("layout_corrupt", "layout seal children missing")
    return value


def _verify_or_init_layout_seal(
    root: Path, pins: Mapping[str, _DirPin], identity: _RootIdentity, *, create: bool
) -> None:
    seal = _read_layout_seal(root, identity)
    expected = _layout_seal_document(pins)
    if seal is None:
        if create:
            _write_layout_seal(root, pins, identity)
        return
    if seal.get("children") != expected["children"]:
        _fail(
            "layout_corrupt",
            "canonical child directory identities do not match layout seal",
        )


def _open_nested_dir_from_pin(
    pin: _DirPin, *parts: str, create: bool = True
) -> _NestedDirPin:
    """Open and identity-pin one canonical nested directory."""
    if len(parts) != 1:
        _fail("unsafe_path", "nested transaction pin requires one component")
    part = parts[0]
    if create:
        try:
            os.mkdir(part, 0o700, dir_fd=pin.fd)
        except FileExistsError:
            pass
        except OSError as exc:
            _fail("root_replaced", f"descriptor-relative nested mkdir failed: {exc}")
    try:
        fd = os.open(part, _dir_open_flags(), dir_fd=pin.fd)
    except OSError as exc:
        _fail("root_replaced", f"descriptor-relative nested open failed: {exc}")
    try:
        info = os.fstat(fd)
        parent_info = os.fstat(pin.fd)
        path_info = os.stat(part, dir_fd=pin.fd, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            _fail("unsafe_path", f"nested path component is not a directory: {part}")
        if (path_info.st_dev, path_info.st_ino) != (info.st_dev, info.st_ino):
            _fail("root_replaced", "nested directory identity raced during pin")
        return _NestedDirPin(
            name=part,
            parent_fd=pin.fd,
            parent_dev=parent_info.st_dev,
            parent_ino=parent_info.st_ino,
            fd=fd,
            dev=info.st_dev,
            ino=info.st_ino,
        )
    except Exception:
        os.close(fd)
        raise


def _write_bytes_at_dirfd(
    dir_fd: int,
    name: str,
    data: bytes,
    *,
    verify_identity: Callable[[], None] | None = None,
) -> None:
    """Atomic create/replace relative to dir_fd with optional identity barriers."""
    import uuid as _uuid
    temporary_name = f".{name}.{_uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    nofollow = int(getattr(os, "O_NOFOLLOW", 0))
    descriptor = None

    def verify() -> None:
        if verify_identity is not None:
            verify_identity()

    try:
        verify()
        descriptor = os.open(temporary_name, flags | nofollow, PRIVATE_FILE_MODE, dir_fd=dir_fd)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _fail("unsafe_path", f"refusing unsafe temporary file {name}")
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail("write_failed", f"failed writing {name}")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        verify()
        os.replace(temporary_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
        verify()
    except WorkflowEffectLedgerError:
        raise
    except OSError as exc:
        _fail("unsafe_path", f"descriptor-relative write failed for {name}: {exc}")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            os.unlink(temporary_name, dir_fd=dir_fd)
        except OSError:
            pass


def _read_bytes_at_dirfd(dir_fd: int, name: str) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    nofollow = int(getattr(os, "O_NOFOLLOW", 0))
    try:
        descriptor = os.open(name, flags | nofollow, dir_fd=dir_fd)
    except FileNotFoundError:
        raise
    except OSError as exc:
        _fail("unsafe_path", f"descriptor-relative open failed for {name}: {exc}")
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _fail("unsafe_path", f"refusing unsafe file {name}")
        chunks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _name_for_digest_file(value: str) -> str:
    return _name_for(value)


def _validated_root(private_root: Path | str) -> tuple[Path, _RootIdentity]:
    return _pin_root(private_root)


def _effect_ledger_path(private_root: Path, effect_id: str) -> Path:
    return private_root / "effects" / f"{_name_for(effect_id)}.jsonl"


def effect_ledger_path_v1(*, private_root: Path | str, effect_id: str) -> Path:
    root = runtime_state.pin_runtime_path(Path(private_root))
    return _effect_ledger_path(root, effect_id)


def _fence_path(private_root: Path, target_digest: str) -> Path:
    return private_root / "fences" / f"{_name_for(target_digest)}.json"


def _idempotency_index_path(private_root: Path, idempotency_key: str) -> Path:
    return private_root / "indexes" / "idempotency" / f"{_name_for(idempotency_key)}.json"


def _idempotency_lock_path(private_root: Path, idempotency_key: str) -> Path:
    return (
        private_root
        / "locks"
        / "idempotency"
        / f"{_name_for(idempotency_key)}.lock"
    )


def _target_lock_path(private_root: Path, target_digest: str) -> Path:
    return private_root / "locks" / f"{_name_for(target_digest)}.lock"


def _require_text(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("payload_invalid", f"{label} must be a non-empty string")
    return value


def _require_digest(value: object, *, label: str) -> str:
    text = _require_text(value, label=label)
    body = text[len("sha256:") :] if text.startswith("sha256:") else ""
    if len(body) != 64 or any(char not in "0123456789abcdef" for char in body):
        _fail("payload_invalid", f"{label} must be sha256:<64 hex>")
    return text


def _write_all(descriptor: int, record: bytes) -> None:
    view = memoryview(record)
    offset = 0
    while offset < len(view):
        try:
            written = os.write(descriptor, view[offset:])
        except InterruptedError:
            continue
        except OSError as exc:
            raise _LedgerWriteFailure(offset, exc) from exc
        if written <= 0:
            raise _LedgerWriteFailure(
                offset,
                OSError(f"ledger write made no progress at {offset} of {len(record)}"),
            )
        offset += written


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate_key", f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    # Non-finite JSON constants are admitted-domain payload rejects, not grammar errors.
    _fail("payload_invalid", f"non-finite JSON number is forbidden: {value}")


def _map_effect_contract_error(exc: EffectContractError) -> str:
    """Preserve meaningful public ledger taxonomy for contract failures.

    Narrow mapping (4A.5B.2):
    - identity_mismatch: event_id derivation mismatch
    - schema_invalid: only schema_version discrimination or unsupported ledger event_type
    - payload_invalid: payload enum/value/key/type/calendar and all other contract failures
      (including \"unsupported classification\" / side_effect_tier / etc.)
    """
    message = str(exc).lower()
    code = getattr(exc, "code", "") or ""
    if "event_id" in message and "match" in message:
        return "identity_mismatch"
    if "schema_version" in message:
        return "schema_invalid"
    # Only unsupported *ledger event_type* is schema-level; payload enums stay payload_invalid.
    if "unsupported effect ledger event_type" in message:
        return "schema_invalid"
    if "unsupported event_type" in message and "classification" not in message:
        # Defensive: bare unsupported event_type without payload-enum wording.
        if "classification" not in message and "side_effect" not in message:
            return "schema_invalid"
    if code in {"effect_contract_not_canonical"}:
        return "payload_invalid"
    # Discriminated payload key/type/calendar/enum failures are payload_invalid.
    return "payload_invalid"


def _reject_non_integer_json_number(value: str) -> int:
    """Fail closed at every public JSON parse boundary: admitted integers only.

    Accept textual integral decimal/exponent forms whose exact Decimal value is an
    integer in the JS safe range (1.0, -0.0, 1e0). Reject fractional/unsafe/nonfinite
    without float loss by using decimal.Decimal then normalizing to int.
    """
    from decimal import Decimal, InvalidOperation

    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError):
        _fail("payload_invalid", f"non-integer JSON number is forbidden: {value}")
    if not number.is_finite():
        _fail("payload_invalid", f"non-finite JSON number is forbidden: {value}")
    if number != number.to_integral_value():
        _fail("payload_invalid", f"non-integer JSON number is forbidden: {value}")
    as_int = int(number)
    if as_int < -9007199254740991 or as_int > 9007199254740991:
        _fail("payload_invalid", f"JSON integer exceeds JS-safe range: {value}")
    # Normalize -0 to 0.
    return 0 if as_int == 0 else as_int


def _normalized_event(candidate: object) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        _fail("schema_invalid", "event must be an object")
    # Public contract parse enforces envelope + exact discriminated payload.
    try:
        parsed = parse_workflow_effect_ledger_event_v1(candidate)
    except EffectContractError as exc:
        _fail(_map_effect_contract_error(exc), str(exc))

    # Internal replay may use thawed mutable copies; public APIs re-freeze on return.
    return {
        "schema_version": parsed.schema_version,
        "effect_id": parsed.effect_id,
        "sequence": parsed.sequence,
        "previous_event_id": parsed.previous_event_id,
        "idempotency_key": parsed.idempotency_key,
        "occurred_at": parsed.occurred_at,
        "event_type": parsed.event_type,
        "payload": thaw_admitted_json_v1(parsed.payload),
        "event_id": parsed.event_id,
    }


def _freeze_public_event(event: Mapping[str, Any]) -> Any:
    return freeze_admitted_json_v1(thaw_admitted_json_v1(dict(event)))


def _freeze_public_projection(projection: Mapping[str, Any]) -> Any:
    return freeze_admitted_json_v1(thaw_admitted_json_v1(dict(projection)))


def _freeze_public_result(result: Mapping[str, Any]) -> Any:
    """Freeze complete public append/claim/replay result root (incl. metadata)."""
    return freeze_admitted_json_v1(thaw_admitted_json_v1(dict(result)))


def _status_after_event(status: str, event: Mapping[str, Any]) -> str:
    event_type = event["event_type"]
    payload = event["payload"]
    if event_type == "claimed":
        return "claimed"
    if event_type == "intent_recorded":
        return "intent_recorded"
    if event_type == "observation_recorded":
        classification = payload.get("classification")
        if classification is None and isinstance(payload.get("observation"), dict):
            classification = payload["observation"].get("classification")
        if classification == "unknown":
            return "unknown"
        if classification in {"applied", "not_applied"}:
            return "observed"
        _fail("payload_invalid", "observation classification invalid")
    if event_type == "reconciled":
        return "reconciled"
    if event_type == "receipt_terminal":
        classification = payload.get("result_classification")
        if classification is None and isinstance(payload.get("receipt"), dict):
            classification = payload["receipt"].get("result_classification")
        if classification not in {"applied", "not_applied"}:
            _fail("payload_invalid", "terminal receipt classification invalid")
        return str(classification)
    if event_type == "compensation_intent":
        return "compensation_pending"
    if event_type == "compensation_observation":
        return "compensation_observed"
    if event_type == "compensation_receipt":
        return "compensated"
    _fail("schema_invalid", f"unsupported event_type: {event_type}")


def _exact_keys(payload: Mapping[str, Any], required: set[str], *, label: str) -> None:
    keys = set(payload)
    missing = sorted(required - keys)
    if missing:
        _fail("payload_invalid", f"{label} missing keys: {missing}")
    extra = sorted(keys - required)
    if extra:
        _fail("payload_invalid", f"{label} has unknown keys: {extra}")


def _require_claim_bindings(payload: Mapping[str, Any], facts: Mapping[str, Any], *, label: str) -> None:
    for key in (
        "command_digest",
        "request_digest",
        "target_digest",
        "adapter_id",
        "operation",
        "payload_digest",
        "idempotency_key",
        "effect_id",
    ):
        if key not in payload:
            continue
        expected = facts.get(key)
        if expected is not None and payload.get(key) != expected:
            _fail("payload_invalid", f"{label} {key} does not match claim facts")


def _validate_compensation_receipt_binding_v1(
    *,
    effect_id: str,
    payload: Mapping[str, Any],
    parsed_receipt: Any,
    observation: Mapping[str, Any],
    original_receipt: Mapping[str, Any],
    facts: Mapping[str, Any],
) -> None:
    """Reusable exact binding for embedded compensation receipt.

    Binds wrapper + complete original claim + applied original receipt +
    compensation intent + compensation observation + embedded receipt body.
    """
    if payload.get("receipt_digest") != parsed_receipt.receipt_digest:
        _fail("payload_invalid", "compensation receipt_digest mismatch")
    if parsed_receipt.result_classification not in {"applied", "not_applied"}:
        _fail("payload_invalid", "compensation receipt classification invalid")

    original = _require_digest(payload["original_receipt_digest"], label="original_receipt_digest")
    if original != facts.get("original_receipt_digest"):
        _fail(
            "payload_invalid",
            "compensation receipt original binding mismatch",
        )
    original_parsed = _parse_receipt(original_receipt)
    if original_parsed.receipt_digest != original:
        _fail("payload_invalid", "compensation receipt original body mismatch")
    # Exact original receipt body digest must match the applied terminal when present.
    stored_original = facts.get("original_receipt") or facts.get("terminal_receipt")
    if isinstance(stored_original, dict):
        if canonical_digest_v1(thaw_admitted_json_v1(stored_original)) != canonical_digest_v1(
            thaw_admitted_json_v1(original_receipt)
        ):
            # Allow equivalent canonical bodies via digest of parsed receipts.
            if _parse_receipt(stored_original).receipt_digest != original_parsed.receipt_digest:
                _fail("payload_invalid", "compensation original receipt body mismatch")

    if payload["effect_id"] != effect_id:
        _fail("payload_invalid", "compensation receipt effect_id mismatch")
    if parsed_receipt.effect_id != effect_id:
        _fail("payload_invalid", "compensation receipt body effect_id mismatch")

    fence = payload["fence"]
    if not isinstance(fence, int) or isinstance(fence, bool) or fence < 1:
        _fail("payload_invalid", "compensation receipt fence invalid")
    intent_fence = facts.get("compensation_fence", facts.get("fence"))
    if intent_fence not in {None, fence}:
        _fail("payload_invalid", "compensation receipt fence mismatch")
    if payload.get("fence") != parsed_receipt.fence:
        _fail("payload_invalid", "compensation receipt wrapper/body fence mismatch")

    # Wrapper fields bind to compensation intent (preferred) then original claim facts.
    for key in (
        "command_digest",
        "request_digest",
        "target_digest",
        "payload_digest",
        "authority_binding_digest",
    ):
        val = _require_digest(payload[key], label=key)
        expected = facts.get(f"compensation_{key}", facts.get(key))
        if key == "request_digest":
            expected = facts.get("compensation_request_digest", expected)
        if expected not in {None, val}:
            _fail("payload_invalid", f"compensation receipt {key} mismatch")
    for key in ("adapter_id", "operation", "idempotency_key"):
        val = _require_text(payload[key], label=key)
        expected = facts.get(f"compensation_{key}", facts.get(key))
        if expected not in {None, val}:
            _fail("payload_invalid", f"compensation receipt {key} mismatch")

    # Embedded receipt body binds exactly to wrapper + intent/classification facts.
    if parsed_receipt.command_digest != payload["command_digest"]:
        _fail("payload_invalid", "compensation receipt body command_digest mismatch")
    if parsed_receipt.adapter_id != payload["adapter_id"]:
        _fail("payload_invalid", "compensation receipt body adapter_id mismatch")
    if parsed_receipt.operation != payload["operation"]:
        _fail("payload_invalid", "compensation receipt body operation mismatch")
    if parsed_receipt.payload_digest != payload["payload_digest"]:
        _fail("payload_invalid", "compensation receipt body payload_digest mismatch")
    if parsed_receipt.idempotency_key != payload["idempotency_key"]:
        _fail("payload_invalid", "compensation receipt body idempotency_key mismatch")
    if parsed_receipt.authority_binding_digest != payload["authority_binding_digest"]:
        _fail("payload_invalid", "compensation receipt body authority_binding_digest mismatch")

    recomputed_target = canonical_digest_v1(
        {
            "target_kind": parsed_receipt.target["target_kind"],
            "target_ref": parsed_receipt.target["target_ref"],
        }
    )
    if payload["target_digest"] != recomputed_target:
        _fail("payload_invalid", "compensation receipt target_digest does not match target body")
    expected_target = facts.get("compensation_target_digest", facts.get("target_digest"))
    if expected_target not in {None, recomputed_target}:
        _fail("payload_invalid", "compensation receipt target_digest does not match intent")

    # compensation_ref / reconcile_ref bind to intent and applied original receipt.
    expected_comp_ref = facts.get("compensation_ref")
    if expected_comp_ref is not None and parsed_receipt.compensation_ref != expected_comp_ref:
        _fail("payload_invalid", "compensation receipt compensation_ref mismatch")
    if parsed_receipt.compensation_ref != original_parsed.compensation_ref:
        _fail("payload_invalid", "compensation receipt compensation_ref != original")
    if parsed_receipt.reconcile_ref != original_parsed.reconcile_ref:
        _fail("payload_invalid", "compensation receipt reconcile_ref != original")

    # Classification binds to compensation observation classification when present.
    prior_class = facts.get("compensation_classification")
    if prior_class is not None and parsed_receipt.result_classification != prior_class:
        _fail("payload_invalid", "compensation receipt classification does not match observation")

    obs_digest = _require_digest(payload["observation_digest"], label="observation_digest")
    if obs_digest != canonical_digest_v1(observation):
        _fail("payload_invalid", "compensation receipt observation_digest mismatch")
    prior_comp_obs = facts.get("compensation_observation")
    if isinstance(prior_comp_obs, dict) and canonical_digest_v1(prior_comp_obs) != canonical_digest_v1(
        observation
    ):
        _fail("payload_invalid", "compensation receipt observation does not match prior")
    obs_parsed = _parse_observation(observation)
    if obs_parsed.effect_id != effect_id or obs_parsed.fence != fence:
        _fail("payload_invalid", "compensation receipt observation fence/effect mismatch")
    if obs_parsed.classification != parsed_receipt.result_classification and prior_class is None:
        if obs_parsed.classification in {"applied", "not_applied"}:
            _fail(
                "payload_invalid",
                "compensation receipt classification does not match observation body",
            )

    # Compensation receipt preflight/post bind to compensation intent/observation facts.
    _require_equal_readback_fact(
        actual=parsed_receipt.preflight_readback,
        expected=facts.get("compensation_preflight_readback"),
        expected_digest=facts.get("compensation_preflight_readback_digest"),
        label="compensation_receipt.preflight_readback",
    )
    _require_equal_readback_fact(
        actual=parsed_receipt.post_readback,
        expected=facts.get("compensation_readback"),
        expected_digest=facts.get("compensation_readback_digest"),
        label="compensation_receipt.post_readback",
    )
    # Original embedded receipt still binds original durable preflight/post.
    _require_equal_readback_fact(
        actual=original_parsed.preflight_readback,
        expected=facts.get("preflight_readback"),
        expected_digest=facts.get("preflight_readback_digest"),
        label="compensation_receipt.original_receipt.preflight_readback",
    )
    _require_equal_readback_fact(
        actual=original_parsed.post_readback,
        expected=facts.get("readback"),
        expected_digest=facts.get("readback_digest"),
        label="compensation_receipt.original_receipt.post_readback",
    )


def _validate_transition(status: str, event: dict[str, Any], facts: dict[str, Any]) -> None:
    event_type = event["event_type"]
    allowed = _ALLOWED_TRANSITIONS.get(status, frozenset())
    if event_type not in allowed:
        _fail(
            "invalid_transition",
            f"event_type {event_type} is not allowed from status {status}",
        )
    payload = event["payload"]
    if not isinstance(payload, dict) or not payload:
        _fail("payload_invalid", f"{event_type} payload must be a non-empty object")

    effect_id = event["effect_id"]

    if event_type == "claimed":
        required = {
            "command_digest",
            "target_digest",
            "owner",
            "request_digest",
            "idempotency_key",
            "adapter_id",
            "operation",
            "fence",
            "payload_digest",
            "effect_id",
        }
        _exact_keys(payload, required, label="claimed")
        _require_digest(payload["command_digest"], label="command_digest")
        _require_digest(payload["target_digest"], label="target_digest")
        _require_digest(payload["request_digest"], label="request_digest")
        _require_digest(payload["payload_digest"], label="payload_digest")
        _require_text(payload["owner"], label="owner")
        _require_text(payload["idempotency_key"], label="idempotency_key")
        _require_text(payload["adapter_id"], label="adapter_id")
        _require_text(payload["operation"], label="operation")
        if payload["effect_id"] != effect_id:
            _fail("payload_invalid", "claimed effect_id does not match event.effect_id")
        fence = payload["fence"]
        if not isinstance(fence, int) or isinstance(fence, bool) or fence < 1:
            _fail("payload_invalid", "claimed fence must be a positive integer")

    if event_type == "intent_recorded":
        required = {
            "fence",
            "command_digest",
            "request_digest",
            "target_digest",
            "authority_binding_digest",
            "preflight_readback",
            "preflight_readback_digest",
            "adapter_id",
            "operation",
            "payload_digest",
            "idempotency_key",
            "effect_id",
        }
        _exact_keys(payload, required, label="intent_recorded")
        fence = payload["fence"]
        if not isinstance(fence, int) or isinstance(fence, bool) or fence < 1:
            _fail("payload_invalid", "intent fence must be a positive integer")
        claimed_fence = facts.get("fence")
        if claimed_fence is not None and fence != claimed_fence:
            _fail("stale_fence", "intent fence does not match claimed fence")
        for key in ("command_digest", "request_digest", "target_digest", "payload_digest"):
            val = _require_digest(payload[key], label=key)
            if facts.get(key) not in {None, val}:
                _fail("payload_invalid", f"intent {key} does not match claim")
        for key in ("adapter_id", "operation", "idempotency_key"):
            val = _require_text(payload[key], label=key)
            if facts.get(key) not in {None, val}:
                _fail("payload_invalid", f"intent {key} does not match claim")
        if payload["effect_id"] != effect_id:
            _fail("payload_invalid", "intent effect_id mismatch")
        _require_digest(payload["authority_binding_digest"], label="authority_binding_digest")
        if not isinstance(payload.get("preflight_readback"), dict):
            _fail("payload_invalid", "preflight_readback must be an object")
        _require_digest(payload["preflight_readback_digest"], label="preflight_readback_digest")
        if payload["preflight_readback_digest"] != canonical_digest_v1(payload["preflight_readback"]):
            _fail("payload_invalid", "preflight_readback_digest mismatch")

    if event_type == "observation_recorded":
        required = {
            "fence",
            "classification",
            "adapter_result_digest",
            "readback",
            "readback_digest",
            "observation",
            "command_digest",
            "request_digest",
            "target_digest",
            "authority_binding_digest",
            "adapter_id",
            "operation",
            "payload_digest",
            "idempotency_key",
            "effect_id",
        }
        _exact_keys(payload, required, label="observation_recorded")
        obs = payload.get("observation")
        if not isinstance(obs, dict):
            _fail("payload_invalid", "observation_recorded requires full observation object")
        parsed = _parse_observation(obs)
        if parsed.effect_id != effect_id or payload["effect_id"] != effect_id:
            _fail("payload_invalid", "observation.effect_id does not match ledger effect_id")
        if payload.get("classification") != parsed.classification:
            _fail("payload_invalid", "observation classification mismatch")
        if payload.get("fence") != parsed.fence:
            _fail("payload_invalid", "observation fence mismatch")
        claimed_fence = facts.get("fence")
        if claimed_fence is not None and parsed.fence != claimed_fence:
            _fail("stale_fence", "observation fence does not match claimed fence")
        for key in ("command_digest", "request_digest", "target_digest", "payload_digest"):
            val = _require_digest(payload[key], label=key)
            if facts.get(key) not in {None, val}:
                _fail("payload_invalid", f"observation {key} does not match claim")
        for key in ("adapter_id", "operation", "idempotency_key"):
            val = _require_text(payload[key], label=key)
            if facts.get(key) not in {None, val}:
                _fail("payload_invalid", f"observation {key} does not match claim")
        _require_digest(payload["authority_binding_digest"], label="authority_binding_digest")
        if facts.get("authority_binding_digest") not in {None, payload["authority_binding_digest"]}:
            _fail("payload_invalid", "observation authority_binding_digest does not match intent")
        # Exact wrapper vs embedded adapter_result_digest (null vs non-null must reject).
        if payload.get("adapter_result_digest") != parsed.adapter_result_digest:
            _fail("payload_invalid", "adapter_result_digest mismatch")
        if canonical_digest_v1(payload.get("readback")) != canonical_digest_v1(
            thaw_admitted_json_v1(parsed.readback)
        ):
            _fail("payload_invalid", "observation readback mismatch")
        readback_digest = _require_digest(payload["readback_digest"], label="readback_digest")
        if readback_digest != _fact_digest(payload.get("readback")):
            _fail("payload_invalid", "observation readback_digest mismatch")
        if readback_digest != _fact_digest(parsed.readback):
            _fail("payload_invalid", "observation body readback_digest mismatch")

    if event_type == "receipt_terminal":
        required = {
            "fence",
            "result_classification",
            "receipt_digest",
            "command_digest",
            "request_digest",
            "target_digest",
            "authority_binding_digest",
            "observation_digest",
            "receipt",
            "adapter_id",
            "operation",
            "payload_digest",
            "idempotency_key",
            "effect_id",
            "observation",
        }
        _exact_keys(payload, required, label="receipt_terminal")
        receipt = payload.get("receipt")
        if not isinstance(receipt, dict):
            _fail("payload_invalid", "receipt_terminal requires full receipt object")
        parsed = _parse_receipt(receipt)
        if parsed.effect_id != effect_id or payload["effect_id"] != effect_id:
            _fail("payload_invalid", "receipt.effect_id does not match ledger effect_id")
        if payload.get("receipt_digest") != parsed.receipt_digest:
            _fail("payload_invalid", "receipt_digest does not match receipt body")
        if payload.get("result_classification") != parsed.result_classification:
            _fail("payload_invalid", "result_classification mismatch")
        if payload.get("fence") != parsed.fence:
            _fail("payload_invalid", "receipt wrapper fence does not match receipt body fence")
        claimed_fence = facts.get("fence")
        if claimed_fence is not None and parsed.fence != claimed_fence:
            _fail("stale_fence", "receipt fence does not match claimed fence")
        prior_class = facts.get("reconcile_classification") or facts.get("classification")
        if prior_class is not None and prior_class != parsed.result_classification:
            _fail(
                "payload_invalid",
                "receipt classification does not match prior observation/reconcile",
            )
        for key in ("command_digest", "request_digest", "target_digest", "payload_digest"):
            val = _require_digest(payload[key], label=key)
            if facts.get(key) not in {None, val}:
                _fail("payload_invalid", f"receipt {key} does not match claim")
        if parsed.command_digest != payload["command_digest"]:
            _fail("payload_invalid", "receipt body command_digest mismatch")
        if parsed.adapter_id != payload["adapter_id"] or facts.get("adapter_id") not in {None, parsed.adapter_id}:
            _fail("payload_invalid", "receipt adapter_id mismatch")
        if parsed.operation != payload["operation"] or facts.get("operation") not in {None, parsed.operation}:
            _fail("payload_invalid", "receipt operation mismatch")
        if parsed.payload_digest != payload["payload_digest"]:
            _fail("payload_invalid", "receipt payload_digest mismatch")
        if parsed.idempotency_key != payload["idempotency_key"] or facts.get("idempotency_key") not in {
            None,
            parsed.idempotency_key,
        }:
            _fail("payload_invalid", "receipt idempotency_key mismatch")
        auth = _require_digest(payload["authority_binding_digest"], label="authority_binding_digest")
        if parsed.authority_binding_digest != auth:
            _fail("payload_invalid", "receipt authority_binding_digest mismatch")
        if facts.get("authority_binding_digest") not in {None, auth}:
            _fail("payload_invalid", "receipt authority does not match intent")
        # Recompute target_digest from receipt target body and bind to wrapper/claim.
        recomputed_target = canonical_digest_v1(
            {
                "target_kind": parsed.target["target_kind"],
                "target_ref": parsed.target["target_ref"],
            }
        )
        if payload["target_digest"] != recomputed_target:
            _fail("payload_invalid", "receipt target_digest does not match receipt target body")
        if facts.get("target_digest") not in {None, recomputed_target}:
            _fail("payload_invalid", "receipt target_digest does not match claim")
        obs_digest = _require_digest(payload["observation_digest"], label="observation_digest")
        obs = payload.get("observation")
        if not isinstance(obs, dict):
            _fail("payload_invalid", "receipt requires exact observation body")
        if obs_digest != canonical_digest_v1(obs):
            _fail("payload_invalid", "receipt observation_digest mismatch")
        prior_obs = facts.get("observation")
        # Always canonical-bind embedded observation body to durable observation.
        # Reconciliation may relax only receipt result_classification / post_readback
        # against the unknown observation; it must never permit changed
        # observation_id/timestamp/fence/readback/body.
        if isinstance(prior_obs, dict):
            if canonical_digest_v1(prior_obs) != canonical_digest_v1(obs):
                _fail("payload_invalid", "receipt observation does not match durable observation")
        # Bind receipt fence/effect/command/request to claim/observation.
        obs_parsed = _parse_observation(obs)
        if obs_parsed.fence != parsed.fence or obs_parsed.effect_id != parsed.effect_id:
            _fail("payload_invalid", "receipt observation fence/effect binding mismatch")
        if obs_parsed.classification != parsed.result_classification:
            if facts.get("reconcile_classification") is not None:
                # Explicit unknown → reconciled relaxation: receipt classification may
                # follow reconcile_classification rather than embedded observation class.
                pass
            elif obs_parsed.classification not in {"applied", "not_applied"}:
                pass
            else:
                _fail("payload_invalid", "receipt classification does not match observation")

        # Terminal receipt preflight/post must equal durable intent/observation facts.
        _require_equal_readback_fact(
            actual=parsed.preflight_readback,
            expected=facts.get("preflight_readback"),
            expected_digest=facts.get("preflight_readback_digest"),
            label="receipt.preflight_readback",
        )
        # Observed and reconciled paths both bind terminal post_readback to a
        # durable fact+digest pair (observation readback or reconcile readback).
        if status in {"observed", "reconciled"}:
            _require_equal_readback_fact(
                actual=parsed.post_readback,
                expected=facts.get("readback"),
                expected_digest=facts.get("readback_digest"),
                label="receipt.post_readback",
            )

    if event_type == "reconciled":
        if status not in {"unknown", "intent_recorded"}:
            _fail("invalid_transition", "reconcile is only valid from unknown/intent_recorded")
        required = {
            "classification",
            "fence",
            "command_digest",
            "request_digest",
            "target_digest",
            "evidence_digest",
            "adapter_id",
            "operation",
            "payload_digest",
            "idempotency_key",
            "effect_id",
            "authority_binding_digest",
            "readback",
            "readback_digest",
        }
        _exact_keys(payload, required, label="reconciled")
        if payload.get("classification") not in {"applied", "not_applied"}:
            _fail("payload_invalid", "reconcile classification must be terminal")
        fence = payload["fence"]
        if not isinstance(fence, int) or isinstance(fence, bool) or fence < 1:
            _fail("payload_invalid", "reconcile fence invalid")
        if facts.get("fence") not in {None, fence}:
            _fail("stale_fence", "reconcile fence does not match claimed fence")
        for key in ("command_digest", "request_digest", "target_digest", "payload_digest", "evidence_digest", "authority_binding_digest"):
            val = _require_digest(payload[key], label=key)
            if key != "evidence_digest" and facts.get(key) not in {None, val}:
                _fail("payload_invalid", f"reconcile {key} mismatch")
        for key in ("adapter_id", "operation", "idempotency_key"):
            val = _require_text(payload[key], label=key)
            if facts.get(key) not in {None, val}:
                _fail("payload_invalid", f"reconcile {key} mismatch")
        if payload["effect_id"] != effect_id:
            _fail("payload_invalid", "reconcile effect_id mismatch")
        if not isinstance(payload.get("readback"), dict):
            _fail("payload_invalid", "reconcile readback must be an object")
        readback_digest = _require_digest(payload["readback_digest"], label="readback_digest")
        if readback_digest != _fact_digest(payload["readback"]):
            _fail("payload_invalid", "reconcile readback_digest mismatch")

    if event_type == "compensation_intent":
        if status != "applied":
            _fail(
                "invalid_transition",
                "compensation requires proven applied terminal receipt",
            )
        required = {
            "original_receipt_digest",
            "original_receipt",
            "compensation_ref",
            "request_digest",
            "command_digest",
            "target_digest",
            "adapter_id",
            "operation",
            "payload_digest",
            "idempotency_key",
            "effect_id",
            "fence",
            "authority_binding_digest",
            "preflight_readback",
            "preflight_readback_digest",
        }
        _exact_keys(payload, required, label="compensation_intent")
        original = _require_digest(payload.get("original_receipt_digest"), label="original_receipt_digest")
        terminal = facts.get("terminal_receipt_digest")
        if terminal is None:
            _fail(
                "compensation_without_terminal",
                "compensation requires an exact applied terminal receipt",
            )
        if original != terminal:
            _fail(
                "payload_invalid",
                "compensation must bind the exact original terminal receipt digest",
            )
        stored = facts.get("terminal_receipt")
        original_receipt = payload.get("original_receipt")
        if not isinstance(original_receipt, dict):
            _fail("payload_invalid", "compensation requires exact original_receipt body")
        parsed = _parse_receipt(original_receipt)
        if parsed.receipt_digest != original:
            _fail("payload_invalid", "original_receipt digest mismatch")
        if parsed.result_classification != "applied":
            _fail(
                "compensation_without_terminal",
                "compensation requires applied original receipt classification",
            )
        if isinstance(stored, dict) and stored != original_receipt:
            # compare via digest
            stored_parsed = _parse_receipt(stored)
            if stored_parsed.receipt_digest != original:
                _fail("compensation_receipt_mismatch", "stored terminal receipt digest mismatch")
        # Original embedded receipt must still bind original durable preflight/post facts.
        _require_equal_readback_fact(
            actual=parsed.preflight_readback,
            expected=facts.get("preflight_readback"),
            expected_digest=facts.get("preflight_readback_digest"),
            label="compensation.original_receipt.preflight_readback",
        )
        _require_equal_readback_fact(
            actual=parsed.post_readback,
            expected=facts.get("readback"),
            expected_digest=facts.get("readback_digest"),
            label="compensation.original_receipt.post_readback",
        )
        for key in ("command_digest", "request_digest", "target_digest", "payload_digest", "authority_binding_digest"):
            val = _require_digest(payload[key], label=key)
            if key == "request_digest":
                continue
            if facts.get(key) not in {None, val} and key != "authority_binding_digest":
                # command/target from original claim
                if key in {"command_digest", "target_digest", "payload_digest"} and facts.get(key) not in {None, val}:
                    _fail("payload_invalid", f"compensation {key} mismatch")
        for key in ("adapter_id", "operation", "idempotency_key", "compensation_ref"):
            _require_text(payload[key], label=key)
        if not isinstance(payload.get("preflight_readback"), dict):
            _fail("payload_invalid", "compensation preflight_readback must be an object")
        preflight_digest = _require_digest(
            payload["preflight_readback_digest"], label="preflight_readback_digest"
        )
        if preflight_digest != _fact_digest(payload["preflight_readback"]):
            _fail("payload_invalid", "compensation preflight_readback_digest mismatch")
        if payload["effect_id"] != effect_id:
            _fail("payload_invalid", "compensation intent effect_id mismatch")
        fence = payload["fence"]
        if not isinstance(fence, int) or isinstance(fence, bool) or fence < 1:
            _fail("payload_invalid", "compensation intent fence invalid")
        if facts.get("fence") not in {None, fence}:
            _fail("payload_invalid", "compensation intent fence mismatch")

    if event_type == "compensation_observation":
        required = {
            "classification",
            "observation",
            "readback",
            "readback_digest",
            "original_receipt_digest",
            "original_receipt",
            "fence",
            "command_digest",
            "request_digest",
            "target_digest",
            "adapter_id",
            "operation",
            "payload_digest",
            "idempotency_key",
            "effect_id",
            "authority_binding_digest",
        }
        _exact_keys(payload, required, label="compensation_observation")
        if facts.get("original_receipt_digest") is None:
            _fail("compensation_without_terminal", "compensation observation unbound")
        obs = payload.get("observation")
        if not isinstance(obs, dict):
            _fail("payload_invalid", "compensation observation requires body")
        parsed = _parse_observation(obs)
        if payload.get("classification") != parsed.classification:
            _fail("payload_invalid", "compensation observation classification mismatch")
        if parsed.classification not in {"applied", "not_applied", "unknown"}:
            _fail("payload_invalid", "compensation observation classification invalid")
        original = _require_digest(payload["original_receipt_digest"], label="original_receipt_digest")
        if original != facts.get("original_receipt_digest"):
            _fail(
                "payload_invalid",
                "compensation observation original receipt mismatch",
            )
        original_receipt = payload.get("original_receipt")
        if not isinstance(original_receipt, dict):
            _fail("payload_invalid", "compensation observation requires original_receipt")
        if _parse_receipt(original_receipt).receipt_digest != original:
            _fail("payload_invalid", "compensation observation original body mismatch")
        if payload["effect_id"] != effect_id:
            _fail("payload_invalid", "compensation observation effect_id mismatch")
        if parsed.effect_id != effect_id:
            _fail("payload_invalid", "compensation observation body effect_id mismatch")
        fence = payload["fence"]
        if not isinstance(fence, int) or isinstance(fence, bool) or fence < 1:
            _fail("payload_invalid", "compensation observation fence invalid")
        # Bind to complete compensation-intent facts (not merely original claim fence).
        intent_fence = facts.get("compensation_fence")
        if intent_fence is None:
            intent_fence = facts.get("fence")
        if intent_fence not in {None, fence}:
            _fail("payload_invalid", "compensation observation fence mismatch")
        if parsed.fence != fence:
            _fail("payload_invalid", "compensation observation body fence mismatch")
        for key in (
            "command_digest",
            "request_digest",
            "target_digest",
            "payload_digest",
            "authority_binding_digest",
        ):
            val = _require_digest(payload[key], label=key)
            expected = facts.get(f"compensation_{key}", facts.get(key))
            if key == "request_digest":
                expected = facts.get("compensation_request_digest", expected)
            if expected not in {None, val}:
                _fail("payload_invalid", f"compensation observation {key} mismatch")
        for key in ("adapter_id", "operation", "idempotency_key"):
            val = _require_text(payload[key], label=key)
            expected = facts.get(f"compensation_{key}", facts.get(key))
            if expected not in {None, val}:
                _fail("payload_invalid", f"compensation observation {key} mismatch")
        if facts.get("compensation_effect_id") not in {None, effect_id, payload["effect_id"]}:
            _fail("payload_invalid", "compensation observation effect binding mismatch")
        if not isinstance(payload.get("readback"), dict):
            _fail("payload_invalid", "compensation observation readback must be an object")
        readback_digest = _require_digest(payload["readback_digest"], label="readback_digest")
        if readback_digest != _fact_digest(payload["readback"]):
            _fail("payload_invalid", "compensation observation readback_digest mismatch")
        if canonical_digest_v1(payload["readback"]) != canonical_digest_v1(
            thaw_admitted_json_v1(parsed.readback)
        ):
            _fail("payload_invalid", "compensation observation wrapper/body readback mismatch")
        # Original embedded receipt still binds original durable facts.
        original_parsed = _parse_receipt(original_receipt)
        _require_equal_readback_fact(
            actual=original_parsed.preflight_readback,
            expected=facts.get("preflight_readback"),
            expected_digest=facts.get("preflight_readback_digest"),
            label="compensation_observation.original_receipt.preflight_readback",
        )
        _require_equal_readback_fact(
            actual=original_parsed.post_readback,
            expected=facts.get("readback"),
            expected_digest=facts.get("readback_digest"),
            label="compensation_observation.original_receipt.post_readback",
        )

    if event_type == "compensation_receipt":
        required = {
            "receipt_digest",
            "receipt",
            "original_receipt_digest",
            "original_receipt",
            "fence",
            "command_digest",
            "request_digest",
            "target_digest",
            "adapter_id",
            "operation",
            "payload_digest",
            "idempotency_key",
            "effect_id",
            "authority_binding_digest",
            "observation_digest",
            "observation",
        }
        _exact_keys(payload, required, label="compensation_receipt")
        if facts.get("original_receipt_digest") is None:
            _fail("compensation_without_terminal", "compensation receipt unbound")
        receipt = payload.get("receipt")
        if not isinstance(receipt, dict):
            _fail("payload_invalid", "compensation receipt requires body")
        parsed = _parse_receipt(receipt)
        obs = payload.get("observation")
        if not isinstance(obs, dict):
            _fail("payload_invalid", "compensation receipt requires observation")
        original_receipt = payload.get("original_receipt")
        if not isinstance(original_receipt, dict):
            _fail("payload_invalid", "compensation receipt requires original_receipt")
        _validate_compensation_receipt_binding_v1(
            effect_id=effect_id,
            payload=payload,
            parsed_receipt=parsed,
            observation=obs,
            original_receipt=original_receipt,
            facts=facts,
        )


def _replay(events: list[dict[str, Any]]) -> None:
    last: dict[str, Any] | None = None
    by_key: dict[str, bytes] = {}
    status = "absent"
    facts: dict[str, Any] = {}
    for event in events:
        canonical = _compact_json(event)
        existing = by_key.get(event["idempotency_key"])
        if existing is not None and existing != canonical:
            _fail("idempotency_conflict", "idempotency key maps to another event")
        by_key[event["idempotency_key"]] = canonical
        if last is not None and event["effect_id"] != last["effect_id"]:
            _fail("mixed_effect", "one effect ledger cannot mix effect_id values")
        expected_sequence = 1 if last is None else last["sequence"] + 1
        if event["sequence"] != expected_sequence:
            _fail("sequence_mismatch", "event sequence is not contiguous")
        expected_previous = None if last is None else last["event_id"]
        if event["previous_event_id"] != expected_previous:
            _fail("broken_link", "previous_event_id does not match tip")
        _validate_transition(status, event, facts)
        # Retain immutable complete claim facts and complete compensation-intent facts.
        payload = event["payload"]
        if event["event_type"] == "claimed":
            facts["owner"] = payload.get("owner")
            facts["request_digest"] = payload.get("request_digest")
            facts["command_digest"] = payload.get("command_digest")
            facts["target_digest"] = payload.get("target_digest")
            facts["fence"] = payload.get("fence")
            facts["adapter_id"] = payload.get("adapter_id")
            facts["operation"] = payload.get("operation")
            facts["idempotency_key"] = payload.get("idempotency_key")
            facts["payload_digest"] = payload.get("payload_digest")
            facts["effect_id"] = event.get("effect_id")
            facts["claim"] = dict(payload)
        elif event["event_type"] == "intent_recorded":
            facts["authority_binding_digest"] = payload.get("authority_binding_digest")
            facts["preflight_readback"] = payload.get("preflight_readback")
            facts["preflight_readback_digest"] = payload.get("preflight_readback_digest")
            facts["intent"] = dict(payload)
        elif event["event_type"] == "observation_recorded":
            obs = payload.get("observation")
            if isinstance(obs, dict):
                facts["observation"] = obs
                facts["classification"] = obs.get("classification")
                facts["readback"] = obs.get("readback")
            else:
                facts["classification"] = payload.get("classification")
                facts["readback"] = payload.get("readback")
            facts["adapter_result_digest"] = payload.get("adapter_result_digest")
            # Durable post-result readback digest for terminal receipt binding.
            if "readback_digest" in payload:
                facts["readback_digest"] = payload.get("readback_digest")
            elif facts.get("readback") is not None:
                facts["readback_digest"] = _fact_digest(facts["readback"])
        elif event["event_type"] == "reconciled":
            facts["reconcile_classification"] = payload.get("classification")
            # Durable reconciliation post-readback fact for terminal receipt binding.
            facts["readback"] = payload.get("readback")
            facts["readback_digest"] = payload.get("readback_digest")
            if facts.get("readback_digest") is None and facts.get("readback") is not None:
                facts["readback_digest"] = _fact_digest(facts["readback"])
            facts["reconcile_readback"] = facts.get("readback")
            facts["reconcile_readback_digest"] = facts.get("readback_digest")
            facts["reconcile"] = dict(payload)
        elif event["event_type"] == "receipt_terminal":
            receipt = payload.get("receipt")
            if isinstance(receipt, dict):
                parsed = _parse_receipt(receipt)
                facts["terminal_receipt"] = effect_receipt_to_dict(parsed)
                facts["terminal_receipt_digest"] = parsed.receipt_digest
                facts["result_classification"] = parsed.result_classification
            else:
                facts["terminal_receipt_digest"] = payload.get("receipt_digest")
                facts["result_classification"] = payload.get("result_classification")
        elif event["event_type"] == "compensation_intent":
            facts["compensation_request_digest"] = payload.get("request_digest")
            facts["compensation_command_digest"] = payload.get("command_digest")
            facts["compensation_target_digest"] = payload.get("target_digest")
            facts["compensation_payload_digest"] = payload.get("payload_digest")
            facts["compensation_authority_binding_digest"] = payload.get(
                "authority_binding_digest"
            )
            facts["compensation_adapter_id"] = payload.get("adapter_id")
            facts["compensation_operation"] = payload.get("operation")
            facts["compensation_idempotency_key"] = payload.get("idempotency_key")
            facts["compensation_effect_id"] = payload.get("effect_id")
            facts["compensation_fence"] = payload.get("fence")
            facts["compensation_ref"] = payload.get("compensation_ref")
            facts["original_receipt_digest"] = payload.get("original_receipt_digest")
            facts["original_receipt"] = payload.get("original_receipt")
            facts["compensation_preflight_readback"] = payload.get("preflight_readback")
            facts["compensation_preflight_readback_digest"] = payload.get(
                "preflight_readback_digest"
            )
            facts["compensation_intent"] = dict(payload)
        elif event["event_type"] == "compensation_observation":
            obs = payload.get("observation")
            if isinstance(obs, dict):
                facts["compensation_observation"] = obs
                facts["compensation_classification"] = obs.get("classification")
                facts["compensation_readback"] = obs.get("readback")
            else:
                facts["compensation_classification"] = payload.get("classification")
                facts["compensation_readback"] = payload.get("readback")
            if "readback_digest" in payload:
                facts["compensation_readback_digest"] = payload.get("readback_digest")
            elif facts.get("compensation_readback") is not None:
                facts["compensation_readback_digest"] = _fact_digest(
                    facts["compensation_readback"]
                )
        status = _status_after_event(status, event)
        last = event


def _decode_ledger(raw: bytes) -> list[dict[str, Any]]:
    if not raw:
        return []
    if not raw.endswith(b"\n"):
        _fail("torn_tail", "ledger does not end with a complete JSONL record")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        _fail("invalid_utf8", str(exc))
    events: list[dict[str, Any]] = []
    for index, line in enumerate(text[:-1].split("\n")):
        if not line:
            _fail("invalid_json", f"empty JSONL record at index {index}")
        try:
            candidate = json.loads(
                line,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_constant,
                parse_int=_reject_non_integer_json_number,
                parse_float=_reject_non_integer_json_number,
            )
        except WorkflowEffectLedgerError:
            raise
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            _fail("invalid_json", f"invalid JSONL record at index {index}: {exc}")
        events.append(_normalized_event(candidate))
    _replay(events)
    return events


def _project(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not events:
        return {
            "effect_id": None,
            "status": "absent",
            "fence": None,
            "facts": {},
            "terminal_receipt_digest": None,
            "terminal_receipt": None,
            "observation": None,
            "event_count": 0,
        }
    effect_id = events[0]["effect_id"]
    status = "absent"
    fence = None
    terminal_receipt_digest = None
    terminal_receipt = None
    observation = None
    facts: dict[str, Any] = {}
    for event in events:
        payload = event["payload"]
        event_type = event["event_type"]
        if event_type == "claimed":
            fence = payload.get("fence")
            facts["owner"] = payload.get("owner")
            facts["request_digest"] = payload.get("request_digest")
            facts["command_digest"] = payload.get("command_digest")
            facts["target_digest"] = payload.get("target_digest")
            facts["idempotency_key"] = payload.get("idempotency_key")
            facts["adapter_id"] = payload.get("adapter_id")
            facts["operation"] = payload.get("operation")
            facts["payload_digest"] = payload.get("payload_digest")
            facts["effect_id"] = event.get("effect_id")
        elif event_type == "intent_recorded":
            facts["authority_binding_digest"] = payload.get("authority_binding_digest")
            facts["preflight_readback"] = payload.get("preflight_readback")
            facts["preflight_readback_digest"] = payload.get("preflight_readback_digest")
        elif event_type == "observation_recorded":
            obs = payload.get("observation")
            if isinstance(obs, dict):
                observation = obs
                facts["classification"] = obs.get("classification")
                facts["readback"] = obs.get("readback")
                facts["observation"] = obs
            else:
                facts["classification"] = payload.get("classification")
                facts["readback"] = payload.get("readback")
                observation = {
                    "classification": payload.get("classification"),
                    "adapter_result_digest": payload.get("adapter_result_digest"),
                    "readback": payload.get("readback"),
                    "fence": payload.get("fence"),
                }
            if "readback_digest" in payload:
                facts["readback_digest"] = payload.get("readback_digest")
            elif facts.get("readback") is not None:
                facts["readback_digest"] = _fact_digest(facts["readback"])
        elif event_type == "reconciled":
            facts["reconcile_classification"] = payload.get("classification")
            facts["readback"] = payload.get("readback")
            if "readback_digest" in payload:
                facts["readback_digest"] = payload.get("readback_digest")
            elif facts.get("readback") is not None:
                facts["readback_digest"] = _fact_digest(facts["readback"])
            facts["reconcile_readback"] = facts.get("readback")
            facts["reconcile_readback_digest"] = facts.get("readback_digest")
            facts["reconcile"] = dict(payload)
        elif event_type == "receipt_terminal":
            receipt = payload.get("receipt")
            if isinstance(receipt, dict):
                parsed = _parse_receipt(receipt)
                terminal_receipt = effect_receipt_to_dict(parsed)
                terminal_receipt_digest = parsed.receipt_digest
                facts["result_classification"] = parsed.result_classification
                facts["terminal_receipt"] = terminal_receipt
            else:
                terminal_receipt_digest = payload.get("receipt_digest")
                facts["result_classification"] = payload.get("result_classification")
        elif event_type == "compensation_intent":
            facts["compensation_request_digest"] = payload.get("request_digest")
            facts["compensation_command_digest"] = payload.get("command_digest")
            facts["compensation_target_digest"] = payload.get("target_digest")
            facts["compensation_payload_digest"] = payload.get("payload_digest")
            facts["compensation_authority_binding_digest"] = payload.get(
                "authority_binding_digest"
            )
            facts["compensation_adapter_id"] = payload.get("adapter_id")
            facts["compensation_operation"] = payload.get("operation")
            facts["compensation_idempotency_key"] = payload.get("idempotency_key")
            facts["compensation_effect_id"] = payload.get("effect_id")
            facts["compensation_fence"] = payload.get("fence")
            facts["compensation_ref"] = payload.get("compensation_ref")
            facts["original_receipt_digest"] = payload.get("original_receipt_digest")
            facts["original_receipt"] = payload.get("original_receipt")
            facts["compensation_preflight_readback"] = payload.get("preflight_readback")
            facts["compensation_preflight_readback_digest"] = payload.get(
                "preflight_readback_digest"
            )
            facts["compensation_intent"] = dict(payload)
        elif event_type == "compensation_observation":
            obs = payload.get("observation")
            if isinstance(obs, dict):
                facts["compensation_observation"] = obs
                facts["compensation_classification"] = obs.get("classification")
                facts["compensation_readback"] = obs.get("readback")
            else:
                facts["compensation_classification"] = payload.get("classification")
                facts["compensation_readback"] = payload.get("readback")
            if "readback_digest" in payload:
                facts["compensation_readback_digest"] = payload.get("readback_digest")
            elif facts.get("compensation_readback") is not None:
                facts["compensation_readback_digest"] = _fact_digest(
                    facts["compensation_readback"]
                )
            # Retain compensation-local continuity facts from observation payload too.
            for key in (
                "request_digest",
                "command_digest",
                "target_digest",
                "payload_digest",
                "authority_binding_digest",
                "adapter_id",
                "operation",
                "idempotency_key",
                "fence",
            ):
                if f"compensation_{key}" not in facts and key in payload:
                    facts[f"compensation_{key}"] = payload.get(key)
            if "original_receipt_digest" in payload:
                facts["original_receipt_digest"] = payload.get("original_receipt_digest")
            if "original_receipt" in payload:
                facts["original_receipt"] = payload.get("original_receipt")
        # _status_after_event for first event uses claimed path; bootstrap:
        if event_type == "claimed":
            status = "claimed"
            facts["fence"] = payload.get("fence")
        else:
            # recompute cleanly from running status already updated above when not first
            pass
    # Recompute status cleanly to avoid bootstrap quirks.
    status = "absent"
    for event in events:
        status = _status_after_event(status, event)
    return {
        "effect_id": effect_id,
        "status": status,
        "fence": fence,
        "facts": facts,
        "terminal_receipt_digest": terminal_receipt_digest,
        "terminal_receipt": terminal_receipt,
        "observation": observation,
        "event_count": len(events),
    }


def _read_events_from_path(path: Path, *, private_root: Path) -> list[dict[str, Any]]:
    try:
        raw = read_existing_private_file_bytes(path, private_root=private_root)
    except FileNotFoundError:
        return []
    except OSError as exc:
        _fail("unsafe_target", str(exc))
    return _decode_ledger(raw)


def _read_effect_events_from_pins(
    *, child_pins: Mapping[str, _DirPin], effect_id: str
) -> list[dict[str, Any]]:
    leaf_name = f"{_name_for(effect_id)}.jsonl"
    try:
        raw = _read_bytes_at_dirfd(child_pins["effects"].fd, leaf_name)
    except FileNotFoundError:
        return []
    return _decode_ledger(raw)


def _project_effect_from_pins(
    *, child_pins: Mapping[str, _DirPin], effect_id: str
) -> dict[str, Any]:
    projection = _project(_read_effect_events_from_pins(child_pins=child_pins, effect_id=effect_id))
    if projection["effect_id"] is None:
        projection["effect_id"] = effect_id
    return _publicize_projection(projection)


def verify_target_ownership_identity_v1(ownership: TargetOwnershipV1) -> None:
    _verify_transaction_identity(
        ownership.root_identity, ownership.child_pins, ownership._nested_pins
    )


def project_effect_under_ownership_v1(
    *, ownership: TargetOwnershipV1, effect_id: str
) -> dict[str, Any]:
    verify_target_ownership_identity_v1(ownership)
    projection = _project_effect_from_pins(
        child_pins=ownership.child_pins,
        effect_id=_require_text(effect_id, label="effect_id"),
    )
    verify_target_ownership_identity_v1(ownership)
    return projection


def read_effect_events_under_ownership_v1(
    *, ownership: TargetOwnershipV1, effect_id: str
) -> tuple[Any, ...]:
    """Read events through the descriptor-pinned namespace held by ownership."""
    verify_target_ownership_identity_v1(ownership)
    events = _read_effect_events_from_pins(
        child_pins=ownership.child_pins,
        effect_id=_require_text(effect_id, label="effect_id"),
    )
    result = tuple(_freeze_public_event(event) for event in events)
    verify_target_ownership_identity_v1(ownership)
    return result


def read_effect_events_v1(*, private_root: Path | str, effect_id: str) -> tuple[Any, ...]:
    root, identity = _validated_root(private_root)
    _verify_root_identity(identity)
    # Pin canonical child identities; replaced/missing effects dir is corruption when sealed.
    pins = _pin_child_directories(root, identity)
    try:
        _verify_transaction_identity(identity, pins)
        seal = _read_layout_seal(root, identity)
        if seal is not None:
            expected = _layout_seal_document(pins)
            if seal.get("children") != expected["children"]:
                _fail(
                    "layout_corrupt",
                    "canonical effects/child directory identity does not match layout seal",
                )
        # Missing leaf is absent only inside the anchored effects directory.
        effects_pin = pins["effects"]
        leaf_name = f"{_name_for(effect_id)}.jsonl"
        try:
            raw = _read_bytes_at_dirfd(effects_pin.fd, leaf_name)
        except FileNotFoundError:
            return ()
        except WorkflowEffectLedgerError:
            raise
        except OSError as exc:
            _fail("unsafe_path", str(exc))
        events = _decode_ledger(raw)
        return tuple(_freeze_public_event(event) for event in events)
    finally:
        _close_dir_pins(pins)
        _close_root_identity(identity)


def project_effect_v1(*, private_root: Path | str, effect_id: str) -> dict[str, Any]:
    # Internal decode uses thawed events; freeze only the public projection.
    root, identity = _validated_root(private_root)
    _verify_root_identity(identity)
    pins = _pin_child_directories(root, identity)
    try:
        _verify_transaction_identity(identity, pins)
        seal = _read_layout_seal(root, identity)
        if seal is not None:
            expected = _layout_seal_document(pins)
            if seal.get("children") != expected["children"]:
                _fail(
                    "layout_corrupt",
                    "canonical effects/child directory identity does not match layout seal",
                )
        effects_pin = pins["effects"]
        leaf_name = f"{_name_for(effect_id)}.jsonl"
        try:
            raw = _read_bytes_at_dirfd(effects_pin.fd, leaf_name)
            events = _decode_ledger(raw)
        except FileNotFoundError:
            events = []
        except WorkflowEffectLedgerError:
            raise
        except OSError as exc:
            _fail("unsafe_path", str(exc))
    finally:
        _close_dir_pins(pins)
        _close_root_identity(identity)
    projection = _project(events)
    if projection["effect_id"] is None:
        projection["effect_id"] = effect_id
    # Crash after durable intent before observation is reconcile-only unknown.
    if projection["status"] == "intent_recorded":
        projection = {
            **projection,
            "status": "unknown",
            "facts": {
                **dict(projection.get("facts") or {}),
                "awaiting_reconcile": True,
                "durable_phase": "intent_recorded",
            },
        }
    return _freeze_public_projection(projection)


def _publicize_projection(projection: dict[str, Any]) -> dict[str, Any]:
    if projection.get("status") == "intent_recorded":
        projection = {
            **projection,
            "status": "unknown",
            "facts": {
                **dict(projection.get("facts") or {}),
                "awaiting_reconcile": True,
                "durable_phase": "intent_recorded",
            },
        }
    return _freeze_public_projection(projection)


def _read_json_private(path: Path, *, private_root: Path) -> dict[str, Any] | None:
    try:
        raw = read_existing_private_file_bytes(path, private_root=private_root)
    except FileNotFoundError:
        return None
    except OSError as exc:
        _fail("unsafe_target", str(exc))
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail("invalid_json", str(exc))
    if not isinstance(value, dict):
        _fail("invalid_json", "expected object document")
    return value


def _allocate_fence(
    private_root: Path,
    target_digest: str,
    *,
    identity: _RootIdentity,
    child_pins: Mapping[str, _DirPin] | None = None,
) -> int:
    _verify_transaction_identity(identity, child_pins)
    name = f"{_name_for(target_digest)}.json"
    current = None
    if child_pins is not None:
        fences_fd = child_pins["fences"].fd
        try:
            raw = _read_bytes_at_dirfd(fences_fd, name)
            current = json.loads(raw.decode("utf-8"))
        except FileNotFoundError:
            current = None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            _fail("fence_corrupt", str(exc))
    else:
        path = _fence_path(private_root, target_digest)
        current = _read_json_private(path, private_root=private_root)
    next_fence = 1
    if current is not None:
        if not isinstance(current, dict):
            _fail("fence_corrupt", "target fence document invalid")
        if current.get("schema_version") != _FENCE_SCHEMA_VERSION:
            _fail("fence_corrupt", "target fence document schema mismatch")
        stored_target = current.get("target_digest")
        if stored_target != target_digest:
            _fail(
                "fence_identity_mismatch",
                "fence document target_digest does not match requested target",
            )
        value = current.get("fence")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            _fail("fence_corrupt", "target fence counter invalid")
        next_fence = value + 1
    document = {
        "schema_version": _FENCE_SCHEMA_VERSION,
        "target_digest": target_digest,
        "fence": next_fence,
    }
    payload = _compact_json(document) + b"\n"
    if child_pins is not None:
        _write_bytes_at_dirfd(child_pins["fences"].fd, name, payload)
    else:
        path = _fence_path(private_root, target_digest)
        try:
            write_private_file_atomic(path, payload, private_root=private_root)
        except OSError as exc:
            _fail("unsafe_path", str(exc))
    _verify_transaction_identity(identity, child_pins)
    return next_fence


def _require_event_id_syntax(value: object, *, label: str = "event_id") -> str:
    text_value = _require_text(value, label=label)
    body = text_value[4:] if text_value.startswith("wel_") else ""
    if len(body) != 64 or any(char not in "0123456789abcdef" for char in body):
        _fail("index_corrupt", f"{label} must match wel_<64 lowercase hex>")
    return text_value


def _validate_index_document(
    document: dict[str, Any],
    *,
    idempotency_key: str,
    allow_pending: bool = False,
) -> dict[str, Any]:
    if not isinstance(document, dict):
        _fail("index_corrupt", "idempotency index must be an object")
    keys = set(document)
    if keys != _INDEX_EXACT_KEYS:
        missing = sorted(_INDEX_EXACT_KEYS - keys)
        extra = sorted(keys - _INDEX_EXACT_KEYS)
        _fail(
            "index_corrupt",
            f"idempotency index keys must be exact; missing={missing} extra={extra}",
        )
    if document.get("schema_version") != _INDEX_SCHEMA_VERSION:
        _fail("index_corrupt", "idempotency index schema mismatch")
    if document.get("idempotency_key") != idempotency_key:
        _fail("index_corrupt", "idempotency index key binding mismatch")
    state = document.get("state")
    if state not in {"pending", "bound"}:
        _fail("index_corrupt", "idempotency index state invalid")
    if state == "pending" and not allow_pending:
        # pending is valid durable SSOT; callers decide how to repair
        pass
    _require_digest(document["request_digest"], label="index.request_digest")
    _require_digest(document["target_digest"], label="index.target_digest")
    _require_digest(document["command_digest"], label="index.command_digest")
    _require_digest(document["payload_digest"], label="index.payload_digest")
    _require_text(document["effect_id"], label="index.effect_id")
    _require_text(document["adapter_id"], label="index.adapter_id")
    _require_text(document["operation"], label="index.operation")
    event_id = document.get("event_id")
    fence = document.get("fence")
    if state == "bound":
        if not isinstance(event_id, str) or not event_id:
            _fail("index_corrupt", "bound idempotency index missing event_id")
        _require_event_id_syntax(event_id, label="index.event_id")
        if not isinstance(fence, int) or isinstance(fence, bool) or fence < 1:
            _fail("index_corrupt", "bound idempotency index missing positive fence")
    else:
        if event_id is not None:
            _fail("index_corrupt", "pending event_id must be exactly null")
        if fence is not None:
            _fail("index_corrupt", "pending fence must be exactly null")
    return document


def _index_document(
    *,
    idempotency_key: str,
    request_digest: str,
    effect_id: str,
    target_digest: str,
    state: str,
    event_id: str | None,
    fence: int | None,
    command_digest: str,
    adapter_id: str,
    operation: str,
    payload_digest: str,
) -> dict[str, Any]:
    return {
        "schema_version": _INDEX_SCHEMA_VERSION,
        "state": state,
        "idempotency_key": idempotency_key,
        "request_digest": request_digest,
        "effect_id": effect_id,
        "target_digest": target_digest,
        "event_id": event_id,
        "fence": fence,
        "command_digest": command_digest,
        "adapter_id": adapter_id,
        "operation": operation,
        "payload_digest": payload_digest,
    }


def _identity_conflict(existing: Mapping[str, Any], expected: Mapping[str, Any], *, label: str) -> None:
    for key in sorted(_INDEX_IDENTITY_KEYS):
        left = existing.get(key)
        right = expected.get(key)
        if left != right:
            _fail(
                "idempotency_conflict",
                f"idempotency {label} field {key} conflicts: durable={left!r} intended={right!r}",
            )


def _read_idempotency_index(
    private_root: Path,
    idempotency_key: str,
    *,
    child_pins: Mapping[str, _DirPin] | None = None,
    nested_fds: Mapping[str, _NestedDirPin] | None = None,
    identity: _RootIdentity | None = None,
) -> dict[str, Any] | None:
    name = f"{_name_for(idempotency_key)}.json"
    document = None
    nested_pin = None
    owns_nested = False
    if nested_fds is not None and "indexes_idempotency" in nested_fds:
        nested_pin = nested_fds["indexes_idempotency"]
    elif child_pins is not None:
        nested_pin = _open_nested_dir_from_pin(child_pins["indexes"], "idempotency", create=True)
        owns_nested = True
    try:
        if nested_pin is not None:
            if identity is not None:
                _verify_transaction_identity(identity, child_pins, nested_fds)
            try:
                raw = _read_bytes_at_dirfd(nested_pin.fd, name)
            except FileNotFoundError:
                if identity is not None:
                    _verify_transaction_identity(identity, child_pins, nested_fds)
                return None
            if identity is not None:
                _verify_transaction_identity(identity, child_pins, nested_fds)
            try:
                document = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                _fail("index_corrupt", str(exc))
        else:
            document = _read_json_private(
                _idempotency_index_path(private_root, idempotency_key), private_root=private_root
            )
    finally:
        if owns_nested and nested_pin is not None:
            try:
                os.close(nested_pin.fd)
            except OSError:
                pass
    if document is None:
        if identity is not None:
            _verify_transaction_identity(identity, child_pins, nested_fds)
        return None
    if not isinstance(document, dict):
        _fail("index_corrupt", "idempotency index must be an object")
    validated = _validate_index_document(document, idempotency_key=idempotency_key, allow_pending=True)
    if identity is not None:
        _verify_transaction_identity(identity, child_pins, nested_fds)
    return validated


def _write_idempotency_key_record(
    private_root: Path,
    *,
    idempotency_key: str,
    request_digest: str,
    effect_id: str,
    target_digest: str,
    state: str,
    event_id: str | None,
    fence: int | None,
    command_digest: str,
    adapter_id: str,
    operation: str,
    payload_digest: str,
    identity: _RootIdentity,
    child_pins: Mapping[str, _DirPin] | None = None,
    nested_fds: Mapping[str, _NestedDirPin] | None = None,
) -> None:
    _verify_transaction_identity(identity, child_pins, nested_fds)
    document = _index_document(
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        effect_id=effect_id,
        target_digest=target_digest,
        state=state,
        event_id=event_id,
        fence=fence,
        command_digest=command_digest,
        adapter_id=adapter_id,
        operation=operation,
        payload_digest=payload_digest,
    )
    _validate_index_document(document, idempotency_key=idempotency_key, allow_pending=True)
    payload = _compact_json(document) + b"\n"
    name = f"{_name_for(idempotency_key)}.json"
    nested_pin = None
    owns_nested = False
    if nested_fds is not None and "indexes_idempotency" in nested_fds:
        nested_pin = nested_fds["indexes_idempotency"]
    elif child_pins is not None:
        nested_pin = _open_nested_dir_from_pin(child_pins["indexes"], "idempotency", create=True)
        owns_nested = True
    try:
        if nested_pin is not None:
            _verify_transaction_identity(identity, child_pins, nested_fds)
            _write_bytes_at_dirfd(
                nested_pin.fd,
                name,
                payload,
                verify_identity=lambda: _verify_transaction_identity(
                    identity, child_pins, nested_fds
                ),
            )
            _verify_transaction_identity(identity, child_pins, nested_fds)
        else:
            path = _idempotency_index_path(private_root, idempotency_key)
            try:
                write_private_file_atomic(path, payload, private_root=private_root)
            except OSError as exc:
                _fail("unsafe_path", str(exc))
    finally:
        if owns_nested and nested_pin is not None:
            try:
                os.close(nested_pin.fd)
            except OSError:
                pass
    _verify_transaction_identity(identity, child_pins, nested_fds)


def _write_idempotency_index(
    private_root: Path,
    *,
    idempotency_key: str,
    request_digest: str,
    effect_id: str,
    event_id: str,
    identity: _RootIdentity,
    target_digest: str | None = None,
    fence: int | None = None,
    command_digest: str | None = None,
    adapter_id: str | None = None,
    operation: str | None = None,
    payload_digest: str | None = None,
    child_pins: Mapping[str, _DirPin] | None = None,
    nested_fds: Mapping[str, _NestedDirPin] | None = None,
) -> None:
    existing = _read_idempotency_index(
        private_root,
        idempotency_key,
        child_pins=child_pins,
        nested_fds=nested_fds,
        identity=identity,
    )
    if target_digest is None:
        if existing is not None:
            target_digest = str(existing.get("target_digest") or "")
        if not target_digest:
            _fail("index_corrupt", "target_digest required to bind idempotency index")
    if fence is None and existing is not None:
        fence = existing.get("fence")  # type: ignore[assignment]
    if command_digest is None and existing is not None:
        command_digest = existing.get("command_digest")  # type: ignore[assignment]
    if adapter_id is None and existing is not None:
        adapter_id = existing.get("adapter_id")  # type: ignore[assignment]
    if operation is None and existing is not None:
        operation = existing.get("operation")  # type: ignore[assignment]
    if payload_digest is None and existing is not None:
        payload_digest = existing.get("payload_digest")  # type: ignore[assignment]
    if (
        fence is None
        or command_digest is None
        or adapter_id is None
        or operation is None
        or payload_digest is None
    ):
        _fail("index_corrupt", "full first-claim identity required to bind idempotency index")
    _write_idempotency_key_record(
        private_root,
        idempotency_key=idempotency_key,
        request_digest=request_digest,
        effect_id=effect_id,
        target_digest=str(target_digest),
        state="bound",
        event_id=event_id,
        fence=int(fence),
        command_digest=str(command_digest),
        adapter_id=str(adapter_id),
        operation=str(operation),
        payload_digest=str(payload_digest),
        identity=identity,
        child_pins=child_pins,
        nested_fds=nested_fds,
    )


def _append_event_body(
    *,
    private_root: Path,
    effect_id: str,
    event_type: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
    occurred_at: str,
    identity: _RootIdentity,
    request_digest: str | None,
    child_pins: Mapping[str, _DirPin] | None,
    nested_fds: Mapping[str, _NestedDirPin] | None,
    target_digest_for_index: str | None,
    descriptor: int,
    anchor: Any,
    created: bool,
) -> dict[str, Any]:
    _verify_transaction_identity(identity, child_pins, nested_fds)
    os.fchmod(descriptor, PRIVATE_FILE_MODE)
    events = _decode_ledger(read_ledger_descriptor_bytes(descriptor))
    _verify_transaction_identity(identity, child_pins, nested_fds)
    for event in events:
        if event["idempotency_key"] != idempotency_key:
            continue
        if (
            event["effect_id"] == effect_id
            and event["event_type"] == event_type
            and event["payload"] == dict(payload)
        ):
            try:
                _fsync_descriptor(descriptor)
                _fsync_descriptor(anchor.directory_descriptor)
            except OSError as exc:
                _fail(
                    "durability_unknown",
                    f"duplicate ledger event fsync failed: {exc}",
                )
            projection = _project(events)
            if request_digest is not None:
                _write_idempotency_index(
                    private_root,
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                    effect_id=effect_id,
                    event_id=event["event_id"],
                    identity=identity,
                    target_digest=target_digest_for_index
                    or projection.get("facts", {}).get("target_digest")
                    or payload.get("target_digest"),
                    fence=payload.get("fence") or projection.get("fence"),
                    command_digest=payload.get("command_digest")
                    or projection.get("facts", {}).get("command_digest"),
                    adapter_id=payload.get("adapter_id")
                    or projection.get("facts", {}).get("adapter_id"),
                    operation=payload.get("operation")
                    or projection.get("facts", {}).get("operation"),
                    payload_digest=payload.get("payload_digest")
                    or projection.get("facts", {}).get("payload_digest"),
                    child_pins=child_pins,
                    nested_fds=nested_fds,
                )
            return _freeze_public_result(
                {
                    **_publicize_projection(projection),
                    "event_id": event["event_id"],
                    "replayed": True,
                    "fence": projection.get("fence") or payload.get("fence"),
                }
            )
        _fail("idempotency_conflict", "idempotency key maps to another event")
    if request_digest is not None:
        existing_index = _read_idempotency_index(
            private_root,
            idempotency_key,
            child_pins=child_pins,
            nested_fds=nested_fds,
            identity=identity,
        )
        if existing_index is not None:
            if existing_index.get("request_digest") != request_digest:
                _fail(
                    "idempotency_conflict",
                    "idempotency key is bound to a different request digest",
                )
    # transition validation against current projection
    projection = _project(events)
    status = projection["status"] if events else "absent"
    body: dict[str, Any] = {
        "schema_version": _EVENT_SCHEMA_VERSION,
        "effect_id": effect_id,
        "sequence": len(events) + 1,
        "previous_event_id": events[-1]["event_id"] if events else None,
        "idempotency_key": idempotency_key,
        "occurred_at": occurred_at,
        "event_type": event_type,
        "payload": dict(payload),
    }
    event = {**body, "event_id": derive_workflow_effect_ledger_event_id_v1(body)}
    event = _normalized_event(event)
    facts = {
        **projection.get("facts", {}),
        "terminal_receipt_digest": projection.get("terminal_receipt_digest"),
        "terminal_receipt": projection.get("terminal_receipt"),
        "observation": projection.get("observation") or projection.get("facts", {}).get("observation"),
    }
    _validate_transition(status, event, facts)
    next_events = [*events, event]
    _replay(next_events)
    projection = _project(next_events)
    record = _compact_json(event) + b"\n"
    record_offset = os.lseek(descriptor, 0, os.SEEK_END)
    try:
        _write_all(descriptor, record)
    except _LedgerWriteFailure as exc:
        try:
            os.ftruncate(descriptor, record_offset)
            _fsync_descriptor(descriptor)
            if created:
                os.unlink(anchor.name, dir_fd=anchor.directory_descriptor)
                _fsync_descriptor(anchor.directory_descriptor)
        except OSError as rollback_exc:
            _fail(
                "durability_unknown",
                "ledger append failed and rollback durability is unknown: "
                f"{rollback_exc}",
            )
        _fail(
            "write_failed",
            f"ledger append failed after {exc.bytes_written} bytes: {exc.cause}",
        )
    try:
        _fsync_descriptor(descriptor)
        if created:
            _fsync_descriptor(anchor.directory_descriptor)
    except OSError as exc:
        _fail("durability_unknown", f"ledger fsync failed: {exc}")
    try:
        if request_digest is not None:
            _write_idempotency_index(
                private_root,
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                effect_id=effect_id,
                event_id=event["event_id"],
                identity=identity,
                target_digest=target_digest_for_index
                or projection.get("facts", {}).get("target_digest")
                or payload.get("target_digest"),
                fence=payload.get("fence") or projection.get("fence"),
                command_digest=payload.get("command_digest")
                or projection.get("facts", {}).get("command_digest"),
                adapter_id=payload.get("adapter_id")
                or projection.get("facts", {}).get("adapter_id"),
                operation=payload.get("operation")
                or projection.get("facts", {}).get("operation"),
                payload_digest=payload.get("payload_digest")
                or projection.get("facts", {}).get("payload_digest"),
                child_pins=child_pins,
                nested_fds=nested_fds,
            )
        _verify_transaction_identity(identity, child_pins, nested_fds)
    except WorkflowEffectLedgerError as exc:
        raise WorkflowEffectLedgerError(
            exc.code,
            str(exc),
            phase="post_event_fsync",
        ) from exc
    except OSError as exc:
        raise WorkflowEffectLedgerError(
            "durability_unknown",
            f"post-fsync publication/identity failure: {exc}",
            phase="post_event_fsync",
        ) from exc
    return _freeze_public_result(
        {
            **_publicize_projection(projection),
            "event_id": event["event_id"],
            "replayed": False,
            "fence": projection.get("fence") or payload.get("fence"),
        }
    )



def _append_event(
    *,
    private_root: Path,
    effect_id: str,
    event_type: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
    occurred_at: str,
    identity: _RootIdentity,
    request_digest: str | None = None,
    child_pins: Mapping[str, _DirPin] | None = None,
    nested_fds: Mapping[str, _NestedDirPin] | None = None,
    target_digest_for_index: str | None = None,
) -> dict[str, Any]:
    if event_type not in _EVENT_TYPES:
        _fail("payload_invalid", f"unsupported event_type: {event_type}")
    _verify_transaction_identity(identity, child_pins, nested_fds)
    path = _effect_ledger_path(private_root, effect_id)
    leaf_name = f"{_name_for(effect_id)}.jsonl"
    lock_name = f".{leaf_name}.lock"

    def _run_with_descriptor(directory_descriptor: int, anchor_obj):
        nonlocal_created = False
        try:
            os.stat(leaf_name, dir_fd=directory_descriptor, follow_symlinks=False)
            created = False
        except FileNotFoundError:
            created = True
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        nofollow = int(getattr(os, "O_NOFOLLOW", 0))
        descriptor = os.open(leaf_name, flags | nofollow, PRIVATE_FILE_MODE, dir_fd=directory_descriptor)
        return created, descriptor, anchor_obj

    if child_pins is not None:
        effects_fd = child_pins["effects"].fd
        # Serialize via lock file in the same anchored effects directory.
        lock_flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            lock_flags |= os.O_CLOEXEC
        try:
            lock_fd = runtime_state._open_regular_file_at(
                effects_fd, lock_name, lock_flags, PRIVATE_FILE_MODE
            )
        except OSError as exc:
            _fail("unsafe_path", f"effect lock open failed: {exc}")
        lock_info = os.fstat(lock_fd)
        if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1:
            os.close(lock_fd)
            _fail("unsafe_path", "effect lock must be a single-link regular file")
        locked = False
        try:
            os.fchmod(lock_fd, PRIVATE_FILE_MODE)
            runtime_state._acquire_posix_lock(lock_fd)
            locked = True
            _verify_transaction_identity(identity, child_pins)
            try:
                os.stat(leaf_name, dir_fd=effects_fd, follow_symlinks=False)
                created = False
            except FileNotFoundError:
                created = True
            flags = os.O_RDWR | os.O_CREAT
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            try:
                descriptor = runtime_state._open_regular_file_at(
                    effects_fd, leaf_name, flags, PRIVATE_FILE_MODE
                )
            except OSError as exc:
                _fail("unsafe_path", f"effect leaf open failed: {exc}")
            leaf_info = os.fstat(descriptor)
            if not stat.S_ISREG(leaf_info.st_mode) or leaf_info.st_nlink != 1:
                os.close(descriptor)
                _fail("unsafe_path", "effect leaf must be a single-link regular file")
            try:
                os.fchmod(descriptor, PRIVATE_FILE_MODE)
                # Build a lightweight anchor-like object for fsync of directory.
                class _A:
                    directory_descriptor = effects_fd
                    name = leaf_name
                anchor = _A()
                return _append_event_body(
                    private_root=private_root,
                    effect_id=effect_id,
                    event_type=event_type,
                    payload=payload,
                    idempotency_key=idempotency_key,
                    occurred_at=occurred_at,
                    identity=identity,
                    request_digest=request_digest,
                    child_pins=child_pins,
                    nested_fds=nested_fds,
                    target_digest_for_index=target_digest_for_index,
                    descriptor=descriptor,
                    anchor=anchor,
                    created=created,
                )
            finally:
                os.close(descriptor)
        finally:
            try:
                if locked:
                    runtime_state._release_posix_lock(lock_fd)
            finally:
                os.close(lock_fd)

    with exclusive_ledger_lock(path, private_root=private_root) as anchor:
        if anchor is None:
            _fail("lock_anchor_missing", "ledger lock did not provide an anchor")
        try:
            os.stat(anchor.name, dir_fd=anchor.directory_descriptor, follow_symlinks=False)
            created = False
        except FileNotFoundError:
            created = True
        descriptor = open_ledger_file(
            path,
            os.O_RDWR | os.O_CREAT,
            private_root=private_root,
            anchor=anchor,
            mode=PRIVATE_FILE_MODE,
        )
        try:
            os.fchmod(descriptor, PRIVATE_FILE_MODE)
            return _append_event_body(
                private_root=private_root,
                effect_id=effect_id,
                event_type=event_type,
                payload=payload,
                idempotency_key=idempotency_key,
                occurred_at=occurred_at,
                identity=identity,
                request_digest=request_digest,
                child_pins=child_pins,
                nested_fds=nested_fds,
                target_digest_for_index=target_digest_for_index,
                descriptor=descriptor,
                anchor=anchor,
                created=created,
            )
        finally:
            os.close(descriptor)


@contextmanager
def hold_target_ownership_v1(
    *, private_root: Path | str, target_digest: str
) -> Iterator[TargetOwnershipV1]:
    """Hold cross-process target ownership through claim/intent/dispatch/observation."""
    root, identity = _validated_root(private_root)
    target_digest = _require_digest(target_digest, label="target_digest")
    child_pins = _pin_child_directories(root, identity)
    nested_fds: dict[str, _NestedDirPin] | None = None
    try:
        with _root_lock(root):
            nested_fds = _pin_nested_transaction_dirs(child_pins)
            lock_name = _target_lock_name(target_digest)
            with _exclusive_lock_at_dirfd(
                child_pins["locks"].fd,
                lock_name,
                verify_identity=lambda: _verify_transaction_identity(
                    identity, child_pins, nested_fds
                ),
            ) as lock_fd:
                _verify_transaction_identity(identity, child_pins, nested_fds)
                _verify_or_init_layout_seal(root, child_pins, identity, create=True)
                ownership = TargetOwnershipV1(
                    private_root=root,
                    target_digest=target_digest,
                    root_identity=identity,
                    child_pins=child_pins,
                    _lock_fd=lock_fd,
                    _nested_pins=nested_fds,
                )
                try:
                    yield ownership
                except BaseException:
                    # Verify before propagating the original boundary error, but
                    # do not replace an already precise root_replaced result with
                    # a second context-exit copy of the same ownership failure.
                    try:
                        _verify_transaction_identity(
                            ownership.root_identity,
                            ownership.child_pins,
                            ownership._nested_pins,
                        )
                    except WorkflowEffectLedgerError:
                        pass
                    raise
                else:
                    # A normal return does not cross the ownership boundary until
                    # every pinned root/top-level/nested identity is checked under lock.
                    _verify_transaction_identity(
                        ownership.root_identity,
                        ownership.child_pins,
                        ownership._nested_pins,
                    )
    finally:
        _close_nested_fds(nested_fds)
        _close_dir_pins(child_pins)
        _close_root_identity(identity)


def claim_effect_v1(
    *,
    private_root: Path | str,
    effect_id: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
    occurred_at: str,
    ownership: TargetOwnershipV1 | None = None,
    crash_phase: str | None = None,
) -> dict[str, Any]:
    if crash_phase not in {None, "post_pending_fsync", "post_claim_fsync_pre_bound"}:
        _fail("crash_phase_invalid", f"unsupported claim crash phase: {crash_phase}")
    owns_root = False
    owns_pins = False
    owns_nested = False
    nested_fds: dict[str, _NestedDirPin] | None = None
    if ownership is not None:
        root = ownership.private_root
        identity = ownership.root_identity
        child_pins = ownership.child_pins
        nested_fds = dict(ownership._nested_pins)
        _verify_transaction_identity(identity, child_pins, nested_fds)
    else:
        root, identity = _validated_root(private_root)
        owns_root = True
        child_pins = _pin_child_directories(root, identity)
        owns_pins = True
    effect_id = _require_text(effect_id, label="effect_id")
    idempotency_key = _require_text(idempotency_key, label="idempotency_key")
    occurred_at = _require_text(occurred_at, label="occurred_at")
    body = dict(payload)
    target_digest = _require_digest(body.get("target_digest"), label="target_digest")
    request_digest = _require_digest(body.get("request_digest"), label="request_digest")
    command_digest = _require_digest(body.get("command_digest"), label="command_digest")
    owner = _require_text(body.get("owner"), label="owner")
    adapter_id = _require_text(body.get("adapter_id"), label="adapter_id")
    operation = _require_text(body.get("operation"), label="operation")
    payload_digest = _require_digest(body.get("payload_digest"), label="payload_digest")
    claim_idempotency = _require_text(
        body.get("idempotency_key", idempotency_key), label="payload.idempotency_key"
    )
    if ownership is not None and ownership.target_digest != target_digest:
        _fail("ownership_mismatch", "ownership target_digest does not match claim")

    intended_identity = {
        "idempotency_key": claim_idempotency,
        "request_digest": request_digest,
        "effect_id": effect_id,
        "target_digest": target_digest,
        "command_digest": command_digest,
        "adapter_id": adapter_id,
        "operation": operation,
        "payload_digest": payload_digest,
    }

    def _finalize_bound_from_claim(
        *,
        claim_event: Mapping[str, Any],
        existing_effect: str,
        nested: Mapping[str, _NestedDirPin] | None,
    ) -> dict[str, Any]:
        claim_payload = claim_event["payload"]
        expected = {
            **intended_identity,
            "effect_id": existing_effect,
            "event_id": claim_event["event_id"],
            "fence": claim_payload.get("fence"),
            "request_digest": claim_payload.get("request_digest"),
            "target_digest": claim_payload.get("target_digest"),
            "command_digest": claim_payload.get("command_digest"),
            "adapter_id": claim_payload.get("adapter_id"),
            "operation": claim_payload.get("operation"),
            "payload_digest": claim_payload.get("payload_digest"),
            "idempotency_key": claim_payload.get("idempotency_key"),
        }
        # Intended command must match first durable claim identity exactly.
        for key in (
            "request_digest",
            "target_digest",
            "command_digest",
            "adapter_id",
            "operation",
            "payload_digest",
            "idempotency_key",
        ):
            if intended_identity[key] != expected[key]:
                _fail(
                    "idempotency_conflict",
                    f"intended {key} conflicts with first durable claim",
                )
        if claim_event.get("event_type") != "claimed":
            _fail("index_corrupt", "first wel_ event is not claimed")
        if claim_event["effect_id"] != existing_effect:
            _fail("index_corrupt", "indexed effect_id mismatch")
        if not isinstance(expected["fence"], int) or isinstance(expected["fence"], bool) or expected["fence"] < 1:
            _fail("index_corrupt", "first claim missing positive fence")
        _write_idempotency_index(
            root,
            idempotency_key=claim_idempotency,
            request_digest=str(expected["request_digest"]),
            effect_id=existing_effect,
            event_id=str(claim_event["event_id"]),
            identity=identity,
            target_digest=str(expected["target_digest"]),
            fence=int(expected["fence"]),
            command_digest=str(expected["command_digest"]),
            adapter_id=str(expected["adapter_id"]),
            operation=str(expected["operation"]),
            payload_digest=str(expected["payload_digest"]),
            child_pins=child_pins,
            nested_fds=nested,
        )
        rebound = _read_idempotency_index(
            root,
            claim_idempotency,
            child_pins=child_pins,
            nested_fds=nested,
            identity=identity,
        )
        if rebound is None or rebound.get("state") != "bound":
            _fail("index_corrupt", "failed to finalize bound idempotency record")
        expected_bound = {
            **expected,
            "schema_version": _INDEX_SCHEMA_VERSION,
            "state": "bound",
        }
        # Drop non-index keys for exact compare.
        expected_doc = _index_document(
            idempotency_key=str(expected["idempotency_key"]),
            request_digest=str(expected["request_digest"]),
            effect_id=str(existing_effect),
            target_digest=str(expected["target_digest"]),
            state="bound",
            event_id=str(claim_event["event_id"]),
            fence=int(expected["fence"]),
            command_digest=str(expected["command_digest"]),
            adapter_id=str(expected["adapter_id"]),
            operation=str(expected["operation"]),
            payload_digest=str(expected["payload_digest"]),
        )
        if dict(rebound) != expected_doc:
            _fail("index_corrupt", "bound record does not match first durable claim identity")
        return expected_doc

    def _claim_under_locks(nested: Mapping[str, _NestedDirPin] | None) -> dict[str, Any]:
        _verify_transaction_identity(identity, child_pins, nested)
        _verify_or_init_layout_seal(root, child_pins, identity, create=True)
        _verify_transaction_identity(identity, child_pins, nested)
        existing_index = _read_idempotency_index(
            root,
            claim_idempotency,
            child_pins=child_pins,
            nested_fds=nested,
            identity=identity,
        )
        if existing_index is not None:
            # Any durable identity field conflict rejects.
            for key in (
                "request_digest",
                "effect_id",
                "target_digest",
                "command_digest",
                "adapter_id",
                "operation",
                "payload_digest",
                "idempotency_key",
            ):
                durable = existing_index.get(key)
                intended = intended_identity.get(key)
                if durable not in {None, intended} and durable != intended:
                    _fail(
                        "idempotency_conflict",
                        f"idempotency key is bound to a different {key}",
                    )
            existing_effect = str(existing_index.get("effect_id") or effect_id)
            events = _read_effect_events_from_pins(
                child_pins=child_pins, effect_id=existing_effect
            )
            if events:
                claim_event = events[0]
                if existing_index.get("event_id"):
                    _require_event_id_syntax(existing_index["event_id"])
                    if claim_event["event_id"] != existing_index["event_id"]:
                        _fail(
                            "index_corrupt",
                            "idempotency index event_id does not match claim event",
                        )
                if existing_index.get("fence") is not None:
                    if claim_event["payload"].get("fence") != existing_index.get("fence"):
                        _fail("index_corrupt", "idempotency index fence does not match claim")
                _finalize_bound_from_claim(
                    claim_event=claim_event,
                    existing_effect=existing_effect,
                    nested=nested,
                )
                projection = _publicize_projection(_project(events))
                return _freeze_public_result(
                    {
                        **projection,
                        "event_id": claim_event["event_id"],
                        "replayed": True,
                    }
                )
            # Index without durable claim events: only valid for pure pending before claim.
            if existing_index.get("state") == "pending" and existing_index.get("event_id") is None:
                for key in (
                    "request_digest",
                    "effect_id",
                    "target_digest",
                    "command_digest",
                    "adapter_id",
                    "operation",
                    "payload_digest",
                ):
                    if existing_index.get(key) != intended_identity[key]:
                        _fail(
                            "idempotency_conflict",
                            f"pending key owned by different {key}",
                        )
                # Continue into claim creation path for same owner/request.
            else:
                projection = _project_effect_from_pins(
                    child_pins=child_pins, effect_id=existing_effect
                )
                if projection["status"] != "absent":
                    _fail(
                        "index_corrupt",
                        "idempotency index missing claim event linkage for non-absent effect",
                    )

        existing_events = _read_effect_events_from_pins(
            child_pins=child_pins, effect_id=effect_id
        )
        if existing_events:
            projection = _publicize_projection(_project(existing_events))
            claim_event = existing_events[0]
            claim_payload = claim_event["payload"]
            stored_request = claim_payload.get("request_digest")
            stored_key = claim_payload.get("idempotency_key", claim_idempotency)
            if stored_request is not None and stored_key == claim_idempotency:
                if stored_request != request_digest:
                    _fail(
                        "idempotency_conflict",
                        "existing claim request_digest conflicts",
                    )
                _finalize_bound_from_claim(
                    claim_event=claim_event,
                    existing_effect=effect_id,
                    nested=nested,
                )
            return _freeze_public_result(
                {
                    **projection,
                    "event_id": existing_events[0]["event_id"],
                    "replayed": True,
                }
            )

        # Transactional SSOT: publish pending key ownership before claim/fence.
        _write_idempotency_key_record(
            root,
            idempotency_key=claim_idempotency,
            request_digest=request_digest,
            effect_id=effect_id,
            target_digest=target_digest,
            state="pending",
            event_id=None,
            fence=None,
            command_digest=command_digest,
            adapter_id=adapter_id,
            operation=operation,
            payload_digest=payload_digest,
            identity=identity,
            child_pins=child_pins,
            nested_fds=nested,
        )
        if crash_phase == "post_pending_fsync":
            os._exit(CRASH_EXIT_POST_PENDING_FSYNC)
        _verify_transaction_identity(identity, child_pins)
        fence = _allocate_fence(root, target_digest, identity=identity, child_pins=child_pins)
        _verify_transaction_identity(identity, child_pins)
        claim_payload = {
            "command_digest": command_digest,
            "target_digest": target_digest,
            "owner": owner,
            "request_digest": request_digest,
            "idempotency_key": claim_idempotency,
            "adapter_id": adapter_id,
            "operation": operation,
            "fence": fence,
            "payload_digest": payload_digest,
            "effect_id": effect_id,
        }
        result = _append_event(
            private_root=root,
            effect_id=effect_id,
            event_type="claimed",
            payload=claim_payload,
            idempotency_key=idempotency_key,
            occurred_at=occurred_at,
            identity=identity,
            request_digest=None,
            child_pins=child_pins,
            nested_fds=nested,
            target_digest_for_index=target_digest,
        )
        if crash_phase == "post_claim_fsync_pre_bound":
            os._exit(CRASH_EXIT_POST_CLAIM_FSYNC_PRE_BOUND)
        _write_idempotency_index(
            root,
            idempotency_key=claim_idempotency,
            request_digest=request_digest,
            effect_id=effect_id,
            event_id=str(result["event_id"]),
            identity=identity,
            target_digest=target_digest,
            fence=fence,
            command_digest=command_digest,
            adapter_id=adapter_id,
            operation=operation,
            payload_digest=payload_digest,
            child_pins=child_pins,
            nested_fds=nested,
        )
        return result

    try:
        with _root_lock(root):
            if nested_fds is None:
                nested_fds = _pin_nested_transaction_dirs(child_pins)
                owns_nested = True
            # Verify both nested idempotency identities before creating/opening
            # any idempotency lock or index leaf in either held or replacement dirs.
            _verify_transaction_identity(identity, child_pins, nested_fds)
            idemp_lock_name = _idempotency_lock_name(claim_idempotency)
            if ownership is not None:
                # Target lock is already held by ownership: target -> idempotency.
                _verify_transaction_identity(identity, child_pins, nested_fds)
                with _exclusive_lock_at_dirfd(
                    nested_fds["locks_idempotency"].fd,
                    idemp_lock_name,
                    verify_identity=lambda: _verify_transaction_identity(
                        identity, child_pins, nested_fds
                    ),
                ):
                    _verify_transaction_identity(identity, child_pins, nested_fds)
                    result = _claim_under_locks(nested_fds)
                    _verify_transaction_identity(identity, child_pins, nested_fds)
                    return result
            # Direct claim uses the same global order: target -> idempotency.
            target_lock_name = _target_lock_name(target_digest)
            with _exclusive_lock_at_dirfd(
                child_pins["locks"].fd,
                target_lock_name,
                verify_identity=lambda: _verify_transaction_identity(
                    identity, child_pins, nested_fds
                ),
            ):
                with _exclusive_lock_at_dirfd(
                    nested_fds["locks_idempotency"].fd,
                    idemp_lock_name,
                    verify_identity=lambda: _verify_transaction_identity(
                        identity, child_pins, nested_fds
                    ),
                ):
                    result = _claim_under_locks(nested_fds)
                    _verify_transaction_identity(identity, child_pins, nested_fds)
                    return result
    finally:
        if owns_nested:
            _close_nested_fds(nested_fds)
        if owns_pins:
            _close_dir_pins(child_pins)
        if owns_root:
            _close_root_identity(identity)



def append_effect_event_v1(
    *,
    private_root: Path | str,
    effect_id: str,
    event_type: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
    occurred_at: str,
    ownership: TargetOwnershipV1 | None = None,
) -> dict[str, Any]:
    owns_pins = False
    owns_root = False
    owns_nested = False
    nested_fds: dict[str, _NestedDirPin] | None = None
    if ownership is not None:
        root = ownership.private_root
        identity = ownership.root_identity
        child_pins = ownership.child_pins
        nested_fds = dict(ownership._nested_pins)
        _verify_transaction_identity(identity, child_pins, nested_fds)
    else:
        root, identity = _validated_root(private_root)
        owns_root = True
        child_pins = _pin_child_directories(root, identity)
        owns_pins = True
        nested_fds = _pin_nested_transaction_dirs(child_pins)
        owns_nested = True
        _verify_transaction_identity(identity, child_pins, nested_fds)
    effect_id = _require_text(effect_id, label="effect_id")
    event_type = _require_text(event_type, label="event_type")
    if event_type == "claimed":
        _fail("payload_invalid", "use claim_effect_v1 for claimed events")
    if event_type not in _EVENT_TYPES:
        _fail("payload_invalid", f"unsupported event_type: {event_type}")
    # Normalize admitted numbers (fence 1.0 -> 1) before any fence/type checks.
    try:
        body = normalize_admitted_json_v1(dict(payload))
    except EffectContractError as exc:
        _fail(_map_effect_contract_error(exc), str(exc))
    if not isinstance(body, dict):
        _fail("payload_invalid", "payload must be an object")
    try:
        with _root_lock(root):
            _verify_transaction_identity(identity, child_pins, nested_fds)
            events = _read_effect_events_from_pins(
                child_pins=child_pins, effect_id=effect_id
            )
            # Public events are frozen; thaw for internal projection/comparison.
            internal_events = [thaw_admitted_json_v1(event) for event in events]
            if not internal_events:
                _fail("effect_missing", "effect must be claimed before further events")
            projection = _project(internal_events)
            current_fence = projection.get("fence")
            if "fence" in body:
                fence = body["fence"]
                if not isinstance(fence, int) or isinstance(fence, bool) or fence < 1:
                    _fail("payload_invalid", "fence must be a positive integer")
                # Compensation events validate fence inside transition as payload_invalid.
                if (
                    current_fence is not None
                    and fence != current_fence
                    and not str(event_type).startswith("compensation_")
                ):
                    _fail("stale_fence", "event fence does not match claimed fence")
            if ownership is not None:
                claimed_target = projection.get("facts", {}).get("target_digest")
                if claimed_target and claimed_target != ownership.target_digest:
                    _fail("ownership_mismatch", "ownership does not match claimed target")
            result = _append_event(
                private_root=root,
                effect_id=effect_id,
                event_type=event_type,
                payload=body,
                idempotency_key=_require_text(idempotency_key, label="idempotency_key"),
                occurred_at=_require_text(occurred_at, label="occurred_at"),
                identity=identity,
                request_digest=(
                    _require_digest(body.get("request_digest"), label="request_digest")
                    if "request_digest" in body
                    else None
                ),
                child_pins=child_pins,
                nested_fds=nested_fds,
                target_digest_for_index=projection.get("facts", {}).get("target_digest"),
            )
            _verify_transaction_identity(identity, child_pins, nested_fds)
            return result
    finally:
        if owns_nested:
            _close_nested_fds(nested_fds)
            _close_dir_pins(child_pins)
        if owns_root:
            _close_root_identity(identity)


def allowed_effect_transitions_v1() -> dict[str, tuple[str, ...]]:
    """Public read-only view of the complete allowed transition table."""
    return {status: tuple(sorted(events)) for status, events in _ALLOWED_TRANSITIONS.items()}
