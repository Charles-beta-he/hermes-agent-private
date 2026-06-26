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

from hermes_cli.agent_os_control_plane import doctor, format_summary, generate_replay_quality_report, replay_recent_user_intents, route_shadow, status


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
    if action == "route":
        text = getattr(args, "text", None) or ""
        return _emit(route_shadow(text, home), as_json=as_json)
    if action == "replay":
        return _emit(replay_recent_user_intents(home, limit=getattr(args, "limit", 20)), as_json=as_json)
    if action == "report":
        return _emit(generate_replay_quality_report(home, limit=getattr(args, "limit", 20), write=getattr(args, "write", False)), as_json=as_json)
    return _emit(status(home), as_json=as_json)


def build_agent_os_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "agent-os",
        help="Inspect Hermes Agent OS Control Plane advisory projections",
        description=(
            "Read-only/shadow-only Agent OS Control Plane commands. These expose "
            "CapabilityGraph projection status, doctor checks, and shadow intent "
            "routing without invoking tools, changing runtime routing, mutating "
            "task/mission lifecycle, or sending external messages."
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
