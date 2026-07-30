"""Digest-bound runtime activation for the Personal AI OS Temporal Commitment Ledger.

The ledger implementation remains owned by the Agent OS project artifact. This
module deploys those exact reviewed bytes into an immutable content-addressed
release, binds them to profile-local state with an explicit user AuthorityGrant,
and exposes fail-closed status/inspection helpers for ``hermes agent-os``.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

AUTHORITY_SCHEMA = "TemporalCommitmentRuntimeAuthority.v1"
MANIFEST_SCHEMA = "TemporalCommitmentRuntimeManifest.v1"
RECEIPT_SCHEMA = "TemporalCommitmentRuntimeReceipt.v1"
_SHA256_LENGTH = 64

_SAFETY = {
    "runtime_mutation": "profile_local_temporal_ledger_runtime_only",
    "external_side_effects_executed": False,
    "calendar_write": False,
    "reminder_write": False,
    "long_term_memory_write": False,
    "scheduler_armed": False,
    "authority_effect": "none",
    "global_certification": False,
}
_AUTHORITY_FIELDS = {
    "schema",
    "authorization_id",
    "granted_by",
    "source_ref",
    "action",
    "module_source",
    "module_sha256",
    "runtime_manifest",
    "ledger_path",
    "evidence_refs",
    "rollback",
    "issued_at",
    "expires_at",
}
_MANIFEST_FIELDS = {
    "schema",
    "active",
    "module_sha256",
    "module_path",
    "ledger_path",
    "source_ref",
    "authorization_id",
    "authority_digest",
    "activated_at",
    "deactivated_at",
    "previous_manifest_digest",
    "rollback",
    "safety",
}


class RuntimeActivationError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _now().isoformat(timespec="seconds")


def _home(value: str | Path | None) -> Path:
    raw = Path(value) if value is not None else Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    path = raw.expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise RuntimeActivationError("hermes_home_unavailable", str(path))
    return path


def _paths(home: Path) -> dict[str, Path]:
    state = home / "state" / "personal-ai-os"
    runtime = home / "runtime" / "personal-ai-os"
    return {
        "state": state,
        "runtime": runtime,
        "manifest": state / "temporal-commitment-runtime.json",
        "ledger": state / "temporal-commitments.v1.jsonl",
        "receipts": state / "temporal-commitment-runtime-receipts.jsonl",
        "lock": state / "temporal-commitment-runtime.lock",
        "releases": runtime / "releases",
    }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _safe_regular(path: Path, *, code: str, writable: bool | None = None) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RuntimeActivationError(code, f"{path}: {exc}") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RuntimeActivationError(code, f"unsafe file identity: {path}")
    if writable is False and info.st_mode & 0o222:
        raise RuntimeActivationError(code, f"release is writable: {path}")
    return info


def _reject_symlink_components(path: Path, *, code: str) -> None:
    if not path.is_absolute():
        raise RuntimeActivationError(code, f"path is not absolute: {path}")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        try:
            info = current.lstat()
        except OSError as exc:
            raise RuntimeActivationError(code, f"{current}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise RuntimeActivationError(code, f"symlink component: {current}")


def _read_regular_bytes(path: Path, *, code: str, writable: bool | None = None) -> bytes:
    _reject_symlink_components(path, code=code)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise RuntimeActivationError(code, f"{path}: {exc}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeActivationError(code, f"unsafe file identity: {path}")
        if writable is False and info.st_mode & 0o222:
            raise RuntimeActivationError(code, f"release is writable: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _ensure_private_dir(path: Path, home: Path) -> None:
    try:
        path.relative_to(home)
    except ValueError as exc:
        raise RuntimeActivationError("unsafe_runtime_path", str(path)) from exc
    current = home
    for part in path.relative_to(home).parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            info = current.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise RuntimeActivationError("unsafe_runtime_path", str(current))


def _fsync_dir(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    total = 0
    while total < len(data):
        written = os.write(fd, view[total:])
        if written <= 0:
            raise RuntimeActivationError("short_write")
        total += written


def _atomic_write(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    if path.exists() or path.is_symlink():
        _safe_regular(path, code="unsafe_runtime_target")
    tmp = path.parent / f".{path.name}.tmp-{os.getpid()}-{secrets.token_hex(6)}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(tmp, flags, mode)
    try:
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(tmp, path)
        os.chmod(path, mode)
        _fsync_dir(path.parent)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def _append_receipt(path: Path, payload: dict[str, Any]) -> None:
    flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeActivationError("unsafe_receipt_path")
        _write_all(fd, _canonical_bytes(payload))
        os.fsync(fd)
    finally:
        os.close(fd)


def _authorization_used(path: Path, authorization_id: str) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    raw = _read_regular_bytes(path, code="runtime_receipts_malformed")
    if raw and not raw.endswith(b"\n"):
        raise RuntimeActivationError("runtime_receipts_malformed", "torn final row")
    try:
        rows = [json.loads(line) for line in raw.splitlines()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeActivationError("runtime_receipts_malformed", str(exc)) from exc
    if any(type(row) is not dict for row in rows):
        raise RuntimeActivationError("runtime_receipts_malformed")
    return any(row.get("authorization_id") == authorization_id for row in rows)


def _acquire_runtime_lock(path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    existed = path.exists() or path.is_symlink()
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise RuntimeActivationError("unsafe_runtime_lock", str(exc)) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeActivationError("unsafe_runtime_lock")
        if not existed:
            _fsync_dir(path.parent)
        fcntl.flock(fd, fcntl.LOCK_EX)
        return fd
    except Exception:
        os.close(fd)
        raise


def _restore_manifest(home: str | Path | None, previous_raw: bytes | None) -> None:
    root = _home(home)
    manifest_path = _paths(root)["manifest"]
    if previous_raw is None:
        manifest_path.unlink(missing_ok=True)
        _fsync_dir(manifest_path.parent)
    else:
        _atomic_write(manifest_path, previous_raw)


def _read_json(path: Path, *, missing_code: str, malformed_code: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = _read_regular_bytes(path, code=malformed_code)
        payload = json.loads(raw)
    except RuntimeActivationError:
        raise
    except FileNotFoundError as exc:
        raise RuntimeActivationError(missing_code, str(path)) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeActivationError(malformed_code, f"{path}: {exc}") from exc
    if type(payload) is not dict:
        raise RuntimeActivationError(malformed_code, f"not an object: {path}")
    return payload, raw


def _parse_timestamp(value: Any, code: str) -> datetime:
    if type(value) is not str:
        raise RuntimeActivationError(code)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeActivationError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeActivationError(code)
    return parsed


def _valid_sha(value: Any) -> bool:
    return type(value) is str and len(value) == _SHA256_LENGTH and all(ch in "0123456789abcdef" for ch in value)


def _load_authority(home: Path, authority_gate: str | Path, expected_action: str) -> tuple[dict[str, Any], str, bytes]:
    gate_path = Path(authority_gate).expanduser()
    if not gate_path.is_absolute() or gate_path.is_symlink():
        raise RuntimeActivationError("unsafe_authority_gate")
    payload, raw = _read_json(gate_path, missing_code="authority_gate_missing", malformed_code="authority_gate_malformed")
    if set(payload) != _AUTHORITY_FIELDS or payload.get("schema") != AUTHORITY_SCHEMA:
        raise RuntimeActivationError("authority_schema_invalid")
    if payload.get("action") != expected_action:
        raise RuntimeActivationError("authority_action_mismatch")
    if payload.get("granted_by") != "user":
        raise RuntimeActivationError("authority_not_user_granted")
    for field in ("authorization_id", "source_ref"):
        if type(payload.get(field)) is not str or not payload[field].strip():
            raise RuntimeActivationError("authority_identity_invalid")
    if not payload["authorization_id"].startswith("auth:"):
        raise RuntimeActivationError("authority_identity_invalid")
    if not _valid_sha(payload.get("module_sha256")):
        raise RuntimeActivationError("authority_module_digest_invalid")
    if type(payload.get("evidence_refs")) is not list or not payload["evidence_refs"] or not all(
        type(item) is str and item.strip() for item in payload["evidence_refs"]
    ):
        raise RuntimeActivationError("authority_missing_evidence")
    rollback = payload.get("rollback")
    expected_rollback_action = "deactivate" if expected_action == "activate" else "activate"
    if type(rollback) is not dict or set(rollback) != {"action", "command"} or rollback.get("action") != expected_rollback_action or not isinstance(rollback.get("command"), str) or not rollback["command"].strip():
        raise RuntimeActivationError("authority_rollback_invalid")
    issued = _parse_timestamp(payload.get("issued_at"), "authority_time_invalid")
    expires = _parse_timestamp(payload.get("expires_at"), "authority_time_invalid")
    now = _now()
    if expires <= issued:
        raise RuntimeActivationError("authority_time_invalid")
    if now >= expires:
        raise RuntimeActivationError("authority_expired")
    if expires - issued > timedelta(minutes=30):
        raise RuntimeActivationError("authority_ttl_too_long")
    if issued > now + timedelta(minutes=5):
        raise RuntimeActivationError("authority_time_invalid")

    expected = _paths(home)
    if payload.get("runtime_manifest") != str(expected["manifest"]) or payload.get("ledger_path") != str(expected["ledger"]):
        raise RuntimeActivationError("authority_target_mismatch")
    source = Path(payload.get("module_source", ""))
    if not source.is_absolute() or source.is_symlink():
        raise RuntimeActivationError("unsafe_module_source")
    source_bytes = _read_regular_bytes(source, code="unsafe_module_source")
    if _sha256(source_bytes) != payload["module_sha256"]:
        raise RuntimeActivationError("module_digest_mismatch")
    return payload, _sha256(raw), source_bytes


def _manifest_valid(home: Path, payload: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    if set(payload) != _MANIFEST_FIELDS or payload.get("schema") != MANIFEST_SCHEMA:
        return ["runtime_manifest_schema_invalid"]
    expected = _paths(home)
    digest = payload.get("module_sha256")
    if not _valid_sha(digest):
        findings.append("runtime_manifest_digest_invalid")
        return findings
    expected_module = expected["releases"] / str(digest) / "temporal_commitment_ledger.py"
    if payload.get("module_path") != str(expected_module):
        findings.append("active_module_path_mismatch")
    if payload.get("ledger_path") != str(expected["ledger"]):
        findings.append("active_ledger_path_mismatch")
    if type(payload.get("active")) is not bool:
        findings.append("runtime_manifest_active_invalid")
    for field in ("authorization_id", "authority_digest", "activated_at", "source_ref"):
        if type(payload.get(field)) is not str or not payload[field]:
            findings.append(f"runtime_manifest_{field}_invalid")
    if payload.get("deactivated_at") is not None and type(payload.get("deactivated_at")) is not str:
        findings.append("runtime_manifest_deactivated_at_invalid")
    if payload.get("previous_manifest_digest") is not None and not _valid_sha(payload.get("previous_manifest_digest")):
        findings.append("runtime_manifest_previous_digest_invalid")
    if type(payload.get("rollback")) is not dict or payload.get("safety") != _SAFETY:
        findings.append("runtime_manifest_policy_invalid")
    return findings


def _load_module(path: Path, digest: str) -> ModuleType:
    source_bytes = _read_regular_bytes(path, code="unsafe_active_module", writable=False)
    if _sha256(source_bytes) != digest:
        raise RuntimeActivationError("active_module_digest_mismatch")
    name = f"_hermes_temporal_commitment_ledger_{digest}"
    module = ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    try:
        exec(compile(source_bytes, str(path), "exec"), module.__dict__)
    except Exception as exc:
        raise RuntimeActivationError("active_module_import_failed", str(exc)) from exc
    finally:
        sys.modules.pop(name, None)
    for function in ("append_event", "replay_ledger", "inspect_ledger"):
        if not callable(getattr(module, function, None)):
            raise RuntimeActivationError("active_module_contract_invalid", function)
    return module


def _create_empty_ledger(path: Path) -> None:
    if path.exists() or path.is_symlink():
        _safe_regular(path, code="unsafe_ledger_path")
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_dir(path.parent)


def _deploy_release(source_bytes: bytes, digest: str, releases: Path, home: Path) -> Path:
    release_dir = releases / digest
    _ensure_private_dir(release_dir, home)
    target = release_dir / "temporal_commitment_ledger.py"
    if target.exists() or target.is_symlink():
        if _sha256(_read_regular_bytes(target, code="unsafe_release_path", writable=False)) != digest:
            raise RuntimeActivationError("release_digest_mismatch")
        return target
    _atomic_write(target, source_bytes, mode=0o444)
    if _sha256(_read_regular_bytes(target, code="unsafe_release_path", writable=False)) != digest:
        raise RuntimeActivationError("release_digest_mismatch")
    return target


def _failure(code: str, message: str = "", *, active: bool = False) -> dict[str, Any]:
    return {"ok": False, "trusted": False, "active": active, "activated": False, "deactivated": False, "error_code": code, "message": message, **_SAFETY}


def runtime_status(home: str | Path | None = None) -> dict[str, Any]:
    try:
        root = _home(home)
        pathset = _paths(root)
        manifest_path = pathset["manifest"]
        if not manifest_path.exists() and not manifest_path.is_symlink():
            return {
                "ok": True,
                "trusted": True,
                "active": False,
                "state": "not_activated",
                "manifest_path": str(manifest_path),
                "ledger_path": str(pathset["ledger"]),
                "findings": ["runtime_not_activated"],
                **_SAFETY,
            }
        manifest, _ = _read_json(manifest_path, missing_code="runtime_manifest_missing", malformed_code="runtime_manifest_malformed")
        findings = _manifest_valid(root, manifest)
        if findings:
            return {"ok": False, "trusted": False, "active": False, "state": "invalid", "manifest_path": str(manifest_path), "findings": findings, **_SAFETY}
        if manifest["active"] is False:
            return {
                "ok": True,
                "trusted": True,
                "active": False,
                "state": "deactivated",
                "manifest_path": str(manifest_path),
                "module_sha256": manifest["module_sha256"],
                "module_path": manifest["module_path"],
                "ledger_path": manifest["ledger_path"],
                "authorization_id": manifest["authorization_id"],
                "findings": [],
                **_SAFETY,
            }
        module_path = Path(manifest["module_path"])
        ledger_path = Path(manifest["ledger_path"])
        module = _load_module(module_path, manifest["module_sha256"])
        _safe_regular(ledger_path, code="unsafe_ledger_path")
        replay = module.replay_ledger(ledger_path)
        if replay.get("runtime_mutation") is not False or replay.get("external_side_effects_executed") is not False or replay.get("authority_effect") != "none":
            raise RuntimeActivationError("active_module_safety_contract_invalid")
        return {
            "ok": True,
            "trusted": True,
            "active": True,
            "state": "active",
            "manifest_path": str(manifest_path),
            "module_sha256": manifest["module_sha256"],
            "module_path": manifest["module_path"],
            "ledger_path": manifest["ledger_path"],
            "authorization_id": manifest["authorization_id"],
            "row_count": replay.get("row_count"),
            "ledger_digest": replay.get("ledger_digest"),
            "findings": [],
            **_SAFETY,
        }
    except RuntimeActivationError as exc:
        finding = exc.code
        return {"ok": False, "trusted": False, "active": False, "state": "invalid", "findings": [finding], "error_code": exc.code, "message": str(exc), **_SAFETY}
    except Exception as exc:
        return {"ok": False, "trusted": False, "active": False, "state": "invalid", "findings": ["runtime_status_failed"], "error_code": "runtime_status_failed", "message": str(exc), **_SAFETY}


def activate_runtime(*, home: str | Path | None = None, authority_gate: str | Path) -> dict[str, Any]:
    previous_raw: bytes | None = None
    manifest_written = False
    lock_fd: int | None = None
    try:
        root = _home(home)
        pathset = _paths(root)
        authority, authority_digest, source_bytes = _load_authority(root, authority_gate, "activate")
        _ensure_private_dir(pathset["state"], root)
        lock_fd = _acquire_runtime_lock(pathset["lock"])
        current = runtime_status(root)
        if current.get("active") is True and current.get("trusted") is True:
            manifest, _ = _read_json(pathset["manifest"], missing_code="runtime_manifest_missing", malformed_code="runtime_manifest_malformed")
            if manifest.get("authority_digest") == authority_digest and manifest.get("module_sha256") == authority["module_sha256"]:
                return {"ok": True, "trusted": True, "active": True, "activated": True, "deactivated": False, "outcome": "already_active", "module_sha256": authority["module_sha256"], "readback": current, **_SAFETY}
        elif current.get("state") not in {"not_activated", "deactivated"}:
            raise RuntimeActivationError("existing_runtime_untrusted")
        if _authorization_used(pathset["receipts"], authority["authorization_id"]):
            raise RuntimeActivationError("authority_replayed")

        _ensure_private_dir(pathset["releases"], root)
        release = _deploy_release(source_bytes, authority["module_sha256"], pathset["releases"], root)
        _create_empty_ledger(pathset["ledger"])
        module = _load_module(release, authority["module_sha256"])
        replay = module.replay_ledger(pathset["ledger"])
        if replay.get("runtime_mutation") is not False or replay.get("external_side_effects_executed") is not False or replay.get("authority_effect") != "none":
            raise RuntimeActivationError("active_module_safety_contract_invalid")

        if pathset["manifest"].exists() or pathset["manifest"].is_symlink():
            previous_raw = _read_regular_bytes(pathset["manifest"], code="runtime_manifest_malformed")
        now = _iso_now()
        manifest = {
            "schema": MANIFEST_SCHEMA,
            "active": True,
            "module_sha256": authority["module_sha256"],
            "module_path": str(release),
            "ledger_path": str(pathset["ledger"]),
            "source_ref": authority["source_ref"],
            "authorization_id": authority["authorization_id"],
            "authority_digest": authority_digest,
            "activated_at": now,
            "deactivated_at": None,
            "previous_manifest_digest": _sha256(previous_raw) if previous_raw is not None else None,
            "rollback": authority["rollback"],
            "safety": _SAFETY,
        }
        _atomic_write(pathset["manifest"], _canonical_bytes(manifest))
        manifest_written = True
        readback = runtime_status(root)
        if not readback.get("ok") or not readback.get("trusted") or not readback.get("active"):
            raise RuntimeActivationError("activation_readback_failed")
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "outcome": "activated",
            "authorization_id": authority["authorization_id"],
            "authority_digest": authority_digest,
            "module_sha256": authority["module_sha256"],
            "manifest_path": str(pathset["manifest"]),
            "ledger_path": str(pathset["ledger"]),
            "occurred_at": now,
            **_SAFETY,
        }
        _append_receipt(pathset["receipts"], receipt)
        return {"ok": True, "trusted": True, "active": True, "activated": True, "deactivated": False, "outcome": "activated", "module_sha256": authority["module_sha256"], "receipt": receipt, "readback": readback, **_SAFETY}
    except RuntimeActivationError as exc:
        if manifest_written:
            try:
                _restore_manifest(home, previous_raw)
            except Exception as rollback_exc:
                return _failure("activation_rollback_failed", f"{exc}; rollback: {rollback_exc}", active=True)
        return _failure(exc.code, str(exc))
    except Exception as exc:
        if manifest_written:
            try:
                _restore_manifest(home, previous_raw)
            except Exception as rollback_exc:
                return _failure("activation_rollback_failed", f"{exc}; rollback: {rollback_exc}", active=True)
        return _failure("runtime_activation_failed", str(exc))
    finally:
        if lock_fd is not None:
            os.close(lock_fd)


def deactivate_runtime(*, home: str | Path | None = None, authority_gate: str | Path) -> dict[str, Any]:
    previous_raw: bytes | None = None
    lock_fd: int | None = None
    try:
        root = _home(home)
        pathset = _paths(root)
        authority, authority_digest, _ = _load_authority(root, authority_gate, "deactivate")
        _ensure_private_dir(pathset["state"], root)
        lock_fd = _acquire_runtime_lock(pathset["lock"])
        current = runtime_status(root)
        if not current.get("ok") or not current.get("trusted") or not current.get("active"):
            raise RuntimeActivationError("active_runtime_required")
        if current.get("module_sha256") != authority["module_sha256"]:
            raise RuntimeActivationError("authority_module_mismatch")
        if _authorization_used(pathset["receipts"], authority["authorization_id"]):
            raise RuntimeActivationError("authority_replayed")
        manifest, previous_raw = _read_json(pathset["manifest"], missing_code="runtime_manifest_missing", malformed_code="runtime_manifest_malformed")
        now = _iso_now()
        inactive = {
            **manifest,
            "active": False,
            "source_ref": authority["source_ref"],
            "authorization_id": authority["authorization_id"],
            "authority_digest": authority_digest,
            "deactivated_at": now,
            "previous_manifest_digest": _sha256(previous_raw),
            "rollback": authority["rollback"],
        }
        _atomic_write(pathset["manifest"], _canonical_bytes(inactive))
        receipt = {
            "schema": RECEIPT_SCHEMA,
            "outcome": "deactivated",
            "authorization_id": authority["authorization_id"],
            "authority_digest": authority_digest,
            "module_sha256": authority["module_sha256"],
            "manifest_path": str(pathset["manifest"]),
            "ledger_path": str(pathset["ledger"]),
            "occurred_at": now,
            **_SAFETY,
        }
        try:
            _append_receipt(pathset["receipts"], receipt)
        except Exception:
            _atomic_write(pathset["manifest"], previous_raw)
            raise
        readback = runtime_status(root)
        if not readback.get("ok") or not readback.get("trusted") or readback.get("active"):
            _atomic_write(pathset["manifest"], previous_raw)
            raise RuntimeActivationError("deactivation_readback_failed")
        return {"ok": True, "trusted": True, "active": False, "activated": False, "deactivated": True, "outcome": "deactivated", "module_sha256": authority["module_sha256"], "receipt": receipt, "readback": readback, **_SAFETY}
    except RuntimeActivationError as exc:
        return _failure(exc.code, str(exc), active=True)
    except Exception as exc:
        return _failure("runtime_deactivation_failed", str(exc), active=True)
    finally:
        if lock_fd is not None:
            os.close(lock_fd)


def inspect_runtime(*, home: str | Path | None = None, as_of: str) -> dict[str, Any]:
    try:
        root = _home(home)
        current = runtime_status(root)
        if not current.get("ok") or not current.get("trusted") or not current.get("active"):
            raise RuntimeActivationError("active_runtime_required")
        when = _parse_timestamp(as_of, "invalid_as_of")
        module = _load_module(Path(current["module_path"]), current["module_sha256"])
        inspection = module.inspect_ledger(Path(current["ledger_path"]), when)
        if inspection.get("runtime_mutation") is not False or inspection.get("external_side_effects_executed") is not False or inspection.get("authority_effect") != "none":
            raise RuntimeActivationError("active_module_safety_contract_invalid")
        return {"ok": True, "trusted": True, "active": True, "inspection": inspection, **_SAFETY}
    except RuntimeActivationError as exc:
        return _failure(exc.code, str(exc))
    except Exception as exc:
        return _failure("runtime_inspection_failed", str(exc))
