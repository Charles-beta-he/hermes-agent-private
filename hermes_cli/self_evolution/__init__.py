"""Reusable self-evolution absorption API for Hermes CLI surfaces."""

from __future__ import annotations

from .candidate import build_self_evolution_candidate_pack, materialize_self_evolution_candidate_pack
from .patch_packet import (
    apply_self_evolution_patch_to_fixture,
    build_self_evolution_patch_packet,
    materialize_self_evolution_patch_packet,
    verify_self_evolution_patch_packet,
)
from .promotion_gate import (
    apply_self_evolution_patch_to_real_skill,
    build_self_evolution_promotion_gate,
    materialize_self_evolution_promotion_gate,
)
from .proposal import (
    build_self_evolution_report,
    materialize_self_evolution_proposal,
    print_self_evolution_report,
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
