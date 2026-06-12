from __future__ import annotations

import hashlib
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone

from hermes_cli import event_governed_agents as ega


def test_worker_done_and_persistent_writes_are_guarded_not_allowed() -> None:
    verdict = ega.evaluate_agent_action(
        role="bounded_worker",
        action="mark_task_done",
        source="delegate_task",
        risk_level="L2",
        evidence_level="E2",
    )

    assert verdict["path"] == "guarded"
    assert verdict["allowed"] is False
    assert verdict["requires_human"] is True
    assert verdict["blocked_reason"] == "worker_cannot_mutate_lifecycle"
    assert "submit_evidence_packet" in verdict["allowed_alternatives"]

    memory_verdict = ega.evaluate_agent_action(
        role="memory_skill_curator",
        action="memory_write",
        source="cron",
        risk_level="L3",
        evidence_level="E2",
    )

    assert memory_verdict["path"] == "guarded"
    assert memory_verdict["allowed"] is False
    assert memory_verdict["blocked_reason"] == "persistent_learning_write_requires_proposal_and_approval"


def test_low_risk_readonly_and_standard_evidence_paths_are_allowed() -> None:
    fast = ega.evaluate_agent_action(
        role="projection_builder",
        action="read_ssot",
        source="dashboard",
        risk_level="L0",
        evidence_level="E0",
    )
    assert fast["path"] == "fast"
    assert fast["allowed"] is True
    assert fast["writes_enabled"] is False

    standard = ega.evaluate_agent_action(
        role="bounded_worker",
        action="submit_evidence_packet",
        source="worker",
        risk_level="L1",
        evidence_level="E2",
    )
    assert standard["path"] == "standard"
    assert standard["allowed"] is True
    assert standard["writes_enabled"] is False
    assert standard["ledger_effect"] == "proposal_or_evidence_only"


def test_event_envelope_rejects_untrusted_high_privilege_escalation() -> None:
    envelope = ega.normalize_event(
        event_type="task.status_changed",
        source="webhook",
        actor="external-hook",
        subject_ref="task:123",
        payload={"target_status": "done"},
        dedupe_key="webhook-task-123-done",
    )

    assert envelope["event_type"] == "task.status_changed"
    assert envelope["permission_context"]["trust_level"] == "untrusted"
    assert envelope["permission_context"]["may_mutate_lifecycle"] is False
    assert envelope["requires_human_confirmation"] is True
    assert envelope["dedupe_key"] == "webhook-task-123-done"


def test_mission_envelope_schema_gate_accepts_traceable_local_slice_and_rejects_blocked_actions() -> None:
    accepted = ega.validate_mission_envelope_schema_gate(
        {
            "mission_id": "msn_20260610_120149_27c88cfc",
            "stage": "intake",
            "direction": "Hermes architecture self-advance",
            "success_criteria": ["ledger traceability", "evidence-backed next step"],
            "constraints": ["local reversible work only"],
            "blocked_actions": ["external_message_send", "production_deploy", "skill_patch"],
            "trigger": {"mode": "event-driven", "signal_type": "taskpool_status_changed"},
            "budget_boundary": {"budget_exhausted": False, "remaining_ticks_known": True, "remaining_ticks": 1},
            "open_decisions": [],
            "requested_actions": ["local_code_patch", "focused_pytest"],
        }
    )

    assert accepted["accepted_as"] == "mission_envelope_projection"
    assert accepted["may_execute_bounded_slice"] is True
    assert accepted["external_side_effects_allowed"] is False
    assert accepted["blocked_reasons"] == []
    assert accepted["envelope_digest"]

    rejected = ega.validate_mission_envelope_schema_gate(
        {
            "mission_id": "msn_bad",
            "stage": "intake",
            "direction": "Hermes architecture self-advance",
            "success_criteria": ["ledger traceability"],
            "constraints": [],
            "blocked_actions": ["production_deploy"],
            "trigger": {"mode": "event-driven"},
            "budget_boundary": {"budget_exhausted": True},
            "requested_actions": ["production_deploy"],
        }
    )

    assert rejected["accepted_as"] == "rejected_mission_envelope_projection"
    assert rejected["may_execute_bounded_slice"] is False
    assert rejected["external_side_effects_allowed"] is False
    assert "budget_exhausted_must_be_explicit_false" in rejected["blocked_reasons"]
    assert "requested_blocked_action:production_deploy" in rejected["blocked_reasons"]
    assert "requested_dangerous_action:production_deploy" in rejected["blocked_reasons"]


def test_mission_envelope_schema_gate_rejects_ambiguous_or_dangerous_shapes() -> None:
    base = {
        "mission_id": "msn_20260610_120149_27c88cfc",
        "stage": "intake",
        "direction": "Hermes architecture self-advance",
        "success_criteria": ["ledger traceability"],
        "constraints": ["local only"],
        "blocked_actions": [],
        "trigger": {"mode": "event-driven"},
        "budget_boundary": {"budget_exhausted": False, "remaining_ticks": 1},
        "open_decisions": [],
        "requested_actions": ["local_code_patch"],
    }

    dangerous = ega.validate_mission_envelope_schema_gate({**base, "requested_actions": ["send_external_message"]})
    assert dangerous["may_execute_bounded_slice"] is False
    assert "requested_dangerous_action:send_external_message" in dangerous["blocked_reasons"]
    assert "requested_action_not_allowed:send_external_message" in dangerous["blocked_reasons"]

    ambiguous_budget = ega.validate_mission_envelope_schema_gate({**base, "budget_boundary": {}})
    assert ambiguous_budget["may_execute_bounded_slice"] is False
    assert "budget_exhausted_must_be_explicit_false" in ambiguous_budget["blocked_reasons"]
    assert "budget_remaining_ticks_must_be_positive_integer" in ambiguous_budget["blocked_reasons"]

    known_without_count = ega.validate_mission_envelope_schema_gate(
        {**base, "budget_boundary": {"budget_exhausted": False, "remaining_ticks_known": True}}
    )
    assert known_without_count["may_execute_bounded_slice"] is False
    assert "budget_remaining_ticks_must_be_positive_integer" in known_without_count["blocked_reasons"]

    bad_tick_values = [0, -1, "1", True]
    for value in bad_tick_values:
        result = ega.validate_mission_envelope_schema_gate({**base, "budget_boundary": {"budget_exhausted": False, "remaining_ticks": value}})
        assert result["may_execute_bounded_slice"] is False
        assert "budget_remaining_ticks_must_be_positive_integer" in result["blocked_reasons"]

    bare_prefix = ega.validate_mission_envelope_schema_gate({**base, "mission_id": "msn_"})
    assert bare_prefix["may_execute_bounded_slice"] is False
    assert "mission_id_must_match_msn_timestamp_digest_format" in bare_prefix["blocked_reasons"]

    malformed_scalar = ega.validate_mission_envelope_schema_gate({**base, "stage": 123})
    assert malformed_scalar["may_execute_bounded_slice"] is False
    assert "stage_must_be_nonempty_string" in malformed_scalar["blocked_reasons"]

    missing_open_decisions = dict(base)
    missing_open_decisions.pop("open_decisions")
    missing_open = ega.validate_mission_envelope_schema_gate(missing_open_decisions)
    assert missing_open["may_execute_bounded_slice"] is False
    assert "missing_required_key:open_decisions" in missing_open["blocked_reasons"]
    assert "open_decisions_must_be_explicit_list" in missing_open["blocked_reasons"]

    non_serializable = ega.validate_mission_envelope_schema_gate({**base, "constraints": {"set-value"}})
    assert non_serializable["may_execute_bounded_slice"] is False
    assert non_serializable["envelope_digest"]


def test_mission_envelope_schema_gate_has_cli_projection(tmp_path) -> None:
    envelope = {
        "mission_id": "msn_20260610_120149_27c88cfc",
        "stage": "intake",
        "direction": "Hermes architecture self-advance",
        "success_criteria": ["ledger traceability"],
        "constraints": ["local only"],
        "blocked_actions": ["external_message_send", "production_deploy"],
        "trigger": {"mode": "event-driven", "signal_type": "taskpool_status_changed"},
        "budget_boundary": {"budget_exhausted": False, "remaining_ticks": 1},
        "open_decisions": [],
        "requested_actions": ["read_only_verification", "stage_report"],
    }

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--validate-mission-envelope",
            "--mission-envelope-json",
            json.dumps(envelope),
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_projection = json.loads(proc.stdout)

    assert cli_projection["accepted_as"] == "mission_envelope_projection"
    assert cli_projection["may_execute_bounded_slice"] is True
    assert cli_projection["external_side_effects_allowed"] is False
    assert cli_projection["blocked_reasons"] == []


def test_worker_result_contract_accepts_evidence_only_and_rejects_smuggled_authority() -> None:
    accepted = ega.validate_worker_result_contract(
        worker_type="claude-code",
        worker_id="worker-1",
        task_ref="task:arch-coverage",
        result={
            "status": "succeeded",
            "evidence_refs": ["artifact:/tmp/proof.json"],
            "proposal_refs": ["proposal:next-slice"],
        },
    )

    assert accepted["accepted_as"] == "evidence_or_proposal"
    assert accepted["allowed_to_mutate_lifecycle"] is False
    assert accepted["allowed_external_writes"] is False
    assert accepted["blocked_reasons"] == []
    assert accepted["result_digest"]

    rejected = ega.validate_worker_result_contract(
        worker_type="codex",
        worker_id="worker-2",
        task_ref="task:arch-coverage",
        result={
            "status": "succeeded",
            "evidence_refs": ["artifact:/tmp/proof.json"],
            "task_status": "done",
            "external_write": {"adapter": "kanban"},
        },
    )

    assert rejected["accepted_as"] == "rejected_contract_projection"
    assert rejected["allowed_to_mutate_lifecycle"] is False
    assert rejected["forbidden_keys"] == ["external_write", "task_status"]
    assert "worker_result_contains_forbidden_authority_keys" in rejected["blocked_reasons"]

    no_evidence = ega.validate_worker_result_contract(
        worker_type="opencode",
        worker_id="worker-3",
        task_ref="task:arch-coverage",
        result={"status": "succeeded", "evidence_refs": []},
    )
    assert "succeeded_worker_result_requires_evidence_refs" in no_evidence["blocked_reasons"]


def test_fork_join_graph_contract_is_projection_only_and_rejects_unsafe_graphs() -> None:
    valid = ega.validate_fork_join_graph_contract(
        {
            "graph_id": "agent-coverage-fork-join",
            "nodes": [
                {"id": "plan", "kind": "task", "requires": []},
                {"id": "worker-a", "kind": "task", "requires": ["plan"]},
                {"id": "worker-b", "kind": "task", "requires": ["plan"]},
                {
                    "id": "join",
                    "kind": "join",
                    "requires": ["worker-a", "worker-b"],
                    "evidence_refs_required": ["AC-join"],
                },
            ],
        }
    )

    assert valid["accepted_as"] == "ledger_projection_contract"
    assert valid["runtime_parallel_dispatch_enabled"] is False
    assert valid["lifecycle_mutation_enabled"] is False
    assert valid["join_count"] == 1
    assert valid["blocked_reasons"] == []

    unsafe = ega.validate_fork_join_graph_contract(
        {
            "graph_id": "unsafe",
            "nodes": [
                {"id": "a", "kind": "task", "requires": ["b"]},
                {"id": "b", "kind": "join", "requires": ["a"]},
            ],
        }
    )

    assert unsafe["accepted_as"] == "rejected_graph_contract_projection"
    assert "cycle_detected" in unsafe["blocked_reasons"]
    assert "join_requires_two_or_more_inputs:b" in unsafe["blocked_reasons"]
    assert "join_requires_evidence_refs:b" in unsafe["blocked_reasons"]


def test_certification_contract_requires_quorum_truth_gate_and_evidence() -> None:
    valid = ega.validate_certification_contract(
        {
            "subject_ref": "task:arch-coverage",
            "quorum": 2,
            "truth_gate": True,
            "reviewers": [
                {"role": "planner", "model": "claude"},
                {"role": "reviewer", "model": "codex"},
                {"role": "arbiter", "model": "mimo"},
            ],
            "claims": [{"id": "AC-1", "evidence_refs": ["artifact:/tmp/proof.json"]}],
        }
    )

    assert valid["certification_possible"] is True
    assert valid["accepted_as"] == "certification_projection"
    assert valid["allowed_to_mark_done"] is False
    assert valid["model_consensus_is_fact"] is False
    assert valid["blocked_reasons"] == []

    invalid = ega.validate_certification_contract(
        {
            "subject_ref": "task:arch-coverage",
            "quorum": 3,
            "truth_gate": False,
            "reviewers": [{"role": "planner", "model": "claude"}],
            "claims": [{"id": "AC-1", "evidence_refs": []}],
        }
    )

    assert invalid["certification_possible"] is False
    assert "at_least_two_reviewers_required" in invalid["blocked_reasons"]
    assert "quorum_exceeds_reviewer_count" in invalid["blocked_reasons"]
    assert "truth_gate_required" in invalid["blocked_reasons"]
    assert invalid["unsupported_claims"] == ["AC-1"]


def test_artifact_pointer_contract_keeps_hot_path_bounded_and_rejects_inline_blobs() -> None:
    valid = ega.validate_artifact_pointer_contract(
        {
            "artifact_uri": "artifact://agent-coverage/proof.json",
            "artifact_hash": "sha256:" + "a" * 64,
            "artifact_type": "json",
            "summary": "Focused proof output",
            "size_bytes": 4096,
        }
    )

    assert valid["accepted_as"] == "artifact_pointer_contract"
    assert valid["hot_path_payload_allowed"] is True
    assert valid["external_artifact_required"] is True
    assert valid["blocked_reasons"] == []

    inline_blob = ega.validate_artifact_pointer_contract(
        {
            "artifact_uri": "artifact://agent-coverage/big-log.txt",
            "artifact_hash": "sha256:" + "b" * 64,
            "artifact_type": "transcript",
            "summary": "x" * 300,
            "size_bytes": 2_000_000,
            "inline_content": "raw transcript must not enter hot ledger",
        }
    )

    assert inline_blob["accepted_as"] == "rejected_artifact_pointer_projection"
    assert inline_blob["hot_path_payload_allowed"] is False
    assert "inline_content_forbidden" in inline_blob["blocked_reasons"]
    assert "summary_too_long" in inline_blob["blocked_reasons"]


def test_workflow_metadata_contract_validates_substate_without_lifecycle_authority() -> None:
    valid = ega.validate_workflow_metadata_contract(
        {
            "task_ref": "task:arch-coverage",
            "workflow_state": "awaiting_join_evidence",
            "lane": "agent-architecture",
            "deadline_at": "2026-06-30T00:00:00Z",
            "sla_class": "normal",
            "resource_profile": {"worker_type": "claude-code", "max_parallel": 2},
            "quota_profile": {"max_cost_usd": 5},
            "checkpoint": {
                "required": True,
                "type": "final_review",
                "owner": "human",
                "resume_policy": "after_approval",
            },
        }
    )

    assert valid["accepted_as"] == "workflow_metadata_contract"
    assert valid["canonical_lifecycle_unchanged"] is True
    assert valid["allowed_to_mutate_lifecycle"] is False
    assert valid["blocked_reasons"] == []

    invalid = ega.validate_workflow_metadata_contract(
        {
            "task_ref": "task:arch-coverage",
            "workflow_state": "done",
            "lane": "agent-architecture",
            "canonical_status": "done",
            "checkpoint": {"required": True, "type": "final_review"},
        }
    )

    assert invalid["accepted_as"] == "rejected_workflow_metadata_projection"
    assert invalid["allowed_to_mutate_lifecycle"] is False
    assert "workflow_state_must_not_be_canonical_lifecycle_status" in invalid["blocked_reasons"]
    assert "canonical_status_forbidden_in_metadata_contract" in invalid["blocked_reasons"]
    assert "checkpoint_owner_required" in invalid["blocked_reasons"]


def test_control_plane_report_is_json_and_report_only(tmp_path) -> None:
    report = ega.build_control_plane_report()

    assert report["schema_version"] == 1
    assert report["mode"] == "report_only"
    assert report["ssot"]["projection_is_disposable"] is True
    assert report["invariants"]["worker_may_mark_done"] is False
    assert report["invariants"]["model_consensus_is_fact"] is False
    assert "bounded_worker" in report["roles"]
    assert report["roles"]["bounded_worker"]["forbidden_actions"]

    proc = subprocess.run(
        [sys.executable, "-m", "hermes_cli.event_governed_agents", "--json"],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_report = json.loads(proc.stdout)
    assert cli_report["mode"] == "report_only"
    assert cli_report["danger_gate"]["dangerous_actions_require_human"] is True


def test_agent_architecture_coverage_report_is_non_authorizing_contract(tmp_path) -> None:
    report = ega.build_agent_architecture_coverage_report()

    assert report["schema_version"] == 1
    assert report["mode"] == "report_only"
    assert report["ssot"]["external_agents_are_fact_sources"] is False
    assert report["ssot"]["worker_outputs_are_evidence_or_proposals"] is True
    assert report["coverage_count"] == 8
    assert report["priority_counts"]["P1"] >= 4
    capabilities = {item["capability"]: item for item in report["capabilities"]}
    assert capabilities["parallel_branches"]["coverage_status"] == "runtime_boundary_started"
    assert "governed worker executor" in capabilities["parallel_branches"]["next_safe_slice"]
    assert capabilities["workflow_composition"]["authority_boundary"].startswith("do_not_make workflow templates")
    assert report["phase_boundary"] == {
        "runtime_worker_dispatch_enabled": False,
        "lifecycle_mutation_enabled": False,
        "external_adapter_write_enabled": False,
        "requires_separate_human_gate_before_enabling": True,
    }

    proc = subprocess.run(
        [sys.executable, "-m", "hermes_cli.event_governed_agents", "--coverage", "--json"],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_report = json.loads(proc.stdout)
    assert cli_report["architecture"] == "Hermes Agent Architecture Coverage"
    assert cli_report["phase_boundary"]["runtime_worker_dispatch_enabled"] is False


def test_start_runtime_boundary_records_real_started_run_without_lifecycle_or_external_writes(tmp_path) -> None:
    ledger_path = tmp_path / "agent-runtime-runs.jsonl"
    request = {
        "task_ref": "task:arch-coverage",
        "worker_type": "claude-code",
        "worker_id": "worker-1",
        "run_purpose": "verify_next_slice",
        "workflow_state": "runtime_started",
        "evidence_refs": ["artifact://agent-coverage/start-proof.json"],
    }

    started = ega.start_bounded_runtime_run(request=request, ledger_path=ledger_path)

    assert started["accepted_as"] == "bounded_runtime_run_started"
    assert started["runtime_boundary"] == "started_ledger_only"
    assert started["run_status"] == "started"
    assert started["task_ref"] == "task:arch-coverage"
    assert started["worker_type"] == "claude-code"
    assert started["external_executor_invoked"] is False
    assert started["canonical_lifecycle_unchanged"] is True
    assert started["allowed_to_mutate_lifecycle"] is False
    assert started["external_adapter_write_through_enabled"] is False
    assert started["blocked_reasons"] == []

    rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["run_id"] == started["run_id"]
    assert rows[0]["request_digest"] == started["request_digest"]

    retry = ega.start_bounded_runtime_run(request=request, ledger_path=ledger_path)
    retry_rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert retry["accepted_as"] == "bounded_runtime_run_started"
    assert retry["idempotent_replay"] is True
    assert retry["run_id"] == started["run_id"]
    assert len(retry_rows) == 1


def test_start_runtime_boundary_fails_closed_on_smuggled_authority_and_cli_smoke(tmp_path) -> None:
    ledger_path = tmp_path / "agent-runtime-runs.jsonl"
    rejected = ega.start_bounded_runtime_run(
        request={
            "task_ref": "task:arch-coverage",
            "worker_type": "codex",
            "worker_id": "worker-2",
            "run_purpose": "unsafe",
            "evidence_refs": ["artifact://agent-coverage/start-proof.json"],
            "task_status": "done",
            "external_write": {"adapter": "kanban"},
        },
        ledger_path=ledger_path,
    )

    assert rejected["accepted_as"] == "rejected_runtime_run_start"
    assert rejected["run_status"] == "rejected"
    assert rejected["external_executor_invoked"] is False
    assert rejected["canonical_lifecycle_unchanged"] is True
    assert "runtime_start_request_contains_forbidden_authority_keys" in rejected["blocked_reasons"]
    assert not ledger_path.exists()

    request_json = json.dumps(
        {
            "task_ref": "task:arch-coverage",
            "worker_type": "opencode",
            "worker_id": "worker-3",
            "run_purpose": "cli_smoke",
            "evidence_refs": ["artifact://agent-coverage/cli-start-proof.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--start-runtime-run",
            "--runtime-ledger",
            str(ledger_path),
            "--runtime-request-json",
            request_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_started = json.loads(proc.stdout)
    assert cli_started["accepted_as"] == "bounded_runtime_run_started"
    assert cli_started["runtime_boundary"] == "started_ledger_only"
    assert ledger_path.exists()


def test_execute_runtime_boundary_runs_allowlisted_builtin_executor_and_appends_result(tmp_path) -> None:
    ledger_path = tmp_path / "agent-runtime-runs.jsonl"
    started = ega.start_bounded_runtime_run(
        request={
            "task_ref": "task:arch-coverage",
            "worker_type": "hermes-builtin-readonly",
            "worker_id": "builtin-1",
            "run_purpose": "readonly_verification",
            "workflow_state": "runtime_started",
            "evidence_refs": ["artifact://agent-coverage/start-proof.json"],
        },
        ledger_path=ledger_path,
    )

    completed = ega.execute_bounded_runtime_run(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        executor_request={
            "executor_type": "hermes_builtin_readonly",
            "action": "summarize_started_run",
            "evidence_refs": ["artifact://agent-coverage/executor-proof.json"],
        },
    )

    assert completed["accepted_as"] == "bounded_runtime_run_completed"
    assert completed["run_status"] == "completed"
    assert completed["runtime_boundary"] == "builtin_readonly_executor_completed"
    assert completed["executor_invoked"] is True
    assert completed["external_executor_invoked"] is False
    assert completed["allowed_to_mutate_lifecycle"] is False
    assert completed["external_adapter_write_through_enabled"] is False
    assert completed["result"]["summary"] == "Started run verified from ledger"

    rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert [row["run_status"] for row in rows] == ["started", "completed"]

    retry = ega.execute_bounded_runtime_run(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        executor_request={
            "executor_type": "hermes_builtin_readonly",
            "action": "summarize_started_run",
            "evidence_refs": ["artifact://agent-coverage/executor-proof.json"],
        },
    )
    retry_rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert retry["idempotent_replay"] is True
    assert retry["run_status"] == "completed"
    assert len(retry_rows) == 2


def test_execute_runtime_boundary_rejects_unknown_executor_and_cli_smoke(tmp_path) -> None:
    ledger_path = tmp_path / "agent-runtime-runs.jsonl"
    started = ega.start_bounded_runtime_run(
        request={
            "task_ref": "task:arch-coverage",
            "worker_type": "hermes-builtin-readonly",
            "worker_id": "builtin-2",
            "run_purpose": "readonly_verification",
            "evidence_refs": ["artifact://agent-coverage/start-proof.json"],
        },
        ledger_path=ledger_path,
    )

    rejected = ega.execute_bounded_runtime_run(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        executor_request={
            "executor_type": "claude_code_external",
            "action": "summarize_started_run",
            "evidence_refs": ["artifact://agent-coverage/executor-proof.json"],
        },
    )

    assert rejected["accepted_as"] == "rejected_runtime_execution"
    assert rejected["run_status"] == "rejected"
    assert rejected["executor_invoked"] is False
    assert "executor_type_not_allowlisted" in rejected["blocked_reasons"]
    assert len(ledger_path.read_text().splitlines()) == 1

    executor_json = json.dumps(
        {
            "executor_type": "hermes_builtin_readonly",
            "action": "summarize_started_run",
            "evidence_refs": ["artifact://agent-coverage/cli-executor-proof.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--execute-runtime-run",
            "--runtime-ledger",
            str(ledger_path),
            "--run-id",
            started["run_id"],
            "--executor-request-json",
            executor_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_completed = json.loads(proc.stdout)
    assert cli_completed["accepted_as"] == "bounded_runtime_run_completed"
    assert cli_completed["runtime_boundary"] == "builtin_readonly_executor_completed"
    assert len(ledger_path.read_text().splitlines()) == 2


def test_external_agent_probe_invokes_allowlisted_process_and_appends_probe_result(tmp_path) -> None:
    ledger_path = tmp_path / "agent-runtime-runs.jsonl"
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/sh\necho 'Claude Code 1.2.3'\n")
    fake_claude.chmod(0o755)
    started = ega.start_bounded_runtime_run(
        request={
            "task_ref": "task:arch-coverage",
            "worker_type": "claude-code",
            "worker_id": "claude-probe",
            "run_purpose": "external_agent_probe",
            "evidence_refs": ["artifact://agent-coverage/start-proof.json"],
        },
        ledger_path=ledger_path,
    )

    probed = ega.probe_external_agent_runtime_run(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        probe_request={
            "executor_type": "claude_code",
            "command_path": str(fake_claude),
            "evidence_refs": ["artifact://agent-coverage/claude-probe.json"],
        },
    )

    assert probed["accepted_as"] == "external_agent_probe_completed"
    assert probed["run_status"] == "external_probe_completed"
    assert probed["runtime_boundary"] == "external_agent_version_probe_completed"
    assert probed["external_executor_invoked"] is True
    assert probed["allowed_to_mutate_lifecycle"] is False
    assert probed["external_adapter_write_through_enabled"] is False
    assert probed["probe"]["returncode"] == 0
    assert "Claude Code" in probed["probe"]["stdout"]
    rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert [row["run_status"] for row in rows] == ["started", "external_probe_completed"]


def test_external_agent_probe_rejects_unallowlisted_command_and_cli_smoke(tmp_path) -> None:
    ledger_path = tmp_path / "agent-runtime-runs.jsonl"
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/sh\necho 'Claude Code 1.2.3'\n")
    fake_claude.chmod(0o755)
    started = ega.start_bounded_runtime_run(
        request={
            "task_ref": "task:arch-coverage",
            "worker_type": "claude-code",
            "worker_id": "claude-probe-cli",
            "run_purpose": "external_agent_probe",
            "evidence_refs": ["artifact://agent-coverage/start-proof.json"],
        },
        ledger_path=ledger_path,
    )

    rejected = ega.probe_external_agent_runtime_run(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        probe_request={
            "executor_type": "claude_code",
            "command_path": "/bin/sh",
            "evidence_refs": ["artifact://agent-coverage/rejected-probe.json"],
        },
    )

    assert rejected["accepted_as"] == "rejected_external_agent_probe"
    assert rejected["external_executor_invoked"] is False
    assert "command_path_basename_mismatch" in rejected["blocked_reasons"]
    assert len(ledger_path.read_text().splitlines()) == 1

    probe_json = json.dumps(
        {
            "executor_type": "claude_code",
            "command_path": str(fake_claude),
            "evidence_refs": ["artifact://agent-coverage/cli-probe.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--probe-external-runtime-run",
            "--runtime-ledger",
            str(ledger_path),
            "--run-id",
            started["run_id"],
            "--probe-request-json",
            probe_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_probed = json.loads(proc.stdout)
    assert cli_probed["accepted_as"] == "external_agent_probe_completed"
    assert cli_probed["external_executor_invoked"] is True
    assert len(ledger_path.read_text().splitlines()) == 2


def test_external_agent_prompt_dry_run_sends_readonly_prompt_and_records_artifact_pointer(tmp_path) -> None:
    ledger_path = tmp_path / "agent-runtime-runs.jsonl"
    artifact_path = tmp_path / "artifacts" / "claude-dry-run.json"
    argv_log = tmp_path / "argv.json"
    fake_claude = tmp_path / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(argv_log)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "print(json.dumps({'result': 'DRY_RUN_OK'}))\n"
    )
    fake_claude.chmod(0o755)
    started = ega.start_bounded_runtime_run(
        request={
            "task_ref": "task:arch-coverage",
            "worker_type": "claude-code",
            "worker_id": "claude-dry-run",
            "run_purpose": "external_agent_prompt_dry_run",
            "evidence_refs": ["artifact://agent-coverage/start-proof.json"],
        },
        ledger_path=ledger_path,
    )

    dry_run = ega.dry_run_external_agent_prompt(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        prompt_request={
            "executor_type": "claude_code",
            "command_path": str(fake_claude),
            "prompt": "只读总结这个 run，不要调用工具，不要写文件。只回复 DRY_RUN_OK。",
            "artifact_path": str(artifact_path),
            "evidence_refs": ["artifact://agent-coverage/prompt-dry-run-request.json"],
        },
    )

    assert dry_run["accepted_as"] == "external_agent_prompt_dry_run_completed"
    assert dry_run["run_status"] == "external_prompt_dry_run_completed"
    assert dry_run["runtime_boundary"] == "external_agent_prompt_dry_run_completed"
    assert dry_run["external_executor_invoked"] is True
    assert dry_run["task_prompt_sent"] is True
    assert dry_run["tools_enabled"] is False
    assert dry_run["write_mode_enabled"] is False
    assert dry_run["allowed_to_mutate_lifecycle"] is False
    assert dry_run["external_adapter_write_through_enabled"] is False
    assert dry_run["artifact"]["artifact_uri"] == f"file://{artifact_path}"
    assert dry_run["artifact"]["artifact_type"] == "json"
    assert len(dry_run["artifact"]["sha256"]) == 64
    assert artifact_path.exists()
    artifact = json.loads(artifact_path.read_text())
    assert artifact["returncode"] == 0
    assert "DRY_RUN_OK" in artifact["stdout"]
    argv = json.loads(argv_log.read_text())
    assert "-p" in argv
    assert "--tools" in argv
    assert "" in argv
    assert "--permission-mode" in argv
    assert "plan" in argv
    assert "--no-session-persistence" in argv
    rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert [row["run_status"] for row in rows] == ["started", "external_prompt_dry_run_completed"]


def test_external_agent_prompt_dry_run_rejects_tool_or_write_authority_and_cli_smoke(tmp_path) -> None:
    ledger_path = tmp_path / "agent-runtime-runs.jsonl"
    artifact_path = tmp_path / "artifacts" / "claude-dry-run.json"
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/sh\necho '{\"result\":\"DRY_RUN_OK\"}'\n")
    fake_claude.chmod(0o755)
    started = ega.start_bounded_runtime_run(
        request={
            "task_ref": "task:arch-coverage",
            "worker_type": "claude-code",
            "worker_id": "claude-dry-run-cli",
            "run_purpose": "external_agent_prompt_dry_run",
            "evidence_refs": ["artifact://agent-coverage/start-proof.json"],
        },
        ledger_path=ledger_path,
    )

    rejected = ega.dry_run_external_agent_prompt(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        prompt_request={
            "executor_type": "claude_code",
            "command_path": str(fake_claude),
            "prompt": "please edit files",
            "artifact_path": str(artifact_path),
            "tools_enabled": True,
            "task_status": "done",
            "evidence_refs": ["artifact://agent-coverage/rejected-prompt.json"],
        },
    )

    assert rejected["accepted_as"] == "rejected_external_agent_prompt_dry_run"
    assert rejected["external_executor_invoked"] is False
    assert "tools_must_be_disabled" in rejected["blocked_reasons"]
    assert "prompt_request_contains_forbidden_authority_keys" in rejected["blocked_reasons"]
    assert not artifact_path.exists()
    assert len(ledger_path.read_text().splitlines()) == 1

    request_json = json.dumps(
        {
            "executor_type": "claude_code",
            "command_path": str(fake_claude),
            "prompt": "只读 dry run，回复 DRY_RUN_OK。",
            "artifact_path": str(artifact_path),
            "evidence_refs": ["artifact://agent-coverage/cli-prompt-dry-run.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--dry-run-external-agent-prompt",
            "--runtime-ledger",
            str(ledger_path),
            "--run-id",
            started["run_id"],
            "--prompt-request-json",
            request_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_dry_run = json.loads(proc.stdout)
    assert cli_dry_run["accepted_as"] == "external_agent_prompt_dry_run_completed"
    assert cli_dry_run["artifact"]["artifact_uri"] == f"file://{artifact_path}"
    assert len(ledger_path.read_text().splitlines()) == 2


def test_external_agent_readonly_context_packet_uses_artifact_context_and_records_output_pointer(tmp_path) -> None:
    ledger_path = tmp_path / "agent-runtime-runs.jsonl"
    context_path = tmp_path / "context.md"
    output_path = tmp_path / "artifacts" / "claude-readonly-context.json"
    argv_log = tmp_path / "argv.json"
    fake_claude = tmp_path / "claude"
    context_path.write_text("# Context\nOnly summarize this artifact.\n", encoding="utf-8")
    fake_claude.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(argv_log)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        "print(json.dumps({'result': 'READONLY_CONTEXT_OK'}))\n",
        encoding="utf-8",
    )
    fake_claude.chmod(0o755)
    started = ega.start_bounded_runtime_run(
        request={
            "task_ref": "task:arch-coverage",
            "worker_type": "claude-code",
            "worker_id": "claude-readonly-context",
            "run_purpose": "external_agent_readonly_context",
            "evidence_refs": ["artifact://agent-coverage/start-proof.json"],
        },
        ledger_path=ledger_path,
    )

    result = ega.run_external_agent_readonly_context(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        context_request={
            "executor_type": "claude_code",
            "command_path": str(fake_claude),
            "instruction": "只读分析 context artifact，回复 READONLY_CONTEXT_OK。",
            "context_artifact_uri": f"file://{context_path}",
            "context_sha256": hashlib.sha256(context_path.read_bytes()).hexdigest(),
            "output_artifact_path": str(output_path),
            "evidence_refs": ["artifact://agent-coverage/readonly-context-request.json"],
        },
    )

    assert result["accepted_as"] == "external_agent_readonly_context_completed"
    assert result["run_status"] == "external_readonly_context_completed"
    assert result["runtime_boundary"] == "external_agent_readonly_context_completed"
    assert result["external_executor_invoked"] is True
    assert result["readonly_context_attached"] is True
    assert result["task_prompt_sent"] is True
    assert result["tools_enabled"] is False
    assert result["write_mode_enabled"] is False
    assert result["allowed_to_mutate_lifecycle"] is False
    assert result["context"]["context_artifact_uri"] == f"file://{context_path}"
    assert result["artifact"]["artifact_uri"] == f"file://{output_path}"
    assert len(result["artifact"]["sha256"]) == 64
    output = json.loads(output_path.read_text())
    assert output["returncode"] == 0
    assert "READONLY_CONTEXT_OK" in output["stdout"]
    assert output["context_artifact_uri"] == f"file://{context_path}"
    argv = json.loads(argv_log.read_text())
    prompt_arg = argv[argv.index("-p") + 1]
    assert f"Context artifact: file://{context_path}" in prompt_arg
    assert "Context sha256:" in prompt_arg
    assert "--tools" in argv and "" in argv
    assert "--permission-mode" in argv and "plan" in argv
    rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert [row["run_status"] for row in rows] == ["started", "external_readonly_context_completed"]


def test_external_agent_readonly_context_rejects_inline_context_write_and_cli_smoke(tmp_path) -> None:
    ledger_path = tmp_path / "agent-runtime-runs.jsonl"
    context_path = tmp_path / "context.md"
    output_path = tmp_path / "artifacts" / "claude-readonly-context.json"
    fake_claude = tmp_path / "claude"
    context_path.write_text("# Context\nReadonly packet.\n", encoding="utf-8")
    fake_claude.write_text("#!/bin/sh\necho '{\"result\":\"READONLY_CONTEXT_OK\"}'\n", encoding="utf-8")
    fake_claude.chmod(0o755)
    started = ega.start_bounded_runtime_run(
        request={
            "task_ref": "task:arch-coverage",
            "worker_type": "claude-code",
            "worker_id": "claude-readonly-context-cli",
            "run_purpose": "external_agent_readonly_context",
            "evidence_refs": ["artifact://agent-coverage/start-proof.json"],
        },
        ledger_path=ledger_path,
    )

    rejected = ega.run_external_agent_readonly_context(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        context_request={
            "executor_type": "claude_code",
            "command_path": str(fake_claude),
            "instruction": "edit this",
            "context_artifact_uri": f"file://{context_path}",
            "context_sha256": hashlib.sha256(context_path.read_bytes()).hexdigest(),
            "inline_context": "forbidden hot-path blob",
            "output_artifact_path": str(output_path),
            "write_mode_enabled": True,
            "external_write": True,
            "evidence_refs": ["artifact://agent-coverage/rejected-readonly-context.json"],
        },
    )

    assert rejected["accepted_as"] == "rejected_external_agent_readonly_context"
    assert rejected["external_executor_invoked"] is False
    assert "inline_context_forbidden" in rejected["blocked_reasons"]
    assert "write_mode_must_be_disabled" in rejected["blocked_reasons"]
    assert "context_request_contains_forbidden_authority_keys" in rejected["blocked_reasons"]
    assert not output_path.exists()
    assert len(ledger_path.read_text().splitlines()) == 1

    request_json = json.dumps(
        {
            "executor_type": "claude_code",
            "command_path": str(fake_claude),
            "instruction": "只读 context artifact，回复 READONLY_CONTEXT_OK。",
            "context_artifact_uri": f"file://{context_path}",
            "context_sha256": hashlib.sha256(context_path.read_bytes()).hexdigest(),
            "output_artifact_path": str(output_path),
            "evidence_refs": ["artifact://agent-coverage/cli-readonly-context.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--run-external-agent-readonly-context",
            "--runtime-ledger",
            str(ledger_path),
            "--run-id",
            started["run_id"],
            "--context-request-json",
            request_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_result = json.loads(proc.stdout)
    assert cli_result["accepted_as"] == "external_agent_readonly_context_completed"
    assert cli_result["context"]["context_artifact_uri"] == f"file://{context_path}"
    assert cli_result["artifact"]["artifact_uri"] == f"file://{output_path}"
    assert len(ledger_path.read_text().splitlines()) == 2


def test_external_agent_proposal_artifact_accepts_review_proposal_with_evidence(tmp_path) -> None:
    ledger_path = tmp_path / "agent-runtime-runs.jsonl"
    proposal_path = tmp_path / "proposal.json"
    proposal = {
        "schema_version": 1,
        "proposal_type": "worker_review_proposal",
        "run_id": "placeholder",
        "summary": "Proposal only; human/certification gate still required.",
        "claims": [
            {"id": "claim-1", "text": "Readonly context was analyzed.", "evidence_refs": ["artifact://agent-coverage/readonly-output.json"]}
        ],
        "failure_classification": "none",
        "lifecycle_target": "review_proposed",
        "evidence_refs": ["artifact://agent-coverage/proposal.json"],
    }
    started = ega.start_bounded_runtime_run(
        request={
            "task_ref": "task:arch-coverage",
            "worker_type": "claude-code",
            "worker_id": "claude-proposal",
            "run_purpose": "external_agent_proposal",
            "evidence_refs": ["artifact://agent-coverage/start-proof.json"],
        },
        ledger_path=ledger_path,
    )
    proposal["run_id"] = started["run_id"]
    proposal_path.write_text(json.dumps(proposal), encoding="utf-8")

    result = ega.append_external_agent_proposal(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        proposal_request={
            "proposal_artifact_uri": f"file://{proposal_path}",
            "proposal_sha256": hashlib.sha256(proposal_path.read_bytes()).hexdigest(),
            "evidence_refs": ["artifact://agent-coverage/proposal-submit.json"],
        },
    )

    assert result["accepted_as"] == "external_agent_proposal_recorded"
    assert result["run_status"] == "external_proposal_recorded"
    assert result["runtime_boundary"] == "external_agent_proposal_artifact_recorded"
    assert result["proposal"]["lifecycle_target"] == "review_proposed"
    assert result["allowed_to_mutate_lifecycle"] is False
    assert result["allowed_to_mark_done"] is False
    assert result["external_adapter_write_through_enabled"] is False
    assert result["artifact"]["artifact_uri"] == f"file://{proposal_path}"
    assert len(result["artifact"]["sha256"]) == 64
    assert result["claim_count"] == 1
    rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert [row["run_status"] for row in rows] == ["started", "external_proposal_recorded"]


def test_external_agent_proposal_artifact_rejects_done_and_unsupported_claims_cli_smoke(tmp_path) -> None:
    ledger_path = tmp_path / "agent-runtime-runs.jsonl"
    proposal_path = tmp_path / "proposal.json"
    started = ega.start_bounded_runtime_run(
        request={
            "task_ref": "task:arch-coverage",
            "worker_type": "claude-code",
            "worker_id": "claude-proposal-cli",
            "run_purpose": "external_agent_proposal",
            "evidence_refs": ["artifact://agent-coverage/start-proof.json"],
        },
        ledger_path=ledger_path,
    )
    rejected_proposal = {
        "schema_version": 1,
        "proposal_type": "worker_review_proposal",
        "run_id": started["run_id"],
        "summary": "bad",
        "claims": [{"id": "claim-1", "text": "unsupported"}],
        "failure_classification": "none",
        "lifecycle_target": "done",
        "evidence_refs": ["artifact://agent-coverage/rejected-proposal.json"],
    }
    proposal_path.write_text(json.dumps(rejected_proposal), encoding="utf-8")

    rejected = ega.append_external_agent_proposal(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        proposal_request={
            "proposal_artifact_uri": f"file://{proposal_path}",
            "proposal_sha256": hashlib.sha256(proposal_path.read_bytes()).hexdigest(),
            "task_status": "done",
            "evidence_refs": ["artifact://agent-coverage/rejected-submit.json"],
        },
    )

    assert rejected["accepted_as"] == "rejected_external_agent_proposal"
    assert rejected["proposal_recorded"] is False
    assert "proposal_request_contains_forbidden_authority_keys" in rejected["blocked_reasons"]
    assert "proposal_lifecycle_target_must_be_review_proposed" in rejected["blocked_reasons"]
    assert "all_proposal_claims_require_evidence_refs" in rejected["blocked_reasons"]
    assert len(ledger_path.read_text().splitlines()) == 1

    good_proposal = {
        "schema_version": 1,
        "proposal_type": "worker_review_proposal",
        "run_id": started["run_id"],
        "summary": "ready for certification, not done",
        "claims": [{"id": "claim-1", "text": "supported", "evidence_refs": ["artifact://agent-coverage/proof.json"]}],
        "failure_classification": "none",
        "lifecycle_target": "review_proposed",
        "evidence_refs": ["artifact://agent-coverage/good-proposal.json"],
    }
    proposal_path.write_text(json.dumps(good_proposal), encoding="utf-8")
    request_json = json.dumps(
        {
            "proposal_artifact_uri": f"file://{proposal_path}",
            "proposal_sha256": hashlib.sha256(proposal_path.read_bytes()).hexdigest(),
            "evidence_refs": ["artifact://agent-coverage/cli-proposal-submit.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--append-external-agent-proposal",
            "--runtime-ledger",
            str(ledger_path),
            "--run-id",
            started["run_id"],
            "--proposal-request-json",
            request_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_result = json.loads(proc.stdout)
    assert cli_result["accepted_as"] == "external_agent_proposal_recorded"
    assert cli_result["proposal"]["lifecycle_target"] == "review_proposed"
    assert len(ledger_path.read_text().splitlines()) == 2


def _write_recorded_proposal(tmp_path, *, run_purpose="external_agent_proposal"):
    ledger_path = tmp_path / "agent-runtime-runs.jsonl"
    proposal_path = tmp_path / "proposal.json"
    started = ega.start_bounded_runtime_run(
        request={
            "task_ref": "task:arch-coverage",
            "worker_type": "claude-code",
            "worker_id": "claude-certification",
            "run_purpose": run_purpose,
            "evidence_refs": ["artifact://agent-coverage/start-proof.json"],
        },
        ledger_path=ledger_path,
    )
    proposal = {
        "schema_version": 1,
        "proposal_type": "worker_review_proposal",
        "run_id": started["run_id"],
        "summary": "ready for certification, not done",
        "claims": [{"id": "claim-1", "text": "supported", "evidence_refs": ["artifact://agent-coverage/proof.json"]}],
        "failure_classification": "none",
        "lifecycle_target": "review_proposed",
        "evidence_refs": ["artifact://agent-coverage/proposal.json"],
    }
    proposal_path.write_text(json.dumps(proposal), encoding="utf-8")
    proposal_record = ega.append_external_agent_proposal(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        proposal_request={
            "proposal_artifact_uri": f"file://{proposal_path}",
            "proposal_sha256": hashlib.sha256(proposal_path.read_bytes()).hexdigest(),
            "evidence_refs": ["artifact://agent-coverage/proposal-submit.json"],
        },
    )
    return ledger_path, started, proposal_path, proposal_record


def test_certification_promotion_packet_records_passed_for_review_not_done(tmp_path) -> None:
    ledger_path, started, proposal_path, proposal_record = _write_recorded_proposal(tmp_path)
    certification_path = tmp_path / "certification.json"
    certification = {
        "schema_version": 1,
        "certification_type": "multi_model_proposal_certification",
        "run_id": started["run_id"],
        "proposal_artifact_uri": f"file://{proposal_path}",
        "proposal_sha256": proposal_record["artifact"]["sha256"],
        "reviewers": [
            {"id": "claude", "verdict": "pass", "evidence_refs": ["artifact://agent-coverage/claude-review.json"]},
            {"id": "codex", "verdict": "pass", "evidence_refs": ["artifact://agent-coverage/codex-review.json"]},
        ],
        "quorum": 2,
        "truth_gate": True,
        "claims": [{"id": "claim-1", "text": "supported", "evidence_refs": ["artifact://agent-coverage/proof.json"]}],
        "summary": "certification packet passed for review only",
        "lifecycle_target": "certification_passed_for_review",
        "evidence_refs": ["artifact://agent-coverage/certification.json"],
    }
    certification_path.write_text(json.dumps(certification), encoding="utf-8")

    result = ega.append_certification_promotion(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        certification_request={
            "certification_artifact_uri": f"file://{certification_path}",
            "certification_sha256": hashlib.sha256(certification_path.read_bytes()).hexdigest(),
            "evidence_refs": ["artifact://agent-coverage/certification-submit.json"],
        },
    )

    assert result["accepted_as"] == "certification_promotion_recorded"
    assert result["run_status"] == "certification_recorded"
    assert result["runtime_boundary"] == "multi_model_certification_promotion_recorded"
    assert result["certification"]["lifecycle_target"] == "certification_passed_for_review"
    assert result["reviewer_count"] == 2
    assert result["quorum"] == 2
    assert result["truth_gate"] is True
    assert result["allowed_to_mark_done"] is False
    assert result["allowed_to_mutate_lifecycle"] is False
    assert result["model_consensus_is_fact"] is False
    assert result["proposal_artifact"]["artifact_uri"] == f"file://{proposal_path}"
    assert result["artifact"]["artifact_uri"] == f"file://{certification_path}"
    rows = [json.loads(line) for line in ledger_path.read_text().splitlines()]
    assert [row["run_status"] for row in rows] == ["started", "external_proposal_recorded", "certification_recorded"]


def test_certification_promotion_rejects_weak_quorum_no_truth_unsupported_claim_and_done_cli_smoke(tmp_path) -> None:
    ledger_path, started, proposal_path, proposal_record = _write_recorded_proposal(tmp_path)
    certification_path = tmp_path / "certification.json"
    rejected_packet = {
        "schema_version": 1,
        "certification_type": "multi_model_proposal_certification",
        "run_id": started["run_id"],
        "proposal_artifact_uri": f"file://{proposal_path}",
        "proposal_sha256": proposal_record["artifact"]["sha256"],
        "reviewers": [{"id": "claude", "verdict": "pass"}],
        "quorum": 1,
        "truth_gate": False,
        "claims": [{"id": "claim-1", "text": "unsupported"}],
        "summary": "bad",
        "lifecycle_target": "done",
        "evidence_refs": ["artifact://agent-coverage/bad-certification.json"],
    }
    certification_path.write_text(json.dumps(rejected_packet), encoding="utf-8")

    rejected = ega.append_certification_promotion(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        certification_request={
            "certification_artifact_uri": f"file://{certification_path}",
            "certification_sha256": hashlib.sha256(certification_path.read_bytes()).hexdigest(),
            "task_status": "done",
            "evidence_refs": ["artifact://agent-coverage/bad-certification-submit.json"],
        },
    )

    assert rejected["accepted_as"] == "rejected_certification_promotion"
    assert rejected["certification_recorded"] is False
    assert "certification_request_contains_forbidden_authority_keys" in rejected["blocked_reasons"]
    assert "at_least_two_reviewers_required" in rejected["blocked_reasons"]
    assert "quorum_must_be_at_least_two" in rejected["blocked_reasons"]
    assert "truth_gate_required" in rejected["blocked_reasons"]
    assert "all_certification_claims_require_evidence_refs" in rejected["blocked_reasons"]
    assert "certification_lifecycle_target_must_be_passed_for_review" in rejected["blocked_reasons"]
    assert len(ledger_path.read_text().splitlines()) == 2

    good_packet = {
        "schema_version": 1,
        "certification_type": "multi_model_proposal_certification",
        "run_id": started["run_id"],
        "proposal_artifact_uri": f"file://{proposal_path}",
        "proposal_sha256": proposal_record["artifact"]["sha256"],
        "reviewers": [
            {"id": "claude", "verdict": "pass", "evidence_refs": ["artifact://agent-coverage/claude-review.json"]},
            {"id": "codex", "verdict": "pass", "evidence_refs": ["artifact://agent-coverage/codex-review.json"]},
        ],
        "quorum": 2,
        "truth_gate": True,
        "claims": [{"id": "claim-1", "text": "supported", "evidence_refs": ["artifact://agent-coverage/proof.json"]}],
        "summary": "certification packet passed for review only",
        "lifecycle_target": "certification_passed_for_review",
        "evidence_refs": ["artifact://agent-coverage/good-certification.json"],
    }
    certification_path.write_text(json.dumps(good_packet), encoding="utf-8")
    request_json = json.dumps(
        {
            "certification_artifact_uri": f"file://{certification_path}",
            "certification_sha256": hashlib.sha256(certification_path.read_bytes()).hexdigest(),
            "evidence_refs": ["artifact://agent-coverage/cli-certification-submit.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--append-certification-promotion",
            "--runtime-ledger",
            str(ledger_path),
            "--run-id",
            started["run_id"],
            "--certification-request-json",
            request_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_result = json.loads(proc.stdout)
    assert cli_result["accepted_as"] == "certification_promotion_recorded"
    assert cli_result["certification"]["lifecycle_target"] == "certification_passed_for_review"
    assert len(ledger_path.read_text().splitlines()) == 3


def _write_certified_run(tmp_path):
    ledger_path, started, proposal_path, proposal_record = _write_recorded_proposal(tmp_path)
    certification_path = tmp_path / "certification.json"
    certification = {
        "schema_version": 1,
        "certification_type": "multi_model_proposal_certification",
        "run_id": started["run_id"],
        "proposal_artifact_uri": f"file://{proposal_path}",
        "proposal_sha256": proposal_record["artifact"]["sha256"],
        "reviewers": [
            {"id": "claude", "verdict": "pass", "evidence_refs": ["artifact://agent-coverage/claude-review.json"]},
            {"id": "codex", "verdict": "pass", "evidence_refs": ["artifact://agent-coverage/codex-review.json"]},
        ],
        "quorum": 2,
        "truth_gate": True,
        "claims": [{"id": "claim-1", "text": "supported", "evidence_refs": ["artifact://agent-coverage/proof.json"]}],
        "summary": "certification packet passed for review only",
        "lifecycle_target": "certification_passed_for_review",
        "evidence_refs": ["artifact://agent-coverage/certification.json"],
    }
    certification_path.write_text(json.dumps(certification), encoding="utf-8")
    certification_record = ega.append_certification_promotion(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        certification_request={
            "certification_artifact_uri": f"file://{certification_path}",
            "certification_sha256": hashlib.sha256(certification_path.read_bytes()).hexdigest(),
            "evidence_refs": ["artifact://agent-coverage/certification-submit.json"],
        },
    )
    return ledger_path, started, proposal_path, proposal_record, certification_path, certification_record


def test_done_authorization_projection_approved_binds_certification_but_write_through_disabled(tmp_path) -> None:
    ledger_path, started, _proposal_path, _proposal_record, certification_path, certification_record = _write_certified_run(tmp_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()

    result = ega.request_done_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        authorization_request={
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "target_status": "done",
            "certification_artifact_uri": f"file://{certification_path}",
            "certification_sha256": certification_record["artifact"]["sha256"],
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/done-auth.json"],
        },
    )

    assert result["accepted_as"] == "done_authorization_projected"
    assert result["run_status"] == "done_authorization_approved"
    assert result["runtime_boundary"] == "human_elevated_done_authorization_projected"
    assert result["authorization_id"].startswith("auth_")
    assert result["target_status"] == "done"
    assert result["write_through_allowed"] is False
    assert result["fact_mutation_enabled"] is False
    assert result["allowed_to_mutate_lifecycle"] is False
    assert result["canonical_lifecycle_unchanged"] is True
    assert result["certification_artifact"]["sha256"] == certification_record["artifact"]["sha256"]
    assert len(ledger_path.read_text().splitlines()) == 4


def test_done_authorization_projection_rejects_non_elevated_expired_drift_and_cli_smoke(tmp_path) -> None:
    ledger_path, started, _proposal_path, _proposal_record, certification_path, certification_record = _write_certified_run(tmp_path)
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    rejected = ega.request_done_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        authorization_request={
            "actor_id": "worker",
            "actor_role": "bounded_worker",
            "decision": "approved",
            "target_status": "done",
            "certification_artifact_uri": f"file://{certification_path}",
            "certification_sha256": "0" * 64,
            "expires_at": expired_at,
            "write_through_allowed": True,
            "evidence_refs": ["artifact://agent-coverage/bad-auth.json"],
        },
    )

    assert rejected["accepted_as"] == "rejected_done_authorization_projection"
    assert rejected["authorization_projected"] is False
    assert "elevated_actor_role_required" in rejected["blocked_reasons"]
    assert "authorization_expired" in rejected["blocked_reasons"]
    assert "authorization_certification_sha256_mismatch" in rejected["blocked_reasons"]
    assert "write_through_must_remain_disabled" in rejected["blocked_reasons"]
    assert len(ledger_path.read_text().splitlines()) == 3

    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    request_json = json.dumps(
        {
            "actor_id": "human-admin-cli",
            "actor_role": "admin",
            "decision": "approved",
            "target_status": "done",
            "certification_artifact_uri": f"file://{certification_path}",
            "certification_sha256": certification_record["artifact"]["sha256"],
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/cli-done-auth.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--request-done-authorization",
            "--runtime-ledger",
            str(ledger_path),
            "--run-id",
            started["run_id"],
            "--authorization-request-json",
            request_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_result = json.loads(proc.stdout)
    assert cli_result["accepted_as"] == "done_authorization_projected"
    assert cli_result["target_status"] == "done"
    assert cli_result["write_through_allowed"] is False
    assert len(ledger_path.read_text().splitlines()) == 4


def _write_authorized_done_run(tmp_path):
    ledger_path, started, proposal_path, proposal_record, certification_path, certification_record = _write_certified_run(tmp_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    authorization_record = ega.request_done_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        authorization_request={
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "target_status": "done",
            "certification_artifact_uri": f"file://{certification_path}",
            "certification_sha256": certification_record["artifact"]["sha256"],
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/done-auth.json"],
        },
    )
    return ledger_path, started, proposal_path, proposal_record, certification_path, certification_record, authorization_record


def test_ledger_local_fact_mutation_marks_done_as_canonical_fact_and_is_idempotent(tmp_path) -> None:
    ledger_path, started, _proposal_path, _proposal_record, certification_path, certification_record, authorization_record = _write_authorized_done_run(tmp_path)

    result = ega.execute_ledger_fact_mutation(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        fact_request={
            "authorization_id": authorization_record["authorization_id"],
            "target_status": "done",
            "certification_artifact_uri": f"file://{certification_path}",
            "certification_sha256": certification_record["artifact"]["sha256"],
            "evidence_refs": ["artifact://agent-coverage/fact-mutation.json"],
        },
    )

    assert result["accepted_as"] == "canonical_lifecycle_fact_mutated"
    assert result["run_status"] == "fact_mutated"
    assert result["runtime_boundary"] == "ledger_local_canonical_fact_mutation"
    assert result["fact_is_canonical"] is True
    assert result["canonical_lifecycle_status"] == "done"
    assert result["allowed_to_mutate_lifecycle"] is True
    assert result["canonical_lifecycle_unchanged"] is False
    assert result["external_adapter_write_through_enabled"] is False
    assert result["authorization_id"] == authorization_record["authorization_id"]
    assert len(ledger_path.read_text().splitlines()) == 5

    replay = ega.execute_ledger_fact_mutation(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        fact_request={
            "authorization_id": authorization_record["authorization_id"],
            "target_status": "done",
            "certification_artifact_uri": f"file://{certification_path}",
            "certification_sha256": certification_record["artifact"]["sha256"],
            "evidence_refs": ["artifact://agent-coverage/fact-mutation.json"],
        },
    )
    assert replay["idempotent_replay"] is True
    assert replay["accepted_as"] == "canonical_lifecycle_fact_mutated"
    assert len(ledger_path.read_text().splitlines()) == 5


def test_ledger_local_fact_mutation_rejects_missing_authorization_expired_and_drift_cli_smoke(tmp_path) -> None:
    ledger_path, started, _proposal_path, _proposal_record, certification_path, certification_record = _write_certified_run(tmp_path)
    rejected = ega.execute_ledger_fact_mutation(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        fact_request={
            "authorization_id": "auth_missing",
            "target_status": "done",
            "certification_artifact_uri": f"file://{certification_path}",
            "certification_sha256": "0" * 64,
            "evidence_refs": ["artifact://agent-coverage/bad-fact.json"],
        },
    )
    assert rejected["accepted_as"] == "rejected_canonical_lifecycle_fact_mutation"
    assert rejected["fact_is_canonical"] is False
    assert "approved_authorization_required" in rejected["blocked_reasons"]
    assert "fact_certification_sha256_mismatch" in rejected["blocked_reasons"]
    assert len(ledger_path.read_text().splitlines()) == 3

    # Append an expired authorization directly to prove the executor re-checks TTL at execution time.
    expired_auth = {
        "schema_version": 1,
        "accepted_as": "done_authorization_projected",
        "run_status": "done_authorization_approved",
        "runtime_boundary": "human_elevated_done_authorization_projected",
        "authorization_projected": True,
        "authorization_id": "auth_expired",
        "run_id": started["run_id"],
        "target_status": "done",
        "expires_at": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        "certification_artifact": {"artifact_uri": f"file://{certification_path}", "sha256": certification_record["artifact"]["sha256"]},
        "evidence_refs": ["artifact://agent-coverage/expired-auth.json"],
    }
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(expired_auth) + "\n")
    expired_rejected = ega.execute_ledger_fact_mutation(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        fact_request={
            "authorization_id": "auth_expired",
            "target_status": "done",
            "certification_artifact_uri": f"file://{certification_path}",
            "certification_sha256": certification_record["artifact"]["sha256"],
            "evidence_refs": ["artifact://agent-coverage/expired-fact.json"],
        },
    )
    assert "authorization_expired" in expired_rejected["blocked_reasons"]

    _ledger_path, started2, _proposal_path2, _proposal_record2, certification_path2, certification_record2, authorization_record = _write_authorized_done_run(tmp_path / "cli")
    request_json = json.dumps(
        {
            "authorization_id": authorization_record["authorization_id"],
            "target_status": "done",
            "certification_artifact_uri": f"file://{certification_path2}",
            "certification_sha256": certification_record2["artifact"]["sha256"],
            "evidence_refs": ["artifact://agent-coverage/cli-fact-mutation.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--execute-ledger-fact-mutation",
            "--runtime-ledger",
            str(_ledger_path),
            "--run-id",
            started2["run_id"],
            "--fact-request-json",
            request_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_result = json.loads(proc.stdout)
    assert cli_result["accepted_as"] == "canonical_lifecycle_fact_mutated"
    assert cli_result["canonical_lifecycle_status"] == "done"
    assert cli_result["fact_is_canonical"] is True


def _write_done_fact_run(tmp_path):
    ledger_path, started, proposal_path, proposal_record, certification_path, certification_record, authorization_record = _write_authorized_done_run(tmp_path)
    fact_record = ega.execute_ledger_fact_mutation(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        fact_request={
            "authorization_id": authorization_record["authorization_id"],
            "target_status": "done",
            "certification_artifact_uri": f"file://{certification_path}",
            "certification_sha256": certification_record["artifact"]["sha256"],
            "evidence_refs": ["artifact://agent-coverage/fact-mutation.json"],
        },
    )
    return ledger_path, started, proposal_path, proposal_record, certification_path, certification_record, authorization_record, fact_record


def test_external_adapter_write_intent_projects_readiness_without_write_through(tmp_path) -> None:
    ledger_path, started, _proposal_path, _proposal_record, _certification_path, _certification_record, _authorization_record, fact_record = _write_done_fact_run(tmp_path)

    result = ega.request_external_adapter_write_intent(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        intent_request={
            "adapter": "taskpool",
            "target_ref": "task:arch-coverage",
            "target_status": "done",
            "canonical_fact_digest": fact_record["fact_request_digest"],
            "readiness_check": {"preflight": "passed", "current_remote_status": "review"},
            "readback_plan": {"method": "fetch_task", "expected_status": "done"},
            "rollback_plan": {"method": "restore_previous_status", "previous_status": "review"},
            "evidence_refs": ["artifact://agent-coverage/write-intent.json"],
        },
    )

    assert result["accepted_as"] == "external_adapter_write_intent_projected"
    assert result["run_status"] == "external_adapter_write_intent_recorded"
    assert result["runtime_boundary"] == "external_adapter_write_intent_projection"
    assert result["adapter"] == "taskpool"
    assert result["target_status"] == "done"
    assert result["canonical_fact_required"] is True
    assert result["canonical_fact_digest"] == fact_record["fact_request_digest"]
    assert result["readiness_required"] is True
    assert result["readback_required"] is True
    assert result["rollback_required"] is True
    assert result["write_through_allowed"] is False
    assert result["external_adapter_write_through_enabled"] is False
    assert len(ledger_path.read_text().splitlines()) == 6


def test_external_adapter_write_intent_rejects_missing_fact_bad_adapter_and_direct_write_cli_smoke(tmp_path) -> None:
    ledger_path, started, _proposal_path, _proposal_record, _certification_path, _certification_record = _write_certified_run(tmp_path)
    rejected = ega.request_external_adapter_write_intent(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        intent_request={
            "adapter": "unknown",
            "target_ref": "task:arch-coverage",
            "target_status": "done",
            "canonical_fact_digest": "missing",
            "readiness_check": {"preflight": "passed"},
            "readback_plan": {"method": "fetch_task"},
            "rollback_plan": {"method": "restore"},
            "write_through_enabled": True,
            "external_write": True,
            "evidence_refs": ["artifact://agent-coverage/bad-write-intent.json"],
        },
    )
    assert rejected["accepted_as"] == "rejected_external_adapter_write_intent"
    assert rejected["write_through_allowed"] is False
    assert "canonical_done_fact_required" in rejected["blocked_reasons"]
    assert "adapter_not_allowlisted" in rejected["blocked_reasons"]
    assert "write_intent_contains_forbidden_authority_keys" in rejected["blocked_reasons"]
    assert len(ledger_path.read_text().splitlines()) == 3

    _ledger_path, started2, _proposal_path2, _proposal_record2, _certification_path2, _certification_record2, _authorization_record2, fact_record = _write_done_fact_run(tmp_path / "cli")
    request_json = json.dumps(
        {
            "adapter": "kanban",
            "target_ref": "card:arch-coverage",
            "target_status": "done",
            "canonical_fact_digest": fact_record["fact_request_digest"],
            "readiness_check": {"preflight": "passed", "current_remote_status": "review"},
            "readback_plan": {"method": "fetch_card", "expected_status": "done"},
            "rollback_plan": {"method": "restore_previous_status", "previous_status": "review"},
            "evidence_refs": ["artifact://agent-coverage/cli-write-intent.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--request-external-adapter-write-intent",
            "--runtime-ledger",
            str(_ledger_path),
            "--run-id",
            started2["run_id"],
            "--adapter-intent-json",
            request_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_result = json.loads(proc.stdout)
    assert cli_result["accepted_as"] == "external_adapter_write_intent_projected"
    assert cli_result["adapter"] == "kanban"
    assert cli_result["write_through_allowed"] is False


def _write_adapter_intent_run(tmp_path):
    ledger_path, started, proposal_path, proposal_record, certification_path, certification_record, authorization_record, fact_record = _write_done_fact_run(tmp_path)
    intent_record = ega.request_external_adapter_write_intent(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        intent_request={
            "adapter": "taskpool",
            "target_ref": "task:arch-coverage",
            "target_status": "done",
            "canonical_fact_digest": fact_record["fact_request_digest"],
            "readiness_check": {"preflight": "passed", "current_remote_status": "review"},
            "readback_plan": {"method": "fetch_task", "expected_status": "done"},
            "rollback_plan": {"method": "restore_previous_status", "previous_status": "review"},
            "evidence_refs": ["artifact://agent-coverage/write-intent.json"],
        },
    )
    return ledger_path, started, proposal_path, proposal_record, certification_path, certification_record, authorization_record, fact_record, intent_record


def test_external_adapter_write_authorization_approves_intent_but_still_no_write(tmp_path) -> None:
    ledger_path, started, *_rest, intent_record = _write_adapter_intent_run(tmp_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()

    result = ega.request_external_adapter_write_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        authorization_request={
            "intent_id": intent_record["intent_id"],
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "adapter": "taskpool",
            "target_ref": "task:arch-coverage",
            "target_status": "done",
            "canonical_fact_digest": intent_record["canonical_fact_digest"],
            "intent_request_digest": intent_record["request_digest"],
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/write-auth.json"],
        },
    )

    assert result["accepted_as"] == "external_adapter_write_authorization_projected"
    assert result["run_status"] == "external_adapter_write_authorization_approved"
    assert result["runtime_boundary"] == "external_adapter_write_authorization_projection"
    assert result["intent_id"] == intent_record["intent_id"]
    assert result["adapter"] == "taskpool"
    assert result["target_ref"] == "task:arch-coverage"
    assert result["target_status"] == "done"
    assert result["write_authorized"] is True
    assert result["write_executed"] is False
    assert result["external_adapter_write_through_enabled"] is False
    assert result["readback_required"] is True
    assert result["rollback_required"] is True
    assert len(ledger_path.read_text().splitlines()) == 7


def test_external_adapter_write_authorization_rejects_non_elevated_expired_drift_and_cli_smoke(tmp_path) -> None:
    ledger_path, started, *_rest, intent_record = _write_adapter_intent_run(tmp_path)
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    rejected = ega.request_external_adapter_write_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        authorization_request={
            "intent_id": intent_record["intent_id"],
            "actor_id": "worker-1",
            "actor_role": "bounded_worker",
            "decision": "approved",
            "adapter": "lark",
            "target_ref": "task:arch-coverage",
            "target_status": "done",
            "canonical_fact_digest": "drifted",
            "intent_request_digest": "drifted",
            "expires_at": expired,
            "write_through_enabled": True,
            "external_write": True,
            "evidence_refs": ["artifact://agent-coverage/bad-write-auth.json"],
        },
    )
    assert rejected["accepted_as"] == "rejected_external_adapter_write_authorization"
    assert rejected["write_authorized"] is False
    assert rejected["external_adapter_write_through_enabled"] is False
    assert "elevated_actor_required" in rejected["blocked_reasons"]
    assert "authorization_expired" in rejected["blocked_reasons"]
    assert "authorization_adapter_mismatch" in rejected["blocked_reasons"]
    assert "authorization_canonical_fact_digest_mismatch" in rejected["blocked_reasons"]
    assert "write_authorization_contains_forbidden_authority_keys" in rejected["blocked_reasons"]
    assert len(ledger_path.read_text().splitlines()) == 6

    _ledger_path, started2, *_rest2, intent_record2 = _write_adapter_intent_run(tmp_path / "cli")
    request_json = json.dumps(
        {
            "intent_id": intent_record2["intent_id"],
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "adapter": intent_record2["adapter"],
            "target_ref": intent_record2["target_ref"],
            "target_status": intent_record2["target_status"],
            "canonical_fact_digest": intent_record2["canonical_fact_digest"],
            "intent_request_digest": intent_record2["request_digest"],
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            "evidence_refs": ["artifact://agent-coverage/cli-write-auth.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--request-external-adapter-write-authorization",
            "--runtime-ledger",
            str(_ledger_path),
            "--run-id",
            started2["run_id"],
            "--adapter-authorization-json",
            request_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_result = json.loads(proc.stdout)
    assert cli_result["accepted_as"] == "external_adapter_write_authorization_projected"
    assert cli_result["write_authorized"] is True
    assert cli_result["write_executed"] is False


def _write_adapter_authorization_run(tmp_path):
    ledger_path, started, proposal_path, proposal_record, certification_path, certification_record, authorization_record, fact_record, intent_record = _write_adapter_intent_run(tmp_path)
    write_authorization = ega.request_external_adapter_write_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        authorization_request={
            "intent_id": intent_record["intent_id"],
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "adapter": intent_record["adapter"],
            "target_ref": intent_record["target_ref"],
            "target_status": intent_record["target_status"],
            "canonical_fact_digest": intent_record["canonical_fact_digest"],
            "intent_request_digest": intent_record["request_digest"],
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            "evidence_refs": ["artifact://agent-coverage/write-auth.json"],
        },
    )
    return (
        ledger_path,
        started,
        proposal_path,
        proposal_record,
        certification_path,
        certification_record,
        authorization_record,
        fact_record,
        intent_record,
        write_authorization,
    )


def test_external_adapter_local_file_executor_writes_and_readback_verifies_idempotently(tmp_path) -> None:
    ledger_path, started, *_rest, intent_record, write_authorization = _write_adapter_authorization_run(tmp_path)
    adapter_state_path = tmp_path / "adapter-state.json"
    adapter_state_path.write_text(json.dumps({"tasks": {"task:arch-coverage": {"status": "review"}}}))

    result = ega.execute_external_adapter_local_file_write(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_request={
            "authorization_id": write_authorization["authorization_id"],
            "intent_id": intent_record["intent_id"],
            "adapter": "taskpool",
            "target_ref": "task:arch-coverage",
            "target_status": "done",
            "adapter_state_path": str(adapter_state_path),
            "expected_previous_status": "review",
            "evidence_refs": ["artifact://agent-coverage/write-execute.json"],
        },
    )

    assert result["accepted_as"] == "external_adapter_write_verified"
    assert result["run_status"] == "external_adapter_write_verified"
    assert result["runtime_boundary"] == "external_adapter_local_file_write_through"
    assert result["write_executed"] is True
    assert result["readback_verified"] is True
    assert result["external_adapter_write_through_enabled"] is True
    assert result["rollback_available"] is True
    assert json.loads(adapter_state_path.read_text())["tasks"]["task:arch-coverage"]["status"] == "done"
    assert len(ledger_path.read_text().splitlines()) == 8

    replay = ega.execute_external_adapter_local_file_write(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_request={
            "authorization_id": write_authorization["authorization_id"],
            "intent_id": intent_record["intent_id"],
            "adapter": "taskpool",
            "target_ref": "task:arch-coverage",
            "target_status": "done",
            "adapter_state_path": str(adapter_state_path),
            "expected_previous_status": "done",
            "evidence_refs": ["artifact://agent-coverage/write-execute-replay.json"],
        },
    )
    assert replay["accepted_as"] == "external_adapter_write_verified"
    assert replay["idempotent_replay"] is True
    assert len(ledger_path.read_text().splitlines()) == 8


def test_external_adapter_local_file_executor_rejects_missing_authorization_drift_and_cli_smoke(tmp_path) -> None:
    ledger_path, started, *_rest, intent_record, write_authorization = _write_adapter_authorization_run(tmp_path)
    missing_state_path = tmp_path / "missing-adapter-state.json"
    rejected = ega.execute_external_adapter_local_file_write(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_request={
            "authorization_id": "missing-auth",
            "intent_id": intent_record["intent_id"],
            "adapter": "lark",
            "target_ref": "task:arch-coverage",
            "target_status": "done",
            "adapter_state_path": str(missing_state_path),
            "expected_previous_status": "review",
            "evidence_refs": ["artifact://agent-coverage/bad-write-execute.json"],
        },
    )
    assert rejected["accepted_as"] == "rejected_external_adapter_write_execution"
    assert rejected["write_executed"] is False
    assert "write_authorization_required" in rejected["blocked_reasons"]
    assert "adapter_state_path_missing" in rejected["blocked_reasons"]
    assert len(ledger_path.read_text().splitlines()) == 7

    _ledger_path, started2, *_rest2, intent_record2, write_authorization2 = _write_adapter_authorization_run(tmp_path / "cli")
    adapter_state_path = tmp_path / "cli" / "adapter-state.json"
    adapter_state_path.parent.mkdir(parents=True, exist_ok=True)
    adapter_state_path.write_text(json.dumps({"tasks": {intent_record2["target_ref"]: {"status": "review"}}}))
    request_json = json.dumps(
        {
            "authorization_id": write_authorization2["authorization_id"],
            "intent_id": intent_record2["intent_id"],
            "adapter": intent_record2["adapter"],
            "target_ref": intent_record2["target_ref"],
            "target_status": intent_record2["target_status"],
            "adapter_state_path": str(adapter_state_path),
            "expected_previous_status": "review",
            "evidence_refs": ["artifact://agent-coverage/cli-write-execute.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--execute-external-adapter-local-file-write",
            "--runtime-ledger",
            str(_ledger_path),
            "--run-id",
            started2["run_id"],
            "--adapter-execution-json",
            request_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_result = json.loads(proc.stdout)
    assert cli_result["accepted_as"] == "external_adapter_write_verified"
    assert cli_result["write_executed"] is True
    assert cli_result["readback_verified"] is True


def _write_taskpool_authorization_run(tmp_path):
    ledger_path, started, proposal_path, proposal_record, certification_path, certification_record, authorization_record, fact_record = _write_done_fact_run(tmp_path)
    intent_record = ega.request_external_adapter_write_intent(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        intent_request={
            "adapter": "taskpool",
            "target_ref": "arch-coverage-task",
            "target_status": "done",
            "canonical_fact_digest": fact_record["fact_request_digest"],
            "readiness_check": {"preflight": "passed", "current_remote_status": "review"},
            "readback_plan": {"method": "read_task_note_frontmatter", "expected_status": "done"},
            "rollback_plan": {"method": "restore_previous_frontmatter_status", "previous_status": "review"},
            "evidence_refs": ["artifact://agent-coverage/taskpool-write-intent.json"],
        },
    )
    write_authorization = ega.request_external_adapter_write_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        authorization_request={
            "intent_id": intent_record["intent_id"],
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "adapter": intent_record["adapter"],
            "target_ref": intent_record["target_ref"],
            "target_status": intent_record["target_status"],
            "canonical_fact_digest": intent_record["canonical_fact_digest"],
            "intent_request_digest": intent_record["request_digest"],
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            "evidence_refs": ["artifact://agent-coverage/taskpool-write-auth.json"],
        },
    )
    return ledger_path, started, intent_record, write_authorization


def test_taskpool_note_closeout_write_through_updates_status_and_readback_idempotently(tmp_path) -> None:
    ledger_path, started, intent_record, write_authorization = _write_taskpool_authorization_run(tmp_path)
    tasks_dir = tmp_path / "vault" / "05-实践与项目" / "tasks"
    tasks_dir.mkdir(parents=True)
    note_path = tasks_dir / "arch-coverage-task.md"
    note_path.write_text("---\ntitle: Arch coverage task\nstatus: review\n---\n\nBody stays intact.\n", encoding="utf-8")

    result = ega.execute_taskpool_note_closeout_write(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_request={
            "authorization_id": write_authorization["authorization_id"],
            "intent_id": intent_record["intent_id"],
            "adapter": "taskpool",
            "target_ref": "arch-coverage-task",
            "target_status": "done",
            "taskpool_tasks_dir": str(tasks_dir),
            "expected_previous_status": "review",
            "evidence_refs": ["artifact://agent-coverage/taskpool-write-execute.json"],
        },
    )

    text = note_path.read_text(encoding="utf-8")
    assert result["accepted_as"] == "taskpool_note_write_verified"
    assert result["run_status"] == "taskpool_note_write_verified"
    assert result["runtime_boundary"] == "taskpool_note_closeout_write_through"
    assert result["write_executed"] is True
    assert result["readback_verified"] is True
    assert result["previous_status"] == "review"
    assert result["readback_status"] == "done"
    assert "status: done" in text
    assert "Body stays intact." in text
    assert "hermes_taskpool_write_through" in text
    assert len(ledger_path.read_text().splitlines()) == 8

    replay = ega.execute_taskpool_note_closeout_write(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_request={
            "authorization_id": write_authorization["authorization_id"],
            "intent_id": intent_record["intent_id"],
            "adapter": "taskpool",
            "target_ref": "arch-coverage-task",
            "target_status": "done",
            "taskpool_tasks_dir": str(tasks_dir),
            "expected_previous_status": "done",
            "evidence_refs": ["artifact://agent-coverage/taskpool-write-replay.json"],
        },
    )
    assert replay["idempotent_replay"] is True
    assert len(ledger_path.read_text().splitlines()) == 8


def test_taskpool_note_closeout_write_rejects_path_escape_symlink_and_cli_smoke(tmp_path) -> None:
    ledger_path, started, intent_record, write_authorization = _write_taskpool_authorization_run(tmp_path)
    tasks_dir = tmp_path / "vault" / "05-实践与项目" / "tasks"
    tasks_dir.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("---\nstatus: review\n---\noutside\n", encoding="utf-8")
    symlink_note = tasks_dir / "arch-coverage-task.md"
    symlink_note.symlink_to(outside)

    rejected = ega.execute_taskpool_note_closeout_write(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_request={
            "authorization_id": write_authorization["authorization_id"],
            "intent_id": intent_record["intent_id"],
            "adapter": "taskpool",
            "target_ref": "arch-coverage-task",
            "target_status": "done",
            "taskpool_tasks_dir": str(tasks_dir),
            "expected_previous_status": "review",
            "evidence_refs": ["artifact://agent-coverage/taskpool-symlink-reject.json"],
        },
    )
    assert rejected["accepted_as"] == "rejected_taskpool_note_write_execution"
    assert rejected["write_executed"] is False
    assert "taskpool_note_is_symlink" in rejected["blocked_reasons"]
    assert "status: review" in outside.read_text(encoding="utf-8")

    bad_ref = ega.execute_taskpool_note_closeout_write(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_request={
            "authorization_id": write_authorization["authorization_id"],
            "intent_id": intent_record["intent_id"],
            "adapter": "taskpool",
            "target_ref": "../escape",
            "target_status": "done",
            "taskpool_tasks_dir": str(tasks_dir),
            "expected_previous_status": "review",
            "evidence_refs": ["artifact://agent-coverage/taskpool-bad-ref.json"],
        },
    )
    assert "target_ref_unsafe" in bad_ref["blocked_reasons"]

    _ledger_path, started2, intent_record2, write_authorization2 = _write_taskpool_authorization_run(tmp_path / "cli")
    cli_tasks_dir = tmp_path / "cli" / "vault" / "05-实践与项目" / "tasks"
    cli_tasks_dir.mkdir(parents=True)
    (cli_tasks_dir / "arch-coverage-task.md").write_text("---\ntitle: CLI task\nstatus: review\n---\n\nCLI body.\n", encoding="utf-8")
    request_json = json.dumps(
        {
            "authorization_id": write_authorization2["authorization_id"],
            "intent_id": intent_record2["intent_id"],
            "adapter": "taskpool",
            "target_ref": "arch-coverage-task",
            "target_status": "done",
            "taskpool_tasks_dir": str(cli_tasks_dir),
            "expected_previous_status": "review",
            "evidence_refs": ["artifact://agent-coverage/cli-taskpool-write.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--execute-taskpool-note-closeout-write",
            "--runtime-ledger",
            str(_ledger_path),
            "--run-id",
            started2["run_id"],
            "--taskpool-execution-json",
            request_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_result = json.loads(proc.stdout)
    assert cli_result["accepted_as"] == "taskpool_note_write_verified"
    assert cli_result["write_executed"] is True
    assert cli_result["readback_verified"] is True


def _write_kanban_authorization_run(tmp_path):
    ledger_path, started, proposal_path, proposal_record, certification_path, certification_record, authorization_record, fact_record = _write_done_fact_run(tmp_path)
    intent_record = ega.request_external_adapter_write_intent(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        intent_request={
            "adapter": "kanban",
            "target_ref": "kb_arch_task",
            "target_status": "done",
            "canonical_fact_digest": fact_record["fact_request_digest"],
            "readiness_check": {"preflight": "passed", "current_remote_status": "review"},
            "readback_plan": {"method": "sqlite_select_task_status", "expected_status": "done"},
            "rollback_plan": {"method": "sqlite_restore_previous_status", "previous_status": "review"},
            "evidence_refs": ["artifact://agent-coverage/kanban-write-intent.json"],
        },
    )
    write_authorization = ega.request_external_adapter_write_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        authorization_request={
            "intent_id": intent_record["intent_id"],
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "adapter": intent_record["adapter"],
            "target_ref": intent_record["target_ref"],
            "target_status": intent_record["target_status"],
            "canonical_fact_digest": intent_record["canonical_fact_digest"],
            "intent_request_digest": intent_record["request_digest"],
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            "evidence_refs": ["artifact://agent-coverage/kanban-write-auth.json"],
        },
    )
    return ledger_path, started, intent_record, write_authorization


def test_kanban_db_closeout_write_updates_status_and_readback_idempotently(tmp_path) -> None:
    ledger_path, started, intent_record, write_authorization = _write_kanban_authorization_run(tmp_path)
    db_path = tmp_path / "kanban.db"
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT)")
        conn.execute("INSERT INTO tasks (id, title, status) VALUES (?, ?, ?)", ("kb_arch_task", "Arch", "review"))

    result = ega.execute_kanban_db_closeout_write(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_request={
            "authorization_id": write_authorization["authorization_id"],
            "intent_id": intent_record["intent_id"],
            "adapter": "kanban",
            "target_ref": "kb_arch_task",
            "target_status": "done",
            "kanban_db_path": str(db_path),
            "expected_previous_status": "review",
            "evidence_refs": ["artifact://agent-coverage/kanban-write-execute.json"],
        },
    )
    assert result["accepted_as"] == "kanban_db_write_verified"
    assert result["write_executed"] is True
    assert result["readback_verified"] is True
    assert result["previous_status"] == "review"
    assert result["readback_status"] == "done"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT status FROM tasks WHERE id=?", ("kb_arch_task",)).fetchone()[0] == "done"
    assert len(ledger_path.read_text().splitlines()) == 8

    replay = ega.execute_kanban_db_closeout_write(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_request={
            "authorization_id": write_authorization["authorization_id"],
            "intent_id": intent_record["intent_id"],
            "adapter": "kanban",
            "target_ref": "kb_arch_task",
            "target_status": "done",
            "kanban_db_path": str(db_path),
            "expected_previous_status": "done",
            "evidence_refs": ["artifact://agent-coverage/kanban-write-replay.json"],
        },
    )
    assert replay["idempotent_replay"] is True
    assert len(ledger_path.read_text().splitlines()) == 8


def test_kanban_db_closeout_write_rejects_symlink_missing_table_and_cli_smoke(tmp_path) -> None:
    ledger_path, started, intent_record, write_authorization = _write_kanban_authorization_run(tmp_path)
    db_path = tmp_path / "kanban.db"
    outside = tmp_path / "outside.db"
    outside.write_text("outside", encoding="utf-8")
    db_path.symlink_to(outside)

    rejected = ega.execute_kanban_db_closeout_write(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_request={
            "authorization_id": write_authorization["authorization_id"],
            "intent_id": intent_record["intent_id"],
            "adapter": "kanban",
            "target_ref": "kb_arch_task",
            "target_status": "done",
            "kanban_db_path": str(db_path),
            "expected_previous_status": "review",
            "evidence_refs": ["artifact://agent-coverage/kanban-symlink-reject.json"],
        },
    )
    assert rejected["accepted_as"] == "rejected_kanban_db_write_execution"
    assert "kanban_db_is_symlink" in rejected["blocked_reasons"]

    _ledger_path, started2, intent_record2, write_authorization2 = _write_kanban_authorization_run(tmp_path / "cli")
    cli_db = tmp_path / "cli" / "kanban.db"
    cli_db.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3
    with sqlite3.connect(cli_db) as conn:
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT)")
        conn.execute("INSERT INTO tasks (id, title, status) VALUES (?, ?, ?)", ("kb_arch_task", "Arch", "review"))
    request_json = json.dumps(
        {
            "authorization_id": write_authorization2["authorization_id"],
            "intent_id": intent_record2["intent_id"],
            "adapter": "kanban",
            "target_ref": "kb_arch_task",
            "target_status": "done",
            "kanban_db_path": str(cli_db),
            "expected_previous_status": "review",
            "evidence_refs": ["artifact://agent-coverage/cli-kanban-write.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--execute-kanban-db-closeout-write",
            "--runtime-ledger",
            str(_ledger_path),
            "--run-id",
            started2["run_id"],
            "--kanban-execution-json",
            request_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_result = json.loads(proc.stdout)
    assert cli_result["accepted_as"] == "kanban_db_write_verified"
    assert cli_result["write_executed"] is True
    assert cli_result["readback_verified"] is True


def test_external_adapter_rollback_restores_taskpool_note_and_kanban_db_idempotently(tmp_path) -> None:
    task_ledger, task_started, task_intent, task_auth = _write_taskpool_authorization_run(tmp_path / "taskpool")
    tasks_dir = tmp_path / "vault" / "05-实践与项目" / "tasks"
    tasks_dir.mkdir(parents=True)
    note = tasks_dir / "arch-coverage-task.md"
    note.write_text("---\ntitle: Rollback task\nstatus: review\n---\n\nBody.\n", encoding="utf-8")
    task_write = ega.execute_taskpool_note_closeout_write(
        ledger_path=task_ledger,
        run_id=task_started["run_id"],
        execution_request={
            "authorization_id": task_auth["authorization_id"],
            "intent_id": task_intent["intent_id"],
            "adapter": "taskpool",
            "target_ref": "arch-coverage-task",
            "target_status": "done",
            "taskpool_tasks_dir": str(tasks_dir),
            "expected_previous_status": "review",
            "evidence_refs": ["artifact://agent-coverage/taskpool-write-execute.json"],
        },
    )
    task_rollback = ega.execute_external_adapter_rollback_write(
        ledger_path=task_ledger,
        run_id=task_started["run_id"],
        rollback_request={
            "adapter": "taskpool",
            "authorization_id": task_auth["authorization_id"],
            "intent_id": task_intent["intent_id"],
            "target_ref": "arch-coverage-task",
            "target_status": "review",
            "expected_current_status": "done",
            "write_request_digest": task_write["request_digest"],
            "taskpool_tasks_dir": str(tasks_dir),
            "evidence_refs": ["artifact://agent-coverage/taskpool-rollback.json"],
        },
    )
    assert task_rollback["accepted_as"] == "external_adapter_rollback_verified"
    assert task_rollback["adapter"] == "taskpool"
    assert task_rollback["readback_status"] == "review"
    assert "status: review" in note.read_text(encoding="utf-8")
    replay = ega.execute_external_adapter_rollback_write(
        ledger_path=task_ledger,
        run_id=task_started["run_id"],
        rollback_request={
            "adapter": "taskpool",
            "authorization_id": task_auth["authorization_id"],
            "intent_id": task_intent["intent_id"],
            "target_ref": "arch-coverage-task",
            "target_status": "review",
            "expected_current_status": "review",
            "write_request_digest": task_write["request_digest"],
            "taskpool_tasks_dir": str(tasks_dir),
            "evidence_refs": ["artifact://agent-coverage/taskpool-rollback-replay.json"],
        },
    )
    assert replay["idempotent_replay"] is True

    kanban_ledger, kanban_started, kanban_intent, kanban_auth = _write_kanban_authorization_run(tmp_path / "kanban")
    db_path = tmp_path / "kanban" / "kanban.db"
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT)")
        conn.execute("INSERT INTO tasks (id, title, status) VALUES (?, ?, ?)", ("kb_arch_task", "Arch", "review"))
    kanban_write = ega.execute_kanban_db_closeout_write(
        ledger_path=kanban_ledger,
        run_id=kanban_started["run_id"],
        execution_request={
            "authorization_id": kanban_auth["authorization_id"],
            "intent_id": kanban_intent["intent_id"],
            "adapter": "kanban",
            "target_ref": "kb_arch_task",
            "target_status": "done",
            "kanban_db_path": str(db_path),
            "expected_previous_status": "review",
            "evidence_refs": ["artifact://agent-coverage/kanban-write-execute.json"],
        },
    )
    kanban_rollback = ega.execute_external_adapter_rollback_write(
        ledger_path=kanban_ledger,
        run_id=kanban_started["run_id"],
        rollback_request={
            "adapter": "kanban",
            "authorization_id": kanban_auth["authorization_id"],
            "intent_id": kanban_intent["intent_id"],
            "target_ref": "kb_arch_task",
            "target_status": "review",
            "expected_current_status": "done",
            "write_request_digest": kanban_write["request_digest"],
            "kanban_db_path": str(db_path),
            "evidence_refs": ["artifact://agent-coverage/kanban-rollback.json"],
        },
    )
    assert kanban_rollback["accepted_as"] == "external_adapter_rollback_verified"
    assert kanban_rollback["adapter"] == "kanban"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT status FROM tasks WHERE id=?", ("kb_arch_task",)).fetchone()[0] == "review"


def test_external_adapter_rollback_rejects_digest_drift_current_drift_and_cli_smoke(tmp_path) -> None:
    ledger_path, started, intent_record, write_authorization = _write_kanban_authorization_run(tmp_path)
    db_path = tmp_path / "kanban.db"
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT)")
        conn.execute("INSERT INTO tasks (id, title, status) VALUES (?, ?, ?)", ("kb_arch_task", "Arch", "review"))
    write = ega.execute_kanban_db_closeout_write(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_request={
            "authorization_id": write_authorization["authorization_id"],
            "intent_id": intent_record["intent_id"],
            "adapter": "kanban",
            "target_ref": "kb_arch_task",
            "target_status": "done",
            "kanban_db_path": str(db_path),
            "expected_previous_status": "review",
            "evidence_refs": ["artifact://agent-coverage/kanban-write-execute.json"],
        },
    )
    rejected = ega.execute_external_adapter_rollback_write(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        rollback_request={
            "adapter": "kanban",
            "authorization_id": write_authorization["authorization_id"],
            "intent_id": intent_record["intent_id"],
            "target_ref": "kb_arch_task",
            "target_status": "review",
            "expected_current_status": "done",
            "write_request_digest": "deadbeef",
            "kanban_db_path": str(db_path),
            "evidence_refs": ["artifact://agent-coverage/kanban-rollback-drift.json"],
        },
    )
    assert rejected["accepted_as"] == "rejected_external_adapter_rollback"
    assert "verified_write_event_required" in rejected["blocked_reasons"]

    request_json = json.dumps(
        {
            "adapter": "kanban",
            "authorization_id": write_authorization["authorization_id"],
            "intent_id": intent_record["intent_id"],
            "target_ref": "kb_arch_task",
            "target_status": "review",
            "expected_current_status": "done",
            "write_request_digest": write["request_digest"],
            "kanban_db_path": str(db_path),
            "evidence_refs": ["artifact://agent-coverage/cli-kanban-rollback.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--execute-external-adapter-rollback-write",
            "--runtime-ledger",
            str(ledger_path),
            "--run-id",
            started["run_id"],
            "--adapter-rollback-json",
            request_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_result = json.loads(proc.stdout)
    assert cli_result["accepted_as"] == "external_adapter_rollback_verified"
    assert cli_result["write_executed"] is True
    assert cli_result["readback_verified"] is True


def test_outbound_outbox_executor_records_pending_item_without_sending_and_is_idempotent(tmp_path) -> None:
    ledger_path, started, intent_record, write_authorization = _write_kanban_authorization_run(tmp_path)
    db_path = tmp_path / "kanban.db"
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT)")
        conn.execute("INSERT INTO tasks (id, title, status) VALUES (?, ?, ?)", ("kb_arch_task", "Arch", "review"))
    write = ega.execute_kanban_db_closeout_write(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_request={
            "authorization_id": write_authorization["authorization_id"],
            "intent_id": intent_record["intent_id"],
            "adapter": "kanban",
            "target_ref": "kb_arch_task",
            "target_status": "done",
            "kanban_db_path": str(db_path),
            "expected_previous_status": "review",
            "evidence_refs": ["artifact://agent-coverage/kanban-write-execute.json"],
        },
    )
    outbox_path = tmp_path / "outbox" / "outbound.jsonl"
    result = ega.execute_outbound_outbox_enqueue(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        outbox_request={
            "source_run_status": "kanban_db_write_verified",
            "source_request_digest": write["request_digest"],
            "channels": ["telegram", "lark"],
            "target_id": "ops-review-room",
            "target_allowlist": ["ops-review-room"],
            "outbox_path": str(outbox_path),
            "payload": {"text": "Kanban task kb_arch_task closed by governed write-through."},
            "evidence_refs": ["artifact://agent-coverage/outbox.json"],
        },
    )
    assert result["accepted_as"] == "outbound_outbox_item_enqueued"
    assert result["delivery_status"] == "pending"
    assert result["send_executed"] is False
    assert result["readback_verified"] is True
    assert result["channels"] == ["telegram", "lark"]
    assert "payload_hash" in result
    assert "payload" not in result
    outbox_rows = [json.loads(line) for line in outbox_path.read_text(encoding="utf-8").splitlines()]
    assert len(outbox_rows) == 1
    assert outbox_rows[0]["payload_hash"] == result["payload_hash"]
    assert outbox_rows[0]["delivery_status"] == "pending"
    assert "payload" not in outbox_rows[0]

    replay = ega.execute_outbound_outbox_enqueue(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        outbox_request={
            "source_run_status": "kanban_db_write_verified",
            "source_request_digest": write["request_digest"],
            "channels": ["telegram"],
            "target_id": "ops-review-room",
            "target_allowlist": ["ops-review-room"],
            "outbox_path": str(outbox_path),
            "payload": {"text": "Kanban task kb_arch_task closed by governed write-through."},
            "evidence_refs": ["artifact://agent-coverage/outbox-replay.json"],
        },
    )
    assert replay["idempotent_replay"] is True
    assert len(outbox_path.read_text(encoding="utf-8").splitlines()) == 1


def test_outbound_outbox_rejects_unverified_source_secret_payload_bad_target_and_cli_smoke(tmp_path) -> None:
    ledger_path, started, intent_record, write_authorization = _write_kanban_authorization_run(tmp_path)
    db_path = tmp_path / "kanban.db"
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT)")
        conn.execute("INSERT INTO tasks (id, title, status) VALUES (?, ?, ?)", ("kb_arch_task", "Arch", "review"))
    write = ega.execute_kanban_db_closeout_write(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_request={
            "authorization_id": write_authorization["authorization_id"],
            "intent_id": intent_record["intent_id"],
            "adapter": "kanban",
            "target_ref": "kb_arch_task",
            "target_status": "done",
            "kanban_db_path": str(db_path),
            "expected_previous_status": "review",
            "evidence_refs": ["artifact://agent-coverage/kanban-write-execute.json"],
        },
    )
    outbox_path = tmp_path / "outbox.jsonl"
    rejected_secret = ega.execute_outbound_outbox_enqueue(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        outbox_request={
            "source_run_status": "kanban_db_write_verified",
            "source_request_digest": write["request_digest"],
            "channels": ["telegram"],
            "target_id": "ops-review-room",
            "target_allowlist": ["ops-review-room"],
            "outbox_path": str(outbox_path),
            "payload": {"text": "ok", "token": "secret-token"},
            "evidence_refs": ["artifact://agent-coverage/outbox-secret.json"],
        },
    )
    assert rejected_secret["accepted_as"] == "rejected_outbound_outbox_enqueue"
    assert "payload_contains_secret_like_key" in rejected_secret["blocked_reasons"]
    rejected_target = ega.execute_outbound_outbox_enqueue(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        outbox_request={
            "source_run_status": "kanban_db_write_verified",
            "source_request_digest": write["request_digest"],
            "channels": ["telegram"],
            "target_id": "random-room",
            "target_allowlist": ["ops-review-room"],
            "outbox_path": str(outbox_path),
            "payload": {"text": "ok"},
            "evidence_refs": ["artifact://agent-coverage/outbox-target.json"],
        },
    )
    assert "target_not_allowlisted" in rejected_target["blocked_reasons"]

    request_json = json.dumps(
        {
            "source_run_status": "kanban_db_write_verified",
            "source_request_digest": write["request_digest"],
            "channels": ["telegram"],
            "target_id": "ops-review-room",
            "target_allowlist": ["ops-review-room"],
            "outbox_path": str(outbox_path),
            "payload": {"text": "CLI smoke pending notification."},
            "evidence_refs": ["artifact://agent-coverage/outbox-cli.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--execute-outbound-outbox-enqueue",
            "--runtime-ledger",
            str(ledger_path),
            "--run-id",
            started["run_id"],
            "--outbox-request-json",
            request_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_result = json.loads(proc.stdout)
    assert cli_result["accepted_as"] == "outbound_outbox_item_enqueued"
    assert cli_result["delivery_status"] == "pending"
    assert cli_result["send_executed"] is False


def _make_outbox_enqueue_run(tmp_path):
    ledger_path, started, intent_record, write_authorization = _write_kanban_authorization_run(tmp_path)
    db_path = tmp_path / "kanban.db"
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT)")
        conn.execute("INSERT INTO tasks (id, title, status) VALUES (?, ?, ?)", ("kb_arch_task", "Arch", "review"))
    write = ega.execute_kanban_db_closeout_write(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_request={
            "authorization_id": write_authorization["authorization_id"],
            "intent_id": intent_record["intent_id"],
            "adapter": "kanban",
            "target_ref": "kb_arch_task",
            "target_status": "done",
            "kanban_db_path": str(db_path),
            "expected_previous_status": "review",
            "evidence_refs": ["artifact://agent-coverage/kanban-write-execute.json"],
        },
    )
    outbox_path = tmp_path / "outbox.jsonl"
    outbox = ega.execute_outbound_outbox_enqueue(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        outbox_request={
            "source_run_status": "kanban_db_write_verified",
            "source_request_digest": write["request_digest"],
            "channels": ["telegram", "lark"],
            "target_id": "ops-review-room",
            "target_allowlist": ["ops-review-room"],
            "outbox_path": str(outbox_path),
            "payload": {"text": "Governed closeout verified; pending delivery."},
            "evidence_refs": ["artifact://agent-coverage/outbox.json"],
        },
    )
    return ledger_path, started, outbox, outbox_path


def test_outbound_delivery_authorization_projects_send_authority_without_sending_and_is_idempotent(tmp_path) -> None:
    ledger_path, started, outbox, outbox_path = _make_outbox_enqueue_run(tmp_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    result = ega.request_outbound_delivery_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        delivery_authorization_request={
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "telegram",
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/delivery-auth.json"],
        },
    )
    assert result["accepted_as"] == "outbound_delivery_authorization_projected"
    assert result["delivery_authorization_id"]
    assert result["delivery_status"] == "authorized_pending_send"
    assert result["send_executed"] is False
    assert result["readback_verified"] is True
    assert result["channel"] == "telegram"

    replay = ega.request_outbound_delivery_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        delivery_authorization_request={
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "telegram",
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/delivery-auth-replay.json"],
        },
    )
    assert replay["idempotent_replay"] is True
    assert len([json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines() if "outbound_delivery_authorization_projected" in line]) == 1


def test_outbound_delivery_authorization_rejects_drift_unauthorized_expired_and_cli_smoke(tmp_path) -> None:
    ledger_path, started, outbox, outbox_path = _make_outbox_enqueue_run(tmp_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    rejected_actor = ega.request_outbound_delivery_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        delivery_authorization_request={
            "actor_id": "worker",
            "actor_role": "bounded_worker",
            "decision": "approved",
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "telegram",
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/delivery-auth-worker.json"],
        },
    )
    assert rejected_actor["accepted_as"] == "rejected_outbound_delivery_authorization"
    assert "elevated_actor_required" in rejected_actor["blocked_reasons"]
    rejected_drift = ega.request_outbound_delivery_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        delivery_authorization_request={
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "discord",
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/delivery-auth-drift.json"],
        },
    )
    assert "channel_not_in_outbox_item" in rejected_drift["blocked_reasons"]
    rejected_expired = ega.request_outbound_delivery_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        delivery_authorization_request={
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "telegram",
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "expires_at": expired,
            "evidence_refs": ["artifact://agent-coverage/delivery-auth-expired.json"],
        },
    )
    assert "authorization_expired" in rejected_expired["blocked_reasons"]

    request_json = json.dumps(
        {
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "lark",
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/delivery-auth-cli.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--request-outbound-delivery-authorization",
            "--runtime-ledger",
            str(ledger_path),
            "--run-id",
            started["run_id"],
            "--delivery-authorization-json",
            request_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_result = json.loads(proc.stdout)
    assert cli_result["accepted_as"] == "outbound_delivery_authorization_projected"
    assert cli_result["channel"] == "lark"
    assert cli_result["send_executed"] is False


def _make_delivery_authorization_run(tmp_path):
    ledger_path, started, outbox, outbox_path = _make_outbox_enqueue_run(tmp_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    authorization = ega.request_outbound_delivery_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        delivery_authorization_request={
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "telegram",
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/delivery-auth.json"],
        },
    )
    return ledger_path, started, outbox, outbox_path, authorization


def test_telegram_sender_dry_run_projects_payload_without_token_or_send_and_is_idempotent(tmp_path) -> None:
    ledger_path, started, outbox, outbox_path, authorization = _make_delivery_authorization_run(tmp_path)
    result = ega.execute_telegram_sender_dry_run(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        telegram_dry_run_request={
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "telegram",
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "sender_mode": "dry_run",
            "platform_payload": {"chat_id": "ops-review-room", "text": "Governed closeout verified; pending delivery."},
            "evidence_refs": ["artifact://agent-coverage/telegram-dry-run.json"],
        },
    )
    assert result["accepted_as"] == "telegram_delivery_dry_run_verified"
    assert result["channel"] == "telegram"
    assert result["sender_mode"] == "dry_run"
    assert result["send_executed"] is False
    assert result["token_read"] is False
    assert result["readback_verified"] is True
    assert result["platform_payload_hash"]
    assert "platform_payload" not in result

    replay = ega.execute_telegram_sender_dry_run(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        telegram_dry_run_request={
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "telegram",
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "sender_mode": "dry_run",
            "platform_payload": {"chat_id": "ops-review-room", "text": "Governed closeout verified; pending delivery."},
            "evidence_refs": ["artifact://agent-coverage/telegram-dry-run-replay.json"],
        },
    )
    assert replay["idempotent_replay"] is True
    assert len([line for line in ledger_path.read_text(encoding="utf-8").splitlines() if "telegram_delivery_dry_run_verified" in line]) == 1


def test_telegram_sender_dry_run_rejects_send_mode_channel_drift_secret_payload_and_cli_smoke(tmp_path) -> None:
    ledger_path, started, outbox, outbox_path, authorization = _make_delivery_authorization_run(tmp_path)
    rejected_mode = ega.execute_telegram_sender_dry_run(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        telegram_dry_run_request={
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "telegram",
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "sender_mode": "send",
            "platform_payload": {"chat_id": "ops-review-room", "text": "x"},
            "evidence_refs": ["artifact://agent-coverage/telegram-send-mode.json"],
        },
    )
    assert rejected_mode["accepted_as"] == "rejected_telegram_delivery_dry_run"
    assert "dry_run_mode_required" in rejected_mode["blocked_reasons"]
    rejected_channel = ega.execute_telegram_sender_dry_run(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        telegram_dry_run_request={
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "lark",
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "sender_mode": "dry_run",
            "platform_payload": {"chat_id": "ops-review-room", "text": "x"},
            "evidence_refs": ["artifact://agent-coverage/telegram-channel-drift.json"],
        },
    )
    assert "telegram_channel_required" in rejected_channel["blocked_reasons"]
    rejected_secret = ega.execute_telegram_sender_dry_run(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        telegram_dry_run_request={
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "telegram",
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "sender_mode": "dry_run",
            "platform_payload": {"chat_id": "ops-review-room", "text": "x", "bot_token": "secret-token"},
            "evidence_refs": ["artifact://agent-coverage/telegram-secret.json"],
        },
    )
    assert "platform_payload_contains_secret_like_key" in rejected_secret["blocked_reasons"]

    request_json = json.dumps(
        {
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "telegram",
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "sender_mode": "dry_run",
            "platform_payload": {"chat_id": "ops-review-room", "text": "CLI Telegram dry-run."},
            "evidence_refs": ["artifact://agent-coverage/telegram-dry-run-cli.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--execute-telegram-sender-dry-run",
            "--runtime-ledger",
            str(ledger_path),
            "--run-id",
            started["run_id"],
            "--telegram-dry-run-json",
            request_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_result = json.loads(proc.stdout)
    assert cli_result["accepted_as"] == "telegram_delivery_dry_run_verified"
    assert cli_result["send_executed"] is False
    assert cli_result["token_read"] is False


def _make_lark_delivery_authorization_run(tmp_path):
    ledger_path, started, intent_record, write_authorization = _write_kanban_authorization_run(tmp_path)
    db_path = tmp_path / "lark-kanban.db"
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT)")
        conn.execute("INSERT INTO tasks (id, title, status) VALUES (?, ?, ?)", ("kb_arch_task", "Arch", "review"))
    write_result = ega.execute_kanban_db_closeout_write(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_request={
            "authorization_id": write_authorization["authorization_id"],
            "intent_id": intent_record["intent_id"],
            "adapter": "kanban",
            "target_ref": "kb_arch_task",
            "target_status": "done",
            "kanban_db_path": str(db_path),
            "expected_previous_status": "review",
            "evidence_refs": ["artifact://agent-coverage/kanban-db-write.json"],
        },
    )
    outbox_path = tmp_path / "lark-outbox.jsonl"
    outbox = ega.execute_outbound_outbox_enqueue(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        outbox_request={
            "source_run_status": "kanban_db_write_verified",
            "source_request_digest": write_result["request_digest"],
            "channels": ["lark"],
            "target_id": "lark-review-chat",
            "target_allowlist": ["lark-review-chat"],
            "outbox_path": str(outbox_path),
            "payload": {"text": "Governed closeout verified; pending Lark delivery."},
            "evidence_refs": ["artifact://agent-coverage/lark-outbox.json"],
        },
    )
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    authorization = ega.request_outbound_delivery_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        delivery_authorization_request={
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "lark",
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/lark-delivery-auth.json"],
        },
    )
    return ledger_path, started, outbox, outbox_path, authorization


def test_lark_sender_dry_run_projects_payload_without_token_or_send_and_is_idempotent(tmp_path) -> None:
    ledger_path, started, outbox, outbox_path, authorization = _make_lark_delivery_authorization_run(tmp_path)
    result = ega.execute_lark_sender_dry_run(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        lark_dry_run_request={
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "lark",
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "sender_mode": "dry_run",
            "platform_payload": {"receive_id": "lark-review-chat", "msg_type": "text", "content": {"text": "Governed closeout verified; pending Lark delivery."}},
            "evidence_refs": ["artifact://agent-coverage/lark-dry-run.json"],
        },
    )
    assert result["accepted_as"] == "lark_delivery_dry_run_verified"
    assert result["channel"] == "lark"
    assert result["sender_mode"] == "dry_run"
    assert result["send_executed"] is False
    assert result["token_read"] is False
    assert result["readback_verified"] is True
    assert result["platform_payload_hash"]
    assert "platform_payload" not in result

    replay = ega.execute_lark_sender_dry_run(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        lark_dry_run_request={
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "lark",
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "sender_mode": "dry_run",
            "platform_payload": {"receive_id": "lark-review-chat", "msg_type": "text", "content": {"text": "Governed closeout verified; pending Lark delivery."}},
            "evidence_refs": ["artifact://agent-coverage/lark-dry-run-replay.json"],
        },
    )
    assert replay["idempotent_replay"] is True
    assert len([line for line in ledger_path.read_text(encoding="utf-8").splitlines() if "lark_delivery_dry_run_verified" in line]) == 1


def test_lark_sender_dry_run_rejects_send_mode_channel_drift_secret_payload_and_cli_smoke(tmp_path) -> None:
    ledger_path, started, outbox, outbox_path, authorization = _make_lark_delivery_authorization_run(tmp_path)
    rejected_mode = ega.execute_lark_sender_dry_run(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        lark_dry_run_request={
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "lark",
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "sender_mode": "send",
            "platform_payload": {"receive_id": "lark-review-chat", "msg_type": "text", "content": {"text": "x"}},
            "evidence_refs": ["artifact://agent-coverage/lark-send-mode.json"],
        },
    )
    assert rejected_mode["accepted_as"] == "rejected_lark_delivery_dry_run"
    assert "dry_run_mode_required" in rejected_mode["blocked_reasons"]
    rejected_channel = ega.execute_lark_sender_dry_run(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        lark_dry_run_request={
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "telegram",
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "sender_mode": "dry_run",
            "platform_payload": {"receive_id": "lark-review-chat", "msg_type": "text", "content": {"text": "x"}},
            "evidence_refs": ["artifact://agent-coverage/lark-channel-drift.json"],
        },
    )
    assert "lark_channel_required" in rejected_channel["blocked_reasons"]
    rejected_secret = ega.execute_lark_sender_dry_run(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        lark_dry_run_request={
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "lark",
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "sender_mode": "dry_run",
            "platform_payload": {"receive_id": "lark-review-chat", "msg_type": "text", "tenant_access_token": "secret-token", "content": {"text": "x"}},
            "evidence_refs": ["artifact://agent-coverage/lark-secret.json"],
        },
    )
    assert "platform_payload_contains_secret_like_key" in rejected_secret["blocked_reasons"]

    request_json = json.dumps(
        {
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "lark",
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "sender_mode": "dry_run",
            "platform_payload": {"receive_id": "lark-review-chat", "msg_type": "text", "content": {"text": "CLI Lark dry-run."}},
            "evidence_refs": ["artifact://agent-coverage/lark-dry-run-cli.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--execute-lark-sender-dry-run",
            "--runtime-ledger",
            str(ledger_path),
            "--run-id",
            started["run_id"],
            "--lark-dry-run-json",
            request_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_result = json.loads(proc.stdout)
    assert cli_result["accepted_as"] == "lark_delivery_dry_run_verified"
    assert cli_result["send_executed"] is False
    assert cli_result["token_read"] is False


def _make_telegram_dry_run_verified(tmp_path):
    ledger_path, started, outbox, outbox_path, authorization = _make_delivery_authorization_run(tmp_path)
    dry_run = ega.execute_telegram_sender_dry_run(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        telegram_dry_run_request={
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "telegram",
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "sender_mode": "dry_run",
            "platform_payload": {"chat_id": "ops-review-room", "text": "Governed closeout verified; pending delivery."},
            "evidence_refs": ["artifact://agent-coverage/telegram-dry-run.json"],
        },
    )
    return ledger_path, started, outbox, outbox_path, authorization, dry_run


def _make_lark_dry_run_verified(tmp_path):
    ledger_path, started, outbox, outbox_path, authorization = _make_lark_delivery_authorization_run(tmp_path)
    dry_run = ega.execute_lark_sender_dry_run(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        lark_dry_run_request={
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "outbox_path": str(outbox_path),
            "channel": "lark",
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "sender_mode": "dry_run",
            "platform_payload": {"receive_id": "lark-review-chat", "msg_type": "text", "content": {"text": "Governed closeout verified; pending Lark delivery."}},
            "evidence_refs": ["artifact://agent-coverage/lark-dry-run.json"],
        },
    )
    return ledger_path, started, outbox, outbox_path, authorization, dry_run


def test_real_sender_preflight_projects_disabled_capability_without_token_or_send_and_is_idempotent(tmp_path) -> None:
    ledger_path, started, outbox, outbox_path, authorization, dry_run = _make_telegram_dry_run_verified(tmp_path)
    result = ega.request_real_sender_preflight(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        preflight_request={
            "channel": "telegram",
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "dry_run_request_digest": dry_run["request_digest"],
            "outbox_id": outbox["request_digest"],
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:TELEGRAM_BOT_TOKEN",
            "sender_capability_enabled": False,
            "dry_run_max_age_seconds": 600,
            "evidence_refs": ["artifact://agent-coverage/real-sender-preflight.json"],
        },
    )
    assert result["accepted_as"] == "real_sender_preflight_verified"
    assert result["channel"] == "telegram"
    assert result["send_executed"] is False
    assert result["token_read"] is False
    assert result["sender_capability_enabled"] is False
    assert result["readback_verified"] is True

    replay = ega.request_real_sender_preflight(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        preflight_request={
            "channel": "telegram",
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "dry_run_request_digest": dry_run["request_digest"],
            "outbox_id": outbox["request_digest"],
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:TELEGRAM_BOT_TOKEN",
            "sender_capability_enabled": False,
            "dry_run_max_age_seconds": 600,
            "evidence_refs": ["artifact://agent-coverage/real-sender-preflight-replay.json"],
        },
    )
    assert replay["idempotent_replay"] is True
    assert len([line for line in ledger_path.read_text(encoding="utf-8").splitlines() if "real_sender_preflight_verified" in line]) == 1


def test_real_sender_preflight_supports_lark_and_rejects_enabled_capability_drift_secret_token_and_cli_smoke(tmp_path) -> None:
    ledger_path, started, outbox, outbox_path, authorization, dry_run = _make_lark_dry_run_verified(tmp_path)
    ok = ega.request_real_sender_preflight(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        preflight_request={
            "channel": "lark",
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "dry_run_request_digest": dry_run["request_digest"],
            "outbox_id": outbox["request_digest"],
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:LARK_APP_SECRET",
            "sender_capability_enabled": False,
            "dry_run_max_age_seconds": 600,
            "evidence_refs": ["artifact://agent-coverage/lark-real-sender-preflight.json"],
        },
    )
    assert ok["accepted_as"] == "real_sender_preflight_verified"
    assert ok["channel"] == "lark"
    rejected_enabled = ega.request_real_sender_preflight(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        preflight_request={
            "channel": "lark",
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "dry_run_request_digest": dry_run["request_digest"],
            "outbox_id": outbox["request_digest"],
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:LARK_APP_SECRET",
            "sender_capability_enabled": True,
            "dry_run_max_age_seconds": 600,
            "evidence_refs": ["artifact://agent-coverage/enabled-preflight.json"],
        },
    )
    assert "sender_capability_must_remain_disabled" in rejected_enabled["blocked_reasons"]
    rejected_drift = ega.request_real_sender_preflight(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        preflight_request={
            "channel": "lark",
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "dry_run_request_digest": dry_run["request_digest"],
            "outbox_id": outbox["request_digest"],
            "target_id": "lark-review-chat",
            "payload_hash": "bad-hash",
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:LARK_APP_SECRET",
            "sender_capability_enabled": False,
            "dry_run_max_age_seconds": 600,
            "evidence_refs": ["artifact://agent-coverage/drift-preflight.json"],
        },
    )
    assert "payload_hash_drift" in rejected_drift["blocked_reasons"]
    rejected_secret_token = ega.request_real_sender_preflight(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        preflight_request={
            "channel": "lark",
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "dry_run_request_digest": dry_run["request_digest"],
            "outbox_id": outbox["request_digest"],
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "secret-literal-token-value",
            "sender_capability_enabled": False,
            "dry_run_max_age_seconds": 600,
            "evidence_refs": ["artifact://agent-coverage/secret-token-preflight.json"],
        },
    )
    assert "token_source_must_be_reference_not_secret" in rejected_secret_token["blocked_reasons"]

    request_json = json.dumps(
        {
            "channel": "lark",
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "dry_run_request_digest": dry_run["request_digest"],
            "outbox_id": outbox["request_digest"],
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:LARK_APP_SECRET",
            "sender_capability_enabled": False,
            "dry_run_max_age_seconds": 600,
            "evidence_refs": ["artifact://agent-coverage/real-sender-preflight-cli.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--request-real-sender-preflight",
            "--runtime-ledger",
            str(ledger_path),
            "--run-id",
            started["run_id"],
            "--real-sender-preflight-json",
            request_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_result = json.loads(proc.stdout)
    assert cli_result["accepted_as"] == "real_sender_preflight_verified"
    assert cli_result["send_executed"] is False
    assert cli_result["token_read"] is False


def _make_telegram_real_sender_preflight(tmp_path):
    ledger_path, started, outbox, outbox_path, authorization, dry_run = _make_telegram_dry_run_verified(tmp_path)
    preflight = ega.request_real_sender_preflight(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        preflight_request={
            "channel": "telegram",
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "dry_run_request_digest": dry_run["request_digest"],
            "outbox_id": outbox["request_digest"],
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:TELEGRAM_BOT_TOKEN",
            "sender_capability_enabled": False,
            "dry_run_max_age_seconds": 600,
            "evidence_refs": ["artifact://agent-coverage/telegram-preflight.json"],
        },
    )
    return ledger_path, started, outbox, outbox_path, authorization, dry_run, preflight


def _make_lark_real_sender_preflight(tmp_path):
    ledger_path, started, outbox, outbox_path, authorization, dry_run = _make_lark_dry_run_verified(tmp_path)
    preflight = ega.request_real_sender_preflight(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        preflight_request={
            "channel": "lark",
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "dry_run_request_digest": dry_run["request_digest"],
            "outbox_id": outbox["request_digest"],
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:LARK_APP_SECRET",
            "sender_capability_enabled": False,
            "dry_run_max_age_seconds": 600,
            "evidence_refs": ["artifact://agent-coverage/lark-preflight.json"],
        },
    )
    return ledger_path, started, outbox, outbox_path, authorization, dry_run, preflight


def test_real_sender_execution_authorization_projects_second_approval_without_token_or_send_and_is_idempotent(tmp_path) -> None:
    ledger_path, started, outbox, outbox_path, authorization, dry_run, preflight = _make_telegram_real_sender_preflight(tmp_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    result = ega.request_real_sender_execution_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_authorization_request={
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "channel": "telegram",
            "preflight_request_digest": preflight["request_digest"],
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:TELEGRAM_BOT_TOKEN",
            "sender_capability_requested": True,
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/real-sender-exec-auth.json"],
        },
    )
    assert result["accepted_as"] == "real_sender_execution_authorized"
    assert result["channel"] == "telegram"
    assert result["send_executed"] is False
    assert result["token_read"] is False
    assert result["sender_capability_requested"] is True
    assert result["authorization_status"] == "approved_not_executed"

    replay = ega.request_real_sender_execution_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_authorization_request={
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "channel": "telegram",
            "preflight_request_digest": preflight["request_digest"],
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:TELEGRAM_BOT_TOKEN",
            "sender_capability_requested": True,
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/real-sender-exec-auth-replay.json"],
        },
    )
    assert replay["idempotent_replay"] is True
    assert len([line for line in ledger_path.read_text(encoding="utf-8").splitlines() if "real_sender_execution_authorized" in line]) == 1


def test_real_sender_execution_authorization_supports_lark_and_rejects_drift_denied_bad_actor_capability_and_cli_smoke(tmp_path) -> None:
    ledger_path, started, outbox, outbox_path, authorization, dry_run, preflight = _make_lark_real_sender_preflight(tmp_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    ok = ega.request_real_sender_execution_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_authorization_request={
            "actor_id": "ops-admin",
            "actor_role": "admin",
            "decision": "approved",
            "channel": "lark",
            "preflight_request_digest": preflight["request_digest"],
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:LARK_APP_SECRET",
            "sender_capability_requested": True,
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/lark-real-sender-exec-auth.json"],
        },
    )
    assert ok["accepted_as"] == "real_sender_execution_authorized"
    assert ok["channel"] == "lark"

    rejected_drift = ega.request_real_sender_execution_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_authorization_request={
            "actor_id": "ops-admin",
            "actor_role": "admin",
            "decision": "approved",
            "channel": "lark",
            "preflight_request_digest": preflight["request_digest"],
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "target_id": "lark-review-chat",
            "payload_hash": "bad-hash",
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:LARK_APP_SECRET",
            "sender_capability_requested": True,
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/drift-exec-auth.json"],
        },
    )
    assert "payload_hash_drift" in rejected_drift["blocked_reasons"]

    rejected_denied = ega.request_real_sender_execution_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_authorization_request={
            "actor_id": "ops-admin",
            "actor_role": "admin",
            "decision": "denied",
            "channel": "lark",
            "preflight_request_digest": preflight["request_digest"],
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:LARK_APP_SECRET",
            "sender_capability_requested": True,
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/denied-exec-auth.json"],
        },
    )
    assert "decision_must_be_approved" in rejected_denied["blocked_reasons"]

    rejected_bad_actor = ega.request_real_sender_execution_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_authorization_request={
            "actor_id": "worker-1",
            "actor_role": "bounded_worker",
            "decision": "approved",
            "channel": "lark",
            "preflight_request_digest": preflight["request_digest"],
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:LARK_APP_SECRET",
            "sender_capability_requested": True,
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/bad-actor-exec-auth.json"],
        },
    )
    assert "actor_role_not_elevated" in rejected_bad_actor["blocked_reasons"]

    rejected_capability = ega.request_real_sender_execution_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_authorization_request={
            "actor_id": "ops-admin",
            "actor_role": "admin",
            "decision": "approved",
            "channel": "lark",
            "preflight_request_digest": preflight["request_digest"],
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:LARK_APP_SECRET",
            "sender_capability_requested": False,
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/no-capability-exec-auth.json"],
        },
    )
    assert "sender_capability_request_required" in rejected_capability["blocked_reasons"]

    request_json = json.dumps(
        {
            "actor_id": "ops-admin",
            "actor_role": "admin",
            "decision": "approved",
            "channel": "lark",
            "preflight_request_digest": preflight["request_digest"],
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:LARK_APP_SECRET",
            "sender_capability_requested": True,
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/real-sender-exec-auth-cli.json"],
        }
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.event_governed_agents",
            "--request-real-sender-execution-authorization",
            "--runtime-ledger",
            str(ledger_path),
            "--run-id",
            started["run_id"],
            "--real-sender-execution-authorization-json",
            request_json,
            "--json",
        ],
        cwd=tmp_path,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
    )
    cli_result = json.loads(proc.stdout)
    assert cli_result["accepted_as"] == "real_sender_execution_authorized"
    assert cli_result["send_executed"] is False
    assert cli_result["token_read"] is False


class _TelegramTestHandler(http.server.BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        body = json.loads(raw or "{}")
        type(self).requests.append({"path": self.path, "body": body, "authorization": self.headers.get("Authorization")})
        response = {"ok": True, "result": {"message_id": 4242, "chat": {"id": body.get("chat_id")}, "text": body.get("text")}}
        encoded = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


class _ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


def _run_telegram_test_server():
    _TelegramTestHandler.requests = []
    server = _ThreadingTCPServer(("127.0.0.1", 0), _TelegramTestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_telegram_real_sender_executes_http_send_with_authorization_token_and_records_message_id(tmp_path, monkeypatch) -> None:
    ledger_path, started, outbox, outbox_path, authorization, dry_run, preflight = _make_telegram_real_sender_preflight(tmp_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    execution_authorization = ega.request_real_sender_execution_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_authorization_request={
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "channel": "telegram",
            "preflight_request_digest": preflight["request_digest"],
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:TELEGRAM_BOT_TOKEN",
            "sender_capability_requested": True,
            "expires_at": expires_at,
            "evidence_refs": ["artifact://agent-coverage/telegram-real-auth.json"],
        },
    )
    server, api_base_url = _run_telegram_test_server()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token-123")
    try:
        result = ega.execute_telegram_real_sender(
            ledger_path=ledger_path,
            run_id=started["run_id"],
            telegram_send_request={
                "execution_authorization_id": execution_authorization["authorization_id"],
                "channel": "telegram",
                "preflight_request_digest": preflight["request_digest"],
                "outbox_id": outbox["request_digest"],
                "target_id": "ops-review-room",
                "payload_hash": outbox["payload_hash"],
                "platform_payload_hash": dry_run["platform_payload_hash"],
                "token_source": "env:TELEGRAM_BOT_TOKEN",
                "sender_capability_enabled": True,
                "executor_mode": "send",
                "platform_payload": {"chat_id": "ops-review-room", "text": "Governed closeout verified; pending delivery."},
                "api_base_url": api_base_url,
                "evidence_refs": ["artifact://agent-coverage/telegram-real-send.json"],
            },
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result["accepted_as"] == "telegram_delivery_sent"
    assert result["send_executed"] is True
    assert result["token_read"] is True
    assert result["platform_message_id"] == 4242
    assert result["token_source"] == "env:TELEGRAM_BOT_TOKEN"
    assert result["token_value_stored"] is False
    assert _TelegramTestHandler.requests[0]["path"] == "/bottest-token-123/sendMessage"
    assert _TelegramTestHandler.requests[0]["body"] == {"chat_id": "ops-review-room", "text": "Governed closeout verified; pending delivery."}


def test_telegram_real_sender_rejects_disabled_capability_hash_drift_missing_token_and_cli_smoke(tmp_path, monkeypatch) -> None:
    ledger_path, started, outbox, outbox_path, authorization, dry_run, preflight = _make_telegram_real_sender_preflight(tmp_path)
    execution_authorization = ega.request_real_sender_execution_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_authorization_request={
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "channel": "telegram",
            "preflight_request_digest": preflight["request_digest"],
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "target_id": "ops-review-room",
            "payload_hash": outbox["payload_hash"],
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:TELEGRAM_BOT_TOKEN",
            "sender_capability_requested": True,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "evidence_refs": ["artifact://agent-coverage/telegram-real-auth.json"],
        },
    )
    base_request = {
        "execution_authorization_id": execution_authorization["authorization_id"],
        "channel": "telegram",
        "preflight_request_digest": preflight["request_digest"],
        "outbox_id": outbox["request_digest"],
        "target_id": "ops-review-room",
        "payload_hash": outbox["payload_hash"],
        "platform_payload_hash": dry_run["platform_payload_hash"],
        "token_source": "env:TELEGRAM_BOT_TOKEN",
        "sender_capability_enabled": True,
        "executor_mode": "send",
        "platform_payload": {"chat_id": "ops-review-room", "text": "Governed closeout verified; pending delivery."},
        "api_base_url": "http://127.0.0.1:9",
        "evidence_refs": ["artifact://agent-coverage/telegram-real-send.json"],
    }
    rejected_capability = ega.execute_telegram_real_sender(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        telegram_send_request={**base_request, "sender_capability_enabled": False},
    )
    assert "sender_capability_enabled_required" in rejected_capability["blocked_reasons"]
    rejected_hash = ega.execute_telegram_real_sender(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        telegram_send_request={**base_request, "platform_payload": {"chat_id": "ops-review-room", "text": "tampered"}},
    )
    assert "platform_payload_hash_drift" in rejected_hash["blocked_reasons"]
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    rejected_token = ega.execute_telegram_real_sender(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        telegram_send_request=base_request,
    )
    assert "token_value_missing" in rejected_token["blocked_reasons"]

    server, api_base_url = _run_telegram_test_server()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "cli-token-456")
    try:
        request_json = json.dumps({**base_request, "api_base_url": api_base_url, "evidence_refs": ["artifact://agent-coverage/telegram-real-send-cli.json"]})
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "hermes_cli.event_governed_agents",
                "--execute-telegram-real-sender",
                "--runtime-ledger",
                str(ledger_path),
                "--run-id",
                started["run_id"],
                "--telegram-real-send-json",
                request_json,
                "--json",
            ],
            cwd=tmp_path,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
        )
    finally:
        server.shutdown()
        server.server_close()
    cli_result = json.loads(proc.stdout)
    assert cli_result["accepted_as"] == "telegram_delivery_sent"
    assert cli_result["platform_message_id"] == 4242
    assert cli_result["send_executed"] is True
    assert cli_result["token_read"] is True


class _LarkTestHandler(http.server.BaseHTTPRequestHandler):
    requests: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        body = json.loads(raw or "{}")
        type(self).requests.append({"path": self.path, "body": body, "authorization": self.headers.get("Authorization")})
        response = {"code": 0, "msg": "ok", "data": {"message_id": "om_4242"}}
        encoded = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:
        return


def _run_lark_test_server():
    _LarkTestHandler.requests = []
    server = _ThreadingTCPServer(("127.0.0.1", 0), _LarkTestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_lark_real_sender_executes_http_send_with_bearer_token_and_records_message_id(tmp_path, monkeypatch) -> None:
    ledger_path, started, outbox, outbox_path, authorization, dry_run, preflight = _make_lark_real_sender_preflight(tmp_path)
    execution_authorization = ega.request_real_sender_execution_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_authorization_request={
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "channel": "lark",
            "preflight_request_digest": preflight["request_digest"],
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:LARK_APP_SECRET",
            "sender_capability_requested": True,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "evidence_refs": ["artifact://agent-coverage/lark-real-auth.json"],
        },
    )
    server, api_base_url = _run_lark_test_server()
    monkeypatch.setenv("LARK_APP_SECRET", "tenant-token-123")
    try:
        result = ega.execute_lark_real_sender(
            ledger_path=ledger_path,
            run_id=started["run_id"],
            lark_send_request={
                "execution_authorization_id": execution_authorization["authorization_id"],
                "channel": "lark",
                "preflight_request_digest": preflight["request_digest"],
                "outbox_id": outbox["request_digest"],
                "target_id": "lark-review-chat",
                "payload_hash": outbox["payload_hash"],
                "platform_payload_hash": dry_run["platform_payload_hash"],
                "token_source": "env:LARK_APP_SECRET",
                "sender_capability_enabled": True,
                "executor_mode": "send",
                "platform_payload": {"receive_id": "lark-review-chat", "msg_type": "text", "content": {"text": "Governed closeout verified; pending Lark delivery."}},
                "receive_id_type": "chat_id",
                "api_base_url": api_base_url,
                "evidence_refs": ["artifact://agent-coverage/lark-real-send.json"],
            },
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result["accepted_as"] == "lark_delivery_sent"
    assert result["send_executed"] is True
    assert result["token_read"] is True
    assert result["platform_message_id"] == "om_4242"
    assert result["token_value_stored"] is False
    assert _LarkTestHandler.requests[0]["path"] == "/open-apis/im/v1/messages?receive_id_type=chat_id"
    assert _LarkTestHandler.requests[0]["authorization"] == "Bearer tenant-token-123"
    assert _LarkTestHandler.requests[0]["body"] == {
        "receive_id": "lark-review-chat",
        "msg_type": "text",
        "content": json.dumps({"text": "Governed closeout verified; pending Lark delivery."}, ensure_ascii=False),
    }


def test_lark_real_sender_rejects_disabled_capability_hash_drift_missing_token_and_cli_smoke(tmp_path, monkeypatch) -> None:
    ledger_path, started, outbox, outbox_path, authorization, dry_run, preflight = _make_lark_real_sender_preflight(tmp_path)
    execution_authorization = ega.request_real_sender_execution_authorization(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        execution_authorization_request={
            "actor_id": "human-admin",
            "actor_role": "admin",
            "decision": "approved",
            "channel": "lark",
            "preflight_request_digest": preflight["request_digest"],
            "delivery_authorization_id": authorization["delivery_authorization_id"],
            "outbox_id": outbox["request_digest"],
            "target_id": "lark-review-chat",
            "payload_hash": outbox["payload_hash"],
            "platform_payload_hash": dry_run["platform_payload_hash"],
            "token_source": "env:LARK_APP_SECRET",
            "sender_capability_requested": True,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            "evidence_refs": ["artifact://agent-coverage/lark-real-auth.json"],
        },
    )
    base_request = {
        "execution_authorization_id": execution_authorization["authorization_id"],
        "channel": "lark",
        "preflight_request_digest": preflight["request_digest"],
        "outbox_id": outbox["request_digest"],
        "target_id": "lark-review-chat",
        "payload_hash": outbox["payload_hash"],
        "platform_payload_hash": dry_run["platform_payload_hash"],
        "token_source": "env:LARK_APP_SECRET",
        "sender_capability_enabled": True,
        "executor_mode": "send",
        "platform_payload": {"receive_id": "lark-review-chat", "msg_type": "text", "content": {"text": "Governed closeout verified; pending Lark delivery."}},
        "receive_id_type": "chat_id",
        "api_base_url": "http://127.0.0.1:9",
        "evidence_refs": ["artifact://agent-coverage/lark-real-send.json"],
    }
    rejected_capability = ega.execute_lark_real_sender(ledger_path=ledger_path, run_id=started["run_id"], lark_send_request={**base_request, "sender_capability_enabled": False})
    assert "sender_capability_enabled_required" in rejected_capability["blocked_reasons"]
    rejected_hash = ega.execute_lark_real_sender(
        ledger_path=ledger_path,
        run_id=started["run_id"],
        lark_send_request={**base_request, "platform_payload": {"receive_id": "lark-review-chat", "msg_type": "text", "content": {"text": "tampered"}}},
    )
    assert "platform_payload_hash_drift" in rejected_hash["blocked_reasons"]
    monkeypatch.delenv("LARK_APP_SECRET", raising=False)
    rejected_token = ega.execute_lark_real_sender(ledger_path=ledger_path, run_id=started["run_id"], lark_send_request=base_request)
    assert "token_value_missing" in rejected_token["blocked_reasons"]

    server, api_base_url = _run_lark_test_server()
    monkeypatch.setenv("LARK_APP_SECRET", "cli-tenant-token-456")
    try:
        request_json = json.dumps({**base_request, "api_base_url": api_base_url, "evidence_refs": ["artifact://agent-coverage/lark-real-send-cli.json"]})
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "hermes_cli.event_governed_agents",
                "--execute-lark-real-sender",
                "--runtime-ledger",
                str(ledger_path),
                "--run-id",
                started["run_id"],
                "--lark-real-send-json",
                request_json,
                "--json",
            ],
            cwd=tmp_path,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": os.pathsep.join([os.getcwd(), os.environ.get("PYTHONPATH", "")])},
        )
    finally:
        server.shutdown()
        server.server_close()
    cli_result = json.loads(proc.stdout)
    assert cli_result["accepted_as"] == "lark_delivery_sent"
    assert cli_result["platform_message_id"] == "om_4242"
    assert cli_result["send_executed"] is True
    assert cli_result["token_read"] is True
