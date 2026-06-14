from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_guarded_manual_send_runner.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_guarded_manual_send_runner", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_activation(root: Path):
    path = root / "docs/private/workflow-kernel-guarded-send-runner-activation/workflow_kernel_guarded_send_runner_activation_20260613_phase58.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "status": "GUARDED_SEND_RUNNER_ACTIVATION_PACKET_READY",
        "contract_digest": "digest-1",
        "authority_assessment": {"manual_send_runner_is_active_now": False},
        "contract": {"required_manual_gate": {"requires_exact_payload": True}},
    }))


def test_preview_requires_ready_activation_and_exact_target(tmp_path):
    mod = load()
    write_activation(tmp_path)
    preview = mod.build_preview(tmp_path, "telegram", "hello", "reason")
    assert preview["status"] == "GUARDED_MANUAL_SEND_RUNNER_READY"
    assert preview["external_side_effects_executed"] is False
    blocked = mod.build_preview(tmp_path, "lark", "hello", "reason")
    assert blocked["status"] == "GUARDED_MANUAL_SEND_RUNNER_BLOCKED"
    assert "target_not_allowlisted" in blocked["validation"]["errors"]


def test_execute_once_records_receipt_with_mocked_send(tmp_path, monkeypatch):
    mod = load()
    monkeypatch.chdir(tmp_path)
    write_activation(tmp_path)
    def fake_send(target, payload):
        return {"command": ["hermes", "send"], "returncode": 0, "stdout": '{"success": true, "platform": "telegram", "message_id": 999}', "stderr": "", "parsed": {"success": True, "platform": "telegram", "message_id": 999}}
    monkeypatch.setattr(mod, "run_hermes_send", fake_send)
    receipt = mod.execute_once(tmp_path, "telegram", "hello", "reason")
    assert receipt["status"] == "ONE_SHOT_GUARDED_SEND_VERIFIED"
    assert receipt["external_side_effects_executed"] is True
    assert receipt["unattended_dispatch_enabled"] is False
    assert (mod.LEDGER).exists()


def test_idempotency_key_mismatch_blocks(tmp_path):
    mod = load()
    write_activation(tmp_path)
    preview = mod.build_preview(tmp_path, "telegram", "hello", "reason", "wrong")
    assert preview["status"] == "GUARDED_MANUAL_SEND_RUNNER_BLOCKED"
    assert "idempotency_key_mismatch" in preview["validation"]["errors"]
