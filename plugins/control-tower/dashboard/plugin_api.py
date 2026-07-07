"""Control Tower dashboard plugin — read-only backend API routes.

Mounted at /api/plugins/control-tower/ by the dashboard plugin system.
The plugin exposes ledger projections and controlled operator intent. It may
execute narrow consumers after explicit gates: L0/L1 read-only executor runs,
approved evidence-bound closeout inside the Control Tower ledger, and explicitly
approved digest-bound Kanban/taskpool closeout write-through plus digest-bound
rollback/reopen of those writes. Conflicting evidence override is exposed only
as a non-executing, ledger-gated admin projection; approved overrides are not
consumed by closeout execution in this slice.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from hermes_cli import control_tower
from hermes_cli.missions import MissionBudget, MissionStore, decision_to_dict, mission_to_dict

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


class ConflictingEvidenceOverrideAuthorizationRequest(BaseModel):
    actor_type: str = Field(default="human", min_length=1)
    actor_id: str = Field(default="dashboard", min_length=1)
    actor_role: str = Field(min_length=1)
    reason: str = Field(default="", min_length=0)
    ttl_hours: int = control_tower.DEFAULT_CLOSEOUT_AUTH_TTL_HOURS


class ConflictingEvidenceOverrideDecisionRequest(BaseModel):
    decision: str = Field(min_length=1)
    actor_type: str = Field(default="human", min_length=1)
    actor_id: str = Field(default="dashboard", min_length=1)
    actor_role: str = Field(min_length=1)
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


class ExternalAdapterWriteAuthorizationRequest(BaseModel):
    actor_type: str = Field(default="human", min_length=1)
    actor_id: str = Field(default="dashboard", min_length=1)
    actor_role: str = Field(default="operator", min_length=1)
    reason: str = Field(default="", min_length=0)


class ExternalAdapterWriteAuthorizationDecisionRequest(BaseModel):
    decision: str = Field(min_length=1)
    actor_type: str = Field(default="human", min_length=1)
    actor_id: str = Field(default="dashboard", min_length=1)
    actor_role: str = Field(default="operator", min_length=1)
    reason: str = Field(default="", min_length=0)


class ExternalAdapterWriteExecutionRequest(BaseModel):
    expected_command_execution_digest: str = Field(min_length=1)
    actor_type: str = Field(default="human", min_length=1)
    actor_id: str = Field(default="dashboard", min_length=1)
    actor_role: str = Field(default="operator", min_length=1)
    reason: str = Field(default="", min_length=0)


class MissionCreateRequest(BaseModel):
    direction: str = Field(min_length=1, max_length=8000)
    cadence: str = Field(default="every 6h", min_length=1, max_length=200)
    deliver: str = Field(default="local", min_length=1, max_length=100)
    max_ticks: int = Field(default=12, gt=0, le=100)
    max_turns_per_tick: int = Field(default=8, gt=0, le=50)
    max_total_usd: float | None = Field(default=None, gt=0)
    constraints: list[str] = Field(default_factory=list, max_length=50)
    non_goals: list[str] = Field(default_factory=list, max_length=50)
    success_criteria: list[str] = Field(default_factory=list, max_length=50)
    allowed_actions: list[str] = Field(default_factory=list, max_length=50)
    blocked_actions: list[str] = Field(default_factory=list, max_length=50)


class MissionReasonRequest(BaseModel):
    reason: str = Field(default="", max_length=1000)


class MissionDecisionAnswerRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=4000)


class MissionStageReportRequest(BaseModel):
    summary: str = Field(min_length=1, max_length=4000)
    evidence: list[str] = Field(default_factory=list, max_length=20)
    risks: list[str] = Field(default_factory=list, max_length=20)
    next_step: str = Field(default="", max_length=2000)
    needs_user: bool = False


class MissionInstallCronRequest(BaseModel):
    schedule: str | None = Field(default=None, max_length=200)
    deliver: str | None = Field(default=None, max_length=100)
    name: str | None = Field(default=None, max_length=200)
    enabled_toolsets: list[str] | None = Field(default=None, max_length=20)
    confirm: str = Field(default="", max_length=200)


def _mission_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=500, detail="mission action failed")


def _mission_projection(store: MissionStore) -> dict:
    return {
        "board": store.board(),
        "inbox": store.inbox(),
    }


def _mission_detail(store: MissionStore, mission_id: str) -> dict:
    mission = store.get_mission(mission_id)
    if not mission:
        raise KeyError(mission_id)
    return {
        "mission": mission_to_dict(mission),
        "open_decisions": [decision_to_dict(d) for d in store.open_decisions(mission_id)],
        "recent_events": store.list_events(mission_id, limit=50),
        "doctor": store.doctor(mission_id),
        "report": store.report(mission_id, limit=50),
        "projection": _mission_projection(store),
    }


@router.get("/snapshot")
def get_snapshot():
    """Return the read-only Control Tower Phase 1 snapshot."""
    return control_tower.build_snapshot()


@router.get("/missions")
def get_missions():
    """Return Mission control-plane projections for the dashboard."""
    store = MissionStore()
    return _mission_projection(store)


@router.post("/missions")
def create_mission(req: MissionCreateRequest):
    """Create a default-off Mission from the dashboard."""
    store = MissionStore()
    try:
        budget = MissionBudget(
            max_ticks=req.max_ticks,
            max_turns_per_tick=req.max_turns_per_tick,
            max_total_usd=req.max_total_usd,
        )
        mission = store.create_mission(
            direction=req.direction,
            cadence=req.cadence,
            deliver=req.deliver,
            budget=budget,
            constraints=req.constraints,
            non_goals=req.non_goals,
            success_criteria=req.success_criteria or ["produce evidence-backed stage progress"],
            allowed_actions=req.allowed_actions,
            blocked_actions=req.blocked_actions,
            enable=False,
        )
        return _mission_detail(store, mission.mission_id)
    except (KeyError, ValueError) as exc:
        raise _mission_http_error(exc) from exc


@router.get("/missions/{mission_id}")
def get_mission_detail(mission_id: str):
    """Return one Mission with doctor checks, events, and report."""
    store = MissionStore()
    try:
        return _mission_detail(store, mission_id)
    except (KeyError, ValueError) as exc:
        raise _mission_http_error(exc) from exc


@router.post("/missions/{mission_id}/pause")
def pause_mission(mission_id: str, req: MissionReasonRequest):
    """Pause a Mission through the MissionStore ledger."""
    store = MissionStore()
    try:
        store.set_status(mission_id, "paused", reason=req.reason or "dashboard pause")
        return _mission_detail(store, mission_id)
    except (KeyError, ValueError) as exc:
        raise _mission_http_error(exc) from exc


@router.post("/missions/{mission_id}/resume")
def resume_mission(mission_id: str, req: MissionReasonRequest):
    """Resume a Mission only when no human decisions are open."""
    store = MissionStore()
    try:
        if store.open_decisions(mission_id):
            raise ValueError("mission has open decisions; answer them before resuming")
        store.set_status(mission_id, "active", reason=req.reason or "dashboard resume")
        return _mission_detail(store, mission_id)
    except (KeyError, ValueError) as exc:
        raise _mission_http_error(exc) from exc


@router.post("/missions/{mission_id}/cancel")
def cancel_mission(mission_id: str, req: MissionReasonRequest):
    """Cancel a Mission through the MissionStore ledger."""
    store = MissionStore()
    try:
        store.set_status(mission_id, "cancelled", reason=req.reason or "dashboard cancel")
        return _mission_detail(store, mission_id)
    except (KeyError, ValueError) as exc:
        raise _mission_http_error(exc) from exc


@router.post("/missions/{mission_id}/tick-preview")
def preview_mission_tick(mission_id: str):
    """Generate a dry-run Mission tick preview without consuming tick budget."""
    store = MissionStore()
    try:
        result = store.tick(mission_id, dry_run=True)
        return {"tick": result, "detail": _mission_detail(store, mission_id)}
    except (KeyError, ValueError) as exc:
        raise _mission_http_error(exc) from exc


@router.post("/missions/{mission_id}/stage-report")
def append_mission_stage_report(mission_id: str, req: MissionStageReportRequest):
    """Append a local Mission stage report event."""
    store = MissionStore()
    try:
        event = store.append_stage_report(
            mission_id,
            summary=req.summary,
            evidence=req.evidence,
            risks=req.risks,
            next_step=req.next_step,
            needs_user=req.needs_user,
        )
        return {"event": event, "detail": _mission_detail(store, mission_id)}
    except (KeyError, ValueError) as exc:
        raise _mission_http_error(exc) from exc


@router.post("/missions/{mission_id}/install-cron")
def install_mission_cron(mission_id: str, req: MissionInstallCronRequest):
    """Install Mission cron only after explicit dashboard confirmation."""
    store = MissionStore()
    try:
        mission = store.get_mission(mission_id)
        if not mission:
            raise KeyError(mission_id)
        if req.confirm != mission_id:
            raise ValueError("explicit confirmation is required: confirm must equal the mission id")
        if mission.status != "active":
            raise ValueError(f"mission status is {mission.status}; resume before installing cron")
        if store.open_decisions(mission_id):
            raise ValueError("mission has open decisions; answer them before installing cron")
        doctor = store.doctor(mission_id)
        if doctor.get("cron_jobs"):
            raise ValueError("mission cron is already installed")
        result = store.install_cron(
            mission_id,
            schedule=req.schedule,
            deliver=req.deliver,
            name=req.name,
            enabled_toolsets=req.enabled_toolsets,
        )
        return {"install": result, "detail": _mission_detail(store, mission_id)}
    except (KeyError, ValueError) as exc:
        raise _mission_http_error(exc) from exc


@router.post("/mission-decisions/{decision_id}/answer")
def answer_mission_decision(decision_id: str, req: MissionDecisionAnswerRequest):
    """Answer a Mission decision; MissionStore resumes after the last open decision."""
    store = MissionStore()
    try:
        decision = store.answer_decision(decision_id, req.answer)
        return {"decision": decision_to_dict(decision), "detail": _mission_detail(store, decision.mission_id)}
    except (KeyError, ValueError) as exc:
        raise _mission_http_error(exc) from exc


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


@router.post("/evidence-gates/{evidence_review_id}/conflicting-evidence-override")
def request_conflicting_evidence_override_authorization(
    evidence_review_id: str,
    req: ConflictingEvidenceOverrideAuthorizationRequest,
):
    """Request non-executing conflicting evidence override intent; does not unblock closeout."""
    return control_tower.request_conflicting_evidence_override_authorization(
        evidence_review_id=evidence_review_id,
        actor_type=req.actor_type,
        actor_id=req.actor_id,
        actor_role=req.actor_role,
        source_surface="dashboard",
        reason=req.reason,
        ttl_hours=req.ttl_hours,
    )


@router.post("/conflicting-evidence-overrides/{conflict_override_authorization_id}/decision")
def decide_conflicting_evidence_override_authorization(
    conflict_override_authorization_id: str,
    req: ConflictingEvidenceOverrideDecisionRequest,
):
    """Decide conflicting evidence override projection without enabling closeout."""
    return control_tower.decide_conflicting_evidence_override_authorization(
        conflict_override_authorization_id=conflict_override_authorization_id,
        decision=req.decision,
        actor_type=req.actor_type,
        actor_id=req.actor_id,
        actor_role=req.actor_role,
        source_surface="dashboard",
        reason=req.reason,
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


@router.post("/external-write-reviews/{external_write_review_id}/authorization")
def request_external_adapter_write_authorization(
    external_write_review_id: str,
    req: ExternalAdapterWriteAuthorizationRequest,
):
    """Request digest-bound external write authorization without enabling write-through."""
    return control_tower.request_external_adapter_write_authorization(
        external_write_review_id=external_write_review_id,
        actor_type=req.actor_type,
        actor_id=req.actor_id,
        actor_role=req.actor_role,
        source_surface="dashboard",
        reason=req.reason,
    )


@router.post("/external-write-authorizations/{external_write_authorization_id}/decision")
def decide_external_adapter_write_authorization(
    external_write_authorization_id: str,
    req: ExternalAdapterWriteAuthorizationDecisionRequest,
):
    """Decide external write authorization while write-through remains disabled."""
    return control_tower.decide_external_adapter_write_authorization(
        external_write_authorization_id=external_write_authorization_id,
        decision=req.decision,
        actor_type=req.actor_type,
        actor_id=req.actor_id,
        actor_role=req.actor_role,
        source_surface="dashboard",
        reason=req.reason,
    )


@router.post("/external-write-authorizations/{external_write_authorization_id}/execution-preflight")
def prepare_external_adapter_write_execution_preflight(
    external_write_authorization_id: str,
    req: ExternalAdapterWriteAuthorizationRequest,
):
    """Prepare command-digest external write preflight without executing adapter writes."""
    return control_tower.prepare_external_adapter_write_execution_preflight(
        external_write_authorization_id=external_write_authorization_id,
        actor_type=req.actor_type,
        actor_id=req.actor_id,
        actor_role=req.actor_role,
        source_surface="dashboard",
        reason=req.reason,
    )


@router.post("/external-write-preflights/{external_write_preflight_id}/execute")
def execute_external_adapter_write_preflight(
    external_write_preflight_id: str,
    req: ExternalAdapterWriteExecutionRequest,
):
    """Execute digest-bound Kanban/taskpool external closeout write-through after explicit admin approval."""
    return control_tower.execute_external_adapter_write_preflight(
        external_write_preflight_id=external_write_preflight_id,
        expected_command_execution_digest=req.expected_command_execution_digest,
        actor_type=req.actor_type,
        actor_id=req.actor_id,
        actor_role=req.actor_role,
        source_surface="dashboard",
        reason=req.reason,
    )


@router.post("/external-write-runs/{external_write_execution_id}/rollback")
def rollback_external_adapter_write_execution(
    external_write_execution_id: str,
    req: ExternalAdapterWriteExecutionRequest,
):
    """Rollback/reopen a digest-bound Kanban/taskpool external write after explicit admin approval."""
    return control_tower.execute_external_adapter_write_rollback(
        external_write_execution_id=external_write_execution_id,
        expected_command_execution_digest=req.expected_command_execution_digest,
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
