"""Runtime guard for durable writes shared across Hermes sessions.

The norm governance store is the durable control plane.  This module is the
small integration shim that write tools can use without knowing SQLite details:
normalize the resource key, acquire a short lease, run the mutation, then release
it.  When another live session owns the resource we fail closed instead of
silently overwriting.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from agent.norm_governance import LeaseConflict, NormGovernanceStore, normalize_resource_key


DEFAULT_WRITE_LEASE_TTL_SECONDS = 300


@dataclass(frozen=True)
class WriteGuardResult:
    resource_key: str
    owner_session_id: str
    lease_acquired: bool
    disabled: bool = False
    conflict: str = ""


class WriteGuardConflict(RuntimeError):
    """Raised when a durable write is blocked by another active writer."""

    def __init__(self, resource_key: str, active_owner_session_id: str, expires_at: float):
        self.resource_key = resource_key
        self.active_owner_session_id = active_owner_session_id
        self.expires_at = expires_at
        super().__init__(
            f"resource {resource_key} is currently leased by session "
            f"{active_owner_session_id} until {expires_at:.3f}; rebase/fuse instead of overwriting"
        )


def _truthy_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def norm_write_guard_enabled() -> bool:
    """Whether durable-write leases are enforced.

    Default-on for production; tests or emergency recovery can set
    HERMES_NORM_GOVERNANCE_WRITES=0.
    """

    return _truthy_env("HERMES_NORM_GOVERNANCE_WRITES", True)


def current_session_id() -> str:
    for key in ("HERMES_SESSION_ID", "HERMES_SESSION_KEY", "HERMES_RUN_ID"):
        value = os.environ.get(key)
        if value:
            return value
    return f"pid-{os.getpid()}@{socket.gethostname()}"


def governance_store_from_env() -> NormGovernanceStore:
    db_path = os.environ.get("HERMES_NORM_GOVERNANCE_DB")
    return NormGovernanceStore(db_path) if db_path else NormGovernanceStore()


def file_content_hash(path: str | Path) -> str:
    fp = Path(path)
    if not fp.exists():
        return ""
    h = hashlib.sha256()
    with fp.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@contextlib.contextmanager
def durable_write_guard(
    *,
    kind: str,
    identifier: str,
    fragment: str | None = None,
    write_mode: str = "patch",
    intent: str = "",
    task_id: str = "",
    base_hash: str = "",
    owner_session_id: str | None = None,
    ttl_seconds: int = DEFAULT_WRITE_LEASE_TTL_SECONDS,
    store: NormGovernanceStore | None = None,
) -> Iterator[WriteGuardResult]:
    """Acquire a norm-governance lease around a durable write.

    The guard is intentionally small and deterministic.  It does not hide
    conflicts: callers should return a tool error that names the resource and
    suggests rebase/fusion.
    """

    resource_key = normalize_resource_key(kind, identifier, fragment)
    owner = owner_session_id or current_session_id()
    if not norm_write_guard_enabled():
        yield WriteGuardResult(resource_key=resource_key, owner_session_id=owner, lease_acquired=False, disabled=True)
        return

    active_store = store or governance_store_from_env()
    try:
        active_store.acquire_lease(
            resource_key=resource_key,
            owner_session_id=owner,
            task_id=task_id,
            ttl_seconds=ttl_seconds,
            base_hash=base_hash,
            intent=intent,
            write_mode=write_mode,
        )
    except LeaseConflict as exc:
        raise WriteGuardConflict(exc.resource_key, exc.active_owner_session_id, exc.expires_at) from exc

    try:
        yield WriteGuardResult(resource_key=resource_key, owner_session_id=owner, lease_acquired=True)
    finally:
        active_store.release_lease(resource_key, owner)


def conflict_payload(exc: WriteGuardConflict) -> dict[str, object]:
    return {
        "success": False,
        "error": str(exc),
        "norm_governance": {
            "error": "write_lease_conflict",
            "resource_key": exc.resource_key,
            "active_owner_session_id": exc.active_owner_session_id,
            "expires_at": exc.expires_at,
            "remediation": "Refresh/rebase the task or create a FusionRecord before writing this shared resource.",
        },
    }
