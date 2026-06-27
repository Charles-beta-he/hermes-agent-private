"""Persistent session goals — the Ralph loop for Hermes.

A goal is a free-form user objective that stays active across turns. After
each turn completes, a small judge call asks an auxiliary model "is this
goal satisfied by the assistant's last response?". If not, Hermes feeds a
continuation prompt back into the same session and keeps working until the
goal is done, turn budget is exhausted, the user pauses/clears it, or the
user sends a new message (which takes priority and pauses the goal loop).

State is persisted in SessionDB's ``state_meta`` table keyed by
``goal:<session_id>`` so ``/resume`` picks it up.

Design notes / invariants:

- The continuation prompt is just a normal user message appended to the
  session via ``run_conversation``. No system-prompt mutation, no toolset
  swap — prompt caching stays intact.
- Judge failures are fail-OPEN: ``continue``. A broken judge must not wedge
  progress; the turn budget is the backstop.
- When a real user message arrives mid-loop it preempts the continuation
  prompt and also pauses the goal loop for that turn (we still re-judge
  after, so if the user's message happens to complete the goal the judge
  will say ``done``).
- This module has zero hard dependency on ``cli.HermesCLI`` or the gateway
  runner — both wire the same ``GoalManager`` in.

Nothing in this module touches the agent's system prompt or toolset.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Constants & defaults
# ──────────────────────────────────────────────────────────────────────

DEFAULT_MAX_TURNS = 20
DEFAULT_JUDGE_TIMEOUT = 30.0
# Judge output budget. The freeform judge returns a one-line JSON verdict, but
# reasoning models (deepseek-v4, qwq, etc.) burn tokens on hidden reasoning
# before emitting the visible JSON — and the first /goal turn's prompt is
# larger than later turns, which pushes total reply length past tight caps.
# 200 tokens (the original default) reliably truncated the JSON on reasoning
# models, leaving '{"done": true, "reason": "The agent successfully' and
# triggering the auto-pause. 4096 covers reasoning + verdict on every model
# we've live-tested; override via auxiliary.goal_judge.max_tokens for
# specifically constrained setups.
DEFAULT_JUDGE_MAX_TOKENS = 4096
# Cap how much of the last response + recent messages we send to the judge.
_JUDGE_RESPONSE_SNIPPET_CHARS = 4000
# After this many consecutive judge *parse* failures (empty output / non-JSON),
# the loop auto-pauses and points the user at the goal_judge config. API /
# transport errors do NOT count toward this — those are transient. This guards
# against small models (e.g. deepseek-v4-flash) that cannot follow the strict
# JSON reply contract; without it the loop runs until the turn budget is
# exhausted with every reply shaped like `judge returned empty response` or
# `judge reply was not JSON`.
DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES = 3


CONTINUATION_PROMPT_TEMPLATE = (
    "[Continuing toward your standing goal]\n"
    "Goal: {goal}\n\n"
    "Continue working toward this goal. Take the next concrete step. "
    "If you believe the goal is complete, state so explicitly and stop. "
    "If you are blocked and need input from the user, say so clearly and stop."
)

# Used when the user has added one or more /subgoal criteria. Surfaced
# to the agent verbatim so it sees what to target on the next turn,
# and surfaced to the judge so the verdict considers them too.
CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE = (
    "[Continuing toward your standing goal]\n"
    "Goal: {goal}\n\n"
    "Additional criteria the user added mid-loop:\n"
    "{subgoals_block}\n\n"
    "Continue working toward the goal AND all additional criteria. Take "
    "the next concrete step. If you believe the goal and every "
    "additional criterion are complete, state so explicitly and stop. "
    "If you are blocked and need input from the user, say so clearly "
    "and stop."
)


JUDGE_SYSTEM_PROMPT = (
    "You are a strict judge evaluating whether an autonomous agent has "
    "achieved a user's stated goal. You receive the goal text and the "
    "agent's most recent response. Your only job is to decide whether "
    "the goal is fully satisfied based on that response.\n\n"
    "A goal is DONE only when the response includes concrete evidence "
    "that the final deliverable was produced. A bare completion claim is "
    "not enough: look for command output, file/artifact paths, test output, "
    "or proof-log/evidence references.\n\n"
    "If the response says the agent is blocked, needs human input, lacks "
    "permission/credentials, or requires approval, do NOT treat that as "
    "DONE; classify it as not done and explain the human/input boundary.\n\n"
    "Otherwise the goal is NOT done — CONTINUE.\n\n"
    "Reply ONLY with a single JSON object on one line:\n"
    '{\"done\": <true|false>, \"reason\": \"<one-sentence rationale>\"}'
)


JUDGE_USER_PROMPT_TEMPLATE = (
    "Goal:\n{goal}\n\n"
    "Agent's most recent response:\n{response}\n\n"
    "Current time: {current_time}\n\n"
    "Is the goal satisfied?"
)

# Used when the user has added /subgoal criteria. The judge must
# evaluate ALL of them being met, not just the original goal.
JUDGE_USER_PROMPT_WITH_SUBGOALS_TEMPLATE = (
    "Goal:\n{goal}\n\n"
    "Additional criteria the user added mid-loop (all must also be "
    "satisfied for the goal to be DONE):\n{subgoals_block}\n\n"
    "Agent's most recent response:\n{response}\n\n"
    "Current time: {current_time}\n\n"
    "Decision: For each numbered criterion above, find concrete "
    "evidence in the agent's response that the criterion is "
    "satisfied. Do not accept generic phrases like 'all requirements "
    "met' or 'implying it was done' — require specific evidence (a "
    "file contents excerpt, an output line, a command result). If "
    "ANY criterion lacks specific evidence in the response, the goal "
    "is NOT done — return CONTINUE.\n\n"
    "Is the goal AND every additional criterion satisfied?"
)


# ──────────────────────────────────────────────────────────────────────
# Dataclass
# ──────────────────────────────────────────────────────────────────────


@dataclass
class GoalRouteDecision:
    """Dry-run routing projection for a standing goal.

    This is intentionally advisory: setting a /goal must not enqueue taskpool
    work, create cron jobs, or mutate mission state. Callers can display/store
    the route and a later governed path may use it as evidence.
    """

    target_layer: str
    reason: str
    side_effect: str = "dry_run_only"
    schema: str = "goal_route_decision_v1"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GoalEvidenceBundle:
    """Deterministic evidence extracted from the agent's latest response.

    The auxiliary judge is intentionally cheap and can be over-trusting.  This
    local bundle is the fail-closed contract: DONE verdicts require at least one
    concrete proof reference, while human/input blockers are represented as a
    separate class rather than as verified completion.
    """

    command_refs: List[str] = field(default_factory=list)
    file_refs: List[str] = field(default_factory=list)
    proof_refs: List[str] = field(default_factory=list)
    needs_human: bool = False

    @property
    def has_proof(self) -> bool:
        return bool(self.command_refs or self.file_refs or self.proof_refs)


@dataclass
class GoalState:
    """Serializable goal state stored per session."""

    goal: str
    goal_id: str = ""
    status: str = "active"          # active | paused | done | cleared
    turns_used: int = 0
    max_turns: int = DEFAULT_MAX_TURNS
    created_at: float = 0.0
    last_turn_at: float = 0.0
    last_verdict: Optional[str] = None        # "done" | "continue" | "skipped"
    last_reason: Optional[str] = None
    paused_reason: Optional[str] = None       # why we auto-paused (budget, etc.)
    consecutive_parse_failures: int = 0       # judge-output parse failures in a row
    # Structured contract + dry-run route projection for production-grade
    # Goal-Control Plane work. Backwards-compatible: older rows load with None.
    spec: Optional[Dict[str, Any]] = None
    route: Optional[Dict[str, Any]] = None
    # User-added criteria appended mid-loop via the /subgoal command.
    # When non-empty the judge prompt and continuation prompt both
    # include them so the agent works toward them and the judge factors
    # them into the verdict. Backwards-compatible: defaults to empty so
    # old state_meta rows load unchanged.
    subgoals: List[str] = field(default_factory=list)
    # Durable proof/plan context mirrored into the checkpoint sidecar so a
    # restart or compression rollover can recover more than the raw goal text.
    proof_refs: List[str] = field(default_factory=list)
    remaining_plan: List[str] = field(default_factory=list)
    # Dynamic workflow runtime packet compiled from the goal spec. This is
    # Hermes' default-safe Ultracode-style landing: script-held state, DAG
    # progress, evidence contracts, and cost visibility without dispatching
    # workers or executing node commands.
    workflow_run: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        if not self.goal_id:
            self.goal_id = f"goal_{uuid.uuid4().hex[:12]}"

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "GoalState":
        data = json.loads(raw)
        raw_subgoals = data.get("subgoals") or []
        subgoals: List[str] = []
        if isinstance(raw_subgoals, list):
            subgoals = [str(s).strip() for s in raw_subgoals if str(s).strip()]
        raw_proof_refs = data.get("proof_refs") or []
        proof_refs: List[str] = []
        if isinstance(raw_proof_refs, list):
            proof_refs = [str(s).strip() for s in raw_proof_refs if str(s).strip()]
        raw_remaining_plan = data.get("remaining_plan") or []
        remaining_plan: List[str] = []
        if isinstance(raw_remaining_plan, list):
            remaining_plan = [str(s).strip() for s in raw_remaining_plan if str(s).strip()]
        raw_spec = data.get("spec") if isinstance(data.get("spec"), dict) else None
        raw_route = data.get("route") if isinstance(data.get("route"), dict) else None
        raw_workflow_run = data.get("workflow_run") if isinstance(data.get("workflow_run"), dict) else None
        return cls(
            goal=data.get("goal", ""),
            goal_id=str(data.get("goal_id") or ""),
            status=data.get("status", "active"),
            turns_used=int(data.get("turns_used", 0) or 0),
            max_turns=int(data.get("max_turns", DEFAULT_MAX_TURNS) or DEFAULT_MAX_TURNS),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            last_turn_at=float(data.get("last_turn_at", 0.0) or 0.0),
            last_verdict=data.get("last_verdict"),
            last_reason=data.get("last_reason"),
            paused_reason=data.get("paused_reason"),
            consecutive_parse_failures=int(data.get("consecutive_parse_failures", 0) or 0),
            spec=raw_spec,
            route=raw_route,
            subgoals=subgoals,
            proof_refs=proof_refs,
            remaining_plan=remaining_plan,
            workflow_run=raw_workflow_run,
        )

    # --- subgoals helpers -------------------------------------------------

    def render_subgoals_block(self) -> str:
        """Render the subgoals as a numbered ``- N. text`` block. Empty
        when no subgoals exist."""
        if not self.subgoals:
            return ""
        return "\n".join(f"- {i}. {text}" for i, text in enumerate(self.subgoals, start=1))


# ──────────────────────────────────────────────────────────────────────
# Persistence (SessionDB state_meta)
# ──────────────────────────────────────────────────────────────────────


def _meta_key(session_id: str) -> str:
    return f"goal:{session_id}"


def _checkpoint_meta_key(session_id: str) -> str:
    return f"goal_checkpoint:{session_id}"


def _clean_str_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    return [str(v).strip() for v in values if str(v).strip()]


def _build_goal_checkpoint(session_id: str, state: GoalState, *, reason: str = "checkpoint") -> Dict[str, Any]:
    """Build the recovery sidecar for one /goal session.

    The sidecar duplicates the workflow-shaped state that matters after a
    restart or compression rollover. It is intentionally local/state_meta only:
    no taskpool, mission, cron, or external lifecycle mutation happens here.
    """
    return {
        "schema": "goal_checkpoint_v1",
        "session_id": session_id,
        "goal_id": state.goal_id,
        "goal": state.goal,
        "status": state.status,
        "goal_spec": state.spec,
        "route": state.route,
        "subgoals": list(state.subgoals or []),
        "last_verdict": state.last_verdict,
        "last_reason": state.last_reason,
        "proof_refs": list(state.proof_refs or []),
        "remaining_plan": list(state.remaining_plan or []),
        "workflow_run": state.workflow_run,
        "reason": reason,
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def _apply_goal_checkpoint(state: GoalState, checkpoint: Optional[Dict[str, Any]]) -> GoalState:
    if not isinstance(checkpoint, dict) or checkpoint.get("schema") != "goal_checkpoint_v1":
        return state
    goal_id = str(checkpoint.get("goal_id") or "").strip()
    if goal_id:
        state.goal_id = goal_id
    if isinstance(checkpoint.get("goal_spec"), dict):
        state.spec = checkpoint.get("goal_spec")
    if isinstance(checkpoint.get("route"), dict):
        state.route = checkpoint.get("route")
    if isinstance(checkpoint.get("subgoals"), list):
        state.subgoals = _clean_str_list(checkpoint.get("subgoals"))
    if isinstance(checkpoint.get("proof_refs"), list):
        state.proof_refs = _clean_str_list(checkpoint.get("proof_refs"))
    if isinstance(checkpoint.get("remaining_plan"), list):
        state.remaining_plan = _clean_str_list(checkpoint.get("remaining_plan"))
    if isinstance(checkpoint.get("workflow_run"), dict):
        state.workflow_run = checkpoint.get("workflow_run")
    last_verdict = checkpoint.get("last_verdict")
    if last_verdict and not state.last_verdict:
        state.last_verdict = str(last_verdict)
    last_reason = checkpoint.get("last_reason")
    if last_reason and not state.last_reason:
        state.last_reason = str(last_reason)
    return state


_DB_CACHE: Dict[str, Any] = {}


def _get_session_db() -> Optional[Any]:
    """Return a SessionDB instance for the current HERMES_HOME.

    SessionDB has no built-in singleton, but opening a new connection per
    /goal call would thrash the file. We cache one instance per
    ``hermes_home`` path so profile switches still pick up the right DB.
    Defensive against import/instantiation failures so tests and
    non-standard launchers can still use the GoalManager.
    """
    try:
        from hermes_constants import get_hermes_home
        from hermes_state import SessionDB

        home = str(get_hermes_home())
    except Exception as exc:  # pragma: no cover
        logger.debug("GoalManager: SessionDB bootstrap failed (%s)", exc)
        return None

    cached = _DB_CACHE.get(home)
    if cached is not None:
        return cached
    try:
        db = SessionDB()
    except Exception as exc:  # pragma: no cover
        logger.debug("GoalManager: SessionDB() raised (%s)", exc)
        return None
    _DB_CACHE[home] = db
    return db


def load_goal(session_id: str) -> Optional[GoalState]:
    """Load the goal for a session, or None if none exists."""
    if not session_id:
        return None
    db = _get_session_db()
    if db is None:
        return None
    try:
        raw = db.get_meta(_meta_key(session_id))
    except Exception as exc:
        logger.debug("GoalManager: get_meta failed: %s", exc)
        return None
    if not raw:
        return None
    try:
        state = GoalState.from_json(raw)
    except Exception as exc:
        logger.warning("GoalManager: could not parse stored goal for %s: %s", session_id, exc)
        return None
    checkpoint = load_goal_checkpoint(session_id)
    return _apply_goal_checkpoint(state, checkpoint)


def load_goal_checkpoint(session_id: str) -> Optional[Dict[str, Any]]:
    """Load the goal checkpoint sidecar for a session, if present."""
    if not session_id:
        return None
    db = _get_session_db()
    if db is None:
        return None
    try:
        raw = db.get_meta(_checkpoint_meta_key(session_id))
    except Exception as exc:
        logger.debug("GoalManager: get checkpoint meta failed: %s", exc)
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except Exception as exc:
        logger.debug("GoalManager: checkpoint parse failed for %s: %s", session_id, exc)
        return None
    return data if isinstance(data, dict) else None


def save_goal_checkpoint(session_id: str, state: GoalState, *, reason: str = "checkpoint") -> Optional[Dict[str, Any]]:
    """Persist a goal recovery sidecar under a separate SessionDB meta key."""
    if not session_id:
        return None
    db = _get_session_db()
    if db is None:
        return None
    checkpoint = _build_goal_checkpoint(session_id, state, reason=reason)
    try:
        db.set_meta(_checkpoint_meta_key(session_id), json.dumps(checkpoint, ensure_ascii=False))
    except Exception as exc:
        logger.debug("GoalManager: set checkpoint meta failed: %s", exc)
        return None
    return checkpoint


def save_goal(session_id: str, state: GoalState) -> None:
    """Persist a goal to SessionDB. No-op if DB unavailable."""
    if not session_id:
        return
    db = _get_session_db()
    if db is None:
        return
    try:
        db.set_meta(_meta_key(session_id), state.to_json())
    except Exception as exc:
        logger.debug("GoalManager: set_meta failed: %s", exc)


def rebind_goal_checkpoint(old_session_id: str, new_session_id: str, *, reason: str = "rebind") -> bool:
    """Copy active /goal state + checkpoint to a new session id.

    Compression rotates SessionDB rows. This helper is the pre/post-compact
    binding point: force a checkpoint on the old session, then persist the same
    recovered state under the new session id so /goal status survives reload.
    """
    if not old_session_id or not new_session_id or old_session_id == new_session_id:
        return False
    state = load_goal(old_session_id)
    if state is None:
        return False
    save_goal_checkpoint(old_session_id, state, reason=f"pre-{reason}")
    save_goal(new_session_id, state)
    checkpoint = save_goal_checkpoint(new_session_id, state, reason=reason)
    return checkpoint is not None


def _clean_text_list(values: Optional[List[str]]) -> List[str]:
    if not values:
        return []
    return [str(v).strip() for v in values if str(v).strip()]


def goal_route_decision(goal: str, *, success_criteria: Optional[List[str]] = None) -> GoalRouteDecision:
    """Classify a /goal into an advisory execution layer.

    This is a dry-run control-plane decision, not an executor. It deliberately
    returns ``human_gate`` for production/deploy/payment/secret/destructive
    goals before any lower-risk route so /goal cannot silently escalate
    authority.
    """
    text = f"{goal or ''} {' '.join(_clean_text_list(success_criteria))}".lower()
    human_terms = [
        "生产", "deploy", "部署", "付款", "payment", "purchase", "secret",
        "credential", "密钥", "删除", "destructive", "push", "merge",
    ]
    cron_terms = ["每天", "每日", "定时", "schedule", "cron", "every ", "每周", "每小时"]
    mission_terms = ["长期", "mission", "架构", "自推进", "长期推进", "roadmap"]
    taskpool_terms = [
        "代码", "实现", "测试", "修复", "修改", "落地", "全盘推进", "workflow",
        "hooks", "dynamic", "taskpool", "hermes", "repo", "文件",
    ]
    if any(term in text for term in human_terms):
        return GoalRouteDecision("human_gate", "goal requests high-risk authority or external side effects")
    if any(term in text for term in cron_terms):
        return GoalRouteDecision("cron", "goal is recurring or schedule-shaped")
    if any(term in text for term in mission_terms):
        return GoalRouteDecision("mission", "goal is long-running architecture or mission-shaped")
    if any(term in text for term in taskpool_terms):
        return GoalRouteDecision("taskpool", "goal is multi-step implementation or Hermes control-plane work")
    return GoalRouteDecision("session", "goal appears bounded to the current interactive session")


def build_goal_spec(
    goal: str,
    *,
    max_turns: int,
    success_criteria: Optional[List[str]] = None,
    non_goals: Optional[List[str]] = None,
) -> Dict[str, Any]:
    criteria = _clean_text_list(success_criteria)
    boundaries = _clean_text_list(non_goals)
    route = goal_route_decision(goal, success_criteria=criteria)
    return {
        "schema": "goal_spec_v1",
        "user_goal": goal,
        "success_criteria": criteria,
        "non_goals": boundaries,
        "target_layer": route.target_layer,
        "max_turns": int(max_turns),
        "requires_evidence": True,
        "requires_human_gate": route.target_layer == "human_gate",
    }


def compile_goal_promotion_dry_run(goal_spec: Dict[str, Any]) -> Dict[str, Any]:
    """Project a ``goal_spec_v1`` into a governed promotion candidate.

    This is the intentionally non-mutating bridge between the standing-goal
    loop and longer-lived Hermes control-plane surfaces. It returns the shape a
    later audited command could use to create a taskpool item, mission, cron
    job, or human-gate packet, but it never writes those surfaces itself.
    """
    if not isinstance(goal_spec, dict) or goal_spec.get("schema") != "goal_spec_v1":
        raise ValueError("goal_spec_v1 dict required")

    goal = str(goal_spec.get("user_goal") or "").strip()
    if not goal:
        raise ValueError("goal_spec_v1.user_goal is required")

    criteria = _clean_text_list(goal_spec.get("success_criteria") or [])
    route = goal_route_decision(goal, success_criteria=criteria)
    target = route.target_layer
    kind_by_layer = {
        "taskpool": "taskpool_task_candidate",
        "mission": "mission_candidate",
        "cron": "cron_candidate",
        "human_gate": "human_gate_packet_candidate",
        "session": "session_continuation_candidate",
    }
    evidence_by_layer = {
        "taskpool": [
            "objective",
            "acceptance_criteria",
            "structured_evidence_refs",
            "deterministic_test_or_dry_run_readback",
            "before_after_state_diff_when_mutation_is_later_authorized",
        ],
        "mission": [
            "mission_objective",
            "budget_or_tick_boundary",
            "human_gate_or_stop_condition",
            "stage_report_readback",
        ],
        "cron": [
            "schedule_expression",
            "delivery_target_or_no_agent_mode",
            "dry_run_output",
            "duplicate_rate_limit_proof",
        ],
        "human_gate": [
            "explicit_user_authority",
            "risk_summary",
            "rollback_or_compensation_plan",
            "readback_receipt_requirement",
        ],
        "session": [
            "next_concrete_step",
            "goal_judge_verdict",
            "response_evidence_snippet",
        ],
    }
    permission_by_layer = {
        "taskpool": "governed_taskpool_lifecycle_required",
        "mission": "mission_budget_and_handoff_required",
        "cron": "cron_install_or_update_requires_explicit_authority",
        "human_gate": "human_approval_required_before_side_effect",
        "session": "current_session_only",
    }

    candidate = {
        "kind": kind_by_layer.get(target, "session_continuation_candidate"),
        "title": goal[:120],
        "target_layer": target,
        "reason": route.reason,
        "evidence_requirements": evidence_by_layer.get(target, evidence_by_layer["session"]),
        "permission_boundary": permission_by_layer.get(target, "current_session_only"),
        "acceptance_criteria": criteria,
        "non_goals": _clean_text_list(goal_spec.get("non_goals") or []),
        "execute": False,
    }
    return {
        "schema": "goal_promotion_dry_run_v1",
        "dry_run": True,
        "execution_enabled": False,
        "mutation_allowed": False,
        "state_mutations": [],
        "candidate": candidate,
        "route": route.to_dict(),
        "goal_spec": goal_spec,
        "safety_boundary": (
            "Dry-run projection only; no taskpool, mission, cron, lifecycle, "
            "or external state is mutated."
        ),
    }


def _stable_node_id(value: str, *, prefix: str = "node") -> str:
    text = re.sub(r"[^a-zA-Z0-9_-]+", "-", (value or "").strip().lower()).strip("-")
    return f"{prefix}:{text or 'unnamed'}"


def _topological_order_or_raise(nodes: List[Dict[str, Any]]) -> Tuple[List[str], List[Dict[str, str]]]:
    """Return DAG order and edge list for compiled dry-run nodes.

    The compiler is a safety boundary, so malformed dependencies fail closed
    instead of being hidden in an apparently executable plan.
    """
    node_ids = [str(node["id"]) for node in nodes]
    if len(node_ids) != len(set(node_ids)):
        raise ValueError("workflow_plan_v1 DAG requires unique node ids")
    known = set(node_ids)
    edges: List[Dict[str, str]] = []
    outgoing: Dict[str, List[str]] = {node_id: [] for node_id in node_ids}
    indegree: Dict[str, int] = {node_id: 0 for node_id in node_ids}
    for node in nodes:
        node_id = str(node["id"])
        for dep in node.get("depends_on") or []:
            dep_id = str(dep)
            if dep_id not in known:
                raise ValueError(f"workflow_plan_v1 DAG has unknown dependency {dep_id!r} for {node_id!r}")
            edges.append({"from": dep_id, "to": node_id})
            outgoing[dep_id].append(node_id)
            indegree[node_id] += 1

    ready = [node_id for node_id in node_ids if indegree[node_id] == 0]
    order: List[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for child in outgoing[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    if len(order) != len(node_ids):
        raise ValueError("workflow_plan_v1 DAG must be acyclic")
    return order, edges


def _compile_goal_spec_step_nodes(goal_spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    steps = goal_spec.get("workflow_steps") or goal_spec.get("steps") or []
    nodes: List[Dict[str, Any]] = []
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise ValueError("workflow_steps entries must be dicts")
        raw_id = str(step.get("id") or step.get("name") or f"step-{idx}")
        node_id = _stable_node_id(raw_id, prefix="goal")
        depends_on = [_stable_node_id(str(dep), prefix="goal") for dep in step.get("depends_on") or []]
        boundary = str(step.get("permission_boundary") or "local_dry_run_only")
        evidence_contract = _clean_text_list(
            step.get("evidence_contract") or step.get("evidence_requirements") or goal_spec.get("success_criteria") or []
        )
        if not evidence_contract:
            evidence_contract = ["dry_run_readback"]
        executor_ref = {
            key: step[key]
            for key in ("command", "tool", "action", "handler")
            if key in step and step.get(key) is not None
        }
        node = {
            "id": node_id,
            "title": str(step.get("title") or raw_id),
            "kind": "goal_spec_step",
            "depends_on": depends_on,
            "evidence_required": True,
            "evidence_contract": evidence_contract,
            "permission_boundary": boundary,
            "human_gate_required": _requires_human_gate(boundary),
            "execute": False,
            "rollback_boundary": str(step.get("rollback_boundary") or "not_applicable_dry_run_only"),
        }
        if executor_ref:
            node["executor_ref"] = executor_ref
        nodes.append(node)
    return nodes


def _requires_human_gate(boundary: str) -> bool:
    text = (boundary or "").lower()
    gate_terms = [
        "human_gate", "no_bypass", "external", "payment", "deploy", "signing",
        "custody", "login", "2fa", "write", "secret", "anti_bot", "captcha", "cloudflare",
    ]
    return any(term in text for term in gate_terms)


_SOCIAL_PAIN_FIXTURES: Dict[str, Dict[str, Any]] = {
    "developer_agent": {
        "category": "developer_agent",
        "source_report": "artifact:/Users/charles/Desktop/folder/developer_agent_ai_coding_pain_insights_public_dry_run.md",
        "pain_clusters": [
            {
                "id": "developer-cost-control",
                "title": "成本不可预测、token/配额消耗过快",
                "state_object": "agent_task_budget",
                "failure_mode": "unbounded_retry_or_context_bloat",
                "permission_boundary": "budget_hard_stop",
                "source_evidence": [
                    {"url": "https://news.ycombinator.com/item?id=47623434", "excerpt": "quota is finished"},
                    {"url": "https://github.com/cline/cline/pull/10329", "excerpt": "spend limits"},
                ],
                "opportunity_score": 90,
            },
            {
                "id": "developer-context-lifecycle",
                "title": "上下文窗口/生命周期管理差",
                "state_object": "conversation_context",
                "failure_mode": "stale_or_bloated_context",
                "permission_boundary": "evidence_only",
                "source_evidence": [
                    {"url": "https://github.com/Aider-AI/aider/issues/5071", "excerpt": "manually calls /drop"},
                    {"url": "https://github.com/cline/cline/pull/11365", "excerpt": "terminal output context bloat"},
                ],
                "opportunity_score": 90,
            },
            {
                "id": "developer-permission-security",
                "title": "安全、隐私、权限边界阻碍采用",
                "state_object": "tool_permission_boundary",
                "failure_mode": "prompt_injection_or_secret_exfiltration",
                "permission_boundary": "secret_network_shell_human_gate",
                "source_evidence": [
                    {"url": "https://www.anthropic.com/engineering/claude-code-sandboxing", "excerpt": "filesystem and network isolation"},
                    {"url": "https://cursor.com/security", "excerpt": "Privacy Mode"},
                ],
                "opportunity_score": 80,
            },
        ],
    },
    "browser_automation": {
        "category": "browser_automation",
        "source_report": "artifact:/Users/charles/Desktop/folder/browser_automation_public_pain_insight_dryrun.md",
        "pain_clusters": [
            {
                "id": "browser-anti-bot-boundary",
                "title": "CAPTCHA / Cloudflare / 反爬阻断",
                "state_object": "browser_session",
                "failure_mode": "access_control_block",
                "permission_boundary": "human_gate_no_bypass_captcha_cloudflare",
                "source_evidence": [
                    {"url": "https://github.com/browser-use/browser-use/issues/4209", "excerpt": "CAPTCHA solving only works"},
                    {"url": "https://github.com/microsoft/playwright/issues/38704", "excerpt": "bypass the Cloudflare security check"},
                ],
                "opportunity_score": 88,
            },
            {
                "id": "browser-flaky-timeout-locator",
                "title": "等待、超时、locator/DOM 变化导致 flaky",
                "state_object": "browser_trace",
                "failure_mode": "flaky_wait_or_locator_drift",
                "permission_boundary": "rate_limit_safe_retry",
                "source_evidence": [
                    {"url": "https://github.com/microsoft/playwright/issues/40681", "excerpt": "close to their timeout limit"},
                    {"url": "https://github.com/SeleniumHQ/selenium/issues/15314", "excerpt": "custom sleep interval"},
                ],
                "opportunity_score": 82,
            },
            {
                "id": "browser-login-write-risk",
                "title": "登录态、2FA、写操作风险",
                "state_object": "authenticated_browser_profile",
                "failure_mode": "credential_or_write_action_risk",
                "permission_boundary": "login_2fa_write_human_gate",
                "source_evidence": [
                    {"url": "https://raw.githubusercontent.com/microsoft/playwright/main/docs/src/auth.md", "excerpt": "sensitive cookies and headers"},
                    {"url": "https://developers.openai.com/api/docs/guides/tools-computer-use", "excerpt": "confirm before typing or submitting"},
                ],
                "opportunity_score": 90,
            },
        ],
    },
    "stablecoin_payment": {
        "category": "stablecoin_payment",
        "source_report": "artifact:/Users/charles/Desktop/folder/stablecoin_wallet_payment_public_pain_dryrun.md",
        "pain_clusters": [
            {
                "id": "stablecoin-withdrawal-hold",
                "title": "入金/买币后可买不可提、提现冻结",
                "state_object": "withdrawable_balance",
                "failure_mode": "payment_sla_hold",
                "permission_boundary": "read_only_no_hold_bypass",
                "source_evidence": [
                    {"url": "https://support.kraken.com/articles/360000380966-why-is-my-deposit-or-withdrawal-on-hold-", "excerpt": "temporary (72 hour) withdrawal hold"},
                    {"url": "https://support.kraken.com/articles/360000381966-unable-to-withdraw-funds", "excerpt": "Unable to withdraw funds"},
                ],
                "opportunity_score": 82,
            },
            {
                "id": "stablecoin-gas-pending-failed",
                "title": "pending / failed / gas 不足导致付款失败",
                "state_object": "wallet_transaction_state",
                "failure_mode": "pending_or_insufficient_native_token",
                "permission_boundary": "read_only_no_signing_no_custody",
                "source_evidence": [
                    {"url": "https://support.metamask.io/manage-crypto/transactions/how-to-speed-up-or-cancel-a-pending-transaction/", "excerpt": "speed up or cancel a pending transaction"},
                    {"url": "https://support.metamask.io/configure/transactions/how-to-fix-insufficient-funds-error-or-greyed-out-confirm-button/", "excerpt": "insufficient amount of the native token"},
                ],
                "opportunity_score": 88,
            },
            {
                "id": "stablecoin-custody-scam-risk",
                "title": "助记词泄露、假客服、钱包被 drain",
                "state_object": "self_custody_wallet_security",
                "failure_mode": "seed_phrase_social_engineering",
                "permission_boundary": "custody_signing_secret_human_gate",
                "source_evidence": [
                    {"url": "https://support.metamask.io/stay-safe/protect-yourself/ive-been-hacked-scammed-unauthorized-transactions-on-my-account/", "excerpt": "Never give it to anyone"},
                    {"url": "https://support.metamask.io/stay-safe/protect-yourself/social-engineering/spoofing-scams/", "excerpt": "drain your wallet of funds"},
                ],
                "opportunity_score": 84,
            },
        ],
    },
}


def get_social_pain_insight_fixture(category: str) -> Dict[str, Any]:
    """Return a built-in public-source pain-insight fixture by category.

    Fixtures are intentionally small, static, and evidence-referenced. They are
    suitable for workflow compiler tests and dry-run planning, not for market
    sizing or live scraping.
    """
    key = (category or "").strip().lower().replace("-", "_")
    try:
        return json.loads(json.dumps(_SOCIAL_PAIN_FIXTURES[key]))
    except KeyError as exc:
        known = ", ".join(sorted(_SOCIAL_PAIN_FIXTURES))
        raise ValueError(f"unknown social pain fixture category: {category!r}; known: {known}") from exc


_AGENT_ROLE_TOPOLOGIES: Dict[str, Dict[str, Any]] = {
    "openclaw_video_v1": {
        "schema": "agent_role_topology_v1",
        "id": "openclaw_video_v1",
        "source_report": "artifact:/Users/charles/.hermes/docs/private/wechat-video-niuma-openclaw-multi-agent-analysis-20260616.md",
        "source_verification": {
            "openclaw_repo_verified": True,
            "openclaw_repo": "https://github.com/openclaw/openclaw",
            "openclaw_api_checked_at": "2026-06-16T13:55:00+08:00",
            "niuma_public_source": "not_verified",
            "media_claim_metrics": "video_only_not_accepted_as_fact",
        },
        "roles": [
            {
                "role": "planner",
                "title": "Planner",
                "inputs": ["goal_spec", "constraints", "source_evidence"],
                "outputs": ["implementation_plan", "role_dag"],
                "evidence_contract": ["plan references success criteria", "permission boundaries listed"],
                "depends_on": [],
                "permission_boundary": "evidence_only",
            },
            {
                "role": "coder",
                "title": "Coder",
                "inputs": ["implementation_plan", "repo_context"],
                "outputs": ["patch_proposal", "changed_files"],
                "evidence_contract": ["diff artifact", "scope summary"],
                "depends_on": ["planner"],
                "permission_boundary": "local_patch_only",
            },
            {
                "role": "tester",
                "title": "Tester",
                "inputs": ["patch_proposal", "test_plan"],
                "outputs": ["test_results", "failure_taxonomy"],
                "evidence_contract": ["test command output", "failure classification"],
                "depends_on": ["coder"],
                "permission_boundary": "local_test_only",
            },
            {
                "role": "reviewer",
                "title": "Reviewer",
                "inputs": ["patch_proposal", "test_results", "source_evidence"],
                "outputs": ["review_decision", "risk_notes"],
                "evidence_contract": ["claim-level evidence", "regression risk assessment"],
                "depends_on": ["tester"],
                "permission_boundary": "review_only",
            },
            {
                "role": "deploy",
                "title": "Deploy",
                "inputs": ["approved_review", "rollback_plan"],
                "outputs": ["deployment_receipt", "readback_evidence"],
                "evidence_contract": ["explicit authority", "rollback/readback proof"],
                "depends_on": ["reviewer"],
                "permission_boundary": "external_side_effect_human_gate",
            },
        ],
    }
}


def get_agent_role_topology_fixture(name: str) -> Dict[str, Any]:
    key = (name or "").strip().lower().replace("-", "_")
    try:
        return json.loads(json.dumps(_AGENT_ROLE_TOPOLOGIES[key]))
    except KeyError as exc:
        known = ", ".join(sorted(_AGENT_ROLE_TOPOLOGIES))
        raise ValueError(f"unknown agent role topology fixture: {name!r}; known: {known}") from exc


def compile_workflow_plan_v1(
    goal_spec: Dict[str, Any],
    *,
    pain_clusters: Optional[List[Dict[str, Any]]] = None,
    role_topology: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compile a GoalSpec into an inspectable, non-executing workflow plan.

    The compiler is deliberately dry-run only: it creates DAG-like nodes that
    preserve evidence and permission boundaries, but it never dispatches
    workers, mutates taskpool/mission/cron state, or opens external side
    effects. ``pain_clusters`` is the first consumer: public-source research
    fixtures become evidence-bound plan nodes.
    """
    if not isinstance(goal_spec, dict) or goal_spec.get("schema") != "goal_spec_v1":
        raise ValueError("goal_spec_v1 dict required")

    nodes: List[Dict[str, Any]] = _compile_goal_spec_step_nodes(goal_spec)
    for idx, cluster in enumerate(pain_clusters or [], start=1):
        cluster_id = str(cluster.get("id") or f"pain-{idx}")
        evidence = cluster.get("source_evidence") or []
        boundary = str(cluster.get("permission_boundary") or "")
        nodes.append(
            {
                "id": _stable_node_id(cluster_id, prefix="pain"),
                "title": str(cluster.get("title") or cluster_id),
                "kind": "pain_cluster_fixture",
                "depends_on": [],
                "state_object": str(cluster.get("state_object") or "unknown"),
                "failure_mode": str(cluster.get("failure_mode") or "unknown"),
                "permission_boundary": boundary or "evidence_only",
                "human_gate_required": _requires_human_gate(boundary),
                "opportunity_score": cluster.get("opportunity_score"),
                "source_evidence": evidence,
                "evidence_required": True,
                "execute": False,
                "rollback_boundary": "not_applicable_dry_run_only",
            }
        )

    if role_topology:
        if role_topology.get("schema") != "agent_role_topology_v1":
            raise ValueError("agent_role_topology_v1 dict required")
        for role in role_topology.get("roles") or []:
            role_name = str(role.get("role") or "unnamed")
            boundary = str(role.get("permission_boundary") or "evidence_only")
            nodes.append(
                {
                    "id": _stable_node_id(role_name, prefix="role"),
                    "title": str(role.get("title") or role_name),
                    "kind": "agent_role",
                    "role": role_name,
                    "depends_on": [_stable_node_id(dep, prefix="role") for dep in role.get("depends_on") or []],
                    "inputs": list(role.get("inputs") or []),
                    "outputs": list(role.get("outputs") or []),
                    "evidence_contract": list(role.get("evidence_contract") or []),
                    "permission_boundary": boundary,
                    "human_gate_required": _requires_human_gate(boundary),
                    "source_evidence": [role_topology.get("source_report")],
                    "source_verification": role_topology.get("source_verification") or {},
                    "evidence_required": True,
                    "execute": False,
                    "rollback_boundary": "not_applicable_dry_run_only",
                }
            )

    topological_order, edges = _topological_order_or_raise(nodes)

    return {
        "schema": "workflow_plan_v1",
        "dry_run": True,
        "execution_enabled": False,
        "goal_spec": goal_spec,
        "target_layer": goal_spec.get("target_layer") or "session",
        "nodes": nodes,
        "edges": edges,
        "graph": {
            "acyclic": True,
            "topological_order": topological_order,
        },
        "safety_boundary": (
            "Dry-run plan only; no external side effects, no worker dispatch, "
            "no lifecycle mutation, no taskpool/mission/cron writes."
        ),
    }


def compile_dynamic_workflow_run_v1(workflow_plan: Dict[str, Any]) -> Dict[str, Any]:
    """Compile a dry-run dynamic workflow runtime packet from a workflow plan.

    This is the Hermes-safe runtime landing for the Ultracode/dynamic-workflow
    pattern: a script-held state machine with DAG progress, evidence contracts,
    and cost/progress visibility. It is intentionally non-executing; node
    ``command``/``tool``/``handler`` refs are preserved for later governed
    adapters but are never invoked here.
    """
    if not isinstance(workflow_plan, dict) or workflow_plan.get("schema") != "workflow_plan_v1":
        raise ValueError("workflow_plan_v1 dict required")
    nodes = workflow_plan.get("nodes") or []
    if not isinstance(nodes, list):
        raise ValueError("workflow_plan_v1.nodes must be a list")

    node_state: Dict[str, Dict[str, Any]] = {}
    ready_node_ids: List[str] = []
    human_gated = 0
    blocked = 0
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("workflow_plan_v1.nodes entries must be dicts")
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            raise ValueError("workflow_plan_v1 node id is required")
        deps = [str(dep) for dep in (node.get("depends_on") or [])]
        is_human_gated = bool(node.get("human_gate_required"))
        if is_human_gated:
            status = "human_gated"
            human_gated += 1
        elif deps:
            status = "blocked"
            blocked += 1
        else:
            status = "ready"
            ready_node_ids.append(node_id)
        node_state[node_id] = {
            "status": status,
            "depends_on": deps,
            "evidence_contract": list(node.get("evidence_contract") or []),
            "permission_boundary": str(node.get("permission_boundary") or "evidence_only"),
            "execute": False,
            "worker_dispatches": 0,
            "evidence_refs": [],
            "last_reason": "dry_run_ready" if status == "ready" else f"dry_run_{status}",
        }

    nodes_by_id = {str(node.get("id")): node for node in nodes if isinstance(node, dict)}
    next_actions: List[Dict[str, Any]] = []
    for node_id in ready_node_ids:
        node = nodes_by_id.get(node_id, {})
        next_actions.append({
            "node_id": node_id,
            "title": str(node.get("title") or node_id),
            "execute": False,
            "required_evidence": list(node.get("evidence_contract") or []),
            "permission_boundary": str(node.get("permission_boundary") or "evidence_only"),
        })

    return {
        "schema": "dynamic_goal_workflow_run_v1",
        "mode": "dry_run",
        "run_id": f"goal_workflow_{uuid.uuid4().hex[:12]}",
        "script_runtime": "hermes_internal_state_machine",
        "execution_enabled": False,
        "worker_dispatch_enabled": False,
        "adapter_write_enabled": False,
        "state_mutations": [],
        "plan_schema": workflow_plan.get("schema"),
        "plan_node_count": len(nodes),
        "ready_node_ids": ready_node_ids,
        "node_state": node_state,
        "progress": {
            "total": len(nodes),
            "ready": len(ready_node_ids),
            "blocked": blocked,
            "completed": 0,
            "human_gated": human_gated,
        },
        "cost_visibility": {
            "worker_dispatches": 0,
            "tool_invocations": 0,
            "estimated_external_cost": 0,
            "currency": "USD",
        },
        "evidence_ledger": [],
        "next_actions": next_actions,
        "safety_boundary": (
            "Dynamic workflow dry-run only; keeps script-held state and progress "
            "visibility without executing commands, dispatching workers, or mutating "
            "taskpool/mission/cron/external state."
        ),
        "updated_at": datetime.now(tz=timezone.utc).isoformat(),
    }


def clear_goal(session_id: str) -> None:
    """Mark a goal cleared in the DB (preserved for audit, status=cleared)."""
    state = load_goal(session_id)
    if state is None:
        return
    state.status = "cleared"
    save_goal(session_id, state)


# ──────────────────────────────────────────────────────────────────────
# Judge
# ──────────────────────────────────────────────────────────────────────


def _truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "… [truncated]"


_JSON_OBJECT_RE = re.compile(r"\{.*?\}", re.DOTALL)
_COMMAND_EVIDENCE_RE = re.compile(
    r"(?im)\b(?:command|cmd|test(?:s| command)?|ran|run|执行|运行)\s*[:：].{0,240}?"
    r"(?:pytest|unittest|npm|pnpm|yarn|mvn|gradle|go test|cargo test|python(?:3)? -m|hermes|curl|git|passed|exit[_ -]?code|0 failed)"
)
_FILE_EVIDENCE_RE = re.compile(
    r"(?im)(?:\b(?:file|files|changed files?|artifact|path|diff_path|log_path)\s*[:：]\s*)"
    r"([^\n`]+(?:/[^\s,;`]+|\.(?:py|js|ts|tsx|jsx|md|json|yaml|yml|toml|txt|log|db)\b)[^\n`]*)"
)
_ABSOLUTE_FILE_RE = re.compile(
    r"(?m)(?:/Users/[^\s,;`]+|/tmp/[^\s,;`]+|~/[^\s,;`]+|\./[^\s,;`]+)"
)
_PROOF_EVIDENCE_RE = re.compile(
    r"(?im)\b(?:proof[_ -]?log|proof|evidence|receipt)\s*[:：]\s*[^\n`]+"
)
_NEEDS_HUMAN_RE = re.compile(
    r"(?im)\b(?:blocked|need(?:s)?\s+(?:human|user|operator)\s+(?:input|approval|decision)|"
    r"requires?\s+(?:human|user|operator)\s+(?:input|approval|decision)|"
    r"permission|credentials?|secret|captcha|2fa|approval)\b|"
    r"需要(?:人工|用户).{0,12}(?:输入|确认|审批|授权|决定)|人工介入|缺少(?:权限|凭证|密钥)"
)


def _unique_snippets(matches: List[str], *, limit: int = 5) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for match in matches:
        item = " ".join(str(match).strip().split())
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(_truncate(item, 240))
        if len(out) >= limit:
            break
    return out


def extract_goal_evidence_bundle(response: str) -> GoalEvidenceBundle:
    """Extract concrete proof refs from a goal-turn response.

    This is intentionally deterministic and conservative.  The LLM judge may
    provide a semantic done/continue opinion, but verified DONE requires this
    bundle to carry at least one command, file/artifact, or proof/evidence ref.
    """
    text = response or ""
    command_refs = _unique_snippets([m.group(0) for m in _COMMAND_EVIDENCE_RE.finditer(text)])
    file_refs = _unique_snippets(
        [m.group(1) for m in _FILE_EVIDENCE_RE.finditer(text)]
        + [m.group(0) for m in _ABSOLUTE_FILE_RE.finditer(text)]
    )
    proof_refs = _unique_snippets([m.group(0) for m in _PROOF_EVIDENCE_RE.finditer(text)])
    return GoalEvidenceBundle(
        command_refs=command_refs,
        file_refs=file_refs,
        proof_refs=proof_refs,
        needs_human=bool(_NEEDS_HUMAN_RE.search(text)),
    )


def _apply_goal_evidence_contract(
    verdict: str,
    reason: str,
    bundle: GoalEvidenceBundle,
) -> Tuple[str, str]:
    if bundle.needs_human:
        return "blocked", f"needs_human: {_truncate(reason, 220)}"
    if verdict == "done" and not bundle.has_proof:
        return (
            "continue",
            "needs_evidence: judge said done but the response lacks command/file/proof refs; "
            + _truncate(reason, 180),
        )
    return verdict, reason


def _goal_judge_max_tokens() -> int:
    """Resolve auxiliary.goal_judge.max_tokens, falling back to the default.

    ``load_config()`` is cached on the config file's (mtime, size), so calling
    this once per judge turn is cheap. A non-positive or non-int value falls
    back to the default rather than crashing the goal loop.
    """
    try:
        from hermes_cli.config import load_config

        cfg = load_config()
        value = (
            (cfg.get("auxiliary") or {})
            .get("goal_judge", {})
            .get("max_tokens", DEFAULT_JUDGE_MAX_TOKENS)
        )
        value = int(value)
        if value > 0:
            return value
    except Exception:
        pass
    return DEFAULT_JUDGE_MAX_TOKENS


def _parse_judge_response(raw: str) -> Tuple[bool, str, bool]:
    """Parse the judge's reply. Fail-open to ``(False, "<reason>", parse_failed)``.

    Returns ``(done, reason, parse_failed)``. ``parse_failed`` is True when the
    judge returned output that couldn't be interpreted as the expected JSON
    verdict (empty body, prose, malformed JSON). Callers use that flag to
    auto-pause after N consecutive parse failures so a weak judge model
    doesn't silently burn the turn budget.
    """
    if not raw:
        return False, "judge returned empty response", True

    text = raw.strip()

    # Strip markdown code fences the model may wrap JSON in.
    if text.startswith("```"):
        text = text.strip("`")
        # Peel off leading json/JSON/etc tag
        nl = text.find("\n")
        if nl != -1:
            text = text[nl + 1:]

    # First try: parse the whole blob.
    data: Optional[Dict[str, Any]] = None
    try:
        data = json.loads(text)
    except Exception:
        # Second try: pull the first JSON object out.
        match = _JSON_OBJECT_RE.search(text)
        if match:
            try:
                data = json.loads(match.group(0))
            except Exception:
                data = None

    if not isinstance(data, dict):
        return False, f"judge reply was not JSON: {_truncate(raw, 200)!r}", True

    done_val = data.get("done")
    if isinstance(done_val, str):
        done = done_val.strip().lower() in {"true", "yes", "1", "done"}
    else:
        done = bool(done_val)
    reason = str(data.get("reason") or "").strip()
    if not reason:
        reason = "no reason provided"
    return done, reason, False


def judge_goal(
    goal: str,
    last_response: str,
    *,
    timeout: float = DEFAULT_JUDGE_TIMEOUT,
    subgoals: Optional[List[str]] = None,
) -> Tuple[str, str, bool]:
    """Ask the auxiliary model whether the goal is satisfied.

    Returns ``(verdict, reason, parse_failed)`` where verdict is ``"done"``,
    ``"continue"``, or ``"skipped"`` (when the judge couldn't be reached).

    ``parse_failed`` is True only when the judge call succeeded but its output
    was unusable (empty or non-JSON). API/transport errors return False — they
    are transient and should fail-open silently. Callers use this flag to
    auto-pause after N consecutive parse failures (see
    ``DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES``).

    ``subgoals`` is an optional list of user-added criteria (from
    ``/subgoal``) that the judge must also factor into its DONE/CONTINUE
    decision. When non-empty the prompt switches to the with-subgoals
    template; otherwise behavior is identical to the original judge.

    This is deliberately fail-open: any error returns ``("continue", "...", False)``
    so a broken judge doesn't wedge progress — the turn budget and the
    consecutive-parse-failures auto-pause are the backstops.
    """
    if not goal.strip():
        return "skipped", "empty goal", False
    if not last_response.strip():
        # No substantive reply this turn — almost certainly not done yet.
        return "continue", "empty response (nothing to evaluate)", False

    try:
        from agent.auxiliary_client import get_auxiliary_extra_body, get_text_auxiliary_client
    except Exception as exc:
        logger.debug("goal judge: auxiliary client import failed: %s", exc)
        return "continue", "auxiliary client unavailable", False

    try:
        client, model = get_text_auxiliary_client("goal_judge")
    except Exception as exc:
        logger.debug("goal judge: get_text_auxiliary_client failed: %s", exc)
        return "continue", "auxiliary client unavailable", False

    if client is None or not model:
        return "continue", "no auxiliary client configured", False

    # Build the prompt — pick the with-subgoals variant when applicable.
    clean_subgoals = [s.strip() for s in (subgoals or []) if s and s.strip()]
    current_time = datetime.now(tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    if clean_subgoals:
        subgoals_block = "\n".join(
            f"- {i}. {text}" for i, text in enumerate(clean_subgoals, start=1)
        )
        prompt = JUDGE_USER_PROMPT_WITH_SUBGOALS_TEMPLATE.format(
            goal=_truncate(goal, 2000),
            subgoals_block=_truncate(subgoals_block, 2000),
            response=_truncate(last_response, _JUDGE_RESPONSE_SNIPPET_CHARS),
            current_time=current_time,
        )
    else:
        prompt = JUDGE_USER_PROMPT_TEMPLATE.format(
            goal=_truncate(goal, 2000),
            response=_truncate(last_response, _JUDGE_RESPONSE_SNIPPET_CHARS),
            current_time=current_time,
        )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=_goal_judge_max_tokens(),
            timeout=timeout,
            extra_body=get_auxiliary_extra_body() or None,
        )
    except Exception as exc:
        logger.info("goal judge: API call failed (%s) — falling through to continue", exc)
        return "continue", f"judge error: {type(exc).__name__}", False

    try:
        raw = resp.choices[0].message.content or ""
    except Exception:
        raw = ""

    done, reason, parse_failed = _parse_judge_response(raw)
    verdict = "done" if done else "continue"
    verdict, reason = _apply_goal_evidence_contract(
        verdict,
        reason,
        extract_goal_evidence_bundle(last_response),
    )
    logger.info("goal judge: verdict=%s reason=%s", verdict, _truncate(reason, 120))
    return verdict, reason, parse_failed


# ──────────────────────────────────────────────────────────────────────
# GoalManager — the orchestration surface CLI + gateway talk to
# ──────────────────────────────────────────────────────────────────────


class GoalManager:
    """Per-session goal state + continuation decisions.

    The CLI and gateway each hold one ``GoalManager`` per live session.

    Methods:

    - ``set(goal)`` — start a new standing goal.
    - ``clear()`` — remove the active goal.
    - ``pause()`` / ``resume()`` — explicit user controls.
    - ``status()`` — printable one-liner.
    - ``evaluate_after_turn(last_response)`` — call the judge, update state,
      and return a decision dict the caller uses to drive the next turn.
    - ``next_continuation_prompt()`` — the canonical user-role message to
      feed back into ``run_conversation``.
    """

    def __init__(self, session_id: str, *, default_max_turns: int = DEFAULT_MAX_TURNS):
        self.session_id = session_id
        self.default_max_turns = int(default_max_turns or DEFAULT_MAX_TURNS)
        self._state: Optional[GoalState] = load_goal(session_id)

    # --- introspection ------------------------------------------------

    @property
    def state(self) -> Optional[GoalState]:
        return self._state

    def is_active(self) -> bool:
        return self._state is not None and self._state.status == "active"

    def has_goal(self) -> bool:
        return self._state is not None and self._state.status in {"active", "paused"}

    def status_line(self) -> str:
        s = self._state
        if s is None or s.status in {"cleared",}:
            return "No active goal. Set one with /goal <text>."
        turns = f"{s.turns_used}/{s.max_turns} turns"
        sub = f", {len(s.subgoals)} subgoal{'s' if len(s.subgoals) != 1 else ''}" if s.subgoals else ""
        spec_bits = ""
        if s.route or s.spec or s.proof_refs or s.remaining_plan:
            route = (s.route or {}).get("target_layer") or (s.spec or {}).get("target_layer")
            criteria = len((s.spec or {}).get("success_criteria") or [])
            detail = []
            if route:
                detail.append(f"route={route}")
            if criteria:
                detail.append(f"criteria={criteria}")
            if s.proof_refs:
                detail.append(f"proof_refs={len(s.proof_refs)}")
            if s.remaining_plan:
                detail.append(f"remaining_plan={len(s.remaining_plan)}")
            if s.workflow_run:
                progress = s.workflow_run.get("progress") if isinstance(s.workflow_run, dict) else None
                total = (progress or {}).get("total") if isinstance(progress, dict) else None
                detail.append(f"workflow_run={total if total is not None else 'active'}")
            if detail:
                spec_bits = ", " + ", ".join(detail)
        if s.status == "active":
            return f"⊙ Goal (active, {turns}{sub}{spec_bits}): {s.goal}"
        if s.status == "paused":
            extra = f" — {s.paused_reason}" if s.paused_reason else ""
            return f"⏸ Goal (paused, {turns}{sub}{spec_bits}{extra}): {s.goal}"
        if s.status == "done":
            return f"✓ Goal done ({turns}{sub}{spec_bits}): {s.goal}"
        return f"Goal ({s.status}, {turns}{sub}{spec_bits}): {s.goal}"

    # --- mutation -----------------------------------------------------

    def set(
        self,
        goal: str,
        *,
        max_turns: Optional[int] = None,
        success_criteria: Optional[List[str]] = None,
        non_goals: Optional[List[str]] = None,
    ) -> GoalState:
        goal = (goal or "").strip()
        if not goal:
            raise ValueError("goal text is empty")
        resolved_max_turns = int(max_turns) if max_turns else self.default_max_turns
        spec = build_goal_spec(
            goal,
            max_turns=resolved_max_turns,
            success_criteria=success_criteria,
            non_goals=non_goals,
        )
        route = goal_route_decision(goal, success_criteria=spec.get("success_criteria") or [])
        state = GoalState(
            goal=goal,
            status="active",
            turns_used=0,
            max_turns=resolved_max_turns,
            created_at=time.time(),
            last_turn_at=0.0,
            spec=spec,
            route=route.to_dict(),
        )
        self._state = state
        save_goal(self.session_id, state)
        return state

    def promote_dry_run(self) -> Dict[str, Any]:
        """Return a non-mutating governed promotion candidate for the goal."""
        if self._state is None or not self.has_goal():
            raise RuntimeError("no active goal")
        spec = self._state.spec or build_goal_spec(
            self._state.goal,
            max_turns=self._state.max_turns,
            success_criteria=self._state.subgoals,
        )
        return compile_goal_promotion_dry_run(spec)

    def workflow_dry_run(self) -> Dict[str, Any]:
        """Compile and persist a safe dynamic-workflow runtime packet.

        This is runtime state, not just a pure compiler return: the packet is
        saved on the active goal and mirrored into the checkpoint sidecar so a
        resumed/compressed session can recover progress. It still does not
        execute node commands, dispatch workers, or mutate taskpool/mission/cron.
        """
        if self._state is None or not self.has_goal():
            raise RuntimeError("no active goal")
        spec = self._state.spec or build_goal_spec(
            self._state.goal,
            max_turns=self._state.max_turns,
            success_criteria=self._state.subgoals,
        )
        plan = compile_workflow_plan_v1(spec)
        run = compile_dynamic_workflow_run_v1(plan)
        self._state.workflow_run = run
        self._state.remaining_plan = [
            str(action.get("title") or action.get("node_id"))
            for action in run.get("next_actions", [])
            if isinstance(action, dict)
        ]
        save_goal(self.session_id, self._state)
        save_goal_checkpoint(self.session_id, self._state, reason="workflow-dry-run")
        return {
            "schema": "goal_dynamic_workflow_packet_v1",
            "dry_run": True,
            "execution_enabled": False,
            "plan": plan,
            "run": run,
            "safety_boundary": run.get("safety_boundary"),
        }

    def force_checkpoint(self, *, reason: str = "force") -> Dict[str, Any]:
        """Persist and return the goal checkpoint sidecar for this session."""
        if self._state is None or not self.has_goal():
            raise RuntimeError("no active goal")
        checkpoint = save_goal_checkpoint(self.session_id, self._state, reason=reason)
        if checkpoint is None:
            # Keep callers deterministic even if SessionDB is unavailable in a
            # nonstandard test/runtime harness.
            checkpoint = _build_goal_checkpoint(self.session_id, self._state, reason=reason)
        return checkpoint

    def pause(self, reason: str = "user-paused") -> Optional[GoalState]:
        if not self._state:
            return None
        self._state.status = "paused"
        self._state.paused_reason = reason
        save_goal(self.session_id, self._state)
        return self._state

    def resume(self, *, reset_budget: bool = True) -> Optional[GoalState]:
        if not self._state:
            return None
        self._state.status = "active"
        self._state.paused_reason = None
        if reset_budget:
            self._state.turns_used = 0
        save_goal(self.session_id, self._state)
        return self._state

    def clear(self) -> None:
        if self._state is None:
            return
        self._state.status = "cleared"
        save_goal(self.session_id, self._state)
        self._state = None

    def mark_done(self, reason: str) -> None:
        if not self._state:
            return
        self._state.status = "done"
        self._state.last_verdict = "done"
        self._state.last_reason = reason
        save_goal(self.session_id, self._state)

    # --- /subgoal user controls ---------------------------------------

    def add_subgoal(self, text: str) -> str:
        """Append a user-added criterion to the active goal. Requires
        ``has_goal()``; raises ``RuntimeError`` otherwise.

        Returns the cleaned text so the caller can show it back to the user.
        """
        if self._state is None or not self.has_goal():
            raise RuntimeError("no active goal")
        text = (text or "").strip()
        if not text:
            raise ValueError("subgoal text is empty")
        self._state.subgoals.append(text)
        save_goal(self.session_id, self._state)
        return text

    def remove_subgoal(self, index_1based: int) -> str:
        """Remove a subgoal by 1-based index. Returns the removed text."""
        if self._state is None or not self.has_goal():
            raise RuntimeError("no active goal")
        idx = int(index_1based) - 1
        if idx < 0 or idx >= len(self._state.subgoals):
            raise IndexError(
                f"index out of range (1..{len(self._state.subgoals)})"
            )
        removed = self._state.subgoals.pop(idx)
        save_goal(self.session_id, self._state)
        return removed

    def clear_subgoals(self) -> int:
        """Wipe all subgoals. Returns the previous count."""
        if self._state is None or not self.has_goal():
            raise RuntimeError("no active goal")
        prev = len(self._state.subgoals)
        self._state.subgoals = []
        save_goal(self.session_id, self._state)
        return prev

    def render_subgoals(self) -> str:
        """Public helper for the /subgoal slash command."""
        if self._state is None:
            return "(no active goal)"
        if not self._state.subgoals:
            return "(no subgoals — use /subgoal <text> to add criteria)"
        return self._state.render_subgoals_block()

    # --- the main entry point called after every turn -----------------

    def evaluate_after_turn(
        self,
        last_response: str,
        *,
        user_initiated: bool = True,
    ) -> Dict[str, Any]:
        """Run the judge and update state. Return a decision dict.

        ``user_initiated`` distinguishes a real user prompt (True) from a
        continuation prompt we fed ourselves (False). Both increment
        ``turns_used`` because both consume model budget.

        Decision keys:
          - ``status``: current goal status after update
          - ``should_continue``: bool — caller should fire another turn
          - ``continuation_prompt``: str or None
          - ``verdict``: "done" | "continue" | "blocked" | "skipped" | "inactive"
          - ``reason``: str
          - ``message``: user-visible one-liner to print/send
        """
        state = self._state
        if state is None or state.status != "active":
            return {
                "status": state.status if state else None,
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "inactive",
                "reason": "no active goal",
                "message": "",
            }

        # Count the turn that just finished.
        state.turns_used += 1
        state.last_turn_at = time.time()

        verdict, reason, parse_failed = judge_goal(
            state.goal, last_response, subgoals=state.subgoals or None
        )
        state.last_verdict = verdict
        state.last_reason = reason

        # Track consecutive judge parse failures. Reset on any usable reply,
        # including API / transport errors (parse_failed=False) so a flaky
        # network doesn't trip the auto-pause meant for bad judge models.
        if parse_failed:
            state.consecutive_parse_failures += 1
        else:
            state.consecutive_parse_failures = 0

        if verdict == "done":
            state.status = "done"
            save_goal(self.session_id, state)
            return {
                "status": "done",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "done",
                "reason": reason,
                "message": f"✓ Goal achieved: {reason}",
            }

        if verdict == "blocked":
            state.status = "paused"
            state.paused_reason = reason
            save_goal(self.session_id, state)
            return {
                "status": "paused",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "blocked",
                "reason": reason,
                "message": f"⏸ Goal paused — human input needed: {reason}",
            }

        # Auto-pause when the judge model can't produce the expected JSON
        # verdict N turns in a row. Points the user at the goal_judge config
        # so they can route this side task to a model that follows the
        # contract (e.g. google/gemini-3-flash-preview). Without this guard,
        # weak judge models burn the entire turn budget returning prose or
        # empty strings.
        if state.consecutive_parse_failures >= DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES:
            state.status = "paused"
            state.paused_reason = (
                f"judge model returned unparseable output {state.consecutive_parse_failures} turns in a row"
            )
            save_goal(self.session_id, state)
            return {
                "status": "paused",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "continue",
                "reason": reason,
                "message": (
                    f"⏸ Goal paused — the judge model ({state.consecutive_parse_failures} turns) "
                    "isn't returning the required JSON verdict. Route the judge to a stricter "
                    "model in ~/.hermes/config.yaml:\n"
                    "  auxiliary:\n"
                    "    goal_judge:\n"
                    "      provider: openrouter\n"
                    "      model: google/gemini-3-flash-preview\n"
                    "Then /goal resume to continue."
                ),
            }

        if state.turns_used >= state.max_turns:
            state.status = "paused"
            state.paused_reason = f"turn budget exhausted ({state.turns_used}/{state.max_turns})"
            save_goal(self.session_id, state)
            return {
                "status": "paused",
                "should_continue": False,
                "continuation_prompt": None,
                "verdict": "continue",
                "reason": reason,
                "message": (
                    f"⏸ Goal paused — {state.turns_used}/{state.max_turns} turns used. "
                    "Use /goal resume to keep going, or /goal clear to stop."
                ),
            }

        save_goal(self.session_id, state)
        return {
            "status": "active",
            "should_continue": True,
            "continuation_prompt": self.next_continuation_prompt(),
            "verdict": "continue",
            "reason": reason,
            "message": (
                f"↻ Continuing toward goal ({state.turns_used}/{state.max_turns}): {reason}"
            ),
        }

    def next_continuation_prompt(self) -> Optional[str]:
        if not self._state or self._state.status != "active":
            return None
        if self._state.subgoals:
            return CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE.format(
                goal=self._state.goal,
                subgoals_block=self._state.render_subgoals_block(),
            )
        return CONTINUATION_PROMPT_TEMPLATE.format(goal=self._state.goal)


# ──────────────────────────────────────────────────────────────────────
# Kanban worker goal loop
# ──────────────────────────────────────────────────────────────────────

# Continuation prompt fed back to a kanban goal-mode worker that has not
# yet completed/blocked its task. The card's own acceptance criteria are
# the goal — the worker already has the full task body in its first turn,
# so we keep this short and point it back at the lifecycle contract.
KANBAN_GOAL_CONTINUATION_TEMPLATE = (
    "[Continuing toward this kanban task — judge says it is not done yet]\n"
    "Reason: {reason}\n\n"
    "Take the next concrete step toward completing the task. When the work "
    "is genuinely finished, call kanban_complete with a summary. If you are "
    "blocked and need human input, call kanban_block with a reason. Do not "
    "stop without calling one of them."
)

# Fed when the judge believes the work is done but the worker never called
# kanban_complete / kanban_block. One explicit nudge to terminate the task
# the right way before the loop gives up.
KANBAN_GOAL_FINALIZE_TEMPLATE = (
    "[The work looks complete, but the task is still open]\n"
    "Reason: {reason}\n\n"
    "If the task is genuinely done, call kanban_complete now with a short "
    "summary of what you did. If something still blocks completion, call "
    "kanban_block with the reason instead."
)


def run_kanban_goal_loop(
    *,
    task_id: str,
    goal_text: str,
    run_turn,
    task_status_fn,
    block_fn,
    max_turns: int = DEFAULT_MAX_TURNS,
    first_response: str = "",
    log=None,
) -> Dict[str, Any]:
    """Drive a kanban worker through a Ralph-style goal loop.

    The dispatcher spawns a goal-mode worker exactly like a normal worker
    (``hermes -p <profile> chat -q "work kanban task <id>"``). The worker's
    first turn has already run by the time this is called; ``first_response``
    is that turn's reply. From here we:

    1. Check whether the worker already terminated the task (called
       ``kanban_complete`` / ``kanban_block``). If so, stop — nothing to do.
    2. Otherwise judge the latest response against ``goal_text`` (the card's
       title + body). ``continue`` → feed a continuation prompt and run
       another turn IN THE SAME SESSION via ``run_turn``. ``done`` but the
       task is still open → one explicit "call kanban_complete" nudge.
    3. When the turn budget is exhausted and the worker still hasn't
       terminated the task, ``block_fn`` is invoked so the card lands in a
       sticky ``blocked`` state for human review (NOT a silent exit).

    This function performs NO SessionDB persistence — a worker process is
    ephemeral, so the turn budget lives in a local counter. It is fully
    decoupled from the CLI for testability: callers inject ``run_turn``
    (str -> str), ``task_status_fn`` (() -> str|None), and ``block_fn``
    (reason: str -> None).

    Returns a decision dict: ``{"outcome", "turns_used", "reason"}`` where
    outcome is one of ``"completed_by_worker"``, ``"blocked_budget"``,
    ``"blocked_by_worker"``, ``"blocked_by_judge"``, or ``"stopped"``.
    """

    def _log(msg: str) -> None:
        if log is not None:
            try:
                log(msg)
            except Exception:
                pass

    max_turns = int(max_turns or DEFAULT_MAX_TURNS)
    if max_turns < 1:
        max_turns = DEFAULT_MAX_TURNS

    last_response = first_response or ""
    # The first turn already consumed one unit of budget.
    turns_used = 1
    nudged_to_finalize = False

    while True:
        # Did the worker terminate the task itself this turn?
        try:
            status = task_status_fn()
        except Exception as exc:
            _log(f"kanban goal loop: status check failed ({exc}); stopping")
            return {"outcome": "stopped", "turns_used": turns_used, "reason": "status check failed"}

        if status == "done":
            _log(f"kanban goal loop: task {task_id} completed by worker after {turns_used} turn(s)")
            return {"outcome": "completed_by_worker", "turns_used": turns_used, "reason": "worker completed the task"}
        if status == "blocked":
            _log(f"kanban goal loop: task {task_id} blocked by worker after {turns_used} turn(s)")
            return {"outcome": "blocked_by_worker", "turns_used": turns_used, "reason": "worker blocked the task"}
        if status not in ("running", "ready"):
            # Reclaimed / archived / unexpected — let the dispatcher own it.
            _log(f"kanban goal loop: task {task_id} status={status!r}; stopping")
            return {"outcome": "stopped", "turns_used": turns_used, "reason": f"status={status}"}

        # Still open — judge whether the latest response satisfies the card.
        verdict, reason, _parse_failed = judge_goal(goal_text, last_response)
        _log(f"kanban goal loop: turn {turns_used}/{max_turns} verdict={verdict} reason={_truncate(reason, 120)}")

        if verdict == "blocked":
            _log(f"kanban goal loop: task {task_id} judged blocked/needs-human; blocking")
            try:
                block_fn(
                    f"Goal-mode judge classified the worker response as needing human input: "
                    f"{_truncate(reason, 300)}"
                )
            except Exception as exc:
                _log(f"kanban goal loop: block_fn failed ({exc})")
            return {"outcome": "blocked_by_judge", "turns_used": turns_used, "reason": reason}

        if verdict == "done":
            if nudged_to_finalize:
                # Already asked once to call kanban_complete and it still
                # didn't — block for review rather than spin.
                _log(f"kanban goal loop: task {task_id} judged done but worker won't finalize; blocking")
                try:
                    block_fn(
                        f"Goal-mode worker's output looked complete but it never "
                        f"called kanban_complete after a finalize nudge ({reason})."
                    )
                except Exception as exc:
                    _log(f"kanban goal loop: block_fn failed ({exc})")
                return {"outcome": "blocked_budget", "turns_used": turns_used, "reason": "judged done, never finalized"}
            prompt = KANBAN_GOAL_FINALIZE_TEMPLATE.format(reason=_truncate(reason, 400))
            nudged_to_finalize = True
        else:
            prompt = KANBAN_GOAL_CONTINUATION_TEMPLATE.format(reason=_truncate(reason, 400))

        # Budget check BEFORE spending another turn.
        if turns_used >= max_turns:
            _log(f"kanban goal loop: task {task_id} exhausted {turns_used}/{max_turns} turns; blocking")
            try:
                block_fn(
                    f"Goal-mode worker exhausted its turn budget "
                    f"({turns_used}/{max_turns}) without completing the task. "
                    f"Last judge verdict: {_truncate(reason, 300)}"
                )
            except Exception as exc:
                _log(f"kanban goal loop: block_fn failed ({exc})")
            return {"outcome": "blocked_budget", "turns_used": turns_used, "reason": "turn budget exhausted"}

        # Run another turn in the same session.
        try:
            last_response = run_turn(prompt) or ""
        except Exception as exc:
            _log(f"kanban goal loop: run_turn failed ({exc}); stopping")
            return {"outcome": "stopped", "turns_used": turns_used, "reason": f"run_turn error: {type(exc).__name__}"}
        turns_used += 1


__all__ = [
    "GoalState",
    "GoalManager",
    "CONTINUATION_PROMPT_TEMPLATE",
    "CONTINUATION_PROMPT_WITH_SUBGOALS_TEMPLATE",
    "JUDGE_USER_PROMPT_TEMPLATE",
    "JUDGE_USER_PROMPT_WITH_SUBGOALS_TEMPLATE",
    "KANBAN_GOAL_CONTINUATION_TEMPLATE",
    "KANBAN_GOAL_FINALIZE_TEMPLATE",
    "DEFAULT_MAX_TURNS",
    "load_goal",
    "save_goal",
    "load_goal_checkpoint",
    "save_goal_checkpoint",
    "rebind_goal_checkpoint",
    "clear_goal",
    "judge_goal",
    "run_kanban_goal_loop",
]
