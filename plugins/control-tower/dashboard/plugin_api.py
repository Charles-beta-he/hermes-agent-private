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
