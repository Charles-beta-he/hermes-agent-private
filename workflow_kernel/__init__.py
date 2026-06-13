"""Event-governed Modular Workflow Kernel scaffold.

Phase 26 is scaffold/compatibility only: existing scripts remain the public CLI
entrypoints and no runtime switch is enabled here.
"""

PHASE = "45"
RUNTIME_SWITCH_ENABLED = True
CAPABILITIES = (
    "kernel_scaffold_compat_only",
    "canonical_json_io",
    "artifact_sha256",
    "contract_packets",
    "append_only_ledger",
    "runtime_switch_readiness",
    "runtime_auto_enabled",
)

__all__ = ["PHASE", "RUNTIME_SWITCH_ENABLED", "CAPABILITIES"]
