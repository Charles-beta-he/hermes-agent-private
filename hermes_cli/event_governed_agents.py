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
    else:
        result = build_control_plane_report()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(f"{result['architecture'] if 'architecture' in result else 'Agent action verdict'}: {result['mode']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
