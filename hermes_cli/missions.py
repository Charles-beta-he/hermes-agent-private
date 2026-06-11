"""Mission control-plane primitives for long-running Hermes directions.

A Mission is a durable, default-off objective contract for high-level user
directions.  It does not execute arbitrary agent work by itself.  Instead it
records direction, budgets, stage cadence, decision requests, and evidence/event
history, then produces bounded prompts that a scheduler or human can hand to the
normal Hermes agent loop.

Design invariants:
- default-off: newly-created missions are paused unless explicitly enabled;
- evidence-first: progress is append-only mission_events, not chat memory;
- human gate: unresolved decision requests block ticks;
- bounded autonomy: every mission must carry an explicit budget boundary;
- no external side effects here: this module only mutates the local mission DB.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

VALID_MISSION_STATUSES = {"active", "paused", "blocked_on_human", "done", "failed", "cancelled"}
ACTIVE_MISSION_STATUSES = {"active", "blocked_on_human"}
VALID_EVENT_TYPES = {
    "mission_created",
    "mission_enabled",
    "mission_paused",
    "mission_resumed",
    "mission_cancelled",
    "mission_completed",
    "mission_failed",
    "mission_cron_installed",
    "tick_dry_run",
    "tick_prompt_created",
    "stage_report",
    "decision_requested",
    "decision_recorded",
    "budget_exhausted",
    "flow_signal_enqueued",
    "flow_signal_dispatched",
    "maintenance_tick",
}

ACTIONABLE_FLOW_EVENT_TYPES = {
    "mission_enabled",
    "mission_resumed",
    "decision_recorded",
    "stage_report",
}


@dataclass
class MissionBudget:
    """Hard budget boundary for a long-running mission."""

    max_ticks: int | None = None
    max_turns_per_tick: int | None = None
    max_total_usd: float | None = None

    def validate(self) -> None:
        if self.max_ticks is None:
            raise ValueError("mission budget requires --max-ticks as an enforceable tick boundary")
        if self.max_ticks <= 0:
            raise ValueError("--max-ticks must be > 0")
        if self.max_turns_per_tick is not None and self.max_turns_per_tick <= 0:
            raise ValueError("--max-turns-per-tick must be > 0")
        if self.max_total_usd is not None and self.max_total_usd <= 0:
            raise ValueError("--max-total-usd must be > 0")


@dataclass
class Mission:
    mission_id: str
    direction: str
    status: str
    cadence: str
    deliver: str
    budget: MissionBudget
    stage: str = "intake"
    constraints: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    success_criteria: list[str] = field(default_factory=list)
    allowed_actions: list[str] = field(default_factory=list)
    blocked_actions: list[str] = field(default_factory=list)
    plan_ref: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0


@dataclass
class DecisionRequest:
    decision_id: str
    mission_id: str
    question: str
    options: list[str]
    recommended_option: str = ""
    status: str = "open"
    answer: str = ""
    created_at: float = 0.0
    answered_at: float | None = None


@dataclass
class MissionFlowSignal:
    """Pending event-driven trigger for a mission advancement tick."""

    signal_id: str
    mission_id: str
    source_event_id: str
    signal_type: str
    status: str = "pending"
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    consumed_at: float | None = None


def _now() -> float:
    return time.time()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _mission_db_path(path: str | Path | None = None) -> Path:
    if path:
        return Path(path).expanduser()
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home()).expanduser() / "missions.db"
    except Exception:
        return Path.home() / ".hermes" / "missions.db"


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home()).expanduser()
    except Exception:
        return Path.home() / ".hermes"


def mission_tick_script_name(mission_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in mission_id)
    return f"mission_tick_{safe}.py"


def render_mission_tick_script(mission_id: str) -> str:
    """Return the per-mission cron data-collection script content."""

    return f'''#!/usr/bin/env python3
"""Generated Hermes mission tick script for {mission_id}."""
from __future__ import annotations

import json
import sys

from hermes_cli.missions import MissionStore

MISSION_ID = {mission_id!r}


def main() -> int:
    store = MissionStore()
    result = store.tick(MISSION_ID, dry_run=False)
    status = result.get("status")
    if status in {{"prompt_created", "dry_run"}} and result.get("prompt"):
        print(result["prompt"])
        return 0
    if status == "blocked_on_human":
        print("[Hermes Mission blocked on human decision]")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if status == "budget_exhausted":
        print("[Hermes Mission budget exhausted]")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    # No actionable prompt. Empty stdout keeps agent cron quiet when configured
    # as a watchdog; explicit status is kept in the mission ledger.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def install_mission_tick_script(mission_id: str, *, scripts_dir: str | Path | None = None) -> Path:
    """Install/update the per-mission cron script under HERMES_HOME/scripts."""

    base = Path(scripts_dir).expanduser() if scripts_dir else _hermes_home() / "scripts"
    base.mkdir(parents=True, exist_ok=True)
    path = base / mission_tick_script_name(mission_id)
    path.write_text(render_mission_tick_script(mission_id), encoding="utf-8")
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


class MissionStore:
    """SQLite-backed mission ledger."""

    _TRANSITIONS = {
        "paused": {"active", "cancelled", "done", "failed", "blocked_on_human"},
        "active": {"paused", "blocked_on_human", "done", "failed", "cancelled"},
        "blocked_on_human": {"active", "paused", "failed", "cancelled"},
        "done": set(),
        "failed": set(),
        "cancelled": set(),
    }

    def __init__(self, path: str | Path | None = None):
        self.path = _mission_db_path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS missions (
                    mission_id TEXT PRIMARY KEY,
                    direction TEXT NOT NULL,
                    status TEXT NOT NULL,
                    cadence TEXT NOT NULL,
                    deliver TEXT NOT NULL,
                    budget_json TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    constraints_json TEXT NOT NULL,
                    non_goals_json TEXT NOT NULL,
                    success_criteria_json TEXT NOT NULL,
                    allowed_actions_json TEXT NOT NULL,
                    blocked_actions_json TEXT NOT NULL,
                    plan_ref TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mission_events (
                    event_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY(mission_id) REFERENCES missions(mission_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mission_decisions (
                    decision_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    options_json TEXT NOT NULL,
                    recommended_option TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    answer TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    answered_at REAL,
                    FOREIGN KEY(mission_id) REFERENCES missions(mission_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mission_flow_signals (
                    signal_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    source_event_id TEXT NOT NULL UNIQUE,
                    signal_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    consumed_at REAL,
                    FOREIGN KEY(mission_id) REFERENCES missions(mission_id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mission_events_mission_time ON mission_events(mission_id, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mission_decisions_open ON mission_decisions(mission_id, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mission_flow_signals_pending ON mission_flow_signals(mission_id, status, created_at)")

    def create_mission(
        self,
        *,
        direction: str,
        cadence: str,
        deliver: str = "local",
        budget: MissionBudget,
        constraints: Iterable[str] = (),
        non_goals: Iterable[str] = (),
        success_criteria: Iterable[str] = (),
        allowed_actions: Iterable[str] = (),
        blocked_actions: Iterable[str] = (),
        enable: bool = False,
        mission_id: str | None = None,
    ) -> Mission:
        direction = direction.strip()
        if not direction:
            raise ValueError("mission direction is required")
        if not cadence.strip():
            raise ValueError("mission cadence is required")
        budget.validate()
        mission_id = mission_id or f"msn_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        now = _now()
        status = "active" if enable else "paused"
        blocked = list(blocked_actions) or default_blocked_actions()
        mission = Mission(
            mission_id=mission_id,
            direction=direction,
            status=status,
            cadence=cadence.strip(),
            deliver=deliver.strip() or "local",
            budget=budget,
            constraints=[str(x).strip() for x in constraints if str(x).strip()],
            non_goals=[str(x).strip() for x in non_goals if str(x).strip()],
            success_criteria=[str(x).strip() for x in success_criteria if str(x).strip()],
            allowed_actions=[str(x).strip() for x in allowed_actions if str(x).strip()],
            blocked_actions=blocked,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO missions (
                    mission_id, direction, status, cadence, deliver, budget_json, stage,
                    constraints_json, non_goals_json, success_criteria_json,
                    allowed_actions_json, blocked_actions_json, plan_ref, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._mission_values(mission),
            )
        self.append_event(
            mission_id,
            "mission_created",
            "mission created" + (" and enabled" if enable else " paused by default"),
            {"default_off": not enable, "cadence": mission.cadence, "deliver": mission.deliver},
        )
        if enable:
            self.append_event(mission_id, "mission_enabled", "mission explicitly enabled", {})
        return self.get_mission(mission_id)  # type: ignore[return-value]

    def _mission_values(self, mission: Mission) -> tuple[Any, ...]:
        return (
            mission.mission_id,
            mission.direction,
            mission.status,
            mission.cadence,
            mission.deliver,
            _json_dumps(asdict(mission.budget)),
            mission.stage,
            _json_dumps(mission.constraints),
            _json_dumps(mission.non_goals),
            _json_dumps(mission.success_criteria),
            _json_dumps(mission.allowed_actions),
            _json_dumps(mission.blocked_actions),
            mission.plan_ref,
            mission.created_at,
            mission.updated_at,
        )

    def get_mission(self, mission_id: str) -> Mission | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM missions WHERE mission_id = ?", (mission_id,)).fetchone()
        return self._row_to_mission(row) if row else None

    def list_missions(self, include_done: bool = False) -> list[Mission]:
        if include_done:
            query = "SELECT * FROM missions ORDER BY updated_at DESC"
            params: tuple[Any, ...] = ()
        else:
            placeholders = ",".join("?" for _ in ACTIVE_MISSION_STATUSES | {"paused"})
            query = f"SELECT * FROM missions WHERE status IN ({placeholders}) ORDER BY updated_at DESC"
            params = tuple(sorted(ACTIVE_MISSION_STATUSES | {"paused"}))
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_mission(row) for row in rows]

    def set_status(self, mission_id: str, status: str, reason: str = "") -> Mission:
        if status not in VALID_MISSION_STATUSES:
            raise ValueError(f"invalid mission status: {status}")
        current = self.get_mission(mission_id)
        if not current:
            raise KeyError(mission_id)
        if status != current.status and status not in self._TRANSITIONS.get(current.status, set()):
            raise ValueError(f"illegal mission transition: {current.status} -> {status}")
        now = _now()
        with self._connect() as conn:
            conn.execute("UPDATE missions SET status = ?, updated_at = ? WHERE mission_id = ?", (status, now, mission_id))
        event_type = {
            "active": "mission_resumed",
            "paused": "mission_paused",
            "cancelled": "mission_cancelled",
            "done": "mission_completed",
            "failed": "mission_failed",
        }.get(status, "mission_paused")
        self.append_event(mission_id, event_type, reason or f"status -> {status}", {"status": status})
        return self.get_mission(mission_id)  # type: ignore[return-value]

    def append_event(
        self,
        mission_id: str,
        event_type: str,
        summary: str = "",
        payload: dict[str, Any] | None = None,
        *,
        enqueue_flow: bool = True,
    ) -> dict[str, Any]:
        if event_type not in VALID_EVENT_TYPES:
            raise ValueError(f"invalid mission event type: {event_type}")
        if not self.get_mission(mission_id):
            raise KeyError(mission_id)
        event = {
            "event_id": f"evt_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}",
            "mission_id": mission_id,
            "event_type": event_type,
            "summary": summary,
            "payload": payload or {},
            "created_at": _now(),
        }
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO mission_events (event_id, mission_id, event_type, summary, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (event["event_id"], mission_id, event_type, summary, _json_dumps(payload or {}), event["created_at"]),
            )
            conn.execute("UPDATE missions SET updated_at = ? WHERE mission_id = ?", (event["created_at"], mission_id))
        if enqueue_flow and event_type in ACTIONABLE_FLOW_EVENT_TYPES:
            self.enqueue_flow_signal(event)
            self.dispatch_pending_flow_signals(mission_id)
        return event

    def enqueue_flow_signal(self, event: dict[str, Any]) -> MissionFlowSignal:
        """Append a pending signal for an actionable mission event.

        The signal is the event-driven SSOT edge.  It records that a state change
        wants an immediate advancement tick; the actual agent work is still
        gated by the existing mission cron/agent runner.
        """

        signal = MissionFlowSignal(
            signal_id=f"sig_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}",
            mission_id=event["mission_id"],
            source_event_id=event["event_id"],
            signal_type=event["event_type"],
            payload={"summary": event.get("summary", ""), "event_payload": event.get("payload") or {}},
            created_at=_now(),
        )
        inserted = False
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO mission_flow_signals (
                        signal_id, mission_id, source_event_id, signal_type, status, payload_json, created_at, consumed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        signal.signal_id,
                        signal.mission_id,
                        signal.source_event_id,
                        signal.signal_type,
                        signal.status,
                        _json_dumps(signal.payload),
                        signal.created_at,
                        signal.consumed_at,
                    ),
                )
                inserted = True
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "SELECT * FROM mission_flow_signals WHERE source_event_id = ?",
                    (signal.source_event_id,),
                ).fetchone()
                if row:
                    return self._row_to_flow_signal(row)
                raise
        if inserted:
            self.append_event(
                signal.mission_id,
                "flow_signal_enqueued",
                f"event-driven signal enqueued for {signal.signal_type}",
                {"signal_id": signal.signal_id, "source_event_id": signal.source_event_id, "signal_type": signal.signal_type},
                enqueue_flow=False,
            )
        return signal

    def pending_flow_signals(self, mission_id: str, *, limit: int = 20) -> list[MissionFlowSignal]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM mission_flow_signals
                WHERE mission_id = ? AND status = 'pending'
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (mission_id, int(limit)),
            ).fetchall()
        return [self._row_to_flow_signal(row) for row in rows]

    def stale_flow_signals(self, mission_id: str, *, older_than_seconds: int = 900, limit: int = 20) -> list[MissionFlowSignal]:
        """Return pending flow signals old enough to need watchdog attention."""

        cutoff = _now() - max(0, int(older_than_seconds))
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM mission_flow_signals
                WHERE mission_id = ? AND status = 'pending' AND created_at <= ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (mission_id, cutoff, int(limit)),
            ).fetchall()
        return [self._row_to_flow_signal(row) for row in rows]

    def consume_flow_signal(self, signal_id: str) -> MissionFlowSignal:
        now = _now()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM mission_flow_signals WHERE signal_id = ?", (signal_id,)).fetchone()
            if not row:
                raise KeyError(signal_id)
            if row["status"] == "pending":
                conn.execute(
                    "UPDATE mission_flow_signals SET status = 'consumed', consumed_at = ? WHERE signal_id = ?",
                    (now, signal_id),
                )
                row = conn.execute("SELECT * FROM mission_flow_signals WHERE signal_id = ?", (signal_id,)).fetchone()
        return self._row_to_flow_signal(row)

    def dispatch_pending_flow_signals(self, mission_id: str) -> dict[str, Any]:
        """Ask existing mission cron jobs to run soon when pending signals exist.

        This does not execute the agent inline.  It only moves already-installed
        mission cron jobs to ``next_run_at=now`` so cron remains the governed
        runner while state changes provide the primary trigger.
        """

        pending = self.pending_flow_signals(mission_id, limit=1)
        if not pending:
            return {"status": "noop", "reason": "no_pending_signals", "triggered_jobs": []}
        jobs = self._find_cron_jobs_for_mission(mission_id)
        triggered: list[str] = []
        try:
            from cron.jobs import trigger_job
        except Exception:
            trigger_job = None  # type: ignore[assignment]
        if trigger_job is not None:
            for job in jobs:
                job_id = str(job.get("id") or "").strip()
                if not job_id:
                    continue
                result = trigger_job(job_id)
                if result:
                    triggered.append(job_id)
        if triggered:
            self.append_event(
                mission_id,
                "flow_signal_dispatched",
                f"event-driven signal scheduled {len(triggered)} cron job(s)",
                {"signal_id": pending[0].signal_id, "triggered_jobs": triggered},
                enqueue_flow=False,
            )
            return {"status": "dispatched", "signal_id": pending[0].signal_id, "triggered_jobs": triggered}
        return {"status": "pending_no_cron", "signal_id": pending[0].signal_id, "triggered_jobs": []}

    def list_events(self, mission_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM mission_events WHERE mission_id = ? ORDER BY created_at DESC LIMIT ?",
                (mission_id, int(limit)),
            ).fetchall()
        events = []
        for row in rows:
            events.append(self._event_from_row(row))
        return events

    def request_decision(
        self,
        mission_id: str,
        question: str,
        options: Iterable[str] = (),
        recommended_option: str = "",
    ) -> DecisionRequest:
        current = self.get_mission(mission_id)
        if not current:
            raise KeyError(mission_id)
        if "blocked_on_human" not in self._TRANSITIONS.get(current.status, set()) and current.status != "blocked_on_human":
            raise ValueError(f"cannot request decision for mission in terminal state: {current.status}")
        question = question.strip()
        if not question:
            raise ValueError("decision question is required")
        now = _now()
        decision = DecisionRequest(
            decision_id=f"dec_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}",
            mission_id=mission_id,
            question=question,
            options=[str(x).strip() for x in options if str(x).strip()],
            recommended_option=recommended_option.strip(),
            created_at=now,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO mission_decisions (decision_id, mission_id, question, options_json, recommended_option, status, answer, created_at, answered_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    decision.decision_id,
                    mission_id,
                    decision.question,
                    _json_dumps(decision.options),
                    decision.recommended_option,
                    decision.status,
                    decision.answer,
                    decision.created_at,
                    decision.answered_at,
                ),
            )
            conn.execute("UPDATE missions SET status = ?, updated_at = ? WHERE mission_id = ?", ("blocked_on_human", now, mission_id))
        self.append_event(
            mission_id,
            "decision_requested",
            question,
            {"decision_id": decision.decision_id, "options": decision.options, "recommended_option": decision.recommended_option},
        )
        return decision

    def answer_decision(self, decision_id: str, answer: str) -> DecisionRequest:
        answer = answer.strip()
        if not answer:
            raise ValueError("decision answer is required")
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM mission_decisions WHERE decision_id = ?", (decision_id,)).fetchone()
        if not row:
            raise KeyError(decision_id)
        if row["status"] != "open":
            raise ValueError(f"decision is not open: {decision_id}")
        mission = self.get_mission(row["mission_id"])
        if not mission:
            raise KeyError(row["mission_id"])
        if mission.status in {"done", "failed", "cancelled"}:
            raise ValueError(f"cannot answer decision for terminal mission: {mission.status}")
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE mission_decisions SET status = ?, answer = ?, answered_at = ? WHERE decision_id = ?",
                ("answered", answer, now, decision_id),
            )
            remaining = conn.execute(
                "SELECT COUNT(*) AS n FROM mission_decisions WHERE mission_id = ? AND status = 'open' AND decision_id != ?",
                (row["mission_id"], decision_id),
            ).fetchone()
            remaining_open = int(remaining["n"] if remaining else 0)
            next_status = "active" if mission.status == "blocked_on_human" and remaining_open == 0 else mission.status
            conn.execute("UPDATE missions SET status = ?, updated_at = ? WHERE mission_id = ?", (next_status, now, row["mission_id"]))
        self.append_event(row["mission_id"], "decision_recorded", answer, {"decision_id": decision_id})
        return self.get_decision(decision_id)  # type: ignore[return-value]

    def get_decision(self, decision_id: str) -> DecisionRequest | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM mission_decisions WHERE decision_id = ?", (decision_id,)).fetchone()
        return self._row_to_decision(row) if row else None

    def open_decisions(self, mission_id: str) -> list[DecisionRequest]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM mission_decisions WHERE mission_id = ? AND status = 'open' ORDER BY created_at ASC",
                (mission_id,),
            ).fetchall()
        return [self._row_to_decision(row) for row in rows]

    def inbox(self, *, include_done: bool = False) -> list[dict[str, Any]]:
        terminal = {"done", "failed", "cancelled"}
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT d.*, m.direction, m.status AS mission_status, m.stage
                FROM mission_decisions d
                JOIN missions m ON m.mission_id = d.mission_id
                WHERE d.status = 'open'
                ORDER BY d.created_at ASC
                """
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            if not include_done and row["mission_status"] in terminal:
                continue
            decision = self._row_to_decision(row)
            items.append(
                {
                    "mission_id": decision.mission_id,
                    "mission_status": row["mission_status"],
                    "stage": row["stage"],
                    "direction": row["direction"],
                    "decision": decision_to_dict(decision),
                    "decide_command": render_decision_next_command(decision),
                }
            )
        return items

    def board(self, *, include_done: bool = False) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for mission in self.list_missions(include_done=include_done):
            open_decisions = self.open_decisions(mission.mission_id)
            used_ticks = self.count_events(mission.mission_id, {"tick_prompt_created"})
            cron_events = self.list_events_by_type(mission.mission_id, "mission_cron_installed", limit=1)
            max_ticks = mission.budget.max_ticks
            cron_installed = bool(cron_events)
            if open_decisions:
                next_step = f"decide {open_decisions[0].decision_id}"
            elif mission.status == "paused":
                next_step = f"hermes mission resume {mission.mission_id}"
            elif mission.status == "active" and not cron_installed:
                next_step = f"hermes mission install-cron {mission.mission_id}"
            elif max_ticks is not None and used_ticks >= max_ticks:
                next_step = f"hermes mission report {mission.mission_id}"
            else:
                next_step = f"hermes mission report {mission.mission_id}"
            rows.append(
                {
                    "mission_id": mission.mission_id,
                    "status": mission.status,
                    "stage": mission.stage,
                    "direction": mission.direction,
                    "ticks_used": used_ticks,
                    "ticks_max": max_ticks,
                    "cron_installed": cron_installed,
                    "needs_user": bool(open_decisions),
                    "open_decision_id": open_decisions[0].decision_id if open_decisions else None,
                    "next_step": next_step,
                }
            )
        return rows

    def list_events_by_type(self, mission_id: str, event_type: str, *, limit: int = 20) -> list[dict[str, Any]]:
        if event_type not in VALID_EVENT_TYPES:
            raise ValueError(f"invalid mission event type: {event_type}")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM mission_events WHERE mission_id = ? AND event_type = ? ORDER BY created_at DESC LIMIT ?",
                (mission_id, event_type, int(limit)),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def doctor(self, mission_id: str) -> dict[str, Any]:
        mission = self.get_mission(mission_id)
        if not mission:
            raise KeyError(mission_id)
        open_decisions = self.open_decisions(mission_id)
        used_ticks = self.count_events(mission_id, {"tick_prompt_created"})
        pending_signals = self.pending_flow_signals(mission_id, limit=20)
        stale_signals = self.stale_flow_signals(mission_id, limit=20)
        script_path = _hermes_home() / "scripts" / mission_tick_script_name(mission_id)
        cron_jobs = self._find_cron_jobs_for_mission(mission_id)
        checks = []
        def add(name: str, status: str, detail: str = "") -> None:
            checks.append({"name": name, "status": status, "detail": detail})
        add("mission exists", "ok", mission_id)
        add("status", "ok" if mission.status == "active" else "warn", mission.status)
        add("open decisions", "warn" if open_decisions else "ok", str(len(open_decisions)))
        budget_status = "ok"
        if mission.budget.max_ticks is not None and used_ticks >= mission.budget.max_ticks:
            budget_status = "warn"
        add("tick budget", budget_status, f"{used_ticks}/{mission.budget.max_ticks}")
        add("pending flow signals", "warn" if pending_signals and not cron_jobs else "ok", str(len(pending_signals)))
        add("stale flow signals", "warn" if stale_signals else "ok", str(len(stale_signals)))
        add("tick script", "ok" if script_path.exists() else "warn", str(script_path))
        add("cron job", "ok" if cron_jobs else "warn", f"{len(cron_jobs)} matching job(s)")
        return {
            "mission": mission_to_dict(mission),
            "checks": checks,
            "open_decisions": [decision_to_dict(d) for d in open_decisions],
            "pending_flow_signals": [flow_signal_to_dict(s) for s in pending_signals],
            "stale_flow_signals": [flow_signal_to_dict(s) for s in stale_signals],
            "cron_jobs": cron_jobs,
            "script_path": str(script_path),
            "next_step": self._doctor_next_step(mission, open_decisions, bool(cron_jobs), used_ticks, bool(stale_signals)),
        }

    def _doctor_next_step(
        self,
        mission: Mission,
        open_decisions: list[DecisionRequest],
        has_cron: bool,
        used_ticks: int,
        has_stale_signals: bool = False,
    ) -> str:
        if open_decisions:
            return render_decision_next_command(open_decisions[0])
        if mission.status == "paused":
            return f"hermes mission resume {mission.mission_id}"
        if mission.status != "active":
            return f"hermes mission report {mission.mission_id}"
        if mission.budget.max_ticks is not None and used_ticks >= mission.budget.max_ticks:
            return f"hermes mission report {mission.mission_id}"
        if not has_cron:
            return f"hermes mission install-cron {mission.mission_id}"
        if has_stale_signals:
            return f"hermes mission dispatch-events {mission.mission_id}"
        return f"hermes mission report {mission.mission_id}"

    def _find_cron_jobs_for_mission(self, mission_id: str) -> list[dict[str, Any]]:
        jobs_file = _hermes_home() / "cron" / "jobs.json"
        if not jobs_file.exists():
            return []
        try:
            raw = json.loads(jobs_file.read_text(encoding="utf-8"))
        except Exception:
            return []
        if isinstance(raw, dict):
            jobs = raw.get("jobs", [])
        elif isinstance(raw, list):
            jobs = raw
        else:
            jobs = []
        if not isinstance(jobs, list):
            return []
        script_name = mission_tick_script_name(mission_id)
        matches = []
        for job in jobs:
            if not isinstance(job, dict):
                continue
            if job.get("script") == script_name or mission_id in str(job.get("name", "")) or mission_id in str(job.get("prompt", "")):
                matches.append({
                    "id": job.get("id"),
                    "name": job.get("name"),
                    "enabled": job.get("enabled"),
                    "state": job.get("state"),
                    "next_run_at": job.get("next_run_at"),
                    "script": job.get("script"),
                })
        return matches

    def append_stage_report(
        self,
        mission_id: str,
        *,
        summary: str,
        evidence: Iterable[str] = (),
        risks: Iterable[str] = (),
        next_step: str = "",
        needs_user: bool = False,
    ) -> dict[str, Any]:
        mission = self.get_mission(mission_id)
        if not mission:
            raise KeyError(mission_id)
        summary = summary.strip()
        if not summary:
            raise ValueError("stage report summary is required")
        payload = {
            "evidence": [str(x).strip() for x in evidence if str(x).strip()],
            "risks": [str(x).strip() for x in risks if str(x).strip()],
            "next_step": next_step.strip(),
            "needs_user": bool(needs_user),
        }
        event = self.append_event(mission_id, "stage_report", summary, payload)
        return event

    def tick(self, mission_id: str, *, dry_run: bool = True) -> dict[str, Any]:
        mission = self.get_mission(mission_id)
        if not mission:
            raise KeyError(mission_id)
        open_decisions = self.open_decisions(mission_id)
        if open_decisions:
            return {
                "status": "blocked_on_human",
                "mission": mission_to_dict(mission),
                "open_decisions": [decision_to_dict(d) for d in open_decisions],
                "prompt": "",
            }
        if mission.status != "active":
            return {"status": "noop", "reason": f"mission status is {mission.status}", "mission": mission_to_dict(mission), "prompt": ""}
        used_ticks = self.count_events(mission_id, {"tick_prompt_created"})
        if mission.budget.max_ticks is not None and used_ticks >= mission.budget.max_ticks:
            self.set_status(mission_id, "paused", reason="max tick budget exhausted")
            self.append_event(mission_id, "budget_exhausted", "max tick budget exhausted", {"max_ticks": mission.budget.max_ticks})
            return {"status": "budget_exhausted", "mission": mission_to_dict(self.get_mission(mission_id)), "prompt": ""}
        pending_signals = self.pending_flow_signals(mission_id, limit=1)
        signal = pending_signals[0] if pending_signals else None
        prompt = build_tick_prompt(
            mission,
            self.list_events(mission_id, limit=12),
            self.answered_decisions(mission_id, limit=5),
            trigger_signal=signal,
        )
        event_type = "tick_dry_run" if dry_run else "tick_prompt_created"
        mode = "event" if signal else "maintenance"
        payload: dict[str, Any] = {"dry_run": dry_run, "used_ticks_before": used_ticks, "mode": mode}
        if signal:
            payload["signal"] = flow_signal_to_dict(signal)
        self.append_event(mission_id, event_type, "mission tick prompt generated", payload, enqueue_flow=False)
        if not dry_run:
            if signal:
                self.consume_flow_signal(signal.signal_id)
            else:
                self.append_event(
                    mission_id,
                    "maintenance_tick",
                    "periodic maintenance tick generated without pending event signal",
                    {"used_ticks_before": used_ticks},
                    enqueue_flow=False,
                )
        return {
            "status": "dry_run" if dry_run else "prompt_created",
            "mission": mission_to_dict(mission),
            "prompt": prompt,
            "mode": mode,
            "signal": flow_signal_to_dict(signal) if signal else None,
        }

    def count_events(self, mission_id: str, event_types: set[str]) -> int:
        placeholders = ",".join("?" for _ in event_types)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS n FROM mission_events WHERE mission_id = ? AND event_type IN ({placeholders})",
                (mission_id, *sorted(event_types)),
            ).fetchone()
        return int(row["n"] if row else 0)

    def answered_decisions(self, mission_id: str, limit: int = 5) -> list[DecisionRequest]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM mission_decisions WHERE mission_id = ? AND status = 'answered' ORDER BY answered_at DESC LIMIT ?",
                (mission_id, int(limit)),
            ).fetchall()
        return [self._row_to_decision(row) for row in rows]

    def install_cron(
        self,
        mission_id: str,
        *,
        schedule: str | None = None,
        deliver: str | None = None,
        name: str | None = None,
        profile: str | None = None,
        enabled_toolsets: list[str] | None = None,
        scripts_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        """Install a recurring agent cron job that advances this mission.

        The cron job is agent-based: a per-mission script emits the current tick
        prompt at runtime, and the scheduler injects that prompt into the normal
        Hermes agent.  The script only creates ledger events and never performs
        external side effects itself.
        """

        mission = self.get_mission(mission_id)
        if not mission:
            raise KeyError(mission_id)
        if profile:
            raise ValueError("mission install-cron --profile is not supported yet; install from the target profile session so the script and job share one HERMES_HOME")
        script_path = install_mission_tick_script(mission_id, scripts_dir=scripts_dir)
        script_name = script_path.name
        try:
            from cron.jobs import create_job
        except Exception as exc:  # pragma: no cover - import failure is environment-specific
            raise RuntimeError(f"cron job support unavailable: {exc}") from exc
        prompt = (
            "Advance the Hermes Mission using the mission tick prompt provided by the pre-run script. "
            "Respect all mission blocked actions, evidence requirements, budget boundaries, and human-intervention rules. "
            "If the script reports blocked_on_human or budget_exhausted, do not continue work; summarize the blocker and required user decision."
        )
        job = create_job(
            prompt=prompt,
            schedule=schedule or mission.cadence,
            name=name or f"Mission {mission_id}",
            deliver=deliver or mission.deliver,
            script=script_name,
            no_agent=False,
            enabled_toolsets=enabled_toolsets,
            profile=profile,
        )
        self.append_event(
            mission_id,
            "mission_cron_installed",
            f"cron job installed: {job.get('id')}",
            {"job_id": job.get("id"), "script": script_name, "schedule": schedule or mission.cadence, "deliver": deliver or mission.deliver},
            enqueue_flow=False,
        )
        dispatch = self.dispatch_pending_flow_signals(mission_id)
        return {"job": job, "script": str(script_path), "event_dispatch": dispatch}

    def report(self, mission_id: str, limit: int = 20) -> str:
        mission = self.get_mission(mission_id)
        if not mission:
            raise KeyError(mission_id)
        events = self.list_events(mission_id, limit=limit)
        decisions = self.open_decisions(mission_id)
        lines = [
            f"Mission: {mission.mission_id}",
            f"Status: {mission.status}",
            f"Stage: {mission.stage}",
            f"Cadence: {mission.cadence}",
            f"Deliver: {mission.deliver}",
            "",
            "Direction:",
            mission.direction,
            "",
            "Budget:",
            json.dumps(asdict(mission.budget), ensure_ascii=False, sort_keys=True),
            "",
            "Recent events:",
        ]
        if events:
            for event in events:
                lines.extend(render_event_block(event))
        else:
            lines.append("- none")
        if decisions:
            lines.extend(["", "Open decisions:"])
            for d in decisions:
                lines.extend(render_decision_block(d))
        return "\n".join(lines)

    def _event_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "event_id": row["event_id"],
            "mission_id": row["mission_id"],
            "event_type": row["event_type"],
            "summary": row["summary"],
            "payload": _json_loads(row["payload_json"], {}),
            "created_at": row["created_at"],
        }

    def _row_to_mission(self, row: sqlite3.Row) -> Mission:
        budget_raw = _json_loads(row["budget_json"], {})
        budget = MissionBudget(
            max_ticks=budget_raw.get("max_ticks"),
            max_turns_per_tick=budget_raw.get("max_turns_per_tick"),
            max_total_usd=budget_raw.get("max_total_usd"),
        )
        return Mission(
            mission_id=row["mission_id"],
            direction=row["direction"],
            status=row["status"],
            cadence=row["cadence"],
            deliver=row["deliver"],
            budget=budget,
            stage=row["stage"],
            constraints=_json_loads(row["constraints_json"], []),
            non_goals=_json_loads(row["non_goals_json"], []),
            success_criteria=_json_loads(row["success_criteria_json"], []),
            allowed_actions=_json_loads(row["allowed_actions_json"], []),
            blocked_actions=_json_loads(row["blocked_actions_json"], []),
            plan_ref=row["plan_ref"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_decision(self, row: sqlite3.Row) -> DecisionRequest:
        return DecisionRequest(
            decision_id=row["decision_id"],
            mission_id=row["mission_id"],
            question=row["question"],
            options=_json_loads(row["options_json"], []),
            recommended_option=row["recommended_option"] or "",
            status=row["status"],
            answer=row["answer"] or "",
            created_at=row["created_at"],
            answered_at=row["answered_at"],
        )

    def _row_to_flow_signal(self, row: sqlite3.Row) -> MissionFlowSignal:
        return MissionFlowSignal(
            signal_id=row["signal_id"],
            mission_id=row["mission_id"],
            source_event_id=row["source_event_id"],
            signal_type=row["signal_type"],
            status=row["status"],
            payload=_json_loads(row["payload_json"], {}),
            created_at=row["created_at"],
            consumed_at=row["consumed_at"],
        )


def default_blocked_actions() -> list[str]:
    return [
        "external_message_send",
        "remote_push_or_merge",
        "production_deploy",
        "payment_or_purchase",
        "secret_or_credential_write",
        "destructive_delete",
        "memory_skill_cron_config_write_without_explicit_scope",
        "create_or_modify_mission_budget_or_cadence",
    ]


def mission_to_dict(mission: Mission | None) -> dict[str, Any] | None:
    if mission is None:
        return None
    data = asdict(mission)
    data["budget"] = asdict(mission.budget)
    return data


def decision_to_dict(decision: DecisionRequest) -> dict[str, Any]:
    return asdict(decision)


def flow_signal_to_dict(signal: MissionFlowSignal | None) -> dict[str, Any] | None:
    return asdict(signal) if signal is not None else None


def _quote_decision_answer(text: str) -> str:
    safe = text.replace('"', '\\"').strip()
    return f'"{safe}"' if safe else '"<answer>"'


def render_decision_next_command(decision: DecisionRequest) -> str:
    suggested = decision.recommended_option or (decision.options[0] if decision.options else "<answer>")
    return f"hermes mission decide {decision.decision_id} {_quote_decision_answer(suggested)}"


def render_decision_block(decision: DecisionRequest) -> list[str]:
    lines = [f"- {decision.decision_id}: {decision.question}"]
    if decision.options:
        lines.append("  Options:")
        for index, option in enumerate(decision.options, start=1):
            lines.append(f"    {index}. {option}")
    if decision.recommended_option:
        lines.append(f"  Recommended: {decision.recommended_option}")
    lines.append("  Decide:")
    lines.append(f"    {render_decision_next_command(decision)}")
    return lines


def render_event_block(event: dict[str, Any]) -> list[str]:
    """Render a ledger event with evidence-bearing stage report details."""

    lines = [f"- {event['event_type']}: {event['summary']}"]
    if event.get("event_type") != "stage_report":
        return lines
    payload = event.get("payload") or {}
    evidence = payload.get("evidence") or []
    if evidence:
        lines.append("  Evidence:")
        for item in evidence:
            lines.append(f"    - {item}")
    risks = payload.get("risks") or []
    if risks:
        lines.append("  Risks:")
        for item in risks:
            lines.append(f"    - {item}")
    next_step = str(payload.get("next_step") or "").strip()
    if next_step:
        lines.append(f"  Next step: {next_step}")
    if payload.get("needs_user"):
        lines.append("  Needs user: yes")
    return lines


def _truncate(text: str, limit: int) -> str:
    clean = " ".join(str(text).split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)] + "…"


def build_tick_prompt(
    mission: Mission,
    recent_events: list[dict[str, Any]],
    answered_decisions: list[DecisionRequest],
    *,
    trigger_signal: MissionFlowSignal | None = None,
) -> str:
    """Build the bounded user-role prompt for one mission advancement tick."""

    criteria = "\n".join(f"- {x}" for x in mission.success_criteria) or "- produce evidence-backed stage progress"
    constraints = "\n".join(f"- {x}" for x in mission.constraints) or "- preserve existing behavior; stop at permission/risk/evidence boundaries"
    non_goals = "\n".join(f"- {x}" for x in mission.non_goals) or "- do not perform external side effects without explicit authorization"
    blocked = "\n".join(f"- {x}" for x in mission.blocked_actions)
    decisions = "\n".join(f"- {d.question} => {d.answer}" for d in answered_decisions) or "- none"
    events = "\n".join(f"- {e['event_type']}: {e['summary']}" for e in recent_events) or "- none"
    if trigger_signal:
        trigger = (
            f"event-driven ({trigger_signal.signal_type}) from {trigger_signal.source_event_id}: "
            f"{trigger_signal.payload.get('summary', '')}"
        )
    else:
        trigger = "periodic maintenance watchdog: no pending event signal; inspect health, drift, and next safe bounded step"
    max_turns = mission.budget.max_turns_per_tick or 8
    total_budget = mission.budget.max_total_usd if mission.budget.max_total_usd is not None else "not-set"
    return f"""[Hermes Mission Tick]
Mission ID: {mission.mission_id}
Stage: {mission.stage}
Direction:
{mission.direction}

Success criteria:
{criteria}

Constraints:
{constraints}

Non-goals:
{non_goals}

Blocked actions unless an explicit human decision authorizes a narrower digest:
{blocked}

Recent human decisions:
{decisions}

Recent mission evidence/events:
{events}

Trigger mode:
{trigger}

Budget boundary:
- enforceable remaining tick budget is tracked by the mission ledger
- total USD marker: {total_budget}

Advance this mission by exactly one bounded stage slice, within {max_turns} tool-calling turns if tools are needed.
Rules:
1. Prefer read-only discovery, local drafts, tests, and evidence collection.
2. If the next step requires a value judgment, permission escalation, external side effect, destructive action, or evidence you cannot obtain, stop and output a Human Intervention Packet with 2-4 options and a recommendation.
3. Do not claim completion without real evidence. Distinguish completed, verified, drafted, blocked, and assumed.
4. Before ending a successful or partially successful tick, write a durable mission stage report with:
   hermes mission stage-report {mission.mission_id} --summary "<what changed>" --evidence "<real proof path/command/result>" --risk "<risk if any>" --next-step "<next bounded step>"
5. If user input is needed, create the durable decision gate instead of only describing it:
   hermes mission handoff {mission.mission_id} --question "<decision needed>" --option "<option A>" --option "<option B>" --recommend "<recommended option>"
6. End with a concise stage report: progress, evidence, risks, next step, and whether user input is needed.
""".strip()


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _print_inbox(items: list[dict[str, Any]]) -> None:
    if not items:
        print("No open mission decisions.")
        return
    print("Open Mission Decisions")
    print("")
    for index, item in enumerate(items, start=1):
        decision = item["decision"]
        print(f"{index}. {item['mission_id']}  {item['mission_status']}  stage={item['stage']}")
        print(f"   Direction: {_truncate(item['direction'], 96)}")
        print(f"   Decision: {decision['question']}")
        if decision.get("options"):
            print("   Options:")
            for opt_index, option in enumerate(decision["options"], start=1):
                print(f"     {opt_index}. {option}")
        if decision.get("recommended_option"):
            print(f"   Recommended: {decision['recommended_option']}")
        print("   Decide:")
        print(f"     {item['decide_command']}")
        print("")


def _print_board(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No missions found.")
        return
    print("Mission Board")
    print("")
    print(f"{'ID':<28} {'Status':<17} {'Ticks':<9} {'Cron':<5} {'User':<5} Next")
    for row in rows:
        ticks = f"{row['ticks_used']}/{row['ticks_max']}"
        cron = "yes" if row["cron_installed"] else "no"
        user = "yes" if row["needs_user"] else "no"
        print(f"{row['mission_id']:<28} {row['status']:<17} {ticks:<9} {cron:<5} {user:<5} {row['next_step']}")
        print(f"  {_truncate(row['direction'], 110)}")


def _print_doctor(result: dict[str, Any]) -> None:
    mission = result["mission"]
    print(f"Mission Doctor: {mission['mission_id']}")
    print("")
    for check in result["checks"]:
        marker = "ok" if check["status"] == "ok" else "warn"
        print(f"[{marker}] {check['name']}: {check['detail']}")
    if result.get("cron_jobs"):
        print("")
        print("Cron jobs:")
        for job in result["cron_jobs"]:
            print(f"- {job.get('id')} {job.get('state')} next={job.get('next_run_at')} script={job.get('script')}")
    print("")
    print("Next step:")
    print(f"  {result['next_step']}")


def cmd_mission(args: argparse.Namespace) -> int:
    store = MissionStore(getattr(args, "db", None))
    command = getattr(args, "mission_command", None)
    try:
        if command == "create":
            budget = MissionBudget(
                max_ticks=getattr(args, "max_ticks", None),
                max_turns_per_tick=getattr(args, "max_turns_per_tick", None),
                max_total_usd=getattr(args, "max_total_usd", None),
            )
            mission = store.create_mission(
                direction=args.direction,
                cadence=args.cadence,
                deliver=args.deliver,
                budget=budget,
                constraints=args.constraint or [],
                non_goals=args.non_goal or [],
                success_criteria=args.success or [],
                allowed_actions=args.allow_action or [],
                blocked_actions=args.block_action or [],
                enable=bool(args.enable),
            )
            if args.json:
                _print_json(mission_to_dict(mission))
            else:
                print(f"Created mission {mission.mission_id} ({mission.status})")
                if mission.status == "paused":
                    print("Default-off: run `hermes mission resume <id>` before scheduled ticks can advance it.")
            return 0
        if command in {"list", "ls"}:
            missions = [mission_to_dict(m) for m in store.list_missions(include_done=bool(args.all))]
            if args.json:
                _print_json(missions)
            else:
                if not missions:
                    print("No missions found.")
                    return 0
                for m in missions:
                    if not m:
                        continue
                    print(f"{m['mission_id']}\t{m['status']}\t{m['stage']}\t{m['cadence']}\t{m['direction'][:80]}")
            return 0
        if command == "inbox":
            items = store.inbox(include_done=bool(args.all))
            if args.json:
                _print_json(items)
            else:
                _print_inbox(items)
            return 0
        if command == "board":
            rows = store.board(include_done=bool(args.all))
            if args.json:
                _print_json(rows)
            else:
                _print_board(rows)
            return 0
        if command == "doctor":
            result = store.doctor(args.mission_id)
            if args.json:
                _print_json(result)
            else:
                _print_doctor(result)
            return 0
        if command == "signals":
            signals = [flow_signal_to_dict(s) for s in store.pending_flow_signals(args.mission_id)]
            if args.json:
                _print_json(signals)
            else:
                if not signals:
                    print("No pending mission flow signals.")
                else:
                    print("Pending Mission Flow Signals")
                    for signal in signals:
                        if not signal:
                            continue
                        print(f"- {signal['signal_id']} {signal['signal_type']} from {signal['source_event_id']} status={signal['status']}")
            return 0
        if command == "dispatch-events":
            result = store.dispatch_pending_flow_signals(args.mission_id)
            if args.json:
                _print_json(result)
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if command == "status":
            mission = store.get_mission(args.mission_id)
            if not mission:
                raise KeyError(args.mission_id)
            payload = mission_to_dict(mission) or {}
            payload["open_decisions"] = [decision_to_dict(d) for d in store.open_decisions(args.mission_id)]
            payload["pending_flow_signals"] = [flow_signal_to_dict(s) for s in store.pending_flow_signals(args.mission_id)]
            payload["recent_events"] = store.list_events(args.mission_id, limit=args.limit)
            if args.json:
                _print_json(payload)
            else:
                print(store.report(args.mission_id, limit=args.limit))
            return 0
        if command == "pause":
            mission = store.set_status(args.mission_id, "paused", reason=args.reason or "manual pause")
            print(f"Mission {mission.mission_id} paused")
            return 0
        if command == "resume":
            if store.open_decisions(args.mission_id):
                print("Cannot resume: mission has open decisions. Use `hermes mission decide <decision-id> <answer>`.")
                return 2
            mission = store.set_status(args.mission_id, "active", reason=args.reason or "manual resume")
            print(f"Mission {mission.mission_id} active")
            return 0
        if command == "cancel":
            mission = store.set_status(args.mission_id, "cancelled", reason=args.reason or "manual cancel")
            print(f"Mission {mission.mission_id} cancelled")
            return 0
        if command == "ask":
            decision = store.request_decision(args.mission_id, args.question, args.option or [], args.recommend or "")
            if args.json:
                _print_json(decision_to_dict(decision))
            else:
                print(f"Decision requested: {decision.decision_id}")
                for line in render_decision_block(decision):
                    print(line)
            return 0
        if command == "handoff":
            decision = store.request_decision(args.mission_id, args.question, args.option or [], args.recommend or "")
            if args.json:
                _print_json(decision_to_dict(decision))
            else:
                print(f"Human intervention required: {decision.decision_id}")
                print(f"Mission {decision.mission_id} is blocked_on_human until answered.")
                print("")
                for line in render_decision_block(decision):
                    print(line)
            return 0
        if command == "stage-report":
            event = store.append_stage_report(
                args.mission_id,
                summary=args.summary,
                evidence=args.evidence or [],
                risks=args.risk or [],
                next_step=args.next_step or "",
                needs_user=bool(args.needs_user),
            )
            if args.json:
                _print_json(event)
            else:
                print(f"Stage report recorded: {event['event_id']}")
            return 0
        if command == "decide":
            decision = store.answer_decision(args.decision_id, " ".join(args.answer))
            if args.json:
                _print_json(decision_to_dict(decision))
            else:
                print(f"Decision recorded for {decision.decision_id}; mission resumed")
            return 0
        if command == "tick":
            result = store.tick(args.mission_id, dry_run=not bool(args.create_prompt_event))
            if args.json:
                _print_json(result)
            else:
                print(result.get("prompt") or json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if command == "install-cron":
            result = store.install_cron(
                args.mission_id,
                schedule=args.schedule,
                deliver=args.deliver,
                name=args.name,
                profile=args.profile,
                enabled_toolsets=args.toolset,
            )
            if args.json:
                _print_json(result)
            else:
                job = result["job"]
                print(f"Installed mission cron job {job['id']}")
                print(f"Script: {result['script']}")
                print(f"Schedule: {job.get('schedule_display')}")
                print(f"Deliver: {job.get('deliver')}")
            return 0
        if command == "report":
            report = store.report(args.mission_id, limit=args.limit)
            print(report)
            return 0
    except (KeyError, ValueError) as exc:
        print(f"Error: {exc}")
        return 2
    print("Run `hermes mission --help` for usage.")
    return 2
