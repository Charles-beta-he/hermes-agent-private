"""Compatibility wrapper for the modular self-evolution absorption API.

The implementation lives under :mod:`hermes_cli.self_evolution`; this module
preserves the historical import path used by `hermes doctor --self-evolution`
and older tests/scripts.
"""

from __future__ import annotations

from hermes_cli.self_evolution import (
    apply_self_evolution_patch_to_fixture,
    apply_self_evolution_patch_to_real_skill,
    build_self_evolution_candidate_pack,
    build_self_evolution_patch_packet,
    build_self_evolution_promotion_gate,
    build_self_evolution_report,
    materialize_self_evolution_candidate_pack,
    materialize_self_evolution_patch_packet,
    materialize_self_evolution_promotion_gate,
    materialize_self_evolution_proposal,
    print_self_evolution_report,
    verify_self_evolution_patch_packet,
)

__all__ = [
    "apply_self_evolution_patch_to_fixture",
    "apply_self_evolution_patch_to_real_skill",
    "build_self_evolution_candidate_pack",
    "build_self_evolution_patch_packet",
    "build_self_evolution_promotion_gate",
    "build_self_evolution_report",
    "materialize_self_evolution_candidate_pack",
    "materialize_self_evolution_patch_packet",
    "materialize_self_evolution_promotion_gate",
    "materialize_self_evolution_proposal",
    "print_self_evolution_report",
    "verify_self_evolution_patch_packet",
]
