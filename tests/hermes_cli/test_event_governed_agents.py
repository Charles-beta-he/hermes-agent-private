from __future__ import annotations

import json
import os
import subprocess
import sys

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
