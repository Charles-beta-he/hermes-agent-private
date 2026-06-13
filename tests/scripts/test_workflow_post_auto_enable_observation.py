from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_post_auto_enable_observation.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_post_auto_enable_observation", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write_json(path: Path, payload: dict):
    path.write_text(json.dumps(payload))
    return path


def base_files(tmp_path: Path):
    projection = write_json(tmp_path / "projection.json", {
        "status": "SCHEDULER_READBACK_PROJECTION_READY",
        "wrapper_projections": [
            {"job_id": "a", "job_name": "daily", "script": "workflow-kernel-multi-model-smoke-daily-full-runner.py", "projection_status": "KERNEL_SHIM_SCHEDULER_READBACK_READY", "next_run_at": "later", "last_status": "error"},
            {"job_id": "b", "job_name": "every4h", "script": "workflow-kernel-multi-model-smoke-every4h-runner.py", "projection_status": "KERNEL_SHIM_NO_GATE_DECISION_NEEDED", "next_run_at": "later", "last_status": "ok"},
        ],
    })
    correlation = write_json(tmp_path / "correlation.json", {"status": "AWAITING_POST_PROJECTION_SCHEDULED_RUN", "correlation_counts": {"ONLY_PRE_PROJECTION_RUN_OBSERVED": 2}})
    readiness = write_json(tmp_path / "readiness.json", {"status": "RUNTIME_READINESS_SUMMARY_DEGRADED_NOT_BLOCKED", "blocking_checks": ["run_correlation_partial_or_better"]})
    drift = write_json(tmp_path / "drift.json", {"status": "DRIFT_WATCH_CLEAR_READ_ONLY", "missing_receipt_jobs": []})
    return projection, correlation, readiness, drift


def test_observation_pending_scheduled_run(tmp_path):
    mod = load()
    projection, correlation, readiness, drift = base_files(tmp_path)
    report = mod.build_report(projection_path=projection, correlation_path=correlation, readiness_path=readiness, drift_path=drift)
    assert report["status"] == "POST_AUTO_ENABLE_OBSERVATION_PENDING_SCHEDULED_RUN"
    assert report["runtime_switch_enabled"] is True
    assert report["missing_receipt_jobs"] == []
    assert len(report["scheduler_native_jobs"]) == 2


def test_write_report(tmp_path):
    mod = load()
    projection, correlation, readiness, drift = base_files(tmp_path)
    report = mod.build_report(projection_path=projection, correlation_path=correlation, readiness_path=readiness, drift_path=drift)
    artifacts = mod.write_report(report, tmp_path / "out")
    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["md"]).read_text().startswith("# Workflow Kernel")
