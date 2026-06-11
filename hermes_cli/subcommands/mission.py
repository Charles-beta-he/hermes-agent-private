"""``hermes mission`` subcommand parser.

Missions are default-off, evidence-led objective contracts for long-running
high-level directions.  The parser delegates behavior to ``hermes_cli.missions``
to keep main.py wiring thin.
"""

from __future__ import annotations

from hermes_cli.missions import cmd_mission


def build_mission_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "mission",
        help="Manage long-running objective missions (default-off)",
        description=(
            "Create and inspect durable Mission contracts for high-level directions. "
            "Missions are default-off, budget-bound, evidence-led, and block on "
            "human decisions before risky side effects."
        ),
    )
    parser.add_argument("--db", help="Override missions SQLite DB path (test/debug)")
    sub = parser.add_subparsers(dest="mission_command")

    create = sub.add_parser("create", help="Create a default-off mission objective")
    create.add_argument("direction", help="High-level direction/objective to pursue")
    create.add_argument("--cadence", required=True, help="Stage cadence, e.g. 'every 6h' or '0 9 * * *'")
    create.add_argument("--deliver", default="local", help="Stage report target label (default: local)")
    create.add_argument("--max-ticks", type=int, required=True, help="Hard maximum executable mission ticks (required)")
    create.add_argument("--max-turns-per-tick", type=int, help="Soft turn budget to embed in tick prompt")
    create.add_argument("--max-total-usd", type=float, help="Cost budget marker recorded in prompts; tick count is the enforced boundary")
    create.add_argument("--constraint", action="append", help="Mission constraint. Repeatable")
    create.add_argument("--non-goal", action="append", help="Mission non-goal/red line. Repeatable")
    create.add_argument("--success", action="append", help="Mission success criterion. Repeatable")
    create.add_argument("--allow-action", action="append", help="Explicitly allowed action class. Repeatable")
    create.add_argument("--block-action", action="append", help="Blocked action class. Defaults to safe governance list")
    create.add_argument("--enable", action="store_true", help="Start active immediately; default is paused/default-off")
    create.add_argument("--json", action="store_true", help="Emit JSON")
    create.set_defaults(func=cmd_mission)

    list_cmd = sub.add_parser("list", aliases=["ls"], help="List active/paused missions")
    list_cmd.add_argument("--all", action="store_true", help="Include done/cancelled missions")
    list_cmd.add_argument("--json", action="store_true", help="Emit JSON")
    list_cmd.set_defaults(func=cmd_mission)

    inbox = sub.add_parser("inbox", help="List open mission decisions that need user input")
    inbox.add_argument("--all", action="store_true", help="Include decisions attached to terminal missions")
    inbox.add_argument("--json", action="store_true", help="Emit JSON")
    inbox.set_defaults(func=cmd_mission)

    board = sub.add_parser("board", help="Show mission control board with budget, cron, and next action")
    board.add_argument("--all", action="store_true", help="Include done/cancelled missions")
    board.add_argument("--json", action="store_true", help="Emit JSON")
    board.set_defaults(func=cmd_mission)

    doctor = sub.add_parser("doctor", help="Check whether a mission can actually keep advancing")
    doctor.add_argument("mission_id")
    doctor.add_argument("--json", action="store_true", help="Emit JSON")
    doctor.set_defaults(func=cmd_mission)

    signals = sub.add_parser("signals", help="List pending event-driven mission flow signals")
    signals.add_argument("mission_id")
    signals.add_argument("--json", action="store_true", help="Emit JSON")
    signals.set_defaults(func=cmd_mission)

    dispatch_events = sub.add_parser("dispatch-events", help="Trigger the installed mission cron job for pending flow signals")
    dispatch_events.add_argument("mission_id")
    dispatch_events.add_argument("--json", action="store_true", help="Emit JSON")
    dispatch_events.set_defaults(func=cmd_mission)

    status = sub.add_parser("status", help="Show mission state, open decisions, and recent events")
    status.add_argument("mission_id")
    status.add_argument("--limit", type=int, default=20, help="Recent event limit")
    status.add_argument("--json", action="store_true", help="Emit JSON")
    status.set_defaults(func=cmd_mission)

    for name, help_text in [
        ("pause", "Pause a mission"),
        ("resume", "Resume a mission if no open decisions remain"),
        ("cancel", "Cancel a mission"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("mission_id")
        p.add_argument("--reason", help="Audit reason")
        p.set_defaults(func=cmd_mission)

    ask = sub.add_parser("ask", help="Create a human decision request and block the mission")
    ask.add_argument("mission_id")
    ask.add_argument("question")
    ask.add_argument("--option", action="append", help="Decision option. Repeatable")
    ask.add_argument("--recommend", help="Recommended option")
    ask.add_argument("--json", action="store_true", help="Emit JSON")
    ask.set_defaults(func=cmd_mission)

    handoff = sub.add_parser("handoff", help="Create a human-intervention decision gate and block the mission")
    handoff.add_argument("mission_id")
    handoff.add_argument("--question", required=True, help="Decision question")
    handoff.add_argument("--option", action="append", help="Decision option. Repeatable")
    handoff.add_argument("--recommend", help="Recommended option")
    handoff.add_argument("--json", action="store_true", help="Emit JSON")
    handoff.set_defaults(func=cmd_mission)

    stage = sub.add_parser("stage-report", help="Append a durable evidence-backed mission stage report")
    stage.add_argument("mission_id")
    stage.add_argument("--summary", required=True, help="Report summary")
    stage.add_argument("--evidence", action="append", help="Evidence path, command, or observed result. Repeatable")
    stage.add_argument("--risk", action="append", help="Risk or caveat. Repeatable")
    stage.add_argument("--next-step", help="Next bounded step")
    stage.add_argument("--needs-user", action="store_true", help="Mark report as requiring user input")
    stage.add_argument("--json", action="store_true", help="Emit JSON")
    stage.set_defaults(func=cmd_mission)

    decide = sub.add_parser("decide", help="Answer a decision request and resume its mission")
    decide.add_argument("decision_id")
    decide.add_argument("answer", nargs="+", help="Decision answer text")
    decide.add_argument("--json", action="store_true", help="Emit JSON")
    decide.set_defaults(func=cmd_mission)

    tick = sub.add_parser("tick", help="Generate one bounded mission tick prompt")
    tick.add_argument("mission_id")
    tick.add_argument(
        "--create-prompt-event",
        action="store_true",
        help="Record a prompt-created event instead of dry-run event; still does not execute the agent",
    )
    tick.add_argument("--json", action="store_true", help="Emit JSON")
    tick.set_defaults(func=cmd_mission)

    install = sub.add_parser("install-cron", help="Install an agent cron job that advances a mission on its cadence")
    install.add_argument("mission_id")
    install.add_argument("--schedule", help="Override the mission cadence for the cron job")
    install.add_argument("--deliver", help="Override the mission deliver target for cron output")
    install.add_argument("--name", help="Cron job name")
    install.add_argument("--profile", help="Hermes profile for the cron job")
    install.add_argument("--toolset", action="append", help="Restrict cron agent toolsets. Repeatable")
    install.add_argument("--json", action="store_true", help="Emit JSON")
    install.set_defaults(func=cmd_mission)

    report = sub.add_parser("report", help="Print a stage report from mission ledger events")
    report.add_argument("mission_id")
    report.add_argument("--limit", type=int, default=20, help="Recent event limit")
    report.set_defaults(func=cmd_mission)

    parser.set_defaults(func=cmd_mission)
