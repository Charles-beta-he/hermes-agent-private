"""``hermes portfolio`` subcommand parser and handlers."""
from __future__ import annotations

import argparse
import json
from typing import Any

from hermes_cli.portfolio_os import (
    format_summary,
    load_profiles,
    portfolio_status,
    run_doctor,
    strict_verify,
)


def _emit(payload: dict[str, Any], *, as_json: bool) -> int:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_summary(payload))
    return 0 if payload.get("ok", False) else 1


def cmd_portfolio(args: argparse.Namespace) -> int:
    action = getattr(args, "portfolio_action", None) or "status"
    as_json = bool(getattr(args, "json", False))
    home = getattr(args, "home", None)
    if action == "doctor":
        return _emit(run_doctor(home), as_json=as_json)
    if action == "verify":
        return _emit(strict_verify(home), as_json=as_json)
    if action == "projects":
        profiles = load_profiles(home)
        payload = {
            "ok": True,
            "profile_count": len(profiles),
            "projects": [
                {
                    "id": pid,
                    "type": profile.get("project_type"),
                    "ssot_root": profile.get("ssot_root"),
                    "progress_ledger": profile.get("progress_ledger"),
                }
                for pid, profile in sorted(profiles.items())
            ],
        }
        if as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"Portfolio projects: {payload['profile_count']}")
            for project in payload["projects"]:
                print(f"- {project['id']} ({project.get('type') or 'unknown'})")
        return 0
    return _emit(portfolio_status(home), as_json=as_json)


def build_portfolio_parser(subparsers) -> None:
    parser = subparsers.add_parser(
        "portfolio",
        help="Inspect Project Portfolio OS profiles, ledgers, and relation graph",
        description=(
            "Read-only Project Portfolio OS commands. These inspect project "
            "profiles, progress ledgers, relation graph, and evidence-gated "
            "ratchet status without modifying project code."
        ),
    )
    parser.add_argument(
        "--home",
        default=None,
        help="Hermes home to inspect (default: active HERMES_HOME / ~/.hermes)",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.set_defaults(func=cmd_portfolio)
    subs = parser.add_subparsers(dest="portfolio_action")

    for name, help_text in [
        ("status", "Show portfolio health summary and project list"),
        ("doctor", "Run portfolio doctor checks"),
        ("verify", "Run strict production verification checks"),
        ("projects", "List project profiles known to Portfolio OS"),
    ]:
        p = subs.add_parser(name, help=help_text)
        p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
        p.set_defaults(func=cmd_portfolio)
