from __future__ import annotations

import hashlib
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
