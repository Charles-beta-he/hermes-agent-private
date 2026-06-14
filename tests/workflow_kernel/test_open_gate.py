from __future__ import annotations

import json
from pathlib import Path

from workflow_kernel.open_gate import OPEN_CONFIRMATION, build_open_manifest, load_readiness, validate_open_manifest
from scripts import workflow_kernel_limited_unattended_sender as sender


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def write_ready_artifacts(root: Path) -> None:
    write_json(root / "docs/private/workflow-kernel-static-registry-validator/workflow_kernel_static_registry_validation_20260614.json", {"status": "PASS"})
    write_json(root / "docs/private/workflow-kernel-local-runtime-gate/workflow_kernel_local_runtime_dry_run_plan_20260614.json", {"status": "READY_LOCAL_DRY_RUN", "external_side_effects_enabled": False, "model_calls_enabled": False, "adapter_writes_enabled": False})
    write_json(root / "docs/private/workflow-kernel-unattended-dispatch-policy/workflow_kernel_unattended_dispatch_policy_20260613_phase54.json", {"status": "UNATTENDED_DISPATCH_POLICY_GATE_READY"})
    write_json(root / "docs/private/workflow-kernel-unattended-dry-run-scheduler/workflow_kernel_unattended_dry_run_scheduler_20260613_phase55.json", {"status": "UNATTENDED_DRY_RUN_SCHEDULER_READY"})
    write_json(root / "docs/private/workflow-kernel-guarded-manual-send-runner/workflow_kernel_one_shot_guarded_send_20260613_phase60.json", {"status": "ONE_SHOT_GUARDED_SEND_VERIFIED"})
    write_json(root / "docs/private/workflow-kernel-unattended-dispatch-enablement-packet/workflow_kernel_unattended_dispatch_enablement_packet_20260613_phase62.json", {"status": "UNATTENDED_DISPATCH_ENABLEMENT_PACKET_READY"})


def test_open_readiness_passes_when_required_artifacts_are_ready(tmp_path) -> None:
    write_ready_artifacts(tmp_path)

    readiness = load_readiness(tmp_path)

    assert readiness["status"] == "OPEN_READY"
    assert readiness["blockers"] == []


def test_open_manifest_requires_exact_confirmation_to_open(tmp_path) -> None:
    write_ready_artifacts(tmp_path)

    blocked = build_open_manifest(tmp_path, opened=True, confirmation="wrong", operator="tester")
    opened = build_open_manifest(tmp_path, opened=True, confirmation=OPEN_CONFIRMATION, operator="tester")

    assert blocked["status"] == "OPEN_BLOCKED"
    assert "open_confirmation_mismatch" in blocked["errors"]
    assert opened["status"] == "OPENED_LIMITED_UNATTENDED"
    assert opened["opened"] is True
    assert opened["external_side_effects_allowed_by_manifest"] is True
    assert validate_open_manifest(opened)["status"] == "PASS"


def test_limited_unattended_sender_dry_run_requires_open_manifest(tmp_path) -> None:
    write_ready_artifacts(tmp_path)
    manifest = build_open_manifest(tmp_path, opened=False, operator="tester")
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, manifest)

    receipt = sender.build_or_execute(manifest_path, tmp_path / "ledger.jsonl", execute=False)

    assert receipt["status"] == "LIMITED_UNATTENDED_DISPATCH_BLOCKED"
    assert "manifest_not_opened" in receipt["blockers"]


def test_limited_unattended_sender_executes_once_and_blocks_duplicate(tmp_path, monkeypatch) -> None:
    write_ready_artifacts(tmp_path)
    manifest = build_open_manifest(tmp_path, opened=True, confirmation=OPEN_CONFIRMATION, operator="tester")
    manifest_path = tmp_path / "manifest.json"
    ledger_path = tmp_path / "ledger.jsonl"
    write_json(manifest_path, manifest)

    def fake_send(target: str, payload: str) -> dict:
        return {"command": ["hermes", "send"], "returncode": 0, "stdout": '{"success": true, "message_id": "unit"}', "stderr": "", "parsed": {"success": True, "message_id": "unit"}}

    monkeypatch.setattr(sender, "run_send", fake_send)
    first = sender.build_or_execute(manifest_path, ledger_path, execute=True)
    second = sender.build_or_execute(manifest_path, ledger_path, execute=True)

    assert first["status"] == "LIMITED_UNATTENDED_DISPATCH_EXECUTED"
    assert first["external_side_effects_executed"] is True
    assert first["executed_count"] == 1
    assert second["status"] == "LIMITED_UNATTENDED_DISPATCH_BLOCKED"
    assert "duplicate_idempotency_key" in second["target_receipts"][0]["blockers"]
