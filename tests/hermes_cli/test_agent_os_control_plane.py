from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from hermes_cli.agent_os_control_plane import (
    authorize_local_harness,
    bootstrap_erg_runtime_state,
    canonical_schema_status,
    doctor,
    evidence_projection_status,
    execute_local_harness,
    generate_replay_quality_report,
    read_only_runtime_audit,
    replay_recent_user_intents,
    route_shadow,
    status,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_agent_os_canonical_docs(home: Path) -> dict[str, str]:
    docs_root = home / "docs" / "private" / "autonomous-runtime-governance"
    docs_root.mkdir(parents=True, exist_ok=True)
    domain_model = docs_root / "04-core-domain-model.md"
    domain_model.write_text(
        "\n".join(
            [
                "# Core Domain Model",
                "## IntentRecord",
                "## CapabilityNode",
                "## AgentWrapper",
                "## AuthorityGrant",
                "## ExecutionLease",
                "## EvidenceNode",
                "## GateDecision",
                "## ProjectionRecord",
                "## CompensationRecord",
                "## ClosureRecord",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    authority = docs_root / "07-authority-and-side-effect-governance.md"
    authority.write_text("S0 S1 S2 S3 S4 S5 S6 S7\n", encoding="utf-8")
    schema = home / "workflows" / "projects" / "hermes-agent-os-control-plane" / "01-architecture" / "agent-os-schemas.json"
    _write_json(
        schema,
        {
            "side_effect_levels": {f"S{i}": f"level {i}" for i in range(8)},
            "capability_node_required": ["id", "kind", "responsibility", "side_effects", "source_ref", "evidence_requirements"],
            "evidence_node_required": ["id", "kind", "claim_ids", "trust_level", "freshness", "verification_status"],
            "closure_record_required": ["id", "intent_id", "capability_node_id", "status", "claims", "evidence_refs", "next_boundary"],
            "authority_grant_required": ["id", "granted_by", "scope", "duration", "audit"],
            "default_off_side_effects": ["S4", "S5", "S6", "S7"],
        },
    )
    return {
        "autonomous_runtime_governance_domain_model": str(domain_model),
        "autonomous_runtime_governance_authority": str(authority),
        "schema": str(schema),
    }


def _seed_agent_os(home: Path, *, runtime_mutation: bool = False, shadow_executes: bool = False) -> None:
    project = "hermes-agent-os-control-plane"
    root = home / "workflows" / "projects" / project
    _write_json(
        home / "project-profiles" / f"{project}.json",
        {
            "project": project,
            "status": "p0-read-only-projection-validated+p1-sample-shadow-validated",
            "ssot_root": str(root),
            "progress_ledger": str(root / "progress-ledger.md"),
            "canonical_docs": _write_agent_os_canonical_docs(home),
            "current_components": {
                "runtime_implementation": "advisory_readback_only/default-off",
                "capability_graph_projection": "validated-local-read-only-slice/2-nodes",
                "capability_intent": "shadow-only",
            },
        },
    )
    _write_json(
        root / "06-projection" / "capability_graph_projection.json",
        {
            "project": project,
            "runtime_mutation": runtime_mutation,
            "nodes": [
                {
                    "id": "tool:send_message",
                    "kind": "tool",
                    "responsibility": "Send messages to connected platforms",
                    "side_effects": "S4",
                    "source_ref": "test",
                },
                {
                    "id": "skill:hermes-agent",
                    "kind": "skill",
                    "responsibility": "Operate Hermes Agent control plane",
                    "side_effects": "S0",
                    "source_ref": "test",
                },
            ],
        },
    )
    _write_json(root / "06-projection" / "doctor_report.json", {"ok": True, "findings": []})
    _write_json(
        root / "07-shadow" / "router-shadow-report.json",
        {"ok": True, "records": 1, "blocked_by_policy": 0, "would_execute_anything": shadow_executes},
    )


def test_agent_os_status_reads_projection_without_runtime_mutation(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_advisory_guidance": True, "agent_os_runtime_routing": True, "agent_os_multimodel_review": True, "agent_os_local_harness_executor": True, "agent_os_scheduler_cron_write": True, "agent_os_taskpool_lifecycle_write": True, "agent_os_external_dispatch": True, "agent_os_publish_deploy_push": True, "agent_os_destructive_operations": True, "agent_os_payment_credential_entry": True, "agent_os_broad_fanout": True}})
    payload = status(tmp_path)
    assert payload["ok"] is True
    assert payload["mode"] == "advisory_readback_only"
    assert payload["projection"]["node_count"] == 2
    assert payload["config"]["agent_os_advisory_guidance"] is True
    assert payload["config"]["real_runtime_routing"] is True
    assert payload["config"]["agent_os_multimodel_review"] is True
    assert payload["config"]["agent_os_local_harness_executor"] is True
    assert payload["config"]["agent_os_scheduler_cron_write"] is True
    assert payload["config"]["tool_invocation_from_agent_os"] is True
    assert payload["config"]["scheduler_cron_write"] is True
    assert payload["config"]["agent_os_taskpool_lifecycle_write"] is True
    assert payload["config"]["taskpool_mission_lifecycle_write"] is True
    assert payload["config"]["agent_os_external_dispatch"] is True
    assert payload["config"]["external_dispatch"] is True
    assert payload["config"]["agent_os_publish_deploy_push"] is True
    assert payload["config"]["publish_deploy_push"] is True
    assert payload["config"]["agent_os_destructive_operations"] is True
    assert payload["config"]["destructive_operations"] is True
    assert payload["config"]["agent_os_payment_credential_entry"] is True
    assert payload["config"]["payment_credential_entry"] is True
    assert payload["config"]["agent_os_broad_fanout"] is True
    assert payload["config"]["broad_fanout"] is True
    assert payload["runtime_effect"].startswith("broad fan-out enabled")


def test_agent_os_status_defaults_multimodel_review_and_execution_gates_closed(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {}})

    payload = status(tmp_path)

    assert payload["config"]["agent_os_multimodel_review"] is False
    assert payload["config"]["real_runtime_routing"] is False
    assert payload["config"]["tool_invocation_from_agent_os"] is False
    assert payload["config"]["scheduler_cron_write"] is False
    assert payload["config"]["taskpool_mission_lifecycle_write"] is False
    assert payload["config"]["agent_os_external_dispatch"] is False
    assert payload["config"]["external_dispatch"] is False
    assert payload["config"]["agent_os_publish_deploy_push"] is False
    assert payload["config"]["publish_deploy_push"] is False
    assert payload["config"]["agent_os_destructive_operations"] is False
    assert payload["config"]["destructive_operations"] is False
    assert payload["config"]["agent_os_payment_credential_entry"] is False
    assert payload["config"]["payment_credential_entry"] is False
    assert payload["config"]["agent_os_broad_fanout"] is False
    assert payload["config"]["broad_fanout"] is False
    assert payload["runtime_effect"].startswith("none")


def test_agent_os_canonical_schema_status_validates_arg_domain_model_and_schema(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path)

    payload = canonical_schema_status(tmp_path)

    assert payload["ok"] is True
    assert payload["mode"] == "canonical_schema_read_only"
    assert payload["runtime_mutation"] is False
    assert payload["domain_model"]["missing_sections"] == []
    assert payload["agent_os_schema"]["missing_side_effect_levels"] == []
    assert payload["agent_os_schema"]["default_off_side_effects"] == ["S4", "S5", "S6", "S7"]
    assert payload["findings"] == []


def test_agent_os_canonical_schema_status_fails_closed_on_missing_domain_section(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path)
    profile_path = tmp_path / "project-profiles" / "hermes-agent-os-control-plane.json"
    profile = json.loads(profile_path.read_text())
    domain_model = Path(profile["canonical_docs"]["autonomous_runtime_governance_domain_model"])
    domain_model.write_text(domain_model.read_text().replace("## ClosureRecord\n", ""), encoding="utf-8")

    payload = canonical_schema_status(tmp_path)

    assert payload["ok"] is False
    assert "ClosureRecord" in payload["domain_model"]["missing_sections"]
    assert any(f["check"] == "canonical_domain_model_sections" for f in payload["findings"])


def test_agent_os_read_only_runtime_audit_classifies_known_sources_without_mutation(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path)
    _write_json(tmp_path / "cron" / "jobs.json", {"jobs": []})
    outbox = tmp_path / "state" / "gateway_delivery_outbox.jsonl"
    outbox.parent.mkdir(parents=True, exist_ok=True)
    outbox.write_text('{"status":"sent","idempotency_key":"k1"}\n', encoding="utf-8")

    payload = read_only_runtime_audit(tmp_path)

    assert payload["ok"] is True
    assert payload["mode"] == "runtime_audit_read_only"
    assert payload["runtime_mutation"] is False
    assert payload["summary"]["fail"] == 0
    assert payload["summary"]["pass"] >= 4
    assert any(f["surface"] == "cron" and f["status"] == "pass" for f in payload["findings"])
    assert any(f["surface"] == "gateway_outbox" and f["status"] == "pass" for f in payload["findings"])


def test_agent_os_read_only_runtime_audit_fails_closed_on_malformed_outbox(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path)
    outbox = tmp_path / "state" / "gateway_delivery_outbox.jsonl"
    outbox.parent.mkdir(parents=True, exist_ok=True)
    outbox.write_text('{not-json}\n', encoding="utf-8")

    payload = read_only_runtime_audit(tmp_path)

    assert payload["ok"] is False
    assert any(f["surface"] == "gateway_outbox" and f["status"] == "fail" for f in payload["findings"])


def test_agent_os_evidence_projection_maps_audit_sources_to_read_only_evidence_nodes(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path)
    _write_json(tmp_path / "cron" / "jobs.json", {"jobs": [{"id": "daily", "enabled": True}]})
    outbox = tmp_path / "state" / "gateway_delivery_outbox.jsonl"
    outbox.parent.mkdir(parents=True, exist_ok=True)
    outbox.write_text('{"status":"sent","idempotency_key":"k1"}\n', encoding="utf-8")

    payload = evidence_projection_status(tmp_path)

    assert payload["ok"] is True
    assert payload["mode"] == "evidence_projection_read_only"
    assert payload["runtime_mutation"] is False
    assert payload["domain_store_mutation"] is False
    assert payload["summary"]["node_count"] >= 6
    kinds = {node["kind"] for node in payload["evidence_nodes"]}
    assert {"schema_status", "capability_projection", "cron_source", "gateway_outbox", "shadow_router", "project_profile"}.issubset(kinds)
    assert all(node["verification_status"] in {"pass", "warn"} for node in payload["evidence_nodes"])
    assert any(node["claim_ids"] == ["erg-runtime.p2.audit.cron"] for node in payload["evidence_nodes"])


def test_agent_os_evidence_projection_blocks_on_audit_failures(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path)
    outbox = tmp_path / "state" / "gateway_delivery_outbox.jsonl"
    outbox.parent.mkdir(parents=True, exist_ok=True)
    outbox.write_text('{not-json}\n', encoding="utf-8")

    payload = evidence_projection_status(tmp_path)

    assert payload["ok"] is False
    assert payload["summary"]["fail"] >= 1
    assert any(node["verification_status"] == "fail" and node["kind"] == "gateway_outbox" for node in payload["evidence_nodes"])


def test_agent_os_bootstrap_erg_runtime_state_writes_only_exact_p4_targets_idempotently(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path)
    _write_json(tmp_path / "cron" / "jobs.json", {"jobs": [{"id": "daily", "enabled": True}]})
    outbox = tmp_path / "state" / "gateway_delivery_outbox.jsonl"
    outbox.parent.mkdir(parents=True, exist_ok=True)
    outbox.write_text('{"status":"sent","idempotency_key":"k1"}\n', encoding="utf-8")
    gate = tmp_path / "hermes-agent" / "docs" / "private" / "autonomous-runtime-governance" / "19-runtime-promotion-authorization-gate.json"
    _write_json(
        gate,
        {
            "id": "authority-packet:erg-runtime-p4-runtime-promotion-v1",
            "status": "HUMAN_AUTHORIZATION_REQUIRED",
            "requested_authority_for_next_slice": {
                "allowed_targets": [
                    str(tmp_path / "workflows/projects/hermes-agent-os-control-plane/08-runtime/erg_runtime_state.json"),
                    str(tmp_path / "workflows/projects/hermes-agent-os-control-plane/08-runtime/erg_runtime_receipts.jsonl"),
                    str(tmp_path / "workflows/projects/hermes-agent-os-control-plane/08-runtime/reconciliation_plan.json"),
                ],
                "allowed_actions": ["persist read-only EvidenceNode projection snapshot into local ERG runtime state"],
                "not_allowed_by_this_packet": ["scheduler or cron mutation", "gateway outbox mutation or external send"],
            },
        },
    )

    first = bootstrap_erg_runtime_state(tmp_path, authority_gate=gate)
    second = bootstrap_erg_runtime_state(tmp_path, authority_gate=gate)

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["idempotent_replay"] is True
    assert second["after_hashes"]["state"] == first["after_hashes"]["state"]
    assert second["after_hashes"]["plan"] == first["after_hashes"]["plan"]
    assert first["runtime_mutation"] is True
    assert first["external_side_effects_executed"] is False
    assert first["compensation_executed"] is False
    assert set(first["written_targets"].keys()) == {"state", "receipts", "plan"}
    state = json.loads(Path(first["written_targets"]["state"]).read_text())
    plan = json.loads(Path(first["written_targets"]["plan"]).read_text())
    receipts = Path(first["written_targets"]["receipts"]).read_text().strip().splitlines()
    assert state["schema_version"] == "erg-runtime-state/v1"
    assert state["evidence_node_count"] == 6
    assert state["execute"] is False
    assert plan["execute"] is False
    assert all(action["execute"] is False and action["adapter_enabled"] is False for action in plan["actions"])
    assert len(receipts) == 1
    receipt = json.loads(receipts[0])
    assert receipt["idempotency_key"] == "erg-runtime-p4-bootstrap-v1"
    assert receipt["readback"]["evidence_node_count"] == 6


def test_agent_os_bootstrap_erg_runtime_state_rejects_gate_target_drift(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path)
    gate = tmp_path / "gate.json"
    _write_json(
        gate,
        {
            "id": "authority-packet:erg-runtime-p4-runtime-promotion-v1",
            "status": "HUMAN_AUTHORIZATION_REQUIRED",
            "requested_authority_for_next_slice": {
                "allowed_targets": [str(tmp_path / "unexpected.json")],
                "allowed_actions": [],
            },
        },
    )

    payload = bootstrap_erg_runtime_state(tmp_path, authority_gate=gate)

    assert payload["ok"] is False
    assert any(f["check"] == "p4_allowed_targets_exact" for f in payload["findings"])


def test_agent_os_doctor_fails_closed_on_runtime_mutation(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path, runtime_mutation=True)
    payload = doctor(tmp_path)
    assert payload["ok"] is False
    assert any(f["check"] == "runtime_mutation_default_off" for f in payload["findings"])


def test_agent_os_doctor_fails_closed_if_shadow_report_would_execute(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path, shadow_executes=True)
    payload = doctor(tmp_path)
    assert payload["ok"] is False
    assert any(f["check"] == "shadow_no_execute" for f in payload["findings"])


def test_agent_os_route_shadow_blocks_external_side_effects(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path)
    payload = route_shadow("send a Telegram message", tmp_path)
    assert payload["ok"] is True
    assert payload["would_execute"] is False
    assert payload["intent"]["risk_class"] == "high"
    assert any(f["gate"] == "default_off_external_side_effects" for f in payload["policy_findings"])


def test_agent_os_replay_recent_user_intents_is_read_only_and_redacted(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path)
    con = sqlite3.connect(tmp_path / "state.db")
    con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT)")
    con.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp REAL, active INTEGER)"
    )
    con.execute("INSERT INTO sessions (id, source) VALUES ('s1', 'cli')")
    con.execute(
        "INSERT INTO messages (id, session_id, role, content, timestamp, active) VALUES (1, 's1', 'user', ?, 1.0, 1)",
        ("send telegram token=abc123 to ops@example.com",),
    )
    con.commit()
    con.close()

    payload = replay_recent_user_intents(tmp_path, limit=5)
    assert payload["ok"] is True
    assert payload["would_execute_anything"] is False
    assert payload["record_count"] == 1
    preview = payload["records"][0]["redacted_intent_preview"]
    assert "abc123" not in preview
    assert "ops@example.com" not in preview
    assert "[REDACTED]" in preview


def test_agent_os_report_writes_authority_packet_without_opening_gates(tmp_path: Path) -> None:
    _seed_agent_os(tmp_path)
    con = sqlite3.connect(tmp_path / "state.db")
    con.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT)")
    con.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp REAL, active INTEGER)"
    )
    con.execute("INSERT INTO sessions (id, source) VALUES ('s1', 'cli')")
    con.execute(
        "INSERT INTO messages (id, session_id, role, content, timestamp, active) VALUES (1, 's1', 'user', ?, 1.0, 1)",
        ("把这个 PR 合并并发布到生产",),
    )
    con.commit()
    con.close()

    payload = generate_replay_quality_report(tmp_path, limit=5, write=True)
    assert payload["ok"] is True
    assert payload["would_execute_anything"] is False
    packet = payload["authority_packet"]
    assert packet["status"] == "HUMAN_AUTHORIZATION_REQUIRED"
    assert packet["requested_authority"]["runtime_advisory_prompt_injection"] is True
    assert packet["requested_authority"]["real_runtime_routing"] is False
    assert packet["requested_authority"]["external_dispatch"] is False
    assert Path(payload["written"]["report"]).exists()
    assert Path(payload["written"]["authority_packet"]).exists()


def test_agent_os_local_harness_executor_writes_and_receipts(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_local_harness_executor": True}})

    auth = authorize_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert auth["ok"] is True
    assert auth["executed"] is False

    authorize_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    payload = execute_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        expected_previous_status=None,
        home=tmp_path,
    )

    assert payload["ok"] is True
    assert payload["executed"] is True
    assert payload["external_side_effects_executed"] is False
    assert payload["real_taskpool_write_executed"] is False
    state = json.loads(Path(payload["adapter_state_path"]).read_text())
    assert state["tasks"]["task:example"]["status"] == "done"
    receipts = Path(payload["receipt_path"]).read_text().splitlines()
    assert len(receipts) == 1

    duplicate = execute_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert duplicate["ok"] is True
    assert duplicate["executed"] is False
    assert duplicate["idempotent"] is True
    assert Path(payload["receipt_path"]).read_text().splitlines() == receipts

    mismatched_evidence = execute_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:different"],
        home=tmp_path,
    )
    assert mismatched_evidence["ok"] is False
    assert mismatched_evidence["blocked_reason"] == "authorization_record_missing_or_mismatched"


def test_agent_os_local_harness_executor_rejects_when_disabled_or_missing_evidence(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {}})

    disabled = execute_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert disabled["ok"] is False
    assert disabled["blocked_reason"] == "agent_os_local_harness_executor_disabled"

    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_local_harness_executor": True}})
    missing_evidence = execute_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=[],
        home=tmp_path,
    )
    assert missing_evidence["ok"] is False
    assert missing_evidence["blocked_reason"] == "missing_evidence_refs"


def test_agent_os_local_harness_executor_rejects_path_escape(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_local_harness_executor": True}})

    payload = execute_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        adapter_state_path=tmp_path / "outside.json",
        home=tmp_path,
    )
    assert payload["ok"] is False
    assert payload["blocked_reason"] == "invalid_executor_path"



def test_agent_os_local_harness_executor_rejects_without_authorization_record(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_local_harness_executor": True}})

    payload = execute_local_harness(
        authorization_id="auth:missing",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert payload["ok"] is False
    assert payload["blocked_reason"] == "authorization_record_missing_or_mismatched"


def test_agent_os_local_harness_rejects_state_and_receipt_symlinks(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_local_harness_executor": True}})
    executor_dir = tmp_path / "workflows" / "projects" / "hermes-agent-os-control-plane" / "08-executor"
    executor_dir.mkdir(parents=True, exist_ok=True)
    outside_state = tmp_path / "outside-state.json"
    state_link = executor_dir / "local-harness-state.json"
    state_link.symlink_to(outside_state)

    auth = authorize_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert auth["ok"] is False
    assert auth["blocked_reason"] == "invalid_executor_path"
    assert not outside_state.exists()

    state_link.unlink()
    auth = authorize_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert auth["ok"] is True
    outside_receipts = tmp_path / "outside-receipts.jsonl"
    receipts_link = executor_dir / "local-harness-receipts.jsonl"
    receipts_link.symlink_to(outside_receipts)

    payload = execute_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert payload["ok"] is False
    assert payload["blocked_reason"] == "invalid_executor_path"
    assert not outside_receipts.exists()


def test_agent_os_local_harness_rejects_subdirectory_even_if_symlink_points_inside(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_local_harness_executor": True}})
    executor_dir = tmp_path / "workflows" / "projects" / "hermes-agent-os-control-plane" / "08-executor"
    inside = executor_dir / "inside"
    inside.mkdir(parents=True, exist_ok=True)
    link = executor_dir / "link-inside"
    link.symlink_to(inside)

    payload = authorize_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        adapter_state_path=link / "state.json",
        home=tmp_path,
    )
    assert payload["ok"] is False
    assert payload["blocked_reason"] == "invalid_executor_path"


def test_agent_os_local_harness_rolls_back_state_if_receipt_append_fails(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_local_harness_executor": True}})
    auth = authorize_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert auth["ok"] is True

    def fail_append(path, payload):
        raise ValueError("forced receipt failure")

    monkeypatch.setattr("hermes_cli.agent_os_control_plane._append_jsonl", fail_append)
    payload = execute_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert payload["ok"] is False
    assert payload["executed"] is False
    assert payload["blocked_reason"] == "receipt_append_failed"
    state = json.loads(Path(auth["adapter_state_path"]).read_text())
    assert state.get("tasks", {}) == {}


def test_agent_os_local_harness_rejects_parent_traversal_state_path(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_local_harness_executor": True}})

    payload = authorize_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        adapter_state_path="foo/../state.json",
        home=tmp_path,
    )
    assert payload["ok"] is False
    assert payload["blocked_reason"] == "invalid_executor_path"


def test_agent_os_local_harness_reports_rollback_failure(tmp_path: Path, monkeypatch) -> None:
    _seed_agent_os(tmp_path)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"agent": {"agent_os_local_harness_executor": True}})
    auth = authorize_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert auth["ok"] is True

    calls = {"write": 0}

    def fail_append(path, payload):
        raise OSError("forced receipt failure")

    def fail_second_write(path, payload):
        calls["write"] += 1
        if calls["write"] >= 2:
            raise OSError("forced rollback failure")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    monkeypatch.setattr("hermes_cli.agent_os_control_plane._append_jsonl", fail_append)
    monkeypatch.setattr("hermes_cli.agent_os_control_plane._safe_write_json", fail_second_write)
    payload = execute_local_harness(
        authorization_id="auth:test",
        intent_id="intent:test",
        target_ref="task:example",
        target_status="done",
        evidence_refs=["command:pytest-pass"],
        home=tmp_path,
    )
    assert payload["ok"] is False
    assert payload["executed"] is True
    assert payload["rollback_failed"] is True
    assert payload["blocked_reason"] == "receipt_append_failed_rollback_failed"
