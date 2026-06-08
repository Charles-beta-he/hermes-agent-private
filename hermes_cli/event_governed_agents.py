"""Report-only contracts for Hermes Event-Governed Agent Control Plane.

This module is intentionally deterministic and side-effect free. It does not
execute tools, mutate task lifecycle, write memory/skills, send messages, or
change cron/provider configuration. Agents and workers can use it to classify
what they are allowed to propose before a real policy/human gate exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
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
        "coverage_status": "gap_identified",
        "next_safe_slice": "add fork/join graph contract and join evidence requirements before runtime fanout",
        "authority_boundary": "do_not_start high-parallel worker dispatch before join/merge evidence gates exist",
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
