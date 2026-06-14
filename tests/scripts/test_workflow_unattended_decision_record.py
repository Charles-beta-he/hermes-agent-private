from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_unattended_decision_record.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_unattended_decision_record", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def seed(root: Path):
    d = root / "docs/private/workflow-kernel-unattended-dispatch-enablement-packet"
    d.mkdir(parents=True, exist_ok=True)
    packet = {"status": "UNATTENDED_DISPATCH_ENABLEMENT_PACKET_READY", "packet_digest": "pd", "unattended_dispatch_enabled": False}
    schema = {"packet_digest": "pd", "allowed_decisions": ["keep_manual_only", "approve_dry_run_only", "approve_limited_unattended_future_phase", "reject", "request_changes"]}
    (d / "workflow_kernel_unattended_dispatch_enablement_packet_20260613_phase62.json").write_text(json.dumps(packet))
    (d / "workflow_kernel_unattended_dispatch_enablement_decision_schema_20260613_phase62.json").write_text(json.dumps(schema))


def test_keep_manual_only_record_is_ready_and_record_only(tmp_path):
    mod = load()
    seed(tmp_path)
    record = mod.build_record(tmp_path)
    assert record["status"] == "UNATTENDED_DECISION_RECORD_READY"
    assert record["decision"] == "keep_manual_only"
    assert record["selected_option"] == "A_KEEP_MANUAL_ONLY"
    assert record["unattended_dispatch_enabled"] is False
    assert record["external_side_effects_executed"] is False
    assert record["cron_state_mutated"] is False
    assert record["record_only"] is True


def test_invalid_decision_blocks(tmp_path):
    mod = load()
    seed(tmp_path)
    record = mod.build_record(tmp_path, "enable_now")
    assert record["status"] == "UNATTENDED_DECISION_RECORD_BLOCKED"
    assert "decision_allowed" in record["blockers"]
