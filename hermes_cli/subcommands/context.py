"""`hermes context` parser for Context Engineering edge checks."""

from __future__ import annotations

from typing import Callable


def build_context_parser(subparsers, *, cmd_context: Callable) -> None:
    context_parser = subparsers.add_parser(
        "context",
        help="Context Engineering packet and regression helpers",
        description="Run local-only Context Engineering packet lint and regression fixture checks.",
    )
    context_subparsers = context_parser.add_subparsers(dest="context_command")

    packet_parser = context_subparsers.add_parser(
        "packet",
        help="Context Packet helpers",
    )
    packet_subparsers = packet_parser.add_subparsers(dest="packet_command")
    packet_lint = packet_subparsers.add_parser(
        "lint",
        help="Validate a hermes_context_packet_v1 JSON packet",
    )
    packet_lint.add_argument("packet_path", help="Path to a Context Packet JSON file")
    packet_lint.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    packet_lint.set_defaults(func=cmd_context)
    packet_parser.set_defaults(func=cmd_context)

    eval_parser = context_subparsers.add_parser(
        "eval",
        help="Context regression eval helpers",
    )
    eval_subparsers = eval_parser.add_subparsers(dest="eval_command")
    eval_run = eval_subparsers.add_parser(
        "run",
        help="Run pass/fail Context Packet fixtures under eval/fixtures",
    )
    eval_run.add_argument("--root", default=".", help="Project root containing eval/fixtures/{pass,fail}")
    eval_run.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    eval_run.set_defaults(func=cmd_context)
    eval_parser.set_defaults(func=cmd_context)

    preflight_parser = context_subparsers.add_parser(
        "preflight",
        help="Read-only default-off Context Packet runtime preflight projection",
    )
    preflight_parser.add_argument("--packet", dest="packet_path", default=None, help="Path to Context Packet JSON")
    preflight_parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    preflight_parser.set_defaults(func=cmd_context)

    context_parser.set_defaults(func=cmd_context)
