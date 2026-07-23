from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent import chat_completion_helpers
from agent.auxiliary_client import _guarded_chat_completion_create
from agent.iteration_budget import IterationBudget
from agent.tool_guardrails import (
    ToolCallGuardrailConfig,
    ToolCallGuardrailController,
    _result_hash,
)
from agent.conversation_loop import (
    _commit_iteration_graph_budget,
    _consume_iteration_budgets,
    _iteration_budget_available,
    _refund_iteration_budgets,
    _run_codex_app_server_turn_with_budget,
    _run_llm_execution_middleware_with_budget_reconciliation,
)
from agent.codex_runtime import (
    _retire_codex_session,
    run_codex_app_server_turn,
    run_codex_stream,
)
from agent.transports.codex_app_server_session import TurnResult
from hermes_state import SessionDB
from tools.delegate_tool import _resolve_child_relay_policy
from run_agent import AIAgent


def test_graph_budget_is_monotonic_across_turn_budget_reset():
    graph = IterationBudget(2)
    agent = SimpleNamespace(
        iteration_budget=IterationBudget(1),
        graph_budget=graph,
        _budget_grace_call=False,
    )

    assert _consume_iteration_budgets(agent) == (True, "")
    assert graph.used == 1

    # A new root turn replaces only the local budget.
    agent.iteration_budget = IterationBudget(1)
    assert _consume_iteration_budgets(agent) == (True, "")
    assert graph.used == 2

    agent.iteration_budget = IterationBudget(1)
    assert _consume_iteration_budgets(agent) == (False, "graph_budget_exhausted")
    assert agent.iteration_budget.used == 0
    assert graph.used == 2


def test_direct_context_compression_charges_each_physical_dispatch(monkeypatch):
    graph = IterationBudget(1)
    agent = object.__new__(AIAgent)
    agent.graph_budget = graph
    setattr(agent, "_last_budget_reservation", (False, False))
    create = Mock(return_value=SimpleNamespace())
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )

    def compress_context(*args, **kwargs):
        _guarded_chat_completion_create(client, model="summary")
        return args[1], args[2]

    monkeypatch.setattr(
        "agent.conversation_compression.compress_context", compress_context
    )

    AIAgent._compress_context(agent, [], "system")
    assert graph.used == 1
    assert create.call_count == 1

    with pytest.raises(
        RuntimeError, match="graph budget exhausted before auxiliary provider dispatch"
    ):
        AIAgent._compress_context(agent, [], "system")
    assert graph.used == 1
    assert create.call_count == 1


def test_persistent_graph_budget_fork_has_independent_receipt_ownership(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        parent = IterationBudget(3, backend=db, budget_id="graph-1")
        child = parent.fork_handle()

        assert child is not parent
        assert child.budget_id == parent.budget_id
        assert parent.consume() is True
        assert child.consume() is True
        parent.commit_reservation()
        child.refund()

        used, max_total = db.get_execution_graph_budget("graph-1")
        assert (used, max_total) == (1, 3)
        with db._lock:
            conn = db._conn
            assert conn is not None
            states = [
                row["state"]
                for row in conn.execute(
                    "SELECT state FROM execution_graph_reservations "
                    "WHERE graph_id = ? ORDER BY created_at, rowid",
                    ("graph-1",),
                ).fetchall()
            ]
        assert states == ["committed", "refunded"]
        assert parent.persistence_error is None
        assert child.persistence_error is None
    finally:
        db.close()


def test_authoritative_graph_reservation_recovers_after_sibling_refund(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        parent = IterationBudget(1, backend=db, budget_id="shared-refund")
        child = parent.fork_handle()
        agent = SimpleNamespace(
            graph_budget=parent,
            iteration_budget=IterationBudget(1),
            _budget_grace_call=False,
            require_durable_graph_budget=True,
            _graph_budget_persistence_error="",
        )
        assert child.consume() is True
        assert _consume_iteration_budgets(agent) == (
            False,
            "graph_budget_exhausted",
        )
        assert parent.remaining == 0
        with db._lock:
            assert db._conn is not None
            terminal_reason = db._conn.execute(
                "SELECT terminal_reason FROM execution_graph_budgets WHERE id = ?",
                ("shared-refund",),
            ).fetchone()["terminal_reason"]
        assert terminal_reason is None
        child.refund()
        # A delayed terminalization based on the earlier full observation must
        # lose once the sibling refund has restored capacity.
        assert parent.finalize("stale_exhaustion_observation") is False

        assert _iteration_budget_available(agent) is True
        assert _consume_iteration_budgets(agent) == (True, "")
        assert db.get_execution_graph_budget("shared-refund") == (1, 1)
    finally:
        db.close()


def test_committed_graph_exhaustion_persists_terminal_reason(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        owner = IterationBudget(1, backend=db, budget_id="terminal-graph")
        assert owner.consume() is True
        assert owner.commit_reservation() is True
        contender = owner.fork_handle()
        agent = SimpleNamespace(
            graph_budget=contender,
            iteration_budget=IterationBudget(1),
            _budget_grace_call=False,
            require_durable_graph_budget=True,
            _graph_budget_persistence_error="",
        )

        assert _consume_iteration_budgets(agent) == (
            False,
            "graph_budget_exhausted",
        )
        with db._lock:
            assert db._conn is not None
            terminal_reason = db._conn.execute(
                "SELECT terminal_reason FROM execution_graph_budgets WHERE id = ?",
                ("terminal-graph",),
            ).fetchone()["terminal_reason"]
        assert terminal_reason == "graph_budget_exhausted"
        assert contender.consume() is False
    finally:
        db.close()


def test_agent_resume_restores_bound_graph_when_config_is_disabled(
    tmp_path, monkeypatch
):
    db = SessionDB(tmp_path / "state.db")
    try:
        session_id = "resume-disabled-config"
        graph_id = "durable-resume-graph"
        db.create_session(session_id=session_id, source="cli", model="test")
        IterationBudget(3, backend=db, budget_id=graph_id)
        db.bind_session_execution_graph(session_id, graph_id)
        monkeypatch.setattr(
            "hermes_cli.config.load_config",
            lambda: {"convergence": {"max_graph_iterations": 0}},
        )

        agent = AIAgent(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            session_db=db,
            session_id=session_id,
        )

        assert agent.graph_budget is not None
        assert agent.graph_budget.is_persistent is True
        assert agent.graph_budget.budget_id == graph_id
        assert agent.graph_budget.max_total == 3
        assert agent.execution_graph_id == graph_id
    finally:
        db.close()


def test_foreign_handle_cannot_reconcile_leaked_receipt_id(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        owner = IterationBudget(2, backend=db, budget_id="graph-owner")
        sibling = owner.fork_handle()
        assert owner.consume() is True
        leaked_receipt = owner._reservation_ids[-1]

        assert not db.commit_execution_graph_call(
            owner.budget_id, leaked_receipt, sibling._owner_id
        )
        assert (
            db.refund_execution_graph_call(
                owner.budget_id, leaked_receipt, sibling._owner_id
            )
            == 1
        )
        owner.commit_reservation()
        assert db.get_execution_graph_budget(owner.budget_id) == (1, 2)
    finally:
        db.close()


def test_legacy_reservation_table_adds_owner_column_before_first_reserve(tmp_path):
    path = tmp_path / "state.db"
    seeded = SessionDB(db_path=path)
    seeded.close()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE execution_graph_reservations")
        conn.execute(
            """CREATE TABLE execution_graph_reservations (
                   id TEXT PRIMARY KEY,
                   graph_id TEXT NOT NULL,
                   state TEXT NOT NULL,
                   created_at REAL NOT NULL,
                   reconciled_at REAL
               )"""
        )

    migrated = SessionDB(db_path=path)
    try:
        assert migrated._conn is not None
        columns = {
            row["name"]
            for row in migrated._conn.execute(
                "PRAGMA table_info(execution_graph_reservations)"
            ).fetchall()
        }
        assert "owner_id" in columns
        assert migrated.reserve_execution_graph_call("legacy-graph", 1, "owner")
    finally:
        migrated.close()


def test_legacy_ownerless_reserved_row_stays_charged_and_unclaimable(tmp_path):
    path = tmp_path / "state.db"
    seeded = SessionDB(db_path=path)
    seeded.ensure_execution_graph_budget("legacy-ownerless", 2)
    seeded.close()
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE execution_graph_budgets SET used = 1 WHERE id = ?",
            ("legacy-ownerless",),
        )
        conn.execute("DROP TABLE execution_graph_reservations")
        conn.execute(
            """CREATE TABLE execution_graph_reservations (
                   id TEXT PRIMARY KEY,
                   graph_id TEXT NOT NULL,
                   state TEXT NOT NULL,
                   created_at REAL NOT NULL,
                   reconciled_at REAL
               )"""
        )
        conn.execute(
            "INSERT INTO execution_graph_reservations "
            "(id, graph_id, state, created_at) VALUES (?, ?, 'reserved', 1.0)",
            ("legacy-receipt", "legacy-ownerless"),
        )

    migrated = SessionDB(db_path=path)
    try:
        assert not migrated.commit_execution_graph_call(
            "legacy-ownerless", "legacy-receipt", "new-owner"
        )
        assert migrated.refund_execution_graph_call(
            "legacy-ownerless", "legacy-receipt", "new-owner"
        ) == 1
        assert migrated.get_execution_graph_budget("legacy-ownerless") == (1, 2)
        assert migrated._conn is not None
        row = migrated._conn.execute(
            "SELECT owner_id, state FROM execution_graph_reservations WHERE id = ?",
            ("legacy-receipt",),
        ).fetchone()
        assert (row["owner_id"], row["state"]) == ("", "reserved")
    finally:
        migrated.close()


def test_failed_durable_commit_retains_receipt_and_fails_closed():
    class FailingBackend:
        def ensure_execution_graph_budget(self, *_args):
            return 0, 1

        def reserve_execution_graph_call(self, *_args):
            return 1, "receipt-1"

        def commit_execution_graph_call(self, *_args):
            raise OSError("sqlite unavailable")

    graph = IterationBudget(1, backend=FailingBackend(), budget_id="commit-fail")
    assert graph.consume() is True
    assert graph.commit_reservation() is False
    assert graph._reservation_ids == ["receipt-1"]
    assert graph.used == 1
    assert graph.persistence_error == "OSError: sqlite unavailable"


def test_rejected_durable_commit_retains_receipt_and_stays_charged():
    class RejectingBackend:
        def ensure_execution_graph_budget(self, *_args):
            return 0, 1

        def reserve_execution_graph_call(self, *_args):
            return 1, "receipt-rejected"

        def commit_execution_graph_call(self, *_args):
            return False

    graph = IterationBudget(
        1, backend=RejectingBackend(), budget_id="commit-rejected"
    )
    assert graph.consume() is True
    assert graph.commit_reservation() is False
    assert graph._reservation_ids == ["receipt-rejected"]
    assert graph.used == 1
    assert graph.persistence_error == (
        "reservation commit rejected: receipt is not reserved"
    )



def test_middleware_short_circuit_refunds_without_provider_dispatch():
    graph = IterationBudget(1)
    turn_budget = IterationBudget(1)
    agent = SimpleNamespace(
        graph_budget=graph,
        iteration_budget=turn_budget,
        _budget_grace_call=False,
        require_durable_graph_budget=False,
        _graph_budget_persistence_error="",
    )
    assert _consume_iteration_budgets(agent) == (True, "")
    provider_dispatch = Mock()
    synthetic_response = object()

    response = _run_llm_execution_middleware_with_budget_reconciliation(
        agent,
        lambda _kwargs, _next_call, **_metadata: synthetic_response,
        {"model": "test"},
        provider_dispatch,
    )

    assert response is synthetic_response
    provider_dispatch.assert_not_called()
    assert graph.used == 0
    assert turn_budget.used == 0



def test_durable_graph_budget_reservation_is_atomic_across_connections(tmp_path):
    path = tmp_path / "state.db"
    bootstrap = SessionDB(db_path=path)
    bootstrap.close()

    left_db = SessionDB(db_path=path)
    right_db = SessionDB(db_path=path)
    try:
        left = IterationBudget(1, budget_id="root-race", backend=left_db)
        right = IterationBudget(1, budget_id="root-race", backend=right_db)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda budget: budget.consume(), (left, right)))
        assert sorted(results) == [False, True]
        assert left_db.get_execution_graph_budget("root-race")[0] == 1
    finally:
        left_db.close()
        right_db.close()


def test_reopening_graph_cannot_expand_its_original_limit(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        original = IterationBudget(1, budget_id="root-fixed", backend=db)
        assert original.consume() is True
        expanded = IterationBudget(99, budget_id="root-fixed", backend=db)
        assert expanded.max_total == 1
        assert expanded.consume() is False
    finally:
        db.close()


def test_refund_restores_every_budget_consumed_for_a_non_call():
    turn = IterationBudget(2)
    graph = IterationBudget(2)
    agent = SimpleNamespace(iteration_budget=turn, graph_budget=graph)

    assert _consume_iteration_budgets(agent) == (True, "")
    _refund_iteration_budgets(agent, refund_graph=True)

    assert turn.used == 0
    assert graph.used == 0


def test_post_dispatch_retry_refunds_only_the_local_turn_budget():
    turn = IterationBudget(2)
    graph = IterationBudget(2)
    agent = SimpleNamespace(iteration_budget=turn, graph_budget=graph)

    assert _consume_iteration_budgets(agent) == (True, "")
    _commit_iteration_graph_budget(agent)
    _refund_iteration_budgets(agent, refund_graph=True)

    assert turn.used == 0
    assert graph.used == 1


def test_codex_predispatch_failure_refunds_turn_and_graph_reservations():
    turn = IterationBudget(2)
    graph = IterationBudget(2)
    agent = SimpleNamespace(
        iteration_budget=turn,
        graph_budget=graph,
        _run_codex_app_server_turn=Mock(side_effect=RuntimeError("spawn failed")),
    )
    assert _consume_iteration_budgets(agent) == (True, "")

    with pytest.raises(RuntimeError, match="spawn failed"):
        _run_codex_app_server_turn_with_budget(agent, user_message="hello")

    assert turn.used == 0
    assert graph.used == 0


def test_codex_graph_reservation_commits_immediately_before_turn_start(monkeypatch):
    events = []
    session = Mock()

    def _run_turn(*, user_input, dispatch_gate):
        events.append(("startup_complete", user_input))
        assert dispatch_gate() is True
        events.append(("turn_start", user_input))
        raise RuntimeError("provider failed after dispatch")

    session.run_turn.side_effect = _run_turn
    agent = SimpleNamespace(_codex_session=session)
    monkeypatch.setattr(
        "agent.conversation_loop._commit_iteration_graph_budget",
        lambda observed: (events.append(("commit", observed)), True)[1],
    )

    result = run_codex_app_server_turn(
        agent,
        user_message="hello",
        original_user_message="hello",
        messages=[],
        effective_task_id="task",
    )

    assert result["completed"] is False
    assert events == [
        ("startup_complete", "hello"),
        ("commit", agent),
        ("turn_start", "hello"),
    ]


def test_codex_commit_failure_blocks_provider_dispatch(monkeypatch):
    session = Mock()
    provider_dispatches = []

    def _run_turn(*, user_input, dispatch_gate):
        if not dispatch_gate():
            return TurnResult(
                error="graph budget commit failed before Codex provider dispatch"
            )
        provider_dispatches.append(user_input)
        return TurnResult()

    session.run_turn.side_effect = _run_turn
    agent = SimpleNamespace(_codex_session=session)
    monkeypatch.setattr(
        "agent.conversation_loop._commit_iteration_graph_budget",
        lambda _agent: False,
    )

    result = run_codex_app_server_turn(
        agent,
        user_message="hello",
        original_user_message="hello",
        messages=[],
        effective_task_id="task",
    )

    assert result["completed"] is False
    assert "commit failed before Codex provider dispatch" in result["error"]
    session.run_turn.assert_called_once()
    assert provider_dispatches == []


def test_codex_startup_failure_refunds_before_turn_start():
    graph = IterationBudget(1)
    turn_budget = IterationBudget(1)
    session = Mock()
    session.run_turn.return_value = TurnResult(error="startup failed")
    agent = SimpleNamespace(
        _codex_session=session,
        graph_budget=graph,
        iteration_budget=turn_budget,
        _budget_grace_call=False,
        require_durable_graph_budget=False,
        _graph_budget_persistence_error="",
    )
    assert _consume_iteration_budgets(agent) == (True, "")

    result = run_codex_app_server_turn(
        agent,
        user_message="hello",
        original_user_message="hello",
        messages=[],
        effective_task_id="task",
    )

    assert result["completed"] is False
    assert graph.used == 0
    assert turn_budget.used == 0
    gate = session.run_turn.call_args.kwargs["dispatch_gate"]
    assert callable(gate)


def test_codex_raised_startup_failure_refunds_before_turn_start():
    graph = IterationBudget(1)
    turn_budget = IterationBudget(1)
    session = Mock()
    session.run_turn.side_effect = RuntimeError("startup exploded")
    session.close.return_value = True
    agent = SimpleNamespace(
        _codex_session=session,
        graph_budget=graph,
        iteration_budget=turn_budget,
        _budget_grace_call=False,
        require_durable_graph_budget=False,
        _graph_budget_persistence_error="",
    )
    assert _consume_iteration_budgets(agent) == (True, "")

    result = run_codex_app_server_turn(
        agent,
        user_message="hello",
        original_user_message="hello",
        messages=[],
        effective_task_id="task",
    )

    assert result["completed"] is False
    assert result["api_calls"] == 0
    assert graph.used == 0
    assert turn_budget.used == 0


def test_codex_unconfirmed_retirement_blocks_reuse_and_refunds_fresh_budget():
    graph = IterationBudget(1)
    turn_budget = IterationBudget(1)
    session = Mock()
    session.close.return_value = False
    agent = SimpleNamespace(
        _codex_session=session,
        _codex_session_retirement_pending=True,
        graph_budget=graph,
        iteration_budget=turn_budget,
        _budget_grace_call=False,
        require_durable_graph_budget=False,
        _graph_budget_persistence_error="",
    )
    assert _consume_iteration_budgets(agent) == (True, "")

    result = run_codex_app_server_turn(
        agent,
        user_message="hello",
        original_user_message="hello",
        messages=[],
        effective_task_id="task",
    )

    assert result["completed"] is False
    assert result["error"] == "Codex app-server retirement is still unconfirmed"
    session.close.assert_called_once()
    session.run_turn.assert_not_called()
    assert agent._codex_session is session
    assert agent._codex_session_retirement_pending is True
    assert graph.used == 0
    assert turn_budget.used == 0



def test_second_physical_dispatch_requires_a_fresh_graph_admission():
    first_client = Mock()
    first_response = object()
    first_client.chat.completions.create.return_value = first_response
    second_client = Mock()
    clients = iter((first_client, second_client))
    graph = IterationBudget(1)
    agent = SimpleNamespace(
        api_mode="chat_completions",
        provider="openai",
        _interrupt_requested=False,
        verbose_logging=False,
        iteration_budget=IterationBudget(2),
        graph_budget=graph,
        _budget_grace_call=False,
        require_durable_graph_budget=False,
        _compute_non_stream_stale_timeout=lambda *_args, **_kwargs: 5.0,
        _create_request_openai_client=lambda **_kwargs: next(clients),
        _close_request_openai_client=lambda *_args, **_kwargs: None,
        _abort_request_openai_client=lambda *_args, **_kwargs: None,
        _touch_activity=lambda *_args, **_kwargs: None,
    )
    assert _consume_iteration_budgets(agent) == (True, "")
    dispatch_gate = lambda: _commit_iteration_graph_budget(agent)

    assert chat_completion_helpers.interruptible_api_call(
        agent,
        {"model": "test", "messages": []},
        dispatch_gate=dispatch_gate,
    ) is first_response

    with pytest.raises(RuntimeError, match="graph budget exhausted"):
        chat_completion_helpers.interruptible_api_call(
            agent,
            {"model": "test", "messages": []},
            dispatch_gate=dispatch_gate,
        )

    first_client.chat.completions.create.assert_called_once_with(
        model="test", messages=[]
    )
    second_client.chat.completions.create.assert_not_called()
    assert graph.used == 1


def test_codex_transport_retry_is_blocked_without_second_admission():
    client = Mock()
    client.responses.create.side_effect = [
        ConnectionError("retry me"),
        SimpleNamespace(output=[]),
    ]
    agent = SimpleNamespace(
        _interrupt_requested=False,
        _codex_streamed_text_parts=[],
        _client_log_context=lambda: "test",
        _fire_stream_delta=lambda _text: None,
        _fire_reasoning_delta=lambda _text: None,
        _touch_activity=lambda _status: None,
    )
    dispatch_gate = Mock(side_effect=(True, False))

    with pytest.raises(RuntimeError, match="graph budget exhausted"):
        run_codex_stream(
            agent,
            {"model": "test"},
            client=client,
            dispatch_gate=dispatch_gate,
        )

    assert dispatch_gate.call_count == 2
    client.responses.create.assert_called_once_with(model="test", stream=True)


def test_codex_runtime_retirement_preserves_failed_close_ownership():
    session = Mock()
    session.close.return_value = False
    agent = SimpleNamespace(_codex_session=session)

    assert _retire_codex_session(agent, reason="turn_failed") is False
    assert agent._codex_session is session


def test_codex_runtime_retirement_uses_agent_retry_aware_close_hook():
    session = Mock()
    close_hook = Mock()
    agent = SimpleNamespace(
        _codex_session=session,
        _close_codex_session=close_hook,
    )

    _retire_codex_session(agent, reason="turn_failed")

    close_hook.assert_called_once_with(reason="turn_failed")
    session.close.assert_not_called()


def test_grace_call_refund_does_not_mint_local_turn_capacity():
    turn = IterationBudget(2)
    assert turn.consume() is True
    graph = IterationBudget(2)
    agent = SimpleNamespace(
        iteration_budget=turn,
        graph_budget=graph,
        _budget_grace_call=True,
    )

    assert _consume_iteration_budgets(agent) == (True, "")
    _refund_iteration_budgets(agent, refund_graph=True)

    assert turn.used == 1
    assert graph.used == 0


def test_required_durable_graph_budget_fails_closed_before_dispatch():
    agent = SimpleNamespace(
        iteration_budget=IterationBudget(2),
        graph_budget=IterationBudget(2),
        require_durable_graph_budget=True,
        _budget_grace_call=False,
    )

    assert _consume_iteration_budgets(agent) == (
        False,
        "graph_budget_persistence_failed",
    )
    assert agent.iteration_budget.used == 0
    assert agent.graph_budget.used == 0


def test_durable_refund_is_owned_and_exactly_once(tmp_path):
    path = tmp_path / "state.db"
    owner_db = SessionDB(db_path=path)
    stranger_db = SessionDB(db_path=path)
    try:
        owner = IterationBudget(2, backend=owner_db, budget_id="graph-owned")
        stranger = IterationBudget(2, backend=stranger_db, budget_id="graph-owned")
        assert owner.consume() is True

        # An object that did not reserve the slot cannot refund it.
        stranger.refund()
        assert owner_db.get_execution_graph_budget("graph-owned")[0] == 1

        owner.refund()
        owner.refund()
        assert owner_db.get_execution_graph_budget("graph-owned")[0] == 0
    finally:
        owner_db.close()
        stranger_db.close()


def test_committed_durable_reservation_cannot_be_refunded(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        receipt = db.reserve_execution_graph_call(
            "graph-committed", 2, "owner"
        )
        assert receipt is not None
        used, receipt_id = receipt
        assert used == 1
        assert db.commit_execution_graph_call(
            "graph-committed", receipt_id, "owner"
        )
        assert not db.commit_execution_graph_call(
            "graph-committed", receipt_id, "owner"
        )
        assert (
            db.refund_execution_graph_call(
                "graph-committed", receipt_id, "owner"
            )
            == 1
        )
    finally:
        db.close()


def test_persistent_graph_budget_survives_a_new_budget_object(tmp_path):
    db_path = tmp_path / "state.db"
    db = SessionDB(db_path=db_path)
    first = IterationBudget(2, backend=db, budget_id="graph-1")
    assert first.consume() is True
    db.close()

    resumed_db = SessionDB(db_path=db_path)
    resumed = IterationBudget(2, backend=resumed_db, budget_id="graph-1")
    assert resumed.used == 1
    assert resumed.consume() is True
    assert resumed.consume() is False
    resumed_db.close()


def test_session_lineage_resolves_the_same_graph_budget(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(session_id="parent", source="cli")
    db.ensure_execution_graph_budget("graph-1", 4)
    db.bind_session_execution_graph("parent", "graph-1")
    db.create_session(
        session_id="child", source="cli", parent_session_id="parent"
    )

    assert db.resolve_session_execution_graph("child") == "graph-1"
    assert db.resolve_session_execution_graph("parent") == "graph-1"
    db.close()


def test_child_relay_is_non_expansive_by_default():
    parent = SimpleNamespace(auto_relay_enabled=True, max_relay_count=12)
    enabled, cap = _resolve_child_relay_policy(parent, {})
    assert enabled is False
    assert cap == 0


def test_child_relay_requires_explicit_opt_in_and_is_capped():
    parent = SimpleNamespace(auto_relay_enabled=True, max_relay_count=12)
    enabled, cap = _resolve_child_relay_policy(
        parent,
        {"child_auto_relay": True, "child_max_relay_count": 2},
    )
    assert enabled is True
    assert cap == 2


def test_all_history_stall_tracking_requires_a_finite_graph_budget():
    configured = ToolCallGuardrailConfig(
        hard_stop_enabled=True,
        graph_no_progress_halt_after=3,
    )

    disabled = configured.bounded_for_execution_graph(0)
    enabled = configured.bounded_for_execution_graph(12)

    assert disabled.graph_no_progress_halt_after == 0
    assert disabled.graph_fingerprint_capacity == 0
    assert enabled is not configured
    assert enabled.graph_no_progress_halt_after == 3
    assert enabled.graph_fingerprint_capacity == configured.graph_history_size

    explicit = ToolCallGuardrailConfig(
        hard_stop_enabled=True,
        graph_no_progress_halt_after=3,
        graph_fingerprint_capacity=17,
    ).bounded_for_execution_graph(100)
    assert explicit.graph_fingerprint_capacity == 17


def test_graph_seen_set_survives_root_turn_reset_and_halts_replay():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            graph_no_progress_halt_after=2,
            graph_history_size=32,
        )
    )
    args = {"path": "/tmp/evidence.txt"}

    first = controller.after_call("read_file", args, "same evidence", failed=False)
    assert not first.should_halt

    controller.reset_for_turn()
    second = controller.after_call("read_file", args, "same evidence", failed=False)
    assert not second.should_halt

    controller.reset_for_turn()
    third = controller.after_call("read_file", args, "same evidence", failed=False)
    assert third.should_halt
    assert third.code == "graph_no_progress_halt"
    assert controller.graph_progress_snapshot()["seen_count"] == 1
    assert controller.graph_progress_snapshot()["no_progress_streak"] == 2


def test_graph_seen_set_keeps_full_hash_history_beyond_detail_lru():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            graph_no_progress_halt_after=2,
            graph_history_size=1,
            graph_fingerprint_capacity=2,
        )
    )
    args = {"path": "/tmp/evidence.txt"}

    controller.after_call("read_file", args, "evidence-a", failed=False)
    controller.after_call("read_file", args, "evidence-b", failed=False)
    repeated = controller.after_call("read_file", args, "evidence-a", failed=False)

    assert not repeated.should_halt
    snapshot = controller.graph_progress_snapshot()
    assert snapshot["seen_count"] == 2
    assert snapshot["recent_detail_count"] == 1
    assert snapshot["no_progress_streak"] == 1


def test_graph_fingerprint_capacity_halts_before_history_can_grow_unbounded():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            graph_no_progress_halt_after=10,
            graph_history_size=1,
            graph_fingerprint_capacity=2,
        )
    )
    args = {"path": "/stable/evidence.json"}

    assert not controller.after_call("read_file", args, "a", failed=False).should_halt
    assert not controller.after_call("read_file", args, "b", failed=False).should_halt
    exhausted = controller.after_call("read_file", args, "c", failed=False)

    assert exhausted.should_halt
    assert exhausted.code == "graph_history_capacity_halt"
    snapshot = controller.graph_progress_snapshot()
    assert snapshot["seen_count"] == 2
    assert snapshot["fingerprint_capacity"] == 2


def test_graph_novelty_ignores_volatile_fields_and_is_shared_with_child():
    config = ToolCallGuardrailConfig(
        hard_stop_enabled=True,
        graph_no_progress_halt_after=2,
        graph_history_size=8,
    )
    parent = ToolCallGuardrailController(config)
    child = ToolCallGuardrailController(config)
    child.share_graph_state_from(parent)
    args = {"path": "/tmp/evidence.json"}

    first = '{"status":"ok","timestamp":"2026-07-22T10:00:00Z","request_id":"11111111-1111-4111-8111-111111111111"}'
    second = '{"status":"ok","timestamp":"2026-07-22T10:01:00Z","request_id":"22222222-2222-4222-8222-222222222222"}'

    assert not parent.after_call("read_file", args, first, failed=False).should_halt
    assert not child.after_call("read_file", args, second, failed=False).should_halt
    decision = child.after_call("read_file", args, second, failed=False)

    assert decision.should_halt
    assert parent.graph_progress_snapshot()["seen_count"] == 1
    assert parent.graph_progress_snapshot()["halted"] is True
    assert child.graph_progress_snapshot()["no_progress_streak"] == 2
    parent_block = parent.before_call("read_file", {"path": "/new/evidence.json"})
    assert parent_block.should_halt
    assert parent_block.code == "graph_no_progress_halt"


def test_material_duration_and_named_tmp_paths_remain_novel():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            graph_no_progress_halt_after=2,
            graph_history_size=8,
        )
    )

    first = controller.after_call(
        "read_file",
        {"path": "/tmp/tmp-production/evidence.json"},
        '{"status":"ok","duration":10,"latency":10}',
        failed=False,
    )
    second = controller.after_call(
        "read_file",
        {"path": "/tmp/tmp-staging/evidence.json"},
        '{"status":"ok","duration":20,"latency":20}',
        failed=False,
    )

    assert not first.should_halt
    assert not second.should_halt
    snapshot = controller.graph_progress_snapshot()
    assert snapshot["seen_count"] == 2
    assert snapshot["no_progress_streak"] == 0


def test_material_result_change_resets_graph_no_progress_streak():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            graph_no_progress_halt_after=2,
            graph_history_size=8,
        )
    )
    args = {"path": "/tmp/evidence.json"}

    controller.after_call("read_file", args, '{"status":"ok"}', failed=False)
    controller.after_call("read_file", args, '{"status":"ok"}', failed=False)
    changed = controller.after_call("read_file", args, '{"status":"failed"}', failed=False)

    assert not changed.should_halt
    assert controller.graph_progress_snapshot()["no_progress_streak"] == 0
    assert controller.graph_progress_snapshot()["seen_count"] == 2


def test_graph_novelty_ignores_ephemeral_temp_directory_paths():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            graph_no_progress_halt_after=2,
            graph_history_size=8,
        )
    )
    args = {"path": "/stable/request.json"}
    first = '{"status":"ok","artifact":"/tmp/tmp-a1b2c3/result.json"}'
    second = (
        '{"status":"ok","artifact":'
        '"/private/var/folders/ab/cdef1234/T/tmp-z9y8x7/result.json"}'
    )

    assert not controller.after_call("read_file", args, first, failed=False).should_halt
    assert not controller.after_call("read_file", args, second, failed=False).should_halt
    repeated = controller.after_call("read_file", args, second, failed=False)

    assert repeated.should_halt
    snapshot = controller.graph_progress_snapshot()
    assert snapshot["seen_count"] == 1
    assert snapshot["recent_detail_count"] == 1
    assert snapshot["no_progress_streak"] == 2
    assert snapshot["halted"] is True


def test_graph_novelty_ignores_ephemeral_temp_directory_tool_arguments():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            graph_no_progress_halt_after=2,
            graph_history_size=8,
        )
    )
    result = '{"status":"ok","evidence":"unchanged"}'

    first = controller.after_call(
        "read_file", {"path": "/tmp/tmp-a1b2c3/evidence.json"}, result, failed=False
    )
    second = controller.after_call(
        "read_file",
        {
            "path": (
                "/private/var/folders/ab/cdef1234/T/"
                "tmp-z9y8x7/evidence.json"
            )
        },
        result,
        failed=False,
    )
    repeated = controller.after_call(
        "read_file", {"path": "/private/tmp/tmp-q1w2e3/evidence.json"}, result,
        failed=False,
    )

    assert not first.should_halt
    assert not second.should_halt
    assert repeated.should_halt
    snapshot = controller.graph_progress_snapshot()
    assert snapshot["seen_count"] == 1
    assert snapshot["no_progress_streak"] == 2



def test_graph_novelty_ignores_nested_pytest_temp_directories():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            graph_no_progress_halt_after=2,
            graph_history_size=8,
        )
    )
    result = '{"status":"ok","evidence":"unchanged"}'
    root = "/private/var/folders/ab/cdef1234/T/pytest-of-user"

    first = controller.after_call(
        "read_file",
        {"path": f"{root}/pytest-0/test_x0/out.json"},
        result,
        failed=False,
    )
    second = controller.after_call(
        "read_file",
        {"path": f"{root}/pytest-1/test_x0/out.json"},
        result,
        failed=False,
    )
    repeated = controller.after_call(
        "read_file",
        {"path": f"{root}/pytest-2/test_x0/out.json"},
        result,
        failed=False,
    )

    assert not first.should_halt
    assert not second.should_halt
    assert repeated.should_halt
    snapshot = controller.graph_progress_snapshot()
    assert snapshot["seen_count"] == 1
    assert snapshot["no_progress_streak"] == 2



def test_graph_novelty_ignores_volatile_tool_argument_fields():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            graph_no_progress_halt_after=2,
            graph_history_size=8,
        )
    )
    result = '{"status":"ok","evidence":"unchanged"}'

    first = controller.after_call(
        "read_file",
        {"path": "/stable/evidence.json", "request_id": "11111111-1111-4111-8111-111111111111"},
        result,
        failed=False,
    )
    second = controller.after_call(
        "read_file",
        {"path": "/stable/evidence.json", "requestId": "22222222-2222-4222-8222-222222222222"},
        result,
        failed=False,
    )
    repeated = controller.after_call(
        "read_file",
        {"path": "/stable/evidence.json", "request_id": "33333333-3333-4333-8333-333333333333"},
        result,
        failed=False,
    )

    assert not first.should_halt
    assert not second.should_halt
    assert repeated.should_halt
    snapshot = controller.graph_progress_snapshot()
    assert snapshot["seen_count"] == 1
    assert snapshot["no_progress_streak"] == 2


def test_graph_novelty_ignores_camel_case_volatile_result_fields():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            graph_no_progress_halt_after=2,
            graph_history_size=8,
        )
    )
    args = {"path": "/stable/evidence.json"}
    first = '{"status":"ok","requestId":"11111111-1111-4111-8111-111111111111"}'
    second = '{"status":"ok","requestId":"22222222-2222-4222-8222-222222222222"}'

    assert not controller.after_call("read_file", args, first, failed=False).should_halt
    assert not controller.after_call("read_file", args, second, failed=False).should_halt
    repeated = controller.after_call("read_file", args, second, failed=False)

    assert repeated.should_halt
    snapshot = controller.graph_progress_snapshot()
    assert snapshot["seen_count"] == 1
    assert snapshot["no_progress_streak"] == 2


@pytest.mark.parametrize(
    ("left", "right"),
    (
        ('{"execution_time_limit":10}', '{"execution_time_limit":20}'),
        (
            '{"resource_uuid":"11111111-1111-4111-8111-111111111111"}',
            '{"resource_uuid":"22222222-2222-4222-8222-222222222222"}',
        ),
        ('{"request_id":"invoice-123"}', '{"request_id":"invoice-456"}'),
        (
            'completed resource 11111111-1111-4111-8111-111111111111',
            'completed resource 22222222-2222-4222-8222-222222222222',
        ),
    ),
)
def test_graph_novelty_preserves_material_business_payloads(left, right):
    assert _result_hash(left) != _result_hash(right)


def test_graph_novelty_ignores_nested_and_multisegment_temp_directories():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            graph_no_progress_halt_after=2,
            graph_history_size=8,
        )
    )
    result = '{"status":"ok","evidence":"unchanged"}'
    paths = (
        "/tmp/pytest-of-user/pytest-1/test_case/evidence.json",
        "/tmp/pytest-of-user/pytest-2/test_case/evidence.json",
        "/tmp/tmp-1234-abc/test_case/evidence.json",
    )

    decisions = [
        controller.after_call("read_file", {"path": path}, result, failed=False)
        for path in paths
    ]

    assert not decisions[0].should_halt
    assert not decisions[1].should_halt
    assert decisions[2].should_halt
    snapshot = controller.graph_progress_snapshot()
    assert snapshot["seen_count"] == 1
    assert snapshot["no_progress_streak"] == 2


@pytest.mark.parametrize("prefix", ("node-test", "jest", "vitest"))
def test_graph_novelty_ignores_node_test_temp_directories(prefix):
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            graph_no_progress_halt_after=2,
            graph_history_size=8,
        )
    )
    result = '{"status":"ok","evidence":"unchanged"}'

    decisions = [
        controller.after_call(
            "read_file",
            {"path": f"/tmp/{prefix}-{index}/test_case/evidence.json"},
            result,
            failed=False,
        )
        for index in (111, 222, 333)
    ]

    assert not decisions[0].should_halt
    assert not decisions[1].should_halt
    assert decisions[2].should_halt
    assert controller.graph_progress_snapshot()["seen_count"] == 1


def test_temp_path_normalization_preserves_material_argument_leaf_changes():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            graph_no_progress_halt_after=2,
            graph_history_size=8,
        )
    )
    result = '{"status":"ok","evidence":"unchanged"}'

    controller.after_call(
        "read_file", {"path": "/tmp/tmp-a1b2c3/evidence-a.json"}, result,
        failed=False,
    )
    changed = controller.after_call(
        "read_file", {"path": "/tmp/tmp-z9y8x7/evidence-b.json"}, result,
        failed=False,
    )

    assert not changed.should_halt
    assert controller.graph_progress_snapshot()["seen_count"] == 2
    assert controller.graph_progress_snapshot()["no_progress_streak"] == 0


def test_temp_path_normalization_preserves_material_leaf_changes():
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            graph_no_progress_halt_after=2,
            graph_history_size=8,
        )
    )
    args = {"path": "/stable/request.json"}

    controller.after_call(
        "read_file", args, "wrote /tmp/tmp-a1b2c3/result-a.json", failed=False
    )
    changed = controller.after_call(
        "read_file", args, "wrote /tmp/tmp-z9y8x7/result-b.json", failed=False
    )

    assert not changed.should_halt
    assert controller.graph_progress_snapshot()["seen_count"] == 2
    assert controller.graph_progress_snapshot()["no_progress_streak"] == 0


def test_graph_novelty_and_sticky_halt_survive_controller_reconstruction(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        IterationBudget(4, backend=db, budget_id="durable-novelty")
        config = ToolCallGuardrailConfig(
            hard_stop_enabled=True,
            graph_no_progress_halt_after=2,
            graph_history_size=4,
            graph_fingerprint_capacity=4,
        )
        first = ToolCallGuardrailController(config)
        first.bind_durable_graph(db, "durable-novelty")
        assert first.after_call("read_file", {"path": "a"}, "same").action == "allow"
        second = first.after_call("read_file", {"path": "a"}, "same")
        assert second.should_halt is False

        resumed = ToolCallGuardrailController(config)
        resumed.bind_durable_graph(db, "durable-novelty")
        halted = resumed.after_call("read_file", {"path": "a"}, "same")
        assert halted.code == "graph_no_progress_halt"
        assert halted.count == 2

        reconstructed = ToolCallGuardrailController(config)
        reconstructed.bind_durable_graph(db, "durable-novelty")
        sticky = reconstructed.before_call("read_file", {"path": "different"})
        assert sticky.code == "graph_no_progress_halt"
        assert sticky.allows_execution is False
    finally:
        db.close()


def _minimal_agent_for_close() -> AIAgent:
    agent = AIAgent.__new__(AIAgent)
    agent.session_id = "test-session"
    agent._active_children_lock = Mock()
    agent._active_children_lock.__enter__ = Mock(return_value=None)
    agent._active_children_lock.__exit__ = Mock(return_value=False)
    agent._active_children = []
    agent.client = None
    agent._session_messages = []
    agent._end_session_on_close = True
    agent._session_db = Mock()
    return agent


def test_agent_close_closes_codex_before_finalizing_session():
    agent = _minimal_agent_for_close()
    codex = Mock()
    agent._codex_session = codex

    agent.close()

    codex.close.assert_called_once_with()
    assert agent._codex_session is None
    agent._session_db.end_session.assert_called_once_with("test-session", "agent_close")


def test_codex_close_failure_keeps_retry_handle_without_skipping_finalization():
    agent = _minimal_agent_for_close()
    codex = Mock()
    codex.close.side_effect = [RuntimeError("teardown failed"), None]
    agent._codex_session = codex

    agent.close()

    codex.close.assert_called_once_with()
    assert agent._codex_session is codex
    agent._session_db.end_session.assert_called_once_with("test-session", "agent_close")

    agent.close()
    assert codex.close.call_count == 2
    assert agent._codex_session is None
