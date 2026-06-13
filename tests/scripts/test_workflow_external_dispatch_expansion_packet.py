from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_external_dispatch_expansion_packet.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_external_dispatch_expansion_packet", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_packet_is_candidate_only_and_fail_closed():
    mod = load()
    packet = mod.build_packet()
    assert packet["status"] == "EXTERNAL_DISPATCH_EXPANSION_CANDIDATE_READY"
    assert packet["unattended_external_dispatch_enabled"] is False
    assert packet["external_side_effects_executed"] is False
    assert packet["default_safe_adapter"] == "local_outbox_simulator"
    assert "target_allowlist" in packet["policy_requirements"]
    assert "no send_message call" in packet["safe_boundaries"]


def test_external_adapters_require_exact_target_and_payload():
    mod = load()
    packet = mod.build_packet()
    external = [a for a in packet["adapter_options"] if a["external_side_effects"]]
    assert {a["adapter"] for a in external} >= {"telegram", "lark_feishu", "github"}
    for adapter in external:
        assert "explicit" in adapter["required_authority"]
        assert "idempotency_key" in adapter["required_inputs"]
    assert packet["next_required_selection"]["target"].startswith("exact")


def test_write_report(tmp_path):
    mod = load()
    packet = mod.build_packet()
    artifacts = mod.write_report(packet, tmp_path)
    assert Path(artifacts["json"]).exists()
    assert "Adapter options" in Path(artifacts["md"]).read_text()
