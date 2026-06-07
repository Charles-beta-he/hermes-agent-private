"""Control Tower dashboard plugin — read-only backend API routes.

Mounted at /api/plugins/control-tower/ by the dashboard plugin system.
The plugin is intentionally read-only in Phase 1: it exposes a snapshot over the
Control Tower Ledger and does not mutate task lifecycle state.
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
    """Record a non-executing policy projection for a dry-run executor plan."""
    return control_tower.review_executor_plan_policy(
        plan_id=plan_id,
        actor_type=req.actor_type,
        actor_id=req.actor_id,
        source_surface="dashboard",
    )
