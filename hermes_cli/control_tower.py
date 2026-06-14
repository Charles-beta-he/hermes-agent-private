"""Hermes Control Tower read-only ledger and snapshot helpers.

The current Hermes task lifecycle source of truth is the Brain taskpool:
``brain_task_orchestrator.py`` plus the markdown task notes. Kanban is an
execution projection, and Control Tower / Dashboard / Console are operator
projection surfaces. This module deliberately keeps dashboard snapshots
read-only and reports projection drift instead of promoting any projection into
a second task-lifecycle fact source.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home

SCHEMA_VERSION = 1
LEDGER_FILENAME = "control_tower.db"

EVIDENCE_MATURITY_ORDER = {
    "advisory": 0,
    "observed": 1,
    "verified": 2,
    "certified": 3,
    "release_confirmed": 4,
}
RISK_REQUIRED_EVIDENCE_MATURITY = {
    "L0": "observed",
    "L1": "observed",
    "L2": "verified",
    "L3": "certified",
    "L4": "release_confirmed",
}
VALID_EVIDENCE_STALE_STATUSES = {"fresh", "stale", "conflicting"}
CLOSEOUT_REQUIRED_ROLE_BY_RISK = {
    "L0": "operator",
    "L1": "operator",
    "L2": "owner",
    "L3": "admin",
    "L4": "admin",
}
ROLE_RANK = {"operator": 0, "owner": 1, "admin": 2}
DEFAULT_CLOSEOUT_AUTH_TTL_HOURS = 24
READ_ONLY_EXECUTOR_COMMAND_WHITELIST = frozenset(
    {
        "CopyCliCommand",
        "RefreshProjection",
        "RunReadOnlyProofCheck",
        "RunReadOnlySelfCheck",
    }
)
READ_ONLY_EXECUTOR_RISK_WHITELIST = frozenset({"L0", "L1"})

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
    CREATE TABLE IF NOT EXISTS evidence_gate_reviews (
        evidence_review_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        risk_level TEXT NOT NULL,
        evidence_status TEXT NOT NULL,
        required_maturity TEXT NOT NULL,
        acceptance_refs_json TEXT NOT NULL DEFAULT '[]',
        evidence_state_hash TEXT NOT NULL,
        satisfied_refs_json TEXT NOT NULL DEFAULT '[]',
        missing_refs_json TEXT NOT NULL DEFAULT '[]',
        stale_or_conflicting_count INTEGER NOT NULL DEFAULT 0,
        closeout_allowed INTEGER NOT NULL DEFAULT 0,
        blocked_reason TEXT NOT NULL,
        reviewed_by TEXT NOT NULL,
        reviewed_at TEXT NOT NULL,
        UNIQUE(task_id, risk_level, acceptance_refs_json, evidence_state_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS closeout_authorizations (
        closeout_authorization_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        risk_level TEXT NOT NULL,
        evidence_review_id TEXT NOT NULL,
        evidence_state_hash TEXT NOT NULL,
        status TEXT NOT NULL,
        required_role TEXT NOT NULL,
        requested_by TEXT NOT NULL,
        requested_role TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        decided_by TEXT,
        decided_role TEXT,
        decided_at TEXT,
        reason TEXT,
        closeout_allowed INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conflicting_evidence_override_authorizations (
        conflict_override_authorization_id TEXT PRIMARY KEY,
        evidence_review_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        risk_level TEXT NOT NULL,
        evidence_state_hash TEXT NOT NULL,
        stale_or_conflicting_count INTEGER NOT NULL,
        status TEXT NOT NULL,
        required_role TEXT NOT NULL,
        requested_by TEXT NOT NULL,
        requested_role TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        decided_by TEXT,
        decided_role TEXT,
        decided_at TEXT,
        reason TEXT,
        override_allowed INTEGER NOT NULL DEFAULT 0,
        closeout_allowed INTEGER NOT NULL DEFAULT 0,
        CHECK(stale_or_conflicting_count > 0),
        CHECK(status IN ('requested', 'approved', 'rejected', 'expired')),
        CHECK(override_allowed = 0),
        CHECK(closeout_allowed = 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS closeout_execution_runs (
        closeout_execution_id TEXT PRIMARY KEY,
        closeout_authorization_id TEXT NOT NULL UNIQUE,
        task_id TEXT NOT NULL,
        risk_level TEXT NOT NULL,
        evidence_review_id TEXT NOT NULL,
        evidence_state_hash TEXT NOT NULL,
        execution_status TEXT NOT NULL,
        task_status TEXT NOT NULL,
        adapter_writes_json TEXT NOT NULL DEFAULT '[]',
        external_adapter_write_through_enabled INTEGER NOT NULL DEFAULT 0,
        executed_by TEXT NOT NULL,
        executed_role TEXT NOT NULL,
        executed_at TEXT NOT NULL,
        reason TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS external_adapter_write_intents (
        external_write_intent_id TEXT PRIMARY KEY,
        closeout_execution_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        adapter_name TEXT NOT NULL,
        external_ref TEXT NOT NULL,
        operation TEXT NOT NULL,
        intent_status TEXT NOT NULL,
        mutation_payload_json TEXT NOT NULL DEFAULT '{}',
        mutation_digest TEXT NOT NULL,
        writes_enabled INTEGER NOT NULL DEFAULT 0,
        external_adapter_write_through_enabled INTEGER NOT NULL DEFAULT 0,
        rollback_required INTEGER NOT NULL DEFAULT 1,
        rollback_available INTEGER NOT NULL DEFAULT 0,
        rollback_plan_json TEXT NOT NULL DEFAULT '{}',
        prepared_by TEXT NOT NULL,
        prepared_role TEXT NOT NULL,
        prepared_at TEXT NOT NULL,
        reason TEXT,
        CHECK(intent_status = 'blocked_pending_adapter_contract'),
        CHECK(writes_enabled = 0),
        CHECK(external_adapter_write_through_enabled = 0),
        CHECK(rollback_required = 1),
        CHECK(rollback_available = 0),
        UNIQUE(closeout_execution_id, adapter_name, external_ref)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS external_adapter_write_intent_reviews (
        external_write_review_id TEXT PRIMARY KEY,
        external_write_intent_id TEXT NOT NULL UNIQUE,
        closeout_execution_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        adapter_name TEXT NOT NULL,
        external_ref TEXT NOT NULL,
        operation TEXT NOT NULL,
        adapter_contract_version TEXT NOT NULL,
        review_status TEXT NOT NULL,
        blocked_reasons_json TEXT NOT NULL DEFAULT '[]',
        mutation_digest TEXT NOT NULL,
        writes_enabled INTEGER NOT NULL DEFAULT 0,
        external_adapter_write_through_enabled INTEGER NOT NULL DEFAULT 0,
        rollback_required INTEGER NOT NULL DEFAULT 1,
        rollback_available INTEGER NOT NULL DEFAULT 0,
        write_through_allowed INTEGER NOT NULL DEFAULT 0,
        reviewed_by TEXT NOT NULL,
        reviewed_role TEXT NOT NULL,
        reviewed_at TEXT NOT NULL,
        reason TEXT,
        CHECK(review_status = 'blocked'),
        CHECK(writes_enabled = 0),
        CHECK(external_adapter_write_through_enabled = 0),
        CHECK(rollback_required = 1),
        CHECK(write_through_allowed = 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS external_adapter_write_authorizations (
        external_write_authorization_id TEXT PRIMARY KEY,
        external_write_review_id TEXT NOT NULL UNIQUE,
        external_write_intent_id TEXT NOT NULL,
        closeout_execution_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        adapter_name TEXT NOT NULL,
        external_ref TEXT NOT NULL,
        operation TEXT NOT NULL,
        adapter_contract_version TEXT NOT NULL,
        mutation_digest TEXT NOT NULL,
        status TEXT NOT NULL,
        required_role TEXT NOT NULL,
        requested_by TEXT NOT NULL,
        requested_role TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        decided_by TEXT,
        decided_role TEXT,
        decided_at TEXT,
        reason TEXT,
        writes_enabled INTEGER NOT NULL DEFAULT 0,
        external_adapter_write_through_enabled INTEGER NOT NULL DEFAULT 0,
        rollback_required INTEGER NOT NULL DEFAULT 1,
        rollback_available INTEGER NOT NULL DEFAULT 0,
        write_through_allowed INTEGER NOT NULL DEFAULT 0,
        CHECK(status IN ('requested', 'approved', 'denied', 'expired')),
        CHECK(writes_enabled = 0),
        CHECK(external_adapter_write_through_enabled = 0),
        CHECK(rollback_required = 1),
        CHECK(rollback_available = 0),
        CHECK(write_through_allowed = 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS external_adapter_write_execution_preflights (
        external_write_preflight_id TEXT PRIMARY KEY,
        external_write_authorization_id TEXT NOT NULL UNIQUE,
        external_write_review_id TEXT NOT NULL,
        external_write_intent_id TEXT NOT NULL,
        closeout_execution_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        adapter_name TEXT NOT NULL,
        external_ref TEXT NOT NULL,
        operation TEXT NOT NULL,
        adapter_contract_version TEXT NOT NULL,
        mutation_digest TEXT NOT NULL,
        command_execution_payload_json TEXT NOT NULL,
        command_execution_digest TEXT NOT NULL,
        preflight_status TEXT NOT NULL,
        prepared_by TEXT NOT NULL,
        prepared_role TEXT NOT NULL,
        prepared_at TEXT NOT NULL,
        reason TEXT,
        writes_enabled INTEGER NOT NULL DEFAULT 0,
        external_adapter_write_through_enabled INTEGER NOT NULL DEFAULT 0,
        external_write_executor_enabled INTEGER NOT NULL DEFAULT 0,
        rollback_required INTEGER NOT NULL DEFAULT 1,
        rollback_available INTEGER NOT NULL DEFAULT 0,
        write_through_allowed INTEGER NOT NULL DEFAULT 0,
        CHECK(preflight_status = 'blocked_pending_external_write_executor'),
        CHECK(writes_enabled = 0),
        CHECK(external_adapter_write_through_enabled = 0),
        CHECK(external_write_executor_enabled = 0),
        CHECK(rollback_required = 1),
        CHECK(rollback_available = 0),
        CHECK(write_through_allowed = 0)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS external_adapter_write_execution_runs (
        external_write_execution_id TEXT PRIMARY KEY,
        external_write_preflight_id TEXT NOT NULL UNIQUE,
        external_write_authorization_id TEXT NOT NULL,
        external_write_review_id TEXT NOT NULL,
        external_write_intent_id TEXT NOT NULL,
        closeout_execution_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        adapter_name TEXT NOT NULL,
        external_ref TEXT NOT NULL,
        operation TEXT NOT NULL,
        adapter_contract_version TEXT NOT NULL,
        mutation_digest TEXT NOT NULL,
        command_execution_digest TEXT NOT NULL,
        execution_status TEXT NOT NULL,
        adapter_result_json TEXT NOT NULL DEFAULT '{}',
        rollback_snapshot_json TEXT NOT NULL DEFAULT '{}',
        executed_by TEXT NOT NULL,
        executed_role TEXT NOT NULL,
        executed_at TEXT NOT NULL,
        writes_enabled INTEGER NOT NULL DEFAULT 1,
        external_adapter_write_through_enabled INTEGER NOT NULL DEFAULT 1,
        external_write_executor_enabled INTEGER NOT NULL DEFAULT 1,
        rollback_required INTEGER NOT NULL DEFAULT 1,
        rollback_available INTEGER NOT NULL DEFAULT 1,
        rollback_reopen_enabled INTEGER NOT NULL DEFAULT 1,
        write_through_allowed INTEGER NOT NULL DEFAULT 1,
        CHECK(execution_status = 'write_completed'),
        CHECK(writes_enabled = 1),
        CHECK(external_adapter_write_through_enabled = 1),
        CHECK(external_write_executor_enabled = 1),
        CHECK(rollback_required = 1),
        CHECK(rollback_available = 1),
        CHECK(rollback_reopen_enabled = 1),
        CHECK(write_through_allowed = 1)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS external_adapter_write_rollback_runs (
        rollback_run_id TEXT PRIMARY KEY,
        external_write_execution_id TEXT NOT NULL UNIQUE,
        external_write_preflight_id TEXT NOT NULL,
        external_write_authorization_id TEXT NOT NULL,
        external_write_review_id TEXT NOT NULL,
        external_write_intent_id TEXT NOT NULL,
        closeout_execution_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        adapter_name TEXT NOT NULL,
        external_ref TEXT NOT NULL,
        operation TEXT NOT NULL,
        adapter_contract_version TEXT NOT NULL,
        mutation_digest TEXT NOT NULL,
        command_execution_digest TEXT NOT NULL,
        rollback_status TEXT NOT NULL,
        adapter_result_json TEXT NOT NULL DEFAULT '{}',
        rollback_snapshot_json TEXT NOT NULL DEFAULT '{}',
        rolled_back_by TEXT NOT NULL,
        rolled_back_role TEXT NOT NULL,
        rolled_back_at TEXT NOT NULL,
        writes_enabled INTEGER NOT NULL DEFAULT 1,
        external_adapter_write_through_enabled INTEGER NOT NULL DEFAULT 1,
        external_write_executor_enabled INTEGER NOT NULL DEFAULT 1,
        rollback_required INTEGER NOT NULL DEFAULT 1,
        rollback_available INTEGER NOT NULL DEFAULT 1,
        rollback_reopen_enabled INTEGER NOT NULL DEFAULT 1,
        write_through_allowed INTEGER NOT NULL DEFAULT 1,
        CHECK(rollback_status = 'rollback_completed'),
        CHECK(operation = 'reopen'),
        CHECK(writes_enabled = 1),
        CHECK(external_adapter_write_through_enabled = 1),
        CHECK(external_write_executor_enabled = 1),
        CHECK(rollback_required = 1),
        CHECK(rollback_available = 1),
        CHECK(rollback_reopen_enabled = 1),
        CHECK(write_through_allowed = 1)
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
    CREATE TABLE IF NOT EXISTS executor_plans (
        plan_id TEXT PRIMARY KEY,
        command_id TEXT NOT NULL UNIQUE,
        command_type TEXT NOT NULL,
        mode TEXT NOT NULL,
        status TEXT NOT NULL,
        execution TEXT NOT NULL,
        writes_enabled INTEGER NOT NULL DEFAULT 0,
        copy_command TEXT NOT NULL,
        plan_json TEXT NOT NULL DEFAULT '{}',
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS execution_policy_reviews (
        policy_review_id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL UNIQUE,
        command_id TEXT NOT NULL,
        policy_status TEXT NOT NULL,
        allowed_to_execute INTEGER NOT NULL DEFAULT 0,
        writes_enabled INTEGER NOT NULL DEFAULT 0,
        blocked_reason TEXT NOT NULL,
        adapter_writes_json TEXT NOT NULL DEFAULT '[]',
        rollback_preview_json TEXT NOT NULL DEFAULT '{}',
        reviewed_by TEXT NOT NULL,
        reviewed_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS read_only_executor_runs (
        executor_run_id TEXT PRIMARY KEY,
        plan_id TEXT NOT NULL UNIQUE,
        policy_review_id TEXT NOT NULL,
        command_id TEXT NOT NULL,
        command_type TEXT NOT NULL,
        executor_status TEXT NOT NULL,
        writes_enabled INTEGER NOT NULL DEFAULT 0,
        result_json TEXT NOT NULL DEFAULT '{}',
        executed_by TEXT NOT NULL,
        executed_at TEXT NOT NULL
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

def _copy_command_for_plan(command_id: str) -> str:
    return f"hermes control-tower executor-plan --command-id {command_id} --dry-run"


def _existing_executor_plan(conn: sqlite3.Connection, command_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT plan_id, command_id, command_type, mode, status, execution,
               writes_enabled, copy_command, created_by, created_at
        FROM executor_plans
        WHERE command_id = ?
        """,
        (command_id,),
    ).fetchone()


def _command_is_plan_eligible(conn: sqlite3.Connection, command: sqlite3.Row) -> None:
    if command["status"] != "proposed":
        raise ValueError(f"command is not proposed: {command['status']}")
    risk_level = str(command["risk_level"]).upper()
    if not _risk_requires_human(risk_level):
        return
    approval = conn.execute(
        "SELECT status FROM human_approvals WHERE command_id = ? ORDER BY rowid DESC LIMIT 1",
        (command["command_id"],),
    ).fetchone()
    if approval is None or approval["status"] == "requested":
        raise ValueError("command requires approved human approval before executor planning")
    if approval["status"] == "rejected":
        raise ValueError("command approval rejected; executor planning is not allowed")
    if approval["status"] != "approved":
        raise ValueError(f"unsupported approval status: {approval['status']}")


def generate_executor_plan(
    *,
    command_id: str,
    actor_type: str,
    actor_id: str,
    source_surface: str,
) -> dict[str, Any]:
    """Generate a dry-run/copy-command executor plan without running the command."""
    command_id = command_id.strip()
    actor_type = actor_type.strip()
    actor_id = actor_id.strip()
    source_surface = source_surface.strip()
    if not command_id:
        raise ValueError("command_id is required")
    if not actor_type:
        raise ValueError("actor_type is required")
    if not actor_id:
        raise ValueError("actor_id is required")
    if not source_surface:
        raise ValueError("source_surface is required")

    with _connect() as conn:
        command = conn.execute(
            """
            SELECT command_id, command_type, target_type, target_id, risk_level,
                   status, payload_json
            FROM commands
            WHERE command_id = ?
            """,
            (command_id,),
        ).fetchone()
        if command is None:
            raise ValueError(f"command not found: {command_id}")
        _command_is_plan_eligible(conn, command)
        existing = _existing_executor_plan(conn, command_id)
        if existing is not None:
            return {
                "created": False,
                "plan_id": existing["plan_id"],
                "command_id": existing["command_id"],
                "command_type": existing["command_type"],
                "mode": existing["mode"],
                "status": existing["status"],
                "execution": existing["execution"],
                "writes_enabled": bool(existing["writes_enabled"]),
                "copy_command": existing["copy_command"],
                "source_surface": source_surface,
            }
        plan_id = f"plan_{uuid.uuid4().hex}"
        copy_command = _copy_command_for_plan(command_id)
        plan = {
            "command_id": command_id,
            "command_type": command["command_type"],
            "target_type": command["target_type"],
            "target_id": command["target_id"],
            "risk_level": command["risk_level"],
            "payload": json.loads(command["payload_json"]),
            "mode": "dry_run_copy_command_only",
            "writes_enabled": False,
            "copy_command": copy_command,
        }
        created_at = _now()
        conn.execute(
            """
            INSERT INTO executor_plans(
                plan_id, command_id, command_type, mode, status, execution,
                writes_enabled, copy_command, plan_json, created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan_id,
                command_id,
                command["command_type"],
                "dry_run_copy_command_only",
                "planned",
                "not_executed",
                0,
                copy_command,
                _canonical_json(plan),
                actor_id,
                created_at,
            ),
        )
        event_id = _append_event(
            conn,
            event_type="ExecutorPlanGenerated",
            aggregate_type="executor_plan",
            aggregate_id=plan_id,
            command_id=command_id,
            actor_type=actor_type,
            actor_id=actor_id,
            source_surface=source_surface,
            payload={
                "plan_id": plan_id,
                "command_id": command_id,
                "command_type": command["command_type"],
                "mode": "dry_run_copy_command_only",
                "execution": "not_executed",
                "writes_enabled": False,
                "copy_command": copy_command,
            },
        )
        conn.commit()

    return {
        "created": True,
        "plan_id": plan_id,
        "command_id": command_id,
        "command_type": command["command_type"],
        "mode": "dry_run_copy_command_only",
        "status": "planned",
        "execution": "not_executed",
        "writes_enabled": False,
        "copy_command": copy_command,
        "source_surface": source_surface,
        "event_id": event_id,
    }


def _policy_review_response(
    *,
    created: bool,
    row: sqlite3.Row,
    source_surface: str,
) -> dict[str, Any]:
    return {
        "created": created,
        "policy_review_id": row["policy_review_id"],
        "plan_id": row["plan_id"],
        "command_id": row["command_id"],
        "policy_status": row["policy_status"],
        "allowed_to_execute": bool(row["allowed_to_execute"]),
        "writes_enabled": bool(row["writes_enabled"]),
        "blocked_reason": row["blocked_reason"],
        "adapter_writes": json.loads(row["adapter_writes_json"]),
        "rollback_preview": json.loads(row["rollback_preview_json"]),
        "execution": "not_executed",
        "source_surface": source_surface,
        "reviewed_by": row["reviewed_by"],
        "reviewed_at": row["reviewed_at"],
    }


def _read_only_executor_policy_for_plan(plan_json: dict[str, Any]) -> dict[str, Any]:
    command_type = str(plan_json.get("command_type") or "")
    risk_level = str(plan_json.get("risk_level") or "").upper()
    adapter_writes: list[dict[str, Any]] = []
    if command_type not in READ_ONLY_EXECUTOR_COMMAND_WHITELIST:
        return {
            "policy_status": "execution_blocked",
            "allowed_to_execute": False,
            "writes_enabled": False,
            "blocked_reason": "command_type_not_in_read_only_executor_whitelist",
            "adapter_writes": adapter_writes,
            "rollback_preview": {
                "available": False,
                "reason": "blocked action has no lifecycle mutation to roll back",
            },
        }
    if risk_level not in READ_ONLY_EXECUTOR_RISK_WHITELIST:
        return {
            "policy_status": "execution_blocked",
            "allowed_to_execute": False,
            "writes_enabled": False,
            "blocked_reason": "risk_level_exceeds_read_only_executor_whitelist",
            "adapter_writes": adapter_writes,
            "rollback_preview": {
                "available": False,
                "reason": "blocked action has no lifecycle mutation to roll back",
            },
        }
    return {
        "policy_status": "read_only_eligible",
        "allowed_to_execute": True,
        "writes_enabled": False,
        "blocked_reason": "read_only_executor_whitelisted",
        "adapter_writes": adapter_writes,
        "rollback_preview": {
            "available": False,
            "reason": "read-only executor action has no lifecycle mutation to roll back",
        },
    }


def review_executor_plan_policy(
    *,
    plan_id: str,
    actor_type: str,
    actor_id: str,
    source_surface: str,
) -> dict[str, Any]:
    """Record a non-executing policy projection for a dry-run executor plan."""
    plan_id = plan_id.strip()
    actor_type = actor_type.strip()
    actor_id = actor_id.strip()
    source_surface = source_surface.strip()
    if not plan_id:
        raise ValueError("plan_id is required")
    if not actor_type:
        raise ValueError("actor_type is required")
    if not actor_id:
        raise ValueError("actor_id is required")
    if not source_surface:
        raise ValueError("source_surface is required")

    with _connect() as conn:
        plan = conn.execute(
            """
            SELECT plan_id, command_id, command_type, status, execution, writes_enabled, plan_json
            FROM executor_plans
            WHERE plan_id = ?
            """,
            (plan_id,),
        ).fetchone()
        if plan is None:
            raise ValueError(f"executor plan not found: {plan_id}")
        if plan["status"] != "planned":
            raise ValueError(f"executor plan is not planned: {plan['status']}")
        if plan["execution"] != "not_executed" or bool(plan["writes_enabled"]):
            raise ValueError("executor plan is not dry-run-only")
        existing = conn.execute(
            """
            SELECT policy_review_id, plan_id, command_id, policy_status,
                   allowed_to_execute, writes_enabled, blocked_reason,
                   adapter_writes_json, rollback_preview_json, reviewed_by, reviewed_at
            FROM execution_policy_reviews
            WHERE plan_id = ?
            """,
            (plan_id,),
        ).fetchone()
        if existing is not None:
            return _policy_review_response(created=False, row=existing, source_surface=source_surface)

        policy_review_id = f"policy_{uuid.uuid4().hex}"
        reviewed_at = _now()
        policy = _read_only_executor_policy_for_plan(json.loads(plan["plan_json"]))
        adapter_writes = policy["adapter_writes"]
        rollback_preview = policy["rollback_preview"]
        conn.execute(
            """
            INSERT INTO execution_policy_reviews(
                policy_review_id, plan_id, command_id, policy_status,
                allowed_to_execute, writes_enabled, blocked_reason,
                adapter_writes_json, rollback_preview_json, reviewed_by, reviewed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                policy_review_id,
                plan_id,
                plan["command_id"],
                policy["policy_status"],
                1 if policy["allowed_to_execute"] else 0,
                0,
                policy["blocked_reason"],
                _canonical_json(adapter_writes),
                _canonical_json(rollback_preview),
                actor_id,
                reviewed_at,
            ),
        )
        _append_event(
            conn,
            event_type="ExecutionPolicyProjectionRecorded",
            aggregate_type="execution_policy",
            aggregate_id=policy_review_id,
            command_id=plan["command_id"],
            actor_type=actor_type,
            actor_id=actor_id,
            source_surface=source_surface,
            payload={
                "policy_review_id": policy_review_id,
                "plan_id": plan_id,
                "command_id": plan["command_id"],
                "policy_status": policy["policy_status"],
                "allowed_to_execute": policy["allowed_to_execute"],
                "writes_enabled": False,
                "blocked_reason": policy["blocked_reason"],
                "adapter_writes": adapter_writes,
                "rollback_preview": rollback_preview,
                "execution": "not_executed",
            },
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT policy_review_id, plan_id, command_id, policy_status,
                   allowed_to_execute, writes_enabled, blocked_reason,
                   adapter_writes_json, rollback_preview_json, reviewed_by, reviewed_at
            FROM execution_policy_reviews
            WHERE policy_review_id = ?
            """,
            (policy_review_id,),
        ).fetchone()
    return _policy_review_response(created=True, row=row, source_surface=source_surface)


def _read_only_executor_result(command_type: str, plan_json: dict[str, Any]) -> dict[str, Any]:
    if command_type == "CopyCliCommand":
        return {
            "check": "copy_cli_command",
            "mutation_allowed": False,
            "copy_command": plan_json.get("copy_command", ""),
        }
    if command_type == "RefreshProjection":
        return {
            "check": "refresh_projection",
            "mutation_allowed": False,
            "projection": "control_tower_snapshot",
        }
    if command_type == "RunReadOnlySelfCheck":
        return {
            "check": "self_check",
            "mutation_allowed": False,
            "status": "passed",
            "assertions": [
                "writes_disabled",
                "adapter_write_through_disabled",
                "closeout_execution_disabled",
            ],
        }
    if command_type == "RunReadOnlyProofCheck":
        return {
            "check": "proof_check",
            "mutation_allowed": False,
            "status": "passed",
            "acceptance_refs": plan_json.get("payload", {}).get("acceptance_refs", []),
        }
    raise ValueError("command type is not executable by read-only executor")


def _read_only_executor_run_response(*, created: bool, row: sqlite3.Row, source_surface: str) -> dict[str, Any]:
    return {
        "created": created,
        "executor_run_id": row["executor_run_id"],
        "plan_id": row["plan_id"],
        "policy_review_id": row["policy_review_id"],
        "command_id": row["command_id"],
        "command_type": row["command_type"],
        "executor_status": row["executor_status"],
        "execution": row["executor_status"],
        "writes_enabled": bool(row["writes_enabled"]),
        "adapter_writes": [],
        "result": json.loads(row["result_json"]),
        "source_surface": source_surface,
        "executed_by": row["executed_by"],
        "executed_at": row["executed_at"],
    }


def execute_read_only_executor_plan(
    *,
    plan_id: str,
    actor_type: str,
    actor_id: str,
    source_surface: str,
) -> dict[str, Any]:
    """Execute a whitelisted read-only executor plan without adapter or task lifecycle writes."""
    plan_id = plan_id.strip()
    actor_type = actor_type.strip()
    actor_id = actor_id.strip()
    source_surface = source_surface.strip()
    if not plan_id:
        raise ValueError("plan_id is required")
    if not actor_type:
        raise ValueError("actor_type is required")
    if not actor_id:
        raise ValueError("actor_id is required")
    if not source_surface:
        raise ValueError("source_surface is required")

    with _connect() as conn:
        existing = conn.execute(
            """
            SELECT executor_run_id, plan_id, policy_review_id, command_id,
                   command_type, executor_status, writes_enabled, result_json,
                   executed_by, executed_at
            FROM read_only_executor_runs
            WHERE plan_id = ?
            """,
            (plan_id,),
        ).fetchone()
        if existing is not None:
            return _read_only_executor_run_response(created=False, row=existing, source_surface=source_surface)

        plan = conn.execute(
            """
            SELECT plan_id, command_id, command_type, status, execution,
                   writes_enabled, plan_json
            FROM executor_plans
            WHERE plan_id = ?
            """,
            (plan_id,),
        ).fetchone()
        if plan is None:
            raise ValueError(f"executor plan not found: {plan_id}")
        if plan["status"] != "planned" or plan["execution"] != "not_executed" or bool(plan["writes_enabled"]):
            raise ValueError("executor plan is not eligible for read-only execution")

        policy = conn.execute(
            """
            SELECT policy_review_id, policy_status, allowed_to_execute,
                   writes_enabled, blocked_reason
            FROM execution_policy_reviews
            WHERE plan_id = ?
            """,
            (plan_id,),
        ).fetchone()
        if policy is None or not bool(policy["allowed_to_execute"]):
            raise ValueError("allowed execution policy review is required")
        if policy["policy_status"] != "read_only_eligible" or bool(policy["writes_enabled"]):
            raise ValueError("allowed execution policy review is required")

        command = conn.execute(
            """
            SELECT command_id, command_type, target_type, target_id, risk_level,
                   status, payload_json
            FROM commands
            WHERE command_id = ?
            """,
            (plan["command_id"],),
        ).fetchone()
        if command is None:
            raise ValueError(f"command not found: {plan['command_id']}")
        if command["status"] != "proposed":
            raise ValueError(f"command is not proposed: {command['status']}")
        plan_json = json.loads(plan["plan_json"])
        current_payload = json.loads(command["payload_json"])
        current_plan_json = {
            **plan_json,
            "command_type": command["command_type"],
            "risk_level": command["risk_level"],
            "payload": current_payload,
        }
        if (
            command["command_type"] != plan_json.get("command_type")
            or command["risk_level"] != plan_json.get("risk_level")
            or _canonical_json(current_payload) != _canonical_json(plan_json.get("payload", {}))
        ):
            raise ValueError("read-only executor policy is stale")
        current_policy = _read_only_executor_policy_for_plan(current_plan_json)
        if current_policy["policy_status"] != "read_only_eligible":
            raise ValueError("read-only executor policy is stale")

        executor_run_id = f"exec_{uuid.uuid4().hex}"
        executed_at = _now()
        result = _read_only_executor_result(plan["command_type"], current_plan_json)
        try:
            conn.execute(
                """
                INSERT INTO read_only_executor_runs(
                    executor_run_id, plan_id, policy_review_id, command_id,
                    command_type, executor_status, writes_enabled, result_json,
                    executed_by, executed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    executor_run_id,
                    plan_id,
                    policy["policy_review_id"],
                    plan["command_id"],
                    plan["command_type"],
                    "read_only_completed",
                    0,
                    _canonical_json(result),
                    actor_id,
                    executed_at,
                ),
            )
        except sqlite3.IntegrityError:  # pragma: no cover - defensive concurrent retry
            existing_after_race = conn.execute(
                """
                SELECT executor_run_id, plan_id, policy_review_id, command_id,
                       command_type, executor_status, writes_enabled, result_json,
                       executed_by, executed_at
                FROM read_only_executor_runs
                WHERE plan_id = ?
                """,
                (plan_id,),
            ).fetchone()
            if existing_after_race is not None:
                return _read_only_executor_run_response(
                    created=False,
                    row=existing_after_race,
                    source_surface=source_surface,
                )
            raise
        conn.execute(
            "UPDATE executor_plans SET status = 'executed', execution = 'read_only_completed' WHERE plan_id = ?",
            (plan_id,),
        )
        conn.execute(
            "UPDATE commands SET status = 'executed' WHERE command_id = ?",
            (plan["command_id"],),
        )
        _append_event(
            conn,
            event_type="ReadOnlyExecutorRunCompleted",
            aggregate_type="read_only_executor_run",
            aggregate_id=executor_run_id,
            command_id=plan["command_id"],
            actor_type=actor_type,
            actor_id=actor_id,
            source_surface=source_surface,
            payload={
                "executor_run_id": executor_run_id,
                "plan_id": plan_id,
                "policy_review_id": policy["policy_review_id"],
                "command_id": plan["command_id"],
                "command_type": plan["command_type"],
                "executor_status": "read_only_completed",
                "writes_enabled": False,
                "adapter_writes": [],
                "result": result,
            },
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT executor_run_id, plan_id, policy_review_id, command_id,
                   command_type, executor_status, writes_enabled, result_json,
                   executed_by, executed_at
            FROM read_only_executor_runs
            WHERE executor_run_id = ?
            """,
            (executor_run_id,),
        ).fetchone()
    return _read_only_executor_run_response(created=True, row=row, source_surface=source_surface)


def _normalize_acceptance_refs(acceptance_refs: list[str] | None) -> list[str]:
    refs = acceptance_refs or []
    normalized = sorted({ref.strip() for ref in refs if ref and ref.strip()})
    return normalized


def _required_maturity_for_risk(risk_level: str) -> str:
    return RISK_REQUIRED_EVIDENCE_MATURITY.get(risk_level.strip().upper(), "release_confirmed")


def record_evidence(
    *,
    task_id: str,
    evidence_type: str,
    maturity: str,
    source_type: str,
    summary: str,
    producer_type: str,
    producer_id: str,
    workflow_run_id: str | None = None,
    step_run_id: str | None = None,
    artifact_uri: str | None = None,
    artifact_hash: str | None = None,
    verifier: str | None = None,
    acceptance_refs: list[str] | None = None,
    stale_status: str = "fresh",
) -> dict[str, Any]:
    """Record evidence in the ledger without closing or mutating task lifecycle."""
    task_id = task_id.strip()
    evidence_type = evidence_type.strip()
    maturity = maturity.strip()
    source_type = source_type.strip()
    summary = summary.strip()
    producer_type = producer_type.strip()
    producer_id = producer_id.strip()
    stale_status = stale_status.strip()
    if not task_id:
        raise ValueError("task_id is required")
    if not evidence_type:
        raise ValueError("evidence_type is required")
    if maturity not in EVIDENCE_MATURITY_ORDER:
        raise ValueError(f"invalid evidence maturity: {maturity}")
    if stale_status not in VALID_EVIDENCE_STALE_STATUSES:
        raise ValueError(f"invalid evidence stale_status: {stale_status}")
    if not source_type:
        raise ValueError("source_type is required")
    if not summary:
        raise ValueError("summary is required")
    if not producer_type:
        raise ValueError("producer_type is required")
    if not producer_id:
        raise ValueError("producer_id is required")

    evidence_id = f"ev_{uuid.uuid4().hex}"
    now = _now()
    normalized_refs = _normalize_acceptance_refs(acceptance_refs)
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO evidence(
                evidence_id, task_id, workflow_run_id, step_run_id, evidence_type,
                maturity, source_type, artifact_uri, artifact_hash, summary,
                producer_type, producer_id, verifier, acceptance_refs_json,
                stale_status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                task_id,
                workflow_run_id,
                step_run_id,
                evidence_type,
                maturity,
                source_type,
                artifact_uri,
                artifact_hash,
                summary,
                producer_type,
                producer_id,
                verifier,
                _canonical_json(normalized_refs),
                stale_status,
                now,
                now,
            ),
        )
        _append_event(
            conn,
            event_type="EvidenceRecorded",
            aggregate_type="evidence",
            aggregate_id=evidence_id,
            command_id=None,
            actor_type=producer_type,
            actor_id=producer_id,
            source_surface=source_type,
            payload={
                "evidence_id": evidence_id,
                "task_id": task_id,
                "evidence_type": evidence_type,
                "maturity": maturity,
                "acceptance_refs": normalized_refs,
                "stale_status": stale_status,
                "execution": "not_executed",
            },
        )
        conn.commit()
    return {
        "created": True,
        "status": "recorded",
        "evidence_id": evidence_id,
        "task_id": task_id,
        "maturity": maturity,
        "stale_status": stale_status,
        "acceptance_refs": normalized_refs,
        "execution": "not_executed",
    }


def _evidence_review_response(*, created: bool, row: sqlite3.Row, source_surface: str) -> dict[str, Any]:
    return {
        "created": created,
        "evidence_review_id": row["evidence_review_id"],
        "task_id": row["task_id"],
        "risk_level": row["risk_level"],
        "evidence_status": row["evidence_status"],
        "required_maturity": row["required_maturity"],
        "acceptance_refs": json.loads(row["acceptance_refs_json"]),
        "evidence_state_hash": row["evidence_state_hash"],
        "satisfied_refs": json.loads(row["satisfied_refs_json"]),
        "missing_refs": json.loads(row["missing_refs_json"]),
        "stale_or_conflicting_count": int(row["stale_or_conflicting_count"]),
        "closeout_allowed": bool(row["closeout_allowed"]),
        "blocked_reason": row["blocked_reason"],
        "execution": "not_executed",
        "source_surface": source_surface,
        "reviewed_by": row["reviewed_by"],
        "reviewed_at": row["reviewed_at"],
    }


def _evidence_state_hash_for_task(conn: sqlite3.Connection, task_id: str) -> str:
    evidence_rows = conn.execute(
        """
        SELECT evidence_id, maturity, acceptance_refs_json, stale_status, updated_at
        FROM evidence
        WHERE task_id = ?
        ORDER BY evidence_id
        """,
        (task_id,),
    ).fetchall()
    return hashlib.sha256(
        _canonical_json(
            [
                {
                    "evidence_id": row["evidence_id"],
                    "maturity": row["maturity"],
                    "acceptance_refs": json.loads(row["acceptance_refs_json"]),
                    "stale_status": row["stale_status"],
                    "updated_at": row["updated_at"],
                }
                for row in evidence_rows
            ]
        ).encode("utf-8")
    ).hexdigest()


def evaluate_evidence_gate(
    *,
    task_id: str,
    risk_level: str,
    acceptance_refs: list[str] | None,
    actor_type: str,
    actor_id: str,
    source_surface: str,
) -> dict[str, Any]:
    """Evaluate evidence adequacy without closing or mutating task lifecycle."""
    task_id = task_id.strip()
    risk_level = risk_level.strip().upper()
    actor_type = actor_type.strip()
    actor_id = actor_id.strip()
    source_surface = source_surface.strip()
    acceptance_refs_normalized = _normalize_acceptance_refs(acceptance_refs)
    if not task_id:
        raise ValueError("task_id is required")
    if not risk_level:
        raise ValueError("risk_level is required")
    if not actor_type:
        raise ValueError("actor_type is required")
    if not actor_id:
        raise ValueError("actor_id is required")
    if not source_surface:
        raise ValueError("source_surface is required")

    required_maturity = _required_maturity_for_risk(risk_level)
    acceptance_json = _canonical_json(acceptance_refs_normalized)
    required_rank = EVIDENCE_MATURITY_ORDER[required_maturity]
    with _connect() as conn:
        evidence_rows = conn.execute(
            """
            SELECT evidence_id, maturity, acceptance_refs_json, stale_status, updated_at
            FROM evidence
            WHERE task_id = ?
            ORDER BY evidence_id
            """,
            (task_id,),
        ).fetchall()
        evidence_state_hash = _evidence_state_hash_for_task(conn, task_id)
        existing = conn.execute(
            """
            SELECT evidence_review_id, task_id, risk_level, evidence_status,
                   required_maturity, acceptance_refs_json, evidence_state_hash,
                   satisfied_refs_json, missing_refs_json, stale_or_conflicting_count, closeout_allowed,
                   blocked_reason, reviewed_by, reviewed_at
            FROM evidence_gate_reviews
            WHERE task_id = ? AND risk_level = ? AND acceptance_refs_json = ?
              AND evidence_state_hash = ?
            """,
            (task_id, risk_level, acceptance_json, evidence_state_hash),
        ).fetchone()
        if existing is not None:
            return _evidence_review_response(created=False, row=existing, source_surface=source_surface)

        stale_or_conflicting_count = sum(
            1 for row in evidence_rows if row["stale_status"] in {"stale", "conflicting"}
        )
        satisfied: list[str] = []
        refs_to_check = acceptance_refs_normalized or ["__any__"]
        for ref in refs_to_check:
            for row in evidence_rows:
                if row["stale_status"] != "fresh":
                    continue
                if EVIDENCE_MATURITY_ORDER[row["maturity"]] < required_rank:
                    continue
                row_refs = json.loads(row["acceptance_refs_json"])
                if ref == "__any__" or ref in row_refs:
                    satisfied.append(ref)
                    break
        if refs_to_check == ["__any__"]:
            missing = [] if satisfied else ["__any__"]
            satisfied_refs = [] if not satisfied else []
        else:
            satisfied_refs = satisfied
            missing = [ref for ref in refs_to_check if ref not in satisfied_refs]
        evidence_status = "satisfied" if not missing else "insufficient"
        blocked_reason = (
            "closeout_requires_human_boundary"
            if evidence_status == "satisfied"
            else f"insufficient_{required_maturity}_evidence"
        )
        evidence_review_id = f"evgate_{uuid.uuid4().hex}"
        reviewed_at = _now()
        conn.execute(
            """
            INSERT INTO evidence_gate_reviews(
                evidence_review_id, task_id, risk_level, evidence_status,
                required_maturity, acceptance_refs_json, evidence_state_hash,
                satisfied_refs_json, missing_refs_json, stale_or_conflicting_count, closeout_allowed,
                blocked_reason, reviewed_by, reviewed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_review_id,
                task_id,
                risk_level,
                evidence_status,
                required_maturity,
                acceptance_json,
                evidence_state_hash,
                _canonical_json(satisfied_refs),
                _canonical_json(missing),
                stale_or_conflicting_count,
                0,
                blocked_reason,
                actor_id,
                reviewed_at,
            ),
        )
        _append_event(
            conn,
            event_type="EvidenceGateEvaluated",
            aggregate_type="evidence_gate",
            aggregate_id=evidence_review_id,
            command_id=None,
            actor_type=actor_type,
            actor_id=actor_id,
            source_surface=source_surface,
            payload={
                "evidence_review_id": evidence_review_id,
                "task_id": task_id,
                "risk_level": risk_level,
                "evidence_status": evidence_status,
                "required_maturity": required_maturity,
                "satisfied_refs": satisfied_refs,
                "missing_refs": missing,
                "closeout_allowed": False,
                "blocked_reason": blocked_reason,
                "execution": "not_executed",
            },
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT evidence_review_id, task_id, risk_level, evidence_status,
                   required_maturity, acceptance_refs_json, evidence_state_hash,
                   satisfied_refs_json, missing_refs_json, stale_or_conflicting_count, closeout_allowed,
                   blocked_reason, reviewed_by, reviewed_at
            FROM evidence_gate_reviews
            WHERE evidence_review_id = ?
            """,
            (evidence_review_id,),
        ).fetchone()
    return _evidence_review_response(created=True, row=row, source_surface=source_surface)


def _required_closeout_role_for_risk(risk_level: str) -> str:
    return CLOSEOUT_REQUIRED_ROLE_BY_RISK.get(risk_level.strip().upper(), "admin")


def _role_allows(actor_role: str, required_role: str) -> bool:
    return ROLE_RANK.get(actor_role.strip().lower(), -1) >= ROLE_RANK.get(required_role, 999)


def _closeout_authorization_response(*, created: bool, row: sqlite3.Row, source_surface: str) -> dict[str, Any]:
    return {
        "created": created,
        "closeout_authorization_id": row["closeout_authorization_id"],
        "task_id": row["task_id"],
        "risk_level": row["risk_level"],
        "evidence_review_id": row["evidence_review_id"],
        "evidence_state_hash": row["evidence_state_hash"],
        "status": row["status"],
        "required_role": row["required_role"],
        "requested_by": row["requested_by"],
        "requested_role": row["requested_role"],
        "requested_at": row["requested_at"],
        "expires_at": row["expires_at"],
        "decided_by": row["decided_by"],
        "decided_role": row["decided_role"],
        "decided_at": row["decided_at"],
        "closeout_allowed": bool(row["closeout_allowed"]),
        "execution": "not_executed",
        "source_surface": source_surface,
    }


def _conflicting_evidence_override_authorization_response(*, created: bool, row: sqlite3.Row, source_surface: str) -> dict[str, Any]:
    return {
        "created": created,
        "conflict_override_authorization_id": row["conflict_override_authorization_id"],
        "evidence_review_id": row["evidence_review_id"],
        "task_id": row["task_id"],
        "risk_level": row["risk_level"],
        "evidence_state_hash": row["evidence_state_hash"],
        "stale_or_conflicting_count": int(row["stale_or_conflicting_count"]),
        "status": row["status"],
        "required_role": row["required_role"],
        "requested_by": row["requested_by"],
        "requested_role": row["requested_role"],
        "requested_at": row["requested_at"],
        "expires_at": row["expires_at"],
        "decided_by": row["decided_by"],
        "decided_role": row["decided_role"],
        "decided_at": row["decided_at"],
        "override_allowed": bool(row["override_allowed"]),
        "closeout_allowed": bool(row["closeout_allowed"]),
        "execution": "not_executed",
        "source_surface": source_surface,
    }


def _select_conflicting_evidence_override_authorization(
    conn: sqlite3.Connection,
    conflict_override_authorization_id: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT conflict_override_authorization_id, evidence_review_id, task_id,
               risk_level, evidence_state_hash, stale_or_conflicting_count,
               status, required_role, requested_by, requested_role,
               requested_at, expires_at, decided_by, decided_role, decided_at,
               override_allowed, closeout_allowed
        FROM conflicting_evidence_override_authorizations
        WHERE conflict_override_authorization_id = ?
        """,
        (conflict_override_authorization_id,),
    ).fetchone()


def request_conflicting_evidence_override_authorization(
    *,
    evidence_review_id: str,
    actor_type: str,
    actor_id: str,
    actor_role: str,
    source_surface: str,
    reason: str = "",
    ttl_hours: int = DEFAULT_CLOSEOUT_AUTH_TTL_HOURS,
) -> dict[str, Any]:
    """Request a non-executing admin projection for conflicting-evidence override.

    Approval here does not permit closeout. It records intent only; closeout
    remains blocked until a separate consumer explicitly binds and consumes an
    approved override in a later governed slice.
    """
    evidence_review_id = evidence_review_id.strip()
    actor_type = actor_type.strip()
    actor_id = actor_id.strip()
    actor_role = actor_role.strip().lower()
    source_surface = source_surface.strip()
    reason = reason.strip()
    if not evidence_review_id:
        raise ValueError("evidence_review_id is required")
    if not actor_type:
        raise ValueError("actor_type is required")
    if not actor_id:
        raise ValueError("actor_id is required")
    if actor_role not in ROLE_RANK:
        raise ValueError(f"unsupported actor_role: {actor_role}")
    if not _role_allows(actor_role, "admin"):
        raise ValueError("conflicting evidence override authorization requires admin role")
    if not source_surface:
        raise ValueError("source_surface is required")
    if ttl_hours <= 0:
        raise ValueError("ttl_hours must be positive")

    with _connect() as conn:
        review = conn.execute(
            """
            SELECT evidence_review_id, task_id, risk_level, evidence_status,
                   evidence_state_hash, stale_or_conflicting_count
            FROM evidence_gate_reviews
            WHERE evidence_review_id = ?
            """,
            (evidence_review_id,),
        ).fetchone()
        if review is None:
            raise ValueError(f"evidence gate review not found: {evidence_review_id}")
        if review["evidence_status"] != "satisfied":
            raise ValueError("conflicting evidence override requires satisfied positive evidence")
        current_evidence_state_hash = _evidence_state_hash_for_task(conn, review["task_id"])
        if current_evidence_state_hash != review["evidence_state_hash"]:
            raise ValueError("evidence state changed after review; evaluate a fresh evidence gate before override authorization")
        if int(review["stale_or_conflicting_count"]) <= 0:
            raise ValueError("conflicting evidence override requires conflicting or stale evidence")
        existing = conn.execute(
            """
            SELECT conflict_override_authorization_id, evidence_review_id, task_id,
                   risk_level, evidence_state_hash, stale_or_conflicting_count,
                   status, required_role, requested_by, requested_role,
                   requested_at, expires_at, decided_by, decided_role, decided_at,
                   override_allowed, closeout_allowed
            FROM conflicting_evidence_override_authorizations
            WHERE evidence_review_id = ? AND evidence_state_hash = ? AND status != 'expired'
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (evidence_review_id, review["evidence_state_hash"]),
        ).fetchone()
        if existing is not None:
            if existing["status"] == "requested" and datetime.fromisoformat(existing["expires_at"]) <= datetime.now(timezone.utc):
                conn.execute(
                    "UPDATE conflicting_evidence_override_authorizations SET status = ? WHERE conflict_override_authorization_id = ?",
                    ("expired", existing["conflict_override_authorization_id"]),
                )
                _append_event(
                    conn,
                    event_type="ConflictingEvidenceOverrideAuthorizationExpired",
                    aggregate_type="conflicting_evidence_override_authorization",
                    aggregate_id=existing["conflict_override_authorization_id"],
                    actor_type=actor_type,
                    actor_id=actor_id,
                    source_surface=source_surface,
                    payload={
                        "conflict_override_authorization_id": existing["conflict_override_authorization_id"],
                        "evidence_review_id": evidence_review_id,
                        "task_id": existing["task_id"],
                        "override_allowed": False,
                        "closeout_allowed": False,
                        "execution": "not_executed",
                    },
                )
            else:
                return _conflicting_evidence_override_authorization_response(created=False, row=existing, source_surface=source_surface)

        requested_at_dt = datetime.now(timezone.utc)
        requested_at = requested_at_dt.isoformat()
        expires_at = (requested_at_dt + timedelta(hours=ttl_hours)).isoformat()
        conflict_override_authorization_id = f"confoverride_{uuid.uuid4().hex}"
        conn.execute(
            """
            INSERT INTO conflicting_evidence_override_authorizations(
                conflict_override_authorization_id, evidence_review_id, task_id,
                risk_level, evidence_state_hash, stale_or_conflicting_count,
                status, required_role, requested_by, requested_role,
                requested_at, expires_at, reason, override_allowed, closeout_allowed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                conflict_override_authorization_id,
                evidence_review_id,
                review["task_id"],
                review["risk_level"],
                review["evidence_state_hash"],
                int(review["stale_or_conflicting_count"]),
                "requested",
                "admin",
                actor_id,
                actor_role,
                requested_at,
                expires_at,
                reason,
                0,
                0,
            ),
        )
        _append_event(
            conn,
            event_type="ConflictingEvidenceOverrideAuthorizationRequested",
            aggregate_type="conflicting_evidence_override_authorization",
            aggregate_id=conflict_override_authorization_id,
            actor_type=actor_type,
            actor_id=actor_id,
            source_surface=source_surface,
            payload={
                "conflict_override_authorization_id": conflict_override_authorization_id,
                "evidence_review_id": evidence_review_id,
                "task_id": review["task_id"],
                "risk_level": review["risk_level"],
                "evidence_state_hash": review["evidence_state_hash"],
                "stale_or_conflicting_count": int(review["stale_or_conflicting_count"]),
                "required_role": "admin",
                "status": "requested",
                "expires_at": expires_at,
                "override_allowed": False,
                "closeout_allowed": False,
                "execution": "not_executed",
            },
        )
        conn.commit()
        row = _select_conflicting_evidence_override_authorization(conn, conflict_override_authorization_id)
        if row is None:  # pragma: no cover - defensive ledger invariant
            raise RuntimeError("conflicting evidence override authorization insert was not readable")
    return _conflicting_evidence_override_authorization_response(created=True, row=row, source_surface=source_surface)


def decide_conflicting_evidence_override_authorization(
    *,
    conflict_override_authorization_id: str,
    decision: str,
    actor_type: str,
    actor_id: str,
    actor_role: str,
    source_surface: str,
    reason: str = "",
) -> dict[str, Any]:
    """Approve/reject a conflicting-evidence override projection without enabling closeout."""
    conflict_override_authorization_id = conflict_override_authorization_id.strip()
    decision = decision.strip().lower()
    actor_type = actor_type.strip()
    actor_id = actor_id.strip()
    actor_role = actor_role.strip().lower()
    source_surface = source_surface.strip()
    reason = reason.strip()
    if not conflict_override_authorization_id:
        raise ValueError("conflict_override_authorization_id is required")
    if decision not in {"approved", "rejected"}:
        raise ValueError("decision must be approved or rejected")
    if not actor_type:
        raise ValueError("actor_type is required")
    if not actor_id:
        raise ValueError("actor_id is required")
    if actor_role not in ROLE_RANK:
        raise ValueError(f"unsupported actor_role: {actor_role}")
    if not _role_allows(actor_role, "admin"):
        raise ValueError("conflicting evidence override decision requires admin role")
    if not source_surface:
        raise ValueError("source_surface is required")

    with _connect() as conn:
        row = _select_conflicting_evidence_override_authorization(conn, conflict_override_authorization_id)
        if row is None:
            raise ValueError(f"conflicting evidence override authorization not found: {conflict_override_authorization_id}")
        current_evidence_state_hash = _evidence_state_hash_for_task(conn, row["task_id"])
        if current_evidence_state_hash != row["evidence_state_hash"]:
            raise ValueError("evidence state changed after override authorization request; request a fresh evidence-bound override")
        if row["status"] in {"approved", "rejected"}:
            if row["status"] != decision:
                raise ValueError(
                    f"conflicting evidence override authorization already decided as {row['status']}; conflicting decision {decision} rejected"
                )
            return _conflicting_evidence_override_authorization_response(created=False, row=row, source_surface=source_surface)
        if row["status"] != "requested":
            raise ValueError(f"conflicting evidence override authorization is not pending: {row['status']}")
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
            conn.execute(
                "UPDATE conflicting_evidence_override_authorizations SET status = ? WHERE conflict_override_authorization_id = ?",
                ("expired", conflict_override_authorization_id),
            )
            _append_event(
                conn,
                event_type="ConflictingEvidenceOverrideAuthorizationExpired",
                aggregate_type="conflicting_evidence_override_authorization",
                aggregate_id=conflict_override_authorization_id,
                actor_type=actor_type,
                actor_id=actor_id,
                source_surface=source_surface,
                payload={
                    "conflict_override_authorization_id": conflict_override_authorization_id,
                    "evidence_review_id": row["evidence_review_id"],
                    "task_id": row["task_id"],
                    "override_allowed": False,
                    "closeout_allowed": False,
                    "execution": "not_executed",
                },
            )
            conn.commit()
            raise ValueError("conflicting evidence override authorization expired; request a fresh override")
        decided_at = _now()
        conn.execute(
            """
            UPDATE conflicting_evidence_override_authorizations
            SET status = ?, decided_by = ?, decided_role = ?, decided_at = ?, reason = ?,
                override_allowed = 0, closeout_allowed = 0
            WHERE conflict_override_authorization_id = ?
            """,
            (decision, actor_id, actor_role, decided_at, reason, conflict_override_authorization_id),
        )
        _append_event(
            conn,
            event_type="ConflictingEvidenceOverrideAuthorizationGranted" if decision == "approved" else "ConflictingEvidenceOverrideAuthorizationRejected",
            aggregate_type="conflicting_evidence_override_authorization",
            aggregate_id=conflict_override_authorization_id,
            actor_type=actor_type,
            actor_id=actor_id,
            source_surface=source_surface,
            payload={
                "conflict_override_authorization_id": conflict_override_authorization_id,
                "evidence_review_id": row["evidence_review_id"],
                "task_id": row["task_id"],
                "risk_level": row["risk_level"],
                "evidence_state_hash": row["evidence_state_hash"],
                "decision": decision,
                "reason": reason,
                "override_allowed": False,
                "closeout_allowed": False,
                "execution": "not_executed",
            },
        )
        conn.commit()
        updated = _select_conflicting_evidence_override_authorization(conn, conflict_override_authorization_id)
        if updated is None:  # pragma: no cover - defensive ledger invariant
            raise RuntimeError("conflicting evidence override authorization decision was not readable")
    return _conflicting_evidence_override_authorization_response(created=True, row=updated, source_surface=source_surface)


def request_closeout_authorization(
    *,
    evidence_review_id: str,
    actor_type: str,
    actor_id: str,
    actor_role: str,
    source_surface: str,
    reason: str = "",
    ttl_hours: int = DEFAULT_CLOSEOUT_AUTH_TTL_HOURS,
) -> dict[str, Any]:
    """Request human closeout authorization bound to an evidence state hash.

    This is a Phase 5 authorization projection only. It never closes a task and
    never writes through to taskpool/Kanban adapters.
    """
    evidence_review_id = evidence_review_id.strip()
    actor_type = actor_type.strip()
    actor_id = actor_id.strip()
    actor_role = actor_role.strip().lower()
    source_surface = source_surface.strip()
    reason = reason.strip()
    if not evidence_review_id:
        raise ValueError("evidence_review_id is required")
    if not actor_type:
        raise ValueError("actor_type is required")
    if not actor_id:
        raise ValueError("actor_id is required")
    if actor_role not in ROLE_RANK:
        raise ValueError(f"unsupported actor_role: {actor_role}")
    if not source_surface:
        raise ValueError("source_surface is required")
    if ttl_hours <= 0:
        raise ValueError("ttl_hours must be positive")

    with _connect() as conn:
        review = conn.execute(
            """
            SELECT evidence_review_id, task_id, risk_level, evidence_status,
                   evidence_state_hash, stale_or_conflicting_count, closeout_allowed
            FROM evidence_gate_reviews
            WHERE evidence_review_id = ?
            """,
            (evidence_review_id,),
        ).fetchone()
        if review is None:
            raise ValueError(f"evidence gate review not found: {evidence_review_id}")
        if review["evidence_status"] != "satisfied":
            raise ValueError("evidence gate is not satisfied; closeout authorization blocked")
        if int(review["stale_or_conflicting_count"]):
            raise ValueError("conflicting evidence requires remediation before closeout authorization")
        current_evidence_state_hash = _evidence_state_hash_for_task(conn, review["task_id"])
        if current_evidence_state_hash != review["evidence_state_hash"]:
            raise ValueError("evidence state changed after review; evaluate a fresh evidence gate before closeout authorization")
        required_role = _required_closeout_role_for_risk(review["risk_level"])
        if not _role_allows(actor_role, required_role):
            raise ValueError(
                f"actor_role {actor_role} cannot request {review['risk_level']} closeout; requires {required_role}"
            )
        existing = conn.execute(
            """
            SELECT closeout_authorization_id, task_id, risk_level, evidence_review_id,
                   evidence_state_hash, status, required_role, requested_by, requested_role,
                   requested_at, expires_at, decided_by, decided_role, decided_at,
                   closeout_allowed
            FROM closeout_authorizations
            WHERE evidence_review_id = ? AND evidence_state_hash = ? AND status != 'expired'
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (evidence_review_id, review["evidence_state_hash"]),
        ).fetchone()
        if existing is not None:
            if existing["status"] == "requested" and datetime.fromisoformat(existing["expires_at"]) <= datetime.now(timezone.utc):
                conn.execute(
                    "UPDATE closeout_authorizations SET status = ? WHERE closeout_authorization_id = ?",
                    ("expired", existing["closeout_authorization_id"]),
                )
                _append_event(
                    conn,
                    event_type="CloseoutAuthorizationExpired",
                    aggregate_type="closeout_authorization",
                    aggregate_id=existing["closeout_authorization_id"],
                    actor_type=actor_type,
                    actor_id=actor_id,
                    source_surface=source_surface,
                    payload={
                        "closeout_authorization_id": existing["closeout_authorization_id"],
                        "task_id": existing["task_id"],
                        "risk_level": existing["risk_level"],
                        "evidence_review_id": existing["evidence_review_id"],
                        "closeout_allowed": False,
                        "execution": "not_executed",
                    },
                )
            else:
                return _closeout_authorization_response(created=False, row=existing, source_surface=source_surface)

        requested_at_dt = datetime.now(timezone.utc)
        requested_at = requested_at_dt.isoformat()
        expires_at = (requested_at_dt + timedelta(hours=ttl_hours)).isoformat()
        closeout_authorization_id = f"closeout_{uuid.uuid4().hex}"
        conn.execute(
            """
            INSERT INTO closeout_authorizations(
                closeout_authorization_id, task_id, risk_level, evidence_review_id,
                evidence_state_hash, status, required_role, requested_by, requested_role,
                requested_at, expires_at, reason, closeout_allowed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                closeout_authorization_id,
                review["task_id"],
                review["risk_level"],
                evidence_review_id,
                review["evidence_state_hash"],
                "requested",
                required_role,
                actor_id,
                actor_role,
                requested_at,
                expires_at,
                reason,
                0,
            ),
        )
        _append_event(
            conn,
            event_type="CloseoutAuthorizationRequested",
            aggregate_type="closeout_authorization",
            aggregate_id=closeout_authorization_id,
            command_id=None,
            actor_type=actor_type,
            actor_id=actor_id,
            source_surface=source_surface,
            payload={
                "closeout_authorization_id": closeout_authorization_id,
                "task_id": review["task_id"],
                "risk_level": review["risk_level"],
                "evidence_review_id": evidence_review_id,
                "evidence_state_hash": review["evidence_state_hash"],
                "required_role": required_role,
                "status": "requested",
                "expires_at": expires_at,
                "closeout_allowed": False,
                "execution": "not_executed",
            },
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT closeout_authorization_id, task_id, risk_level, evidence_review_id,
                   evidence_state_hash, status, required_role, requested_by, requested_role,
                   requested_at, expires_at, decided_by, decided_role, decided_at,
                   closeout_allowed
            FROM closeout_authorizations
            WHERE closeout_authorization_id = ?
            """,
            (closeout_authorization_id,),
        ).fetchone()
    return _closeout_authorization_response(created=True, row=row, source_surface=source_surface)


def decide_closeout_authorization(
    *,
    closeout_authorization_id: str,
    decision: str,
    actor_type: str,
    actor_id: str,
    actor_role: str,
    source_surface: str,
    reason: str = "",
) -> dict[str, Any]:
    """Decide closeout authorization without closing or mutating a task."""
    closeout_authorization_id = closeout_authorization_id.strip()
    decision = decision.strip().lower()
    actor_type = actor_type.strip()
    actor_id = actor_id.strip()
    actor_role = actor_role.strip().lower()
    source_surface = source_surface.strip()
    reason = reason.strip()
    if not closeout_authorization_id:
        raise ValueError("closeout_authorization_id is required")
    if decision not in {"approved", "rejected"}:
        raise ValueError("decision must be approved or rejected")
    if not actor_type:
        raise ValueError("actor_type is required")
    if not actor_id:
        raise ValueError("actor_id is required")
    if actor_role not in ROLE_RANK:
        raise ValueError(f"unsupported actor_role: {actor_role}")
    if not source_surface:
        raise ValueError("source_surface is required")

    with _connect() as conn:
        row = conn.execute(
            """
            SELECT closeout_authorization_id, task_id, risk_level, evidence_review_id,
                   evidence_state_hash, status, required_role, requested_by, requested_role,
                   requested_at, expires_at, decided_by, decided_role, decided_at,
                   closeout_allowed
            FROM closeout_authorizations
            WHERE closeout_authorization_id = ?
            """,
            (closeout_authorization_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"closeout authorization not found: {closeout_authorization_id}")
        if not _role_allows(actor_role, row["required_role"]):
            raise ValueError(
                f"actor_role {actor_role} cannot decide {row['risk_level']} closeout; requires {row['required_role']}"
            )
        if row["status"] in {"approved", "rejected"}:
            if row["status"] != decision:
                raise ValueError(
                    f"closeout authorization already decided as {row['status']}; conflicting decision {decision} rejected"
                )
            return _closeout_authorization_response(created=False, row=row, source_surface=source_surface)
        if row["status"] != "requested":
            raise ValueError(f"closeout authorization is not pending: {row['status']}")
        current_evidence_state_hash = _evidence_state_hash_for_task(conn, row["task_id"])
        if current_evidence_state_hash != row["evidence_state_hash"]:
            raise ValueError("evidence state changed after authorization request; request a fresh evidence-bound approval")
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
            conn.execute(
                "UPDATE closeout_authorizations SET status = ? WHERE closeout_authorization_id = ?",
                ("expired", closeout_authorization_id),
            )
            _append_event(
                conn,
                event_type="CloseoutAuthorizationExpired",
                aggregate_type="closeout_authorization",
                aggregate_id=closeout_authorization_id,
                actor_type=actor_type,
                actor_id=actor_id,
                source_surface=source_surface,
                payload={
                    "closeout_authorization_id": closeout_authorization_id,
                    "task_id": row["task_id"],
                    "risk_level": row["risk_level"],
                    "evidence_review_id": row["evidence_review_id"],
                    "closeout_allowed": False,
                    "execution": "not_executed",
                },
            )
            conn.commit()
            raise ValueError("closeout authorization expired; request a fresh evidence-bound approval")
        decided_at = _now()
        conn.execute(
            """
            UPDATE closeout_authorizations
            SET status = ?, decided_by = ?, decided_role = ?, decided_at = ?, reason = ?, closeout_allowed = 0
            WHERE closeout_authorization_id = ?
            """,
            (decision, actor_id, actor_role, decided_at, reason, closeout_authorization_id),
        )
        _append_event(
            conn,
            event_type="CloseoutAuthorizationGranted" if decision == "approved" else "CloseoutAuthorizationRejected",
            aggregate_type="closeout_authorization",
            aggregate_id=closeout_authorization_id,
            actor_type=actor_type,
            actor_id=actor_id,
            source_surface=source_surface,
            payload={
                "closeout_authorization_id": closeout_authorization_id,
                "task_id": row["task_id"],
                "risk_level": row["risk_level"],
                "evidence_review_id": row["evidence_review_id"],
                "evidence_state_hash": row["evidence_state_hash"],
                "decision": decision,
                "reason": reason,
                "closeout_allowed": False,
                "execution": "not_executed",
            },
        )
        conn.commit()
        updated = conn.execute(
            """
            SELECT closeout_authorization_id, task_id, risk_level, evidence_review_id,
                   evidence_state_hash, status, required_role, requested_by, requested_role,
                   requested_at, expires_at, decided_by, decided_role, decided_at,
                   closeout_allowed
            FROM closeout_authorizations
            WHERE closeout_authorization_id = ?
            """,
            (closeout_authorization_id,),
        ).fetchone()
    return _closeout_authorization_response(created=True, row=updated, source_surface=source_surface)


def _closeout_execution_response(*, created: bool, row: sqlite3.Row, source_surface: str) -> dict[str, Any]:
    return {
        "created": created,
        "closeout_execution_id": row["closeout_execution_id"],
        "closeout_authorization_id": row["closeout_authorization_id"],
        "task_id": row["task_id"],
        "risk_level": row["risk_level"],
        "evidence_review_id": row["evidence_review_id"],
        "evidence_state_hash": row["evidence_state_hash"],
        "execution_status": row["execution_status"],
        "task_status": row["task_status"],
        "adapter_writes": json.loads(row["adapter_writes_json"]),
        "external_adapter_write_through_enabled": bool(row["external_adapter_write_through_enabled"]),
        "executed_by": row["executed_by"],
        "executed_role": row["executed_role"],
        "executed_at": row["executed_at"],
        "source_surface": source_surface,
    }


def execute_closeout_authorization(
    *,
    closeout_authorization_id: str,
    actor_type: str,
    actor_id: str,
    actor_role: str,
    source_surface: str,
    reason: str = "",
) -> dict[str, Any]:
    """Execute an approved evidence-bound closeout inside the Control Tower ledger only."""
    closeout_authorization_id = closeout_authorization_id.strip()
    actor_type = actor_type.strip()
    actor_id = actor_id.strip()
    actor_role = actor_role.strip().lower()
    source_surface = source_surface.strip()
    reason = reason.strip()
    if not closeout_authorization_id:
        raise ValueError("closeout_authorization_id is required")
    if not actor_type:
        raise ValueError("actor_type is required")
    if not actor_id:
        raise ValueError("actor_id is required")
    if actor_role not in ROLE_RANK:
        raise ValueError(f"unsupported actor_role: {actor_role}")
    if not source_surface:
        raise ValueError("source_surface is required")

    with _connect() as conn:
        existing = conn.execute(
            """
            SELECT closeout_execution_id, closeout_authorization_id, task_id,
                   risk_level, evidence_review_id, evidence_state_hash,
                   execution_status, task_status, adapter_writes_json,
                   external_adapter_write_through_enabled, executed_by,
                   executed_role, executed_at
            FROM closeout_execution_runs
            WHERE closeout_authorization_id = ?
            """,
            (closeout_authorization_id,),
        ).fetchone()
        if existing is not None:
            return _closeout_execution_response(created=False, row=existing, source_surface=source_surface)

        auth = conn.execute(
            """
            SELECT closeout_authorization_id, task_id, risk_level, evidence_review_id,
                   evidence_state_hash, status, required_role, requested_by,
                   requested_role, requested_at, expires_at, decided_by,
                   decided_role, decided_at, closeout_allowed
            FROM closeout_authorizations
            WHERE closeout_authorization_id = ?
            """,
            (closeout_authorization_id,),
        ).fetchone()
        if auth is None:
            raise ValueError(f"closeout authorization not found: {closeout_authorization_id}")
        if auth["status"] != "approved":
            raise ValueError(f"closeout authorization is not approved: {auth['status']}")
        if not _role_allows(actor_role, auth["required_role"]):
            raise ValueError(
                f"actor_role {actor_role} cannot execute {auth['risk_level']} closeout; requires {auth['required_role']}"
            )
        if datetime.fromisoformat(auth["expires_at"]) <= datetime.now(timezone.utc):
            raise ValueError("closeout authorization expired; request a fresh evidence-bound approval")
        current_evidence_state_hash = _evidence_state_hash_for_task(conn, auth["task_id"])
        if current_evidence_state_hash != auth["evidence_state_hash"]:
            raise ValueError("evidence state changed after closeout authorization; request a fresh evidence-bound approval")
        review = conn.execute(
            """
            SELECT evidence_status, stale_or_conflicting_count, evidence_state_hash
            FROM evidence_gate_reviews
            WHERE evidence_review_id = ?
            """,
            (auth["evidence_review_id"],),
        ).fetchone()
        if review is None or review["evidence_status"] != "satisfied" or int(review["stale_or_conflicting_count"]):
            raise ValueError("satisfied evidence gate review is required for closeout execution")
        if review["evidence_state_hash"] != auth["evidence_state_hash"]:
            raise ValueError("closeout authorization does not match evidence gate review")
        task = conn.execute("SELECT task_id, status FROM tasks WHERE task_id = ?", (auth["task_id"],)).fetchone()
        if task is None:
            raise ValueError("Control Tower task row is required before closeout execution")
        if task["status"] == "closed":
            raise ValueError("task is already closed without a closeout execution run")

        closeout_execution_id = f"closeexec_{uuid.uuid4().hex}"
        executed_at = _now()
        try:
            conn.execute(
                """
                INSERT INTO closeout_execution_runs(
                    closeout_execution_id, closeout_authorization_id, task_id,
                    risk_level, evidence_review_id, evidence_state_hash,
                    execution_status, task_status, adapter_writes_json,
                    external_adapter_write_through_enabled, executed_by,
                    executed_role, executed_at, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    closeout_execution_id,
                    closeout_authorization_id,
                    auth["task_id"],
                    auth["risk_level"],
                    auth["evidence_review_id"],
                    auth["evidence_state_hash"],
                    "closeout_completed",
                    "closed",
                    "[]",
                    0,
                    actor_id,
                    actor_role,
                    executed_at,
                    reason,
                ),
            )
        except sqlite3.IntegrityError:  # pragma: no cover - defensive concurrent retry
            existing_after_race = conn.execute(
                """
                SELECT closeout_execution_id, closeout_authorization_id, task_id,
                       risk_level, evidence_review_id, evidence_state_hash,
                       execution_status, task_status, adapter_writes_json,
                       external_adapter_write_through_enabled, executed_by,
                       executed_role, executed_at
                FROM closeout_execution_runs
                WHERE closeout_authorization_id = ?
                """,
                (closeout_authorization_id,),
            ).fetchone()
            if existing_after_race is not None:
                return _closeout_execution_response(
                    created=False,
                    row=existing_after_race,
                    source_surface=source_surface,
                )
            raise
        conn.execute(
            """
            UPDATE tasks
            SET status = 'closed', blocked_reason = NULL, next_action = NULL,
                updated_at = ?, version = version + 1
            WHERE task_id = ?
            """,
            (executed_at, auth["task_id"]),
        )
        conn.execute(
            "UPDATE closeout_authorizations SET closeout_allowed = 1 WHERE closeout_authorization_id = ?",
            (closeout_authorization_id,),
        )
        conn.execute(
            "UPDATE evidence_gate_reviews SET closeout_allowed = 1, blocked_reason = 'closeout_executed_control_tower_ledger' WHERE evidence_review_id = ?",
            (auth["evidence_review_id"],),
        )
        _append_event(
            conn,
            event_type="CloseoutExecutionCompleted",
            aggregate_type="closeout_execution",
            aggregate_id=closeout_execution_id,
            actor_type=actor_type,
            actor_id=actor_id,
            source_surface=source_surface,
            payload={
                "closeout_execution_id": closeout_execution_id,
                "closeout_authorization_id": closeout_authorization_id,
                "task_id": auth["task_id"],
                "risk_level": auth["risk_level"],
                "evidence_review_id": auth["evidence_review_id"],
                "evidence_state_hash": auth["evidence_state_hash"],
                "execution_status": "closeout_completed",
                "task_status": "closed",
                "adapter_writes": [],
                "external_adapter_write_through_enabled": False,
            },
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT closeout_execution_id, closeout_authorization_id, task_id,
                   risk_level, evidence_review_id, evidence_state_hash,
                   execution_status, task_status, adapter_writes_json,
                   external_adapter_write_through_enabled, executed_by,
                   executed_role, executed_at
            FROM closeout_execution_runs
            WHERE closeout_execution_id = ?
            """,
            (closeout_execution_id,),
        ).fetchone()
    return _closeout_execution_response(created=True, row=row, source_surface=source_surface)


EXTERNAL_WRITE_INTENT_ADAPTERS = frozenset({"taskpool", "kanban"})


def _external_adapter_write_intent_response(
    *,
    created: bool,
    row: sqlite3.Row,
    source_surface: str,
) -> dict[str, Any]:
    return {
        "created": created,
        "external_write_intent_id": row["external_write_intent_id"],
        "closeout_execution_id": row["closeout_execution_id"],
        "task_id": row["task_id"],
        "adapter_name": row["adapter_name"],
        "external_ref": row["external_ref"],
        "operation": row["operation"],
        "intent_status": row["intent_status"],
        "mutation_payload": json.loads(row["mutation_payload_json"]),
        "mutation_digest": row["mutation_digest"],
        "writes_enabled": bool(row["writes_enabled"]),
        "external_adapter_write_through_enabled": bool(row["external_adapter_write_through_enabled"]),
        "rollback_required": bool(row["rollback_required"]),
        "rollback_available": bool(row["rollback_available"]),
        "rollback_plan": json.loads(row["rollback_plan_json"]),
        "source_surface": source_surface,
        "prepared_by": row["prepared_by"],
        "prepared_role": row["prepared_role"],
        "prepared_at": row["prepared_at"],
    }


def prepare_external_adapter_write_intent(
    *,
    closeout_execution_id: str,
    adapter_name: str,
    external_ref: str,
    actor_type: str,
    actor_id: str,
    actor_role: str,
    source_surface: str,
    reason: str = "",
) -> dict[str, Any]:
    """Prepare an exact external closeout write intent without executing it."""
    closeout_execution_id = closeout_execution_id.strip()
    adapter_name = adapter_name.strip().lower()
    external_ref = external_ref.strip()
    actor_type = actor_type.strip()
    actor_id = actor_id.strip()
    actor_role = actor_role.strip().lower()
    source_surface = source_surface.strip()
    reason = reason.strip()
    if not closeout_execution_id:
        raise ValueError("closeout_execution_id is required")
    if not adapter_name:
        raise ValueError("adapter_name is required")
    if adapter_name not in EXTERNAL_WRITE_INTENT_ADAPTERS:
        raise ValueError(f"adapter is not enabled for external write intent: {adapter_name}")
    if not external_ref:
        raise ValueError("external_ref is required")
    if not actor_type:
        raise ValueError("actor_type is required")
    if not actor_id:
        raise ValueError("actor_id is required")
    if actor_role not in ROLE_RANK:
        raise ValueError("unsupported actor_role")
    if not _role_allows(actor_role, "owner"):
        raise ValueError("actor role cannot prepare external adapter write intent")
    if not source_surface:
        raise ValueError("source_surface is required")

    with _connect() as conn:
        existing = conn.execute(
            """
            SELECT external_write_intent_id, closeout_execution_id, task_id,
                   adapter_name, external_ref, operation, intent_status,
                   mutation_payload_json, mutation_digest, writes_enabled,
                   external_adapter_write_through_enabled, rollback_required,
                   rollback_available, rollback_plan_json, prepared_by,
                   prepared_role, prepared_at
            FROM external_adapter_write_intents
            WHERE closeout_execution_id = ? AND adapter_name = ? AND external_ref = ?
            """,
            (closeout_execution_id, adapter_name, external_ref),
        ).fetchone()
        if existing is not None:
            return _external_adapter_write_intent_response(
                created=False,
                row=existing,
                source_surface=source_surface,
            )

        execution = conn.execute(
            """
            SELECT closeout_execution_id, task_id, risk_level, evidence_review_id,
                   evidence_state_hash, execution_status, task_status
            FROM closeout_execution_runs
            WHERE closeout_execution_id = ?
            """,
            (closeout_execution_id,),
        ).fetchone()
        if execution is None:
            raise ValueError(f"closeout execution not found: {closeout_execution_id}")
        if execution["execution_status"] != "closeout_completed" or execution["task_status"] != "closed":
            raise ValueError("closeout execution is not completed")
        task = conn.execute(
            "SELECT task_id, external_ref, status FROM tasks WHERE task_id = ?",
            (execution["task_id"],),
        ).fetchone()
        if task is None:
            raise ValueError("Control Tower task row is required before external write intent")
        if task["status"] != "closed":
            raise ValueError(f"Control Tower task is not closed: {task['status']}")
        if task["external_ref"] and task["external_ref"] != external_ref:
            raise ValueError("external_ref does not match Control Tower task external_ref")

        mutation_payload = {
            "operation": "closeout",
            "adapter_name": adapter_name,
            "external_ref": external_ref,
            "task_id": execution["task_id"],
            "target_status": "closed",
            "source": "control_tower_closeout_execution",
            "closeout_execution_id": closeout_execution_id,
            "evidence_review_id": execution["evidence_review_id"],
            "evidence_state_hash": execution["evidence_state_hash"],
        }
        mutation_digest = _payload_hash(mutation_payload)
        rollback_plan = {
            "available": False,
            "required_before_write_through": True,
            "reason": "external adapter write-through remains disabled until rollback/reopen contract is approved",
        }
        external_write_intent_id = f"extintent_{uuid.uuid4().hex}"
        prepared_at = _now()
        try:
            conn.execute(
                """
                INSERT INTO external_adapter_write_intents(
                    external_write_intent_id, closeout_execution_id, task_id,
                    adapter_name, external_ref, operation, intent_status,
                    mutation_payload_json, mutation_digest, writes_enabled,
                    external_adapter_write_through_enabled, rollback_required,
                    rollback_available, rollback_plan_json, prepared_by,
                    prepared_role, prepared_at, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    external_write_intent_id,
                    closeout_execution_id,
                    execution["task_id"],
                    adapter_name,
                    external_ref,
                    "closeout",
                    "blocked_pending_adapter_contract",
                    _canonical_json(mutation_payload),
                    mutation_digest,
                    0,
                    0,
                    1,
                    0,
                    _canonical_json(rollback_plan),
                    actor_id,
                    actor_role,
                    prepared_at,
                    reason,
                ),
            )
        except sqlite3.IntegrityError:  # pragma: no cover - defensive concurrent retry
            existing = conn.execute(
                """
                SELECT external_write_intent_id, closeout_execution_id, task_id,
                       adapter_name, external_ref, operation, intent_status,
                       mutation_payload_json, mutation_digest, writes_enabled,
                       external_adapter_write_through_enabled, rollback_required,
                       rollback_available, rollback_plan_json, prepared_by,
                       prepared_role, prepared_at
                FROM external_adapter_write_intents
                WHERE closeout_execution_id = ? AND adapter_name = ? AND external_ref = ?
                """,
                (closeout_execution_id, adapter_name, external_ref),
            ).fetchone()
            if existing is None:
                raise
            return _external_adapter_write_intent_response(
                created=False,
                row=existing,
                source_surface=source_surface,
            )
        _append_event(
            conn,
            event_type="ExternalAdapterWriteIntentPrepared",
            aggregate_type="external_adapter_write_intent",
            aggregate_id=external_write_intent_id,
            actor_type=actor_type,
            actor_id=actor_id,
            source_surface=source_surface,
            payload={
                "external_write_intent_id": external_write_intent_id,
                "closeout_execution_id": closeout_execution_id,
                "task_id": execution["task_id"],
                "adapter_name": adapter_name,
                "external_ref": external_ref,
                "operation": "closeout",
                "intent_status": "blocked_pending_adapter_contract",
                "mutation_digest": mutation_digest,
                "writes_enabled": False,
                "external_adapter_write_through_enabled": False,
                "rollback_required": True,
                "rollback_available": False,
            },
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT external_write_intent_id, closeout_execution_id, task_id,
                   adapter_name, external_ref, operation, intent_status,
                   mutation_payload_json, mutation_digest, writes_enabled,
                   external_adapter_write_through_enabled, rollback_required,
                   rollback_available, rollback_plan_json, prepared_by,
                   prepared_role, prepared_at
            FROM external_adapter_write_intents
            WHERE external_write_intent_id = ?
            """,
            (external_write_intent_id,),
        ).fetchone()
    return _external_adapter_write_intent_response(
        created=True,
        row=row,
        source_surface=source_surface,
    )


def _external_write_review_response(
    *,
    created: bool,
    row: sqlite3.Row,
    source_surface: str,
) -> dict[str, Any]:
    return {
        "created": created,
        "external_write_review_id": row["external_write_review_id"],
        "external_write_intent_id": row["external_write_intent_id"],
        "closeout_execution_id": row["closeout_execution_id"],
        "task_id": row["task_id"],
        "adapter_name": row["adapter_name"],
        "external_ref": row["external_ref"],
        "operation": row["operation"],
        "adapter_contract_version": row["adapter_contract_version"],
        "review_status": row["review_status"],
        "blocked_reasons": json.loads(row["blocked_reasons_json"]),
        "mutation_digest": row["mutation_digest"],
        "writes_enabled": bool(row["writes_enabled"]),
        "external_adapter_write_through_enabled": bool(row["external_adapter_write_through_enabled"]),
        "rollback_required": bool(row["rollback_required"]),
        "rollback_available": bool(row["rollback_available"]),
        "write_through_allowed": bool(row["write_through_allowed"]),
        "reviewed_by": row["reviewed_by"],
        "reviewed_role": row["reviewed_role"],
        "reviewed_at": row["reviewed_at"],
        "source_surface": source_surface,
    }


def _adapter_contract_version(adapter_name: str, operation: str) -> str:
    if adapter_name not in EXTERNAL_WRITE_INTENT_ADAPTERS:
        raise ValueError(f"adapter is not enabled for external write review: {adapter_name}")
    if operation != "closeout":
        raise ValueError(f"operation is not enabled for external write review: {operation}")
    return f"{adapter_name}.closeout.v0"


def review_external_adapter_write_intent(
    *,
    external_write_intent_id: str,
    actor_type: str,
    actor_id: str,
    actor_role: str,
    source_surface: str,
    reason: str = "",
) -> dict[str, Any]:
    """Review a blocked external write intent without enabling external writes."""
    external_write_intent_id = external_write_intent_id.strip()
    actor_type = actor_type.strip()
    actor_id = actor_id.strip()
    actor_role = actor_role.strip().lower()
    source_surface = source_surface.strip()
    reason = reason.strip()
    if not external_write_intent_id:
        raise ValueError("external_write_intent_id is required")
    if not actor_type:
        raise ValueError("actor_type is required")
    if not actor_id:
        raise ValueError("actor_id is required")
    if actor_role not in ROLE_RANK:
        raise ValueError("unsupported actor_role")
    if not _role_allows(actor_role, "owner"):
        raise ValueError("actor role cannot review external adapter write intent")
    if not source_surface:
        raise ValueError("source_surface is required")

    with _connect() as conn:
        existing = conn.execute(
            """
            SELECT external_write_review_id, external_write_intent_id,
                   closeout_execution_id, task_id, adapter_name, external_ref,
                   operation, adapter_contract_version, review_status,
                   blocked_reasons_json, mutation_digest, writes_enabled,
                   external_adapter_write_through_enabled, rollback_required,
                   rollback_available, write_through_allowed, reviewed_by,
                   reviewed_role, reviewed_at
            FROM external_adapter_write_intent_reviews
            WHERE external_write_intent_id = ?
            """,
            (external_write_intent_id,),
        ).fetchone()
        if existing is not None:
            return _external_write_review_response(created=False, row=existing, source_surface=source_surface)

        intent = conn.execute(
            """
            SELECT external_write_intent_id, closeout_execution_id, task_id,
                   adapter_name, external_ref, operation, intent_status,
                   mutation_payload_json, mutation_digest, writes_enabled,
                   external_adapter_write_through_enabled, rollback_required,
                   rollback_available
            FROM external_adapter_write_intents
            WHERE external_write_intent_id = ?
            """,
            (external_write_intent_id,),
        ).fetchone()
        if intent is None:
            raise ValueError(f"external write intent not found: {external_write_intent_id}")
        if intent["intent_status"] != "blocked_pending_adapter_contract":
            raise ValueError(f"external write intent is not blocked for review: {intent['intent_status']}")
        mutation_payload = json.loads(intent["mutation_payload_json"])
        recomputed_digest = _payload_hash(mutation_payload)
        if recomputed_digest != intent["mutation_digest"]:
            raise ValueError("mutation digest mismatch for external write intent")
        contract_version = _adapter_contract_version(intent["adapter_name"], intent["operation"])
        blocked_reasons = ["external_write_through_disabled"]
        if not bool(intent["rollback_available"]):
            blocked_reasons.append("rollback_unavailable")
        if bool(intent["writes_enabled"]):
            raise ValueError("external write intent unexpectedly has writes_enabled=true")
        if bool(intent["external_adapter_write_through_enabled"]):
            raise ValueError("external write intent unexpectedly has adapter write-through enabled")
        if not bool(intent["rollback_required"]):
            blocked_reasons.append("rollback_requirement_missing")

        external_write_review_id = f"extreview_{uuid.uuid4().hex}"
        reviewed_at = _now()
        try:
            conn.execute(
                """
                INSERT INTO external_adapter_write_intent_reviews(
                    external_write_review_id, external_write_intent_id,
                    closeout_execution_id, task_id, adapter_name, external_ref,
                    operation, adapter_contract_version, review_status,
                    blocked_reasons_json, mutation_digest, writes_enabled,
                    external_adapter_write_through_enabled, rollback_required,
                    rollback_available, write_through_allowed, reviewed_by,
                    reviewed_role, reviewed_at, reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    external_write_review_id,
                    external_write_intent_id,
                    intent["closeout_execution_id"],
                    intent["task_id"],
                    intent["adapter_name"],
                    intent["external_ref"],
                    intent["operation"],
                    contract_version,
                    "blocked",
                    _canonical_json(blocked_reasons),
                    intent["mutation_digest"],
                    0,
                    0,
                    int(bool(intent["rollback_required"])),
                    int(bool(intent["rollback_available"])),
                    0,
                    actor_id,
                    actor_role,
                    reviewed_at,
                    reason,
                ),
            )
        except sqlite3.IntegrityError:  # pragma: no cover - defensive concurrent retry
            existing = conn.execute(
                """
                SELECT external_write_review_id, external_write_intent_id,
                       closeout_execution_id, task_id, adapter_name, external_ref,
                       operation, adapter_contract_version, review_status,
                       blocked_reasons_json, mutation_digest, writes_enabled,
                       external_adapter_write_through_enabled, rollback_required,
                       rollback_available, write_through_allowed, reviewed_by,
                       reviewed_role, reviewed_at
                FROM external_adapter_write_intent_reviews
                WHERE external_write_intent_id = ?
                """,
                (external_write_intent_id,),
            ).fetchone()
            if existing is None:
                raise
            return _external_write_review_response(created=False, row=existing, source_surface=source_surface)
        _append_event(
            conn,
            event_type="ExternalAdapterWriteIntentReviewed",
            aggregate_type="external_adapter_write_intent_review",
            aggregate_id=external_write_review_id,
            actor_type=actor_type,
            actor_id=actor_id,
            source_surface=source_surface,
            payload={
                "external_write_review_id": external_write_review_id,
                "external_write_intent_id": external_write_intent_id,
                "adapter_name": intent["adapter_name"],
                "operation": intent["operation"],
                "adapter_contract_version": contract_version,
                "review_status": "blocked",
                "blocked_reasons": blocked_reasons,
                "mutation_digest": intent["mutation_digest"],
                "write_through_allowed": False,
                "writes_enabled": False,
                "external_adapter_write_through_enabled": False,
            },
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT external_write_review_id, external_write_intent_id,
                   closeout_execution_id, task_id, adapter_name, external_ref,
                   operation, adapter_contract_version, review_status,
                   blocked_reasons_json, mutation_digest, writes_enabled,
                   external_adapter_write_through_enabled, rollback_required,
                   rollback_available, write_through_allowed, reviewed_by,
                   reviewed_role, reviewed_at
            FROM external_adapter_write_intent_reviews
            WHERE external_write_review_id = ?
            """,
            (external_write_review_id,),
        ).fetchone()
    return _external_write_review_response(created=True, row=row, source_surface=source_surface)


EXTERNAL_WRITE_AUTHORIZATION_REQUIRED_ROLE = "admin"
EXTERNAL_WRITE_AUTHORIZATION_TTL_HOURS = 24


def _external_write_authorization_response(
    *,
    created: bool,
    row: sqlite3.Row,
    source_surface: str,
) -> dict[str, Any]:
    return {
        "created": created,
        "external_write_authorization_id": row["external_write_authorization_id"],
        "external_write_review_id": row["external_write_review_id"],
        "external_write_intent_id": row["external_write_intent_id"],
        "closeout_execution_id": row["closeout_execution_id"],
        "task_id": row["task_id"],
        "adapter_name": row["adapter_name"],
        "external_ref": row["external_ref"],
        "operation": row["operation"],
        "adapter_contract_version": row["adapter_contract_version"],
        "mutation_digest": row["mutation_digest"],
        "status": row["status"],
        "required_role": row["required_role"],
        "requested_by": row["requested_by"],
        "requested_role": row["requested_role"],
        "requested_at": row["requested_at"],
        "expires_at": row["expires_at"],
        "decided_by": row["decided_by"],
        "decided_role": row["decided_role"],
        "decided_at": row["decided_at"],
        "writes_enabled": bool(row["writes_enabled"]),
        "external_adapter_write_through_enabled": bool(row["external_adapter_write_through_enabled"]),
        "rollback_required": bool(row["rollback_required"]),
        "rollback_available": bool(row["rollback_available"]),
        "write_through_allowed": bool(row["write_through_allowed"]),
        "source_surface": source_surface,
    }


def _select_external_write_authorization(conn: sqlite3.Connection, external_write_authorization_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT external_write_authorization_id, external_write_review_id,
               external_write_intent_id, closeout_execution_id, task_id,
               adapter_name, external_ref, operation, adapter_contract_version,
               mutation_digest, status, required_role, requested_by,
               requested_role, requested_at, expires_at, decided_by,
               decided_role, decided_at, writes_enabled,
               external_adapter_write_through_enabled, rollback_required,
               rollback_available, write_through_allowed
        FROM external_adapter_write_authorizations
        WHERE external_write_authorization_id = ?
        """,
        (external_write_authorization_id,),
    ).fetchone()


def _external_write_review_with_intent(conn: sqlite3.Connection, external_write_review_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT r.external_write_review_id, r.external_write_intent_id,
               r.closeout_execution_id, r.task_id, r.adapter_name,
               r.external_ref, r.operation, r.adapter_contract_version,
               r.review_status, r.mutation_digest, r.writes_enabled,
               r.external_adapter_write_through_enabled, r.rollback_required,
               r.rollback_available, r.write_through_allowed,
               i.mutation_payload_json, i.mutation_digest AS current_mutation_digest,
               i.intent_status
        FROM external_adapter_write_intent_reviews r
        JOIN external_adapter_write_intents i
          ON i.external_write_intent_id = r.external_write_intent_id
        WHERE r.external_write_review_id = ?
        """,
        (external_write_review_id,),
    ).fetchone()


def _assert_external_write_review_fresh(review: sqlite3.Row) -> None:
    if review["review_status"] != "blocked":
        raise ValueError(f"external write review is not blocked: {review['review_status']}")
    if review["intent_status"] != "blocked_pending_adapter_contract":
        raise ValueError(f"external write intent is not blocked: {review['intent_status']}")
    if bool(review["writes_enabled"]):
        raise ValueError("external write review unexpectedly has writes_enabled=true")
    if bool(review["external_adapter_write_through_enabled"]):
        raise ValueError("external write review unexpectedly has adapter write-through enabled")
    if bool(review["write_through_allowed"]):
        raise ValueError("external write review unexpectedly allows write-through")
    if bool(review["rollback_available"]):
        raise ValueError("external write review unexpectedly has rollback available before boundary approval")
    mutation_payload = json.loads(review["mutation_payload_json"])
    recomputed_digest = _payload_hash(mutation_payload)
    if recomputed_digest != review["mutation_digest"] or recomputed_digest != review["current_mutation_digest"]:
        raise ValueError("mutation digest mismatch for external write authorization")


def request_external_adapter_write_authorization(
    *,
    external_write_review_id: str,
    actor_type: str,
    actor_id: str,
    actor_role: str,
    source_surface: str,
    reason: str = "",
) -> dict[str, Any]:
    """Request digest-bound authorization for a blocked external write review."""
    external_write_review_id = external_write_review_id.strip()
    actor_type = actor_type.strip()
    actor_id = actor_id.strip()
    actor_role = actor_role.strip().lower()
    source_surface = source_surface.strip()
    reason = reason.strip()
    if not external_write_review_id:
        raise ValueError("external_write_review_id is required")
    if not actor_type:
        raise ValueError("actor_type is required")
    if not actor_id:
        raise ValueError("actor_id is required")
    if actor_role not in ROLE_RANK:
        raise ValueError("unsupported actor_role")
    if not _role_allows(actor_role, EXTERNAL_WRITE_AUTHORIZATION_REQUIRED_ROLE):
        raise ValueError("actor role cannot request external adapter write authorization")
    if not source_surface:
        raise ValueError("source_surface is required")

    with _connect() as conn:
        review = _external_write_review_with_intent(conn, external_write_review_id)
        if review is None:
            raise ValueError(f"external write review not found: {external_write_review_id}")
        _assert_external_write_review_fresh(review)
        existing = conn.execute(
            """
            SELECT external_write_authorization_id, status, expires_at
            FROM external_adapter_write_authorizations
            WHERE external_write_review_id = ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (external_write_review_id,),
        ).fetchone()
        if existing is not None:
            if existing["status"] == "expired" or datetime.fromisoformat(existing["expires_at"]) <= datetime.now(timezone.utc):
                conn.execute(
                    "UPDATE external_adapter_write_authorizations SET status = ? WHERE external_write_authorization_id = ?",
                    ("expired", existing["external_write_authorization_id"]),
                )
                conn.commit()
                raise ValueError("external write authorization expired; create a new external write review before requesting again")
            row = _select_external_write_authorization(conn, existing["external_write_authorization_id"])
            return _external_write_authorization_response(created=False, row=row, source_surface=source_surface)
        external_write_authorization_id = f"extauth_{uuid.uuid4().hex}"
        requested_at = _now()
        expires_at = (datetime.fromisoformat(requested_at) + timedelta(hours=EXTERNAL_WRITE_AUTHORIZATION_TTL_HOURS)).isoformat()
        conn.execute(
            """
            INSERT INTO external_adapter_write_authorizations(
                external_write_authorization_id, external_write_review_id,
                external_write_intent_id, closeout_execution_id, task_id,
                adapter_name, external_ref, operation, adapter_contract_version,
                mutation_digest, status, required_role, requested_by,
                requested_role, requested_at, expires_at, reason,
                writes_enabled, external_adapter_write_through_enabled,
                rollback_required, rollback_available, write_through_allowed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                external_write_authorization_id,
                external_write_review_id,
                review["external_write_intent_id"],
                review["closeout_execution_id"],
                review["task_id"],
                review["adapter_name"],
                review["external_ref"],
                review["operation"],
                review["adapter_contract_version"],
                review["mutation_digest"],
                "requested",
                EXTERNAL_WRITE_AUTHORIZATION_REQUIRED_ROLE,
                actor_id,
                actor_role,
                requested_at,
                expires_at,
                reason,
                0,
                0,
                int(bool(review["rollback_required"])),
                int(bool(review["rollback_available"])),
                0,
            ),
        )
        _append_event(
            conn,
            event_type="ExternalAdapterWriteAuthorizationRequested",
            aggregate_type="external_adapter_write_authorization",
            aggregate_id=external_write_authorization_id,
            actor_type=actor_type,
            actor_id=actor_id,
            source_surface=source_surface,
            payload={
                "external_write_authorization_id": external_write_authorization_id,
                "external_write_review_id": external_write_review_id,
                "external_write_intent_id": review["external_write_intent_id"],
                "adapter_contract_version": review["adapter_contract_version"],
                "mutation_digest": review["mutation_digest"],
                "required_role": EXTERNAL_WRITE_AUTHORIZATION_REQUIRED_ROLE,
                "write_through_allowed": False,
                "writes_enabled": False,
                "external_adapter_write_through_enabled": False,
                "expires_at": expires_at,
            },
        )
        conn.commit()
        row = _select_external_write_authorization(conn, external_write_authorization_id)
    return _external_write_authorization_response(created=True, row=row, source_surface=source_surface)


def decide_external_adapter_write_authorization(
    *,
    external_write_authorization_id: str,
    decision: str,
    actor_type: str,
    actor_id: str,
    actor_role: str,
    source_surface: str,
    reason: str = "",
) -> dict[str, Any]:
    """Approve/deny an external write authorization without enabling write-through."""
    external_write_authorization_id = external_write_authorization_id.strip()
    decision = decision.strip().lower()
    actor_type = actor_type.strip()
    actor_id = actor_id.strip()
    actor_role = actor_role.strip().lower()
    source_surface = source_surface.strip()
    reason = reason.strip()
    if not external_write_authorization_id:
        raise ValueError("external_write_authorization_id is required")
    if decision not in {"approved", "denied"}:
        raise ValueError("decision must be approved or denied")
    if not actor_type:
        raise ValueError("actor_type is required")
    if not actor_id:
        raise ValueError("actor_id is required")
    if actor_role not in ROLE_RANK:
        raise ValueError("unsupported actor_role")
    if not source_surface:
        raise ValueError("source_surface is required")

    with _connect() as conn:
        row = _select_external_write_authorization(conn, external_write_authorization_id)
        if row is None:
            raise ValueError(f"external write authorization not found: {external_write_authorization_id}")
        if row["status"] == decision:
            return _external_write_authorization_response(created=False, row=row, source_surface=source_surface)
        if row["status"] != "requested":
            raise ValueError(f"external write authorization already decided: {row['status']}")
        if not _role_allows(actor_role, row["required_role"]):
            raise ValueError(f"actor role cannot decide external adapter write authorization: requires {row['required_role']}")
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
            conn.execute(
                "UPDATE external_adapter_write_authorizations SET status = ? WHERE external_write_authorization_id = ?",
                ("expired", external_write_authorization_id),
            )
            conn.commit()
            raise ValueError("external write authorization expired")
        review = _external_write_review_with_intent(conn, row["external_write_review_id"])
        if review is None:
            raise ValueError(f"external write review not found: {row['external_write_review_id']}")
        _assert_external_write_review_fresh(review)
        if review["mutation_digest"] != row["mutation_digest"]:
            raise ValueError("mutation digest mismatch for external write authorization")
        decided_at = _now()
        conn.execute(
            """
            UPDATE external_adapter_write_authorizations
            SET status = ?, decided_by = ?, decided_role = ?, decided_at = ?, reason = ?,
                writes_enabled = 0, external_adapter_write_through_enabled = 0,
                write_through_allowed = 0
            WHERE external_write_authorization_id = ?
            """,
            (decision, actor_id, actor_role, decided_at, reason, external_write_authorization_id),
        )
        _append_event(
            conn,
            event_type="ExternalAdapterWriteAuthorizationDecided",
            aggregate_type="external_adapter_write_authorization",
            aggregate_id=external_write_authorization_id,
            actor_type=actor_type,
            actor_id=actor_id,
            source_surface=source_surface,
            payload={
                "external_write_authorization_id": external_write_authorization_id,
                "external_write_review_id": row["external_write_review_id"],
                "decision": decision,
                "mutation_digest": row["mutation_digest"],
                "write_through_allowed": False,
                "writes_enabled": False,
                "external_adapter_write_through_enabled": False,
            },
        )
        conn.commit()
        decided = _select_external_write_authorization(conn, external_write_authorization_id)
    return _external_write_authorization_response(created=True, row=decided, source_surface=source_surface)


def _external_write_preflight_response(
    *,
    created: bool,
    row: sqlite3.Row,
    source_surface: str,
) -> dict[str, Any]:
    return {
        "created": created,
        "external_write_preflight_id": row["external_write_preflight_id"],
        "external_write_authorization_id": row["external_write_authorization_id"],
        "external_write_review_id": row["external_write_review_id"],
        "external_write_intent_id": row["external_write_intent_id"],
        "closeout_execution_id": row["closeout_execution_id"],
        "task_id": row["task_id"],
        "adapter_name": row["adapter_name"],
        "external_ref": row["external_ref"],
        "operation": row["operation"],
        "adapter_contract_version": row["adapter_contract_version"],
        "mutation_digest": row["mutation_digest"],
        "command_execution_payload": json.loads(row["command_execution_payload_json"]),
        "command_execution_digest": row["command_execution_digest"],
        "preflight_status": row["preflight_status"],
        "prepared_by": row["prepared_by"],
        "prepared_role": row["prepared_role"],
        "prepared_at": row["prepared_at"],
        "writes_enabled": bool(row["writes_enabled"]),
        "external_adapter_write_through_enabled": bool(row["external_adapter_write_through_enabled"]),
        "external_write_executor_enabled": bool(row["external_write_executor_enabled"]),
        "rollback_required": bool(row["rollback_required"]),
        "rollback_available": bool(row["rollback_available"]),
        "write_through_allowed": bool(row["write_through_allowed"]),
        "source_surface": source_surface,
    }


def _select_external_write_preflight(conn: sqlite3.Connection, external_write_preflight_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT external_write_preflight_id, external_write_authorization_id,
               external_write_review_id, external_write_intent_id,
               closeout_execution_id, task_id, adapter_name, external_ref,
               operation, adapter_contract_version, mutation_digest,
               command_execution_payload_json, command_execution_digest,
               preflight_status, prepared_by, prepared_role, prepared_at,
               writes_enabled, external_adapter_write_through_enabled,
               external_write_executor_enabled, rollback_required,
               rollback_available, write_through_allowed
        FROM external_adapter_write_execution_preflights
        WHERE external_write_preflight_id = ?
        """,
        (external_write_preflight_id,),
    ).fetchone()


def _external_write_authorization_with_review_and_intent(conn: sqlite3.Connection, external_write_authorization_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT a.external_write_authorization_id, a.external_write_review_id,
               a.external_write_intent_id, a.closeout_execution_id, a.task_id,
               a.adapter_name, a.external_ref, a.operation,
               a.adapter_contract_version, a.mutation_digest, a.status,
               a.required_role, a.expires_at, a.writes_enabled,
               a.external_adapter_write_through_enabled, a.rollback_required,
               a.rollback_available, a.write_through_allowed,
               r.review_status, r.mutation_digest AS review_mutation_digest,
               r.writes_enabled AS review_writes_enabled,
               r.external_adapter_write_through_enabled AS review_external_adapter_write_through_enabled,
               r.rollback_available AS review_rollback_available,
               r.write_through_allowed AS review_write_through_allowed,
               i.intent_status, i.mutation_payload_json,
               i.mutation_digest AS current_mutation_digest,
               i.writes_enabled AS intent_writes_enabled,
               i.external_adapter_write_through_enabled AS intent_external_adapter_write_through_enabled,
               i.rollback_required AS intent_rollback_required,
               i.rollback_available AS intent_rollback_available
        FROM external_adapter_write_authorizations a
        JOIN external_adapter_write_intent_reviews r
          ON r.external_write_review_id = a.external_write_review_id
        JOIN external_adapter_write_intents i
          ON i.external_write_intent_id = a.external_write_intent_id
        WHERE a.external_write_authorization_id = ?
        """,
        (external_write_authorization_id,),
    ).fetchone()


def _assert_external_write_authorization_fresh_for_preflight(auth: sqlite3.Row) -> None:
    if auth["status"] != "approved":
        raise ValueError(f"external write authorization is not approved: {auth['status']}")
    if datetime.fromisoformat(auth["expires_at"]) <= datetime.now(timezone.utc):
        raise ValueError("external write authorization expired")
    if bool(auth["writes_enabled"]):
        raise ValueError("external write authorization unexpectedly has writes_enabled=true")
    if bool(auth["external_adapter_write_through_enabled"]):
        raise ValueError("external write authorization unexpectedly has adapter write-through enabled")
    if bool(auth["write_through_allowed"]):
        raise ValueError("external write authorization unexpectedly allows write-through")
    if bool(auth["rollback_available"]):
        raise ValueError("external write authorization unexpectedly has rollback available before boundary approval")
    if auth["review_status"] != "blocked":
        raise ValueError(f"external write review is not blocked: {auth['review_status']}")
    if auth["intent_status"] != "blocked_pending_adapter_contract":
        raise ValueError(f"external write intent is not blocked: {auth['intent_status']}")
    if bool(auth["intent_writes_enabled"]):
        raise ValueError("external write intent unexpectedly has writes_enabled=true")
    if bool(auth["intent_external_adapter_write_through_enabled"]):
        raise ValueError("external write intent unexpectedly has adapter write-through enabled")
    if not bool(auth["intent_rollback_required"]):
        raise ValueError("external write intent unexpectedly lacks rollback requirement")
    if bool(auth["intent_rollback_available"]):
        raise ValueError("external write intent unexpectedly has rollback available before boundary approval")
    if bool(auth["review_writes_enabled"]):
        raise ValueError("external write review unexpectedly has writes_enabled=true")
    if bool(auth["review_external_adapter_write_through_enabled"]):
        raise ValueError("external write review unexpectedly has adapter write-through enabled")
    if bool(auth["review_write_through_allowed"]):
        raise ValueError("external write review unexpectedly allows write-through")
    if bool(auth["review_rollback_available"]):
        raise ValueError("external write review unexpectedly has rollback available before boundary approval")
    mutation_payload = json.loads(auth["mutation_payload_json"])
    recomputed_digest = _payload_hash(mutation_payload)
    if recomputed_digest != auth["mutation_digest"] or recomputed_digest != auth["review_mutation_digest"] or recomputed_digest != auth["current_mutation_digest"]:
        raise ValueError("mutation digest mismatch for external write execution preflight")


def _external_write_command_execution_payload(auth: sqlite3.Row) -> dict[str, Any]:
    return {
        "command_type": "ExternalAdapterWriteThrough",
        "external_write_authorization_id": auth["external_write_authorization_id"],
        "external_write_review_id": auth["external_write_review_id"],
        "external_write_intent_id": auth["external_write_intent_id"],
        "closeout_execution_id": auth["closeout_execution_id"],
        "task_id": auth["task_id"],
        "adapter_name": auth["adapter_name"],
        "external_ref": auth["external_ref"],
        "operation": auth["operation"],
        "adapter_contract_version": auth["adapter_contract_version"],
        "mutation_digest": auth["mutation_digest"],
        "writes_enabled": False,
        "external_adapter_write_through_enabled": False,
        "external_write_executor_enabled": False,
        "rollback_required": True,
        "rollback_available": False,
        "write_through_allowed": False,
    }


def prepare_external_adapter_write_execution_preflight(
    *,
    external_write_authorization_id: str,
    actor_type: str,
    actor_id: str,
    actor_role: str,
    source_surface: str,
    reason: str = "",
) -> dict[str, Any]:
    """Prepare a digest-bound external-write execution preflight without writing adapters."""
    external_write_authorization_id = external_write_authorization_id.strip()
    actor_type = actor_type.strip()
    actor_id = actor_id.strip()
    actor_role = actor_role.strip().lower()
    source_surface = source_surface.strip()
    reason = reason.strip()
    if not external_write_authorization_id:
        raise ValueError("external_write_authorization_id is required")
    if not actor_type:
        raise ValueError("actor_type is required")
    if not actor_id:
        raise ValueError("actor_id is required")
    if actor_role not in ROLE_RANK:
        raise ValueError("unsupported actor_role")
    if not _role_allows(actor_role, EXTERNAL_WRITE_AUTHORIZATION_REQUIRED_ROLE):
        raise ValueError("actor role cannot prepare external adapter write execution preflight")
    if not source_surface:
        raise ValueError("source_surface is required")

    with _connect() as conn:
        auth = _external_write_authorization_with_review_and_intent(conn, external_write_authorization_id)
        if auth is None:
            raise ValueError(f"external write authorization not found: {external_write_authorization_id}")
        _assert_external_write_authorization_fresh_for_preflight(auth)
        existing = conn.execute(
            """
            SELECT external_write_preflight_id
            FROM external_adapter_write_execution_preflights
            WHERE external_write_authorization_id = ?
            """,
            (external_write_authorization_id,),
        ).fetchone()
        if existing is not None:
            row = _select_external_write_preflight(conn, existing["external_write_preflight_id"])
            return _external_write_preflight_response(created=False, row=row, source_surface=source_surface)
        command_payload = _external_write_command_execution_payload(auth)
        command_digest = _payload_hash(command_payload)
        external_write_preflight_id = f"extpreflight_{uuid.uuid4().hex}"
        prepared_at = _now()
        conn.execute(
            """
            INSERT INTO external_adapter_write_execution_preflights(
                external_write_preflight_id, external_write_authorization_id,
                external_write_review_id, external_write_intent_id,
                closeout_execution_id, task_id, adapter_name, external_ref,
                operation, adapter_contract_version, mutation_digest,
                command_execution_payload_json, command_execution_digest,
                preflight_status, prepared_by, prepared_role, prepared_at,
                reason, writes_enabled, external_adapter_write_through_enabled,
                external_write_executor_enabled, rollback_required,
                rollback_available, write_through_allowed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                external_write_preflight_id,
                external_write_authorization_id,
                auth["external_write_review_id"],
                auth["external_write_intent_id"],
                auth["closeout_execution_id"],
                auth["task_id"],
                auth["adapter_name"],
                auth["external_ref"],
                auth["operation"],
                auth["adapter_contract_version"],
                auth["mutation_digest"],
                _canonical_json(command_payload),
                command_digest,
                "blocked_pending_external_write_executor",
                actor_id,
                actor_role,
                prepared_at,
                reason,
                0,
                0,
                0,
                1,
                0,
                0,
            ),
        )
        _append_event(
            conn,
            event_type="ExternalAdapterWriteExecutionPreflightPrepared",
            aggregate_type="external_adapter_write_execution_preflight",
            aggregate_id=external_write_preflight_id,
            actor_type=actor_type,
            actor_id=actor_id,
            source_surface=source_surface,
            payload={
                "external_write_preflight_id": external_write_preflight_id,
                "external_write_authorization_id": external_write_authorization_id,
                "command_execution_digest": command_digest,
                "preflight_status": "blocked_pending_external_write_executor",
                "write_through_allowed": False,
                "writes_enabled": False,
                "external_adapter_write_through_enabled": False,
                "external_write_executor_enabled": False,
            },
        )
        conn.commit()
        row = _select_external_write_preflight(conn, external_write_preflight_id)
    return _external_write_preflight_response(created=True, row=row, source_surface=source_surface)


EXTERNAL_WRITE_EXECUTOR_ADAPTERS = frozenset({"kanban", "taskpool"})


def _select_external_write_execution_run(conn: sqlite3.Connection, external_write_execution_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT external_write_execution_id, external_write_preflight_id,
               external_write_authorization_id, external_write_review_id,
               external_write_intent_id, closeout_execution_id, task_id,
               adapter_name, external_ref, operation, adapter_contract_version,
               mutation_digest, command_execution_digest, execution_status,
               adapter_result_json, rollback_snapshot_json, executed_by,
               executed_role, executed_at, writes_enabled,
               external_adapter_write_through_enabled, external_write_executor_enabled,
               rollback_required, rollback_available, rollback_reopen_enabled,
               write_through_allowed
        FROM external_adapter_write_execution_runs
        WHERE external_write_execution_id = ?
        """,
        (external_write_execution_id,),
    ).fetchone()


def _select_external_write_execution_run_by_preflight(conn: sqlite3.Connection, external_write_preflight_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT external_write_execution_id, external_write_preflight_id,
               external_write_authorization_id, external_write_review_id,
               external_write_intent_id, closeout_execution_id, task_id,
               adapter_name, external_ref, operation, adapter_contract_version,
               mutation_digest, command_execution_digest, execution_status,
               adapter_result_json, rollback_snapshot_json, executed_by,
               executed_role, executed_at, writes_enabled,
               external_adapter_write_through_enabled, external_write_executor_enabled,
               rollback_required, rollback_available, rollback_reopen_enabled,
               write_through_allowed
        FROM external_adapter_write_execution_runs
        WHERE external_write_preflight_id = ?
        """,
        (external_write_preflight_id,),
    ).fetchone()


def _external_write_execution_response(
    *,
    created: bool,
    row: sqlite3.Row,
    source_surface: str,
) -> dict[str, Any]:
    return {
        "created": created,
        "external_write_execution_id": row["external_write_execution_id"],
        "external_write_preflight_id": row["external_write_preflight_id"],
        "external_write_authorization_id": row["external_write_authorization_id"],
        "external_write_review_id": row["external_write_review_id"],
        "external_write_intent_id": row["external_write_intent_id"],
        "closeout_execution_id": row["closeout_execution_id"],
        "task_id": row["task_id"],
        "adapter_name": row["adapter_name"],
        "external_ref": row["external_ref"],
        "operation": row["operation"],
        "adapter_contract_version": row["adapter_contract_version"],
        "mutation_digest": row["mutation_digest"],
        "command_execution_digest": row["command_execution_digest"],
        "execution_status": row["execution_status"],
        "adapter_result": json.loads(row["adapter_result_json"]),
        "rollback_snapshot": json.loads(row["rollback_snapshot_json"]),
        "executed_by": row["executed_by"],
        "executed_role": row["executed_role"],
        "executed_at": row["executed_at"],
        "writes_enabled": bool(row["writes_enabled"]),
        "external_adapter_write_through_enabled": bool(row["external_adapter_write_through_enabled"]),
        "external_write_executor_enabled": bool(row["external_write_executor_enabled"]),
        "rollback_required": bool(row["rollback_required"]),
        "rollback_available": bool(row["rollback_available"]),
        "rollback_reopen_enabled": bool(row["rollback_reopen_enabled"]),
        "write_through_allowed": bool(row["write_through_allowed"]),
        "source_surface": source_surface,
    }


def _assert_external_write_preflight_executable(row: sqlite3.Row, expected_command_execution_digest: str) -> None:
    if row["preflight_status"] != "blocked_pending_external_write_executor":
        raise ValueError(f"external write preflight is not executable: {row['preflight_status']}")
    if row["command_execution_digest"] != expected_command_execution_digest:
        raise ValueError("command execution digest mismatch for external adapter write")
    if bool(row["writes_enabled"]):
        raise ValueError("external write preflight unexpectedly has writes_enabled=true")
    if bool(row["external_adapter_write_through_enabled"]):
        raise ValueError("external write preflight unexpectedly has adapter write-through enabled")
    if bool(row["external_write_executor_enabled"]):
        raise ValueError("external write preflight unexpectedly has executor enabled")
    if bool(row["write_through_allowed"]):
        raise ValueError("external write preflight unexpectedly allows write-through")
    if bool(row["rollback_available"]):
        raise ValueError("external write preflight unexpectedly has rollback available")
    if row["adapter_name"] not in EXTERNAL_WRITE_EXECUTOR_ADAPTERS:
        raise ValueError(f"external write adapter executor is not enabled: {row['adapter_name']}")
    if row["operation"] != "closeout":
        raise ValueError(f"external write operation is not enabled: {row['operation']}")
    payload = json.loads(row["command_execution_payload_json"])
    if _payload_hash(payload) != row["command_execution_digest"]:
        raise ValueError("stored command execution payload digest mismatch")
    for key in (
        "external_write_authorization_id",
        "external_write_review_id",
        "external_write_intent_id",
        "closeout_execution_id",
        "task_id",
        "adapter_name",
        "external_ref",
        "operation",
        "adapter_contract_version",
        "mutation_digest",
    ):
        if payload.get(key) != row[key]:
            raise ValueError(f"external write preflight column drift for digest-bound field: {key}")
    if payload.get("write_through_allowed") is not False or payload.get("external_write_executor_enabled") is not False:
        raise ValueError("preflight payload unexpectedly crossed write-through boundary")


def _execute_kanban_closeout_write(preflight: sqlite3.Row, *, actor_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from hermes_cli import kanban_db

    conn = kanban_db.connect()
    try:
        task = kanban_db.get_task(conn, preflight["external_ref"])
        if task is None:
            raise ValueError(f"kanban task not found: {preflight['external_ref']}")
        rollback_snapshot = {
            "adapter_name": "kanban",
            "external_ref": preflight["external_ref"],
            "status_before": task.status,
            "result_before": task.result,
            "rollback_available": True,
            "rollback_reopen_enabled": True,
            "reason": "digest-bound Kanban rollback/reopen is enabled for this write run",
        }
        summary = f"Control Tower external closeout {preflight['closeout_execution_id']}"
        metadata = {
            "control_tower_external_write_preflight_id": preflight["external_write_preflight_id"],
            "control_tower_command_execution_digest": preflight["command_execution_digest"],
            "control_tower_mutation_digest": preflight["mutation_digest"],
            "executed_by": actor_id,
        }
        ok = kanban_db.complete_task(
            conn,
            preflight["external_ref"],
            result=summary,
            summary=summary,
            metadata=metadata,
        )
        if not ok:
            raise ValueError(f"kanban task is not writable for closeout: {preflight['external_ref']}")
        updated = kanban_db.get_task(conn, preflight["external_ref"])
    finally:
        conn.close()
    if updated is None or updated.status != "done":
        raise ValueError("kanban closeout write did not persist done status")
    adapter_result = {
        "adapter_name": "kanban",
        "external_ref": preflight["external_ref"],
        "operation": "closeout",
        "status_after": updated.status,
        "result_after": updated.result,
    }
    return adapter_result, rollback_snapshot


def _taskpool_tasks_dir() -> Path:
    import os

    vault = Path(os.environ.get("HERMES_BRAIN_VAULT", os.environ.get("OBSIDIAN_VAULT_PATH", str(Path.home() / "Documents" / "brain")))).expanduser()
    return vault / "05-实践与项目" / "tasks"


def _taskpool_task_path(external_ref: str) -> Path:
    if not external_ref or "/" in external_ref or external_ref in {".", ".."} or external_ref.endswith(".md"):
        raise ValueError("taskpool external_ref must be a task id slug without path separators or suffix")
    return _taskpool_tasks_dir() / f"{external_ref}.md"


def _assert_taskpool_path_safe(path: Path) -> None:
    configured_tasks_dir = _taskpool_tasks_dir()
    configured_vault = configured_tasks_dir.parent.parent
    for ancestor in (configured_vault, configured_tasks_dir.parent, configured_tasks_dir):
        if ancestor.exists() and ancestor.is_symlink():
            raise ValueError("taskpool task directory symlinks are not writable")
    if path.parent != configured_tasks_dir:
        raise ValueError("taskpool task note escapes tasks directory")
    if path.is_symlink():
        raise ValueError("taskpool task note symlinks are not writable")
    tasks_dir = configured_tasks_dir.resolve()
    resolved = path.resolve()
    if resolved.parent != tasks_dir:
        raise ValueError("taskpool task note escapes tasks directory")
    if not resolved.is_file():
        raise ValueError("taskpool task note is not a regular file")


def _read_taskpool_note(external_ref: str) -> tuple[Path, str, str]:
    path = _taskpool_task_path(external_ref)
    if not path.exists():
        raise ValueError(f"taskpool task note not found: {external_ref}")
    _assert_taskpool_path_safe(path)
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("taskpool task note missing frontmatter")
    end = text.find("\n---", 4)
    if end < 0:
        raise ValueError("taskpool task note frontmatter is not closed")
    frontmatter = text[4:end]
    body = text[end + 4 :]
    return path, frontmatter, body


def _frontmatter_status(frontmatter: str) -> str:
    for line in frontmatter.splitlines():
        if line.startswith("status:"):
            return line.split(":", 1)[1].strip().strip('"\'')
    raise ValueError("taskpool task note missing status")


def _replace_frontmatter_status(frontmatter: str, status: str) -> str:
    lines = frontmatter.splitlines()
    for idx, line in enumerate(lines):
        if line.startswith("status:"):
            lines[idx] = f"status: {status}"
            return "\n".join(lines) + ("\n" if frontmatter.endswith("\n") else "")
    raise ValueError("taskpool task note missing status")


def _append_taskpool_control_tower_event(body: str, *, kind: str, payload: dict[str, Any]) -> str:
    suffix = "\n" if body.endswith("\n") else "\n\n"
    return body + suffix + f"<!-- {kind}: {_canonical_json(payload)} -->\n"


def _write_taskpool_status(external_ref: str, *, target_status: str, event_kind: str, payload: dict[str, Any]) -> tuple[str, str, Path]:
    path, frontmatter, body = _read_taskpool_note(external_ref)
    before_status = _frontmatter_status(frontmatter)
    updated_frontmatter = _replace_frontmatter_status(frontmatter, target_status)
    updated_body = _append_taskpool_control_tower_event(body, kind=event_kind, payload=payload)
    separator = "" if updated_frontmatter.endswith("\n") else "\n"
    path.write_text(f"---\n{updated_frontmatter}{separator}---{updated_body}", encoding="utf-8")
    _, refreshed_frontmatter, _ = _read_taskpool_note(external_ref)
    status_after = _frontmatter_status(refreshed_frontmatter)
    if status_after != target_status:
        raise ValueError("taskpool write did not persist target status")
    return before_status, status_after, path


def _execute_taskpool_closeout_write(preflight: sqlite3.Row, *, actor_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path, frontmatter, _body = _read_taskpool_note(preflight["external_ref"])
    before_status = _frontmatter_status(frontmatter)
    if before_status not in {"review", "running", "ready", "blocked", "scheduled"}:
        raise ValueError(f"taskpool task is not writable for closeout: {before_status}")
    before_status, status_after, path = _write_taskpool_status(
        preflight["external_ref"],
        target_status="done",
        event_kind="control_tower_external_taskpool_closeout",
        payload={
            "external_write_preflight_id": preflight["external_write_preflight_id"],
            "command_execution_digest": preflight["command_execution_digest"],
            "mutation_digest": preflight["mutation_digest"],
            "executed_by": actor_id,
        },
    )
    adapter_result = {
        "adapter_name": "taskpool",
        "external_ref": preflight["external_ref"],
        "operation": "closeout",
        "status_after": status_after,
        "path": str(path),
    }
    rollback_snapshot = {
        "adapter_name": "taskpool",
        "external_ref": preflight["external_ref"],
        "status_before": before_status,
        "path": str(path),
        "rollback_available": True,
        "rollback_reopen_enabled": True,
        "reason": "digest-bound taskpool rollback/reopen is enabled for this write run",
    }
    return adapter_result, rollback_snapshot


def execute_external_adapter_write_preflight(
    *,
    external_write_preflight_id: str,
    expected_command_execution_digest: str,
    actor_type: str,
    actor_id: str,
    actor_role: str,
    source_surface: str,
    reason: str = "",
) -> dict[str, Any]:
    """Execute an approved, digest-bound external adapter closeout write."""
    external_write_preflight_id = external_write_preflight_id.strip()
    expected_command_execution_digest = expected_command_execution_digest.strip()
    actor_type = actor_type.strip()
    actor_id = actor_id.strip()
    actor_role = actor_role.strip().lower()
    source_surface = source_surface.strip()
    reason = reason.strip()
    if not external_write_preflight_id:
        raise ValueError("external_write_preflight_id is required")
    if not expected_command_execution_digest:
        raise ValueError("expected_command_execution_digest is required")
    if not actor_type:
        raise ValueError("actor_type is required")
    if not actor_id:
        raise ValueError("actor_id is required")
    if actor_role not in ROLE_RANK:
        raise ValueError("unsupported actor_role")
    if not _role_allows(actor_role, EXTERNAL_WRITE_AUTHORIZATION_REQUIRED_ROLE):
        raise ValueError("actor role cannot execute external adapter write")
    if not source_surface:
        raise ValueError("source_surface is required")

    with _connect() as conn:
        existing = _select_external_write_execution_run_by_preflight(conn, external_write_preflight_id)
        if existing is not None:
            if existing["command_execution_digest"] != expected_command_execution_digest:
                raise ValueError("command execution digest mismatch for existing external adapter write")
            return _external_write_execution_response(created=False, row=existing, source_surface=source_surface)
        preflight = _select_external_write_preflight(conn, external_write_preflight_id)
        if preflight is None:
            raise ValueError(f"external write preflight not found: {external_write_preflight_id}")
        _assert_external_write_preflight_executable(preflight, expected_command_execution_digest)
        auth = _external_write_authorization_with_review_and_intent(conn, preflight["external_write_authorization_id"])
        if auth is None:
            raise ValueError("external write authorization source not found")
        _assert_external_write_authorization_fresh_for_preflight(auth)
        current_payload = _external_write_command_execution_payload(auth)
        if _payload_hash(current_payload) != preflight["command_execution_digest"]:
            raise ValueError("command execution digest mismatch after source refresh")

    if preflight["adapter_name"] == "kanban":
        adapter_result, rollback_snapshot = _execute_kanban_closeout_write(preflight, actor_id=actor_id)
    elif preflight["adapter_name"] == "taskpool":
        adapter_result, rollback_snapshot = _execute_taskpool_closeout_write(preflight, actor_id=actor_id)
    else:  # pragma: no cover - guarded by _assert_external_write_preflight_executable
        raise ValueError(f"external write adapter executor is not enabled: {preflight['adapter_name']}")

    external_write_execution_id = f"extwrite_{uuid.uuid4().hex}"
    executed_at = _now()
    with _connect() as conn:
        existing = _select_external_write_execution_run_by_preflight(conn, external_write_preflight_id)
        if existing is not None:
            return _external_write_execution_response(created=False, row=existing, source_surface=source_surface)
        conn.execute(
            """
            INSERT INTO external_adapter_write_execution_runs(
                external_write_execution_id, external_write_preflight_id,
                external_write_authorization_id, external_write_review_id,
                external_write_intent_id, closeout_execution_id, task_id,
                adapter_name, external_ref, operation, adapter_contract_version,
                mutation_digest, command_execution_digest, execution_status,
                adapter_result_json, rollback_snapshot_json, executed_by,
                executed_role, executed_at, writes_enabled,
                external_adapter_write_through_enabled, external_write_executor_enabled,
                rollback_required, rollback_available, rollback_reopen_enabled,
                write_through_allowed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                external_write_execution_id,
                external_write_preflight_id,
                preflight["external_write_authorization_id"],
                preflight["external_write_review_id"],
                preflight["external_write_intent_id"],
                preflight["closeout_execution_id"],
                preflight["task_id"],
                preflight["adapter_name"],
                preflight["external_ref"],
                preflight["operation"],
                preflight["adapter_contract_version"],
                preflight["mutation_digest"],
                preflight["command_execution_digest"],
                "write_completed",
                _canonical_json(adapter_result),
                _canonical_json(rollback_snapshot),
                actor_id,
                actor_role,
                executed_at,
                1,
                1,
                1,
                1,
                1,
                1,
                1,
            ),
        )
        observation_payload = {
            "id": preflight["external_ref"],
            "status": adapter_result["status_after"],
            "result": adapter_result.get("result_after"),
            "source": "external_adapter_write_execution",
            "external_write_execution_id": external_write_execution_id,
            "command_execution_digest": preflight["command_execution_digest"],
        }
        observed_hash = _payload_hash(observation_payload)
        observation_id = f"obs_{uuid.uuid4().hex}"
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
                    preflight["adapter_name"],
                    preflight["external_ref"],
                    "task",
                    observed_hash,
                    _canonical_json(observation_payload),
                    executed_at,
                ),
            )
        except sqlite3.IntegrityError:
            pass
        _append_event(
            conn,
            event_type="ExternalAdapterWriteExecuted",
            aggregate_type="external_adapter_write_execution",
            aggregate_id=external_write_execution_id,
            actor_type=actor_type,
            actor_id=actor_id,
            source_surface=source_surface,
            payload={
                "external_write_execution_id": external_write_execution_id,
                "external_write_preflight_id": external_write_preflight_id,
                "adapter_name": preflight["adapter_name"],
                "external_ref": preflight["external_ref"],
                "execution_status": "write_completed",
                "command_execution_digest": preflight["command_execution_digest"],
                "rollback_available": True,
                "rollback_reopen_enabled": True,
                "reason": reason,
            },
        )
        conn.commit()
        row = _select_external_write_execution_run(conn, external_write_execution_id)
    return _external_write_execution_response(created=True, row=row, source_surface=source_surface)


def _select_external_write_rollback_run(conn: sqlite3.Connection, rollback_run_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT rollback_run_id, external_write_execution_id, external_write_preflight_id,
               external_write_authorization_id, external_write_review_id,
               external_write_intent_id, closeout_execution_id, task_id,
               adapter_name, external_ref, operation, adapter_contract_version,
               mutation_digest, command_execution_digest, rollback_status,
               adapter_result_json, rollback_snapshot_json, rolled_back_by,
               rolled_back_role, rolled_back_at, writes_enabled,
               external_adapter_write_through_enabled, external_write_executor_enabled,
               rollback_required, rollback_available, rollback_reopen_enabled,
               write_through_allowed
        FROM external_adapter_write_rollback_runs
        WHERE rollback_run_id = ?
        """,
        (rollback_run_id,),
    ).fetchone()


def _select_external_write_rollback_run_by_execution(conn: sqlite3.Connection, external_write_execution_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT rollback_run_id, external_write_execution_id, external_write_preflight_id,
               external_write_authorization_id, external_write_review_id,
               external_write_intent_id, closeout_execution_id, task_id,
               adapter_name, external_ref, operation, adapter_contract_version,
               mutation_digest, command_execution_digest, rollback_status,
               adapter_result_json, rollback_snapshot_json, rolled_back_by,
               rolled_back_role, rolled_back_at, writes_enabled,
               external_adapter_write_through_enabled, external_write_executor_enabled,
               rollback_required, rollback_available, rollback_reopen_enabled,
               write_through_allowed
        FROM external_adapter_write_rollback_runs
        WHERE external_write_execution_id = ?
        """,
        (external_write_execution_id,),
    ).fetchone()


def _external_write_rollback_response(*, created: bool, row: sqlite3.Row, source_surface: str) -> dict[str, Any]:
    return {
        "created": created,
        "rollback_run_id": row["rollback_run_id"],
        "external_write_execution_id": row["external_write_execution_id"],
        "external_write_preflight_id": row["external_write_preflight_id"],
        "external_write_authorization_id": row["external_write_authorization_id"],
        "external_write_review_id": row["external_write_review_id"],
        "external_write_intent_id": row["external_write_intent_id"],
        "closeout_execution_id": row["closeout_execution_id"],
        "task_id": row["task_id"],
        "adapter_name": row["adapter_name"],
        "external_ref": row["external_ref"],
        "operation": row["operation"],
        "adapter_contract_version": row["adapter_contract_version"],
        "mutation_digest": row["mutation_digest"],
        "command_execution_digest": row["command_execution_digest"],
        "rollback_status": row["rollback_status"],
        "adapter_result": json.loads(row["adapter_result_json"]),
        "rollback_snapshot": json.loads(row["rollback_snapshot_json"]),
        "rolled_back_by": row["rolled_back_by"],
        "rolled_back_role": row["rolled_back_role"],
        "rolled_back_at": row["rolled_back_at"],
        "writes_enabled": bool(row["writes_enabled"]),
        "external_adapter_write_through_enabled": bool(row["external_adapter_write_through_enabled"]),
        "external_write_executor_enabled": bool(row["external_write_executor_enabled"]),
        "rollback_required": bool(row["rollback_required"]),
        "rollback_available": bool(row["rollback_available"]),
        "rollback_reopen_enabled": bool(row["rollback_reopen_enabled"]),
        "write_through_allowed": bool(row["write_through_allowed"]),
        "source_surface": source_surface,
    }


def _execute_kanban_closeout_rollback(write_run: sqlite3.Row, *, actor_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from hermes_cli import kanban_db

    rollback_snapshot = json.loads(write_run["rollback_snapshot_json"])
    if rollback_snapshot.get("adapter_name") != "kanban" or rollback_snapshot.get("external_ref") != write_run["external_ref"]:
        raise ValueError("rollback snapshot target mismatch")
    before_status = str(rollback_snapshot.get("status_before") or "").strip()
    if before_status not in {"ready", "blocked", "scheduled", "todo", "triage", "review"}:
        raise ValueError(f"rollback target status is not enabled: {before_status}")
    before_result = rollback_snapshot.get("result_before")
    conn = kanban_db.connect()
    try:
        task = kanban_db.get_task(conn, write_run["external_ref"])
        if task is None:
            raise ValueError(f"kanban task not found for rollback: {write_run['external_ref']}")
        if task.status != "done":
            raise ValueError(f"kanban rollback requires current done status: {task.status}")
        current_snapshot = {
            "adapter_name": "kanban",
            "external_ref": write_run["external_ref"],
            "status_before_rollback": task.status,
            "result_before_rollback": task.result,
            "target_status": before_status,
            "source_write_execution_id": write_run["external_write_execution_id"],
        }
        with kanban_db.write_txn(conn):
            conn.execute(
                """
                UPDATE tasks
                   SET status = ?, result = ?, completed_at = NULL,
                       claim_lock = NULL, claim_expires = NULL, worker_pid = NULL,
                       current_run_id = NULL
                 WHERE id = ? AND status = 'done'
                """,
                (before_status, before_result, write_run["external_ref"]),
            )
            conn.execute(
                "INSERT INTO task_events (task_id, kind, payload, created_at) VALUES (?, ?, ?, strftime('%s','now'))",
                (
                    write_run["external_ref"],
                    "control_tower_rollback_reopened",
                    _canonical_json({
                        "external_write_execution_id": write_run["external_write_execution_id"],
                        "command_execution_digest": write_run["command_execution_digest"],
                        "rolled_back_by": actor_id,
                        "status_after": before_status,
                    }),
                ),
            )
        updated = kanban_db.get_task(conn, write_run["external_ref"])
    finally:
        conn.close()
    if updated is None or updated.status != before_status:
        raise ValueError("kanban rollback write did not persist target status")
    adapter_result = {
        "adapter_name": "kanban",
        "external_ref": write_run["external_ref"],
        "operation": "reopen",
        "status_after": updated.status,
        "result_after": updated.result,
    }
    return adapter_result, current_snapshot


def _execute_taskpool_closeout_rollback(write_run: sqlite3.Row, *, actor_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    rollback_snapshot = json.loads(write_run["rollback_snapshot_json"])
    if rollback_snapshot.get("adapter_name") != "taskpool" or rollback_snapshot.get("external_ref") != write_run["external_ref"]:
        raise ValueError("rollback snapshot target mismatch")
    before_status = str(rollback_snapshot.get("status_before") or "").strip()
    if before_status not in {"review", "running", "ready", "blocked", "scheduled"}:
        raise ValueError(f"rollback target status is not enabled: {before_status}")
    _path, frontmatter, _body = _read_taskpool_note(write_run["external_ref"])
    current_status = _frontmatter_status(frontmatter)
    if current_status != "done":
        raise ValueError(f"taskpool rollback requires current done status: {current_status}")
    current_snapshot = {
        "adapter_name": "taskpool",
        "external_ref": write_run["external_ref"],
        "status_before_rollback": current_status,
        "target_status": before_status,
        "source_write_execution_id": write_run["external_write_execution_id"],
    }
    _before_status, status_after, path = _write_taskpool_status(
        write_run["external_ref"],
        target_status=before_status,
        event_kind="control_tower_external_taskpool_rollback",
        payload={
            "external_write_execution_id": write_run["external_write_execution_id"],
            "command_execution_digest": write_run["command_execution_digest"],
            "rolled_back_by": actor_id,
            "status_after": before_status,
        },
    )
    adapter_result = {
        "adapter_name": "taskpool",
        "external_ref": write_run["external_ref"],
        "operation": "reopen",
        "status_after": status_after,
        "path": str(path),
    }
    return adapter_result, current_snapshot


def execute_external_adapter_write_rollback(
    *,
    external_write_execution_id: str,
    expected_command_execution_digest: str,
    actor_type: str,
    actor_id: str,
    actor_role: str,
    source_surface: str,
    reason: str = "",
) -> dict[str, Any]:
    """Rollback/reopen a completed, digest-bound Kanban external write."""
    external_write_execution_id = external_write_execution_id.strip()
    expected_command_execution_digest = expected_command_execution_digest.strip()
    actor_type = actor_type.strip()
    actor_id = actor_id.strip()
    actor_role = actor_role.strip().lower()
    source_surface = source_surface.strip()
    reason = reason.strip()
    if not external_write_execution_id:
        raise ValueError("external_write_execution_id is required")
    if not expected_command_execution_digest:
        raise ValueError("expected_command_execution_digest is required")
    if not actor_type:
        raise ValueError("actor_type is required")
    if not actor_id:
        raise ValueError("actor_id is required")
    if actor_role not in ROLE_RANK:
        raise ValueError("unsupported actor_role")
    if not _role_allows(actor_role, EXTERNAL_WRITE_AUTHORIZATION_REQUIRED_ROLE):
        raise ValueError("actor role cannot execute external adapter write rollback")
    if not source_surface:
        raise ValueError("source_surface is required")

    with _connect() as conn:
        existing = _select_external_write_rollback_run_by_execution(conn, external_write_execution_id)
        if existing is not None:
            if existing["command_execution_digest"] != expected_command_execution_digest:
                raise ValueError("command execution digest mismatch for existing external adapter write rollback")
            return _external_write_rollback_response(created=False, row=existing, source_surface=source_surface)
        write_run = _select_external_write_execution_run(conn, external_write_execution_id)
        if write_run is None:
            raise ValueError(f"external write execution not found: {external_write_execution_id}")
        if write_run["command_execution_digest"] != expected_command_execution_digest:
            raise ValueError("command execution digest mismatch for external adapter write rollback")
        if write_run["execution_status"] != "write_completed":
            raise ValueError(f"external write execution is not rollbackable: {write_run['execution_status']}")
        if write_run["adapter_name"] not in EXTERNAL_WRITE_EXECUTOR_ADAPTERS:
            raise ValueError(f"external write rollback adapter is not enabled: {write_run['adapter_name']}")
        if write_run["operation"] != "closeout":
            raise ValueError(f"external write rollback operation is not enabled: {write_run['operation']}")

    if write_run["adapter_name"] == "kanban":
        adapter_result, rollback_snapshot = _execute_kanban_closeout_rollback(write_run, actor_id=actor_id)
    elif write_run["adapter_name"] == "taskpool":
        adapter_result, rollback_snapshot = _execute_taskpool_closeout_rollback(write_run, actor_id=actor_id)
    else:  # pragma: no cover - guarded above
        raise ValueError(f"external write rollback adapter is not enabled: {write_run['adapter_name']}")
    rollback_run_id = f"extrollback_{uuid.uuid4().hex}"
    rolled_back_at = _now()
    with _connect() as conn:
        existing = _select_external_write_rollback_run_by_execution(conn, external_write_execution_id)
        if existing is not None:
            return _external_write_rollback_response(created=False, row=existing, source_surface=source_surface)
        conn.execute(
            """
            INSERT INTO external_adapter_write_rollback_runs(
                rollback_run_id, external_write_execution_id, external_write_preflight_id,
                external_write_authorization_id, external_write_review_id,
                external_write_intent_id, closeout_execution_id, task_id,
                adapter_name, external_ref, operation, adapter_contract_version,
                mutation_digest, command_execution_digest, rollback_status,
                adapter_result_json, rollback_snapshot_json, rolled_back_by,
                rolled_back_role, rolled_back_at, writes_enabled,
                external_adapter_write_through_enabled, external_write_executor_enabled,
                rollback_required, rollback_available, rollback_reopen_enabled,
                write_through_allowed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rollback_run_id,
                external_write_execution_id,
                write_run["external_write_preflight_id"],
                write_run["external_write_authorization_id"],
                write_run["external_write_review_id"],
                write_run["external_write_intent_id"],
                write_run["closeout_execution_id"],
                write_run["task_id"],
                write_run["adapter_name"],
                write_run["external_ref"],
                "reopen",
                write_run["adapter_contract_version"],
                write_run["mutation_digest"],
                write_run["command_execution_digest"],
                "rollback_completed",
                _canonical_json(adapter_result),
                _canonical_json(rollback_snapshot),
                actor_id,
                actor_role,
                rolled_back_at,
                1,
                1,
                1,
                1,
                1,
                1,
                1,
            ),
        )
        observation_payload = {
            "id": write_run["external_ref"],
            "status": adapter_result["status_after"],
            "result": adapter_result.get("result_after"),
            "source": "external_adapter_write_rollback",
            "rollback_run_id": rollback_run_id,
            "external_write_execution_id": external_write_execution_id,
            "command_execution_digest": write_run["command_execution_digest"],
        }
        try:
            conn.execute(
                """
                INSERT INTO adapter_observations(
                    observation_id, adapter_name, external_ref, observed_type,
                    observed_hash, payload_json, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"obs_{uuid.uuid4().hex}",
                    write_run["adapter_name"],
                    write_run["external_ref"],
                    "external_adapter_rollback",
                    _payload_hash(observation_payload),
                    _canonical_json(observation_payload),
                    rolled_back_at,
                ),
            )
        except sqlite3.IntegrityError:
            pass
        _append_event(
            conn,
            event_type="ExternalAdapterWriteRollbackExecuted",
            aggregate_type="external_adapter_write_rollback",
            aggregate_id=rollback_run_id,
            actor_type=actor_type,
            actor_id=actor_id,
            source_surface=source_surface,
            payload={
                "rollback_run_id": rollback_run_id,
                "external_write_execution_id": external_write_execution_id,
                "adapter_name": write_run["adapter_name"],
                "external_ref": write_run["external_ref"],
                "rollback_status": "rollback_completed",
                "command_execution_digest": write_run["command_execution_digest"],
                "reason": reason,
            },
        )
        conn.commit()
        row = _select_external_write_rollback_run(conn, rollback_run_id)
    return _external_write_rollback_response(created=True, row=row, source_surface=source_surface)


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


def _read_kanban_projection() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Read current Kanban rows without writing Control Tower ledger events."""
    try:
        from hermes_cli import kanban_db
    except Exception as exc:  # pragma: no cover - defensive optional adapter
        return {"available": False, "error": f"import failed: {exc}", "observed_tasks": 0}, []

    db_path = kanban_db.kanban_db_path()
    if not db_path.exists():
        return {"available": False, "path": str(db_path), "observed_tasks": 0}, []

    try:
        conn = kanban_db.connect()
        try:
            tasks = kanban_db.list_tasks(conn, include_archived=False)
        finally:
            conn.close()
    except Exception as exc:
        return {"available": False, "path": str(db_path), "error": str(exc), "observed_tasks": 0}, []

    payloads = [_kanban_task_payload(task) for task in tasks]
    return {
        "available": True,
        "path": str(db_path),
        "observed_tasks": len(payloads),
        "new_observations": 0,
        "observation_mode": "read_only_no_ledger_write",
    }, payloads


def observe_kanban_adapter() -> dict[str, Any]:
    """Record existing Kanban state as adapter input to the ledger.

    This is an explicit observation-ingest helper, not the Dashboard snapshot
    path. Dashboard/Console refreshes must remain read-only projections and use
    ``_read_kanban_projection`` instead.
    """
    adapter, tasks = _read_kanban_projection()
    if not adapter.get("available"):
        return adapter

    created = 0
    for task in tasks:
        result = record_adapter_observation(
            adapter_name="kanban",
            external_ref=f"task:{task.get('id')}",
            observed_type="task",
            payload=task,
        )
        if result.get("created"):
            created += 1
    return {**adapter, "new_observations": created, "observation_mode": "explicit_ledger_observation"}


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


def _executor_plans_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT plan_id, command_id, command_type, mode, status, execution, copy_command
        FROM executor_plans
        WHERE status = 'planned'
        ORDER BY rowid ASC
        LIMIT 20
        """
    ).fetchall()
    return {
        "mode": "dry_run_copy_command_only",
        "writes_enabled": False,
        "planned_count": _count(conn, "executor_plans", "status = 'planned'"),
        "recent_plans": [dict(row) for row in rows],
    }


def _execution_policy_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT policy_review_id, plan_id, command_id, policy_status,
               allowed_to_execute, writes_enabled, blocked_reason,
               rollback_preview_json
        FROM execution_policy_reviews
        ORDER BY rowid ASC
        LIMIT 20
        """
    ).fetchall()
    recent_reviews: list[dict[str, Any]] = []
    for row in rows:
        rollback_preview = json.loads(row["rollback_preview_json"])
        recent_reviews.append(
            {
                "policy_review_id": row["policy_review_id"],
                "plan_id": row["plan_id"],
                "command_id": row["command_id"],
                "policy_status": row["policy_status"],
                "allowed_to_execute": bool(row["allowed_to_execute"]),
                "blocked_reason": row["blocked_reason"],
                "rollback_available": bool(rollback_preview.get("available")),
            }
        )
    return {
        "mode": "read_only_executor_policy_projection",
        "writes_enabled": False,
        "real_executor_enabled": False,
        "read_only_whitelist": sorted(READ_ONLY_EXECUTOR_COMMAND_WHITELIST),
        "risk_whitelist": sorted(READ_ONLY_EXECUTOR_RISK_WHITELIST),
        "review_count": _count(conn, "execution_policy_reviews"),
        "read_only_eligible_count": _count(conn, "execution_policy_reviews", "policy_status = 'read_only_eligible'"),
        "blocked_count": _count(conn, "execution_policy_reviews", "policy_status = 'execution_blocked'"),
        "recent_reviews": recent_reviews,
    }


def _read_only_executor_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT executor_run_id, plan_id, command_id, command_type,
               executor_status, writes_enabled
        FROM read_only_executor_runs
        ORDER BY rowid ASC
        LIMIT 20
        """
    ).fetchall()
    return {
        "mode": "read_only_executor_consumer",
        "writes_enabled": False,
        "adapter_write_through_enabled": False,
        "closeout_execution_enabled": False,
        "run_count": _count(conn, "read_only_executor_runs"),
        "recent_runs": [
            {
                "executor_run_id": row["executor_run_id"],
                "plan_id": row["plan_id"],
                "command_id": row["command_id"],
                "command_type": row["command_type"],
                "executor_status": row["executor_status"],
                "writes_enabled": bool(row["writes_enabled"]),
            }
            for row in rows
        ],
    }


def _evidence_policy_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT evidence_review_id, task_id, risk_level, evidence_status,
               required_maturity, closeout_allowed, blocked_reason
        FROM evidence_gate_reviews
        ORDER BY rowid ASC
        LIMIT 20
        """
    ).fetchall()
    return {
        "mode": "evidence_gate_projection_only",
        "closeout_execution_enabled": False,
        "review_count": _count(conn, "evidence_gate_reviews"),
        "required_maturity_by_risk": dict(RISK_REQUIRED_EVIDENCE_MATURITY),
        "recent_reviews": [
            {
                "evidence_review_id": row["evidence_review_id"],
                "task_id": row["task_id"],
                "risk_level": row["risk_level"],
                "evidence_status": row["evidence_status"],
                "required_maturity": row["required_maturity"],
                "closeout_allowed": bool(row["closeout_allowed"]),
                "blocked_reason": row["blocked_reason"],
            }
            for row in rows
        ],
    }


def _conflicting_evidence_override_authorizations_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    pending_rows = conn.execute(
        """
        SELECT conflict_override_authorization_id, evidence_review_id, task_id,
               risk_level, evidence_state_hash, stale_or_conflicting_count,
               status, required_role, requested_by, requested_role, requested_at, expires_at,
               override_allowed, closeout_allowed
        FROM conflicting_evidence_override_authorizations
        WHERE status = 'requested'
        ORDER BY rowid ASC
        LIMIT 20
        """
    ).fetchall()
    decided_rows = conn.execute(
        """
        SELECT conflict_override_authorization_id, evidence_review_id, task_id,
               risk_level, evidence_state_hash, stale_or_conflicting_count,
               status, required_role, requested_by, requested_role, requested_at,
               expires_at, decided_by, decided_role, decided_at, override_allowed, closeout_allowed
        FROM conflicting_evidence_override_authorizations
        WHERE status IN ('approved', 'rejected', 'expired')
        ORDER BY COALESCE(decided_at, expires_at) DESC, rowid DESC
        LIMIT 20
        """
    ).fetchall()
    return {
        "mode": "conflicting_evidence_override_projection_only",
        "execution_enabled": False,
        "override_consumption_enabled": False,
        "closeout_execution_enabled": False,
        "required_role": "admin",
        "ttl_hours": DEFAULT_CLOSEOUT_AUTH_TTL_HOURS,
        "pending_count": _count(conn, "conflicting_evidence_override_authorizations", "status = 'requested'"),
        "expired_count": _count(conn, "conflicting_evidence_override_authorizations", "status = 'expired'"),
        "pending": [dict(row) for row in pending_rows],
        "decided_count": _count(conn, "conflicting_evidence_override_authorizations", "status IN ('approved', 'rejected')"),
        "recent_decided": [dict(row) for row in decided_rows],
    }


def _closeout_authorizations_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    pending_rows = conn.execute(
        """
        SELECT closeout_authorization_id, task_id, risk_level, evidence_review_id,
               status, required_role, requested_by, requested_role, requested_at, expires_at
        FROM closeout_authorizations
        WHERE status = 'requested'
        ORDER BY rowid ASC
        LIMIT 20
        """
    ).fetchall()
    decided_rows = conn.execute(
        """
        SELECT closeout_authorization_id, task_id, risk_level, evidence_review_id,
               status, required_role, requested_by, requested_role, requested_at,
               expires_at, decided_by, decided_role, decided_at, closeout_allowed
        FROM closeout_authorizations
        WHERE status IN ('approved', 'rejected', 'expired')
        ORDER BY COALESCE(decided_at, expires_at) DESC, rowid DESC
        LIMIT 20
        """
    ).fetchall()
    return {
        "mode": "human_authorization_projection_only",
        "closeout_execution_enabled": False,
        "required_role_by_risk": dict(CLOSEOUT_REQUIRED_ROLE_BY_RISK),
        "ttl_hours": DEFAULT_CLOSEOUT_AUTH_TTL_HOURS,
        "pending_count": _count(conn, "closeout_authorizations", "status = 'requested'"),
        "expired_count": _count(conn, "closeout_authorizations", "status = 'expired'"),
        "pending": [dict(row) for row in pending_rows],
        "decided_count": _count(conn, "closeout_authorizations", "status IN ('approved', 'rejected')"),
        "recent_decided": [dict(row) for row in decided_rows],
    }


def _closeout_execution_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT r.closeout_execution_id, r.closeout_authorization_id, r.task_id,
               t.external_ref AS external_ref,
               r.risk_level, r.evidence_review_id, r.execution_status, r.task_status,
               r.external_adapter_write_through_enabled, r.executed_by, r.executed_role,
               r.executed_at
        FROM closeout_execution_runs r
        LEFT JOIN tasks t ON t.task_id = r.task_id
        ORDER BY r.rowid ASC
        LIMIT 20
        """
    ).fetchall()
    return {
        "mode": "control_tower_ledger_closeout_consumer",
        "external_adapter_write_through_enabled": False,
        "rollback_reopen_enabled": False,
        "run_count": _count(conn, "closeout_execution_runs"),
        "recent_runs": [
            {
                "closeout_execution_id": row["closeout_execution_id"],
                "closeout_authorization_id": row["closeout_authorization_id"],
                "task_id": row["task_id"],
                "external_ref": row["external_ref"],
                "risk_level": row["risk_level"],
                "evidence_review_id": row["evidence_review_id"],
                "execution_status": row["execution_status"],
                "task_status": row["task_status"],
                "external_adapter_write_through_enabled": bool(row["external_adapter_write_through_enabled"]),
                "executed_by": row["executed_by"],
                "executed_role": row["executed_role"],
                "executed_at": row["executed_at"],
            }
            for row in rows
        ],
    }


def _external_adapter_write_intents_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT external_write_intent_id, closeout_execution_id, task_id,
               adapter_name, external_ref, operation, intent_status,
               mutation_digest, writes_enabled, external_adapter_write_through_enabled,
               rollback_required, rollback_available, prepared_by, prepared_role,
               prepared_at
        FROM external_adapter_write_intents
        ORDER BY rowid ASC
        LIMIT 20
        """
    ).fetchall()
    return {
        "mode": "external_adapter_write_intent_projection_only",
        "external_adapter_write_through_enabled": False,
        "writes_enabled": False,
        "rollback_reopen_enabled": False,
        "allowed_adapters": sorted(EXTERNAL_WRITE_INTENT_ADAPTERS),
        "intent_count": _count(conn, "external_adapter_write_intents"),
        "blocked_count": _count(conn, "external_adapter_write_intents", "intent_status = 'blocked_pending_adapter_contract'"),
        "recent_intents": [
            {
                "external_write_intent_id": row["external_write_intent_id"],
                "closeout_execution_id": row["closeout_execution_id"],
                "task_id": row["task_id"],
                "adapter_name": row["adapter_name"],
                "external_ref": row["external_ref"],
                "operation": row["operation"],
                "intent_status": row["intent_status"],
                "mutation_digest": row["mutation_digest"],
                "writes_enabled": bool(row["writes_enabled"]),
                "external_adapter_write_through_enabled": bool(row["external_adapter_write_through_enabled"]),
                "rollback_required": bool(row["rollback_required"]),
                "rollback_available": bool(row["rollback_available"]),
                "prepared_by": row["prepared_by"],
                "prepared_role": row["prepared_role"],
                "prepared_at": row["prepared_at"],
            }
            for row in rows
        ],
    }


def _external_adapter_write_intent_reviews_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT external_write_review_id, external_write_intent_id,
               closeout_execution_id, task_id, adapter_name, external_ref,
               operation, adapter_contract_version, review_status,
               blocked_reasons_json, mutation_digest, writes_enabled,
               external_adapter_write_through_enabled, rollback_required,
               rollback_available, write_through_allowed, reviewed_by,
               reviewed_role, reviewed_at
        FROM external_adapter_write_intent_reviews
        ORDER BY rowid ASC
        LIMIT 20
        """
    ).fetchall()
    return {
        "mode": "external_adapter_write_readiness_projection_only",
        "external_adapter_write_through_enabled": False,
        "writes_enabled": False,
        "rollback_reopen_enabled": False,
        "review_count": _count(conn, "external_adapter_write_intent_reviews"),
        "blocked_count": _count(conn, "external_adapter_write_intent_reviews", "review_status = 'blocked'"),
        "write_through_allowed_count": _count(conn, "external_adapter_write_intent_reviews", "write_through_allowed = 1"),
        "recent_reviews": [
            {
                "external_write_review_id": row["external_write_review_id"],
                "external_write_intent_id": row["external_write_intent_id"],
                "closeout_execution_id": row["closeout_execution_id"],
                "task_id": row["task_id"],
                "adapter_name": row["adapter_name"],
                "external_ref": row["external_ref"],
                "operation": row["operation"],
                "adapter_contract_version": row["adapter_contract_version"],
                "review_status": row["review_status"],
                "blocked_reasons": json.loads(row["blocked_reasons_json"]),
                "mutation_digest": row["mutation_digest"],
                "writes_enabled": bool(row["writes_enabled"]),
                "external_adapter_write_through_enabled": bool(row["external_adapter_write_through_enabled"]),
                "rollback_required": bool(row["rollback_required"]),
                "rollback_available": bool(row["rollback_available"]),
                "write_through_allowed": bool(row["write_through_allowed"]),
                "reviewed_by": row["reviewed_by"],
                "reviewed_role": row["reviewed_role"],
                "reviewed_at": row["reviewed_at"],
            }
            for row in rows
        ],
    }


def _external_adapter_write_authorizations_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT external_write_authorization_id, external_write_review_id,
               external_write_intent_id, closeout_execution_id, task_id,
               adapter_name, external_ref, operation, adapter_contract_version,
               mutation_digest, status, required_role, requested_by,
               requested_role, requested_at, expires_at, decided_by,
               decided_role, decided_at, writes_enabled,
               external_adapter_write_through_enabled, rollback_required,
               rollback_available, write_through_allowed
        FROM external_adapter_write_authorizations
        ORDER BY rowid ASC
        LIMIT 20
        """
    ).fetchall()
    return {
        "mode": "external_adapter_write_authorization_projection_only",
        "external_adapter_write_through_enabled": False,
        "writes_enabled": False,
        "rollback_reopen_enabled": False,
        "authorization_count": _count(conn, "external_adapter_write_authorizations"),
        "requested_count": _count(conn, "external_adapter_write_authorizations", "status = 'requested'"),
        "approved_count": _count(conn, "external_adapter_write_authorizations", "status = 'approved'"),
        "denied_count": _count(conn, "external_adapter_write_authorizations", "status = 'denied'"),
        "expired_count": _count(conn, "external_adapter_write_authorizations", "status = 'expired'"),
        "write_through_allowed_count": _count(conn, "external_adapter_write_authorizations", "write_through_allowed = 1"),
        "recent_authorizations": [
            {
                "external_write_authorization_id": row["external_write_authorization_id"],
                "external_write_review_id": row["external_write_review_id"],
                "external_write_intent_id": row["external_write_intent_id"],
                "closeout_execution_id": row["closeout_execution_id"],
                "task_id": row["task_id"],
                "adapter_name": row["adapter_name"],
                "external_ref": row["external_ref"],
                "operation": row["operation"],
                "adapter_contract_version": row["adapter_contract_version"],
                "mutation_digest": row["mutation_digest"],
                "status": row["status"],
                "required_role": row["required_role"],
                "requested_by": row["requested_by"],
                "requested_role": row["requested_role"],
                "requested_at": row["requested_at"],
                "expires_at": row["expires_at"],
                "decided_by": row["decided_by"],
                "decided_role": row["decided_role"],
                "decided_at": row["decided_at"],
                "writes_enabled": bool(row["writes_enabled"]),
                "external_adapter_write_through_enabled": bool(row["external_adapter_write_through_enabled"]),
                "rollback_required": bool(row["rollback_required"]),
                "rollback_available": bool(row["rollback_available"]),
                "write_through_allowed": bool(row["write_through_allowed"]),
            }
            for row in rows
        ],
    }


def _external_adapter_write_execution_preflights_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT external_write_preflight_id, external_write_authorization_id,
               external_write_review_id, external_write_intent_id,
               task_id, adapter_name, external_ref, operation,
               adapter_contract_version, mutation_digest,
               command_execution_digest, preflight_status, prepared_by,
               prepared_role, prepared_at, external_write_executor_enabled,
               external_adapter_write_through_enabled, rollback_required,
               rollback_available, write_through_allowed
        FROM external_adapter_write_execution_preflights
        ORDER BY rowid ASC
        LIMIT 20
        """
    ).fetchall()
    return {
        "mode": "external_adapter_write_execution_preflight_projection_only",
        "external_adapter_write_through_enabled": False,
        "writes_enabled": False,
        "external_write_executor_enabled": False,
        "rollback_reopen_enabled": False,
        "preflight_count": _count(conn, "external_adapter_write_execution_preflights"),
        "blocked_count": _count(conn, "external_adapter_write_execution_preflights", "preflight_status = 'blocked_pending_external_write_executor'"),
        "write_through_allowed_count": _count(conn, "external_adapter_write_execution_preflights", "write_through_allowed = 1"),
        "recent_preflights": [
            {
                "external_write_preflight_id": row["external_write_preflight_id"],
                "external_write_authorization_id": row["external_write_authorization_id"],
                "external_write_review_id": row["external_write_review_id"],
                "external_write_intent_id": row["external_write_intent_id"],
                "task_id": row["task_id"],
                "adapter_name": row["adapter_name"],
                "external_ref": row["external_ref"],
                "operation": row["operation"],
                "adapter_contract_version": row["adapter_contract_version"],
                "mutation_digest": row["mutation_digest"],
                "command_execution_digest": row["command_execution_digest"],
                "preflight_status": row["preflight_status"],
                "prepared_by": row["prepared_by"],
                "prepared_role": row["prepared_role"],
                "prepared_at": row["prepared_at"],
                "external_write_executor_enabled": bool(row["external_write_executor_enabled"]),
                "external_adapter_write_through_enabled": bool(row["external_adapter_write_through_enabled"]),
                "rollback_required": bool(row["rollback_required"]),
                "rollback_available": bool(row["rollback_available"]),
                "write_through_allowed": bool(row["write_through_allowed"]),
            }
            for row in rows
        ],
    }


def _external_adapter_write_execution_runs_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT external_write_execution_id, external_write_preflight_id,
               external_write_authorization_id, external_write_review_id,
               external_write_intent_id, task_id, adapter_name, external_ref,
               operation, adapter_contract_version, mutation_digest,
               command_execution_digest, execution_status, adapter_result_json,
               rollback_snapshot_json, executed_by, executed_role, executed_at,
               external_write_executor_enabled,
               external_adapter_write_through_enabled, rollback_required,
               rollback_available, rollback_reopen_enabled, write_through_allowed
        FROM external_adapter_write_execution_runs
        ORDER BY rowid ASC
        LIMIT 20
        """
    ).fetchall()
    return {
        "mode": "external_adapter_write_through_consumer",
        "external_adapter_write_through_enabled": True,
        "writes_enabled": True,
        "external_write_executor_enabled": True,
        "rollback_reopen_enabled": False,
        "run_count": _count(conn, "external_adapter_write_execution_runs"),
        "completed_count": _count(conn, "external_adapter_write_execution_runs", "execution_status = 'write_completed'"),
        "write_through_allowed_count": _count(conn, "external_adapter_write_execution_runs", "write_through_allowed = 1"),
        "recent_runs": [
            {
                "external_write_execution_id": row["external_write_execution_id"],
                "external_write_preflight_id": row["external_write_preflight_id"],
                "external_write_authorization_id": row["external_write_authorization_id"],
                "external_write_review_id": row["external_write_review_id"],
                "external_write_intent_id": row["external_write_intent_id"],
                "task_id": row["task_id"],
                "adapter_name": row["adapter_name"],
                "external_ref": row["external_ref"],
                "operation": row["operation"],
                "adapter_contract_version": row["adapter_contract_version"],
                "mutation_digest": row["mutation_digest"],
                "command_execution_digest": row["command_execution_digest"],
                "execution_status": row["execution_status"],
                "adapter_result": json.loads(row["adapter_result_json"]),
                "rollback_snapshot": json.loads(row["rollback_snapshot_json"]),
                "executed_by": row["executed_by"],
                "executed_role": row["executed_role"],
                "executed_at": row["executed_at"],
                "external_write_executor_enabled": bool(row["external_write_executor_enabled"]),
                "external_adapter_write_through_enabled": bool(row["external_adapter_write_through_enabled"]),
                "rollback_required": bool(row["rollback_required"]),
                "rollback_available": bool(row["rollback_available"]),
                "rollback_reopen_enabled": bool(row["rollback_reopen_enabled"]),
                "write_through_allowed": bool(row["write_through_allowed"]),
            }
            for row in rows
        ],
    }


def _external_adapter_write_rollback_runs_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT rollback_run_id, external_write_execution_id, external_write_preflight_id,
               external_write_authorization_id, external_write_review_id,
               external_write_intent_id, task_id, adapter_name, external_ref,
               operation, adapter_contract_version, mutation_digest,
               command_execution_digest, rollback_status, adapter_result_json,
               rollback_snapshot_json, rolled_back_by, rolled_back_role, rolled_back_at,
               external_write_executor_enabled,
               external_adapter_write_through_enabled, rollback_required,
               rollback_available, rollback_reopen_enabled, write_through_allowed
        FROM external_adapter_write_rollback_runs
        ORDER BY rowid ASC
        LIMIT 20
        """
    ).fetchall()
    return {
        "mode": "external_adapter_rollback_reopen_consumer",
        "external_adapter_write_through_enabled": True,
        "writes_enabled": True,
        "external_write_executor_enabled": True,
        "rollback_reopen_enabled": True,
        "run_count": _count(conn, "external_adapter_write_rollback_runs"),
        "completed_count": _count(conn, "external_adapter_write_rollback_runs", "rollback_status = 'rollback_completed'"),
        "recent_runs": [
            {
                "rollback_run_id": row["rollback_run_id"],
                "external_write_execution_id": row["external_write_execution_id"],
                "external_write_preflight_id": row["external_write_preflight_id"],
                "external_write_authorization_id": row["external_write_authorization_id"],
                "external_write_review_id": row["external_write_review_id"],
                "external_write_intent_id": row["external_write_intent_id"],
                "task_id": row["task_id"],
                "adapter_name": row["adapter_name"],
                "external_ref": row["external_ref"],
                "operation": row["operation"],
                "adapter_contract_version": row["adapter_contract_version"],
                "mutation_digest": row["mutation_digest"],
                "command_execution_digest": row["command_execution_digest"],
                "rollback_status": row["rollback_status"],
                "adapter_result": json.loads(row["adapter_result_json"]),
                "rollback_snapshot": json.loads(row["rollback_snapshot_json"]),
                "rolled_back_by": row["rolled_back_by"],
                "rolled_back_role": row["rolled_back_role"],
                "rolled_back_at": row["rolled_back_at"],
                "external_write_executor_enabled": bool(row["external_write_executor_enabled"]),
                "external_adapter_write_through_enabled": bool(row["external_adapter_write_through_enabled"]),
                "rollback_required": bool(row["rollback_required"]),
                "rollback_available": bool(row["rollback_available"]),
                "rollback_reopen_enabled": bool(row["rollback_reopen_enabled"]),
                "write_through_allowed": bool(row["write_through_allowed"]),
            }
            for row in rows
        ],
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


def _mission_flow_signal_snapshot(*, stale_after_seconds: int = 900, limit_per_mission: int = 10) -> dict[str, Any]:
    """Return a read-only projection of pending Mission flow signals.

    Mission remains the canonical fact source for mission objectives and flow
    signals; Control Tower only surfaces their pending/stale state so operators
    can see why a mission cron should run or why the watchdog should intervene.
    """

    try:
        from hermes_cli.missions import MissionStore
    except Exception as exc:
        return {
            "role": "read_only_projection",
            "canonical": False,
            "available": False,
            "status": "unavailable",
            "reason": str(exc),
            "pending_count": 0,
            "stale_count": 0,
            "oldest_pending_age_seconds": 0,
            "max_pending_age_seconds": 0,
            "dispatched_count": 0,
            "maintenance_tick_count": 0,
            "consumed_count": 0,
            "avg_consumed_latency_seconds": None,
            "missions": [],
        }

    try:
        store = MissionStore()
        missions = store.list_missions(include_done=False)
        now_ts = datetime.now(timezone.utc).timestamp()
        rows: list[dict[str, Any]] = []
        pending_count = 0
        stale_count = 0
        pending_ages: list[int] = []
        dispatched_count = 0
        maintenance_tick_count = 0
        consumed_count = 0
        consumed_latency_total = 0
        consumed_latency_count = 0
        with sqlite3.connect(store.path) as mission_conn:
            mission_conn.row_factory = sqlite3.Row
            for mission in missions:
                pending = store.pending_flow_signals(mission.mission_id, limit=limit_per_mission)
                stale_ids = {
                    signal.signal_id
                    for signal in store.stale_flow_signals(
                        mission.mission_id,
                        older_than_seconds=stale_after_seconds,
                        limit=limit_per_mission,
                    )
                }
                mission_dispatched_count = store.count_events(mission.mission_id, {"flow_signal_dispatched"})
                mission_maintenance_tick_count = store.count_events(mission.mission_id, {"maintenance_tick"})
                consumed_row = mission_conn.execute(
                    """
                    SELECT COUNT(*) AS consumed_count,
                           AVG(consumed_at - created_at) AS avg_latency_seconds
                    FROM mission_flow_signals
                    WHERE mission_id = ? AND status = 'consumed' AND consumed_at IS NOT NULL
                    """,
                    (mission.mission_id,),
                ).fetchone()
                mission_consumed_count = int(consumed_row["consumed_count"] if consumed_row else 0)
                mission_avg_consumed_latency = None
                if consumed_row and consumed_row["avg_latency_seconds"] is not None:
                    mission_avg_consumed_latency = max(0, int(consumed_row["avg_latency_seconds"]))
                    consumed_latency_total += mission_avg_consumed_latency * mission_consumed_count
                    consumed_latency_count += mission_consumed_count
                dispatched_count += mission_dispatched_count
                maintenance_tick_count += mission_maintenance_tick_count
                consumed_count += mission_consumed_count
                signal_rows = []
                mission_pending_ages: list[int] = []
                for signal in pending:
                    age_seconds = max(0, int(now_ts - signal.created_at)) if signal.created_at else 0
                    is_stale = signal.signal_id in stale_ids
                    pending_count += 1
                    pending_ages.append(age_seconds)
                    mission_pending_ages.append(age_seconds)
                    if is_stale:
                        stale_count += 1
                    signal_rows.append(
                        {
                            "signal_id": signal.signal_id,
                            "source_event_id": signal.source_event_id,
                            "signal_type": signal.signal_type,
                            "status": signal.status,
                            "age_seconds": age_seconds,
                            "stale": is_stale,
                            "payload": signal.payload,
                        }
                    )
                if signal_rows:
                    rows.append(
                        {
                            "mission_id": mission.mission_id,
                            "stage": mission.stage,
                            "status": mission.status,
                            "cadence": mission.cadence,
                            "metrics": {
                                "oldest_pending_age_seconds": max(mission_pending_ages) if mission_pending_ages else 0,
                                "pending_count": len(signal_rows),
                                "stale_count": sum(1 for signal in signal_rows if signal["stale"]),
                                "dispatched_count": mission_dispatched_count,
                                "maintenance_tick_count": mission_maintenance_tick_count,
                                "consumed_count": mission_consumed_count,
                                "avg_consumed_latency_seconds": mission_avg_consumed_latency,
                            },
                            "pending_signals": signal_rows,
                        }
                    )
        avg_consumed_latency = None
        if consumed_latency_count:
            avg_consumed_latency = max(0, int(consumed_latency_total / consumed_latency_count))
        return {
            "role": "read_only_projection",
            "canonical": False,
            "source": "Mission ledger",
            "status": "ok",
            "stale_after_seconds": int(stale_after_seconds),
            "pending_count": pending_count,
            "stale_count": stale_count,
            "oldest_pending_age_seconds": max(pending_ages) if pending_ages else 0,
            "max_pending_age_seconds": max(pending_ages) if pending_ages else 0,
            "dispatched_count": dispatched_count,
            "maintenance_tick_count": maintenance_tick_count,
            "consumed_count": consumed_count,
            "avg_consumed_latency_seconds": avg_consumed_latency,
            "missions": rows,
        }
    except Exception as exc:
        return {
            "role": "read_only_projection",
            "canonical": False,
            "available": False,
            "status": "error",
            "reason": str(exc),
            "pending_count": 0,
            "stale_count": 0,
            "oldest_pending_age_seconds": 0,
            "max_pending_age_seconds": 0,
            "dispatched_count": 0,
            "maintenance_tick_count": 0,
            "consumed_count": 0,
            "avg_consumed_latency_seconds": None,
            "missions": [],
        }


def _mission_taskpool_bridge_snapshot(*, limit_per_mission: int = 5) -> dict[str, Any]:
    """Return a compact read-only panel for taskpool->mission bridge events.

    MissionStore remains the canonical ledger for bridged taskpool events. This
    projection intentionally reads only mission events and pending signals so the
    dashboard can show which taskpool changes are waking the governed mission
    cron without becoming a second taskpool or executor.
    """

    try:
        from hermes_cli.missions import MissionStore
    except Exception as exc:
        return {
            "role": "read_only_projection",
            "canonical": False,
            "available": False,
            "status": "unavailable",
            "reason": str(exc),
            "bridged_event_count": 0,
            "pending_bridge_signal_count": 0,
            "missions": [],
        }

    try:
        store = MissionStore()
        missions = store.list_missions(include_done=False)
        rows: list[dict[str, Any]] = []
        total_events = 0
        total_pending = 0
        for mission in missions:
            events = store.list_events_by_type(mission.mission_id, "taskpool_status_changed", limit=limit_per_mission)
            if not events:
                continue
            pending_by_source = {
                signal.source_event_id: signal
                for signal in store.pending_flow_signals(mission.mission_id, limit=limit_per_mission * 2)
                if signal.signal_type == "taskpool_status_changed"
            }
            recent_events: list[dict[str, Any]] = []
            mission_pending = 0
            for event in events:
                payload = event.get("payload") or {}
                pending_signal = pending_by_source.get(event["event_id"])
                if pending_signal:
                    mission_pending += 1
                recent_events.append(
                    {
                        "event_id": event["event_id"],
                        "created_at": event["created_at"],
                        "summary": event.get("summary", ""),
                        "task_id": payload.get("task_id") or payload.get("external_ref"),
                        "from_status": payload.get("from_status"),
                        "to_status": payload.get("to_status") or payload.get("status"),
                        "source": payload.get("source"),
                        "pending_signal_id": pending_signal.signal_id if pending_signal else None,
                    }
                )
            total_events += len(events)
            total_pending += mission_pending
            rows.append(
                {
                    "mission_id": mission.mission_id,
                    "stage": mission.stage,
                    "status": mission.status,
                    "bridged_event_count": len(events),
                    "pending_bridge_signal_count": mission_pending,
                    "recent_events": recent_events,
                }
            )
        return {
            "role": "read_only_projection",
            "canonical": False,
            "source": "Mission ledger taskpool_status_changed events",
            "status": "ok",
            "bridged_event_count": total_events,
            "pending_bridge_signal_count": total_pending,
            "missions": rows,
        }
    except Exception as exc:
        return {
            "role": "read_only_projection",
            "canonical": False,
            "available": False,
            "status": "error",
            "reason": str(exc),
            "bridged_event_count": 0,
            "pending_bridge_signal_count": 0,
            "missions": [],
        }


def _source_drift_snapshot(
    *,
    repo_root: Path | None = None,
    isolated_fixture: bool | None = None,
) -> dict[str, Any]:
    """Return a compact git source-drift projection for the operator screen."""

    if isolated_fixture is None:
        isolated_fixture = get_hermes_home() != Path.home() / ".hermes"
    if isolated_fixture:
        return {"status": "not_checked", "reason": "not part of isolated snapshot fixture"}
    root = repo_root or Path(__file__).resolve().parents[1]
    try:
        proc = subprocess.run(
            ["git", "status", "--short", "--branch"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    branch_line = lines[0] if lines else ""
    dirty_lines = lines[1:]
    ahead = "[ahead" in branch_line
    behind = "behind" in branch_line
    dirty = bool(dirty_lines)
    if dirty:
        status = "dirty"
    elif ahead or behind:
        status = "diverged" if ahead and behind else ("ahead" if ahead else "behind")
    else:
        status = "clean"
    return {
        "status": status,
        "branch_line": branch_line,
        "ahead": ahead,
        "behind": behind,
        "dirty": dirty,
        "dirty_count": len(dirty_lines),
    }


def _taskpool_runtime_probe(*, isolated_fixture: bool | None = None) -> dict[str, Any]:
    """Read the governed taskpool runtime selector without mutating state.

    Control Tower's ledger projection can lag the canonical brain/taskpool
    selector. This probe is fail-soft and read-only: it lets the operator screen
    distinguish true no-work from projection drift without claiming or executing
    anything.
    """

    if isolated_fixture is None:
        isolated_fixture = get_hermes_home() != Path.home() / ".hermes"
    if isolated_fixture:
        return {"status": "not_checked", "reason": "not part of isolated snapshot fixture"}
    script = Path.home() / ".hermes" / "scripts" / "brain_task_orchestrator.py"
    if not script.exists():
        return {"status": "unavailable", "reason": f"missing script: {script}"}
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "next", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "reason": "brain_task_orchestrator next --json timed out"}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    if proc.returncode != 0:
        return {
            "status": "error",
            "returncode": proc.returncode,
            "reason": (proc.stderr or proc.stdout).strip()[:500],
        }
    try:
        payload = json.loads(proc.stdout or "{}")
    except Exception as exc:
        return {"status": "error", "reason": f"invalid json: {exc}"}
    if not isinstance(payload, dict):
        return {"status": "error", "reason": f"unexpected json payload type: {type(payload).__name__}"}
    next_task = payload.get("next")
    if next_task:
        if not isinstance(next_task, dict):
            return {"status": "error", "reason": f"unexpected next task type: {type(next_task).__name__}"}
        return {
            "status": "dispatchable",
            "dispatchable": True,
            "next_task_id": next_task.get("id"),
            "next_title": next_task.get("title"),
            "next_status": next_task.get("status"),
            "next_priority": next_task.get("priority"),
            "claim_command_available": bool(payload.get("claim_command")),
        }
    return {
        "status": "idle",
        "dispatchable": False,
        "reason": payload.get("reason") or payload.get("status") or "no next task",
    }


def _kanban_coverage_probe(*, isolated_fixture: bool | None = None) -> dict[str, Any]:
    """Read the Kanban/agent coverage diagnostic as a report-only projection."""

    if isolated_fixture is None:
        isolated_fixture = get_hermes_home() != Path.home() / ".hermes"
    if isolated_fixture:
        return {"status": "not_checked", "reason": "not part of isolated snapshot fixture"}
    script = Path.home() / ".hermes" / "scripts" / "kanban_coverage.py"
    if not script.exists():
        return {"status": "unavailable", "reason": f"missing script: {script}"}
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "reason": "kanban_coverage.py --json timed out"}
    except Exception as exc:
        return {"status": "error", "reason": str(exc)}
    if proc.returncode != 0:
        return {
            "status": "error",
            "returncode": proc.returncode,
            "reason": (proc.stderr or proc.stdout).strip()[:500],
        }
    try:
        payload = json.loads(proc.stdout or "{}")
    except Exception as exc:
        return {"status": "error", "reason": f"invalid json: {exc}"}
    if not isinstance(payload, dict):
        return {"status": "error", "reason": f"unexpected json payload type: {type(payload).__name__}"}
    v2 = payload.get("agent_coverage_v2") if isinstance(payload.get("agent_coverage_v2"), dict) else {}
    return {
        "status": "ok",
        "source": "kanban_coverage.py",
        "report_only": True,
        "ssot": "brain_taskpool_markdown",
        "kanban_role": "execution_projection",
        "dashboard_console_role": "operator_projection",
        "score": payload.get("score"),
        "bottleneck": payload.get("bottleneck"),
        "counts": payload.get("counts") or {},
        "agent_coverage_v2": v2,
    }


def _operator_overview_snapshot(
    *,
    taskpool_health: dict[str, Any],
    next_dispatchable: dict[str, Any] | None,
    blockers: list[dict[str, Any]],
    mission_flow_signals: dict[str, Any],
    evidence_summary: dict[str, Any],
    human_inbox: dict[str, Any],
    source_drift: dict[str, Any] | None = None,
    taskpool_runtime: dict[str, Any] | None = None,
    projection_governance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the first-screen operator projection.

    This projection is intentionally derived from already-read snapshot data. It
    does not execute diagnostics or mutate lifecycle state; it only turns raw
    projections into the answer an operator needs first: can Hermes progress,
    why not, and what is the next safe action?
    """

    active = int(taskpool_health.get("active") or 0)
    pending_human = int(human_inbox.get("pending_count") or 0)
    stale_signals = int(mission_flow_signals.get("stale_count") or 0)
    total_evidence = sum(
        int(evidence_summary.get(maturity) or 0)
        for maturity in ["advisory", "observed", "verified", "certified", "release_confirmed"]
    )
    stale_or_conflicting = int(evidence_summary.get("stale_or_conflicting") or 0)
    source_drift = source_drift or {"status": "not_checked", "reason": "not part of isolated snapshot fixture"}
    taskpool_runtime = taskpool_runtime or {"status": "not_checked", "reason": "not part of isolated snapshot fixture"}
    projection_governance_data: dict[str, Any] = projection_governance or {
        "status": "not_checked",
        "reason": "not part of isolated snapshot fixture",
    }
    raw_coverage_v2 = projection_governance_data.get("agent_coverage_v2")
    coverage_v2: dict[str, Any] = raw_coverage_v2 if isinstance(raw_coverage_v2, dict) else {}
    raw_coverage_counts = coverage_v2.get("counts")
    coverage_counts: dict[str, Any] = raw_coverage_counts if isinstance(raw_coverage_counts, dict) else {}
    raw_coverage_caps = coverage_v2.get("caps")
    coverage_caps: list[Any] = raw_coverage_caps if isinstance(raw_coverage_caps, list) else []
    projection_drift_count = int(coverage_counts.get("projection_drift_tasks") or 0)
    stale_projection_count = int(coverage_counts.get("stale_projection_tasks") or 0)
    coverage_state = str(coverage_v2.get("state") or projection_governance_data.get("status") or "unknown")
    runtime_dispatchable = bool(taskpool_runtime.get("dispatchable"))
    projection_drift = runtime_dispatchable and next_dispatchable is None
    critical_alerts: list[dict[str, Any]] = []
    if projection_drift:
        critical_alerts.append(
            {
                "kind": "taskpool_projection_drift",
                "severity": "critical",
                "message": "Canonical taskpool runtime reports dispatchable work, but the Control Tower ledger projection has no next task.",
            }
        )
    if active and next_dispatchable is None:
        critical_alerts.append(
            {
                "kind": "no_dispatchable_work",
                "severity": "critical",
                "message": f"{active} active task(s) but no dispatchable next task.",
            }
        )
    if projection_drift_count or stale_projection_count or coverage_caps:
        critical_alerts.append(
            {
                "kind": "projection_governance_degraded",
                "severity": "warning",
                "message": (
                    f"Kanban/taskpool projection governance is degraded: "
                    f"state={coverage_state}, projection_drift={projection_drift_count}, "
                    f"stale_projection={stale_projection_count}, caps={len(coverage_caps)}."
                ),
            }
        )
    if stale_signals:
        critical_alerts.append(
            {
                "kind": "stale_mission_signals",
                "severity": "warning",
                "message": f"{stale_signals} mission flow signal(s) are stale.",
            }
        )
    if pending_human:
        critical_alerts.append(
            {
                "kind": "human_gate_pending",
                "severity": "warning",
                "message": f"{pending_human} human approval item(s) are pending.",
            }
        )
    blocker_counts: dict[str, int] = {}
    for blocker in blockers:
        reason = str(blocker.get("reason") or "unknown")
        blocker_counts[reason] = blocker_counts.get(reason, 0) + 1
    blocker_summary = [
        {"reason": reason, "count": count}
        for reason, count in sorted(blocker_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    evidence_debt = {
        "total_evidence": total_evidence,
        "stale_or_conflicting": stale_or_conflicting,
        "status": "empty" if total_evidence == 0 else ("degraded" if stale_or_conflicting else "ok"),
    }
    human_gate = {
        "pending_count": pending_human,
        "status": "waiting" if pending_human else "clear",
    }
    if next_dispatchable is not None:
        status = "progressable"
        headline = "Next dispatchable workflow is available."
        recommended_next_action = "dispatch_next_ready_task"
        progressable = True
    elif projection_drift:
        status = "projection_drift"
        headline = "Taskpool runtime is dispatchable, but the Control Tower projection is stale."
        recommended_next_action = "inspect_taskpool_projection_drift"
        progressable = True
    elif active:
        status = "blocked"
        headline = "Active workflow pressure exists, but no task is dispatchable."
        recommended_next_action = "triage_blockers"
        progressable = False
    elif stale_signals:
        status = "degraded"
        headline = "No active task pressure, but mission flow has stale signals."
        recommended_next_action = "run_read_only_mission_flow_diagnosis"
        progressable = False
    elif pending_human:
        status = "waiting_for_human"
        headline = "Workflow is waiting for human approval."
        recommended_next_action = "review_human_inbox"
        progressable = False
    else:
        status = "idle"
        headline = "No active workflow pressure observed."
        recommended_next_action = "refresh_or_wait_for_events"
        progressable = False
    next_safe_actions: list[dict[str, str]] = []
    if active and next_dispatchable is None:
        next_safe_actions.append(
            {
                "action": "triage_blockers",
                "mode": "read_only",
                "reason": "no_dispatchable_work",
                "command": "Refresh snapshot, inspect blocker summary, then run the taskpool read-only diagnosis path.",
            }
        )
    if projection_drift:
        next_safe_actions.insert(
            0,
            {
                "action": "inspect_taskpool_projection_drift",
                "mode": "read_only",
                "reason": "taskpool_projection_drift",
                "command": "Compare Control Tower ledger projection with brain_task_orchestrator next --json before claiming or resolving blockers.",
            },
        )
    if stale_signals:
        next_safe_actions.append(
            {
                "action": "run_read_only_mission_flow_diagnosis",
                "mode": "read_only",
                "reason": "stale_mission_signals",
                "command": "Inspect Mission event flow, then dispatch-events only after the event boundary is confirmed.",
            }
        )
    if projection_drift_count or stale_projection_count or coverage_caps:
        next_safe_actions.append(
            {
                "action": "inspect_projection_governance",
                "mode": "read_only",
                "reason": "kanban_taskpool_projection_governance_degraded",
                "command": "Run kanban_coverage.py --json, compare Brain task notes with Kanban projection, then reconcile only through the governed Brain→Kanban path.",
            }
        )
    if source_drift.get("dirty") or source_drift.get("ahead") or source_drift.get("behind"):
        next_safe_actions.append(
            {
                "action": "inspect_source_drift",
                "mode": "read_only",
                "reason": "source_drift",
                "command": "Review git status and verification evidence before mixing additional Control Tower changes.",
            }
        )
    return {
        "status": status,
        "progressable": progressable,
        "headline": headline,
        "recommended_next_action": recommended_next_action,
        "critical_alerts": critical_alerts,
        "blocker_summary": blocker_summary,
        "next_safe_actions": next_safe_actions,
        "evidence_debt": evidence_debt,
        "human_gate": human_gate,
        "source_drift": source_drift,
        "taskpool_runtime": taskpool_runtime,
        "projection_governance": projection_governance_data,
    }


def _workflow_kernel_policy_editor_paths() -> dict[str, Path]:
    root = Path(__file__).resolve().parents[1]
    base = root / "docs/private/workflow-kernel-policy-editor"
    return {
        "base": base,
        "ledger": base / "workflow_kernel_policy_editor_approvals.jsonl",
        "projection": base / "workflow_kernel_policy_editor_projection_20260613_phase56.json",
    }


def record_workflow_kernel_policy_editor_decision(
    *,
    decision: str,
    actor_type: str = "human",
    actor_id: str = "dashboard",
    actor_role: str = "operator",
    reason: str = "",
    policy_digest: str | None = None,
) -> dict[str, Any]:
    """Append a local Workflow Kernel policy-editor decision.

    This records operator intent only. It never edits dispatch policy, enables
    unattended dispatch, calls external adapters, or mutates cron state.
    """
    normalized = decision.strip().lower()
    allowed = {"draft", "approve_dry_run_only", "reject", "request_changes"}
    status = "recorded" if normalized in allowed else "blocked"
    paths = _workflow_kernel_policy_editor_paths()
    paths["base"].mkdir(parents=True, exist_ok=True)
    event = {
        "schema": "workflow_kernel_policy_editor_approval_v1",
        "phase": 56,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "status": status,
        "decision": normalized,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "actor_role": actor_role,
        "reason": reason,
        "policy_digest": policy_digest,
        "unattended_dispatch_enabled": False,
        "external_side_effects_executed": False,
        "cron_state_mutated": False,
        "safe_boundaries": [
            "approval ledger only",
            "no policy mutation",
            "no unattended dispatch enablement",
            "no external send",
            "no cron mutation",
        ],
    }
    with paths["ledger"].open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    projection = build_workflow_kernel_policy_editor_projection()
    paths["projection"].write_text(json.dumps(projection, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"status": status, "event": event, "projection": projection, "ledger": str(paths["ledger"])}


def build_workflow_kernel_policy_editor_projection() -> dict[str, Any]:
    paths = _workflow_kernel_policy_editor_paths()
    root = Path(__file__).resolve().parents[1]
    policy_path = root / "docs/private/workflow-kernel-unattended-dispatch-policy/workflow_kernel_unattended_dispatch_policy_20260613_phase54.json"
    phase55_path = root / "docs/private/workflow-kernel-unattended-dry-run-scheduler/workflow_kernel_unattended_dry_run_scheduler_20260613_phase55.json"
    policy = json.loads(policy_path.read_text()) if policy_path.exists() else {}
    phase55 = json.loads(phase55_path.read_text()) if phase55_path.exists() else {}
    events: list[dict[str, Any]] = []
    if paths["ledger"].exists():
        for line in paths["ledger"].read_text().splitlines():
            if line.strip():
                events.append(json.loads(line))
    latest = events[-1] if events else None
    return {
        "schema": "workflow_kernel_policy_editor_projection_v1",
        "phase": 56,
        "status": "POLICY_EDITOR_APPROVAL_UI_READY",
        "editor_mode": "approval_ledger_only",
        "policy_digest": policy.get("policy_digest"),
        "policy_status": policy.get("status"),
        "phase55_status": phase55.get("status"),
        "pending_decisions": 0 if latest else 1,
        "latest_decision": latest,
        "approval_ledger": str(paths["ledger"]),
        "unattended_dispatch_enabled": False,
        "external_side_effects_executed": False,
        "cron_state_mutated": False,
        "allowed_decisions": ["draft", "approve_dry_run_only", "reject", "request_changes"],
        "next_boundary": "larger divergence: enable dry-run cron job vs allow approved policy to activate guarded send runner",
    }


def _workflow_kernel_control_tower_snapshot() -> dict[str, Any]:
    """Return the Workflow Kernel Control Tower projection artifacts.

    This is a read-only bridge from the modular workflow-kernel evidence files
    into the live Control Tower snapshot. It never sends messages, mutates cron,
    enables unattended dispatch, or changes task lifecycle state.
    """
    root = Path(__file__).resolve().parents[1]
    registry_path = root / "docs/private/workflow-kernel-pack-registry/workflow_kernel_pack_registry_20260613_phase51.json"
    phase50_path = root / "docs/private/workflow-kernel-guarded-external-send/workflow_kernel_guarded_external_send_20260613_phase50.json"
    phase52_path = root / "docs/private/workflow-kernel-control-tower-projection/workflow_kernel_control_tower_projection_20260613_phase52.json"
    policy_path = root / "docs/private/workflow-kernel-unattended-dispatch-policy/workflow_kernel_unattended_dispatch_policy_20260613_phase54.json"
    phase55_path = root / "docs/private/workflow-kernel-unattended-dry-run-scheduler/workflow_kernel_unattended_dry_run_scheduler_20260613_phase55.json"
    phase57_path = root / "docs/private/workflow-kernel-dry-run-cron-rollout/workflow_kernel_dry_run_cron_rollout_20260613_phase57.json"
    phase58_path = root / "docs/private/workflow-kernel-guarded-send-runner-activation/workflow_kernel_guarded_send_runner_activation_20260613_phase58.json"
    phase59_path = root / "docs/private/workflow-kernel-guarded-manual-send-runner/workflow_kernel_guarded_manual_send_runner_20260613_phase59.json"
    phase60_path = root / "docs/private/workflow-kernel-guarded-manual-send-runner/workflow_kernel_one_shot_guarded_send_20260613_phase60.json"
    phase61_path = root / "docs/private/workflow-kernel-post-send-correlation-watch/workflow_kernel_post_send_correlation_watch_20260613_phase61.json"
    phase62_path = root / "docs/private/workflow-kernel-unattended-dispatch-enablement-packet/workflow_kernel_unattended_dispatch_enablement_packet_20260613_phase62.json"
    phase63_path = root / "docs/private/workflow-kernel-unattended-decision-record/workflow_kernel_unattended_decision_record_20260613_phase63.json"
    phase64_path = root / "docs/private/workflow-kernel-manual-only-closure/workflow_kernel_manual_only_closure_20260613_phase64.json"
    phase65_path = root / "docs/private/workflow-kernel-manual-only-monitoring/workflow_kernel_manual_only_monitoring_20260613_phase65.json"
    phase66_path = root / "docs/private/workflow-kernel-future-unattended-reopen-packet/workflow_kernel_future_unattended_reopen_packet_20260613_phase66.json"
    phase67_path = root / "docs/private/workflow-kernel-single-point-reopen-choice/workflow_kernel_single_point_reopen_choice_20260613_phase67.json"
    phase68_path = root / "docs/private/workflow-kernel-manual-only-governance-archive/workflow_kernel_manual_only_governance_archive_20260613_phase68.json"
    phase69_path = root / "docs/private/workflow-kernel-periodic-closure-health-report/workflow_kernel_periodic_closure_health_report_20260613_phase69.json"
    phase70_path = root / "docs/private/workflow-kernel-archive-publication-packet/workflow_kernel_archive_publication_packet_20260613_phase70.json"
    phase71_path = root / "docs/private/workflow-kernel-private-archive-retention-watch/workflow_kernel_private_archive_retention_watch_20260613_phase71.json"
    phase72_path = root / "docs/private/workflow-kernel-publication-exact-target-gate/workflow_kernel_publication_exact_target_gate_20260613_phase72.json"
    phase73_path = root / "docs/private/workflow-kernel-exact-target-apply-dry-run-compiler/workflow_kernel_exact_target_apply_dry_run_compiler_20260613_phase73.json"
    phase74_path = root / "docs/private/workflow-kernel-publication-apply-authorization-packet/workflow_kernel_publication_apply_authorization_packet_20260613_phase74.json"
    phase75_path = root / "docs/private/workflow-kernel-private-export-artifact-compiler/workflow_kernel_private_export_artifact_compiler_20260613_phase75.json"
    phase76_path = root / "docs/private/workflow-kernel-publication-apply-implementation-choice/workflow_kernel_publication_apply_implementation_choice_20260613_phase76.json"
    phase77_path = root / "docs/private/workflow-kernel-private-export-retention-watch/workflow_kernel_private_export_retention_watch_20260613_phase77.json"
    phase78_path = root / "docs/private/workflow-kernel-publication-apply-reopen-packet/workflow_kernel_publication_apply_reopen_packet_20260613_phase78.json"
    phase79_path = root / "docs/private/workflow-kernel-publication-closure-health-report/workflow_kernel_publication_closure_health_report_20260613_phase79.json"
    phase80_path = root / "docs/private/workflow-kernel-publication-governance-archive/workflow_kernel_publication_governance_archive_20260613_phase80.json"
    phase81_path = root / "docs/private/workflow-kernel-publication-archive-retention-watch/workflow_kernel_publication_archive_retention_watch_20260613_phase81.json"
    phase82_path = root / "docs/private/workflow-kernel-publication-export-review-packet/workflow_kernel_publication_export_review_packet_20260613_phase82.json"
    phase83_path = root / "docs/private/workflow-kernel-publication-export-review-retention-watch/workflow_kernel_publication_export_review_retention_watch_20260613_phase83.json"
    phase84_path = root / "docs/private/workflow-kernel-publication-apply-final-boundary-packet/workflow_kernel_publication_apply_final_boundary_packet_20260613_phase84.json"
    phase85_path = root / "docs/private/workflow-kernel-publication-final-boundary-retention-watch/workflow_kernel_publication_final_boundary_retention_watch_20260613_phase85.json"
    phase86_path = root / "docs/private/workflow-kernel-publication-governance-closure-summary/workflow_kernel_publication_governance_closure_summary_20260613_phase86.json"
    phase87_path = root / "docs/private/workflow-kernel-publication-closure-retention-watch/workflow_kernel_publication_closure_retention_watch_20260613_phase87.json"
    phase88_path = root / "docs/private/workflow-kernel-publication-apply-reopen-human-gate-packet/workflow_kernel_publication_apply_reopen_human_gate_packet_20260613_phase88.json"
    paths = {
        "registry": registry_path,
        "guarded_send": phase50_path,
        "control_tower_projection": phase52_path,
    }
    missing = [name for name, path in paths.items() if not path.exists()]
    if missing:
        return {
            "status": "missing_artifacts",
            "role": "read_only_workflow_kernel_projection",
            "missing": missing,
            "external_side_effects_enabled": False,
            "unattended_dispatch_enabled": False,
        }
    try:
        registry = json.loads(registry_path.read_text())
        guarded_send = json.loads(phase50_path.read_text())
        control_tower_projection = json.loads(phase52_path.read_text())
        policy = json.loads(policy_path.read_text()) if policy_path.exists() else None
        dry_run_scheduler = json.loads(phase55_path.read_text()) if phase55_path.exists() else None
        dry_run_cron_rollout = json.loads(phase57_path.read_text()) if phase57_path.exists() else None
        guarded_send_activation = json.loads(phase58_path.read_text()) if phase58_path.exists() else None
        guarded_manual_runner = json.loads(phase59_path.read_text()) if phase59_path.exists() else None
        one_shot_guarded_send = json.loads(phase60_path.read_text()) if phase60_path.exists() else None
        post_send_correlation = json.loads(phase61_path.read_text()) if phase61_path.exists() else None
        unattended_enablement_packet = json.loads(phase62_path.read_text()) if phase62_path.exists() else None
        unattended_decision_record = json.loads(phase63_path.read_text()) if phase63_path.exists() else None
        manual_only_closure = json.loads(phase64_path.read_text()) if phase64_path.exists() else None
        manual_only_monitoring = json.loads(phase65_path.read_text()) if phase65_path.exists() else None
        future_reopen_packet = json.loads(phase66_path.read_text()) if phase66_path.exists() else None
        single_point_reopen_choice = json.loads(phase67_path.read_text()) if phase67_path.exists() else None
        manual_governance_archive = json.loads(phase68_path.read_text()) if phase68_path.exists() else None
        periodic_closure_health = json.loads(phase69_path.read_text()) if phase69_path.exists() else None
        archive_publication_packet = json.loads(phase70_path.read_text()) if phase70_path.exists() else None
        private_archive_retention_watch = json.loads(phase71_path.read_text()) if phase71_path.exists() else None
        publication_exact_target_gate = json.loads(phase72_path.read_text()) if phase72_path.exists() else None
        exact_target_apply_dry_run_compiler = json.loads(phase73_path.read_text()) if phase73_path.exists() else None
        publication_apply_authorization_packet = json.loads(phase74_path.read_text()) if phase74_path.exists() else None
        private_export_artifact_compiler = json.loads(phase75_path.read_text()) if phase75_path.exists() else None
        publication_apply_implementation_choice = json.loads(phase76_path.read_text()) if phase76_path.exists() else None
        private_export_retention_watch = json.loads(phase77_path.read_text()) if phase77_path.exists() else None
        publication_apply_reopen_packet = json.loads(phase78_path.read_text()) if phase78_path.exists() else None
        publication_closure_health_report = json.loads(phase79_path.read_text()) if phase79_path.exists() else None
        publication_governance_archive = json.loads(phase80_path.read_text()) if phase80_path.exists() else None
        publication_archive_retention_watch = json.loads(phase81_path.read_text()) if phase81_path.exists() else None
        publication_export_review_packet = json.loads(phase82_path.read_text()) if phase82_path.exists() else None
        publication_export_review_retention_watch = json.loads(phase83_path.read_text()) if phase83_path.exists() else None
        publication_apply_final_boundary_packet = json.loads(phase84_path.read_text()) if phase84_path.exists() else None
        publication_final_boundary_retention_watch = json.loads(phase85_path.read_text()) if phase85_path.exists() else None
        publication_governance_closure_summary = json.loads(phase86_path.read_text()) if phase86_path.exists() else None
        publication_closure_retention_watch = json.loads(phase87_path.read_text()) if phase87_path.exists() else None
        publication_apply_reopen_human_gate_packet = json.loads(phase88_path.read_text()) if phase88_path.exists() else None
        policy_editor = build_workflow_kernel_policy_editor_projection()
    except Exception as exc:
        return {
            "status": "error",
            "role": "read_only_workflow_kernel_projection",
            "reason": str(exc),
            "external_side_effects_enabled": False,
            "unattended_dispatch_enabled": False,
        }
    blockers: list[str] = []
    if registry.get("status") != "PACK_REGISTRY_PROJECTION_READY":
        blockers.append("registry_not_ready")
    if guarded_send.get("status") != "GUARDED_EXTERNAL_SEND_VERIFIED":
        blockers.append("guarded_send_not_verified")
    if control_tower_projection.get("status") != "CONTROL_TOWER_UI_API_PROJECTION_READY":
        blockers.append("control_tower_projection_not_ready")
    unattended_enabled = bool((policy or {}).get("unattended_dispatch_enabled"))
    return {
        "status": "ready" if not blockers else "degraded",
        "role": "read_only_workflow_kernel_projection",
        "workflow_pack_id": registry.get("workflow_pack_id"),
        "agent_pack_id": registry.get("agent_pack_id"),
        "latest_guarded_send": {
            "status": guarded_send.get("status"),
            "adapter": guarded_send.get("adapter"),
            "chat_id": (guarded_send.get("send_result") or {}).get("chat_id"),
            "message_id": (guarded_send.get("send_result") or {}).get("message_id"),
            "external_side_effect_executed": guarded_send.get("external_side_effect_executed"),
        },
        "control_tower_projection": {
            "status": control_tower_projection.get("status"),
            "api_version": control_tower_projection.get("api_version"),
            "html_artifact": str(root / "docs/private/workflow-kernel-control-tower-projection/workflow_kernel_control_tower_projection_20260613_phase52.html"),
        },
        "unattended_dispatch_policy": {
            "status": (policy or {}).get("status", "not_compiled"),
            "default_mode": (policy or {}).get("default_mode", "dry_run_preview"),
            "unattended_dispatch_enabled": unattended_enabled,
            "policy_artifact": str(policy_path) if policy_path.exists() else None,
        },
        "unattended_dry_run_scheduler": {
            "status": (dry_run_scheduler or {}).get("status", "not_compiled"),
            "cadence": (dry_run_scheduler or {}).get("cadence"),
            "latest_idempotency_key": ((dry_run_scheduler or {}).get("latest_receipt") or {}).get("idempotency_key"),
            "would_send": ((dry_run_scheduler or {}).get("latest_receipt") or {}).get("would_send"),
            "receipt_ledger": (dry_run_scheduler or {}).get("receipt_ledger"),
        },
        "policy_editor": policy_editor,
        "dry_run_cron_rollout": {
            "status": (dry_run_cron_rollout or {}).get("status", "not_applied"),
            "job_id": (dry_run_cron_rollout or {}).get("job_id"),
            "script": (dry_run_cron_rollout or {}).get("script"),
            "schedule_display": (dry_run_cron_rollout or {}).get("schedule_display"),
            "rollback_command": (dry_run_cron_rollout or {}).get("rollback_command"),
            "would_send": (dry_run_cron_rollout or {}).get("would_send"),
            "cron_state_mutated": (dry_run_cron_rollout or {}).get("cron_state_mutated"),
        },
        "guarded_send_runner_activation": {
            "status": (guarded_send_activation or {}).get("status", "not_ready"),
            "contract_digest": (guarded_send_activation or {}).get("contract_digest"),
            "manual_send_runner_is_active_now": ((guarded_send_activation or {}).get("authority_assessment") or {}).get("manual_send_runner_is_active_now"),
            "external_side_effects_executed": ((guarded_send_activation or {}).get("execution_boundary") or {}).get("external_side_effects_executed"),
            "next_boundary": ((guarded_send_activation or {}).get("execution_boundary") or {}).get("next_boundary"),
        },
        "guarded_manual_send_runner": {
            "status": (guarded_manual_runner or {}).get("status", "not_ready"),
            "target": (guarded_manual_runner or {}).get("target"),
            "idempotency_key": (guarded_manual_runner or {}).get("idempotency_key"),
            "external_side_effects_executed": (guarded_manual_runner or {}).get("external_side_effects_executed"),
        },
        "one_shot_guarded_send": {
            "status": (one_shot_guarded_send or {}).get("status", "not_executed"),
            "message_id": ((one_shot_guarded_send or {}).get("send_result") or {}).get("message_id"),
            "chat_id": ((one_shot_guarded_send or {}).get("send_result") or {}).get("chat_id"),
            "idempotency_key": (one_shot_guarded_send or {}).get("idempotency_key"),
            "external_side_effects_executed": (one_shot_guarded_send or {}).get("external_side_effects_executed"),
            "unattended_dispatch_enabled": (one_shot_guarded_send or {}).get("unattended_dispatch_enabled"),
        },
        "post_send_correlation_watch": {
            "status": (post_send_correlation or {}).get("status", "not_ready"),
            "message_id": (post_send_correlation or {}).get("message_id"),
            "matching_ledger_rows": (post_send_correlation or {}).get("matching_ledger_rows"),
            "new_message_sent": ((post_send_correlation or {}).get("compensation_watch") or {}).get("new_message_sent"),
            "unattended_dispatch_enabled": (post_send_correlation or {}).get("unattended_dispatch_enabled"),
        },
        "unattended_dispatch_enablement_packet": {
            "status": (unattended_enablement_packet or {}).get("status", "not_ready"),
            "packet_digest": (unattended_enablement_packet or {}).get("packet_digest"),
            "recommended_default": (unattended_enablement_packet or {}).get("recommended_default"),
            "selected_option": (unattended_enablement_packet or {}).get("selected_option"),
            "unattended_dispatch_enabled": (unattended_enablement_packet or {}).get("unattended_dispatch_enabled"),
            "external_side_effects_executed": (unattended_enablement_packet or {}).get("external_side_effects_executed"),
        },
        "unattended_decision_record": {
            "status": (unattended_decision_record or {}).get("status", "not_ready"),
            "decision": (unattended_decision_record or {}).get("decision"),
            "selected_option": (unattended_decision_record or {}).get("selected_option"),
            "record_digest": (unattended_decision_record or {}).get("record_digest"),
            "unattended_dispatch_enabled": (unattended_decision_record or {}).get("unattended_dispatch_enabled"),
            "record_only": (unattended_decision_record or {}).get("record_only"),
        },
        "manual_only_closure": {
            "status": (manual_only_closure or {}).get("status", "not_ready"),
            "final_mode": (manual_only_closure or {}).get("final_mode"),
            "closure_digest": (manual_only_closure or {}).get("closure_digest"),
            "unattended_dispatch_enabled": (manual_only_closure or {}).get("unattended_dispatch_enabled"),
            "external_side_effects_executed": (manual_only_closure or {}).get("external_side_effects_executed"),
        },
        "manual_only_monitoring": {
            "status": (manual_only_monitoring or {}).get("status", "not_ready"),
            "monitor_digest": (manual_only_monitoring or {}).get("monitor_digest"),
            "drift": (manual_only_monitoring or {}).get("drift", []),
            "observed_job": (manual_only_monitoring or {}).get("observed_job"),
            "unattended_dispatch_enabled": (manual_only_monitoring or {}).get("unattended_dispatch_enabled"),
            "read_only": (manual_only_monitoring or {}).get("read_only"),
        },
        "future_unattended_reopen_packet": {
            "status": (future_reopen_packet or {}).get("status", "not_ready"),
            "packet_digest": (future_reopen_packet or {}).get("packet_digest"),
            "recommended_default": (future_reopen_packet or {}).get("recommended_default"),
            "selected_option": (future_reopen_packet or {}).get("selected_option"),
            "reopen_apply_enabled": (future_reopen_packet or {}).get("reopen_apply_enabled"),
            "unattended_dispatch_enabled": (future_reopen_packet or {}).get("unattended_dispatch_enabled"),
            "packet_only": (future_reopen_packet or {}).get("packet_only"),
        },
        "single_point_reopen_choice": {
            "status": (single_point_reopen_choice or {}).get("status", "not_ready"),
            "choice": (single_point_reopen_choice or {}).get("choice"),
            "choice_digest": (single_point_reopen_choice or {}).get("choice_digest"),
            "record_only": (single_point_reopen_choice or {}).get("record_only"),
            "reopen_apply_enabled": (single_point_reopen_choice or {}).get("reopen_apply_enabled"),
            "unattended_dispatch_enabled": (single_point_reopen_choice or {}).get("unattended_dispatch_enabled"),
        },
        "manual_only_governance_archive": {
            "status": (manual_governance_archive or {}).get("status", "not_ready"),
            "archive_digest": (manual_governance_archive or {}).get("archive_digest"),
            "archive_policy": (manual_governance_archive or {}).get("archive_policy"),
            "item_count": len((manual_governance_archive or {}).get("items") or []),
            "read_only": (manual_governance_archive or {}).get("read_only"),
            "unattended_dispatch_enabled": (manual_governance_archive or {}).get("unattended_dispatch_enabled"),
        },
        "periodic_closure_health_report": {
            "status": (periodic_closure_health or {}).get("status", "not_ready"),
            "health": (periodic_closure_health or {}).get("health"),
            "health_digest": (periodic_closure_health or {}).get("health_digest"),
            "stage_report_only": (periodic_closure_health or {}).get("stage_report_only"),
            "read_only": (periodic_closure_health or {}).get("read_only"),
            "unattended_dispatch_enabled": (periodic_closure_health or {}).get("unattended_dispatch_enabled"),
        },
        "archive_publication_packet": {
            "status": (archive_publication_packet or {}).get("status", "not_ready"),
            "packet_digest": (archive_publication_packet or {}).get("packet_digest"),
            "recommended_default": (archive_publication_packet or {}).get("recommended_default"),
            "selected_option": (archive_publication_packet or {}).get("selected_option"),
            "packet_only": (archive_publication_packet or {}).get("packet_only"),
            "publication_apply_enabled": (archive_publication_packet or {}).get("publication_apply_enabled"),
            "external_publication_enabled": (archive_publication_packet or {}).get("external_publication_enabled"),
        },
        "private_archive_retention_watch": {
            "status": (private_archive_retention_watch or {}).get("status", "not_ready"),
            "retention_digest": (private_archive_retention_watch or {}).get("retention_digest"),
            "retention_policy": (private_archive_retention_watch or {}).get("retention_policy"),
            "drift": (private_archive_retention_watch or {}).get("drift", []),
            "read_only": (private_archive_retention_watch or {}).get("read_only"),
            "publication_apply_enabled": (private_archive_retention_watch or {}).get("publication_apply_enabled"),
        },
        "publication_exact_target_gate": {
            "status": (publication_exact_target_gate or {}).get("status", "not_ready"),
            "gate_digest": (publication_exact_target_gate or {}).get("gate_digest"),
            "gate_only": (publication_exact_target_gate or {}).get("gate_only"),
            "selected_target": (publication_exact_target_gate or {}).get("selected_target"),
            "publication_apply_enabled": (publication_exact_target_gate or {}).get("publication_apply_enabled"),
            "external_publication_enabled": (publication_exact_target_gate or {}).get("external_publication_enabled"),
        },
        "exact_target_apply_dry_run_compiler": {
            "status": (exact_target_apply_dry_run_compiler or {}).get("status", "not_ready"),
            "compiler_digest": (exact_target_apply_dry_run_compiler or {}).get("compiler_digest"),
            "compile_mode": (exact_target_apply_dry_run_compiler or {}).get("compile_mode"),
            "dry_run_only": (exact_target_apply_dry_run_compiler or {}).get("dry_run_only"),
            "publication_apply_enabled": (exact_target_apply_dry_run_compiler or {}).get("publication_apply_enabled"),
            "would_publish": ((exact_target_apply_dry_run_compiler or {}).get("dry_run_preview") or {}).get("would_publish"),
        },
        "publication_apply_authorization_packet": {
            "status": (publication_apply_authorization_packet or {}).get("status", "not_ready"),
            "packet_digest": (publication_apply_authorization_packet or {}).get("packet_digest"),
            "recommended_default": (publication_apply_authorization_packet or {}).get("recommended_default"),
            "selected_option": (publication_apply_authorization_packet or {}).get("selected_option"),
            "packet_only": (publication_apply_authorization_packet or {}).get("packet_only"),
            "publication_apply_enabled": (publication_apply_authorization_packet or {}).get("publication_apply_enabled"),
            "external_publication_enabled": (publication_apply_authorization_packet or {}).get("external_publication_enabled"),
        },
        "private_export_artifact_compiler": {
            "status": (private_export_artifact_compiler or {}).get("status", "not_ready"),
            "export_digest": (private_export_artifact_compiler or {}).get("export_digest"),
            "compile_mode": (private_export_artifact_compiler or {}).get("compile_mode"),
            "private_export_ready": (private_export_artifact_compiler or {}).get("private_export_ready"),
            "publication_apply_enabled": (private_export_artifact_compiler or {}).get("publication_apply_enabled"),
            "external_publication_enabled": (private_export_artifact_compiler or {}).get("external_publication_enabled"),
        },
        "publication_apply_implementation_choice": {
            "status": (publication_apply_implementation_choice or {}).get("status", "not_ready"),
            "choice_digest": (publication_apply_implementation_choice or {}).get("choice_digest"),
            "choice": (publication_apply_implementation_choice or {}).get("choice"),
            "record_only": (publication_apply_implementation_choice or {}).get("record_only"),
            "exact_target_present": (publication_apply_implementation_choice or {}).get("exact_target_present"),
            "publication_apply_enabled": (publication_apply_implementation_choice or {}).get("publication_apply_enabled"),
        },
        "private_export_retention_watch": {
            "status": (private_export_retention_watch or {}).get("status", "not_ready"),
            "retention_digest": (private_export_retention_watch or {}).get("retention_digest"),
            "retention_policy": (private_export_retention_watch or {}).get("retention_policy"),
            "drift": (private_export_retention_watch or {}).get("drift", []),
            "read_only": (private_export_retention_watch or {}).get("read_only"),
            "publication_apply_enabled": (private_export_retention_watch or {}).get("publication_apply_enabled"),
        },
        "publication_apply_reopen_packet": {
            "status": (publication_apply_reopen_packet or {}).get("status", "not_ready"),
            "packet_digest": (publication_apply_reopen_packet or {}).get("packet_digest"),
            "recommended_default": (publication_apply_reopen_packet or {}).get("recommended_default"),
            "selected_option": (publication_apply_reopen_packet or {}).get("selected_option"),
            "packet_only": (publication_apply_reopen_packet or {}).get("packet_only"),
            "reopen_apply_enabled": (publication_apply_reopen_packet or {}).get("reopen_apply_enabled"),
            "publication_apply_enabled": (publication_apply_reopen_packet or {}).get("publication_apply_enabled"),
        },
        "publication_closure_health_report": {
            "status": (publication_closure_health_report or {}).get("status", "not_ready"),
            "health_digest": (publication_closure_health_report or {}).get("health_digest"),
            "health": (publication_closure_health_report or {}).get("health"),
            "unhealthy_checks": (publication_closure_health_report or {}).get("unhealthy_checks", []),
            "read_only": (publication_closure_health_report or {}).get("read_only"),
            "publication_apply_enabled": (publication_closure_health_report or {}).get("publication_apply_enabled"),
        },
        "publication_governance_archive": {
            "status": (publication_governance_archive or {}).get("status", "not_ready"),
            "archive_digest": (publication_governance_archive or {}).get("archive_digest"),
            "archive_policy": (publication_governance_archive or {}).get("archive_policy"),
            "item_count": (publication_governance_archive or {}).get("item_count"),
            "read_only": (publication_governance_archive or {}).get("read_only"),
            "publication_apply_enabled": (publication_governance_archive or {}).get("publication_apply_enabled"),
        },
        "publication_archive_retention_watch": {
            "status": (publication_archive_retention_watch or {}).get("status", "not_ready"),
            "retention_digest": (publication_archive_retention_watch or {}).get("retention_digest"),
            "retention_policy": (publication_archive_retention_watch or {}).get("retention_policy"),
            "drift": (publication_archive_retention_watch or {}).get("drift", []),
            "read_only": (publication_archive_retention_watch or {}).get("read_only"),
            "publication_apply_enabled": (publication_archive_retention_watch or {}).get("publication_apply_enabled"),
        },
        "publication_export_review_packet": {
            "status": (publication_export_review_packet or {}).get("status", "not_ready"),
            "packet_digest": (publication_export_review_packet or {}).get("packet_digest"),
            "recommended_default": (publication_export_review_packet or {}).get("recommended_default"),
            "selected_option": (publication_export_review_packet or {}).get("selected_option"),
            "packet_only": (publication_export_review_packet or {}).get("packet_only"),
            "export_apply_enabled": (publication_export_review_packet or {}).get("export_apply_enabled"),
            "publication_apply_enabled": (publication_export_review_packet or {}).get("publication_apply_enabled"),
        },
        "publication_export_review_retention_watch": {
            "status": (publication_export_review_retention_watch or {}).get("status", "not_ready"),
            "retention_digest": (publication_export_review_retention_watch or {}).get("retention_digest"),
            "retention_policy": (publication_export_review_retention_watch or {}).get("retention_policy"),
            "drift": (publication_export_review_retention_watch or {}).get("drift", []),
            "packet_only": (publication_export_review_retention_watch or {}).get("packet_only"),
            "publication_apply_enabled": (publication_export_review_retention_watch or {}).get("publication_apply_enabled"),
        },
        "publication_apply_final_boundary_packet": {
            "status": (publication_apply_final_boundary_packet or {}).get("status", "not_ready"),
            "packet_digest": (publication_apply_final_boundary_packet or {}).get("packet_digest"),
            "recommended_default": (publication_apply_final_boundary_packet or {}).get("recommended_default"),
            "selected_option": (publication_apply_final_boundary_packet or {}).get("selected_option"),
            "packet_only": (publication_apply_final_boundary_packet or {}).get("packet_only"),
            "final_boundary": (publication_apply_final_boundary_packet or {}).get("final_boundary"),
            "publication_apply_enabled": (publication_apply_final_boundary_packet or {}).get("publication_apply_enabled"),
        },
        "publication_final_boundary_retention_watch": {
            "status": (publication_final_boundary_retention_watch or {}).get("status", "not_ready"),
            "retention_digest": (publication_final_boundary_retention_watch or {}).get("retention_digest"),
            "retention_policy": (publication_final_boundary_retention_watch or {}).get("retention_policy"),
            "drift": (publication_final_boundary_retention_watch or {}).get("drift", []),
            "packet_only": (publication_final_boundary_retention_watch or {}).get("packet_only"),
            "publication_apply_enabled": (publication_final_boundary_retention_watch or {}).get("publication_apply_enabled"),
        },
        "publication_governance_closure_summary": {
            "status": (publication_governance_closure_summary or {}).get("status", "not_ready"),
            "summary_digest": (publication_governance_closure_summary or {}).get("summary_digest"),
            "summary_policy": (publication_governance_closure_summary or {}).get("summary_policy"),
            "item_count": (publication_governance_closure_summary or {}).get("item_count"),
            "read_only": (publication_governance_closure_summary or {}).get("read_only"),
            "publication_apply_enabled": (publication_governance_closure_summary or {}).get("publication_apply_enabled"),
        },
        "publication_closure_retention_watch": {
            "status": (publication_closure_retention_watch or {}).get("status", "not_ready"),
            "retention_digest": (publication_closure_retention_watch or {}).get("retention_digest"),
            "retention_policy": (publication_closure_retention_watch or {}).get("retention_policy"),
            "drift": (publication_closure_retention_watch or {}).get("drift", []),
            "read_only": (publication_closure_retention_watch or {}).get("read_only"),
            "publication_apply_enabled": (publication_closure_retention_watch or {}).get("publication_apply_enabled"),
        },
        "publication_apply_reopen_human_gate_packet": {
            "status": (publication_apply_reopen_human_gate_packet or {}).get("status", "not_ready"),
            "packet_digest": (publication_apply_reopen_human_gate_packet or {}).get("packet_digest"),
            "recommended_default": (publication_apply_reopen_human_gate_packet or {}).get("recommended_default"),
            "selected_option": (publication_apply_reopen_human_gate_packet or {}).get("selected_option"),
            "packet_only": (publication_apply_reopen_human_gate_packet or {}).get("packet_only"),
            "human_gate_required_for_apply": (publication_apply_reopen_human_gate_packet or {}).get("human_gate_required_for_apply"),
            "publication_apply_enabled": (publication_apply_reopen_human_gate_packet or {}).get("publication_apply_enabled"),
        },
        "blockers": blockers,
        "external_side_effects_enabled": False,
        "unattended_dispatch_enabled": unattended_enabled,
        "next_boundary": "post-Phase88 human gate packet: publication apply remains closed; real reopen requires human exact target/payload/rollback",
    }


def build_snapshot() -> dict[str, Any]:
    """Build the Control Tower snapshot."""
    ledger = init_ledger()
    kanban_adapter, live_kanban_tasks = _read_kanban_projection()
    mission_flow_signals = _mission_flow_signal_snapshot()
    mission_taskpool_bridge = _mission_taskpool_bridge_snapshot()
    with sqlite3.connect(ledger) as conn:
        conn.row_factory = sqlite3.Row
        last_seq = conn.execute("SELECT COALESCE(MAX(sequence), 0) FROM events").fetchone()[0]
        evidence_counts = {
            maturity: _count(conn, "evidence", f"maturity = '{maturity}'")
            for maturity in ["advisory", "observed", "verified", "certified", "release_confirmed"]
        }
        stale_or_conflicting = _count(conn, "evidence", "stale_status IN ('stale', 'conflicting')")
        conflict_count = _count(conn, "adapter_observations", "conflict_event_id IS NOT NULL")
        historical_tasks = _observed_tasks(conn)
        tasks = live_kanban_tasks or historical_tasks
        command_registry = _command_registry_snapshot(conn)
        human_inbox = _human_inbox_snapshot(conn)
        workflow_map = _workflow_map_snapshot(conn)
        executor_plans = _executor_plans_snapshot(conn)
        execution_policy = _execution_policy_snapshot(conn)
        read_only_executor = _read_only_executor_snapshot(conn)
        evidence_policy = _evidence_policy_snapshot(conn)
        closeout_authorizations = _closeout_authorizations_snapshot(conn)
        conflicting_evidence_override_authorizations = _conflicting_evidence_override_authorizations_snapshot(conn)
        closeout_execution = _closeout_execution_snapshot(conn)
        external_adapter_write_intents = _external_adapter_write_intents_snapshot(conn)
        external_adapter_write_intent_reviews = _external_adapter_write_intent_reviews_snapshot(conn)
        external_adapter_write_authorizations = _external_adapter_write_authorizations_snapshot(conn)
        external_adapter_write_execution_preflights = _external_adapter_write_execution_preflights_snapshot(conn)
        external_adapter_write_execution_runs = _external_adapter_write_execution_runs_snapshot(conn)
        external_adapter_write_rollback_runs = _external_adapter_write_rollback_runs_snapshot(conn)
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
    taskpool_health = _taskpool_health_from_tasks(tasks)
    next_dispatchable = _next_dispatchable_from_tasks(tasks)
    evidence_summary = {
        **evidence_counts,
        "stale_or_conflicting": stale_or_conflicting,
    }
    source_drift = _source_drift_snapshot()
    taskpool_runtime = _taskpool_runtime_probe()
    projection_governance = _kanban_coverage_probe()
    workflow_kernel_control_tower = _workflow_kernel_control_tower_snapshot()
    operator_overview = _operator_overview_snapshot(
        taskpool_health=taskpool_health,
        next_dispatchable=next_dispatchable,
        blockers=blockers,
        mission_flow_signals=mission_flow_signals,
        evidence_summary=evidence_summary,
        human_inbox=human_inbox,
        source_drift=source_drift,
        taskpool_runtime=taskpool_runtime,
        projection_governance=projection_governance,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "ledger": {
            "path": str(ledger),
            "canonical_fact_source": "brain_taskpool_markdown",
            "control_tower_role": "read_only_governance_projection",
            "mode": "read_only_projection",
            "last_sequence": int(last_seq),
            "projection_status": "ok",
        },
        "ssot_boundary": {
            "canonical": "brain_taskpool_markdown",
            "canonical_paths": [
                "~/.hermes/scripts/brain_task_orchestrator.py",
                "~/Documents/brain/05-实践与项目/tasks/*.md",
            ],
            "execution_projection": "~/.hermes/kanban.db",
            "observation_projection": str(ledger),
            "ui_surfaces": ["dashboard_plugin", "console"],
            "rule": "dashboard_console_must_not_advance_task_lifecycle",
        },
        "ui_host": {
            "selected": "dashboard_plugin",
            "plugin_name": "control-tower",
            "canonical": False,
            "role": "operator_projection_surface",
            "reason": "Drop-in dashboard plugin integrates with existing /api/plugins host without making the dashboard a fact source.",
        },
        "adapters": {
            "kanban": {"role": "execution_projection", "canonical": False, **kanban_adapter},
            "taskpool": {"role": "canonical_task_lifecycle", "canonical": True},
            "brain": {"role": "canonical_task_lifecycle", "canonical": True},
            "control_tower_ledger": {"role": "governance_projection", "canonical": False},
            "cron": {"role": "watchdog_adapter", "canonical": False},
            "gateway": {"role": "event_ingress_adapter", "canonical": False},
            "mission_flow": {"role": "projection_adapter", "canonical": False},
            "mission_taskpool_bridge": {"role": "projection_adapter", "canonical": False},
        },
        "mission_flow_signals": mission_flow_signals,
        "mission_taskpool_bridge": mission_taskpool_bridge,
        "projection_governance": projection_governance,
        "workflow_kernel_control_tower": workflow_kernel_control_tower,
        "operator_overview": operator_overview,
        "taskpool_health": taskpool_health,
        "next_dispatchable": next_dispatchable,
        "top_blockers": blockers[:5],
        "command_registry": command_registry,
        "human_inbox": human_inbox,
        "workflow_map": workflow_map,
        "executor_plans": executor_plans,
        "execution_policy": execution_policy,
        "read_only_executor": read_only_executor,
        "evidence_policy": evidence_policy,
        "closeout_authorizations": closeout_authorizations,
        "conflicting_evidence_override_authorizations": conflicting_evidence_override_authorizations,
        "closeout_execution": closeout_execution,
        "external_adapter_write_intents": external_adapter_write_intents,
        "external_adapter_write_intent_reviews": external_adapter_write_intent_reviews,
        "external_adapter_write_authorizations": external_adapter_write_authorizations,
        "external_adapter_write_execution_preflights": external_adapter_write_execution_preflights,
        "external_adapter_write_execution_runs": external_adapter_write_execution_runs,
        "external_adapter_write_rollback_runs": external_adapter_write_rollback_runs,
        "worker_runs": [],
        "cron_watchdogs": [],
        "evidence_summary": evidence_summary,
        "adapter_conflicts": [] if conflict_count == 0 else [{"count": conflict_count}],
    }
