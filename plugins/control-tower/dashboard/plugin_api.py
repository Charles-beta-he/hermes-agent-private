"""Control Tower dashboard plugin — read-only backend API routes.

Mounted at /api/plugins/control-tower/ by the dashboard plugin system.
The plugin exposes ledger projections and controlled operator intent. It may
execute narrow consumers after explicit gates: L0/L1 read-only executor runs and
approved evidence-bound closeout inside the Control Tower ledger. It still never
writes through external taskpool/Kanban adapters.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from hermes_cli import control_tower

router = APIRouter()


class ProposedCommandRequest(BaseModel):
    command_type: str = Field(min_length=1)
    actor_type: str = Field(default="human", min_length=1)
    actor_id: str = Field(default="dashboard", min_length=1)
    target_type: str | None = None
    target_id: str | None = None
    risk_level: str = "L1"
    payload: dict = Field(default_factory=dict)
    idempotency_key: str | None = None


class ApprovalDecisionRequest(BaseModel):
    decision: str = Field(min_length=1)
    actor_type: str = Field(default="human", min_length=1)
    actor_id: str = Field(default="dashboard", min_length=1)
    reason: str = Field(default="", min_length=0)


class ExecutorPlanRequest(BaseModel):
    actor_type: str = Field(default="human", min_length=1)
    actor_id: str = Field(default="dashboard", min_length=1)


class EvidenceRecordRequest(BaseModel):
    task_id: str = Field(min_length=1)
    evidence_type: str = Field(min_length=1)
    maturity: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    producer_type: str = Field(default="human", min_length=1)
    producer_id: str = Field(default="dashboard", min_length=1)
    workflow_run_id: str | None = None
    step_run_id: str | None = None
    artifact_uri: str | None = None
    artifact_hash: str | None = None
    verifier: str | None = None
    acceptance_refs: list[str] = Field(default_factory=list)
    stale_status: str = "fresh"


class EvidenceGateRequest(BaseModel):
    task_id: str = Field(min_length=1)
    risk_level: str = "L1"
    acceptance_refs: list[str] = Field(default_factory=list)
    actor_type: str = Field(default="human", min_length=1)
    actor_id: str = Field(default="dashboard", min_length=1)


class CloseoutAuthorizationRequest(BaseModel):
    actor_type: str = Field(default="human", min_length=1)
    actor_id: str = Field(default="dashboard", min_length=1)
    actor_role: str = Field(default="operator", min_length=1)
    reason: str = Field(default="", min_length=0)
    ttl_hours: int = control_tower.DEFAULT_CLOSEOUT_AUTH_TTL_HOURS


class CloseoutAuthorizationDecisionRequest(BaseModel):
    decision: str = Field(min_length=1)
    actor_type: str = Field(default="human", min_length=1)
    actor_id: str = Field(default="dashboard", min_length=1)
    actor_role: str = Field(default="operator", min_length=1)
    reason: str = Field(default="", min_length=0)


class CloseoutExecutionRequest(BaseModel):
    actor_type: str = Field(default="human", min_length=1)
    actor_id: str = Field(default="dashboard", min_length=1)
    actor_role: str = Field(default="operator", min_length=1)
    reason: str = Field(default="", min_length=0)


class ExternalAdapterWriteIntentRequest(BaseModel):
    adapter_name: str = Field(min_length=1)
    external_ref: str = Field(min_length=1)
    actor_type: str = Field(default="human", min_length=1)
    actor_id: str = Field(default="dashboard", min_length=1)
    actor_role: str = Field(default="operator", min_length=1)
    reason: str = Field(default="", min_length=0)


class ExternalAdapterWriteIntentReviewRequest(BaseModel):
    actor_type: str = Field(default="human", min_length=1)
    actor_id: str = Field(default="dashboard", min_length=1)
    actor_role: str = Field(default="operator", min_length=1)
    reason: str = Field(default="", min_length=0)


@router.get("/snapshot")
def get_snapshot():
    """Return the read-only Control Tower Phase 1 snapshot."""
    return control_tower.build_snapshot()


@router.post("/commands/propose")
def propose_command(req: ProposedCommandRequest):
    """Record a proposed command without executing workflow side effects."""
    return control_tower.propose_command(
        command_type=req.command_type,
        actor_type=req.actor_type,
        actor_id=req.actor_id,
        source_surface="dashboard",
        target_type=req.target_type,
        target_id=req.target_id,
        risk_level=req.risk_level,
        payload=req.payload,
        idempotency_key=req.idempotency_key,
    )


@router.post("/approvals/{approval_id}/decision")
def decide_approval(approval_id: str, req: ApprovalDecisionRequest):
    """Record an approval decision without executing the underlying command."""
    return control_tower.decide_human_approval(
        approval_id=approval_id,
        decision=req.decision,
        actor_type=req.actor_type,
        actor_id=req.actor_id,
        source_surface="dashboard",
        reason=req.reason,
    )


@router.post("/evidence")
def record_evidence(req: EvidenceRecordRequest):
    """Record evidence without closing or mutating task lifecycle state."""
    return control_tower.record_evidence(
        task_id=req.task_id,
        evidence_type=req.evidence_type,
        maturity=req.maturity,
        source_type=req.source_type,
        summary=req.summary,
        producer_type=req.producer_type,
        producer_id=req.producer_id,
        workflow_run_id=req.workflow_run_id,
        step_run_id=req.step_run_id,
        artifact_uri=req.artifact_uri,
        artifact_hash=req.artifact_hash,
        verifier=req.verifier,
        acceptance_refs=req.acceptance_refs,
        stale_status=req.stale_status,
    )


@router.post("/evidence-gates/evaluate")
def evaluate_evidence_gate(req: EvidenceGateRequest):
    """Evaluate evidence adequacy without closing the task."""
    return control_tower.evaluate_evidence_gate(
        task_id=req.task_id,
        risk_level=req.risk_level,
        acceptance_refs=req.acceptance_refs,
        actor_type=req.actor_type,
        actor_id=req.actor_id,
        source_surface="dashboard",
    )


@router.post("/evidence-gates/{evidence_review_id}/closeout-authorization")
def request_closeout_authorization(evidence_review_id: str, req: CloseoutAuthorizationRequest):
    """Request evidence-bound closeout authorization without closing the task."""
    return control_tower.request_closeout_authorization(
        evidence_review_id=evidence_review_id,
        actor_type=req.actor_type,
        actor_id=req.actor_id,
        actor_role=req.actor_role,
        source_surface="dashboard",
        reason=req.reason,
        ttl_hours=req.ttl_hours,
    )


@router.post("/closeout-authorizations/{closeout_authorization_id}/decision")
def decide_closeout_authorization(
    closeout_authorization_id: str,
    req: CloseoutAuthorizationDecisionRequest,
):
    """Decide closeout authorization without mutating lifecycle state."""
    return control_tower.decide_closeout_authorization(
        closeout_authorization_id=closeout_authorization_id,
        decision=req.decision,
        actor_type=req.actor_type,
        actor_id=req.actor_id,
        actor_role=req.actor_role,
        source_surface="dashboard",
        reason=req.reason,
    )


@router.post("/closeout-authorizations/{closeout_authorization_id}/execute")
def execute_closeout_authorization(
    closeout_authorization_id: str,
    req: CloseoutExecutionRequest,
):
    """Execute approved evidence-bound closeout inside the Control Tower ledger only."""
    return control_tower.execute_closeout_authorization(
        closeout_authorization_id=closeout_authorization_id,
        actor_type=req.actor_type,
        actor_id=req.actor_id,
        actor_role=req.actor_role,
        source_surface="dashboard",
        reason=req.reason,
    )


@router.post("/closeout-executions/{closeout_execution_id}/external-write-intent")
def prepare_external_adapter_write_intent(
    closeout_execution_id: str,
    req: ExternalAdapterWriteIntentRequest,
):
    """Prepare a digest-bound external adapter write intent without adapter writes."""
    return control_tower.prepare_external_adapter_write_intent(
        closeout_execution_id=closeout_execution_id,
        adapter_name=req.adapter_name,
        external_ref=req.external_ref,
        actor_type=req.actor_type,
        actor_id=req.actor_id,
        actor_role=req.actor_role,
        source_surface="dashboard",
        reason=req.reason,
    )


@router.post("/external-write-intents/{external_write_intent_id}/review")
def review_external_adapter_write_intent(
    external_write_intent_id: str,
    req: ExternalAdapterWriteIntentReviewRequest,
):
    """Review external adapter write intent readiness without enabling write-through."""
    return control_tower.review_external_adapter_write_intent(
        external_write_intent_id=external_write_intent_id,
        actor_type=req.actor_type,
        actor_id=req.actor_id,
        actor_role=req.actor_role,
        source_surface="dashboard",
        reason=req.reason,
    )


@router.post("/commands/{command_id}/executor-plan")
def generate_executor_plan(command_id: str, req: ExecutorPlanRequest):
    """Generate a dry-run/copy-command plan without executing workflow side effects."""
    return control_tower.generate_executor_plan(
        command_id=command_id,
        actor_type=req.actor_type,
        actor_id=req.actor_id,
        source_surface="dashboard",
    )


@router.post("/executor-plans/{plan_id}/policy-review")
def review_executor_plan_policy(plan_id: str, req: ExecutorPlanRequest):
    """Record a non-mutating read-only executor policy review."""
    return control_tower.review_executor_plan_policy(
        plan_id=plan_id,
        actor_type=req.actor_type,
        actor_id=req.actor_id,
        source_surface="dashboard",
    )


@router.post("/executor-plans/{plan_id}/execute-read-only")
def execute_read_only_executor_plan(plan_id: str, req: ExecutorPlanRequest):
    """Run the whitelisted read-only executor consumer without adapter writes."""
    return control_tower.execute_read_only_executor_plan(
        plan_id=plan_id,
        actor_type=req.actor_type,
        actor_id=req.actor_id,
        source_surface="dashboard",
    )
