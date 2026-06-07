"""Control Tower dashboard plugin — read-only backend API routes.

Mounted at /api/plugins/control-tower/ by the dashboard plugin system.
The plugin is intentionally read-only in Phase 1: it exposes a snapshot over the
Control Tower Ledger and does not mutate task lifecycle state.
"""

from __future__ import annotations

from fastapi import APIRouter

from hermes_cli import control_tower

router = APIRouter()


@router.get("/snapshot")
def get_snapshot():
    """Return the read-only Control Tower Phase 1 snapshot."""
    return control_tower.build_snapshot()
