"""``hermes agent-os`` advisory readback command.

This subcommand is the first runtime-visible surface for the Hermes Agent OS
Control Plane project. It is intentionally read-only / shadow-only: it exposes
status, doctor, and intent-routing advice from project artifacts without
invoking capabilities or changing lifecycle state.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes_cli.agent_workbench_projection import (
    ReviewPacketTemplateError,
    TaskStateProjectionError,
    build_review_packet_template,
    build_task_state_projection,
    load_projection_from_file,
    load_tasks_from_file,
)
from hermes_cli.agent_os_control_plane import (
    authorize_local_harness,
    bootstrap_erg_runtime_state,
    canonical_schema_status,
    doctor,
    evidence_projection_status,
    execute_local_harness,
    format_summary,
    generate_replay_quality_report,
    high_risk_boundary_coverage,
    read_only_runtime_audit,
    replay_recent_user_intents,
    route_shadow,
    status,
)
from hermes_cli.personal_cognitive_reconciler import (
    ReconciliationValidationError,
    cognitive_schema,
    reconcile_packet,
    validation_error_payload,
)
from hermes_cli.semantic_control_layer import (
    SemanticControlValidationError,
    project_semantic_control,
    semantic_control_schema,
    semantic_validation_error_payload,
)
from hermes_cli.temporal_ledger_runtime import (
    activate_runtime as activate_temporal_ledger_runtime,
    deactivate_runtime as deactivate_temporal_ledger_runtime,
    inspect_runtime as inspect_temporal_ledger_runtime,
    runtime_status as temporal_ledger_runtime_status,
)


def _emit(payload: dict[str, Any], *, as_json: bool, sort_keys: bool = False) -> int:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=sort_keys))
    else:
        print(format_summary(payload))
    return 0 if payload.get("ok", False) else 1


def _emit_temporal(payload: dict[str, Any], *, as_json: bool) -> int:
    if as_json:
        print(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        )
    else:
        state = (
            payload.get("state")
            or payload.get("outcome")
            or payload.get("error_code")
            or "unknown"
        )
        print(
            f"Temporal Commitment Ledger Runtime: {'OK' if payload.get('ok') else 'ATTENTION'}"
        )
        print(f"state: {state}")
        print(f"active: {payload.get('active')} trusted: {payload.get('trusted')}")
        if payload.get("module_sha256"):
            print(f"module_sha256: {payload.get('module_sha256')}")
        if payload.get("ledger_path"):
            print(f"ledger_path: {payload.get('ledger_path')}")
        findings = payload.get("findings") or []
        for finding in findings:
            print(f"- {finding}")
    return 0 if payload.get("ok") else 1


def cmd_agent_os(args: argparse.Namespace) -> int:
    action = getattr(args, "agent_os_action", None) or "status"
    as_json = bool(getattr(args, "json", False))
    home = getattr(args, "home", None)
    if action == "cognitive-schema":
        return _emit(cognitive_schema(), as_json=True, sort_keys=True)
    if action == "semantic-schema":
        return _emit(semantic_control_schema(), as_json=True, sort_keys=True)
    if action == "reconcile":
        input_path = Path(getattr(args, "input", ""))
        try:
            packet = json.loads(input_path.read_text(encoding="utf-8"))
            payload = reconcile_packet(
                packet, evaluation_time=datetime.now(timezone.utc)
            )
        except ReconciliationValidationError as exc:
            payload = validation_error_payload(exc)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
            RecursionError,
            OverflowError,
        ) as exc:
            payload = {
                "ok": False,
                "schema": "PersonalSemanticReconciliationResult.v1",
                "state": "blocked",
                "findings": [
                    {
                        "path": str(input_path),
                        "code": "input_read_error",
                        "message": str(exc),
                    }
                ],
                "boundary": {
                    "runtime_mutation": False,
                    "capability_execution": False,
                    "authority_expansion": False,
                    "external_side_effects": False,
                    "durable_semantic_writeback": False,
                },
            }
        exit_code = _emit(payload, as_json=True, sort_keys=True)
        if exit_code:
            raise SystemExit(exit_code)
        return 0
    if action == "semantic-project":
        input_path = Path(getattr(args, "input", ""))
        try:
            envelope = json.loads(input_path.read_text(encoding="utf-8"))
            evaluation_time_text = getattr(args, "evaluation_time", None)
            evaluation_time = (
                datetime.fromisoformat(evaluation_time_text.replace("Z", "+00:00"))
                if evaluation_time_text
                else datetime.now(timezone.utc)
            )
            if evaluation_time.tzinfo is not None:
                evaluation_time = evaluation_time.astimezone(timezone.utc)
            payload = project_semantic_control(
                envelope,
                evaluation_time=evaluation_time,
            )
        except SemanticControlValidationError as exc:
            payload = semantic_validation_error_payload(exc)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            ValueError,
            RecursionError,
            OverflowError,
        ) as exc:
            payload = semantic_validation_error_payload(
                SemanticControlValidationError([
                    {
                        "path": str(input_path),
                        "code": "input_read_error",
                        "message": str(exc),
                    }
                ])
            )
        exit_code = _emit(payload, as_json=True, sort_keys=True)
        if exit_code:
            raise SystemExit(exit_code)
        return 0
    if action == "doctor":
        return _emit(doctor(home), as_json=as_json)
    if action == "schema":
        return _emit(canonical_schema_status(home), as_json=as_json)
    if action == "audit":
        return _emit(read_only_runtime_audit(home), as_json=as_json)
    if action == "evidence":
        return _emit(evidence_projection_status(home), as_json=as_json)
    if action == "bootstrap-runtime":
        return _emit(
            bootstrap_erg_runtime_state(
                home, authority_gate=getattr(args, "authority_gate", None)
            ),
            as_json=as_json,
        )
    if action == "boundary-coverage":
        return _emit(
            high_risk_boundary_coverage(
                home,
                destructive_canary=bool(getattr(args, "destructive_canary", False)),
            ),
            as_json=as_json,
        )
    if action == "route":
        text = getattr(args, "text", None) or ""
        return _emit(route_shadow(text, home), as_json=as_json)
    if action == "workbench-state":
        try:
            payload = build_task_state_projection(load_tasks_from_file(getattr(args, "input", "")))
        except (OSError, UnicodeError, json.JSONDecodeError, TaskStateProjectionError) as exc:
            payload = {
                "ok": False,
                "schema": "AgentWorkbenchTaskStateProjection.v0",
                "mode": "local_projection_only",
                "error": str(exc),
                "runtime_activation": False,
                "would_execute": False,
                "external_side_effects_executed": False,
            }
        else:
            payload = {"ok": True, **payload}
        return _emit(payload, as_json=as_json)
    if action == "workbench-review-template":
        try:
            payload = build_review_packet_template(
                load_projection_from_file(getattr(args, "input", "")),
                candidate_digest=getattr(args, "candidate_digest", ""),
                changed_files=getattr(args, "changed_file", []) or [],
                required_verification=getattr(args, "required_verification", []) or [],
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ReviewPacketTemplateError) as exc:
            payload = {
                "ok": False,
                "schema": "AgentWorkbenchReviewPacketTemplate.v0",
                "mode": "local_review_template_only",
                "error": str(exc),
                "runtime_activation": False,
                "would_execute": False,
                "external_side_effects_executed": False,
            }
        else:
            payload = {"ok": True, **payload}
        return _emit(payload, as_json=as_json)
    if action == "replay":
        return _emit(
            replay_recent_user_intents(home, limit=getattr(args, "limit", 20)),
            as_json=as_json,
        )
    if action == "report":
        return _emit(
            generate_replay_quality_report(
                home,
                limit=getattr(args, "limit", 20),
                write=getattr(args, "write", False),
            ),
            as_json=as_json,
        )
    if action == "authorize-local-harness":
        return _emit(
            authorize_local_harness(
                authorization_id=getattr(args, "authorization_id", ""),
                intent_id=getattr(args, "intent_id", ""),
                target_ref=getattr(args, "target_ref", ""),
                target_status=getattr(args, "target_status", ""),
                evidence_refs=getattr(args, "evidence_ref", []) or [],
                adapter_state_path=getattr(args, "adapter_state_path", None),
                home=home,
            ),
            as_json=as_json,
        )
    if action == "execute-local-harness":
        return _emit(
            execute_local_harness(
                authorization_id=getattr(args, "authorization_id", ""),
                intent_id=getattr(args, "intent_id", ""),
                target_ref=getattr(args, "target_ref", ""),
                target_status=getattr(args, "target_status", ""),
                evidence_refs=getattr(args, "evidence_ref", []) or [],
                adapter_state_path=getattr(args, "adapter_state_path", None),
                expected_previous_status=getattr(
                    args, "expected_previous_status", None
                ),
                home=home,
            ),
            as_json=as_json,
        )
    if action == "temporal-ledger":
        temporal_action = getattr(args, "temporal_ledger_action", None) or "status"
        if temporal_action == "activate":
            return _emit_temporal(
                activate_temporal_ledger_runtime(
                    home=home,
                    authority_gate=getattr(args, "authority_gate", ""),
                ),
                as_json=as_json,
            )
        if temporal_action == "deactivate":
            return _emit_temporal(
                deactivate_temporal_ledger_runtime(
                    home=home,
                    authority_gate=getattr(args, "authority_gate", ""),
                ),
                as_json=as_json,
            )
        if temporal_action == "inspect":
            return _emit_temporal(
                inspect_temporal_ledger_runtime(
                    home=home,
                    as_of=getattr(args, "as_of", ""),
                ),
                as_json=as_json,
            )
        return _emit_temporal(temporal_ledger_runtime_status(home), as_json=as_json)
    return _emit(status(home), as_json=as_json)


def build_agent_os_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "agent-os",
        help="Inspect Hermes Agent OS Control Plane advisory projections",
        description=(
            "Agent OS Control Plane commands. Most commands are read-only/shadow-only; "
            "execute-local-harness is a governed SSOT-local JSON harness executor "
            "that never writes real taskpool/Kanban/Lark/Telegram state."
        ),
    )
    parser.add_argument(
        "--home",
        default=None,
        help="Hermes home to inspect (default: active HERMES_HOME / ~/.hermes)",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    parser.set_defaults(func=cmd_agent_os)
    subs = parser.add_subparsers(dest="agent_os_action")

    for name, help_text in [
        ("status", "Show Agent OS projection status"),
        ("doctor", "Run fail-closed read-only doctor checks"),
        ("schema", "Validate canonical ERG Runtime schema/read-only projection status"),
        ("audit", "Run read-only ERG Runtime source/divergence audit"),
        ("evidence", "Project read-only audit findings into ERG EvidenceNode records"),
        ("bootstrap-runtime", "Execute exact P4 local ERG runtime bootstrap boundary"),
        (
            "boundary-coverage",
            "Project/read back high-risk Agent OS boundary coverage without unscoped execution",
        ),
        (
            "cognitive-schema",
            "Show the stable read-only personal cognitive reconciliation contract",
        ),
        (
            "semantic-schema",
            "Show the stable three-plane semantic control projection contract",
        ),
    ]:
        p = subs.add_parser(name, help=help_text)
        p.add_argument("--home", default=None, help="Hermes home to inspect")
        p.add_argument("--json", action="store_true", help="Emit JSON")
        if name == "bootstrap-runtime":
            p.add_argument(
                "--authority-gate",
                default=None,
                help="Exact P4 authority gate JSON path",
            )
        if name == "boundary-coverage":
            p.add_argument(
                "--destructive-canary",
                action="store_true",
                help="Run disposable local delete/restore canary smoke",
            )
        p.set_defaults(func=cmd_agent_os)

    reconcile = subs.add_parser(
        "reconcile",
        help="Validate and reconcile one personal semantic goal packet without execution",
    )
    reconcile.add_argument(
        "--input",
        required=True,
        help="Explicit PersonalSemanticReconcileInput.v1 JSON path",
    )
    reconcile.add_argument(
        "--home",
        default=None,
        help="Accepted for CLI consistency; reconciliation does not read it",
    )
    reconcile.add_argument(
        "--json", action="store_true", help="Emit deterministic machine-readable JSON"
    )
    reconcile.set_defaults(func=cmd_agent_os)

    semantic_project = subs.add_parser(
        "semantic-project",
        help="Build a deterministic three-plane semantic projection without execution or writeback",
    )
    semantic_project.add_argument(
        "--input", required=True, help="Explicit SemanticControlEnvelope.v1 JSON path"
    )
    semantic_project.add_argument(
        "--evaluation-time",
        default=None,
        help="Optional timezone-aware ISO-8601 evaluation time for exact replay",
    )
    semantic_project.add_argument(
        "--home",
        default=None,
        help="Accepted for CLI consistency; projection does not read it",
    )
    semantic_project.add_argument(
        "--json", action="store_true", help="Emit deterministic machine-readable JSON"
    )
    semantic_project.set_defaults(func=cmd_agent_os)

    route = subs.add_parser(
        "route",
        help="Compute a shadow CapabilityIntent route for text without execution",
    )
    route.add_argument("text", help="Intent text to route in shadow mode")
    route.add_argument("--home", default=None, help="Hermes home to inspect")
    route.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    route.set_defaults(func=cmd_agent_os)

    workbench_state = subs.add_parser(
        "workbench-state",
        help="Build a local-only Agent Workbench task-state projection from a JSON task list",
    )
    workbench_state.add_argument("--input", required=True, help="JSON list or object with tasks")
    workbench_state.add_argument("--home", default=None, help="Accepted for CLI consistency; not mutated")
    workbench_state.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    workbench_state.set_defaults(func=cmd_agent_os)

    workbench_review = subs.add_parser(
        "workbench-review-template",
        help="Build a local-only review packet template bound to a task-state projection",
    )
    workbench_review.add_argument("--input", required=True, help="AgentWorkbenchTaskStateProjection.v0 JSON")
    workbench_review.add_argument("--candidate-digest", required=True, help="sha256:<hex> candidate digest")
    workbench_review.add_argument("--changed-file", action="append", default=[], help="Changed candidate file; repeatable")
    workbench_review.add_argument("--required-verification", action="append", default=[], help="Required local verification command; repeatable")
    workbench_review.add_argument("--home", default=None, help="Accepted for CLI consistency; not mutated")
    workbench_review.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    workbench_review.set_defaults(func=cmd_agent_os)

    replay = subs.add_parser(
        "replay",
        help="Read-only shadow replay over recent redacted user intents from state.db",
    )
    replay.add_argument("--home", default=None, help="Hermes home to inspect")
    replay.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Recent user messages to replay, capped at 50",
    )
    replay.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    replay.set_defaults(func=cmd_agent_os)

    report = subs.add_parser(
        "report",
        help="Generate replay quality report and authority packet without promotion",
    )
    report.add_argument("--home", default=None, help="Hermes home to inspect")
    report.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Recent user messages to replay, capped at 50",
    )
    report.add_argument(
        "--write",
        action="store_true",
        help="Write local project report artifacts under the Agent OS SSOT",
    )
    report.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    report.set_defaults(func=cmd_agent_os)

    auth = subs.add_parser(
        "authorize-local-harness",
        help="Record a non-executing exact local harness authorization tuple",
    )
    auth.add_argument("--home", default=None, help="Hermes home to inspect")
    auth.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    auth.add_argument(
        "--authorization-id", required=True, help="Exact auth:<id> authorization id"
    )
    auth.add_argument("--intent-id", required=True, help="Exact intent:<id> intent id")
    auth.add_argument(
        "--target-ref", required=True, help="Exact task:<id> harness target ref"
    )
    auth.add_argument(
        "--target-status",
        required=True,
        choices=["pending", "review", "done", "blocked"],
        help="Harness target status",
    )
    auth.add_argument(
        "--evidence-ref",
        action="append",
        default=[],
        help="Evidence reference proving the action; repeatable",
    )
    auth.add_argument(
        "--adapter-state-path",
        default=None,
        help="Optional state JSON path under Agent OS 08-executor",
    )
    auth.set_defaults(func=cmd_agent_os)

    harness = subs.add_parser(
        "execute-local-harness",
        help="Execute a governed local JSON harness write and receipt readback only",
    )
    harness.add_argument("--home", default=None, help="Hermes home to inspect")
    harness.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    harness.add_argument(
        "--authorization-id", required=True, help="Exact auth:<id> authorization id"
    )
    harness.add_argument(
        "--intent-id", required=True, help="Exact intent:<id> intent id"
    )
    harness.add_argument(
        "--target-ref", required=True, help="Exact task:<id> harness target ref"
    )
    harness.add_argument(
        "--target-status",
        required=True,
        choices=["pending", "review", "done", "blocked"],
        help="Harness target status",
    )
    harness.add_argument(
        "--evidence-ref",
        action="append",
        default=[],
        help="Evidence reference proving the action; repeatable",
    )
    harness.add_argument(
        "--expected-previous-status",
        default=None,
        help="Optional expected current status for drift guard",
    )
    harness.add_argument(
        "--adapter-state-path",
        default=None,
        help="Optional state JSON path under Agent OS 08-executor",
    )
    harness.set_defaults(func=cmd_agent_os)

    temporal = subs.add_parser(
        "temporal-ledger",
        help="Operate the digest-bound local Temporal Commitment Ledger runtime",
    )
    temporal.set_defaults(func=cmd_agent_os)
    temporal_subs = temporal.add_subparsers(dest="temporal_ledger_action")

    temporal_status = temporal_subs.add_parser(
        "status", help="Read back active runtime state and verify exact module bytes"
    )
    temporal_status.add_argument("--home", default=None, help="Hermes home to inspect")
    temporal_status.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    temporal_status.set_defaults(func=cmd_agent_os)

    for temporal_action in ("activate", "deactivate"):
        action_parser = temporal_subs.add_parser(
            temporal_action,
            help=f"{temporal_action.title()} the profile-local runtime using an exact user AuthorityGrant",
        )
        action_parser.add_argument("--home", default=None, help="Hermes home to mutate")
        action_parser.add_argument(
            "--json", action="store_true", help="Emit machine-readable JSON"
        )
        action_parser.add_argument(
            "--authority-gate",
            required=True,
            help="Exact unexpired TemporalCommitmentRuntimeAuthority.v1 JSON path",
        )
        action_parser.set_defaults(func=cmd_agent_os)

    temporal_inspect = temporal_subs.add_parser(
        "inspect", help="Inspect the active canonical ledger without mutation"
    )
    temporal_inspect.add_argument("--home", default=None, help="Hermes home to inspect")
    temporal_inspect.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON"
    )
    temporal_inspect.add_argument(
        "--as-of", required=True, help="Timezone-aware ISO-8601 projection time"
    )
    temporal_inspect.set_defaults(func=cmd_agent_os)
