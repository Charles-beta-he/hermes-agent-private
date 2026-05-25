"""Taskpool completion gates backed by norm governance.

Workers operate on a snapshot of durable norms.  Before a task is marked done,
this module verifies that the snapshot is still current and that the task's
write-set is not actively owned by another live session unless a FusionRecord
explicitly grants this session active-writer authority.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from agent.norm_governance import NormGovernanceStore, normalize_resource_key
from agent.norm_write_guard import current_session_id, governance_store_from_env


@dataclass(frozen=True)
class CompletionGateResult:
    allowed: bool
    reason: str
    remediation: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "remediation": self.remediation or None,
            "details": self.details,
        }


def _gate_enabled() -> bool:
    raw = os.environ.get("HERMES_NORM_COMPLETION_GATE")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _normalize_write_set(write_set: Iterable[str | Mapping[str, Any]]) -> list[str]:
    resources: list[str] = []
    for item in write_set:
        if isinstance(item, str):
            resources.append(item)
            continue
        kind = str(item.get("kind") or "")
        identifier = str(item.get("identifier") or item.get("path") or item.get("resource") or "")
        fragment = item.get("fragment")
        if kind and identifier:
            resources.append(normalize_resource_key(kind, identifier, str(fragment) if fragment is not None else None))
        elif item.get("resource_key"):
            resources.append(str(item["resource_key"]))
    seen: set[str] = set()
    unique: list[str] = []
    for resource in resources:
        if resource and resource not in seen:
            seen.add(resource)
            unique.append(resource)
    return unique


def _fusion_authorizes_completion(
    *,
    store: NormGovernanceStore,
    fusion_id: str | None,
    task_id: str,
    owner_session_id: str,
    write_set: list[str],
) -> tuple[bool, dict[str, Any]]:
    if not fusion_id:
        return False, {}
    fusion = store.get_fusion_record(fusion_id)
    if not fusion:
        return False, {"fusion_id": fusion_id, "fusion_error": "not_found"}
    if fusion.get("status") not in {"open", "active"}:
        return False, {"fusion_id": fusion_id, "fusion_error": "not_open", "status": fusion.get("status")}
    if fusion.get("active_writer") != owner_session_id:
        return False, {
            "fusion_id": fusion_id,
            "fusion_error": "not_active_writer",
            "active_writer": fusion.get("active_writer"),
        }
    if task_id not in set(str(t) for t in fusion.get("parent_task_ids") or []):
        return False, {"fusion_id": fusion_id, "fusion_error": "task_not_in_fusion"}
    fusion_resources: set[str] = set()
    for ws in fusion.get("write_sets") or []:
        for resource in ws.get("resources") or []:
            fusion_resources.add(str(resource))
    missing = [resource for resource in write_set if resource not in fusion_resources]
    if missing:
        return False, {"fusion_id": fusion_id, "fusion_error": "write_set_not_covered", "missing": missing}
    return True, {"fusion_id": fusion_id}


def evaluate_completion_gate(
    *,
    task_id: str,
    norm_snapshot_hash: str | None,
    write_set: Iterable[str | Mapping[str, Any]] = (),
    owner_session_id: str | None = None,
    fusion_id: str | None = None,
    store: NormGovernanceStore | None = None,
) -> CompletionGateResult:
    """Return whether a taskpool completion may advance to done.

    Fail-closed rules:
    - missing/stale norm snapshot blocks completion;
    - active leases owned by another session over this task's write-set block;
    - a valid FusionRecord can authorize one active writer to complete merged work.
    """

    if not _gate_enabled():
        return CompletionGateResult(True, "disabled", details={"task_id": task_id})

    active_store = store or governance_store_from_env()
    owner = owner_session_id or current_session_id()
    resources = _normalize_write_set(write_set)

    if not norm_snapshot_hash:
        return CompletionGateResult(
            False,
            "missing_norm_snapshot",
            "create_norm_snapshot",
            {"task_id": task_id, "write_set": resources},
        )

    if active_store.is_snapshot_stale(norm_snapshot_hash):
        return CompletionGateResult(
            False,
            "norm_snapshot_stale",
            "refresh_norm_snapshot",
            {"task_id": task_id, "snapshot_hash": norm_snapshot_hash, "write_set": resources},
        )

    active_leases = active_store.active_leases()
    conflicts = [
        {
            "resource_key": lease["resource_key"],
            "active_owner_session_id": lease["owner_session_id"],
            "expires_at": lease["expires_at"],
            "task_id": lease.get("task_id"),
        }
        for lease in active_leases
        if lease.get("resource_key") in resources and lease.get("owner_session_id") != owner
    ]
    if conflicts:
        authorized, fusion_details = _fusion_authorizes_completion(
            store=active_store,
            fusion_id=fusion_id,
            task_id=task_id,
            owner_session_id=owner,
            write_set=resources,
        )
        if not authorized:
            details: dict[str, Any] = {"task_id": task_id, "write_set": resources, "conflicts": conflicts}
            details.update(fusion_details)
            return CompletionGateResult(False, "active_write_conflict", "create_fusion_record", details)
        return CompletionGateResult(
            True,
            "ok",
            details={"task_id": task_id, "write_set": resources, "conflicts": conflicts, **fusion_details},
        )

    return CompletionGateResult(True, "ok", details={"task_id": task_id, "write_set": resources})
