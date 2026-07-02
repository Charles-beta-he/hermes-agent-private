from __future__ import annotations

import json
from pathlib import Path

from agent.conductor_runtime import ComplexTaskHarnessLedger, IcebergComplexityScanner
from hermes_cli.complex_task_harness import (
    checkpoint,
    config_status,
    external_dispatch,
    status,
    taskpool_lifecycle,
    verifier_packets,
)


def test_complex_task_harness_config_status_reports_default_closed() -> None:
    payload = config_status({})

    assert payload["ok"] is True
    assert payload["mode"] == "config_readback_only"
    assert payload["complex_task_harness"]["enabled"] is False
    assert payload["complex_task_harness"]["ledger_enabled"] is False
    assert payload["complex_task_harness"]["runtime_mutation_authorized"] is False
    assert payload["runtime_defaults_enabled"] is False
    assert payload["external_side_effects_enabled"] is False


def test_complex_task_harness_config_status_reports_explicit_opt_in_without_authority() -> None:
    payload = config_status(
        {
            "conductor": {
                "intent_router": {"enabled": True},
                "complex_task_harness": {"enabled": True, "ledger_enabled": True},
            }
        }
    )

    assert payload["runtime_defaults_enabled"] is True
    assert payload["complex_task_harness"]["enabled"] is True
    assert payload["complex_task_harness"]["ledger_enabled"] is True
    assert payload["complex_task_harness"]["runtime_mutation_authorized"] is False
    assert payload["allowed_to_mark_done"] is False


def test_complex_task_harness_cli_config_action_reads_loaded_config(monkeypatch, capsys) -> None:
    import argparse

    from hermes_cli.subcommands import complex_task_harness as subcommand

    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"conductor": {"complex_task_harness": {"enabled": True}}},
    )
    rc = subcommand.cmd_complex_task_harness(
        argparse.Namespace(complex_task_harness_action="config", json=True)
    )
    captured = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert captured["mode"] == "config_readback_only"
    assert captured["complex_task_harness"]["enabled"] is True
    assert captured["allowed_to_mark_done"] is False


def test_complex_task_harness_status_reads_local_ledger(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    ledger = ComplexTaskHarnessLedger(ledger_path)
    scan = IcebergComplexityScanner().scan(
        "继续学习 LazyCodex/OmO 并直接推进到 Hermes core/runtime 替换",
        target_surface="hermes_core_runtime",
    )
    receipt = ledger.record_scan(scan=scan, user_message="继续推进", origin="unit-test")
    ledger.append_evidence(receipt["scan_id"], {"source": "pytest", "status": "pass"})

    payload = status(ledger_path=ledger_path)

    assert payload["ok"] is True
    assert payload["mode"] == "local_readback_only"
    assert payload["event_count"] == 2
    assert payload["latest_scan_id"] == receipt["scan_id"]
    assert payload["external_side_effects_enabled"] is False
    assert payload["allowed_to_mark_done"] is False


def test_complex_task_harness_verifier_packets_readback_filters_verifier_events(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    ledger = ComplexTaskHarnessLedger(ledger_path)
    scan_receipt = ledger.record_scan(
        scan={"schema": "hermes_iceberg_complexity_scan_v1", "classification": "complex"},
        user_message="完成 Hermes core/runtime 替换",
        origin="unit-test",
    )
    ledger.append_checkpoint(
        scan_receipt["scan_id"],
        objective="checkpoint noise",
        completed_evidence=[],
        pending_actions=[],
        rollback="",
    )
    ledger.append_verifier_packet(
        scan_receipt["scan_id"],
        {
            "schema": "hermes_complex_task_verifier_packet_v1",
            "status": "not_certified",
            "missing_evidence": ["independent_verifier_vote"],
        },
        origin="unit-test",
    )

    payload = verifier_packets(ledger_path=ledger_path, scan_id=scan_receipt["scan_id"])

    assert payload["ok"] is True
    assert payload["mode"] == "verifier_packet_readback_only"
    assert payload["event_count"] == 1
    assert payload["events"][0]["event_type"] == "verifier_packet"
    assert payload["events"][0]["verifier_packet"]["schema"] == "hermes_complex_task_verifier_packet_v1"
    assert payload["allowed_to_mark_done"] is False


def test_complex_task_harness_checkpoint_appends_resume_packet(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    ledger = ComplexTaskHarnessLedger(ledger_path)
    receipt = ledger.record_scan(
        scan={"schema": "hermes_iceberg_complexity_scan_v1", "classification": "complex"},
        user_message="继续推进",
        origin="unit-test",
    )

    payload = checkpoint(
        scan_id=receipt["scan_id"],
        objective="continue LazyCodex runtime replacement",
        completed_evidence=["commit:b81329fdc", "pytest:44 passed"],
        pending_actions=["add replay readback CLI"],
        rollback="git reset --hard b90eccc1b in isolated worktree",
        ledger_path=ledger_path,
    )
    events = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]

    assert payload["ok"] is True
    assert payload["receipt"]["event_type"] == "checkpoint"
    assert payload["receipt"]["allowed_to_mark_done"] is False
    assert events[-1]["checkpoint"]["objective"] == "continue LazyCodex runtime replacement"


def test_complex_task_harness_taskpool_lifecycle_requires_exact_fields_and_records_receipt(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    receipt = ComplexTaskHarnessLedger(ledger_path).record_scan(
        scan={"schema": "hermes_iceberg_complexity_scan_v1", "classification": "complex"},
        user_message="continue exact taskpool adapter",
        origin="unit-test",
    )

    payload = taskpool_lifecycle(
        scan_id=receipt["scan_id"],
        task_id="task-123",
        from_status="review",
        to_status="done",
        evidence_ref="proof:pytest:57-passed",
        reason="exact adapter smoke",
        readback_ref="command:brain_task_orchestrator.py list --include-done --json",
        execute=False,
        ledger_path=ledger_path,
    )

    assert payload["ok"] is True
    assert payload["mode"] == "taskpool_lifecycle_adapter_receipt_only"
    assert payload["receipt"]["event_type"] == "taskpool_lifecycle_adapter"
    assert payload["receipt"]["adapter"]["task_id"] == "task-123"
    assert payload["receipt"]["adapter"]["from_status"] == "review"
    assert payload["receipt"]["adapter"]["to_status"] == "done"
    assert payload["receipt"]["adapter"]["execute_requested"] is False
    assert payload["receipt"]["allowed_to_mark_done"] is False


def test_complex_task_harness_taskpool_lifecycle_rejects_missing_evidence_prefix(tmp_path: Path) -> None:
    payload = taskpool_lifecycle(
        scan_id="complex-scan-test",
        task_id="task-123",
        from_status="review",
        to_status="done",
        evidence_ref="pytest passed",
        reason="bad evidence",
        readback_ref="command:readback",
        execute=False,
        ledger_path=tmp_path / "ledger.jsonl",
    )

    assert payload["ok"] is False
    assert payload["receipt"]["status"] == "rejected"
    assert payload["receipt"]["reason"] == "invalid_evidence_ref"


def test_complex_task_harness_external_dispatch_records_digest_bound_outbox(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.jsonl"
    receipt = ComplexTaskHarnessLedger(ledger_path).record_scan(
        scan={"schema": "hermes_iceberg_complexity_scan_v1", "classification": "complex"},
        user_message="continue exact external dispatch adapter",
        origin="unit-test",
    )
    payload_text = "local harness smoke"

    payload = external_dispatch(
        scan_id=receipt["scan_id"],
        platform="local_harness",
        target="local://complex-task-harness/smoke",
        payload=payload_text,
        payload_sha256="sha256:8e56844b774193e7440332a547518d2592f692988e806cbfdc092be1c4be22a9",
        readback_ref="command:hermes complex-task-harness status --json",
        execute=False,
        ledger_path=ledger_path,
    )

    assert payload["ok"] is True
    assert payload["mode"] == "external_dispatch_adapter_outbox_only"
    assert payload["receipt"]["event_type"] == "external_dispatch_adapter"
    assert payload["receipt"]["adapter"]["platform"] == "local_harness"
    assert payload["receipt"]["adapter"]["target"] == "local://complex-task-harness/smoke"
    assert payload["receipt"]["adapter"]["payload_sha256"] == "sha256:8e56844b774193e7440332a547518d2592f692988e806cbfdc092be1c4be22a9"
    assert payload["receipt"]["adapter"]["payload_stored"] is False
    assert payload["receipt"]["adapter"]["send_executed"] is False


def test_complex_task_harness_external_dispatch_rejects_payload_hash_mismatch(tmp_path: Path) -> None:
    payload = external_dispatch(
        scan_id="complex-scan-test",
        platform="local_harness",
        target="local://complex-task-harness/smoke",
        payload="local harness smoke",
        payload_sha256="sha256:not-the-real-hash",
        readback_ref="command:readback",
        execute=False,
        ledger_path=tmp_path / "ledger.jsonl",
    )

    assert payload["ok"] is False
    assert payload["receipt"]["status"] == "rejected"
    assert payload["receipt"]["reason"] == "payload_hash_mismatch"
