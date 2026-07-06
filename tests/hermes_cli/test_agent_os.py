import argparse
import json
from pathlib import Path

from hermes_cli.agent_os import build_agent_os_parser, load_agent_os_state, packet_dir


def _write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _seed_packet(root: Path) -> Path:
    packet = packet_dir(root)
    packet.mkdir(parents=True, exist_ok=True)
    _write_json(packet / "09-doctor-report.json", {"ok": True, "findings": [], "domain_entity_count": 11, "backlog_count": 2})
    _write_json(packet / "07-backlog.json", {"items": [
        {"id": "P0", "phase": "P0", "status": "completed"},
        {"id": "EXT", "phase": "external_dependency", "status": "external_dependency_open"},
    ]})
    _write_json(packet / "10-capability-projection.json", {"ok": True, "node_count": 3, "source_counts": {"skill": 2}, "projected_nodes_would_execute": False})
    _write_json(packet / "11-authority-ledger-report.json", {"ok": True, "record_count": 1, "runtime_mutation": False, "external_side_effects_executed": False, "payment_or_credential_action_executed": False})
    (packet / "11-authority-ledger-projection.jsonl").write_text(json.dumps({"id": "r1"}) + "\n", encoding="utf-8")
    _write_json(packet / "12-data-vault-projection.json", {"ok": True, "item_count": 2, "sensitivity_counts": {"D1": 1}, "raw_secret_exported": False, "global_memory_wip_write": False})
    _write_json(packet / "13-mission-runtime-state.json", {"ok": True, "stage": "executing", "objective": "advance safely", "evidence_dag": [{"id": "e1"}], "evidence_nodes": [{"id": "evidence.test", "claim": "works", "verdict": "pass"}], "open_boundaries": [{"id": "boundary.test", "status": "external"}], "stop_conditions": ["secret"], "runtime_mutation": False, "external_side_effects_executed": False, "taskpool_lifecycle_write_executed": False})
    _write_json(packet / "14-model-review-projection.json", {"ok": True, "mode": "projection", "model_review": {"automation_ceiling": "L3"}, "live_model_fanout_executed": False, "external_side_effects_executed": False})
    (packet / "15-ux-shell-projection.md").write_text("### Today\n### Model Console\n", encoding="utf-8")
    _write_json(packet / "16-device-agent-proposal-projection.json", {"ok": True, "mode": "device_projection", "device_nodes": [{"id": "d"}], "agent_proposal_inbox": [{"id": "p"}], "service_contract_stubs": [{"id": "s"}], "device_physical_effect_executed": False, "agent_proposal_auto_executed": False})
    return packet


def test_load_agent_os_state_is_read_only_projection(tmp_path):
    _seed_packet(tmp_path)

    state = load_agent_os_state(tmp_path)

    assert state["ok"] is True
    assert state["capabilities"]["node_count"] == 3
    assert state["capabilities"]["would_execute"] is False
    assert state["authority"]["external_side_effects_executed"] is False
    assert state["data_vault"]["raw_secret_exported"] is False
    assert state["mission_runtime"]["runtime_mutation"] is False
    assert state["mission_runtime"]["objective"] == "advance safely"
    assert state["mission_runtime"]["evidence_nodes"][0]["id"] == "evidence.test"
    assert state["mission_runtime"]["open_boundaries"][0]["id"] == "boundary.test"
    assert state["model_review"]["automation_ceiling"] == "L3"
    assert state["device_agent"]["device_physical_effect_executed"] is False
    assert state["device_agent"]["device_nodes"][0]["id"] == "d"
    assert state["device_agent"]["agent_proposal_inbox"][0]["id"] == "p"
    assert state["device_agent"]["service_contract_stubs"][0]["id"] == "s"
    assert state["side_effects_executed"] is False
    assert state["ux_shell"]["panels"] == ["Today", "Model Console"]


def test_agent_os_parser_dispatches_status_json(tmp_path, monkeypatch, capsys):
    _seed_packet(tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    parser = argparse.ArgumentParser(prog="hermes")
    subparsers = parser.add_subparsers(dest="command")
    build_agent_os_parser(subparsers)

    args = parser.parse_args(["agent-os", "status", "--json"])
    rc = args.func(args)

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["backlog"]["by_status"]["external_dependency_open"] == 1
    assert out["ux_shell"]["panels"] == ["Today", "Model Console"]
