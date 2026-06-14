from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_future_unattended_reopen_packet.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_future_unattended_reopen_packet", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(root: Path, rel: str, payload: dict):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload))


def seed(root: Path):
    write(root, "docs/private/workflow-kernel-manual-only-monitoring/workflow_kernel_manual_only_monitoring_20260613_phase65.json", {"status": "MANUAL_ONLY_MONITORING_CLEAR", "unattended_dispatch_enabled": False, "read_only": True, "monitor_digest": "md"})
    write(root, "docs/private/workflow-kernel-manual-only-closure/workflow_kernel_manual_only_closure_20260613_phase64.json", {"status": "MANUAL_ONLY_CLOSURE_READY", "unattended_dispatch_enabled": False, "closure_digest": "cd"})


def test_reopen_packet_is_ready_but_packet_only(tmp_path):
    mod = load()
    seed(tmp_path)
    packet = mod.build_packet(tmp_path)
    assert packet["status"] == "FUTURE_UNATTENDED_REOPEN_PACKET_READY"
    assert packet["recommended_default"] == "A_NO_REOPEN_KEEP_MANUAL_ONLY"
    assert packet["selected_option"] is None
    assert packet["packet_only"] is True
    assert packet["reopen_apply_enabled"] is False
    assert packet["unattended_dispatch_enabled"] is False
    assert packet["external_side_effects_executed"] is False
    assert packet["message_sent"] is False


def test_decision_schema_does_not_allow_apply(tmp_path):
    mod = load()
    seed(tmp_path)
    packet = mod.build_packet(tmp_path)
    schema = mod.build_schema(packet)
    assert schema["packet_digest"] == packet["packet_digest"]
    assert schema["apply_allowed_by_this_schema"] is False
    assert "keep_closed" in schema["allowed_decisions"]
