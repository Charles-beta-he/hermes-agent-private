"""``hermes doctor`` subcommand parser.

Extracted verbatim from ``hermes_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_doctor_parser(subparsers, *, cmd_doctor: Callable) -> None:
    """Attach the ``doctor`` subcommand to ``subparsers``."""
    # =========================================================================
    # doctor command
    # =========================================================================
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check configuration and dependencies",
        description="Diagnose issues with Hermes Agent setup",
    )
    doctor_parser.add_argument(
        "--fix", action="store_true", help="Attempt to fix issues automatically"
    )
    doctor_parser.add_argument(
        "--external-data",
        action="store_true",
        help=(
            "Run only the side-effect-free external-data public-read capability "
            "doctor and exit"
        ),
    )
    doctor_parser.add_argument(
        "--provider-gateway-observability",
        action="store_true",
        help=(
            "Run only the side-effect-free local provider/gateway observability "
            "doctor projection and exit"
        ),
    )
    doctor_parser.add_argument(
        "--provider-gateway-event-log",
        default=None,
        help="With --provider-gateway-observability, read this local JSONL event log",
    )
    doctor_parser.add_argument(
        "--self-evolution",
        action="store_true",
        help=(
            "Run only the default-off, proposal-only self-evolution readiness "
            "doctor and exit"
        ),
    )
    doctor_parser.add_argument(
        "--self-evolution-evidence",
        default=None,
        help="With --self-evolution, read this local Raven/EverOS evidence JSON",
    )
    doctor_parser.add_argument(
        "--self-evolution-output",
        default=None,
        help=(
            "With --self-evolution, materialize a local proposal receipt JSON at "
            "this path instead of only printing readiness"
        ),
    )
    doctor_parser.add_argument(
        "--self-evolution-stage-manifest-output",
        default=None,
        help=(
            "With --self-evolution, materialize a read-only stage manifest JSON "
            "for downstream dashboard/taskpool projections"
        ),
    )
    doctor_parser.add_argument(
        "--self-evolution-candidate-output",
        default=None,
        help=(
            "With --self-evolution, materialize a proposal-only experience-ratchet "
            "candidate pack and dry-run skill diff JSON at this path"
        ),
    )
    doctor_parser.add_argument(
        "--self-evolution-candidate-input",
        default=None,
        help="With --self-evolution patch-packet modes, read this candidate pack JSON",
    )
    doctor_parser.add_argument(
        "--self-evolution-patch-packet-output",
        default=None,
        help="With --self-evolution, materialize a fixture-only patch packet JSON at this path",
    )
    doctor_parser.add_argument(
        "--self-evolution-fixture-skill-dir",
        default=None,
        help="With --self-evolution and --self-evolution-patch-packet-output, apply packet to this fixture skill dir only",
    )
    doctor_parser.add_argument(
        "--self-evolution-patch-packet-input",
        default=None,
        help="With promotion-gate/apply modes, read this patch packet JSON",
    )
    doctor_parser.add_argument(
        "--self-evolution-promotion-gate-output",
        default=None,
        help="With --self-evolution, materialize an exact promotion gate packet JSON at this path",
    )
    doctor_parser.add_argument(
        "--self-evolution-promotion-gate-input",
        default=None,
        help="With real-apply mode, read this exact promotion gate packet JSON",
    )
    doctor_parser.add_argument(
        "--self-evolution-real-apply-receipt-output",
        default=None,
        help="With real-apply mode, write the apply/block receipt JSON at this path",
    )
    doctor_parser.add_argument(
        "--self-evolution-target-skill-dir",
        default=None,
        help="Exact skill directory target for promotion gate/apply modes",
    )
    doctor_parser.add_argument(
        "--self-evolution-fixture-receipt",
        default=None,
        help="Fixture apply receipt path referenced by the promotion gate",
    )
    doctor_parser.add_argument(
        "--execute-real-skill-write",
        action="store_true",
        help="DANGEROUS: with exact gate, allow real target skill write; default is closed",
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        help=(
            "With --external-data, --provider-gateway-observability, or "
            "--self-evolution, emit the report as JSON"
        ),
    )
    doctor_parser.add_argument(
        "--ack",
        metavar="ADVISORY_ID",
        default=None,
        help=(
            "Acknowledge a security advisory by ID and exit. After ack, the "
            "advisory will no longer trigger startup banners. Run `hermes "
            "doctor` first to see active advisories and their IDs."
        ),
    )
    doctor_parser.set_defaults(func=cmd_doctor)
