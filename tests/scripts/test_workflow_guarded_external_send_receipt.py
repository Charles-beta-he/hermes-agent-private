from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_guarded_external_send_receipt.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_guarded_external_send_receipt", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_verified_receipt_for_success_result():
    mod = load()
    report = mod.build_receipt(send_result={
        "success": True,
        "platform": "telegram",
        "chat_id": "123",
        "message_id": "456",
        "mirrored": True,
        "note": "sent",
    })
    assert report["status"] == "GUARDED_EXTERNAL_SEND_VERIFIED"
    assert report["external_side_effect_executed"] is True
    assert report["unattended_external_dispatch_enabled"] is False
    assert report["send_result"]["message_id"] == "456"
    assert "no repeated send" in report["safe_boundaries"]


def test_blocks_failed_result():
    mod = load()
    report = mod.build_receipt(send_result={"success": False, "platform": "telegram"})
    assert report["status"] == "GUARDED_EXTERNAL_SEND_BLOCKED"
    assert "send_result_not_success" in report["blockers"]
    assert "message_id_missing" in report["blockers"]


def test_write_report_creates_files(tmp_path):
    mod = load()
    report = mod.build_receipt(send_result={"success": True, "platform": "telegram", "chat_id": "123", "message_id": "456"})
    artifacts = mod.write_report(report, tmp_path)
    assert Path(artifacts["json"]).exists()
    assert "Message ID" in Path(artifacts["md"]).read_text()
