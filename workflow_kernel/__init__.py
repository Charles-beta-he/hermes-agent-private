"""Event-governed Modular Workflow Kernel."""

from workflow_kernel.workflow_graph_runtime import (
    LocalExecutionGrant,
    RuntimeTaskBinding,
    TaskContext,
    WorkflowGraphRuntimeError,
    execute_workflow_plan_v1,
)


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
    "workflow_execution_plan_local_graph_runtime",
    "dependency_driven_scheduler",
)

__all__ = [
    "PHASE",
    "RUNTIME_SWITCH_ENABLED",
    "CAPABILITIES",
    "LocalExecutionGrant",
    "RuntimeTaskBinding",
    "TaskContext",
    "WorkflowGraphRuntimeError",
    "execute_workflow_plan_v1",
]
