"""Contracts and bounded starts for Hermes Event-Governed Agent Control Plane.

Most helpers are deterministic contract projections. The explicit runtime paths
first record a ledger-local started run, then may invoke an allowlisted builtin
read-only executor that appends a completed result. They still do not invoke
external agent processes, mutate task lifecycle, write memory/skills, send
messages, or change cron/provider configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

FAST_ACTIONS = frozenset({"read_ssot", "read_projection", "summarize", "health_check"})
STANDARD_ACTIONS = frozenset({"submit_evidence_packet", "submit_proposal", "run_read_only_verification"})
DANGEROUS_ACTIONS = frozenset(
    {
        "mark_task_done",
        "archive_task",
        "reopen_task",
        "memory_write",
        "skill_patch",
        "skill_create",
        "skill_delete",
        "cron_create",
        "cron_delete",
        "provider_config_change",
        "send_external_message",
        "external_write",
        "deploy",
        "delete_file",
    }
)
UNTRUSTED_SOURCES = frozenset({"webhook", "gateway", "cron", "dashboard", "projection"})
WORKER_RESULT_STATUSES = frozenset({"succeeded", "failed", "blocked", "needs_human", "partial"})
BUILTIN_READONLY_EXECUTORS = frozenset({"hermes_builtin_readonly"})
BUILTIN_READONLY_ACTIONS = frozenset({"summarize_started_run"})
CANONICAL_LIFECYCLE_STATUSES = frozenset({"pending", "claimed", "running", "retry", "blocked", "review", "done"})
ARTIFACT_TYPES = frozenset({"json", "log", "screenshot", "trace", "metrics", "transcript", "report"})
HOT_PATH_SUMMARY_MAX_CHARS = 240
WORKER_FORBIDDEN_RESULT_KEYS = frozenset(
    {
        "mark_done",
        "task_status",
        "lifecycle_status",
        "external_write",
        "memory_write",
        "skill_patch",
        "send_message",
        "provider_config_change",
    }
)

ROLE_CONTRACTS: dict[str, dict[str, Any]] = {
    "event_router": {
        "mode": "event_capture_only",
        "allowed_actions": ["normalize_event", "record_event"],
        "forbidden_actions": sorted(DANGEROUS_ACTIONS | {"terminal_execute", "file_write"}),
        "writes_enabled": False,
    },
    "control_tower": {
        "mode": "orchestrator_proposal_only",
        "allowed_actions": ["submit_proposal", "dispatch_bounded_worker", "request_policy_review"],
        "forbidden_actions": ["bypass_policy_gate", "self_approve", "mark_task_done_without_evidence"],
        "writes_enabled": False,
    },
    "bounded_worker": {
        "mode": "evidence_or_proposal_only",
        "allowed_actions": ["submit_evidence_packet", "submit_proposal", "run_scoped_task"],
        "forbidden_actions": sorted(DANGEROUS_ACTIONS | {"self_approve", "permission_escalation"}),
        "writes_enabled": False,
    },
    "verifier": {
        "mode": "verification_verdict_only",
        "allowed_actions": ["run_read_only_verification", "submit_verification_verdict"],
        "forbidden_actions": ["business_mutation", "external_write", "mark_task_done"],
        "writes_enabled": False,
    },
    "memory_skill_curator": {
        "mode": "persistent_learning_proposal_only",
        "allowed_actions": ["submit_memory_proposal", "submit_skill_patch_proposal"],
        "forbidden_actions": ["memory_write", "skill_patch", "skill_create", "skill_delete"],
        "writes_enabled": False,
    },
    "projection_builder": {
        "mode": "read_only_projection",
        "allowed_actions": ["read_ssot", "read_projection", "summarize"],
        "forbidden_actions": ["mutate_lifecycle", "external_write"],
        "writes_enabled": False,
    },
}

EVIDENCE_LEVELS = {
    "E0": "claim_only",
    "E1": "static_evidence",
    "E2": "execution_evidence",
    "E3": "external_receipt_or_human_acceptance",
    "E4": "sustained_operational_evidence",
}

AGENT_ARCHITECTURE_COVERAGE: tuple[dict[str, Any], ...] = (
    {
        "capability": "state_machine",
        "hermes_anchor": "7-state canonical lifecycle with policy/evidence guarded transitions",
        "external_agent_leverage": "dynamic workflow substates, pause/resume, and branch-local runtime state",
        "coverage_status": "partially_covered",
        "next_safe_slice": "add workflow_state/substate projections without changing canonical lifecycle authority",
        "authority_boundary": "do_not_add_custom lifecycle statuses that bypass pending/claimed/running/review/done gates",
        "priority": "P1",
    },
    {
        "capability": "scheduling",
        "hermes_anchor": "taskpool/Control Tower dispatch projections with priority and lease readback",
        "external_agent_leverage": "deadline, SLA, resource/quota-aware routing, and dynamic reprioritization",
        "coverage_status": "partially_covered",
        "next_safe_slice": "record deadline/resource profiles as report-only scheduling metadata",
        "authority_boundary": "do_not_dispatch workers from scheduling metadata without policy review and lease claim",
        "priority": "P1",
    },
    {
        "capability": "human_checkpoints",
        "hermes_anchor": "mandatory review/done human boundary and guarded high-risk approvals",
        "external_agent_leverage": "configurable mid-workflow checkpoints and async human responses",
        "coverage_status": "partially_covered",
        "next_safe_slice": "model checkpoint policy as explicit workflow contract data",
        "authority_boundary": "do_not_weaken no-autonomous-done invariant",
        "priority": "P2",
    },
    {
        "capability": "parallel_branches",
        "hermes_anchor": "bounded lane/worker execution with canonical ledger readback",
        "external_agent_leverage": "fork-join, parallel map, synchronization barriers, and merge aggregation",
        "coverage_status": "runtime_boundary_started",
        "next_safe_slice": "connect ledger-started runs to a governed worker executor behind policy gate and readback",
        "authority_boundary": "do_not_invoke high-parallel external workers before run ledger, join evidence gates, and stop/rollback paths exist",
        "priority": "P1",
    },
    {
        "capability": "evidence_gates",
        "hermes_anchor": "evidence spine, maturity gates, hashes, and artifact pointers",
        "external_agent_leverage": "richer artifacts such as logs, screenshots, traces, metrics, and reviewer packets",
        "coverage_status": "mostly_covered",
        "next_safe_slice": "keep hot ledger compact and add artifact type contracts/pointers",
        "authority_boundary": "do_not_store full transcripts or large evidence blobs in hot JSONL/frontmatter",
        "priority": "P2",
    },
    {
        "capability": "multi_model_support",
        "hermes_anchor": "multi-model quorum/certification with truth gate and best-within-constraints votes",
        "external_agent_leverage": "role-specific model routing, fallback, ensemble critique, and cost-aware selection",
        "coverage_status": "partially_covered",
        "next_safe_slice": "declare planner/implementer/reviewer/arbiter roles as routing metadata",
        "authority_boundary": "do_not_treat model consensus as fact without claim-linked evidence",
        "priority": "P2",
    },
    {
        "capability": "error_recovery",
        "hermes_anchor": "fail-closed retry/blocked/reconcile transitions with evidence-producing errors",
        "external_agent_leverage": "fine-grained error taxonomy, fallback strategies, and workflow adaptation",
        "coverage_status": "partially_covered",
        "next_safe_slice": "add deterministic error classes and allowed fallback proposals",
        "authority_boundary": "do_not_auto-unblock or auto-close from fallback classification alone",
        "priority": "P1",
    },
    {
        "capability": "workflow_composition",
        "hermes_anchor": "Control Tower workflow_runs/steps and taskpool-derived execution projections",
        "external_agent_leverage": "reusable workflow templates, nested workflows, component contracts, and versioning",
        "coverage_status": "gap_identified",
        "next_safe_slice": "add workflow template contract validation before nested runtime execution",
        "authority_boundary": "do_not_make workflow templates a second fact source separate from the ledger",
        "priority": "P1",
    },
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _path_for(action: str, risk_level: str) -> str:
    risk = risk_level.strip().upper()
    if action in FAST_ACTIONS and risk in {"L0", "L1"}:
        return "fast"
    if action in STANDARD_ACTIONS and risk in {"L0", "L1", "L2"}:
        return "standard"
    return "guarded"


def evaluate_agent_action(
    *,
    role: str,
    action: str,
    source: str,
    risk_level: str,
    evidence_level: str,
) -> dict[str, Any]:
    """Classify an agent action under the event-governed control-plane contract.

    The result is a report-only policy projection. It never grants runtime
    authority by itself; guarded actions still need the real policy/human gate.
    """

    normalized_role = role.strip().lower()
    normalized_action = action.strip().lower()
    normalized_source = source.strip().lower()
    normalized_risk = risk_level.strip().upper()
    normalized_evidence = evidence_level.strip().upper()
    contract = ROLE_CONTRACTS.get(normalized_role, {"mode": "unknown_role", "forbidden_actions": [], "writes_enabled": False})
    path = _path_for(normalized_action, normalized_risk)
    requires_human = path == "guarded" or normalized_source in UNTRUSTED_SOURCES and normalized_action in DANGEROUS_ACTIONS
    blocked_reason: str | None = None

    if normalized_role == "bounded_worker" and normalized_action in {"mark_task_done", "archive_task", "reopen_task"}:
        blocked_reason = "worker_cannot_mutate_lifecycle"
    elif normalized_action in {"memory_write", "skill_patch", "skill_create", "skill_delete"}:
        blocked_reason = "persistent_learning_write_requires_proposal_and_approval"
    elif normalized_source in UNTRUSTED_SOURCES and normalized_action in DANGEROUS_ACTIONS:
        blocked_reason = "untrusted_source_cannot_request_privileged_mutation_without_human_gate"
    elif normalized_action in DANGEROUS_ACTIONS:
        blocked_reason = "dangerous_action_requires_policy_and_human_gate"
    elif normalized_action not in set(contract.get("allowed_actions", [])) | FAST_ACTIONS | STANDARD_ACTIONS:
        blocked_reason = "action_not_declared_for_role"

    allowed = blocked_reason is None and not requires_human
    if path == "standard" and normalized_evidence not in {"E1", "E2", "E3", "E4"}:
        allowed = False
        blocked_reason = "standard_path_requires_evidence"

    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "report_only",
        "role": normalized_role,
        "role_mode": contract.get("mode", "unknown_role"),
        "action": normalized_action,
        "source": normalized_source,
        "risk_level": normalized_risk,
        "evidence_level": normalized_evidence,
        "path": path,
        "allowed": allowed,
        "requires_human": requires_human,
        "writes_enabled": False,
        "ledger_effect": "proposal_or_evidence_only",
        "blocked_reason": blocked_reason,
        "allowed_alternatives": ["submit_evidence_packet", "submit_proposal", "request_human_gate"] if blocked_reason else [],
    }


def validate_worker_result_contract(
    *,
    worker_type: str,
    worker_id: str,
    task_ref: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Validate a worker result as evidence/proposal only.

    This is the first safe worker-adapter contract for Claude Code/Codex/OpenCode
    style executors. It deliberately rejects lifecycle/external-write keys so a
    worker cannot smuggle authority through its result payload.
    """

    normalized_status = str(result.get("status", "")).strip().lower()
    evidence_refs = result.get("evidence_refs", [])
    proposal_refs = result.get("proposal_refs", [])
    forbidden_keys = sorted(WORKER_FORBIDDEN_RESULT_KEYS.intersection(result.keys()))
    blocked_reasons: list[str] = []

    if not worker_type.strip():
        blocked_reasons.append("worker_type_required")
    if not worker_id.strip():
        blocked_reasons.append("worker_id_required")
    if not task_ref.strip():
        blocked_reasons.append("task_ref_required")
    if normalized_status not in WORKER_RESULT_STATUSES:
        blocked_reasons.append("invalid_worker_result_status")
    if not isinstance(evidence_refs, list):
        blocked_reasons.append("evidence_refs_must_be_list")
    if not isinstance(proposal_refs, list):
        blocked_reasons.append("proposal_refs_must_be_list")
    if forbidden_keys:
        blocked_reasons.append("worker_result_contains_forbidden_authority_keys")
    if normalized_status == "succeeded" and not evidence_refs:
        blocked_reasons.append("succeeded_worker_result_requires_evidence_refs")

    accepted = not blocked_reasons
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "report_only",
        "worker_type": worker_type.strip(),
        "worker_id": worker_id.strip(),
        "task_ref": task_ref.strip(),
        "status": normalized_status,
        "accepted_as": "evidence_or_proposal" if accepted else "rejected_contract_projection",
        "allowed_to_mutate_lifecycle": False,
        "allowed_external_writes": False,
        "forbidden_keys": forbidden_keys,
        "evidence_refs": evidence_refs if isinstance(evidence_refs, list) else [],
        "proposal_refs": proposal_refs if isinstance(proposal_refs, list) else [],
        "blocked_reasons": blocked_reasons,
        "result_digest": _digest(result),
    }


def validate_artifact_pointer_contract(artifact: dict[str, Any]) -> dict[str, Any]:
    """Validate that evidence artifacts stay pointer-only in the hot path."""

    artifact_uri = str(artifact.get("artifact_uri", "")).strip()
    artifact_hash = str(artifact.get("artifact_hash", "")).strip()
    artifact_type = str(artifact.get("artifact_type", "")).strip().lower()
    summary = str(artifact.get("summary", ""))
    size_bytes = int(artifact.get("size_bytes", 0) or 0)
    errors: list[str] = []

    if not artifact_uri:
        errors.append("artifact_uri_required")
    if not artifact_hash.startswith("sha256:") or len(artifact_hash) != 71:
        errors.append("sha256_artifact_hash_required")
    if artifact_type not in ARTIFACT_TYPES:
        errors.append("unsupported_artifact_type")
    if not summary.strip():
        errors.append("summary_required")
    if len(summary) > HOT_PATH_SUMMARY_MAX_CHARS:
        errors.append("summary_too_long")
    if size_bytes < 0:
        errors.append("size_bytes_must_be_non_negative")
    if "inline_content" in artifact or "content" in artifact or "raw" in artifact:
        errors.append("inline_content_forbidden")

    accepted = not errors
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "report_only",
        "accepted_as": "artifact_pointer_contract" if accepted else "rejected_artifact_pointer_projection",
        "artifact_uri": artifact_uri,
        "artifact_hash": artifact_hash,
        "artifact_type": artifact_type,
        "summary_chars": len(summary),
        "size_bytes": size_bytes,
        "hot_path_payload_allowed": accepted,
        "external_artifact_required": True,
        "inline_content_allowed": False,
        "blocked_reasons": errors,
        "artifact_digest": _digest(artifact),
    }


def validate_workflow_metadata_contract(metadata: dict[str, Any]) -> dict[str, Any]:
    """Validate workflow metadata as a substate contract without lifecycle authority."""

    task_ref = str(metadata.get("task_ref", "")).strip()
    workflow_state = str(metadata.get("workflow_state", "")).strip().lower()
    lane = str(metadata.get("lane", "")).strip()
    checkpoint = metadata.get("checkpoint", {})
    errors: list[str] = []

    if not task_ref:
        errors.append("task_ref_required")
    if not workflow_state:
        errors.append("workflow_state_required")
    if workflow_state in CANONICAL_LIFECYCLE_STATUSES:
        errors.append("workflow_state_must_not_be_canonical_lifecycle_status")
    if not lane:
        errors.append("lane_required")
    if "canonical_status" in metadata or "task_status" in metadata or "lifecycle_status" in metadata:
        errors.append("canonical_status_forbidden_in_metadata_contract")
    if not isinstance(metadata.get("resource_profile", {}), dict):
        errors.append("resource_profile_must_be_object")
    if not isinstance(metadata.get("quota_profile", {}), dict):
        errors.append("quota_profile_must_be_object")
    if checkpoint:
        if not isinstance(checkpoint, dict):
            errors.append("checkpoint_must_be_object")
            checkpoint = {}
        elif checkpoint.get("required") and not checkpoint.get("owner"):
            errors.append("checkpoint_owner_required")

    accepted = not errors
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "report_only",
        "task_ref": task_ref,
        "workflow_state": workflow_state,
        "lane": lane,
        "accepted_as": "workflow_metadata_contract" if accepted else "rejected_workflow_metadata_projection",
        "canonical_lifecycle_unchanged": True,
        "allowed_to_mutate_lifecycle": False,
        "runtime_dispatch_enabled": False,
        "checkpoint_required": bool(isinstance(checkpoint, dict) and checkpoint.get("required")),
        "blocked_reasons": errors,
        "metadata_digest": _digest(metadata),
    }


def validate_fork_join_graph_contract(graph: dict[str, Any]) -> dict[str, Any]:
    """Validate a fork/join graph as a ledger projection, not an executor.

    Nodes describe taskpool-derived execution shape only. A valid graph still has
    runtime fanout disabled until a separate dispatcher/policy slice exists.
    """

    nodes = graph.get("nodes", [])
    errors: list[str] = []
    if not isinstance(nodes, list) or not nodes:
        errors.append("nodes_required")
        nodes = []

    node_ids: list[str] = []
    deps_by_id: dict[str, list[str]] = {}
    for node in nodes:
        if not isinstance(node, dict):
            errors.append("node_must_be_object")
            continue
        node_id = str(node.get("id", "")).strip()
        if not node_id:
            errors.append("node_id_required")
            continue
        node_ids.append(node_id)
        requires = node.get("requires", [])
        if not isinstance(requires, list):
            errors.append(f"requires_must_be_list:{node_id}")
            requires = []
        deps_by_id[node_id] = [str(dep).strip() for dep in requires if str(dep).strip()]
        if str(node.get("kind", "")).strip().lower() == "join":
            if len(deps_by_id[node_id]) < 2:
                errors.append(f"join_requires_two_or_more_inputs:{node_id}")
            if not node.get("evidence_refs_required"):
                errors.append(f"join_requires_evidence_refs:{node_id}")

    if len(set(node_ids)) != len(node_ids):
        errors.append("duplicate_node_id")
    known = set(node_ids)
    for node_id, deps in deps_by_id.items():
        for dep in deps:
            if dep not in known:
                errors.append(f"missing_dependency:{node_id}:{dep}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited or "cycle_detected" in errors:
            return
        if node_id in visiting:
            errors.append("cycle_detected")
            return
        visiting.add(node_id)
        for dep in deps_by_id.get(node_id, []):
            if dep in deps_by_id:
                visit(dep)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in node_ids:
        visit(node_id)

    accepted = not errors
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "report_only",
        "graph_id": str(graph.get("graph_id", "")).strip(),
        "accepted_as": "ledger_projection_contract" if accepted else "rejected_graph_contract_projection",
        "node_count": len(node_ids),
        "join_count": sum(1 for node in nodes if isinstance(node, dict) and str(node.get("kind", "")).lower() == "join"),
        "runtime_parallel_dispatch_enabled": False,
        "lifecycle_mutation_enabled": False,
        "ledger_projection_only": True,
        "blocked_reasons": errors,
        "graph_digest": _digest(graph),
    }


def start_bounded_runtime_run(*, request: dict[str, Any], ledger_path: str | Path) -> dict[str, Any]:
    """Record the first real-but-ledger-only runtime start boundary.

    This is intentionally narrower than external worker dispatch: it writes a
    durable started run record and idempotently returns it on retry, but it does
    not invoke external executors, mutate task lifecycle, or write adapters.
    """

    path = Path(ledger_path)
    task_ref = str(request.get("task_ref", "")).strip()
    worker_type = str(request.get("worker_type", "")).strip().lower()
    worker_id = str(request.get("worker_id", "")).strip()
    run_purpose = str(request.get("run_purpose", "")).strip()
    workflow_state = str(request.get("workflow_state", "")).strip().lower()
    evidence_refs = request.get("evidence_refs", [])
    forbidden_keys = sorted(WORKER_FORBIDDEN_RESULT_KEYS.intersection(request.keys()))
    blocked_reasons: list[str] = []

    if not task_ref:
        blocked_reasons.append("task_ref_required")
    if not worker_type:
        blocked_reasons.append("worker_type_required")
    if not worker_id:
        blocked_reasons.append("worker_id_required")
    if not run_purpose:
        blocked_reasons.append("run_purpose_required")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("runtime_start_requires_evidence_refs")
    if workflow_state and workflow_state in CANONICAL_LIFECYCLE_STATUSES:
        blocked_reasons.append("workflow_state_must_not_be_canonical_lifecycle_status")
    if forbidden_keys:
        blocked_reasons.append("runtime_start_request_contains_forbidden_authority_keys")

    request_digest = _digest(request)
    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "runtime_start",
        "runtime_boundary": "started_ledger_only",
        "task_ref": task_ref,
        "worker_type": worker_type,
        "worker_id": worker_id,
        "run_purpose": run_purpose,
        "workflow_state": workflow_state,
        "request_digest": request_digest,
        "external_executor_invoked": False,
        "canonical_lifecycle_unchanged": True,
        "allowed_to_mutate_lifecycle": False,
        "external_adapter_write_through_enabled": False,
        "persistent_learning_write_enabled": False,
        "blocked_reasons": blocked_reasons,
    }

    if blocked_reasons:
        return {
            **base,
            "accepted_as": "rejected_runtime_run_start",
            "run_status": "rejected",
            "run_id": "",
            "idempotent_replay": False,
        }

    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                existing = json.loads(line)
            except json.JSONDecodeError:
                continue
            if existing.get("request_digest") == request_digest:
                return {**existing, "idempotent_replay": True}

    run_id = "run_" + _digest({"task_ref": task_ref, "worker_id": worker_id, "request_digest": request_digest})[:24]
    record = {
        **base,
        "accepted_as": "bounded_runtime_run_started",
        "run_status": "started",
        "run_id": run_id,
        "idempotent_replay": False,
        "evidence_refs": evidence_refs,
        "started_at": _now(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def _read_runtime_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def execute_bounded_runtime_run(*, ledger_path: str | Path, run_id: str, executor_request: dict[str, Any]) -> dict[str, Any]:
    """Execute the first governed builtin read-only executor for a started run.

    This crosses from merely recording a start into invoking a constrained local
    executor. It remains ledger-local and read-only: external agent processes,
    lifecycle mutation, adapter writes, and persistent learning are still off.
    """

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    executor_type = str(executor_request.get("executor_type", "")).strip().lower()
    action = str(executor_request.get("action", "")).strip().lower()
    evidence_refs = executor_request.get("evidence_refs", [])
    request_digest = _digest(executor_request)
    rows = _read_runtime_ledger(path)
    started = next((row for row in rows if row.get("run_id") == normalized_run_id and row.get("run_status") == "started"), None)
    completed = next((row for row in rows if row.get("run_id") == normalized_run_id and row.get("run_status") == "completed"), None)
    blocked_reasons: list[str] = []

    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if started is None:
        blocked_reasons.append("started_run_not_found")
    if executor_type not in BUILTIN_READONLY_EXECUTORS:
        blocked_reasons.append("executor_type_not_allowlisted")
    if action not in BUILTIN_READONLY_ACTIONS:
        blocked_reasons.append("executor_action_not_allowlisted")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("executor_requires_evidence_refs")
    if sorted(WORKER_FORBIDDEN_RESULT_KEYS.intersection(executor_request.keys())):
        blocked_reasons.append("executor_request_contains_forbidden_authority_keys")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "runtime_execution",
        "run_id": normalized_run_id,
        "executor_type": executor_type,
        "action": action,
        "executor_request_digest": request_digest,
        "executor_invoked": False,
        "external_executor_invoked": False,
        "canonical_lifecycle_unchanged": True,
        "allowed_to_mutate_lifecycle": False,
        "external_adapter_write_through_enabled": False,
        "persistent_learning_write_enabled": False,
        "blocked_reasons": blocked_reasons,
    }

    if completed is not None and completed.get("executor_request_digest") == request_digest:
        return {**completed, "idempotent_replay": True}

    if blocked_reasons:
        return {
            **base,
            "accepted_as": "rejected_runtime_execution",
            "run_status": "rejected",
            "runtime_boundary": "builtin_readonly_executor_rejected",
            "idempotent_replay": False,
        }

    assert started is not None
    result = {
        "summary": "Started run verified from ledger",
        "verified_started_run_id": normalized_run_id,
        "verified_task_ref": started.get("task_ref"),
        "verified_request_digest": started.get("request_digest"),
        "evidence_refs": evidence_refs,
    }
    record = {
        **base,
        "accepted_as": "bounded_runtime_run_completed",
        "run_status": "completed",
        "runtime_boundary": "builtin_readonly_executor_completed",
        "executor_invoked": True,
        "idempotent_replay": False,
        "task_ref": started.get("task_ref"),
        "worker_type": started.get("worker_type"),
        "worker_id": started.get("worker_id"),
        "result": result,
        "completed_at": _now(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def validate_certification_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Validate a multi-model certification packet without granting authority."""

    reviewers = contract.get("reviewers", [])
    claims = contract.get("claims", [])
    quorum = int(contract.get("quorum", 0) or 0)
    truth_gate = bool(contract.get("truth_gate"))
    errors: list[str] = []

    if not isinstance(reviewers, list) or len(reviewers) < 2:
        errors.append("at_least_two_reviewers_required")
        reviewers = [] if not isinstance(reviewers, list) else reviewers
    if quorum < 2:
        errors.append("quorum_must_be_at_least_two")
    if quorum > len(reviewers):
        errors.append("quorum_exceeds_reviewer_count")
    if not truth_gate:
        errors.append("truth_gate_required")
    if not isinstance(claims, list) or not claims:
        errors.append("claims_required")
        claims = [] if not isinstance(claims, list) else claims

    unsupported_claims: list[str] = []
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            unsupported_claims.append(f"claim:{index}")
            continue
        claim_id = str(claim.get("id") or f"claim:{index}")
        evidence_refs = claim.get("evidence_refs", [])
        if not isinstance(evidence_refs, list) or not evidence_refs:
            unsupported_claims.append(claim_id)
    if unsupported_claims:
        errors.append("all_claims_require_evidence_refs")

    certification_possible = not errors
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "report_only",
        "subject_ref": str(contract.get("subject_ref", "")).strip(),
        "certification_possible": certification_possible,
        "accepted_as": "certification_projection" if certification_possible else "rejected_certification_projection",
        "reviewer_count": len(reviewers),
        "quorum": quorum,
        "truth_gate": truth_gate,
        "unsupported_claims": unsupported_claims,
        "allowed_to_mark_done": False,
        "model_consensus_is_fact": False,
        "blocked_reasons": errors,
        "contract_digest": _digest(contract),
    }


def normalize_event(
    *,
    event_type: str,
    source: str,
    actor: str,
    subject_ref: str,
    payload: dict[str, Any],
    dedupe_key: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic event envelope without mutating the ledger."""

    normalized_source = source.strip().lower()
    target_status = str(payload.get("target_status", "")).lower()
    trust_level = "untrusted" if normalized_source in UNTRUSTED_SOURCES else "trusted"
    may_mutate_lifecycle = trust_level == "trusted" and target_status not in {"done", "archived", "reopen", "reopened"}
    requires_human = target_status in {"done", "archived", "reopen", "reopened"} or not may_mutate_lifecycle
    payload_digest = _digest(payload)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "report_only",
        "event_id": "evt_" + _digest({"event_type": event_type, "source": source, "actor": actor, "subject_ref": subject_ref, "payload_digest": payload_digest})[:24],
        "event_type": event_type,
        "source": normalized_source,
        "actor": actor,
        "subject_ref": subject_ref,
        "payload_digest": payload_digest,
        "payload": payload,
        "dedupe_key": dedupe_key or payload_digest,
        "permission_context": {
            "trust_level": trust_level,
            "may_mutate_lifecycle": may_mutate_lifecycle,
            "may_write_memory": False,
            "may_write_skill": False,
            "may_send_external_message": False,
        },
        "requires_human_confirmation": requires_human,
        "created_at": _now(),
    }


def build_agent_architecture_coverage_report() -> dict[str, Any]:
    """Return the report-only Claude/Codex/OpenCode-to-Hermes coverage contract.

    The report is intentionally deterministic and non-authorizing: it can guide
    planning, dashboards, and model reviews, but it does not dispatch workers or
    mutate lifecycle state.
    """

    items = [dict(item) for item in AGENT_ARCHITECTURE_COVERAGE]
    priority_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for item in items:
        priority_counts[item["priority"]] = priority_counts.get(item["priority"], 0) + 1
        status_counts[item["coverage_status"]] = status_counts.get(item["coverage_status"], 0) + 1

    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "report_only",
        "architecture": "Hermes Agent Architecture Coverage",
        "ssot": {
            "canonical": "Control Tower Ledger / taskpool by fact domain",
            "external_agents_are_fact_sources": False,
            "model_outputs_are_fact_sources": False,
            "worker_outputs_are_evidence_or_proposals": True,
        },
        "coverage_count": len(items),
        "coverage_status_counts": status_counts,
        "priority_counts": priority_counts,
        "capabilities": items,
        "implementation_sequence": [
            "workflow_state_and_contract_metadata",
            "fork_join_graph_contract",
            "worker_adapter_result_contract",
            "multi_model_certification_contract",
            "evidence_artifact_pointer_contract",
        ],
        "phase_boundary": {
            "runtime_worker_dispatch_enabled": False,
            "lifecycle_mutation_enabled": False,
            "external_adapter_write_enabled": False,
            "requires_separate_human_gate_before_enabling": True,
        },
    }


def build_control_plane_report() -> dict[str, Any]:
    """Return the stable control-plane contract report."""

    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "report_only",
        "architecture": "Hermes Event-Governed Agent Control Plane",
        "ssot": {
            "canonical": "Control Tower Ledger / taskpool / brain by fact domain",
            "projection_is_disposable": True,
            "model_output_is_fact": False,
            "worker_output_is_fact": False,
        },
        "invariants": {
            "worker_may_mark_done": False,
            "model_consensus_is_fact": False,
            "gateway_may_mutate_lifecycle": False,
            "dashboard_may_mutate_lifecycle_directly": False,
            "memory_skill_default_autowrite": False,
            "cron_webhook_inherit_cli_privilege": False,
        },
        "paths": {
            "fast": {"risk_levels": ["L0", "L1"], "writes_enabled": False},
            "standard": {"requires": ["evidence_packet", "verifier_verdict"], "writes_enabled": False},
            "guarded": {"requires": ["policy_gate", "human_confirmation", "audit_record"], "writes_enabled": False},
        },
        "danger_gate": {
            "dangerous_actions": sorted(DANGEROUS_ACTIONS),
            "dangerous_actions_require_human": True,
            "action_preview_required": True,
            "rollback_or_escape_required": True,
        },
        "evidence_levels": EVIDENCE_LEVELS,
        "roles": ROLE_CONTRACTS,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report Hermes Event-Governed Agent Control Plane contracts")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    parser.add_argument("--check-action", action="store_true", help="Evaluate one action instead of full report")
    parser.add_argument("--coverage", action="store_true", help="Emit report-only agent architecture coverage matrix")
    parser.add_argument("--start-runtime-run", action="store_true", help="Record a ledger-only bounded runtime run start")
    parser.add_argument("--execute-runtime-run", action="store_true", help="Execute an allowlisted builtin read-only runtime run")
    parser.add_argument("--runtime-ledger", default="", help="JSONL ledger path for runtime run operations")
    parser.add_argument("--runtime-request-json", default="{}", help="JSON request payload for --start-runtime-run")
    parser.add_argument("--run-id", default="", help="Run id for --execute-runtime-run")
    parser.add_argument("--executor-request-json", default="{}", help="JSON executor payload for --execute-runtime-run")
    parser.add_argument("--role", default="bounded_worker")
    parser.add_argument("--action", default="submit_evidence_packet")
    parser.add_argument("--source", default="cli")
    parser.add_argument("--risk-level", default="L1")
    parser.add_argument("--evidence-level", default="E2")
    args = parser.parse_args(argv)

    if args.check_action:
        result = evaluate_agent_action(
            role=args.role,
            action=args.action,
            source=args.source,
            risk_level=args.risk_level,
            evidence_level=args.evidence_level,
        )
    elif args.start_runtime_run:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --start-runtime-run")
        request = json.loads(args.runtime_request_json)
        if not isinstance(request, dict):
            parser.error("--runtime-request-json must decode to an object")
        result = start_bounded_runtime_run(request=request, ledger_path=args.runtime_ledger)
    elif args.execute_runtime_run:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --execute-runtime-run")
        if not args.run_id:
            parser.error("--run-id is required with --execute-runtime-run")
        executor_request = json.loads(args.executor_request_json)
        if not isinstance(executor_request, dict):
            parser.error("--executor-request-json must decode to an object")
        result = execute_bounded_runtime_run(ledger_path=args.runtime_ledger, run_id=args.run_id, executor_request=executor_request)
    elif args.coverage:
        result = build_agent_architecture_coverage_report()
    else:
        result = build_control_plane_report()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{result['architecture'] if 'architecture' in result else 'Agent action verdict'}: {result['mode']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
