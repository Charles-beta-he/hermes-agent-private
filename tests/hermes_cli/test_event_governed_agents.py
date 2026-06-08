from __future__ import annotations

import json
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
    )
    cli_report = json.loads(proc.stdout)
    assert cli_report["mode"] == "report_only"
    assert cli_report["danger_gate"]["dangerous_actions_require_human"] is True
