from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_runtime_enablement_packet.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_runtime_enablement_packet", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_summary(path: Path, status: str = "RUNTIME_READINESS_SUMMARY_READY_PENDING_EXPLICIT_ENABLE", blocking=None) -> Path:
    path.write_text(json.dumps({
        "report_id": "summary1",
        "status": status,
        "blocking_checks": [] if blocking is None else blocking,
        "runtime_switch_enabled_by_default": False,
    }, ensure_ascii=False))
    return path


def test_packet_ready_when_summary_ready(tmp_path):
    mod = load()
    summary = write_summary(tmp_path / "summary.json")
    packet = mod.build_packet(summary)
    assert packet["status"] == "RUNTIME_ENABLEMENT_HUMAN_AUTH_PACKET_READY"
    assert packet["decision_required"] is True
    assert packet["recommended_safe_default"] == "A_READ_ONLY_CONTINUE"
    assert "mutate ~/.hermes/cron/jobs.json" in packet["closed_boundaries_without_explicit_authorization"]


def test_packet_blocked_when_summary_has_blockers(tmp_path):
    mod = load()
    summary = write_summary(tmp_path / "summary.json", blocking=["probe_artifact_fresh"])
    packet = mod.build_packet(summary)
    assert packet["status"] == "RUNTIME_ENABLEMENT_PACKET_BLOCKED_BY_READINESS"
    assert packet["summary_blocking_checks"] == ["probe_artifact_fresh"]


def test_option_matrix_has_single_recommended_safe_default():
    mod = load()
    options = mod.option_matrix()
    recommended = [o for o in options if o["recommended_default"]]
    assert [o["option_id"] for o in recommended] == ["A_READ_ONLY_CONTINUE"]
    assert {o["option_id"] for o in options}.issuperset({"B_SINGLE_WRAPPER_RUNTIME_ENABLE_DRY_RUN", "C_SINGLE_WRAPPER_SCHEDULER_NATIVE_ROLLOUT"})


def test_write_packet_creates_json_and_markdown(tmp_path):
    mod = load()
    summary = write_summary(tmp_path / "summary.json")
    packet = mod.build_packet(summary)
    artifacts = mod.write_packet(packet, tmp_path / "out")
    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["md"]).exists()
    assert artifacts["sha256"]
