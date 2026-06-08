"""Contracts and bounded starts for Hermes Event-Governed Agent Control Plane.

Most helpers are deterministic contract projections. The explicit runtime paths
first record a ledger-local started run, then may invoke an allowlisted builtin
read-only executor that appends a completed result. Separate external-agent
paths may run ``--version`` probes, send no-tool/no-write dry-run prompts,
attach read-only context artifact pointers, record validated proposal artifact
pointers, record multi-model certification promotion packets, project elevated
done authorizations, execute a ledger-local fact mutation, project an external
adapter write intent, project an external adapter write authorization, execute
a local-file adapter harness write-through, execute a digest-bound taskpool
note closeout, execute a digest-bound Kanban DB closeout, execute a
digest-bound taskpool/Kanban rollback, or enqueue Lark/Telegram outbox
notifications without sending, or project elevated delivery authorization
without sending, execute Telegram/Lark sender dry-runs, project real sender
preflight while the sender remains disabled, or project elevated real sender
execution authorization without reading tokens or sending, or execute an
authorized Telegram real sender. Only the explicit
ledger-local fact mutation can change canonical lifecycle facts, only the
local-file adapter harness can write adapter-state fixtures, taskpool note
closeout/rollback can mutate taskpool markdown frontmatter status, Kanban DB
closeout/rollback can mutate a single tasks.status row, the outbox executor
can append pending no-secret notification intents, the delivery authorization
projection can authorize a future sender without calling platform APIs, the
Telegram/Lark dry-runs can record a payload hash/summary without reading tokens
or sending, real sender preflight can verify readiness without enabling or
executing send, real sender execution authorization can grant a short-lived
future execution capability without touching secrets or platform APIs, and the
Telegram real sender can execute only against exact authorization/hash binding
while never storing token values; no path writes memory/skills,
outbound messages, or cron/provider configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import json
import sqlite3
import subprocess
import urllib.error
import urllib.request
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
EXTERNAL_AGENT_PROBE_COMMANDS = {"claude_code": "claude", "codex": "codex", "opencode": "opencode"}
CANONICAL_LIFECYCLE_STATUSES = frozenset({"pending", "claimed", "running", "retry", "blocked", "review", "done"})
ARTIFACT_TYPES = frozenset({"json", "log", "screenshot", "trace", "metrics", "transcript", "report"})
HOT_PATH_SUMMARY_MAX_CHARS = 240
ELEVATED_DONE_AUTH_ROLES = frozenset({"admin", "elevated_operator", "human_admin"})
ALLOWED_EXTERNAL_WRITE_INTENT_ADAPTERS = frozenset({"taskpool", "kanban", "lark", "telegram"})
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


def probe_external_agent_runtime_run(*, ledger_path: str | Path, run_id: str, probe_request: dict[str, Any]) -> dict[str, Any]:
    """Invoke a narrow external agent version probe for a started run.

    This is the first external-process boundary. It only runs an allowlisted
    binary with ``--version`` and appends the probe receipt; it does not pass a
    task prompt, invoke tools, mutate lifecycle, or write external adapters.
    """

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    executor_type = str(probe_request.get("executor_type", "")).strip().lower()
    command_path = str(probe_request.get("command_path", "")).strip()
    evidence_refs = probe_request.get("evidence_refs", [])
    request_digest = _digest(probe_request)
    rows = _read_runtime_ledger(path)
    started = next((row for row in rows if row.get("run_id") == normalized_run_id and row.get("run_status") == "started"), None)
    expected_basename = EXTERNAL_AGENT_PROBE_COMMANDS.get(executor_type)
    command_basename = Path(command_path).name if command_path else ""
    blocked_reasons: list[str] = []

    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if started is None:
        blocked_reasons.append("started_run_not_found")
    if expected_basename is None:
        blocked_reasons.append("executor_type_not_allowlisted")
    if not command_path:
        blocked_reasons.append("command_path_required")
    elif expected_basename is not None and command_basename != expected_basename:
        blocked_reasons.append("command_path_basename_mismatch")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("external_probe_requires_evidence_refs")
    if sorted(WORKER_FORBIDDEN_RESULT_KEYS.intersection(probe_request.keys())):
        blocked_reasons.append("external_probe_request_contains_forbidden_authority_keys")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "external_agent_probe",
        "run_id": normalized_run_id,
        "executor_type": executor_type,
        "command_basename": command_basename,
        "probe_request_digest": request_digest,
        "external_executor_invoked": False,
        "allowed_to_mutate_lifecycle": False,
        "canonical_lifecycle_unchanged": True,
        "external_adapter_write_through_enabled": False,
        "persistent_learning_write_enabled": False,
        "blocked_reasons": blocked_reasons,
    }

    if blocked_reasons:
        return {
            **base,
            "accepted_as": "rejected_external_agent_probe",
            "run_status": "rejected",
            "runtime_boundary": "external_agent_version_probe_rejected",
        }

    assert started is not None
    proc = subprocess.run([command_path, "--version"], check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    record = {
        **base,
        "accepted_as": "external_agent_probe_completed",
        "run_status": "external_probe_completed",
        "runtime_boundary": "external_agent_version_probe_completed",
        "external_executor_invoked": True,
        "task_prompt_sent": False,
        "task_ref": started.get("task_ref"),
        "worker_type": started.get("worker_type"),
        "worker_id": started.get("worker_id"),
        "probe": {
            "argv": [command_basename, "--version"],
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip()[:HOT_PATH_SUMMARY_MAX_CHARS],
            "stderr": proc.stderr.strip()[:HOT_PATH_SUMMARY_MAX_CHARS],
            "evidence_refs": evidence_refs,
        },
        "probed_at": _now(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def dry_run_external_agent_prompt(*, ledger_path: str | Path, run_id: str, prompt_request: dict[str, Any]) -> dict[str, Any]:
    """Send a no-tool/no-write prompt to an allowlisted external agent.

    This is intentionally a dry-run packet boundary. It sends a prompt, captures
    stdout/stderr into an external artifact file, appends only a pointer to the
    run ledger, and keeps lifecycle/adapter/write authority disabled.
    """

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    executor_type = str(prompt_request.get("executor_type", "")).strip().lower()
    command_path = str(prompt_request.get("command_path", "")).strip()
    prompt = str(prompt_request.get("prompt", "")).strip()
    artifact_path_raw = str(prompt_request.get("artifact_path", "")).strip()
    evidence_refs = prompt_request.get("evidence_refs", [])
    tools_enabled = bool(prompt_request.get("tools_enabled", False))
    write_mode_enabled = bool(prompt_request.get("write_mode_enabled", False))
    request_digest = _digest(prompt_request)
    rows = _read_runtime_ledger(path)
    started = next((row for row in rows if row.get("run_id") == normalized_run_id and row.get("run_status") == "started"), None)
    expected_basename = EXTERNAL_AGENT_PROBE_COMMANDS.get(executor_type)
    command_basename = Path(command_path).name if command_path else ""
    artifact_path = Path(artifact_path_raw) if artifact_path_raw else None
    blocked_reasons: list[str] = []

    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if started is None:
        blocked_reasons.append("started_run_not_found")
    if expected_basename is None:
        blocked_reasons.append("executor_type_not_allowlisted")
    if not command_path:
        blocked_reasons.append("command_path_required")
    elif expected_basename is not None and command_basename != expected_basename:
        blocked_reasons.append("command_path_basename_mismatch")
    if not prompt:
        blocked_reasons.append("prompt_required")
    if not artifact_path_raw:
        blocked_reasons.append("artifact_path_required")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("prompt_dry_run_requires_evidence_refs")
    if tools_enabled:
        blocked_reasons.append("tools_must_be_disabled")
    if write_mode_enabled:
        blocked_reasons.append("write_mode_must_be_disabled")
    if sorted(WORKER_FORBIDDEN_RESULT_KEYS.intersection(prompt_request.keys())):
        blocked_reasons.append("prompt_request_contains_forbidden_authority_keys")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "external_agent_prompt_dry_run",
        "run_id": normalized_run_id,
        "executor_type": executor_type,
        "command_basename": command_basename,
        "prompt_request_digest": request_digest,
        "external_executor_invoked": False,
        "task_prompt_sent": False,
        "tools_enabled": False,
        "write_mode_enabled": False,
        "allowed_to_mutate_lifecycle": False,
        "canonical_lifecycle_unchanged": True,
        "external_adapter_write_through_enabled": False,
        "persistent_learning_write_enabled": False,
        "blocked_reasons": blocked_reasons,
    }

    if blocked_reasons:
        return {
            **base,
            "accepted_as": "rejected_external_agent_prompt_dry_run",
            "run_status": "rejected",
            "runtime_boundary": "external_agent_prompt_dry_run_rejected",
        }

    assert started is not None
    assert artifact_path is not None
    argv = [
        command_path,
        "-p",
        prompt,
        "--permission-mode",
        "plan",
        "--tools",
        "",
        "--no-session-persistence",
        "--output-format",
        "json",
    ]
    proc = subprocess.run(argv, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    artifact_payload = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "external_agent_prompt_dry_run",
        "run_id": normalized_run_id,
        "executor_type": executor_type,
        "argv": [command_basename, "-p", "<prompt>", "--permission-mode", "plan", "--tools", "", "--no-session-persistence", "--output-format", "json"],
        "prompt_digest": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "evidence_refs": evidence_refs,
    }
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_bytes = json.dumps(artifact_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    artifact_path.write_bytes(artifact_bytes)
    record = {
        **base,
        "accepted_as": "external_agent_prompt_dry_run_completed",
        "run_status": "external_prompt_dry_run_completed",
        "runtime_boundary": "external_agent_prompt_dry_run_completed",
        "external_executor_invoked": True,
        "task_prompt_sent": True,
        "task_ref": started.get("task_ref"),
        "worker_type": started.get("worker_type"),
        "worker_id": started.get("worker_id"),
        "artifact": {
            "artifact_uri": f"file://{artifact_path}",
            "artifact_type": "json",
            "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
            "size_bytes": len(artifact_bytes),
            "summary": (proc.stdout or proc.stderr).strip()[:HOT_PATH_SUMMARY_MAX_CHARS],
            "evidence_refs": evidence_refs,
        },
        "completed_at": _now(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def run_external_agent_readonly_context(*, ledger_path: str | Path, run_id: str, context_request: dict[str, Any]) -> dict[str, Any]:
    """Send a no-write/no-tool prompt that references an external context artifact.

    The hot ledger receives only the context pointer/hash and output artifact
    pointer/hash. Inline context remains forbidden so large evidence stays off
    the runtime hot path.
    """

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    executor_type = str(context_request.get("executor_type", "")).strip().lower()
    command_path = str(context_request.get("command_path", "")).strip()
    instruction = str(context_request.get("instruction", "")).strip()
    context_artifact_uri = str(context_request.get("context_artifact_uri", "")).strip()
    context_sha256 = str(context_request.get("context_sha256", "")).strip().lower()
    output_artifact_path_raw = str(context_request.get("output_artifact_path", "")).strip()
    evidence_refs = context_request.get("evidence_refs", [])
    tools_enabled = bool(context_request.get("tools_enabled", False))
    write_mode_enabled = bool(context_request.get("write_mode_enabled", False))
    request_digest = _digest(context_request)
    rows = _read_runtime_ledger(path)
    started = next((row for row in rows if row.get("run_id") == normalized_run_id and row.get("run_status") == "started"), None)
    expected_basename = EXTERNAL_AGENT_PROBE_COMMANDS.get(executor_type)
    command_basename = Path(command_path).name if command_path else ""
    output_artifact_path = Path(output_artifact_path_raw) if output_artifact_path_raw else None
    context_file_path: Path | None = None
    blocked_reasons: list[str] = []

    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if started is None:
        blocked_reasons.append("started_run_not_found")
    if expected_basename is None:
        blocked_reasons.append("executor_type_not_allowlisted")
    if not command_path:
        blocked_reasons.append("command_path_required")
    elif expected_basename is not None and command_basename != expected_basename:
        blocked_reasons.append("command_path_basename_mismatch")
    if not instruction:
        blocked_reasons.append("instruction_required")
    if not context_artifact_uri:
        blocked_reasons.append("context_artifact_uri_required")
    elif not context_artifact_uri.startswith("file://"):
        blocked_reasons.append("context_artifact_uri_must_be_file_uri")
    else:
        context_file_path = Path(context_artifact_uri.removeprefix("file://"))
        if not context_file_path.exists() or not context_file_path.is_file():
            blocked_reasons.append("context_artifact_not_found")
    if not context_sha256:
        blocked_reasons.append("context_sha256_required")
    elif len(context_sha256) != 64:
        blocked_reasons.append("context_sha256_invalid")
    if context_file_path is not None and context_file_path.exists() and context_sha256:
        actual_sha = hashlib.sha256(context_file_path.read_bytes()).hexdigest()
        if actual_sha != context_sha256:
            blocked_reasons.append("context_sha256_mismatch")
    if "inline_context" in context_request:
        blocked_reasons.append("inline_context_forbidden")
    if not output_artifact_path_raw:
        blocked_reasons.append("output_artifact_path_required")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("readonly_context_requires_evidence_refs")
    if tools_enabled:
        blocked_reasons.append("tools_must_be_disabled")
    if write_mode_enabled:
        blocked_reasons.append("write_mode_must_be_disabled")
    if sorted(WORKER_FORBIDDEN_RESULT_KEYS.intersection(context_request.keys())):
        blocked_reasons.append("context_request_contains_forbidden_authority_keys")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "external_agent_readonly_context",
        "run_id": normalized_run_id,
        "executor_type": executor_type,
        "command_basename": command_basename,
        "context_request_digest": request_digest,
        "external_executor_invoked": False,
        "task_prompt_sent": False,
        "readonly_context_attached": False,
        "tools_enabled": False,
        "write_mode_enabled": False,
        "allowed_to_mutate_lifecycle": False,
        "canonical_lifecycle_unchanged": True,
        "external_adapter_write_through_enabled": False,
        "persistent_learning_write_enabled": False,
        "blocked_reasons": blocked_reasons,
    }

    if blocked_reasons:
        return {
            **base,
            "accepted_as": "rejected_external_agent_readonly_context",
            "run_status": "rejected",
            "runtime_boundary": "external_agent_readonly_context_rejected",
        }

    assert started is not None
    assert output_artifact_path is not None
    prompt = (
        f"{instruction}\n\n"
        "Hermes governed readonly context packet.\n"
        f"Run id: {normalized_run_id}\n"
        f"Task ref: {started.get('task_ref')}\n"
        f"Context artifact: {context_artifact_uri}\n"
        f"Context sha256: {context_sha256}\n\n"
        "Rules: do not call tools, do not edit files, do not run shell commands, "
        "do not mutate task lifecycle, and treat the context artifact as readonly evidence."
    )
    argv = [
        command_path,
        "-p",
        prompt,
        "--permission-mode",
        "plan",
        "--tools",
        "",
        "--no-session-persistence",
        "--output-format",
        "json",
    ]
    proc = subprocess.run(argv, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    artifact_payload = {
        "schema_version": SCHEMA_VERSION,
        "artifact_type": "external_agent_readonly_context",
        "run_id": normalized_run_id,
        "executor_type": executor_type,
        "argv": [command_basename, "-p", "<readonly_context_prompt>", "--permission-mode", "plan", "--tools", "", "--no-session-persistence", "--output-format", "json"],
        "instruction_digest": hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
        "context_artifact_uri": context_artifact_uri,
        "context_sha256": context_sha256,
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "evidence_refs": evidence_refs,
    }
    output_artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_bytes = json.dumps(artifact_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    output_artifact_path.write_bytes(artifact_bytes)
    record = {
        **base,
        "accepted_as": "external_agent_readonly_context_completed",
        "run_status": "external_readonly_context_completed",
        "runtime_boundary": "external_agent_readonly_context_completed",
        "external_executor_invoked": True,
        "task_prompt_sent": True,
        "readonly_context_attached": True,
        "task_ref": started.get("task_ref"),
        "worker_type": started.get("worker_type"),
        "worker_id": started.get("worker_id"),
        "context": {
            "context_artifact_uri": context_artifact_uri,
            "sha256": context_sha256,
            "evidence_refs": evidence_refs,
        },
        "artifact": {
            "artifact_uri": f"file://{output_artifact_path}",
            "artifact_type": "json",
            "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
            "size_bytes": len(artifact_bytes),
            "summary": (proc.stdout or proc.stderr).strip()[:HOT_PATH_SUMMARY_MAX_CHARS],
            "evidence_refs": evidence_refs,
        },
        "completed_at": _now(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def append_external_agent_proposal(*, ledger_path: str | Path, run_id: str, proposal_request: dict[str, Any]) -> dict[str, Any]:
    """Validate and append an external agent proposal artifact pointer.

    This records a worker proposal for later certification/human review. It does
    not mutate lifecycle and explicitly refuses done/closed targets.
    """

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    proposal_artifact_uri = str(proposal_request.get("proposal_artifact_uri", "")).strip()
    proposal_sha256 = str(proposal_request.get("proposal_sha256", "")).strip().lower()
    request_evidence_refs = proposal_request.get("evidence_refs", [])
    request_digest = _digest(proposal_request)
    rows = _read_runtime_ledger(path)
    started = next((row for row in rows if row.get("run_id") == normalized_run_id and row.get("run_status") == "started"), None)
    proposal_path: Path | None = None
    proposal: dict[str, Any] = {}
    blocked_reasons: list[str] = []

    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if started is None:
        blocked_reasons.append("started_run_not_found")
    if not proposal_artifact_uri:
        blocked_reasons.append("proposal_artifact_uri_required")
    elif not proposal_artifact_uri.startswith("file://"):
        blocked_reasons.append("proposal_artifact_uri_must_be_file_uri")
    else:
        proposal_path = Path(proposal_artifact_uri.removeprefix("file://"))
        if not proposal_path.exists() or not proposal_path.is_file():
            blocked_reasons.append("proposal_artifact_not_found")
    if not proposal_sha256:
        blocked_reasons.append("proposal_sha256_required")
    elif len(proposal_sha256) != 64:
        blocked_reasons.append("proposal_sha256_invalid")
    if not isinstance(request_evidence_refs, list) or not request_evidence_refs:
        blocked_reasons.append("proposal_request_requires_evidence_refs")
    if sorted(WORKER_FORBIDDEN_RESULT_KEYS.intersection(proposal_request.keys())):
        blocked_reasons.append("proposal_request_contains_forbidden_authority_keys")

    artifact_bytes = b""
    if proposal_path is not None and proposal_path.exists():
        artifact_bytes = proposal_path.read_bytes()
        actual_sha = hashlib.sha256(artifact_bytes).hexdigest()
        if proposal_sha256 and actual_sha != proposal_sha256:
            blocked_reasons.append("proposal_sha256_mismatch")
        try:
            decoded = json.loads(artifact_bytes.decode("utf-8"))
            if isinstance(decoded, dict):
                proposal = decoded
            else:
                blocked_reasons.append("proposal_artifact_must_decode_to_object")
        except json.JSONDecodeError:
            blocked_reasons.append("proposal_artifact_invalid_json")

    claims = proposal.get("claims", []) if proposal else []
    proposal_evidence_refs = proposal.get("evidence_refs", []) if proposal else []
    lifecycle_target = str(proposal.get("lifecycle_target", "")).strip() if proposal else ""
    failure_classification = str(proposal.get("failure_classification", "")).strip() if proposal else ""
    summary = str(proposal.get("summary", "")).strip() if proposal else ""
    unsupported_claims: list[str] = []

    if proposal:
        if proposal.get("proposal_type") != "worker_review_proposal":
            blocked_reasons.append("proposal_type_must_be_worker_review_proposal")
        if proposal.get("run_id") != normalized_run_id:
            blocked_reasons.append("proposal_run_id_mismatch")
        if not summary:
            blocked_reasons.append("proposal_summary_required")
        if not failure_classification:
            blocked_reasons.append("proposal_failure_classification_required")
        if lifecycle_target != "review_proposed":
            blocked_reasons.append("proposal_lifecycle_target_must_be_review_proposed")
        if not isinstance(proposal_evidence_refs, list) or not proposal_evidence_refs:
            blocked_reasons.append("proposal_requires_evidence_refs")
        if not isinstance(claims, list) or not claims:
            blocked_reasons.append("proposal_claims_required")
            claims = []
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict):
                unsupported_claims.append(f"claim:{index}")
                continue
            claim_id = str(claim.get("id") or f"claim:{index}")
            claim_evidence_refs = claim.get("evidence_refs", [])
            if not isinstance(claim_evidence_refs, list) or not claim_evidence_refs:
                unsupported_claims.append(claim_id)
        if unsupported_claims:
            blocked_reasons.append("all_proposal_claims_require_evidence_refs")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "external_agent_proposal",
        "run_id": normalized_run_id,
        "proposal_request_digest": request_digest,
        "proposal_recorded": False,
        "allowed_to_mutate_lifecycle": False,
        "allowed_to_mark_done": False,
        "canonical_lifecycle_unchanged": True,
        "external_adapter_write_through_enabled": False,
        "persistent_learning_write_enabled": False,
        "blocked_reasons": blocked_reasons,
        "unsupported_claims": unsupported_claims,
    }

    if blocked_reasons:
        return {
            **base,
            "accepted_as": "rejected_external_agent_proposal",
            "run_status": "rejected",
            "runtime_boundary": "external_agent_proposal_artifact_rejected",
        }

    assert started is not None
    record = {
        **base,
        "accepted_as": "external_agent_proposal_recorded",
        "run_status": "external_proposal_recorded",
        "runtime_boundary": "external_agent_proposal_artifact_recorded",
        "proposal_recorded": True,
        "task_ref": started.get("task_ref"),
        "worker_type": started.get("worker_type"),
        "worker_id": started.get("worker_id"),
        "claim_count": len(claims),
        "proposal": {
            "proposal_type": proposal.get("proposal_type"),
            "summary": summary[:HOT_PATH_SUMMARY_MAX_CHARS],
            "failure_classification": failure_classification,
            "lifecycle_target": lifecycle_target,
            "evidence_refs": proposal_evidence_refs,
        },
        "artifact": {
            "artifact_uri": proposal_artifact_uri,
            "artifact_type": "json",
            "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
            "size_bytes": len(artifact_bytes),
            "summary": summary[:HOT_PATH_SUMMARY_MAX_CHARS],
            "evidence_refs": request_evidence_refs,
        },
        "recorded_at": _now(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def append_certification_promotion(*, ledger_path: str | Path, run_id: str, certification_request: dict[str, Any]) -> dict[str, Any]:
    """Validate and append a multi-model certification promotion packet.

    This promotes an external proposal only to certified-for-review. It does not
    mark the task done or mutate lifecycle; done still requires a later gate.
    """

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    certification_artifact_uri = str(certification_request.get("certification_artifact_uri", "")).strip()
    certification_sha256 = str(certification_request.get("certification_sha256", "")).strip().lower()
    request_evidence_refs = certification_request.get("evidence_refs", [])
    request_digest = _digest(certification_request)
    rows = _read_runtime_ledger(path)
    started = next((row for row in rows if row.get("run_id") == normalized_run_id and row.get("run_status") == "started"), None)
    proposal_record = next((row for row in rows if row.get("run_id") == normalized_run_id and row.get("run_status") == "external_proposal_recorded"), None)
    certification_path: Path | None = None
    certification: dict[str, Any] = {}
    blocked_reasons: list[str] = []

    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if started is None:
        blocked_reasons.append("started_run_not_found")
    if proposal_record is None:
        blocked_reasons.append("recorded_proposal_required")
    if not certification_artifact_uri:
        blocked_reasons.append("certification_artifact_uri_required")
    elif not certification_artifact_uri.startswith("file://"):
        blocked_reasons.append("certification_artifact_uri_must_be_file_uri")
    else:
        certification_path = Path(certification_artifact_uri.removeprefix("file://"))
        if not certification_path.exists() or not certification_path.is_file():
            blocked_reasons.append("certification_artifact_not_found")
    if not certification_sha256:
        blocked_reasons.append("certification_sha256_required")
    elif len(certification_sha256) != 64:
        blocked_reasons.append("certification_sha256_invalid")
    if not isinstance(request_evidence_refs, list) or not request_evidence_refs:
        blocked_reasons.append("certification_request_requires_evidence_refs")
    if sorted(WORKER_FORBIDDEN_RESULT_KEYS.intersection(certification_request.keys())):
        blocked_reasons.append("certification_request_contains_forbidden_authority_keys")

    artifact_bytes = b""
    if certification_path is not None and certification_path.exists():
        artifact_bytes = certification_path.read_bytes()
        actual_sha = hashlib.sha256(artifact_bytes).hexdigest()
        if certification_sha256 and actual_sha != certification_sha256:
            blocked_reasons.append("certification_sha256_mismatch")
        try:
            decoded = json.loads(artifact_bytes.decode("utf-8"))
            if isinstance(decoded, dict):
                certification = decoded
            else:
                blocked_reasons.append("certification_artifact_must_decode_to_object")
        except json.JSONDecodeError:
            blocked_reasons.append("certification_artifact_invalid_json")

    reviewers = certification.get("reviewers", []) if certification else []
    claims = certification.get("claims", []) if certification else []
    quorum = int(certification.get("quorum", 0) or 0) if certification else 0
    truth_gate = bool(certification.get("truth_gate")) if certification else False
    lifecycle_target = str(certification.get("lifecycle_target", "")).strip() if certification else ""
    summary = str(certification.get("summary", "")).strip() if certification else ""
    certification_evidence_refs = certification.get("evidence_refs", []) if certification else []
    proposal_artifact_uri = str(certification.get("proposal_artifact_uri", "")).strip() if certification else ""
    proposal_sha256 = str(certification.get("proposal_sha256", "")).strip().lower() if certification else ""
    unsupported_claims: list[str] = []
    unsupported_reviewers: list[str] = []

    if certification:
        if certification.get("certification_type") != "multi_model_proposal_certification":
            blocked_reasons.append("certification_type_must_be_multi_model_proposal_certification")
        if certification.get("run_id") != normalized_run_id:
            blocked_reasons.append("certification_run_id_mismatch")
        if proposal_record is not None:
            recorded_artifact = proposal_record.get("artifact", {}) if isinstance(proposal_record.get("artifact"), dict) else {}
            if proposal_artifact_uri != recorded_artifact.get("artifact_uri"):
                blocked_reasons.append("certification_proposal_artifact_uri_mismatch")
            if proposal_sha256 != recorded_artifact.get("sha256"):
                blocked_reasons.append("certification_proposal_sha256_mismatch")
        if not summary:
            blocked_reasons.append("certification_summary_required")
        if lifecycle_target != "certification_passed_for_review":
            blocked_reasons.append("certification_lifecycle_target_must_be_passed_for_review")
        if not isinstance(certification_evidence_refs, list) or not certification_evidence_refs:
            blocked_reasons.append("certification_requires_evidence_refs")
        if not isinstance(reviewers, list) or len(reviewers) < 2:
            blocked_reasons.append("at_least_two_reviewers_required")
            reviewers = [] if not isinstance(reviewers, list) else reviewers
        if quorum < 2:
            blocked_reasons.append("quorum_must_be_at_least_two")
        if quorum > len(reviewers):
            blocked_reasons.append("quorum_exceeds_reviewer_count")
        if not truth_gate:
            blocked_reasons.append("truth_gate_required")
        for index, reviewer in enumerate(reviewers):
            if not isinstance(reviewer, dict):
                unsupported_reviewers.append(f"reviewer:{index}")
                continue
            reviewer_id = str(reviewer.get("id") or f"reviewer:{index}")
            reviewer_evidence_refs = reviewer.get("evidence_refs", [])
            if not isinstance(reviewer_evidence_refs, list) or not reviewer_evidence_refs:
                unsupported_reviewers.append(reviewer_id)
        if unsupported_reviewers:
            blocked_reasons.append("all_reviewers_require_evidence_refs")
        if not isinstance(claims, list) or not claims:
            blocked_reasons.append("certification_claims_required")
            claims = [] if not isinstance(claims, list) else claims
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict):
                unsupported_claims.append(f"claim:{index}")
                continue
            claim_id = str(claim.get("id") or f"claim:{index}")
            claim_evidence_refs = claim.get("evidence_refs", [])
            if not isinstance(claim_evidence_refs, list) or not claim_evidence_refs:
                unsupported_claims.append(claim_id)
        if unsupported_claims:
            blocked_reasons.append("all_certification_claims_require_evidence_refs")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "multi_model_certification_promotion",
        "run_id": normalized_run_id,
        "certification_request_digest": request_digest,
        "certification_recorded": False,
        "allowed_to_mutate_lifecycle": False,
        "allowed_to_mark_done": False,
        "model_consensus_is_fact": False,
        "canonical_lifecycle_unchanged": True,
        "external_adapter_write_through_enabled": False,
        "persistent_learning_write_enabled": False,
        "blocked_reasons": blocked_reasons,
        "unsupported_claims": unsupported_claims,
        "unsupported_reviewers": unsupported_reviewers,
    }
    if blocked_reasons:
        return {
            **base,
            "accepted_as": "rejected_certification_promotion",
            "run_status": "rejected",
            "runtime_boundary": "multi_model_certification_promotion_rejected",
        }

    assert started is not None
    assert proposal_record is not None
    record = {
        **base,
        "accepted_as": "certification_promotion_recorded",
        "run_status": "certification_recorded",
        "runtime_boundary": "multi_model_certification_promotion_recorded",
        "certification_recorded": True,
        "task_ref": started.get("task_ref"),
        "worker_type": started.get("worker_type"),
        "worker_id": started.get("worker_id"),
        "reviewer_count": len(reviewers),
        "quorum": quorum,
        "truth_gate": truth_gate,
        "claim_count": len(claims),
        "proposal_artifact": {
            "artifact_uri": proposal_artifact_uri,
            "sha256": proposal_sha256,
        },
        "certification": {
            "certification_type": certification.get("certification_type"),
            "summary": summary[:HOT_PATH_SUMMARY_MAX_CHARS],
            "lifecycle_target": lifecycle_target,
            "evidence_refs": certification_evidence_refs,
        },
        "artifact": {
            "artifact_uri": certification_artifact_uri,
            "artifact_type": "json",
            "sha256": hashlib.sha256(artifact_bytes).hexdigest(),
            "size_bytes": len(artifact_bytes),
            "summary": summary[:HOT_PATH_SUMMARY_MAX_CHARS],
            "evidence_refs": request_evidence_refs,
        },
        "recorded_at": _now(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def request_done_authorization(*, ledger_path: str | Path, run_id: str, authorization_request: dict[str, Any]) -> dict[str, Any]:
    """Project a human/elevated done authorization without mutating facts yet."""

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    rows = _read_runtime_ledger(path)
    certification_record = next((row for row in rows if row.get("run_id") == normalized_run_id and row.get("run_status") == "certification_recorded"), None)
    actor_role = str(authorization_request.get("actor_role", "")).strip()
    actor_id = str(authorization_request.get("actor_id", "")).strip()
    decision = str(authorization_request.get("decision", "")).strip().lower()
    target_status = str(authorization_request.get("target_status", "")).strip().lower()
    certification_artifact_uri = str(authorization_request.get("certification_artifact_uri", "")).strip()
    certification_sha256 = str(authorization_request.get("certification_sha256", "")).strip().lower()
    expires_at = str(authorization_request.get("expires_at", "")).strip()
    evidence_refs = authorization_request.get("evidence_refs", [])
    request_digest = _digest(authorization_request)
    blocked_reasons: list[str] = []
    expires_dt: datetime | None = None

    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if certification_record is None:
        blocked_reasons.append("certification_record_required")
    if not actor_id:
        blocked_reasons.append("actor_id_required")
    if actor_role not in ELEVATED_DONE_AUTH_ROLES:
        blocked_reasons.append("elevated_actor_role_required")
    if decision != "approved":
        blocked_reasons.append("authorization_decision_must_be_approved")
    if target_status != "done":
        blocked_reasons.append("authorization_target_status_must_be_done")
    if authorization_request.get("write_through_allowed") is True or authorization_request.get("fact_mutation_enabled") is True:
        blocked_reasons.append("write_through_must_remain_disabled")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("authorization_requires_evidence_refs")
    if not expires_at:
        blocked_reasons.append("authorization_expires_at_required")
    else:
        try:
            expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            if expires_dt <= datetime.now(timezone.utc):
                blocked_reasons.append("authorization_expired")
        except ValueError:
            blocked_reasons.append("authorization_expires_at_invalid")
    if certification_record is not None:
        recorded_artifact = certification_record.get("artifact", {}) if isinstance(certification_record.get("artifact"), dict) else {}
        if certification_artifact_uri != recorded_artifact.get("artifact_uri"):
            blocked_reasons.append("authorization_certification_artifact_uri_mismatch")
        if certification_sha256 != recorded_artifact.get("sha256"):
            blocked_reasons.append("authorization_certification_sha256_mismatch")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "done_authorization_projection",
        "run_id": normalized_run_id,
        "authorization_projected": False,
        "authorization_request_digest": request_digest,
        "target_status": target_status,
        "write_through_allowed": False,
        "fact_mutation_enabled": False,
        "allowed_to_mutate_lifecycle": False,
        "canonical_lifecycle_unchanged": True,
        "external_adapter_write_through_enabled": False,
        "blocked_reasons": blocked_reasons,
    }
    if blocked_reasons:
        return {
            **base,
            "accepted_as": "rejected_done_authorization_projection",
            "run_status": "rejected",
            "runtime_boundary": "human_elevated_done_authorization_rejected",
        }

    assert certification_record is not None
    authorization_id = "auth_" + _digest({"run_id": normalized_run_id, "request_digest": request_digest})[:24]
    record = {
        **base,
        "accepted_as": "done_authorization_projected",
        "run_status": "done_authorization_approved",
        "runtime_boundary": "human_elevated_done_authorization_projected",
        "authorization_projected": True,
        "authorization_id": authorization_id,
        "actor_id": actor_id,
        "actor_role": actor_role,
        "decision": decision,
        "expires_at": expires_at,
        "certification_artifact": {
            "artifact_uri": certification_artifact_uri,
            "sha256": certification_sha256,
        },
        "evidence_refs": evidence_refs,
        "recorded_at": _now(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def execute_ledger_fact_mutation(*, ledger_path: str | Path, run_id: str, fact_request: dict[str, Any]) -> dict[str, Any]:
    """Consume an approved done authorization and append a canonical ledger-local fact."""

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    rows = _read_runtime_ledger(path)
    authorization_id = str(fact_request.get("authorization_id", "")).strip()
    target_status = str(fact_request.get("target_status", "")).strip().lower()
    certification_artifact_uri = str(fact_request.get("certification_artifact_uri", "")).strip()
    certification_sha256 = str(fact_request.get("certification_sha256", "")).strip().lower()
    evidence_refs = fact_request.get("evidence_refs", [])
    request_digest = _digest(fact_request)
    authorization_record = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "done_authorization_approved"
            and row.get("authorization_id") == authorization_id
        ),
        None,
    )
    certification_record = next((row for row in rows if row.get("run_id") == normalized_run_id and row.get("run_status") == "certification_recorded"), None)
    existing_fact = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "fact_mutated"
            and row.get("authorization_id") == authorization_id
            and row.get("canonical_lifecycle_status") == target_status
        ),
        None,
    )
    if existing_fact is not None:
        replay = dict(existing_fact)
        replay["idempotent_replay"] = True
        return replay

    blocked_reasons: list[str] = []
    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if not authorization_id:
        blocked_reasons.append("authorization_id_required")
    if authorization_record is None:
        blocked_reasons.append("approved_authorization_required")
    if certification_record is None:
        blocked_reasons.append("certification_record_required")
    if target_status != "done":
        blocked_reasons.append("fact_target_status_must_be_done")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("fact_mutation_requires_evidence_refs")
    if sorted(WORKER_FORBIDDEN_RESULT_KEYS.intersection(fact_request.keys())):
        blocked_reasons.append("fact_request_contains_forbidden_authority_keys")
    if authorization_record is not None:
        if authorization_record.get("target_status") != target_status:
            blocked_reasons.append("fact_authorization_target_status_mismatch")
        try:
            expires_dt = datetime.fromisoformat(str(authorization_record.get("expires_at", "")).replace("Z", "+00:00"))
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            if expires_dt <= datetime.now(timezone.utc):
                blocked_reasons.append("authorization_expired")
        except ValueError:
            blocked_reasons.append("authorization_expires_at_invalid")
        auth_cert = authorization_record.get("certification_artifact", {}) if isinstance(authorization_record.get("certification_artifact"), dict) else {}
        if certification_artifact_uri != auth_cert.get("artifact_uri"):
            blocked_reasons.append("fact_certification_artifact_uri_mismatch")
        if certification_sha256 != auth_cert.get("sha256"):
            blocked_reasons.append("fact_certification_sha256_mismatch")
    elif certification_record is not None:
        recorded_artifact = certification_record.get("artifact", {}) if isinstance(certification_record.get("artifact"), dict) else {}
        if certification_artifact_uri != recorded_artifact.get("artifact_uri"):
            blocked_reasons.append("fact_certification_artifact_uri_mismatch")
        if certification_sha256 != recorded_artifact.get("sha256"):
            blocked_reasons.append("fact_certification_sha256_mismatch")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "ledger_local_fact_mutation",
        "run_id": normalized_run_id,
        "authorization_id": authorization_id,
        "fact_is_canonical": False,
        "canonical_lifecycle_status": target_status,
        "fact_request_digest": request_digest,
        "allowed_to_mutate_lifecycle": False,
        "canonical_lifecycle_unchanged": True,
        "external_adapter_write_through_enabled": False,
        "blocked_reasons": blocked_reasons,
        "idempotent_replay": False,
    }
    if blocked_reasons:
        return {
            **base,
            "accepted_as": "rejected_canonical_lifecycle_fact_mutation",
            "run_status": "rejected",
            "runtime_boundary": "ledger_local_canonical_fact_mutation_rejected",
        }

    assert authorization_record is not None
    record = {
        **base,
        "accepted_as": "canonical_lifecycle_fact_mutated",
        "run_status": "fact_mutated",
        "runtime_boundary": "ledger_local_canonical_fact_mutation",
        "fact_is_canonical": True,
        "allowed_to_mutate_lifecycle": True,
        "canonical_lifecycle_unchanged": False,
        "target_status": target_status,
        "certification_artifact": {
            "artifact_uri": certification_artifact_uri,
            "sha256": certification_sha256,
        },
        "evidence_refs": evidence_refs,
        "mutated_at": _now(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def request_external_adapter_write_intent(*, ledger_path: str | Path, run_id: str, intent_request: dict[str, Any]) -> dict[str, Any]:
    """Project an external adapter write intent without performing write-through."""

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    rows = _read_runtime_ledger(path)
    adapter = str(intent_request.get("adapter", "")).strip().lower()
    target_ref = str(intent_request.get("target_ref", "")).strip()
    target_status = str(intent_request.get("target_status", "")).strip().lower()
    canonical_fact_digest = str(intent_request.get("canonical_fact_digest", "")).strip()
    readiness_check = intent_request.get("readiness_check")
    readback_plan = intent_request.get("readback_plan")
    rollback_plan = intent_request.get("rollback_plan")
    evidence_refs = intent_request.get("evidence_refs", [])
    request_digest = _digest(intent_request)
    done_fact = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "fact_mutated"
            and row.get("fact_is_canonical") is True
            and row.get("canonical_lifecycle_status") == "done"
            and row.get("fact_request_digest") == canonical_fact_digest
        ),
        None,
    )
    blocked_reasons: list[str] = []
    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if done_fact is None:
        blocked_reasons.append("canonical_done_fact_required")
    if adapter not in ALLOWED_EXTERNAL_WRITE_INTENT_ADAPTERS:
        blocked_reasons.append("adapter_not_allowlisted")
    if not target_ref:
        blocked_reasons.append("target_ref_required")
    if target_status != "done":
        blocked_reasons.append("target_status_must_be_done")
    if not canonical_fact_digest:
        blocked_reasons.append("canonical_fact_digest_required")
    if not isinstance(readiness_check, dict) or not readiness_check:
        blocked_reasons.append("readiness_check_required")
    if not isinstance(readback_plan, dict) or not readback_plan:
        blocked_reasons.append("readback_plan_required")
    if not isinstance(rollback_plan, dict) or not rollback_plan:
        blocked_reasons.append("rollback_plan_required")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("write_intent_requires_evidence_refs")
    forbidden_keys = sorted((WORKER_FORBIDDEN_RESULT_KEYS | {"write_through_enabled", "write_through_allowed", "external_adapter_write_through_enabled"}).intersection(intent_request.keys()))
    if forbidden_keys:
        blocked_reasons.append("write_intent_contains_forbidden_authority_keys")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "external_adapter_write_intent_projection",
        "run_id": normalized_run_id,
        "adapter": adapter,
        "target_ref": target_ref,
        "target_status": target_status,
        "canonical_fact_required": True,
        "canonical_fact_digest": canonical_fact_digest,
        "readiness_required": True,
        "readback_required": True,
        "rollback_required": True,
        "write_through_allowed": False,
        "external_adapter_write_through_enabled": False,
        "request_digest": request_digest,
        "blocked_reasons": blocked_reasons,
    }
    if blocked_reasons:
        return {
            **base,
            "accepted_as": "rejected_external_adapter_write_intent",
            "run_status": "rejected",
            "runtime_boundary": "external_adapter_write_intent_rejected",
        }

    intent_id = "write_intent_" + _digest({"run_id": normalized_run_id, "request_digest": request_digest})[:24]
    record = {
        **base,
        "accepted_as": "external_adapter_write_intent_projected",
        "run_status": "external_adapter_write_intent_recorded",
        "runtime_boundary": "external_adapter_write_intent_projection",
        "intent_id": intent_id,
        "readiness_check": readiness_check,
        "readback_plan": readback_plan,
        "rollback_plan": rollback_plan,
        "evidence_refs": evidence_refs,
        "recorded_at": _now(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def request_external_adapter_write_authorization(*, ledger_path: str | Path, run_id: str, authorization_request: dict[str, Any]) -> dict[str, Any]:
    """Authorize a recorded adapter write intent without executing the external write."""

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    rows = _read_runtime_ledger(path)
    intent_id = str(authorization_request.get("intent_id", "")).strip()
    actor_id = str(authorization_request.get("actor_id", "")).strip()
    actor_role = str(authorization_request.get("actor_role", "")).strip()
    decision = str(authorization_request.get("decision", "")).strip().lower()
    adapter = str(authorization_request.get("adapter", "")).strip().lower()
    target_ref = str(authorization_request.get("target_ref", "")).strip()
    target_status = str(authorization_request.get("target_status", "")).strip().lower()
    canonical_fact_digest = str(authorization_request.get("canonical_fact_digest", "")).strip()
    intent_request_digest = str(authorization_request.get("intent_request_digest", "")).strip()
    expires_at = str(authorization_request.get("expires_at", "")).strip()
    evidence_refs = authorization_request.get("evidence_refs", [])
    request_digest = _digest(authorization_request)
    intent_record = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "external_adapter_write_intent_recorded"
            and row.get("intent_id") == intent_id
        ),
        None,
    )
    blocked_reasons: list[str] = []
    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if intent_record is None:
        blocked_reasons.append("write_intent_required")
    if not actor_id:
        blocked_reasons.append("actor_id_required")
    if actor_role not in ELEVATED_DONE_AUTH_ROLES:
        blocked_reasons.append("elevated_actor_required")
    if decision != "approved":
        blocked_reasons.append("approval_decision_required")
    if target_status != "done":
        blocked_reasons.append("target_status_must_be_done")
    if not expires_at:
        blocked_reasons.append("expires_at_required")
    else:
        try:
            expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            if expires_dt <= datetime.now(timezone.utc):
                blocked_reasons.append("authorization_expired")
        except ValueError:
            blocked_reasons.append("authorization_expires_at_invalid")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("write_authorization_requires_evidence_refs")
    forbidden_keys = sorted((WORKER_FORBIDDEN_RESULT_KEYS | {"write_through_enabled", "write_through_allowed", "external_adapter_write_through_enabled"}).intersection(authorization_request.keys()))
    if forbidden_keys:
        blocked_reasons.append("write_authorization_contains_forbidden_authority_keys")
    if intent_record is not None:
        if adapter != intent_record.get("adapter"):
            blocked_reasons.append("authorization_adapter_mismatch")
        if target_ref != intent_record.get("target_ref"):
            blocked_reasons.append("authorization_target_ref_mismatch")
        if target_status != intent_record.get("target_status"):
            blocked_reasons.append("authorization_target_status_mismatch")
        if canonical_fact_digest != intent_record.get("canonical_fact_digest"):
            blocked_reasons.append("authorization_canonical_fact_digest_mismatch")
        if intent_request_digest != intent_record.get("request_digest"):
            blocked_reasons.append("authorization_intent_request_digest_mismatch")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "external_adapter_write_authorization_projection",
        "run_id": normalized_run_id,
        "intent_id": intent_id,
        "actor_id": actor_id,
        "actor_role": actor_role,
        "decision": decision,
        "adapter": adapter,
        "target_ref": target_ref,
        "target_status": target_status,
        "canonical_fact_digest": canonical_fact_digest,
        "intent_request_digest": intent_request_digest,
        "expires_at": expires_at,
        "write_authorized": False,
        "write_executed": False,
        "readback_required": True,
        "rollback_required": True,
        "external_adapter_write_through_enabled": False,
        "request_digest": request_digest,
        "blocked_reasons": blocked_reasons,
    }
    if blocked_reasons:
        return {
            **base,
            "accepted_as": "rejected_external_adapter_write_authorization",
            "run_status": "rejected",
            "runtime_boundary": "external_adapter_write_authorization_rejected",
        }

    authorization_id = "adapter_auth_" + _digest({"run_id": normalized_run_id, "request_digest": request_digest})[:24]
    record = {
        **base,
        "accepted_as": "external_adapter_write_authorization_projected",
        "run_status": "external_adapter_write_authorization_approved",
        "runtime_boundary": "external_adapter_write_authorization_projection",
        "authorization_id": authorization_id,
        "write_authorized": True,
        "evidence_refs": evidence_refs,
        "recorded_at": _now(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def execute_external_adapter_local_file_write(*, ledger_path: str | Path, run_id: str, execution_request: dict[str, Any]) -> dict[str, Any]:
    """Execute a narrow local-file adapter write-through and read it back."""

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    rows = _read_runtime_ledger(path)
    authorization_id = str(execution_request.get("authorization_id", "")).strip()
    intent_id = str(execution_request.get("intent_id", "")).strip()
    adapter = str(execution_request.get("adapter", "")).strip().lower()
    target_ref = str(execution_request.get("target_ref", "")).strip()
    target_status = str(execution_request.get("target_status", "")).strip().lower()
    adapter_state_path_raw = str(execution_request.get("adapter_state_path", "")).strip()
    expected_previous_status = str(execution_request.get("expected_previous_status", "")).strip().lower()
    evidence_refs = execution_request.get("evidence_refs", [])
    request_digest = _digest(execution_request)
    auth_record = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "external_adapter_write_authorization_approved"
            and row.get("authorization_id") == authorization_id
            and row.get("intent_id") == intent_id
        ),
        None,
    )
    existing = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "external_adapter_write_verified"
            and row.get("authorization_id") == authorization_id
            and row.get("intent_id") == intent_id
            and row.get("adapter") == adapter
            and row.get("target_ref") == target_ref
            and row.get("target_status") == target_status
        ),
        None,
    )
    if existing is not None:
        return {**existing, "idempotent_replay": True}

    blocked_reasons: list[str] = []
    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if auth_record is None:
        blocked_reasons.append("write_authorization_required")
    if adapter not in ALLOWED_EXTERNAL_WRITE_INTENT_ADAPTERS:
        blocked_reasons.append("adapter_not_allowlisted")
    if target_status != "done":
        blocked_reasons.append("target_status_must_be_done")
    if not adapter_state_path_raw:
        blocked_reasons.append("adapter_state_path_required")
    adapter_state_path = Path(adapter_state_path_raw) if adapter_state_path_raw else None
    if adapter_state_path is not None and not adapter_state_path.exists():
        blocked_reasons.append("adapter_state_path_missing")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("write_execution_requires_evidence_refs")
    if auth_record is not None:
        if adapter != auth_record.get("adapter"):
            blocked_reasons.append("execution_adapter_mismatch")
        if target_ref != auth_record.get("target_ref"):
            blocked_reasons.append("execution_target_ref_mismatch")
        if target_status != auth_record.get("target_status"):
            blocked_reasons.append("execution_target_status_mismatch")
        expires_at = str(auth_record.get("expires_at", ""))
        try:
            expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            if expires_dt <= datetime.now(timezone.utc):
                blocked_reasons.append("write_authorization_expired")
        except ValueError:
            blocked_reasons.append("write_authorization_expires_at_invalid")

    state: dict[str, Any] | None = None
    previous_status = ""
    if adapter_state_path is not None and adapter_state_path.exists():
        try:
            loaded = json.loads(adapter_state_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                state = loaded
            else:
                blocked_reasons.append("adapter_state_must_be_object")
        except json.JSONDecodeError:
            blocked_reasons.append("adapter_state_json_invalid")
    if state is not None:
        tasks = state.get("tasks")
        if not isinstance(tasks, dict):
            blocked_reasons.append("adapter_state_tasks_object_required")
        elif target_ref not in tasks or not isinstance(tasks.get(target_ref), dict):
            blocked_reasons.append("adapter_state_target_ref_missing")
        else:
            previous_status = str(tasks[target_ref].get("status", "")).strip().lower()
            if expected_previous_status and previous_status != expected_previous_status:
                blocked_reasons.append("adapter_state_previous_status_mismatch")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "external_adapter_local_file_write_through",
        "run_id": normalized_run_id,
        "authorization_id": authorization_id,
        "intent_id": intent_id,
        "adapter": adapter,
        "target_ref": target_ref,
        "target_status": target_status,
        "adapter_state_path": adapter_state_path_raw,
        "previous_status": previous_status,
        "write_executed": False,
        "readback_verified": False,
        "rollback_available": False,
        "external_adapter_write_through_enabled": False,
        "request_digest": request_digest,
        "blocked_reasons": blocked_reasons,
    }
    if blocked_reasons:
        return {
            **base,
            "accepted_as": "rejected_external_adapter_write_execution",
            "run_status": "rejected",
            "runtime_boundary": "external_adapter_write_execution_rejected",
        }

    assert adapter_state_path is not None
    assert state is not None
    tasks = state["tasks"]
    tasks[target_ref]["status"] = target_status
    tasks[target_ref]["updated_by"] = "hermes_event_governed_agent_executor"
    tasks[target_ref]["updated_at"] = _now()
    adapter_state_path.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    readback_state = json.loads(adapter_state_path.read_text(encoding="utf-8"))
    readback_status = str(readback_state["tasks"][target_ref].get("status", "")).strip().lower()
    readback_verified = readback_status == target_status
    record = {
        **base,
        "accepted_as": "external_adapter_write_verified" if readback_verified else "external_adapter_write_mismatch",
        "run_status": "external_adapter_write_verified" if readback_verified else "external_adapter_write_mismatch",
        "runtime_boundary": "external_adapter_local_file_write_through",
        "write_executed": True,
        "readback_status": readback_status,
        "readback_verified": readback_verified,
        "rollback_available": True,
        "external_adapter_write_through_enabled": True,
        "evidence_refs": evidence_refs,
        "recorded_at": _now(),
        "idempotent_replay": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def _extract_frontmatter_status(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith("status:"):
            return line.split(":", 1)[1].strip().strip('"\'').lower()
    return ""


def _replace_frontmatter_status(text: str, target_status: str) -> str:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("frontmatter_required")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            raise ValueError("frontmatter_status_required")
        if lines[index].startswith("status:"):
            newline = "\n" if lines[index].endswith("\n") else ""
            lines[index] = f"status: {target_status}{newline}"
            return "".join(lines)
    raise ValueError("frontmatter_status_required")


def _has_symlink_in_taskpool_chain(tasks_dir: Path) -> bool:
    check_paths = [tasks_dir, tasks_dir.parent, tasks_dir.parent.parent]
    return any(path.is_symlink() for path in check_paths)


def _safe_taskpool_note_path(tasks_dir: Path, target_ref: str) -> tuple[Path | None, list[str]]:
    errors: list[str] = []
    if not target_ref or target_ref in {".", ".."} or "/" in target_ref or "\\" in target_ref or target_ref.endswith(".md"):
        errors.append("target_ref_unsafe")
        return None, errors
    if _has_symlink_in_taskpool_chain(tasks_dir):
        errors.append("taskpool_path_chain_contains_symlink")
    note_path = tasks_dir / f"{target_ref}.md"
    if note_path.parent != tasks_dir:
        errors.append("taskpool_note_parent_mismatch")
    if note_path.is_symlink():
        errors.append("taskpool_note_is_symlink")
    if not note_path.exists():
        errors.append("taskpool_note_missing")
    elif not note_path.is_file():
        errors.append("taskpool_note_not_regular_file")
    try:
        if tasks_dir.exists() and note_path.exists() and note_path.resolve().parent != tasks_dir.resolve():
            errors.append("taskpool_note_resolves_outside_tasks_dir")
    except OSError:
        errors.append("taskpool_note_resolution_failed")
    return note_path if not errors else None, errors


def execute_taskpool_note_closeout_write(*, ledger_path: str | Path, run_id: str, execution_request: dict[str, Any]) -> dict[str, Any]:
    """Execute a digest-bound taskpool note closeout by mutating only frontmatter status."""

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    rows = _read_runtime_ledger(path)
    authorization_id = str(execution_request.get("authorization_id", "")).strip()
    intent_id = str(execution_request.get("intent_id", "")).strip()
    adapter = str(execution_request.get("adapter", "")).strip().lower()
    target_ref = str(execution_request.get("target_ref", "")).strip()
    target_status = str(execution_request.get("target_status", "")).strip().lower()
    tasks_dir_raw = str(execution_request.get("taskpool_tasks_dir", "")).strip()
    expected_previous_status = str(execution_request.get("expected_previous_status", "")).strip().lower()
    evidence_refs = execution_request.get("evidence_refs", [])
    request_digest = _digest(execution_request)
    auth_record = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "external_adapter_write_authorization_approved"
            and row.get("authorization_id") == authorization_id
            and row.get("intent_id") == intent_id
        ),
        None,
    )
    existing = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "taskpool_note_write_verified"
            and row.get("authorization_id") == authorization_id
            and row.get("intent_id") == intent_id
            and row.get("target_ref") == target_ref
            and row.get("target_status") == target_status
        ),
        None,
    )
    if existing is not None:
        return {**existing, "idempotent_replay": True}

    blocked_reasons: list[str] = []
    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if auth_record is None:
        blocked_reasons.append("write_authorization_required")
    if adapter != "taskpool":
        blocked_reasons.append("adapter_must_be_taskpool")
    if target_status != "done":
        blocked_reasons.append("target_status_must_be_done")
    if not target_ref or target_ref in {".", ".."} or "/" in target_ref or "\\" in target_ref or target_ref.endswith(".md"):
        blocked_reasons.append("target_ref_unsafe")
    if not tasks_dir_raw:
        blocked_reasons.append("taskpool_tasks_dir_required")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("taskpool_write_requires_evidence_refs")
    if auth_record is not None:
        if adapter != auth_record.get("adapter"):
            blocked_reasons.append("execution_adapter_mismatch")
        if target_ref != auth_record.get("target_ref"):
            blocked_reasons.append("execution_target_ref_mismatch")
        if target_status != auth_record.get("target_status"):
            blocked_reasons.append("execution_target_status_mismatch")
        expires_at = str(auth_record.get("expires_at", ""))
        try:
            expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            if expires_dt <= datetime.now(timezone.utc):
                blocked_reasons.append("write_authorization_expired")
        except ValueError:
            blocked_reasons.append("write_authorization_expires_at_invalid")

    tasks_dir = Path(tasks_dir_raw) if tasks_dir_raw else None
    note_path: Path | None = None
    previous_status = ""
    if tasks_dir is not None:
        if not tasks_dir.exists() or not tasks_dir.is_dir():
            blocked_reasons.append("taskpool_tasks_dir_missing")
        elif _has_symlink_in_taskpool_chain(tasks_dir):
            blocked_reasons.append("taskpool_tasks_dir_symlink_chain")
        elif "target_ref_unsafe" not in blocked_reasons:
            note_path = tasks_dir / f"{target_ref}.md"
            if note_path.parent != tasks_dir:
                blocked_reasons.append("taskpool_note_parent_escape")
            elif note_path.is_symlink():
                blocked_reasons.append("taskpool_note_is_symlink")
            elif not note_path.exists():
                blocked_reasons.append("taskpool_note_missing")
            elif not note_path.is_file():
                blocked_reasons.append("taskpool_note_not_regular_file")
            else:
                try:
                    if note_path.resolve().parent != tasks_dir.resolve():
                        blocked_reasons.append("taskpool_note_resolve_escape")
                except FileNotFoundError:
                    blocked_reasons.append("taskpool_note_missing")
                if not blocked_reasons:
                    text = note_path.read_text(encoding="utf-8")
                    previous_status = _extract_frontmatter_status(text)
                    if not previous_status:
                        blocked_reasons.append("frontmatter_status_required")
                    elif expected_previous_status and previous_status != expected_previous_status:
                        blocked_reasons.append("taskpool_note_previous_status_mismatch")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "taskpool_note_closeout_write_through",
        "run_id": normalized_run_id,
        "authorization_id": authorization_id,
        "intent_id": intent_id,
        "adapter": adapter,
        "target_ref": target_ref,
        "target_status": target_status,
        "taskpool_tasks_dir": tasks_dir_raw,
        "previous_status": previous_status,
        "write_executed": False,
        "readback_verified": False,
        "rollback_available": False,
        "external_adapter_write_through_enabled": False,
        "request_digest": request_digest,
        "blocked_reasons": blocked_reasons,
    }
    if blocked_reasons:
        return {
            **base,
            "accepted_as": "rejected_taskpool_note_write_execution",
            "run_status": "rejected",
            "runtime_boundary": "taskpool_note_write_execution_rejected",
        }

    assert note_path is not None
    original_text = note_path.read_text(encoding="utf-8")
    updated_text = _replace_frontmatter_status(original_text, target_status)
    audit_marker = f"\n<!-- hermes_taskpool_write_through authorization_id={authorization_id} previous_status={previous_status} target_status={target_status} recorded_at={_now()} -->\n"
    note_path.write_text(updated_text.rstrip("\n") + audit_marker, encoding="utf-8")
    readback_text = note_path.read_text(encoding="utf-8")
    readback_status = _extract_frontmatter_status(readback_text)
    readback_verified = readback_status == target_status
    record = {
        **base,
        "accepted_as": "taskpool_note_write_verified" if readback_verified else "taskpool_note_write_mismatch",
        "run_status": "taskpool_note_write_verified" if readback_verified else "taskpool_note_write_mismatch",
        "runtime_boundary": "taskpool_note_closeout_write_through",
        "write_executed": True,
        "readback_status": readback_status,
        "readback_verified": readback_verified,
        "rollback_available": True,
        "external_adapter_write_through_enabled": True,
        "evidence_refs": evidence_refs,
        "recorded_at": _now(),
        "idempotent_replay": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def execute_kanban_db_closeout_write(*, ledger_path: str | Path, run_id: str, execution_request: dict[str, Any]) -> dict[str, Any]:
    """Execute a digest-bound Kanban DB closeout by mutating only tasks.status."""

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    rows = _read_runtime_ledger(path)
    authorization_id = str(execution_request.get("authorization_id", "")).strip()
    intent_id = str(execution_request.get("intent_id", "")).strip()
    adapter = str(execution_request.get("adapter", "")).strip().lower()
    target_ref = str(execution_request.get("target_ref", "")).strip()
    target_status = str(execution_request.get("target_status", "")).strip().lower()
    db_path_raw = str(execution_request.get("kanban_db_path", "")).strip()
    expected_previous_status = str(execution_request.get("expected_previous_status", "")).strip().lower()
    evidence_refs = execution_request.get("evidence_refs", [])
    request_digest = _digest(execution_request)
    auth_record = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "external_adapter_write_authorization_approved"
            and row.get("authorization_id") == authorization_id
            and row.get("intent_id") == intent_id
        ),
        None,
    )
    existing = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "kanban_db_write_verified"
            and row.get("authorization_id") == authorization_id
            and row.get("intent_id") == intent_id
            and row.get("target_ref") == target_ref
            and row.get("target_status") == target_status
        ),
        None,
    )
    if existing is not None:
        return {**existing, "idempotent_replay": True}

    blocked_reasons: list[str] = []
    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if auth_record is None:
        blocked_reasons.append("write_authorization_required")
    if adapter != "kanban":
        blocked_reasons.append("adapter_must_be_kanban")
    if target_status != "done":
        blocked_reasons.append("target_status_must_be_done")
    if not target_ref:
        blocked_reasons.append("target_ref_required")
    if not db_path_raw:
        blocked_reasons.append("kanban_db_path_required")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("kanban_write_requires_evidence_refs")
    if auth_record is not None:
        if adapter != auth_record.get("adapter"):
            blocked_reasons.append("execution_adapter_mismatch")
        if target_ref != auth_record.get("target_ref"):
            blocked_reasons.append("execution_target_ref_mismatch")
        if target_status != auth_record.get("target_status"):
            blocked_reasons.append("execution_target_status_mismatch")
        expires_at = str(auth_record.get("expires_at", ""))
        try:
            expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            if expires_dt <= datetime.now(timezone.utc):
                blocked_reasons.append("write_authorization_expired")
        except ValueError:
            blocked_reasons.append("write_authorization_expires_at_invalid")

    db_path = Path(db_path_raw) if db_path_raw else None
    previous_status = ""
    if db_path is not None:
        if db_path.is_symlink():
            blocked_reasons.append("kanban_db_is_symlink")
        elif not db_path.exists():
            blocked_reasons.append("kanban_db_missing")
        elif not db_path.is_file():
            blocked_reasons.append("kanban_db_not_regular_file")
        if not blocked_reasons:
            try:
                with sqlite3.connect(db_path) as conn:
                    table = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone()
                    if table is None:
                        blocked_reasons.append("kanban_tasks_table_missing")
                    else:
                        row = conn.execute("SELECT status FROM tasks WHERE id=?", (target_ref,)).fetchone()
                        if row is None:
                            blocked_reasons.append("kanban_task_missing")
                        else:
                            previous_status = str(row[0] or "").strip().lower()
                            if expected_previous_status and previous_status != expected_previous_status:
                                blocked_reasons.append("kanban_previous_status_mismatch")
            except sqlite3.Error:
                blocked_reasons.append("kanban_db_unreadable")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "kanban_db_closeout_write_through",
        "run_id": normalized_run_id,
        "authorization_id": authorization_id,
        "intent_id": intent_id,
        "adapter": adapter,
        "target_ref": target_ref,
        "target_status": target_status,
        "kanban_db_path": db_path_raw,
        "previous_status": previous_status,
        "write_executed": False,
        "readback_verified": False,
        "rollback_available": False,
        "external_adapter_write_through_enabled": False,
        "request_digest": request_digest,
        "blocked_reasons": blocked_reasons,
    }
    if blocked_reasons:
        return {
            **base,
            "accepted_as": "rejected_kanban_db_write_execution",
            "run_status": "rejected",
            "runtime_boundary": "kanban_db_write_execution_rejected",
        }

    assert db_path is not None
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE tasks SET status=? WHERE id=?", (target_status, target_ref))
        conn.commit()
        readback = conn.execute("SELECT status FROM tasks WHERE id=?", (target_ref,)).fetchone()
    readback_status = str(readback[0] if readback else "").strip().lower()
    readback_verified = readback_status == target_status
    record = {
        **base,
        "accepted_as": "kanban_db_write_verified" if readback_verified else "kanban_db_write_mismatch",
        "run_status": "kanban_db_write_verified" if readback_verified else "kanban_db_write_mismatch",
        "runtime_boundary": "kanban_db_closeout_write_through",
        "write_executed": True,
        "readback_status": readback_status,
        "readback_verified": readback_verified,
        "rollback_available": True,
        "external_adapter_write_through_enabled": True,
        "evidence_refs": evidence_refs,
        "recorded_at": _now(),
        "idempotent_replay": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def _contains_secret_like_key(value: Any) -> bool:
    secret_markers = ("token", "password", "secret", "api_key", "apikey", "authorization", "credential")
    if isinstance(value, dict):
        for key, item in value.items():
            if any(marker in str(key).lower() for marker in secret_markers):
                return True
            if _contains_secret_like_key(item):
                return True
    if isinstance(value, list):
        return any(_contains_secret_like_key(item) for item in value)
    return False


def _compact_payload_summary(payload: Any) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("text"), str):
        summary = payload["text"]
    else:
        summary = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return summary[:240]


def execute_outbound_outbox_enqueue(*, ledger_path: str | Path, run_id: str, outbox_request: dict[str, Any]) -> dict[str, Any]:
    """Create a pending outbound item for Lark/Telegram without sending it."""

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    rows = _read_runtime_ledger(path)
    source_run_status = str(outbox_request.get("source_run_status", "")).strip()
    source_request_digest = str(outbox_request.get("source_request_digest", "")).strip()
    channels_raw = outbox_request.get("channels", [])
    channels = [str(channel).strip().lower() for channel in channels_raw] if isinstance(channels_raw, list) else []
    target_id = str(outbox_request.get("target_id", "")).strip()
    target_allowlist_raw = outbox_request.get("target_allowlist", [])
    target_allowlist = [str(item).strip() for item in target_allowlist_raw] if isinstance(target_allowlist_raw, list) else []
    outbox_path_raw = str(outbox_request.get("outbox_path", "")).strip()
    payload = outbox_request.get("payload", {})
    evidence_refs = outbox_request.get("evidence_refs", [])
    request_digest = _digest(outbox_request)
    payload_hash = _digest(payload)
    allowed_source_statuses = {
        "taskpool_note_write_verified",
        "kanban_db_write_verified",
        "external_adapter_rollback_verified",
    }
    source_event = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == source_run_status
            and row.get("request_digest") == source_request_digest
        ),
        None,
    )
    existing = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "outbound_outbox_item_enqueued"
            and row.get("source_request_digest") == source_request_digest
            and row.get("target_id") == target_id
            and row.get("payload_hash") == payload_hash
        ),
        None,
    )
    if existing is not None:
        return {**existing, "idempotent_replay": True}

    blocked_reasons: list[str] = []
    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if source_run_status not in allowed_source_statuses:
        blocked_reasons.append("source_status_not_outbox_eligible")
    if source_event is None:
        blocked_reasons.append("verified_source_event_required")
    if not channels:
        blocked_reasons.append("channels_required")
    unsupported_channels = [channel for channel in channels if channel not in {"telegram", "lark"}]
    if unsupported_channels:
        blocked_reasons.append("channel_not_allowlisted")
    if not target_id:
        blocked_reasons.append("target_id_required")
    if target_id not in target_allowlist:
        blocked_reasons.append("target_not_allowlisted")
    if not outbox_path_raw:
        blocked_reasons.append("outbox_path_required")
    else:
        outbox_path = Path(outbox_path_raw)
        if outbox_path.exists() and outbox_path.is_symlink():
            blocked_reasons.append("outbox_path_is_symlink")
        if outbox_path.parent.exists() and outbox_path.parent.is_symlink():
            blocked_reasons.append("outbox_parent_is_symlink")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("outbox_requires_evidence_refs")
    if _contains_secret_like_key(payload):
        blocked_reasons.append("payload_contains_secret_like_key")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "outbound_outbox_enqueue",
        "run_id": normalized_run_id,
        "source_run_status": source_run_status,
        "source_request_digest": source_request_digest,
        "channels": channels,
        "target_id": target_id,
        "request_digest": request_digest,
        "payload_hash": payload_hash,
        "payload_summary": _compact_payload_summary(payload),
        "delivery_status": "not_enqueued",
        "send_executed": False,
        "readback_verified": False,
        "blocked_reasons": blocked_reasons,
    }
    if blocked_reasons:
        return {**base, "accepted_as": "rejected_outbound_outbox_enqueue", "run_status": "rejected", "runtime_boundary": "outbound_outbox_rejected"}

    outbox_path = Path(outbox_path_raw)
    outbox_item = {
        "schema_version": SCHEMA_VERSION,
        "outbox_id": request_digest,
        "run_id": normalized_run_id,
        "source_run_status": source_run_status,
        "source_request_digest": source_request_digest,
        "channels": channels,
        "target_id": target_id,
        "payload_hash": payload_hash,
        "payload_summary": _compact_payload_summary(payload),
        "delivery_status": "pending",
        "send_executed": False,
        "created_at": _now(),
        "evidence_refs": evidence_refs,
    }
    outbox_path.parent.mkdir(parents=True, exist_ok=True)
    with outbox_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(outbox_item, ensure_ascii=False, sort_keys=True) + "\n")
    readback_rows = [json.loads(line) for line in outbox_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    readback_verified = any(
        row.get("outbox_id") == request_digest
        and row.get("payload_hash") == payload_hash
        and row.get("delivery_status") == "pending"
        for row in readback_rows
    )
    record = {
        **base,
        "accepted_as": "outbound_outbox_item_enqueued" if readback_verified else "outbound_outbox_readback_mismatch",
        "run_status": "outbound_outbox_item_enqueued" if readback_verified else "outbound_outbox_readback_mismatch",
        "runtime_boundary": "outbound_outbox_enqueue_only",
        "delivery_status": "pending" if readback_verified else "unknown",
        "readback_verified": readback_verified,
        "outbox_path": str(outbox_path),
        "evidence_refs": evidence_refs,
        "recorded_at": _now(),
        "idempotent_replay": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def request_outbound_delivery_authorization(*, ledger_path: str | Path, run_id: str, delivery_authorization_request: dict[str, Any]) -> dict[str, Any]:
    """Project elevated authorization to send a pending outbox item without sending it."""

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    rows = _read_runtime_ledger(path)
    actor_id = str(delivery_authorization_request.get("actor_id", "")).strip()
    actor_role = str(delivery_authorization_request.get("actor_role", "")).strip()
    decision = str(delivery_authorization_request.get("decision", "")).strip().lower()
    outbox_id = str(delivery_authorization_request.get("outbox_id", "")).strip()
    outbox_path_raw = str(delivery_authorization_request.get("outbox_path", "")).strip()
    channel = str(delivery_authorization_request.get("channel", "")).strip().lower()
    target_id = str(delivery_authorization_request.get("target_id", "")).strip()
    payload_hash = str(delivery_authorization_request.get("payload_hash", "")).strip()
    expires_at = str(delivery_authorization_request.get("expires_at", "")).strip()
    evidence_refs = delivery_authorization_request.get("evidence_refs", [])
    request_digest = _digest(delivery_authorization_request)
    delivery_authorization_id = _digest({"run_id": normalized_run_id, "outbox_id": outbox_id, "channel": channel, "target_id": target_id, "payload_hash": payload_hash})

    outbox_event = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "outbound_outbox_item_enqueued"
            and row.get("request_digest") == outbox_id
        ),
        None,
    )
    existing = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "outbound_delivery_authorization_projected"
            and row.get("delivery_authorization_id") == delivery_authorization_id
        ),
        None,
    )
    if existing is not None:
        return {**existing, "idempotent_replay": True}

    outbox_item = None
    blocked_reasons: list[str] = []
    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if actor_role not in ELEVATED_DONE_AUTH_ROLES:
        blocked_reasons.append("elevated_actor_required")
    if not actor_id:
        blocked_reasons.append("actor_id_required")
    if decision != "approved":
        blocked_reasons.append("delivery_not_approved")
    if not outbox_id:
        blocked_reasons.append("outbox_id_required")
    if outbox_event is None:
        blocked_reasons.append("pending_outbox_event_required")
    if not outbox_path_raw:
        blocked_reasons.append("outbox_path_required")
    else:
        outbox_path = Path(outbox_path_raw)
        if outbox_path.exists() and outbox_path.is_symlink():
            blocked_reasons.append("outbox_path_is_symlink")
        elif outbox_path.exists():
            try:
                outbox_rows = [json.loads(line) for line in outbox_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                outbox_item = next((row for row in outbox_rows if row.get("outbox_id") == outbox_id), None)
                if outbox_item is None:
                    blocked_reasons.append("outbox_item_readback_required")
            except (OSError, json.JSONDecodeError):
                blocked_reasons.append("outbox_readback_failed")
        else:
            blocked_reasons.append("outbox_path_missing")
    if not channel:
        blocked_reasons.append("channel_required")
    if channel not in {"telegram", "lark"}:
        blocked_reasons.append("channel_not_delivery_allowlisted")
    if outbox_event is not None and channel not in outbox_event.get("channels", []):
        blocked_reasons.append("channel_not_in_outbox_item")
    if outbox_item is not None and channel not in outbox_item.get("channels", []):
        blocked_reasons.append("channel_not_in_outbox_readback")
    if not target_id:
        blocked_reasons.append("target_id_required")
    if outbox_event is not None and outbox_event.get("target_id") != target_id:
        blocked_reasons.append("target_id_drift")
    if outbox_item is not None and outbox_item.get("target_id") != target_id:
        blocked_reasons.append("target_id_readback_drift")
    if not payload_hash:
        blocked_reasons.append("payload_hash_required")
    if outbox_event is not None and outbox_event.get("payload_hash") != payload_hash:
        blocked_reasons.append("payload_hash_drift")
    if outbox_item is not None and outbox_item.get("payload_hash") != payload_hash:
        blocked_reasons.append("payload_hash_readback_drift")
    if outbox_event is not None and outbox_event.get("delivery_status") != "pending":
        blocked_reasons.append("outbox_not_pending")
    if outbox_item is not None and outbox_item.get("delivery_status") != "pending":
        blocked_reasons.append("outbox_readback_not_pending")
    if not expires_at:
        blocked_reasons.append("expires_at_required")
    else:
        try:
            expires_dt = datetime.fromisoformat(expires_at)
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            if expires_dt <= datetime.now(timezone.utc):
                blocked_reasons.append("authorization_expired")
        except ValueError:
            blocked_reasons.append("invalid_expires_at")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("delivery_authorization_requires_evidence_refs")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "outbound_delivery_authorization",
        "run_id": normalized_run_id,
        "actor_id": actor_id,
        "actor_role": actor_role,
        "decision": decision,
        "outbox_id": outbox_id,
        "channel": channel,
        "target_id": target_id,
        "payload_hash": payload_hash,
        "request_digest": request_digest,
        "delivery_authorization_id": delivery_authorization_id,
        "expires_at": expires_at,
        "send_executed": False,
        "readback_verified": outbox_item is not None,
        "blocked_reasons": blocked_reasons,
    }
    if blocked_reasons:
        return {**base, "accepted_as": "rejected_outbound_delivery_authorization", "run_status": "rejected", "runtime_boundary": "outbound_delivery_authorization_rejected", "delivery_status": "not_authorized"}

    record = {
        **base,
        "accepted_as": "outbound_delivery_authorization_projected",
        "run_status": "outbound_delivery_authorization_projected",
        "runtime_boundary": "outbound_delivery_authorization_projection",
        "delivery_status": "authorized_pending_send",
        "outbox_path": outbox_path_raw,
        "evidence_refs": evidence_refs,
        "recorded_at": _now(),
        "idempotent_replay": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def execute_telegram_sender_dry_run(*, ledger_path: str | Path, run_id: str, telegram_dry_run_request: dict[str, Any]) -> dict[str, Any]:
    """Verify Telegram sender payload contract without reading token or sending."""

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    rows = _read_runtime_ledger(path)
    delivery_authorization_id = str(telegram_dry_run_request.get("delivery_authorization_id", "")).strip()
    outbox_id = str(telegram_dry_run_request.get("outbox_id", "")).strip()
    outbox_path_raw = str(telegram_dry_run_request.get("outbox_path", "")).strip()
    channel = str(telegram_dry_run_request.get("channel", "")).strip().lower()
    target_id = str(telegram_dry_run_request.get("target_id", "")).strip()
    payload_hash = str(telegram_dry_run_request.get("payload_hash", "")).strip()
    sender_mode = str(telegram_dry_run_request.get("sender_mode", "")).strip().lower()
    platform_payload = telegram_dry_run_request.get("platform_payload", {})
    evidence_refs = telegram_dry_run_request.get("evidence_refs", [])
    request_digest = _digest(telegram_dry_run_request)
    platform_payload_hash = _digest(platform_payload)
    delivery_event = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "outbound_delivery_authorization_projected"
            and row.get("delivery_authorization_id") == delivery_authorization_id
        ),
        None,
    )
    existing = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "telegram_delivery_dry_run_verified"
            and row.get("delivery_authorization_id") == delivery_authorization_id
            and row.get("platform_payload_hash") == platform_payload_hash
        ),
        None,
    )
    if existing is not None:
        return {**existing, "idempotent_replay": True}

    outbox_item = None
    blocked_reasons: list[str] = []
    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if not delivery_authorization_id:
        blocked_reasons.append("delivery_authorization_id_required")
    if delivery_event is None:
        blocked_reasons.append("delivery_authorization_required")
    if not outbox_id:
        blocked_reasons.append("outbox_id_required")
    if delivery_event is not None and delivery_event.get("outbox_id") != outbox_id:
        blocked_reasons.append("outbox_id_drift")
    if not outbox_path_raw:
        blocked_reasons.append("outbox_path_required")
    else:
        outbox_path = Path(outbox_path_raw)
        if outbox_path.exists() and outbox_path.is_symlink():
            blocked_reasons.append("outbox_path_is_symlink")
        elif outbox_path.exists():
            try:
                outbox_rows = [json.loads(line) for line in outbox_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                outbox_item = next((row for row in outbox_rows if row.get("outbox_id") == outbox_id), None)
                if outbox_item is None:
                    blocked_reasons.append("outbox_item_readback_required")
            except (OSError, json.JSONDecodeError):
                blocked_reasons.append("outbox_readback_failed")
        else:
            blocked_reasons.append("outbox_path_missing")
    if channel != "telegram":
        blocked_reasons.append("telegram_channel_required")
    if delivery_event is not None and delivery_event.get("channel") != "telegram":
        blocked_reasons.append("delivery_authorization_channel_not_telegram")
    if delivery_event is not None and delivery_event.get("target_id") != target_id:
        blocked_reasons.append("target_id_drift")
    if outbox_item is not None and outbox_item.get("target_id") != target_id:
        blocked_reasons.append("target_id_readback_drift")
    if delivery_event is not None and delivery_event.get("payload_hash") != payload_hash:
        blocked_reasons.append("payload_hash_drift")
    if outbox_item is not None and outbox_item.get("payload_hash") != payload_hash:
        blocked_reasons.append("payload_hash_readback_drift")
    if delivery_event is not None:
        expires_at = str(delivery_event.get("expires_at", ""))
        try:
            expires_dt = datetime.fromisoformat(expires_at)
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            if expires_dt <= datetime.now(timezone.utc):
                blocked_reasons.append("delivery_authorization_expired")
        except ValueError:
            blocked_reasons.append("invalid_delivery_authorization_expiry")
    if sender_mode != "dry_run":
        blocked_reasons.append("dry_run_mode_required")
    if not isinstance(platform_payload, dict):
        blocked_reasons.append("platform_payload_object_required")
    else:
        if str(platform_payload.get("chat_id", "")).strip() != target_id:
            blocked_reasons.append("platform_payload_target_drift")
        if not str(platform_payload.get("text", "")).strip():
            blocked_reasons.append("platform_payload_text_required")
        if _contains_secret_like_key(platform_payload):
            blocked_reasons.append("platform_payload_contains_secret_like_key")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("telegram_dry_run_requires_evidence_refs")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "telegram_sender_dry_run",
        "run_id": normalized_run_id,
        "delivery_authorization_id": delivery_authorization_id,
        "outbox_id": outbox_id,
        "channel": channel,
        "target_id": target_id,
        "payload_hash": payload_hash,
        "sender_mode": sender_mode,
        "request_digest": request_digest,
        "platform_payload_hash": platform_payload_hash,
        "platform_payload_summary": _compact_payload_summary(platform_payload),
        "send_executed": False,
        "token_read": False,
        "readback_verified": outbox_item is not None,
        "blocked_reasons": blocked_reasons,
    }
    if blocked_reasons:
        return {**base, "accepted_as": "rejected_telegram_delivery_dry_run", "run_status": "rejected", "runtime_boundary": "telegram_sender_dry_run_rejected"}

    record = {
        **base,
        "accepted_as": "telegram_delivery_dry_run_verified",
        "run_status": "telegram_delivery_dry_run_verified",
        "runtime_boundary": "telegram_sender_dry_run_only",
        "delivery_status": "dry_run_verified",
        "evidence_refs": evidence_refs,
        "recorded_at": _now(),
        "idempotent_replay": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def execute_lark_sender_dry_run(*, ledger_path: str | Path, run_id: str, lark_dry_run_request: dict[str, Any]) -> dict[str, Any]:
    """Verify Lark sender payload contract without reading token or sending."""

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    rows = _read_runtime_ledger(path)
    delivery_authorization_id = str(lark_dry_run_request.get("delivery_authorization_id", "")).strip()
    outbox_id = str(lark_dry_run_request.get("outbox_id", "")).strip()
    outbox_path_raw = str(lark_dry_run_request.get("outbox_path", "")).strip()
    channel = str(lark_dry_run_request.get("channel", "")).strip().lower()
    target_id = str(lark_dry_run_request.get("target_id", "")).strip()
    payload_hash = str(lark_dry_run_request.get("payload_hash", "")).strip()
    sender_mode = str(lark_dry_run_request.get("sender_mode", "")).strip().lower()
    platform_payload = lark_dry_run_request.get("platform_payload", {})
    evidence_refs = lark_dry_run_request.get("evidence_refs", [])
    request_digest = _digest(lark_dry_run_request)
    platform_payload_hash = _digest(platform_payload)
    delivery_event = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "outbound_delivery_authorization_projected"
            and row.get("delivery_authorization_id") == delivery_authorization_id
        ),
        None,
    )
    existing = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "lark_delivery_dry_run_verified"
            and row.get("delivery_authorization_id") == delivery_authorization_id
            and row.get("platform_payload_hash") == platform_payload_hash
        ),
        None,
    )
    if existing is not None:
        return {**existing, "idempotent_replay": True}

    outbox_item = None
    blocked_reasons: list[str] = []
    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if not delivery_authorization_id:
        blocked_reasons.append("delivery_authorization_id_required")
    if delivery_event is None:
        blocked_reasons.append("delivery_authorization_required")
    if not outbox_id:
        blocked_reasons.append("outbox_id_required")
    if delivery_event is not None and delivery_event.get("outbox_id") != outbox_id:
        blocked_reasons.append("outbox_id_drift")
    if not outbox_path_raw:
        blocked_reasons.append("outbox_path_required")
    else:
        outbox_path = Path(outbox_path_raw)
        if outbox_path.exists() and outbox_path.is_symlink():
            blocked_reasons.append("outbox_path_is_symlink")
        elif outbox_path.exists():
            try:
                outbox_rows = [json.loads(line) for line in outbox_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                outbox_item = next((row for row in outbox_rows if row.get("outbox_id") == outbox_id), None)
                if outbox_item is None:
                    blocked_reasons.append("outbox_item_readback_required")
            except (OSError, json.JSONDecodeError):
                blocked_reasons.append("outbox_readback_failed")
        else:
            blocked_reasons.append("outbox_path_missing")
    if channel != "lark":
        blocked_reasons.append("lark_channel_required")
    if delivery_event is not None and delivery_event.get("channel") != "lark":
        blocked_reasons.append("delivery_authorization_channel_not_lark")
    if delivery_event is not None and delivery_event.get("target_id") != target_id:
        blocked_reasons.append("target_id_drift")
    if outbox_item is not None and outbox_item.get("target_id") != target_id:
        blocked_reasons.append("target_id_readback_drift")
    if delivery_event is not None and delivery_event.get("payload_hash") != payload_hash:
        blocked_reasons.append("payload_hash_drift")
    if outbox_item is not None and outbox_item.get("payload_hash") != payload_hash:
        blocked_reasons.append("payload_hash_readback_drift")
    if delivery_event is not None:
        expires_at = str(delivery_event.get("expires_at", ""))
        try:
            expires_dt = datetime.fromisoformat(expires_at)
            if expires_dt.tzinfo is None:
                expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            if expires_dt <= datetime.now(timezone.utc):
                blocked_reasons.append("delivery_authorization_expired")
        except ValueError:
            blocked_reasons.append("invalid_delivery_authorization_expiry")
    if sender_mode != "dry_run":
        blocked_reasons.append("dry_run_mode_required")
    if not isinstance(platform_payload, dict):
        blocked_reasons.append("platform_payload_object_required")
    else:
        if str(platform_payload.get("receive_id", "")).strip() != target_id:
            blocked_reasons.append("platform_payload_target_drift")
        if str(platform_payload.get("msg_type", "")).strip() != "text":
            blocked_reasons.append("platform_payload_text_msg_type_required")
        content = platform_payload.get("content")
        if not isinstance(content, dict) or not str(content.get("text", "")).strip():
            blocked_reasons.append("platform_payload_text_required")
        if _contains_secret_like_key(platform_payload):
            blocked_reasons.append("platform_payload_contains_secret_like_key")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("lark_dry_run_requires_evidence_refs")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "lark_sender_dry_run",
        "run_id": normalized_run_id,
        "delivery_authorization_id": delivery_authorization_id,
        "outbox_id": outbox_id,
        "channel": channel,
        "target_id": target_id,
        "payload_hash": payload_hash,
        "sender_mode": sender_mode,
        "request_digest": request_digest,
        "platform_payload_hash": platform_payload_hash,
        "platform_payload_summary": _compact_payload_summary(platform_payload),
        "send_executed": False,
        "token_read": False,
        "readback_verified": outbox_item is not None,
        "blocked_reasons": blocked_reasons,
    }
    if blocked_reasons:
        return {**base, "accepted_as": "rejected_lark_delivery_dry_run", "run_status": "rejected", "runtime_boundary": "lark_sender_dry_run_rejected"}

    record = {
        **base,
        "accepted_as": "lark_delivery_dry_run_verified",
        "run_status": "lark_delivery_dry_run_verified",
        "runtime_boundary": "lark_sender_dry_run_only",
        "delivery_status": "dry_run_verified",
        "evidence_refs": evidence_refs,
        "recorded_at": _now(),
        "idempotent_replay": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def request_real_sender_preflight(*, ledger_path: str | Path, run_id: str, preflight_request: dict[str, Any]) -> dict[str, Any]:
    """Project readiness for real sender execution without token access or sending."""

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    rows = _read_runtime_ledger(path)
    channel = str(preflight_request.get("channel", "")).strip().lower()
    delivery_authorization_id = str(preflight_request.get("delivery_authorization_id", "")).strip()
    dry_run_request_digest = str(preflight_request.get("dry_run_request_digest", "")).strip()
    outbox_id = str(preflight_request.get("outbox_id", "")).strip()
    target_id = str(preflight_request.get("target_id", "")).strip()
    payload_hash = str(preflight_request.get("payload_hash", "")).strip()
    platform_payload_hash = str(preflight_request.get("platform_payload_hash", "")).strip()
    token_source = str(preflight_request.get("token_source", "")).strip()
    sender_capability_enabled = preflight_request.get("sender_capability_enabled")
    dry_run_max_age_seconds = preflight_request.get("dry_run_max_age_seconds", 0)
    evidence_refs = preflight_request.get("evidence_refs", [])
    request_digest = _digest(preflight_request)
    dry_run_status = f"{channel}_delivery_dry_run_verified" if channel in {"telegram", "lark"} else ""
    dry_run_event = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == dry_run_status
            and row.get("request_digest") == dry_run_request_digest
        ),
        None,
    )
    existing = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "real_sender_preflight_verified"
            and row.get("channel") == channel
            and row.get("delivery_authorization_id") == delivery_authorization_id
            and row.get("dry_run_request_digest") == dry_run_request_digest
            and row.get("outbox_id") == outbox_id
            and row.get("target_id") == target_id
            and row.get("payload_hash") == payload_hash
            and row.get("platform_payload_hash") == platform_payload_hash
            and row.get("token_source") == token_source
            and row.get("sender_capability_enabled") == sender_capability_enabled
        ),
        None,
    )
    if existing is not None:
        return {**existing, "idempotent_replay": True}

    blocked_reasons: list[str] = []
    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if channel not in {"telegram", "lark"}:
        blocked_reasons.append("sender_channel_not_allowlisted")
    if not delivery_authorization_id:
        blocked_reasons.append("delivery_authorization_id_required")
    if not dry_run_request_digest:
        blocked_reasons.append("dry_run_request_digest_required")
    if dry_run_event is None:
        blocked_reasons.append("sender_dry_run_required")
    if dry_run_event is not None:
        if dry_run_event.get("delivery_authorization_id") != delivery_authorization_id:
            blocked_reasons.append("delivery_authorization_id_drift")
        if dry_run_event.get("outbox_id") != outbox_id:
            blocked_reasons.append("outbox_id_drift")
        if dry_run_event.get("target_id") != target_id:
            blocked_reasons.append("target_id_drift")
        if dry_run_event.get("payload_hash") != payload_hash:
            blocked_reasons.append("payload_hash_drift")
        if dry_run_event.get("platform_payload_hash") != platform_payload_hash:
            blocked_reasons.append("platform_payload_hash_drift")
        if dry_run_event.get("send_executed") is not False:
            blocked_reasons.append("dry_run_must_not_have_sent")
        if dry_run_event.get("token_read") is not False:
            blocked_reasons.append("dry_run_must_not_have_read_token")
        recorded_at_raw = str(dry_run_event.get("recorded_at", ""))
        try:
            recorded_at = datetime.fromisoformat(recorded_at_raw)
            if recorded_at.tzinfo is None:
                recorded_at = recorded_at.replace(tzinfo=timezone.utc)
            max_age = int(dry_run_max_age_seconds)
            if max_age <= 0:
                blocked_reasons.append("dry_run_max_age_seconds_required")
            elif (datetime.now(timezone.utc) - recorded_at).total_seconds() > max_age:
                blocked_reasons.append("dry_run_too_old")
        except (TypeError, ValueError):
            blocked_reasons.append("invalid_dry_run_recorded_at_or_max_age")
    if not token_source:
        blocked_reasons.append("token_source_required")
    elif not token_source.startswith(("env:", "keychain:", "secret-ref:", "config-ref:")):
        blocked_reasons.append("token_source_must_be_reference_not_secret")
    if sender_capability_enabled is not False:
        blocked_reasons.append("sender_capability_must_remain_disabled")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("real_sender_preflight_requires_evidence_refs")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "real_sender_preflight",
        "run_id": normalized_run_id,
        "channel": channel,
        "delivery_authorization_id": delivery_authorization_id,
        "dry_run_request_digest": dry_run_request_digest,
        "outbox_id": outbox_id,
        "target_id": target_id,
        "payload_hash": payload_hash,
        "platform_payload_hash": platform_payload_hash,
        "token_source": token_source,
        "sender_capability_enabled": sender_capability_enabled,
        "request_digest": request_digest,
        "send_executed": False,
        "token_read": False,
        "readback_verified": dry_run_event is not None,
        "blocked_reasons": blocked_reasons,
    }
    if blocked_reasons:
        return {**base, "accepted_as": "rejected_real_sender_preflight", "run_status": "rejected", "runtime_boundary": "real_sender_preflight_rejected"}

    record = {
        **base,
        "accepted_as": "real_sender_preflight_verified",
        "run_status": "real_sender_preflight_verified",
        "runtime_boundary": "real_sender_preflight_no_send",
        "delivery_status": "preflight_verified_sender_still_disabled",
        "evidence_refs": evidence_refs,
        "recorded_at": _now(),
        "idempotent_replay": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def request_real_sender_execution_authorization(*, ledger_path: str | Path, run_id: str, execution_authorization_request: dict[str, Any]) -> dict[str, Any]:
    """Project elevated authorization to execute a real sender without reading token or sending yet."""

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    rows = _read_runtime_ledger(path)
    actor_id = str(execution_authorization_request.get("actor_id", "")).strip()
    actor_role = str(execution_authorization_request.get("actor_role", "")).strip().lower()
    decision = str(execution_authorization_request.get("decision", "")).strip().lower()
    channel = str(execution_authorization_request.get("channel", "")).strip().lower()
    preflight_request_digest = str(execution_authorization_request.get("preflight_request_digest", "")).strip()
    delivery_authorization_id = str(execution_authorization_request.get("delivery_authorization_id", "")).strip()
    outbox_id = str(execution_authorization_request.get("outbox_id", "")).strip()
    target_id = str(execution_authorization_request.get("target_id", "")).strip()
    payload_hash = str(execution_authorization_request.get("payload_hash", "")).strip()
    platform_payload_hash = str(execution_authorization_request.get("platform_payload_hash", "")).strip()
    token_source = str(execution_authorization_request.get("token_source", "")).strip()
    sender_capability_requested = execution_authorization_request.get("sender_capability_requested")
    expires_at = str(execution_authorization_request.get("expires_at", "")).strip()
    evidence_refs = execution_authorization_request.get("evidence_refs", [])
    semantic_authorization_request = {key: value for key, value in execution_authorization_request.items() if key != "evidence_refs"}
    request_digest = _digest(semantic_authorization_request)
    authorization_id = _digest({"run_id": normalized_run_id, "request_digest": request_digest, "kind": "real_sender_execution_authorization"})
    preflight_event = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "real_sender_preflight_verified"
            and row.get("request_digest") == preflight_request_digest
        ),
        None,
    )
    existing = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "real_sender_execution_authorized"
            and row.get("request_digest") == request_digest
            and row.get("authorization_id") == authorization_id
        ),
        None,
    )
    if existing is not None:
        return {**existing, "idempotent_replay": True}

    blocked_reasons: list[str] = []
    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if not actor_id:
        blocked_reasons.append("actor_id_required")
    if actor_role not in ELEVATED_DONE_AUTH_ROLES:
        blocked_reasons.append("actor_role_not_elevated")
    if decision != "approved":
        blocked_reasons.append("decision_must_be_approved")
    if channel not in {"telegram", "lark"}:
        blocked_reasons.append("sender_channel_not_allowlisted")
    if not preflight_request_digest:
        blocked_reasons.append("preflight_request_digest_required")
    if preflight_event is None:
        blocked_reasons.append("real_sender_preflight_required")
    if preflight_event is not None:
        if preflight_event.get("channel") != channel:
            blocked_reasons.append("channel_drift")
        if preflight_event.get("delivery_authorization_id") != delivery_authorization_id:
            blocked_reasons.append("delivery_authorization_id_drift")
        if preflight_event.get("outbox_id") != outbox_id:
            blocked_reasons.append("outbox_id_drift")
        if preflight_event.get("target_id") != target_id:
            blocked_reasons.append("target_id_drift")
        if preflight_event.get("payload_hash") != payload_hash:
            blocked_reasons.append("payload_hash_drift")
        if preflight_event.get("platform_payload_hash") != platform_payload_hash:
            blocked_reasons.append("platform_payload_hash_drift")
        if preflight_event.get("token_source") != token_source:
            blocked_reasons.append("token_source_drift")
        if preflight_event.get("sender_capability_enabled") is not False:
            blocked_reasons.append("preflight_sender_capability_must_be_disabled")
        if preflight_event.get("send_executed") is not False:
            blocked_reasons.append("preflight_must_not_have_sent")
        if preflight_event.get("token_read") is not False:
            blocked_reasons.append("preflight_must_not_have_read_token")
    if sender_capability_requested is not True:
        blocked_reasons.append("sender_capability_request_required")
    if not token_source:
        blocked_reasons.append("token_source_required")
    elif not token_source.startswith(("env:", "keychain:", "secret-ref:", "config-ref:")):
        blocked_reasons.append("token_source_must_be_reference_not_secret")
    if not expires_at:
        blocked_reasons.append("expires_at_required")
    else:
        try:
            expiry = datetime.fromisoformat(expires_at)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= datetime.now(timezone.utc):
                blocked_reasons.append("authorization_expired")
        except ValueError:
            blocked_reasons.append("expires_at_invalid")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("execution_authorization_requires_evidence_refs")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "real_sender_execution_authorization",
        "run_id": normalized_run_id,
        "actor_id": actor_id,
        "actor_role": actor_role,
        "decision": decision,
        "channel": channel,
        "preflight_request_digest": preflight_request_digest,
        "delivery_authorization_id": delivery_authorization_id,
        "outbox_id": outbox_id,
        "target_id": target_id,
        "payload_hash": payload_hash,
        "platform_payload_hash": platform_payload_hash,
        "token_source": token_source,
        "sender_capability_requested": sender_capability_requested,
        "expires_at": expires_at,
        "request_digest": request_digest,
        "authorization_id": authorization_id,
        "send_executed": False,
        "token_read": False,
        "readback_verified": preflight_event is not None,
        "blocked_reasons": blocked_reasons,
    }
    if blocked_reasons:
        return {**base, "accepted_as": "rejected_real_sender_execution_authorization", "run_status": "rejected", "runtime_boundary": "real_sender_execution_authorization_rejected"}

    record = {
        **base,
        "accepted_as": "real_sender_execution_authorized",
        "run_status": "real_sender_execution_authorized",
        "runtime_boundary": "real_sender_execution_authorization_no_send",
        "authorization_status": "approved_not_executed",
        "evidence_refs": evidence_refs,
        "recorded_at": _now(),
        "idempotent_replay": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def _read_env_token_reference(token_source: str) -> tuple[str, list[str]]:
    if not token_source.startswith("env:"):
        return "", ["token_source_env_required_for_telegram_sender"]
    env_name = token_source.removeprefix("env:").strip()
    if not env_name:
        return "", ["token_env_name_required"]
    token_value = os.environ.get(env_name, "")
    if not token_value:
        return "", ["token_value_missing"]
    return token_value, []


def _post_json(url: str, payload: dict[str, Any], *, timeout_seconds: float = 10.0, headers: dict[str, str] | None = None) -> dict[str, Any]:
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, data=encoded, headers=request_headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        body = response.read().decode("utf-8")
        if not body:
            return {"ok": False, "description": "empty_response"}
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            return {"ok": False, "description": "non_object_response"}
        return parsed


def execute_telegram_real_sender(*, ledger_path: str | Path, run_id: str, telegram_send_request: dict[str, Any]) -> dict[str, Any]:
    """Execute an authorized Telegram sender request by reading an env token and calling sendMessage."""

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    rows = _read_runtime_ledger(path)
    execution_authorization_id = str(telegram_send_request.get("execution_authorization_id", "")).strip()
    channel = str(telegram_send_request.get("channel", "")).strip().lower()
    preflight_request_digest = str(telegram_send_request.get("preflight_request_digest", "")).strip()
    outbox_id = str(telegram_send_request.get("outbox_id", "")).strip()
    target_id = str(telegram_send_request.get("target_id", "")).strip()
    payload_hash = str(telegram_send_request.get("payload_hash", "")).strip()
    platform_payload_hash = str(telegram_send_request.get("platform_payload_hash", "")).strip()
    token_source = str(telegram_send_request.get("token_source", "")).strip()
    sender_capability_enabled = telegram_send_request.get("sender_capability_enabled")
    executor_mode = str(telegram_send_request.get("executor_mode", "")).strip().lower()
    platform_payload = telegram_send_request.get("platform_payload")
    api_base_url = str(telegram_send_request.get("api_base_url", "https://api.telegram.org")).strip().rstrip("/")
    evidence_refs = telegram_send_request.get("evidence_refs", [])
    request_digest = _digest({key: value for key, value in telegram_send_request.items() if key != "evidence_refs"})
    authorization_event = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "real_sender_execution_authorized"
            and row.get("authorization_id") == execution_authorization_id
        ),
        None,
    )
    existing = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "telegram_delivery_sent"
            and row.get("request_digest") == request_digest
        ),
        None,
    )
    if existing is not None:
        return {**existing, "idempotent_replay": True}

    blocked_reasons: list[str] = []
    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if channel != "telegram":
        blocked_reasons.append("telegram_channel_required")
    if not execution_authorization_id:
        blocked_reasons.append("execution_authorization_id_required")
    if authorization_event is None:
        blocked_reasons.append("real_sender_execution_authorization_required")
    if authorization_event is not None:
        if authorization_event.get("channel") != "telegram":
            blocked_reasons.append("authorization_channel_not_telegram")
        if authorization_event.get("preflight_request_digest") != preflight_request_digest:
            blocked_reasons.append("preflight_request_digest_drift")
        if authorization_event.get("outbox_id") != outbox_id:
            blocked_reasons.append("outbox_id_drift")
        if authorization_event.get("target_id") != target_id:
            blocked_reasons.append("target_id_drift")
        if authorization_event.get("payload_hash") != payload_hash:
            blocked_reasons.append("payload_hash_drift")
        if authorization_event.get("platform_payload_hash") != platform_payload_hash:
            blocked_reasons.append("platform_payload_hash_drift")
        if authorization_event.get("token_source") != token_source:
            blocked_reasons.append("token_source_drift")
        if authorization_event.get("sender_capability_requested") is not True:
            blocked_reasons.append("authorization_capability_not_requested")
        expires_at = str(authorization_event.get("expires_at", ""))
        try:
            expiry = datetime.fromisoformat(expires_at)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= datetime.now(timezone.utc):
                blocked_reasons.append("execution_authorization_expired")
        except ValueError:
            blocked_reasons.append("execution_authorization_expires_at_invalid")
    if sender_capability_enabled is not True:
        blocked_reasons.append("sender_capability_enabled_required")
    if executor_mode != "send":
        blocked_reasons.append("executor_mode_send_required")
    if not isinstance(platform_payload, dict):
        blocked_reasons.append("platform_payload_object_required")
        computed_platform_payload_hash = ""
    else:
        computed_platform_payload_hash = _digest(platform_payload)
        if computed_platform_payload_hash != platform_payload_hash:
            blocked_reasons.append("platform_payload_hash_drift")
        if platform_payload.get("chat_id") != target_id:
            blocked_reasons.append("telegram_chat_id_drift")
        if not str(platform_payload.get("text", "")).strip():
            blocked_reasons.append("telegram_text_required")
        if _contains_secret_like_key(platform_payload):
            blocked_reasons.append("platform_payload_contains_secret_like_key")
    if not api_base_url:
        blocked_reasons.append("api_base_url_required")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("telegram_real_sender_requires_evidence_refs")

    token_value = ""
    token_blocked: list[str] = []
    if not blocked_reasons:
        token_value, token_blocked = _read_env_token_reference(token_source)
        blocked_reasons.extend(token_blocked)

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "telegram_real_sender",
        "run_id": normalized_run_id,
        "channel": channel,
        "execution_authorization_id": execution_authorization_id,
        "preflight_request_digest": preflight_request_digest,
        "outbox_id": outbox_id,
        "target_id": target_id,
        "payload_hash": payload_hash,
        "platform_payload_hash": platform_payload_hash,
        "computed_platform_payload_hash": computed_platform_payload_hash,
        "token_source": token_source,
        "token_value_stored": False,
        "sender_capability_enabled": sender_capability_enabled,
        "executor_mode": executor_mode,
        "api_base_url": api_base_url,
        "request_digest": request_digest,
        "blocked_reasons": blocked_reasons,
    }
    if blocked_reasons:
        return {**base, "accepted_as": "rejected_telegram_real_sender", "run_status": "rejected", "runtime_boundary": "telegram_real_sender_rejected", "send_executed": False, "token_read": bool(token_value)}

    send_url = f"{api_base_url}/bot{token_value}/sendMessage"
    try:
        platform_response = _post_json(send_url, platform_payload)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {
            **base,
            "accepted_as": "telegram_delivery_send_failed",
            "run_status": "telegram_delivery_send_failed",
            "runtime_boundary": "telegram_real_sender_platform_failure",
            "send_executed": True,
            "token_read": True,
            "token_value_stored": False,
            "failure_class": type(exc).__name__,
            "failure_detail": str(exc)[:200],
        }
    if platform_response.get("ok") is not True:
        return {
            **base,
            "accepted_as": "telegram_delivery_send_failed",
            "run_status": "telegram_delivery_send_failed",
            "runtime_boundary": "telegram_real_sender_platform_rejected",
            "send_executed": True,
            "token_read": True,
            "token_value_stored": False,
            "failure_class": "telegram_api_rejected",
            "failure_detail": str(platform_response.get("description", ""))[:200],
        }
    result = platform_response.get("result") if isinstance(platform_response.get("result"), dict) else {}
    platform_message_id = result.get("message_id")
    record = {
        **base,
        "accepted_as": "telegram_delivery_sent",
        "run_status": "telegram_delivery_sent",
        "runtime_boundary": "telegram_real_sender_executed",
        "delivery_status": "sent",
        "send_executed": True,
        "token_read": True,
        "token_value_stored": False,
        "platform_message_id": platform_message_id,
        "platform_response_ok": True,
        "evidence_refs": evidence_refs,
        "recorded_at": _now(),
        "idempotent_replay": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def execute_lark_real_sender(*, ledger_path: str | Path, run_id: str, lark_send_request: dict[str, Any]) -> dict[str, Any]:
    """Execute an authorized Lark sender request by reading an env token and calling Lark message API."""

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    rows = _read_runtime_ledger(path)
    execution_authorization_id = str(lark_send_request.get("execution_authorization_id", "")).strip()
    channel = str(lark_send_request.get("channel", "")).strip().lower()
    preflight_request_digest = str(lark_send_request.get("preflight_request_digest", "")).strip()
    outbox_id = str(lark_send_request.get("outbox_id", "")).strip()
    target_id = str(lark_send_request.get("target_id", "")).strip()
    payload_hash = str(lark_send_request.get("payload_hash", "")).strip()
    platform_payload_hash = str(lark_send_request.get("platform_payload_hash", "")).strip()
    token_source = str(lark_send_request.get("token_source", "")).strip()
    sender_capability_enabled = lark_send_request.get("sender_capability_enabled")
    executor_mode = str(lark_send_request.get("executor_mode", "")).strip().lower()
    platform_payload = lark_send_request.get("platform_payload")
    receive_id_type = str(lark_send_request.get("receive_id_type", "chat_id")).strip() or "chat_id"
    api_base_url = str(lark_send_request.get("api_base_url", "https://open.feishu.cn")).strip().rstrip("/")
    evidence_refs = lark_send_request.get("evidence_refs", [])
    request_digest = _digest({key: value for key, value in lark_send_request.items() if key != "evidence_refs"})
    authorization_event = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "real_sender_execution_authorized"
            and row.get("authorization_id") == execution_authorization_id
        ),
        None,
    )
    existing = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "lark_delivery_sent"
            and row.get("request_digest") == request_digest
        ),
        None,
    )
    if existing is not None:
        return {**existing, "idempotent_replay": True}

    blocked_reasons: list[str] = []
    if not normalized_run_id:
        blocked_reasons.append("run_id_required")
    if channel != "lark":
        blocked_reasons.append("lark_channel_required")
    if not execution_authorization_id:
        blocked_reasons.append("execution_authorization_id_required")
    if authorization_event is None:
        blocked_reasons.append("real_sender_execution_authorization_required")
    if authorization_event is not None:
        if authorization_event.get("channel") != "lark":
            blocked_reasons.append("authorization_channel_not_lark")
        if authorization_event.get("preflight_request_digest") != preflight_request_digest:
            blocked_reasons.append("preflight_request_digest_drift")
        if authorization_event.get("outbox_id") != outbox_id:
            blocked_reasons.append("outbox_id_drift")
        if authorization_event.get("target_id") != target_id:
            blocked_reasons.append("target_id_drift")
        if authorization_event.get("payload_hash") != payload_hash:
            blocked_reasons.append("payload_hash_drift")
        if authorization_event.get("platform_payload_hash") != platform_payload_hash:
            blocked_reasons.append("platform_payload_hash_drift")
        if authorization_event.get("token_source") != token_source:
            blocked_reasons.append("token_source_drift")
        if authorization_event.get("sender_capability_requested") is not True:
            blocked_reasons.append("authorization_capability_not_requested")
        expires_at = str(authorization_event.get("expires_at", ""))
        try:
            expiry = datetime.fromisoformat(expires_at)
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry <= datetime.now(timezone.utc):
                blocked_reasons.append("execution_authorization_expired")
        except ValueError:
            blocked_reasons.append("execution_authorization_expires_at_invalid")
    if sender_capability_enabled is not True:
        blocked_reasons.append("sender_capability_enabled_required")
    if executor_mode != "send":
        blocked_reasons.append("executor_mode_send_required")
    if receive_id_type not in {"chat_id", "open_id", "user_id", "email", "union_id"}:
        blocked_reasons.append("lark_receive_id_type_not_allowlisted")
    if not isinstance(platform_payload, dict):
        blocked_reasons.append("platform_payload_object_required")
        computed_platform_payload_hash = ""
    else:
        computed_platform_payload_hash = _digest(platform_payload)
        if computed_platform_payload_hash != platform_payload_hash:
            blocked_reasons.append("platform_payload_hash_drift")
        if platform_payload.get("receive_id") != target_id:
            blocked_reasons.append("lark_receive_id_drift")
        if platform_payload.get("msg_type") != "text":
            blocked_reasons.append("lark_msg_type_text_required")
        content = platform_payload.get("content")
        if not isinstance(content, dict) or not str(content.get("text", "")).strip():
            blocked_reasons.append("lark_text_required")
        if _contains_secret_like_key(platform_payload):
            blocked_reasons.append("platform_payload_contains_secret_like_key")
    if not api_base_url:
        blocked_reasons.append("api_base_url_required")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("lark_real_sender_requires_evidence_refs")

    token_value = ""
    token_blocked: list[str] = []
    if not blocked_reasons:
        token_value, token_blocked = _read_env_token_reference(token_source)
        blocked_reasons.extend(token_blocked)

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "lark_real_sender",
        "run_id": normalized_run_id,
        "channel": channel,
        "execution_authorization_id": execution_authorization_id,
        "preflight_request_digest": preflight_request_digest,
        "outbox_id": outbox_id,
        "target_id": target_id,
        "payload_hash": payload_hash,
        "platform_payload_hash": platform_payload_hash,
        "computed_platform_payload_hash": computed_platform_payload_hash,
        "token_source": token_source,
        "token_value_stored": False,
        "sender_capability_enabled": sender_capability_enabled,
        "executor_mode": executor_mode,
        "receive_id_type": receive_id_type,
        "api_base_url": api_base_url,
        "request_digest": request_digest,
        "blocked_reasons": blocked_reasons,
    }
    if blocked_reasons:
        return {**base, "accepted_as": "rejected_lark_real_sender", "run_status": "rejected", "runtime_boundary": "lark_real_sender_rejected", "send_executed": False, "token_read": bool(token_value)}

    send_url = f"{api_base_url}/open-apis/im/v1/messages?receive_id_type={receive_id_type}"
    assert isinstance(platform_payload, dict)
    send_payload: dict[str, Any] = {str(key): value for key, value in platform_payload.items()}
    if isinstance(send_payload.get("content"), dict):
        send_payload["content"] = json.dumps(send_payload["content"], ensure_ascii=False)
    try:
        platform_response = _post_json(send_url, send_payload, headers={"Authorization": f"Bearer {token_value}"})
    except urllib.error.HTTPError as exc:
        try:
            failure_body = exc.read().decode("utf-8")[:200]
        except Exception:
            failure_body = str(exc)[:200]
        return {
            **base,
            "accepted_as": "lark_delivery_send_failed",
            "run_status": "lark_delivery_send_failed",
            "runtime_boundary": "lark_real_sender_platform_failure",
            "send_executed": True,
            "token_read": True,
            "token_value_stored": False,
            "failure_class": type(exc).__name__,
            "failure_detail": failure_body,
        }
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {
            **base,
            "accepted_as": "lark_delivery_send_failed",
            "run_status": "lark_delivery_send_failed",
            "runtime_boundary": "lark_real_sender_platform_failure",
            "send_executed": True,
            "token_read": True,
            "token_value_stored": False,
            "failure_class": type(exc).__name__,
            "failure_detail": str(exc)[:200],
        }
    if platform_response.get("code") not in {0, "0"}:
        return {
            **base,
            "accepted_as": "lark_delivery_send_failed",
            "run_status": "lark_delivery_send_failed",
            "runtime_boundary": "lark_real_sender_platform_rejected",
            "send_executed": True,
            "token_read": True,
            "token_value_stored": False,
            "failure_class": "lark_api_rejected",
            "failure_detail": str(platform_response.get("msg", ""))[:200],
        }
    data = platform_response.get("data") if isinstance(platform_response.get("data"), dict) else {}
    platform_message_id = data.get("message_id")
    record = {
        **base,
        "accepted_as": "lark_delivery_sent",
        "run_status": "lark_delivery_sent",
        "runtime_boundary": "lark_real_sender_executed",
        "delivery_status": "sent",
        "send_executed": True,
        "token_read": True,
        "token_value_stored": False,
        "platform_message_id": platform_message_id,
        "platform_response_ok": True,
        "evidence_refs": evidence_refs,
        "recorded_at": _now(),
        "idempotent_replay": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record


def execute_external_adapter_rollback_write(*, ledger_path: str | Path, run_id: str, rollback_request: dict[str, Any]) -> dict[str, Any]:
    """Rollback a previously verified taskpool/Kanban closeout to its recorded previous_status."""

    path = Path(ledger_path)
    normalized_run_id = run_id.strip()
    rows = _read_runtime_ledger(path)
    adapter = str(rollback_request.get("adapter", "")).strip().lower()
    authorization_id = str(rollback_request.get("authorization_id", "")).strip()
    intent_id = str(rollback_request.get("intent_id", "")).strip()
    target_ref = str(rollback_request.get("target_ref", "")).strip()
    target_status = str(rollback_request.get("target_status", "")).strip().lower()
    expected_current_status = str(rollback_request.get("expected_current_status", "")).strip().lower()
    write_request_digest = str(rollback_request.get("write_request_digest", "")).strip()
    evidence_refs = rollback_request.get("evidence_refs", [])
    request_digest = _digest(rollback_request)
    verified_status_by_adapter = {
        "taskpool": "taskpool_note_write_verified",
        "kanban": "kanban_db_write_verified",
    }
    verified_status = verified_status_by_adapter.get(adapter, "")
    write_event = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == verified_status
            and row.get("authorization_id") == authorization_id
            and row.get("intent_id") == intent_id
            and row.get("target_ref") == target_ref
            and row.get("request_digest") == write_request_digest
        ),
        None,
    )
    existing = next(
        (
            row
            for row in rows
            if row.get("run_id") == normalized_run_id
            and row.get("run_status") == "external_adapter_rollback_verified"
            and row.get("authorization_id") == authorization_id
            and row.get("intent_id") == intent_id
            and row.get("target_ref") == target_ref
            and row.get("target_status") == target_status
            and row.get("write_request_digest") == write_request_digest
        ),
        None,
    )
    if existing is not None:
        return {**existing, "idempotent_replay": True}

    blocked_reasons: list[str] = []
    if adapter not in {"taskpool", "kanban"}:
        blocked_reasons.append("rollback_adapter_not_supported")
    if write_event is None:
        blocked_reasons.append("verified_write_event_required")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        blocked_reasons.append("rollback_requires_evidence_refs")
    if not target_status:
        blocked_reasons.append("rollback_target_status_required")
    previous_status = str((write_event or {}).get("previous_status", "")).strip().lower()
    if write_event is not None:
        if adapter != str(write_event.get("adapter", "")).lower():
            blocked_reasons.append("rollback_adapter_mismatch")
        if target_ref != write_event.get("target_ref"):
            blocked_reasons.append("rollback_target_ref_mismatch")
        if target_status != previous_status:
            blocked_reasons.append("rollback_target_status_must_match_previous_status")
        if not previous_status:
            blocked_reasons.append("write_event_previous_status_missing")

    current_status = ""
    readback_status = ""
    task_note_path = ""
    db_path_raw = ""
    if adapter == "taskpool" and "verified_write_event_required" not in blocked_reasons:
        tasks_dir_raw = str(rollback_request.get("taskpool_tasks_dir", "")).strip()
        if not tasks_dir_raw:
            blocked_reasons.append("taskpool_tasks_dir_required")
        else:
            note_path, path_errors = _safe_taskpool_note_path(Path(tasks_dir_raw), target_ref)
            blocked_reasons.extend(path_errors)
            if note_path is not None and not blocked_reasons:
                task_note_path = str(note_path)
                text = note_path.read_text(encoding="utf-8")
                current_status = _extract_frontmatter_status(text)
                if expected_current_status and current_status != expected_current_status:
                    blocked_reasons.append("rollback_current_status_mismatch")
    elif adapter == "kanban" and "verified_write_event_required" not in blocked_reasons:
        db_path_raw = str(rollback_request.get("kanban_db_path", "")).strip()
        if not db_path_raw:
            blocked_reasons.append("kanban_db_path_required")
        else:
            db_path = Path(db_path_raw)
            if db_path.is_symlink():
                blocked_reasons.append("kanban_db_is_symlink")
            elif not db_path.exists():
                blocked_reasons.append("kanban_db_missing")
            elif not db_path.is_file():
                blocked_reasons.append("kanban_db_not_regular_file")
            if not blocked_reasons:
                try:
                    with sqlite3.connect(db_path) as conn:
                        table = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone()
                        if table is None:
                            blocked_reasons.append("kanban_tasks_table_missing")
                        else:
                            row = conn.execute("SELECT status FROM tasks WHERE id=?", (target_ref,)).fetchone()
                            if row is None:
                                blocked_reasons.append("kanban_task_missing")
                            else:
                                current_status = str(row[0] or "").strip().lower()
                                if expected_current_status and current_status != expected_current_status:
                                    blocked_reasons.append("rollback_current_status_mismatch")
                except sqlite3.Error:
                    blocked_reasons.append("kanban_db_unreadable")

    base = {
        "schema_version": SCHEMA_VERSION,
        "mode": "external_adapter_rollback_write",
        "run_id": normalized_run_id,
        "adapter": adapter,
        "authorization_id": authorization_id,
        "intent_id": intent_id,
        "target_ref": target_ref,
        "target_status": target_status,
        "expected_current_status": expected_current_status,
        "previous_status": previous_status,
        "current_status": current_status,
        "write_request_digest": write_request_digest,
        "request_digest": request_digest,
        "write_executed": False,
        "readback_verified": False,
        "external_adapter_write_through_enabled": False,
        "blocked_reasons": blocked_reasons,
    }
    if blocked_reasons:
        return {**base, "accepted_as": "rejected_external_adapter_rollback", "run_status": "rejected", "runtime_boundary": "external_adapter_rollback_rejected"}

    if adapter == "taskpool":
        note_path = Path(task_note_path)
        text = note_path.read_text(encoding="utf-8")
        updated = _replace_frontmatter_status(text, target_status)
        if "<!-- hermes_adapter_rollback" not in updated:
            updated += f"\n<!-- hermes_adapter_rollback run_id={normalized_run_id} adapter=taskpool target_status={target_status} -->\n"
        note_path.write_text(updated, encoding="utf-8")
        readback_status = _extract_frontmatter_status(note_path.read_text(encoding="utf-8"))
    else:
        db_path = Path(db_path_raw)
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE tasks SET status=? WHERE id=?", (target_status, target_ref))
            conn.commit()
            row = conn.execute("SELECT status FROM tasks WHERE id=?", (target_ref,)).fetchone()
        readback_status = str(row[0] if row else "").strip().lower()

    readback_verified = readback_status == target_status
    record = {
        **base,
        "accepted_as": "external_adapter_rollback_verified" if readback_verified else "external_adapter_rollback_mismatch",
        "run_status": "external_adapter_rollback_verified" if readback_verified else "external_adapter_rollback_mismatch",
        "runtime_boundary": "external_adapter_digest_bound_rollback",
        "write_executed": True,
        "readback_status": readback_status,
        "readback_verified": readback_verified,
        "external_adapter_write_through_enabled": True,
        "evidence_refs": evidence_refs,
        "recorded_at": _now(),
        "idempotent_replay": False,
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
    parser.add_argument("--probe-external-runtime-run", action="store_true", help="Run an allowlisted external agent --version probe")
    parser.add_argument("--dry-run-external-agent-prompt", action="store_true", help="Send a no-tool/no-write prompt to an allowlisted external agent")
    parser.add_argument("--run-external-agent-readonly-context", action="store_true", help="Send a no-tool/no-write prompt with a readonly context artifact pointer")
    parser.add_argument("--append-external-agent-proposal", action="store_true", help="Append a validated external agent proposal artifact pointer")
    parser.add_argument("--append-certification-promotion", action="store_true", help="Append a validated multi-model certification promotion artifact pointer")
    parser.add_argument("--request-done-authorization", action="store_true", help="Project an elevated done authorization without fact mutation")
    parser.add_argument("--execute-ledger-fact-mutation", action="store_true", help="Execute ledger-local canonical lifecycle fact mutation")
    parser.add_argument("--request-external-adapter-write-intent", action="store_true", help="Project external adapter write intent without write-through")
    parser.add_argument("--request-external-adapter-write-authorization", action="store_true", help="Project external adapter write authorization without executing write-through")
    parser.add_argument("--execute-external-adapter-local-file-write", action="store_true", help="Execute authorized external adapter write against a local-file adapter harness and read it back")
    parser.add_argument("--execute-taskpool-note-closeout-write", action="store_true", help="Execute authorized taskpool note closeout by mutating frontmatter status and reading it back")
    parser.add_argument("--execute-kanban-db-closeout-write", action="store_true", help="Execute authorized Kanban DB closeout by mutating tasks.status and reading it back")
    parser.add_argument("--execute-external-adapter-rollback-write", action="store_true", help="Execute digest-bound rollback for a previously verified taskpool/Kanban closeout")
    parser.add_argument("--execute-outbound-outbox-enqueue", action="store_true", help="Enqueue a pending Lark/Telegram outbox item without sending it")
    parser.add_argument("--request-outbound-delivery-authorization", action="store_true", help="Project elevated authorization for a pending outbound item without sending it")
    parser.add_argument("--execute-telegram-sender-dry-run", action="store_true", help="Verify Telegram sender payload contract without token access or sending")
    parser.add_argument("--execute-lark-sender-dry-run", action="store_true", help="Verify Lark sender payload contract without token access or sending")
    parser.add_argument("--request-real-sender-preflight", action="store_true", help="Verify real sender readiness while sender capability remains disabled")
    parser.add_argument("--request-real-sender-execution-authorization", action="store_true", help="Project elevated authorization for future real sender execution without token access or sending")
    parser.add_argument("--execute-telegram-real-sender", action="store_true", help="Execute authorized Telegram sender by reading env token and calling Telegram sendMessage")
    parser.add_argument("--execute-lark-real-sender", action="store_true", help="Execute authorized Lark sender by reading env token and calling Lark message API")
    parser.add_argument("--runtime-ledger", default="", help="JSONL ledger path for runtime run operations")
    parser.add_argument("--runtime-request-json", default="{}", help="JSON request payload for --start-runtime-run")
    parser.add_argument("--run-id", default="", help="Run id for runtime operations")
    parser.add_argument("--executor-request-json", default="{}", help="JSON executor payload for --execute-runtime-run")
    parser.add_argument("--probe-request-json", default="{}", help="JSON probe payload for --probe-external-runtime-run")
    parser.add_argument("--prompt-request-json", default="{}", help="JSON prompt payload for --dry-run-external-agent-prompt")
    parser.add_argument("--context-request-json", default="{}", help="JSON context payload for --run-external-agent-readonly-context")
    parser.add_argument("--proposal-request-json", default="{}", help="JSON proposal payload for --append-external-agent-proposal")
    parser.add_argument("--certification-request-json", default="{}", help="JSON certification payload for --append-certification-promotion")
    parser.add_argument("--authorization-request-json", default="{}", help="JSON authorization payload for --request-done-authorization")
    parser.add_argument("--fact-request-json", default="{}", help="JSON fact mutation payload for --execute-ledger-fact-mutation")
    parser.add_argument("--adapter-intent-json", default="{}", help="JSON adapter write intent payload for --request-external-adapter-write-intent")
    parser.add_argument("--adapter-authorization-json", default="{}", help="JSON adapter write authorization payload for --request-external-adapter-write-authorization")
    parser.add_argument("--adapter-execution-json", default="{}", help="JSON adapter write execution payload for --execute-external-adapter-local-file-write")
    parser.add_argument("--taskpool-execution-json", default="{}", help="JSON taskpool note write execution payload for --execute-taskpool-note-closeout-write")
    parser.add_argument("--kanban-execution-json", default="{}", help="JSON Kanban DB write execution payload for --execute-kanban-db-closeout-write")
    parser.add_argument("--adapter-rollback-json", default="{}", help="JSON rollback payload for --execute-external-adapter-rollback-write")
    parser.add_argument("--outbox-request-json", default="{}", help="JSON outbox enqueue payload for --execute-outbound-outbox-enqueue")
    parser.add_argument("--delivery-authorization-json", default="{}", help="JSON delivery authorization payload for --request-outbound-delivery-authorization")
    parser.add_argument("--telegram-dry-run-json", default="{}", help="JSON Telegram dry-run payload for --execute-telegram-sender-dry-run")
    parser.add_argument("--lark-dry-run-json", default="{}", help="JSON Lark dry-run payload for --execute-lark-sender-dry-run")
    parser.add_argument("--real-sender-preflight-json", default="{}", help="JSON real sender preflight payload for --request-real-sender-preflight")
    parser.add_argument("--real-sender-execution-authorization-json", default="{}", help="JSON real sender execution authorization payload for --request-real-sender-execution-authorization")
    parser.add_argument("--telegram-real-send-json", default="{}", help="JSON Telegram real sender payload for --execute-telegram-real-sender")
    parser.add_argument("--lark-real-send-json", default="{}", help="JSON Lark real sender payload for --execute-lark-real-sender")
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
    elif args.probe_external_runtime_run:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --probe-external-runtime-run")
        if not args.run_id:
            parser.error("--run-id is required with --probe-external-runtime-run")
        probe_request = json.loads(args.probe_request_json)
        if not isinstance(probe_request, dict):
            parser.error("--probe-request-json must decode to an object")
        result = probe_external_agent_runtime_run(ledger_path=args.runtime_ledger, run_id=args.run_id, probe_request=probe_request)
    elif args.dry_run_external_agent_prompt:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --dry-run-external-agent-prompt")
        if not args.run_id:
            parser.error("--run-id is required with --dry-run-external-agent-prompt")
        prompt_request = json.loads(args.prompt_request_json)
        if not isinstance(prompt_request, dict):
            parser.error("--prompt-request-json must decode to an object")
        result = dry_run_external_agent_prompt(ledger_path=args.runtime_ledger, run_id=args.run_id, prompt_request=prompt_request)
    elif args.run_external_agent_readonly_context:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --run-external-agent-readonly-context")
        if not args.run_id:
            parser.error("--run-id is required with --run-external-agent-readonly-context")
        context_request = json.loads(args.context_request_json)
        if not isinstance(context_request, dict):
            parser.error("--context-request-json must decode to an object")
        result = run_external_agent_readonly_context(ledger_path=args.runtime_ledger, run_id=args.run_id, context_request=context_request)
    elif args.append_external_agent_proposal:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --append-external-agent-proposal")
        if not args.run_id:
            parser.error("--run-id is required with --append-external-agent-proposal")
        proposal_request = json.loads(args.proposal_request_json)
        if not isinstance(proposal_request, dict):
            parser.error("--proposal-request-json must decode to an object")
        result = append_external_agent_proposal(ledger_path=args.runtime_ledger, run_id=args.run_id, proposal_request=proposal_request)
    elif args.append_certification_promotion:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --append-certification-promotion")
        if not args.run_id:
            parser.error("--run-id is required with --append-certification-promotion")
        certification_request = json.loads(args.certification_request_json)
        if not isinstance(certification_request, dict):
            parser.error("--certification-request-json must decode to an object")
        result = append_certification_promotion(ledger_path=args.runtime_ledger, run_id=args.run_id, certification_request=certification_request)
    elif args.request_done_authorization:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --request-done-authorization")
        if not args.run_id:
            parser.error("--run-id is required with --request-done-authorization")
        authorization_request = json.loads(args.authorization_request_json)
        if not isinstance(authorization_request, dict):
            parser.error("--authorization-request-json must decode to an object")
        result = request_done_authorization(ledger_path=args.runtime_ledger, run_id=args.run_id, authorization_request=authorization_request)
    elif args.execute_ledger_fact_mutation:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --execute-ledger-fact-mutation")
        if not args.run_id:
            parser.error("--run-id is required with --execute-ledger-fact-mutation")
        fact_request = json.loads(args.fact_request_json)
        if not isinstance(fact_request, dict):
            parser.error("--fact-request-json must decode to an object")
        result = execute_ledger_fact_mutation(ledger_path=args.runtime_ledger, run_id=args.run_id, fact_request=fact_request)
    elif args.request_external_adapter_write_intent:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --request-external-adapter-write-intent")
        if not args.run_id:
            parser.error("--run-id is required with --request-external-adapter-write-intent")
        adapter_intent = json.loads(args.adapter_intent_json)
        if not isinstance(adapter_intent, dict):
            parser.error("--adapter-intent-json must decode to an object")
        result = request_external_adapter_write_intent(ledger_path=args.runtime_ledger, run_id=args.run_id, intent_request=adapter_intent)
    elif args.request_external_adapter_write_authorization:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --request-external-adapter-write-authorization")
        if not args.run_id:
            parser.error("--run-id is required with --request-external-adapter-write-authorization")
        adapter_authorization = json.loads(args.adapter_authorization_json)
        if not isinstance(adapter_authorization, dict):
            parser.error("--adapter-authorization-json must decode to an object")
        result = request_external_adapter_write_authorization(ledger_path=args.runtime_ledger, run_id=args.run_id, authorization_request=adapter_authorization)
    elif args.execute_external_adapter_local_file_write:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --execute-external-adapter-local-file-write")
        if not args.run_id:
            parser.error("--run-id is required with --execute-external-adapter-local-file-write")
        adapter_execution = json.loads(args.adapter_execution_json)
        if not isinstance(adapter_execution, dict):
            parser.error("--adapter-execution-json must decode to an object")
        result = execute_external_adapter_local_file_write(ledger_path=args.runtime_ledger, run_id=args.run_id, execution_request=adapter_execution)
    elif args.execute_taskpool_note_closeout_write:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --execute-taskpool-note-closeout-write")
        if not args.run_id:
            parser.error("--run-id is required with --execute-taskpool-note-closeout-write")
        taskpool_execution = json.loads(args.taskpool_execution_json)
        if not isinstance(taskpool_execution, dict):
            parser.error("--taskpool-execution-json must decode to an object")
        result = execute_taskpool_note_closeout_write(ledger_path=args.runtime_ledger, run_id=args.run_id, execution_request=taskpool_execution)
    elif args.execute_kanban_db_closeout_write:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --execute-kanban-db-closeout-write")
        if not args.run_id:
            parser.error("--run-id is required with --execute-kanban-db-closeout-write")
        kanban_execution = json.loads(args.kanban_execution_json)
        if not isinstance(kanban_execution, dict):
            parser.error("--kanban-execution-json must decode to an object")
        result = execute_kanban_db_closeout_write(ledger_path=args.runtime_ledger, run_id=args.run_id, execution_request=kanban_execution)
    elif args.execute_external_adapter_rollback_write:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --execute-external-adapter-rollback-write")
        if not args.run_id:
            parser.error("--run-id is required with --execute-external-adapter-rollback-write")
        adapter_rollback = json.loads(args.adapter_rollback_json)
        if not isinstance(adapter_rollback, dict):
            parser.error("--adapter-rollback-json must decode to an object")
        result = execute_external_adapter_rollback_write(ledger_path=args.runtime_ledger, run_id=args.run_id, rollback_request=adapter_rollback)
    elif args.execute_outbound_outbox_enqueue:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --execute-outbound-outbox-enqueue")
        if not args.run_id:
            parser.error("--run-id is required with --execute-outbound-outbox-enqueue")
        outbox_request = json.loads(args.outbox_request_json)
        if not isinstance(outbox_request, dict):
            parser.error("--outbox-request-json must decode to an object")
        result = execute_outbound_outbox_enqueue(ledger_path=args.runtime_ledger, run_id=args.run_id, outbox_request=outbox_request)
    elif args.request_outbound_delivery_authorization:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --request-outbound-delivery-authorization")
        if not args.run_id:
            parser.error("--run-id is required with --request-outbound-delivery-authorization")
        delivery_authorization = json.loads(args.delivery_authorization_json)
        if not isinstance(delivery_authorization, dict):
            parser.error("--delivery-authorization-json must decode to an object")
        result = request_outbound_delivery_authorization(ledger_path=args.runtime_ledger, run_id=args.run_id, delivery_authorization_request=delivery_authorization)
    elif args.execute_telegram_sender_dry_run:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --execute-telegram-sender-dry-run")
        if not args.run_id:
            parser.error("--run-id is required with --execute-telegram-sender-dry-run")
        telegram_dry_run = json.loads(args.telegram_dry_run_json)
        if not isinstance(telegram_dry_run, dict):
            parser.error("--telegram-dry-run-json must decode to an object")
        result = execute_telegram_sender_dry_run(ledger_path=args.runtime_ledger, run_id=args.run_id, telegram_dry_run_request=telegram_dry_run)
    elif args.execute_lark_sender_dry_run:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --execute-lark-sender-dry-run")
        if not args.run_id:
            parser.error("--run-id is required with --execute-lark-sender-dry-run")
        lark_dry_run = json.loads(args.lark_dry_run_json)
        if not isinstance(lark_dry_run, dict):
            parser.error("--lark-dry-run-json must decode to an object")
        result = execute_lark_sender_dry_run(ledger_path=args.runtime_ledger, run_id=args.run_id, lark_dry_run_request=lark_dry_run)
    elif args.request_real_sender_preflight:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --request-real-sender-preflight")
        if not args.run_id:
            parser.error("--run-id is required with --request-real-sender-preflight")
        preflight_request = json.loads(args.real_sender_preflight_json)
        if not isinstance(preflight_request, dict):
            parser.error("--real-sender-preflight-json must decode to an object")
        result = request_real_sender_preflight(ledger_path=args.runtime_ledger, run_id=args.run_id, preflight_request=preflight_request)
    elif args.request_real_sender_execution_authorization:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --request-real-sender-execution-authorization")
        if not args.run_id:
            parser.error("--run-id is required with --request-real-sender-execution-authorization")
        execution_authorization_request = json.loads(args.real_sender_execution_authorization_json)
        if not isinstance(execution_authorization_request, dict):
            parser.error("--real-sender-execution-authorization-json must decode to an object")
        result = request_real_sender_execution_authorization(ledger_path=args.runtime_ledger, run_id=args.run_id, execution_authorization_request=execution_authorization_request)
    elif args.execute_telegram_real_sender:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --execute-telegram-real-sender")
        if not args.run_id:
            parser.error("--run-id is required with --execute-telegram-real-sender")
        telegram_send_request = json.loads(args.telegram_real_send_json)
        if not isinstance(telegram_send_request, dict):
            parser.error("--telegram-real-send-json must decode to an object")
        result = execute_telegram_real_sender(ledger_path=args.runtime_ledger, run_id=args.run_id, telegram_send_request=telegram_send_request)
    elif args.execute_lark_real_sender:
        if not args.runtime_ledger:
            parser.error("--runtime-ledger is required with --execute-lark-real-sender")
        if not args.run_id:
            parser.error("--run-id is required with --execute-lark-real-sender")
        lark_send_request = json.loads(args.lark_real_send_json)
        if not isinstance(lark_send_request, dict):
            parser.error("--lark-real-send-json must decode to an object")
        result = execute_lark_real_sender(ledger_path=args.runtime_ledger, run_id=args.run_id, lark_send_request=lark_send_request)
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
