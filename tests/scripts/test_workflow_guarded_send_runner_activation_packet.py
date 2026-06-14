from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_guarded_send_runner_activation_packet.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_guarded_send_runner_activation_packet", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_packet_is_not_activated_and_requires_manual_gate(tmp_path):
    mod = load()
    write_json(tmp_path / "docs/private/workflow-kernel-unattended-dispatch-policy/workflow_kernel_unattended_dispatch_policy_20260613_phase54.json", {"status": "UNATTENDED_DISPATCH_POLICY_GATE_READY", "rate_limits": {"telegram_home": "1/manual"}})
    write_json(tmp_path / "docs/private/workflow-kernel-policy-editor/workflow_kernel_policy_editor_projection_20260613_phase56.json", {"status": "POLICY_EDITOR_APPROVAL_UI_READY", "latest_decision": {"decision": "approve_dry_run_only"}})
    write_json(tmp_path / "docs/private/workflow-kernel-dry-run-cron-rollout/workflow_kernel_dry_run_cron_rollout_20260613_phase57.json", {"status": "DRY_RUN_CRON_ROLLOUT_APPLIED", "job_id": "wk57dryrun01"})

    packet = mod.build_packet(tmp_path)

    assert packet["status"] == "GUARDED_SEND_RUNNER_ACTIVATION_PACKET_READY"
    assert packet["authority_assessment"]["manual_send_runner_is_active_now"] is False
    assert packet["execution_boundary"]["external_side_effects_executed"] is False
    assert packet["execution_boundary"]["send_message_called"] is False
    assert packet["contract"]["required_manual_gate"]["requires_exact_payload"] is True
    assert packet["contract"]["safe_defaults"]["automatic_send_allowed"] is False


def test_write_report_creates_contract_and_packet(tmp_path, monkeypatch):
    mod = load()
    monkeypatch.chdir(tmp_path)
    packet = mod.build_packet(tmp_path)
    mod.write_report(packet)
    assert mod.REPORT_JSON.exists()
    assert mod.CONTRACT_JSON.exists()
    saved = json.loads(mod.REPORT_JSON.read_text())
    assert saved["contract_digest"] == packet["contract_digest"]
