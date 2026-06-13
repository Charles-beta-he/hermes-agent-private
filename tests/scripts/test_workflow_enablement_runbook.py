from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_enablement_runbook.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_enablement_runbook", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    sequence = tmp_path / "sequence.json"
    packet = tmp_path / "packet.json"
    sequence.write_text(json.dumps({"status": "WORKFLOW_SEQUENCE_MAP_READY"}, ensure_ascii=False))
    packet.write_text(json.dumps({
        "status": "RUNTIME_ENABLEMENT_HUMAN_AUTH_PACKET_READY",
        "closed_boundaries_without_explicit_authorization": ["mutate ~/.hermes/cron/jobs.json"],
    }, ensure_ascii=False))
    return sequence, packet


def test_runbook_ready_with_sequence_and_packet(tmp_path):
    mod = load()
    sequence, packet = write_inputs(tmp_path)
    report = mod.build_runbook(sequence, packet)
    assert report["status"] == "ENABLEMENT_RUNBOOK_READY"
    assert report["safe_default_track"] == "A_READ_ONLY_CONTINUE"
    assert report["next_recommended_phase"].startswith("Phase40")


def test_runbook_blocks_when_inputs_missing(tmp_path):
    mod = load()
    report = mod.build_runbook(tmp_path / "missing-sequence.json", tmp_path / "missing-packet.json")
    assert report["status"] == "ENABLEMENT_RUNBOOK_BLOCKED_BY_MISSING_INPUTS"


def test_runbook_contains_no_execute_boundary_commands(tmp_path):
    mod = load()
    sequence, packet = write_inputs(tmp_path)
    report = mod.build_runbook(sequence, packet)
    all_commands = "\n".join(row["command"] for row in report["preflight_commands"] + report["dry_run_commands"])
    assert "cronjob run" not in all_commands
    assert "send_message" not in all_commands
    assert "RUNTIME_SWITCH_ENABLED=True" not in all_commands
    assert any("blocking_checks=[]" in gate for gate in report["go_no_go_gates"])


def test_write_report_creates_artifacts(tmp_path):
    mod = load()
    sequence, packet = write_inputs(tmp_path)
    report = mod.build_runbook(sequence, packet)
    artifacts = mod.write_report(report, tmp_path / "out")
    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["md"]).exists()
    assert artifacts["sha256"]
