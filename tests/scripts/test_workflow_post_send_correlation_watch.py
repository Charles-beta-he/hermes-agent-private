from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_post_send_correlation_watch.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_post_send_correlation_watch", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def seed(root: Path):
    d = root / "docs/private/workflow-kernel-guarded-manual-send-runner"
    d.mkdir(parents=True)
    p59 = {"status": "GUARDED_MANUAL_SEND_RUNNER_READY", "idempotency_key": "k", "payload_sha256": "h", "external_side_effects_executed": False}
    p60 = {"status": "ONE_SHOT_GUARDED_SEND_VERIFIED", "idempotency_key": "k", "payload_sha256": "h", "unattended_dispatch_enabled": False, "compensation_path": "manual correction", "send_result": {"message_id": "1", "chat_id": "2"}}
    (d / "workflow_kernel_guarded_manual_send_runner_20260613_phase59.json").write_text(json.dumps(p59))
    (d / "workflow_kernel_one_shot_guarded_send_20260613_phase60.json").write_text(json.dumps(p60))
    (d / "workflow_kernel_guarded_manual_send_receipts.jsonl").write_text(json.dumps(p60) + "\n")


def test_build_report_correlates_phase60_receipt(tmp_path):
    mod = load()
    seed(tmp_path)
    report = mod.build_report(tmp_path)
    assert report["status"] == "POST_SEND_CORRELATION_COMPENSATION_WATCH_READY"
    assert report["message_id"] == "1"
    assert report["checks"]["idempotency_correlated"] is True
    assert report["checks"]["payload_hash_correlated"] is True
    assert report["matching_ledger_rows"] == 1
    assert report["external_side_effects_executed"] is False
    assert report["unattended_dispatch_enabled"] is False


def test_missing_ledger_blocks(tmp_path):
    mod = load()
    seed(tmp_path)
    ledger = tmp_path / "docs/private/workflow-kernel-guarded-manual-send-runner/workflow_kernel_guarded_manual_send_receipts.jsonl"
    ledger.unlink()
    report = mod.build_report(tmp_path)
    assert report["status"] == "POST_SEND_CORRELATION_COMPENSATION_WATCH_BLOCKED"
    assert "ledger_contains_receipt" in report["blockers"]
