"""Runtime switch readiness compiler for the workflow kernel.

This module makes the kernel switchable in a compatibility-shim sense while
keeping default runtime behavior disabled until an explicit enable gate.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from workflow_kernel import CAPABILITIES, RUNTIME_SWITCH_ENABLED
from workflow_kernel.contracts import contract_packet

COMPATIBILITY_CLIS = [
    "scripts/workflow_architecture_migration_evaluation.py",
    "scripts/cron_provider_health_gate.py",
    "workflow_kernel/cron_wrappers.py",
]

REQUIRED_TESTS = [
    "tests/workflow_kernel/test_kernel_scaffold.py",
    "tests/workflow_kernel/test_runtime_switch_readiness.py",
    "tests/scripts/test_workflow_architecture_migration_evaluation.py",
    "tests/scripts/test_cron_provider_health_gate.py",
]

BLOCKED_CAPABILITIES = [
    "cron_mutation",
    "external_dispatch_expansion",
    "autorun_enablement",
    "config_migration",
]


def compile_runtime_readiness(root: str | Path = ".") -> dict[str, Any]:
    base = Path(root)
    cli_status = {path: (base / path).exists() for path in COMPATIBILITY_CLIS}
    test_status = {path: (base / path).exists() for path in REQUIRED_TESTS}
    ready = all(cli_status.values()) and all(test_status.values()) and RUNTIME_SWITCH_ENABLED is False
    payload = {
        "switch_mode": "compatibility_shim",
        "runtime_switch_enabled_by_default": RUNTIME_SWITCH_ENABLED,
        "capabilities": list(CAPABILITIES),
        "compatibility_clis": COMPATIBILITY_CLIS,
        "required_tests": REQUIRED_TESTS,
        "blocked_capabilities": BLOCKED_CAPABILITIES,
        "cli_status": cli_status,
        "test_status": test_status,
        "explicit_enable_required": True,
    }
    packet = contract_packet(packet_type="runtime_switch_readiness", payload=payload, evidence_refs=["phase26:kernel_scaffold", "phase27:runtime_readiness"])
    return {
        "report_id": "workflow_kernel_runtime_readiness_20260613_phase27",
        "status": "RUNTIME_SWITCH_READY_PENDING_EXPLICIT_ENABLE" if ready else "RUNTIME_SWITCH_NOT_READY",
        "switch_mode": "compatibility_shim",
        "runtime_switch_enabled_by_default": RUNTIME_SWITCH_ENABLED,
        "explicit_enable_required": True,
        "compatibility_clis": COMPATIBILITY_CLIS,
        "required_tests": REQUIRED_TESTS,
        "blocked_capabilities": BLOCKED_CAPABILITIES,
        "readiness_packet": packet,
        "next_closed_boundary": "explicit runtime switch enablement and one-slice wrapper rollout",
    }
