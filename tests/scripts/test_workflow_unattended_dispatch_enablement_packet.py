from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_unattended_dispatch_enablement_packet.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_unattended_dispatch_enablement_packet", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def seed(root: Path):
    files = {
        "docs/private/workflow-kernel-post-send-correlation-watch/workflow_kernel_post_send_correlation_watch_20260613_phase61.json": {"status": "POST_SEND_CORRELATION_COMPENSATION_WATCH_READY"},
        "docs/private/workflow-kernel-unattended-dispatch-policy/workflow_kernel_unattended_dispatch_policy_20260613_phase54.json": {"status": "UNATTENDED_DISPATCH_POLICY_GATE_READY", "unattended_dispatch_enabled": False},
        "docs/private/workflow-kernel-dry-run-cron-rollout/workflow_kernel_dry_run_cron_rollout_20260613_phase57.json": {"status": "DRY_RUN_CRON_ROLLOUT_APPLIED"},
        "docs/private/workflow-kernel-guarded-manual-send-runner/workflow_kernel_one_shot_guarded_send_20260613_phase60.json": {"status": "ONE_SHOT_GUARDED_SEND_VERIFIED", "unattended_dispatch_enabled": False},
    }
    for rel, payload in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload))


def test_packet_ready_but_does_not_enable_unattended(tmp_path):
    mod = load()
    seed(tmp_path)
    packet = mod.build_packet(tmp_path)
    assert packet["status"] == "UNATTENDED_DISPATCH_ENABLEMENT_PACKET_READY"
    assert packet["unattended_dispatch_enabled"] is False
    assert packet["external_side_effects_executed"] is False
    assert packet["cron_state_mutated"] is False
    assert packet["recommended_default"] == "A_KEEP_MANUAL_ONLY"
    assert packet["required_controls_for_future_enablement"]["requires_idempotency_key"] is True


def test_missing_phase61_blocks_packet(tmp_path):
    mod = load()
    seed(tmp_path)
    (tmp_path / "docs/private/workflow-kernel-post-send-correlation-watch/workflow_kernel_post_send_correlation_watch_20260613_phase61.json").unlink()
    packet = mod.build_packet(tmp_path)
    assert packet["status"] == "UNATTENDED_DISPATCH_ENABLEMENT_PACKET_BLOCKED"
    assert "phase61_correlation_ready" in packet["blockers"]


def test_decision_schema_is_record_only():
    mod = load()
    schema = mod.decision_schema("digest")
    assert "approve_limited_unattended_future_phase" in schema["allowed_decisions"]
    assert "must not enable unattended" in schema["execution_boundary"]
