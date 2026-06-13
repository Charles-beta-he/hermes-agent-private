from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_runtime_readiness_summary.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_runtime_readiness_summary", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_artifact_state_marks_missing_as_stale():
    mod = load()
    state = mod.artifact_state("missing", None, None, now=mod.utc_now(), stale_hours=24)
    assert state["exists"] is False
    assert state["stale"] is True


def test_artifact_state_marks_fresh_created_at():
    mod = load()
    now = mod.utc_now()
    state = mod.artifact_state("fresh", Path("/tmp/fresh.json"), {"status": "OK", "created_at": mod.iso(now), "report_id": "r1"}, now=now, stale_hours=24)
    assert state["exists"] is True
    assert state["status"] == "OK"
    assert state["stale"] is False
    assert state["report_id"] == "r1"


def test_endpoint_statuses_extracts_probe_results():
    mod = load()
    statuses = mod.endpoint_statuses({"results": [{"id": "xiaomi", "classification": "reachable"}, {"id": "openai", "classification": "reachable"}]})
    assert statuses == {"xiaomi": "reachable", "openai": "reachable"}


def test_build_report_is_read_only_summary_shape():
    mod = load()
    report = mod.build_report(stale_hours=24)
    assert report["report_id"] == "workflow_kernel_runtime_readiness_summary_20260613_phase36"
    assert report["runtime_switch_enabled_by_default"] is True
    assert "runtime switch is auto-enabled" in report["safe_boundaries"]
    assert "projection" in report["artifact_states"]
    assert "correlation" in report["artifact_states"]
    assert "probe" in report["artifact_states"]
    assert set(report["checks"]).issuperset({
        "runtime_switch_ready_or_enabled",
        "scheduler_projection_ready",
        "receipt_aware_correlation_ready",
        "global_runtime_switch_auto_enabled",
    })
