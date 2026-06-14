from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_unattended_dry_run_scheduler.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_unattended_dry_run_scheduler", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def policy() -> dict:
    return {
        "status": "UNATTENDED_DISPATCH_POLICY_GATE_READY",
        "policy_digest": "abc123",
        "policy_version": "20260613.phase54.v1",
        "unattended_dispatch_enabled": False,
        "policy": {
            "target_allowlist": [{
                "adapter": "telegram",
                "target_type": "telegram_home_channel",
                "chat_id": "5524172309",
                "allowed_purposes": ["operator_control_tower_status"],
            }],
            "rate_limits": {"per_target_per_hour": 1, "per_target_per_day": 3},
        },
    }


def test_dry_run_receipt_never_sends():
    mod = load()
    receipt = mod.build_dry_run_receipt(policy(), now=datetime(2026, 6, 13, 3, 0, tzinfo=timezone.utc))
    assert receipt["status"] == "UNATTENDED_DRY_RUN_RECEIPT_RECORDED"
    assert receipt["would_send"] is False
    assert receipt["external_side_effects_executed"] is False
    assert receipt["unattended_dispatch_enabled"] is False
    assert receipt["adapter"] == "telegram"
    assert receipt["time_bucket_utc_hour"] == "20260613T03"


def test_policy_not_fail_closed_blocks():
    mod = load()
    p = policy()
    p["unattended_dispatch_enabled"] = True
    receipt = mod.build_dry_run_receipt(p)
    assert receipt["status"] == "UNATTENDED_DRY_RUN_BLOCKED"
    assert "policy_not_fail_closed" in receipt["blockers"]


def test_append_receipt_and_write_report(tmp_path):
    mod = load()
    receipt = mod.build_dry_run_receipt(policy())
    ledger = tmp_path / "receipts.jsonl"
    mod.append_receipt(ledger, receipt)
    assert ledger.read_text().count("UNATTENDED_DRY_RUN_RECEIPT_RECORDED") == 1
    report = mod.build_report(receipt, ledger_path=ledger, cadence="daily")
    artifacts = mod.write_report(report, tmp_path / "out")
    assert Path(artifacts["json"]).exists()
    assert "Unattended Dry-run Scheduler" in Path(artifacts["md"]).read_text()
