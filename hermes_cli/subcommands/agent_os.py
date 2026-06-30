"""``hermes agent-os`` advisory readback command.

This subcommand is the first runtime-visible surface for the Hermes Agent OS
Control Plane project. It is intentionally read-only / shadow-only: it exposes
status, doctor, and intent-routing advice from project artifacts without
invoking capabilities or changing lifecycle state.
"""
from __future__ import annotations

import argparse
import json
from typing import Any

from hermes_cli.agent_os_control_plane import (
    authorize_local_harness,
    canonical_schema_status,
    doctor,
    evidence_projection_status,
    execute_local_harness,
    format_summary,
    generate_replay_quality_report,
    read_only_runtime_audit,
    replay_recent_user_intents,
    route_shadow,
    status,
)


def _emit(payload: dict[str, Any], *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_summary(payload))
    return 0 if payload.get("ok", False) else 1


def cmd_agent_os(args: argparse.Namespace) -> int:
    action = getattr(args, "agent_os_action", None) or "status"
    as_json = bool(getattr(args, "json", False))
    home = getattr(args, "home", None)
    if action == "doctor":
        return _emit(doctor(home), as_json=as_json)
    if action == "schema":
        return _emit(canonical_schema_status(home), as_json=as_json)
    if action == "audit":
        return _emit(read_only_runtime_audit(home), as_json=as_json)
    if action == "evidence":
        return _emit(evidence_projection_status(home), as_json=as_json)
    if action == "route":
        text = getattr(args, "text", None) or ""
        return _emit(route_shadow(text, home), as_json=as_json)
    if action == "replay":
        return _emit(replay_recent_user_intents(home, limit=getattr(args, "limit", 20)), as_json=as_json)
    if action == "report":
        return _emit(generate_replay_quality_report(home, limit=getattr(args, "limit", 20), write=getattr(args, "write", False)), as_json=as_json)
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
                expected_previous_status=getattr(args, "expected_previous_status", None),
                home=home,
            ),
            as_json=as_json,
        )
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
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.set_defaults(func=cmd_agent_os)
    subs = parser.add_subparsers(dest="agent_os_action")

    for name, help_text in [
        ("status", "Show Agent OS projection status"),
        ("doctor", "Run fail-closed read-only doctor checks"),
        ("schema", "Validate canonical ERG Runtime schema/read-only projection status"),
        ("audit", "Run read-only ERG Runtime source/divergence audit"),
        ("evidence", "Project read-only audit findings into ERG EvidenceNode records"),
    ]:
        p = subs.add_parser(name, help=help_text)
        p.add_argument("--home", default=None, help="Hermes home to inspect")
        p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
        p.set_defaults(func=cmd_agent_os)

    route = subs.add_parser(
        "route",
        help="Compute a shadow CapabilityIntent route for text without execution",
    )
    route.add_argument("text", help="Intent text to route in shadow mode")
    route.add_argument("--home", default=None, help="Hermes home to inspect")
    route.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    route.set_defaults(func=cmd_agent_os)

    replay = subs.add_parser(
        "replay",
        help="Read-only shadow replay over recent redacted user intents from state.db",
    )
    replay.add_argument("--home", default=None, help="Hermes home to inspect")
    replay.add_argument("--limit", type=int, default=20, help="Recent user messages to replay, capped at 50")
    replay.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    replay.set_defaults(func=cmd_agent_os)

    report = subs.add_parser(
        "report",
        help="Generate replay quality report and authority packet without promotion",
    )
    report.add_argument("--home", default=None, help="Hermes home to inspect")
    report.add_argument("--limit", type=int, default=20, help="Recent user messages to replay, capped at 50")
    report.add_argument("--write", action="store_true", help="Write local project report artifacts under the Agent OS SSOT")
    report.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    report.set_defaults(func=cmd_agent_os)

    auth = subs.add_parser(
        "authorize-local-harness",
        help="Record a non-executing exact local harness authorization tuple",
    )
    auth.add_argument("--home", default=None, help="Hermes home to inspect")
    auth.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    auth.add_argument("--authorization-id", required=True, help="Exact auth:<id> authorization id")
    auth.add_argument("--intent-id", required=True, help="Exact intent:<id> intent id")
    auth.add_argument("--target-ref", required=True, help="Exact task:<id> harness target ref")
    auth.add_argument("--target-status", required=True, choices=["pending", "review", "done", "blocked"], help="Harness target status")
    auth.add_argument("--evidence-ref", action="append", default=[], help="Evidence reference proving the action; repeatable")
    auth.add_argument("--adapter-state-path", default=None, help="Optional state JSON path under Agent OS 08-executor")
    auth.set_defaults(func=cmd_agent_os)

    harness = subs.add_parser(
        "execute-local-harness",
        help="Execute a governed local JSON harness write and receipt readback only",
    )
    harness.add_argument("--home", default=None, help="Hermes home to inspect")
    harness.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    harness.add_argument("--authorization-id", required=True, help="Exact auth:<id> authorization id")
    harness.add_argument("--intent-id", required=True, help="Exact intent:<id> intent id")
    harness.add_argument("--target-ref", required=True, help="Exact task:<id> harness target ref")
    harness.add_argument("--target-status", required=True, choices=["pending", "review", "done", "blocked"], help="Harness target status")
    harness.add_argument("--evidence-ref", action="append", default=[], help="Evidence reference proving the action; repeatable")
    harness.add_argument("--expected-previous-status", default=None, help="Optional expected current status for drift guard")
    harness.add_argument("--adapter-state-path", default=None, help="Optional state JSON path under Agent OS 08-executor")
    harness.set_defaults(func=cmd_agent_os)
