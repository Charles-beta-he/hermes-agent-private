"""Event-governed Modular Workflow Kernel."""

from workflow_kernel.workflow_compensation import (
    WorkflowCompensationError,
    prepare_workflow_compensation_v1,
)
from workflow_kernel.workflow_execution_event_ledger import (
    WorkflowExecutionEventLedgerError,
    WorkflowExecutionLedgerSink,
    compare_rfc3339_instants_v1,
    derive_execution_approval_digest_v1,
    derive_execution_ids_v1,
    read_workflow_execution_events,
    replay_workflow_execution_events_v1,
)
from workflow_kernel.workflow_graph_runtime import (
    LocalExecutionGrant,
    RuntimeExecutionSession,
    RuntimeTaskBinding,
    TaskContext,
    WorkflowGraphRuntimeError,
    derive_runtime_apply_approval_digest_v1,
    derive_runtime_binding_digest_v1,
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
    "durable_execution_event_ledger",
    "lease_fencing_stale_writer_gate",
    "policy_bound_dry_run_apply",
    "explicit_compensation_package",
)

__all__ = [
    "PHASE",
    "RUNTIME_SWITCH_ENABLED",
    "CAPABILITIES",
    "LocalExecutionGrant",
    "RuntimeExecutionSession",
    "RuntimeTaskBinding",
    "TaskContext",
    "WorkflowCompensationError",
    "WorkflowExecutionEventLedgerError",
    "WorkflowExecutionLedgerSink",
    "WorkflowGraphRuntimeError",
    "compare_rfc3339_instants_v1",
    "derive_execution_approval_digest_v1",
    "derive_execution_ids_v1",
    "derive_runtime_apply_approval_digest_v1",
    "derive_runtime_binding_digest_v1",
    "execute_workflow_plan_v1",
    "prepare_workflow_compensation_v1",
    "read_workflow_execution_events",
    "replay_workflow_execution_events_v1",
]
