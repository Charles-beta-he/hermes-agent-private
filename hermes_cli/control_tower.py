"""Hermes Control Tower read-only ledger and snapshot helpers.

The Control Tower Ledger is the single canonical workflow fact source for the
Control Tower architecture. Existing Kanban/taskpool/brain surfaces remain
adapters/projections during migration; this module deliberately starts with a
read-only snapshot and adapter-observation contract rather than lifecycle writes.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home

SCHEMA_VERSION = 1
LEDGER_FILENAME = "control_tower.db"

CONTROL_TOWER_COMMAND_LIFECYCLE_SCHEMA: dict[str, Any] = {
    "schema_id": "control-tower-command-lifecycle",
    "version": 1,
    "nodes": [
        {
            "id": "command_proposed",
            "label": "Command proposed",
            "requires": [],
            "truth_source": "events.CommandProposed",
        },
        {
            "id": "approval_requested",
            "label": "Human approval requested",
            "requires": ["command_proposed"],
            "truth_source": "human_approvals.status=requested",
        },
        {
            "id": "approval_decided",
            "label": "Human approval decided",
            "requires": ["approval_requested"],
            "truth_source": "human_approvals.status in approved/rejected",
        },
        {
            "id": "executor_eligible",
            "label": "Executor eligible",
            "requires": ["approval_decided"],
            "truth_source": "future_policy_executor_disabled",
            "enabled": False,
        },
    ],
}

_TABLES_SQL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS ledger_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS commands (
        command_id TEXT PRIMARY KEY,
        command_type TEXT NOT NULL,
        actor_type TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        source_surface TEXT NOT NULL,
        target_type TEXT,
        target_id TEXT,
        idempotency_key TEXT UNIQUE,
        risk_level TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        policy_decision_id TEXT,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        event_id TEXT PRIMARY KEY,
        sequence INTEGER NOT NULL UNIQUE,
        event_type TEXT NOT NULL,
        aggregate_type TEXT NOT NULL,
        aggregate_id TEXT NOT NULL,
        command_id TEXT,
        actor_type TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        source_surface TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        causation_id TEXT,
        payload_json TEXT NOT NULL,
        schema_version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        task_id TEXT PRIMARY KEY,
        external_ref TEXT,
        title TEXT NOT NULL,
        project TEXT,
        board TEXT,
        priority TEXT,
        status TEXT NOT NULL,
        risk_class TEXT NOT NULL DEFAULT 'unknown',
        assignee TEXT,
        workflow_template_id TEXT,
        current_step_key TEXT,
        acceptance_json TEXT NOT NULL DEFAULT '[]',
        red_lines_json TEXT NOT NULL DEFAULT '[]',
        dependencies_json TEXT NOT NULL DEFAULT '[]',
        evidence_refs_json TEXT NOT NULL DEFAULT '[]',
        blocked_reason TEXT,
        next_action TEXT,
        version INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_runs (
        workflow_run_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        workflow_template_id TEXT NOT NULL,
        status TEXT NOT NULL,
        current_step_key TEXT,
        started_at TEXT,
        ended_at TEXT,
        last_event_sequence INTEGER,
        context_json TEXT NOT NULL DEFAULT '{}',
        version INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflow_steps (
        step_run_id TEXT PRIMARY KEY,
        workflow_run_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        step_key TEXT NOT NULL,
        step_type TEXT NOT NULL,
        status TEXT NOT NULL,
        gate_type TEXT,
        gate_result TEXT,
        blocked_reason TEXT,
        started_at TEXT,
        ended_at TEXT,
        evidence_refs_json TEXT NOT NULL DEFAULT '[]',
        event_refs_json TEXT NOT NULL DEFAULT '[]',
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS worker_runs (
        worker_run_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        workflow_run_id TEXT,
        step_run_id TEXT,
        worker_type TEXT NOT NULL,
        worker_id TEXT NOT NULL,
        model_provider TEXT,
        model_name TEXT,
        workspace TEXT,
        status TEXT NOT NULL,
        lease_owner TEXT,
        lease_expires_at TEXT,
        heartbeat_at TEXT,
        result_ref TEXT,
        evidence_refs_json TEXT NOT NULL DEFAULT '[]',
        exit_reason TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence (
        evidence_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        workflow_run_id TEXT,
        step_run_id TEXT,
        evidence_type TEXT NOT NULL,
        maturity TEXT NOT NULL,
        source_type TEXT NOT NULL,
        artifact_uri TEXT,
        artifact_hash TEXT,
        summary TEXT NOT NULL,
        producer_type TEXT NOT NULL,
        producer_id TEXT NOT NULL,
        verifier TEXT,
        acceptance_refs_json TEXT NOT NULL DEFAULT '[]',
        stale_status TEXT NOT NULL DEFAULT 'fresh',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS policy_decisions (
        policy_decision_id TEXT PRIMARY KEY,
        command_id TEXT,
        actor_type TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        action TEXT NOT NULL,
        risk_level TEXT NOT NULL,
        decision TEXT NOT NULL,
        policy_source TEXT NOT NULL,
        reason TEXT NOT NULL,
        required_evidence_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS human_approvals (
        approval_id TEXT PRIMARY KEY,
        task_id TEXT,
        workflow_run_id TEXT,
        command_id TEXT,
        requested_action TEXT NOT NULL,
        risk_level TEXT NOT NULL,
        status TEXT NOT NULL,
        request_summary TEXT NOT NULL,
        evidence_summary_json TEXT NOT NULL DEFAULT '[]',
        consequence_summary TEXT NOT NULL,
        escape_path TEXT,
        requested_by TEXT NOT NULL,
        decided_by TEXT,
        requested_at TEXT NOT NULL,
        decided_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS adapter_observations (
        observation_id TEXT PRIMARY KEY,
        adapter_name TEXT NOT NULL,
        external_ref TEXT NOT NULL,
        observed_type TEXT NOT NULL,
        observed_hash TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        reconciled_event_id TEXT,
        conflict_event_id TEXT,
        observed_at TEXT NOT NULL,
        UNIQUE(adapter_name, external_ref, observed_type, observed_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS projection_checkpoints (
        projection_name TEXT PRIMARY KEY,
        last_sequence INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL,
        error TEXT,
        updated_at TEXT NOT NULL
    )
    """,
)

_INDEXES_SQL: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_events_aggregate ON events(aggregate_type, aggregate_id, sequence)",
    "CREATE INDEX IF NOT EXISTS idx_events_correlation ON events(correlation_id, sequence)",
    "CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(event_type, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_commands_target ON commands(target_type, target_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_commands_actor ON commands(actor_type, actor_id, created_at)",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_workflow_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Validate a small workflow schema graph and return it unchanged.

    This mirrors the useful OpenSpec artifact-graph guardrails while keeping the
    schema as a Control Tower projection rule, not an external workflow truth.
    """
    nodes = schema.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("workflow schema nodes must be a list")
    node_ids: list[str] = []
    for node in nodes:
        node_id = str(node.get("id", "")).strip() if isinstance(node, dict) else ""
        if not node_id:
            raise ValueError("workflow node id is required")
        if node_id in node_ids:
            raise ValueError(f"duplicate workflow node id: {node_id}")
        node_ids.append(node_id)
    known = set(node_ids)
    dependencies: dict[str, list[str]] = {}
    for node in nodes:
        node_id = str(node["id"])
        requires = node.get("requires", [])
        if not isinstance(requires, list):
            raise ValueError(f"workflow node requires must be a list: {node_id}")
        dependencies[node_id] = [str(dep) for dep in requires]
        for dep in dependencies[node_id]:
            if dep not in known:
                raise ValueError(f"missing dependency for {node_id}: {dep}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str, trail: list[str]) -> None:
        if node_id in visiting:
            cycle = " -> ".join([*trail, node_id])
            raise ValueError(f"workflow cycle detected: {cycle}")
        if node_id in visited:
            return
        visiting.add(node_id)
        for dep in dependencies[node_id]:
            visit(dep, [*trail, node_id])
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in node_ids:
        visit(node_id, [])
    return schema


def _active_profile(home: Path) -> str:
    try:
        active = (home / "active_profile").read_text(encoding="utf-8").strip()
    except OSError:
        active = ""
    return active or "default"


def ledger_path() -> Path:
    """Return the canonical Control Tower Ledger path for the active Hermes home."""
    return get_hermes_home() / LEDGER_FILENAME


def _connect() -> sqlite3.Connection:
    path = init_ledger()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_ledger(path: Path | None = None) -> Path:
    """Create the Control Tower Ledger schema if needed and return its path."""
    ledger = path or ledger_path()
    ledger.parent.mkdir(parents=True, exist_ok=True)
    now = _now()
    with sqlite3.connect(ledger) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        for sql in _TABLES_SQL:
            conn.execute(sql)
        for sql in _INDEXES_SQL:
            conn.execute(sql)
        meta = {
            "schema_version": str(SCHEMA_VERSION),
            "created_at": now,
            "active_profile": _active_profile(ledger.parent),
            "ledger_id": str(uuid.uuid4()),
        }
        for key, value in meta.items():
            conn.execute(
                """
                INSERT INTO ledger_meta(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (key, value, now),
            )
        conn.commit()
    return ledger


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _payload_hash(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _next_sequence(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(sequence), 0) + 1 FROM events").fetchone()
    return int(row[0])


def _append_event(
    conn: sqlite3.Connection,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
    actor_type: str = "system",
    actor_id: str = "control_tower",
    source_surface: str = "control_tower",
    command_id: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> str:
    event_id = f"evt_{uuid.uuid4().hex}"
    conn.execute(
        """
        INSERT INTO events(
            event_id, sequence, event_type, aggregate_type, aggregate_id,
            command_id, actor_type, actor_id, source_surface,
            correlation_id, causation_id, payload_json, schema_version, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            _next_sequence(conn),
            event_type,
            aggregate_type,
            aggregate_id,
            command_id,
            actor_type,
            actor_id,
            source_surface,
            correlation_id or event_id,
            causation_id,
            _canonical_json(payload),
            SCHEMA_VERSION,
            _now(),
        ),
    )
    return event_id


def _risk_requires_human(risk_level: str) -> bool:
    return risk_level.upper() not in {"L0", "L1"}


def _existing_command_by_idempotency(conn: sqlite3.Connection, idempotency_key: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT command_id, command_type, source_surface, target_type, target_id,
               risk_level, status, created_at
        FROM commands
        WHERE idempotency_key = ?
        """,
        (idempotency_key,),
    ).fetchone()


def propose_command(
    *,
    command_type: str,
    actor_type: str,
    actor_id: str,
    source_surface: str,
    target_type: str | None,
    target_id: str | None,
    risk_level: str,
    payload: dict[str, Any],
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Record a proposed command in the ledger without executing it.

    Phase 2 deliberately stops at proposed/audited commands. Even L0/L1 entries
    are *not* executed here; they are durable operator intent records for a later
    policy-gated executor. L2+ additionally create a human approval request.
    """
    command_type = command_type.strip()
    actor_type = actor_type.strip()
    actor_id = actor_id.strip()
    source_surface = source_surface.strip()
    risk_level = risk_level.strip().upper()
    if not command_type:
        raise ValueError("command_type is required")
    if not actor_type:
        raise ValueError("actor_type is required")
    if not actor_id:
        raise ValueError("actor_id is required")
    if not source_surface:
        raise ValueError("source_surface is required")
    if risk_level not in {"L0", "L1", "L2", "L3", "L4"}:
        raise ValueError("risk_level must be one of L0, L1, L2, L3, L4")

    now = _now()
    with _connect() as conn:
        if idempotency_key:
            existing = _existing_command_by_idempotency(conn, idempotency_key)
            if existing is not None:
                approval = conn.execute(
                    "SELECT approval_id FROM human_approvals WHERE command_id = ?",
                    (existing["command_id"],),
                ).fetchone()
                return {
                    "created": False,
                    "command_id": existing["command_id"],
                    "command_type": existing["command_type"],
                    "status": existing["status"],
                    "execution": "not_executed",
                    "requires_human": approval is not None,
                    "approval_id": approval["approval_id"] if approval else None,
                    "source_surface": existing["source_surface"],
                    "target_type": existing["target_type"],
                    "target_id": existing["target_id"],
                    "risk_level": existing["risk_level"],
                }

        command_id = f"cmd_{uuid.uuid4().hex}"
        conn.execute(
            """
            INSERT INTO commands(
                command_id, command_type, actor_type, actor_id, source_surface,
                target_type, target_id, idempotency_key, risk_level,
                payload_json, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                command_id,
                command_type,
                actor_type,
                actor_id,
                source_surface,
                target_type,
                target_id,
                idempotency_key,
                risk_level,
                _canonical_json(payload),
                "proposed",
                now,
            ),
        )
        event_id = _append_event(
            conn,
            event_type="CommandProposed",
            aggregate_type="command",
            aggregate_id=command_id,
            command_id=command_id,
            actor_type=actor_type,
            actor_id=actor_id,
            source_surface=source_surface,
            payload={
                "command_type": command_type,
                "target_type": target_type,
                "target_id": target_id,
                "risk_level": risk_level,
                "execution": "not_executed",
            },
        )
        approval_id = None
        if _risk_requires_human(risk_level):
            approval_id = f"appr_{uuid.uuid4().hex}"
            conn.execute(
                """
                INSERT INTO human_approvals(
                    approval_id, task_id, command_id, requested_action,
                    risk_level, status, request_summary, consequence_summary,
                    requested_by, requested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    target_id if target_type == "task" else None,
                    command_id,
                    command_type,
                    risk_level,
                    "requested",
                    f"Approve proposed {command_type}",
                    "No action has been executed; approval is required before dispatch.",
                    actor_id,
                    now,
                ),
            )
            _append_event(
                conn,
                event_type="HumanApprovalRequested",
                aggregate_type="approval",
                aggregate_id=approval_id,
                command_id=command_id,
                actor_type=actor_type,
                actor_id=actor_id,
                source_surface=source_surface,
                causation_id=event_id,
                payload={
                    "approval_id": approval_id,
                    "command_id": command_id,
                    "requested_action": command_type,
                    "risk_level": risk_level,
                    "status": "requested",
                },
            )
        conn.commit()

    return {
        "created": True,
        "command_id": command_id,
        "command_type": command_type,
        "status": "proposed",
        "execution": "not_executed",
        "requires_human": approval_id is not None,
        "approval_id": approval_id,
        "source_surface": source_surface,
        "target_type": target_type,
        "target_id": target_id,
        "risk_level": risk_level,
    }

def decide_human_approval(
    *,
    approval_id: str,
    decision: str,
    actor_type: str,
    actor_id: str,
    source_surface: str,
    reason: str = "",
) -> dict[str, Any]:
    """Approve or reject a human approval request without executing the command.

    Approval decisions are single-use and idempotent for repeated submissions of
    the same final decision. A conflicting second decision is rejected so the
    Human Inbox cannot silently flip authority after review.
    """
    approval_id = approval_id.strip()
    decision = decision.strip().lower()
    actor_type = actor_type.strip()
    actor_id = actor_id.strip()
    source_surface = source_surface.strip()
    if not approval_id:
        raise ValueError("approval_id is required")
    if decision not in {"approved", "rejected"}:
        raise ValueError("decision must be approved or rejected")
    if not actor_type:
        raise ValueError("actor_type is required")
    if not actor_id:
        raise ValueError("actor_id is required")
    if not source_surface:
        raise ValueError("source_surface is required")

    with _connect() as conn:
        row = conn.execute(
            """
            SELECT approval_id, command_id, requested_action, risk_level, status,
                   decided_by, decided_at
            FROM human_approvals
            WHERE approval_id = ?
            """,
            (approval_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"approval not found: {approval_id}")
        if row["status"] in {"approved", "rejected"}:
            if row["status"] != decision:
                raise ValueError(
                    f"approval already decided as {row['status']}; conflicting decision {decision} rejected"
                )
            return {
                "created": False,
                "approval_id": approval_id,
                "command_id": row["command_id"],
                "status": row["status"],
                "decision": row["status"],
                "execution": "not_executed",
                "source_surface": source_surface,
                "decided_by": row["decided_by"],
                "decided_at": row["decided_at"],
            }
        if row["status"] != "requested":
            raise ValueError(f"approval is not pending: {row['status']}")

        decided_at = _now()
        conn.execute(
            """
            UPDATE human_approvals
            SET status = ?, decided_by = ?, decided_at = ?
            WHERE approval_id = ?
            """,
            (decision, actor_id, decided_at, approval_id),
        )
        event_type = "HumanApprovalGranted" if decision == "approved" else "HumanApprovalRejected"
        event_id = _append_event(
            conn,
            event_type=event_type,
            aggregate_type="approval",
            aggregate_id=approval_id,
            command_id=row["command_id"],
            actor_type=actor_type,
            actor_id=actor_id,
            source_surface=source_surface,
            payload={
                "approval_id": approval_id,
                "command_id": row["command_id"],
                "requested_action": row["requested_action"],
                "risk_level": row["risk_level"],
                "decision": decision,
                "reason": reason,
                "execution": "not_executed",
            },
        )
        conn.commit()

    return {
        "created": True,
        "approval_id": approval_id,
        "command_id": row["command_id"],
        "status": decision,
        "decision": decision,
        "execution": "not_executed",
        "source_surface": source_surface,
        "decided_by": actor_id,
        "decided_at": decided_at,
        "event_id": event_id,
    }



def record_adapter_observation(
    *,
    adapter_name: str,
    external_ref: str,
    observed_type: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Record raw legacy/external state without making it canonical truth.

    Identical observations are deduped by a canonical JSON hash. A new
    observation emits one ``TaskObservedFromAdapter`` event for Phase 1 replay.
    """
    observed_hash = _payload_hash(payload)
    observation_id = f"obs_{uuid.uuid4().hex}"
    observed_at = _now()
    with _connect() as conn:
        try:
            conn.execute(
                """
                INSERT INTO adapter_observations(
                    observation_id, adapter_name, external_ref, observed_type,
                    observed_hash, payload_json, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_id,
                    adapter_name,
                    external_ref,
                    observed_type,
                    observed_hash,
                    _canonical_json(payload),
                    observed_at,
                ),
            )
        except sqlite3.IntegrityError:
            row = conn.execute(
                """
                SELECT observation_id FROM adapter_observations
                WHERE adapter_name = ? AND external_ref = ? AND observed_type = ? AND observed_hash = ?
                """,
                (adapter_name, external_ref, observed_type, observed_hash),
            ).fetchone()
            return {"created": False, "observation_id": row[0], "observed_hash": observed_hash}

        event_id = _append_event(
            conn,
            event_type="TaskObservedFromAdapter" if observed_type == "task" else "AdapterObservationRecorded",
            aggregate_type="adapter",
            aggregate_id=f"{adapter_name}:{external_ref}",
            payload={
                "adapter_name": adapter_name,
                "external_ref": external_ref,
                "observed_type": observed_type,
                "observed_hash": observed_hash,
                "observation_id": observation_id,
            },
        )
        conn.execute(
            "UPDATE adapter_observations SET reconciled_event_id = ? WHERE observation_id = ?",
            (event_id, observation_id),
        )
        conn.commit()
    return {
        "created": True,
        "observation_id": observation_id,
        "observed_hash": observed_hash,
        "event_id": event_id,
    }


def _count(conn: sqlite3.Connection, table: str, where: str = "1=1") -> int:
    # Internal helper with fixed table names only; callers pass literals from this module.
    row = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()
    return int(row[0])


def _kanban_task_payload(task: Any) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "body": task.body,
        "assignee": task.assignee,
        "status": task.status,
        "priority": task.priority,
        "tenant": task.tenant,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "completed_at": task.completed_at,
        "claim_lock": task.claim_lock,
        "claim_expires": task.claim_expires,
        "last_heartbeat_at": task.last_heartbeat_at,
        "current_run_id": task.current_run_id,
        "workflow_template_id": task.workflow_template_id,
        "current_step_key": task.current_step_key,
        "session_id": task.session_id,
    }


def observe_kanban_adapter() -> dict[str, Any]:
    """Observe existing Kanban state as adapter input to the ledger.

    This does not mutate Kanban/task lifecycle state. It only records canonical
    ledger observations for replay/projection and gracefully reports unavailable
    Kanban installs instead of creating a second truth source.
    """
    try:
        from hermes_cli import kanban_db
    except Exception as exc:  # pragma: no cover - defensive optional adapter
        return {"available": False, "error": f"import failed: {exc}", "observed_tasks": 0}

    db_path = kanban_db.kanban_db_path()
    if not db_path.exists():
        return {"available": False, "path": str(db_path), "observed_tasks": 0}

    try:
        conn = kanban_db.connect()
        try:
            tasks = kanban_db.list_tasks(conn, include_archived=False)
        finally:
            conn.close()
    except Exception as exc:
        return {"available": False, "path": str(db_path), "error": str(exc), "observed_tasks": 0}

    created = 0
    for task in tasks:
        result = record_adapter_observation(
            adapter_name="kanban",
            external_ref=f"task:{task.id}",
            observed_type="task",
            payload=_kanban_task_payload(task),
        )
        if result.get("created"):
            created += 1
    return {
        "available": True,
        "path": str(db_path),
        "observed_tasks": len(tasks),
        "new_observations": created,
    }


def _taskpool_health_from_tasks(tasks: list[dict[str, Any]]) -> dict[str, int]:
    active_statuses = {"triage", "todo", "scheduled", "ready", "running", "blocked", "review"}
    return {
        "active": sum(1 for task in tasks if task.get("status") in active_statuses),
        "ready": sum(1 for task in tasks if task.get("status") == "ready"),
        "blocked": sum(1 for task in tasks if task.get("status") == "blocked"),
        "claimed": sum(1 for task in tasks if task.get("claim_lock")),
        "running": sum(1 for task in tasks if task.get("status") == "running"),
        "review": sum(1 for task in tasks if task.get("status") == "review"),
        "stale_leases": 0,
    }


def _observed_tasks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT payload_json FROM adapter_observations
        WHERE adapter_name = 'kanban' AND observed_type = 'task' AND conflict_event_id IS NULL
        ORDER BY observed_at DESC
        """
    ).fetchall()
    latest_by_ref: dict[str, dict[str, Any]] = {}
    for row in rows:
        payload = json.loads(row[0])
        latest_by_ref.setdefault(str(payload.get("id")), payload)
    return list(latest_by_ref.values())


def _next_dispatchable_from_tasks(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    ready = [task for task in tasks if task.get("status") == "ready"]
    if not ready:
        return None
    ready.sort(key=lambda task: (-int(task.get("priority") or 0), int(task.get("created_at") or 0)))
    task = ready[0]
    return {
        "adapter": "kanban",
        "external_ref": f"task:{task.get('id')}",
        "title": task.get("title"),
        "priority": task.get("priority"),
        "assignee": task.get("assignee"),
        "gate": "read_only_observed_ready",
    }


def _command_registry_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT command_id, command_type, source_surface, target_type, target_id,
               risk_level, status, created_at
        FROM commands
        WHERE status = 'proposed'
        ORDER BY rowid ASC
        LIMIT 20
        """
    ).fetchall()
    return {
        "mode": "proposed_only_no_execution",
        "proposed_count": _count(conn, "commands", "status = 'proposed'"),
        "recent_proposed": [dict(row) for row in rows],
    }


def _human_inbox_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    pending_rows = conn.execute(
        """
        SELECT approval_id, command_id, task_id, requested_action, risk_level,
               status, request_summary, consequence_summary, requested_by, requested_at
        FROM human_approvals
        WHERE status = 'requested'
        ORDER BY rowid ASC
        LIMIT 20
        """
    ).fetchall()
    decided_rows = conn.execute(
        """
        SELECT approval_id, command_id, task_id, requested_action, risk_level,
               status, request_summary, consequence_summary, requested_by,
               requested_at, decided_by, decided_at
        FROM human_approvals
        WHERE status IN ('approved', 'rejected')
        ORDER BY decided_at DESC, rowid DESC
        LIMIT 20
        """
    ).fetchall()
    return {
        "pending_count": _count(conn, "human_approvals", "status = 'requested'"),
        "pending": [dict(row) for row in pending_rows],
        "decided_count": _count(conn, "human_approvals", "status IN ('approved', 'rejected')"),
        "recent_decided": [dict(row) for row in decided_rows],
    }


def _workflow_map_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    schema = validate_workflow_schema(CONTROL_TOWER_COMMAND_LIFECYCLE_SCHEMA)
    pending = conn.execute(
        """
        SELECT approval_id, command_id, requested_action, risk_level
        FROM human_approvals
        WHERE status = 'requested'
        ORDER BY rowid ASC
        """
    ).fetchall()
    approved_count = _count(conn, "human_approvals", "status = 'approved'")
    ready = [
        {
            "node_id": "approval_requested",
            "approval_id": row["approval_id"],
            "command_id": row["command_id"],
            "action": row["requested_action"],
            "risk_level": row["risk_level"],
        }
        for row in pending
    ]
    blocked: list[dict[str, Any]] = []
    if pending:
        blocked.append(
            {
                "node_id": "executor_eligible",
                "reason": "executor_disabled_phase_boundary",
                "required_human_decisions": len(pending),
            }
        )
    elif approved_count:
        blocked.append(
            {
                "node_id": "executor_eligible",
                "reason": "executor_disabled_phase_boundary",
                "approved_commands_waiting": approved_count,
            }
        )
    return {
        "schema_id": schema["schema_id"],
        "version": schema["version"],
        "source": "ledger_projection",
        "nodes": schema["nodes"],
        "ready": ready,
        "blocked": blocked,
    }


def build_snapshot() -> dict[str, Any]:
    """Build the Phase 1 read-only Control Tower snapshot."""
    ledger = init_ledger()
    kanban_adapter = observe_kanban_adapter()
    with sqlite3.connect(ledger) as conn:
        conn.row_factory = sqlite3.Row
        last_seq = conn.execute("SELECT COALESCE(MAX(sequence), 0) FROM events").fetchone()[0]
        evidence_counts = {
            maturity: _count(conn, "evidence", f"maturity = '{maturity}'")
            for maturity in ["advisory", "observed", "verified", "certified", "release_confirmed"]
        }
        stale_or_conflicting = _count(conn, "evidence", "stale_status IN ('stale', 'conflicting')")
        conflict_count = _count(conn, "adapter_observations", "conflict_event_id IS NOT NULL")
        tasks = _observed_tasks(conn)
        command_registry = _command_registry_snapshot(conn)
        human_inbox = _human_inbox_snapshot(conn)
        workflow_map = _workflow_map_snapshot(conn)
    blockers = [
        {
            "adapter": "kanban",
            "external_ref": f"task:{task.get('id')}",
            "title": task.get("title"),
            "reason": "kanban_status_blocked",
        }
        for task in tasks
        if task.get("status") == "blocked"
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "ledger": {
            "path": str(ledger),
            "canonical_fact_source": "Control Tower Ledger",
            "mode": "read_only_projection",
            "last_sequence": int(last_seq),
            "projection_status": "ok",
        },
        "ui_host": {
            "selected": "dashboard_plugin",
            "plugin_name": "control-tower",
            "reason": "Drop-in dashboard plugin integrates with existing /api/plugins host without making the dashboard a fact source.",
        },
        "adapters": {
            "kanban": {"role": "projection_adapter", "canonical": False, **kanban_adapter},
            "taskpool": {"role": "projection_adapter", "canonical": False},
            "brain": {"role": "projection_adapter", "canonical": False},
            "cron": {"role": "watchdog_adapter", "canonical": False},
            "gateway": {"role": "event_ingress_adapter", "canonical": False},
        },
        "taskpool_health": _taskpool_health_from_tasks(tasks),
        "next_dispatchable": _next_dispatchable_from_tasks(tasks),
        "top_blockers": blockers[:5],
        "command_registry": command_registry,
        "human_inbox": human_inbox,
        "workflow_map": workflow_map,
        "worker_runs": [],
        "cron_watchdogs": [],
        "evidence_summary": {
            **evidence_counts,
            "stale_or_conflicting": stale_or_conflicting,
        },
        "adapter_conflicts": [] if conflict_count == 0 else [{"count": conflict_count}],
    }
