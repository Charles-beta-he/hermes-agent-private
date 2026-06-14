"""Static workflow-kernel registry for bounded no-runtime validation.

This module is intentionally data-only: it does not execute workflow modules,
call models, mutate adapters, or perform external side effects.  It captures the
phase-1 contract approved in the taskpool governance note so validators and
future import gates have one local source of truth.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

SCHEMA = "workflow_kernel_static_registry_v1"
WORKFLOW_CONTRACT = "modular-workflow-kernel-static-registry-validator"
PHASE = "phase1_static_no_runtime"

EVIDENCE_PREFIXES = ("artifact:", "command:", "file:", "proof:", "classification:", "not_applicable:")
HUMAN_GATE_PACKET_FIELDS = (
    "decision_id",
    "scope",
    "requested_authority",
    "digest",
    "expires_at",
    "operator",
)
IMPORT_GUARDS = (
    "all_modules_have_evidence_required",
    "all_modules_have_model_policy",
    "evidence_prefixes_valid",
    "human_gate_packet_fields_present",
    "runtime_disabled_in_phase1",
    "workflow_contract_valid",
)

MODULE_NAMES = (
    "goal_calibrator.v1",
    "source_research.v1",
    "acceptance_compiler.v1",
    "evidence_verifier.v1",
    "decision_synthesizer.v1",
)

_ROUTE_BY_MODULE = {
    "goal_calibrator.v1": "local_static_governance_planner",
    "source_research.v1": "local_static_source_mapper",
    "acceptance_compiler.v1": "local_static_acceptance_compiler",
    "evidence_verifier.v1": "local_static_evidence_verifier",
    "decision_synthesizer.v1": "local_static_decision_synthesizer",
}


def _module_contract(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "contract_id": f"module_contract:{name}",
        "route_alias": f"model_route:{name}",
        "evidence_required": True,
        "model_policy": {
            "mode": "deferred_human_gate_required",
            "allowed_in_phase1": False,
            "route_alias": _ROUTE_BY_MODULE[name],
        },
        "required_evidence_prefixes": list(EVIDENCE_PREFIXES),
        "runtime_allowed": False,
        "adapter_write_allowed": False,
        "external_side_effects_allowed": False,
    }


STATIC_REGISTRY: dict[str, Any] = {
    "schema": SCHEMA,
    "workflow_contract": WORKFLOW_CONTRACT,
    "phase": PHASE,
    "source_evidence": [
        "artifact:/Users/charles/.hermes/docs/private/workflow-kernel-candidate-governance-20260614.md",
    ],
    "safety_flags": {
        "runtime_enabled": False,
        "write_through_enabled": False,
        "external_side_effects_enabled": False,
        "model_calls_enabled": False,
        "adapter_writes_enabled": False,
    },
    "import_guards": {guard: True for guard in IMPORT_GUARDS},
    "evidence_prefixes": list(EVIDENCE_PREFIXES),
    "human_gate_packet_required_fields": list(HUMAN_GATE_PACKET_FIELDS),
    "modules": [_module_contract(name) for name in MODULE_NAMES],
    "model_routes": [
        {
            "alias": f"model_route:{name}",
            "module": name,
            "provider": "human_gate_required",
            "model": "deferred",
            "runtime_allowed": False,
            "policy": _ROUTE_BY_MODULE[name],
        }
        for name in MODULE_NAMES
    ],
    "red_lines": [
        "no runtime executor enablement",
        "no model calls",
        "no adapter write-through",
        "no external side effects",
    ],
}


def registry_snapshot() -> dict[str, Any]:
    """Return a defensive copy of the static registry."""
    return deepcopy(STATIC_REGISTRY)


__all__ = [
    "EVIDENCE_PREFIXES",
    "HUMAN_GATE_PACKET_FIELDS",
    "IMPORT_GUARDS",
    "MODULE_NAMES",
    "PHASE",
    "SCHEMA",
    "STATIC_REGISTRY",
    "WORKFLOW_CONTRACT",
    "registry_snapshot",
]
