from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_cron_run_outcome_correlation.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_cron_run_outcome_correlation", SCRIPT)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False))
    return path


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    return path


def jobs_payload(every4h_status: str = "ok") -> dict:
    return {
        "jobs": [
            {"id": "42baeebb646c", "name": "multi-model-smoke-daily-full", "last_run_at": "2026-06-13T08:37:00+08:00", "last_status": "error", "next_run_at": "2026-06-14T08:35:00+08:00"},
            {"id": "2c14ca2a9bd2", "name": "multi-model-smoke-every-4h", "last_run_at": "2026-06-14T00:17:00+08:00", "last_status": every4h_status, "next_run_at": "2026-06-14T04:17:00+08:00"},
        ]
    }


def projection_payload() -> dict:
    return {
        "report_id": "projection_test",
        "created_at": "2026-06-13T14:27:16Z",
        "wrapper_projections": [
            {"job_id": "42baeebb646c", "job_name": "multi-model-smoke-daily-full", "projection_status": "KERNEL_SHIM_SCHEDULER_READBACK_READY", "kernel_shim_detected": True, "gate_decision": "retry_allowed_by_provider_health"},
            {"job_id": "2c14ca2a9bd2", "job_name": "multi-model-smoke-every-4h", "projection_status": "KERNEL_SHIM_NO_GATE_DECISION_NEEDED", "kernel_shim_detected": True, "gate_decision": None},
        ],
    }


def test_correlates_post_projection_every4h_run(tmp_path):
    mod = load()
    jobs = write_json(tmp_path / "jobs.json", jobs_payload())
    projection = write_json(tmp_path / "projection.json", projection_payload())
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    write_jsonl(log_dir / "multi-model-smoke-every4h-wrapper.jsonl", [{"timestamp": "2026-06-13T16:17:36Z", "status": "ok", "ok": True, "returncode": 0, "script_args": ["--skip-claude", "--timeout", "45"], "summary": {"status": "pass"}}])
    write_jsonl(log_dir / "multi-model-smoke-daily-full-wrapper.jsonl", [{"timestamp": "2026-06-13T00:35:19Z", "status": "partial_fail_recorded", "ok": False}])

    report = mod.build_report(jobs_path=jobs, projection_path=projection, log_dir=log_dir)

    assert report["status"] == "REAL_RUN_CORRELATION_PARTIAL_READY"
    assert report["correlation_counts"] == {"ONLY_PRE_PROJECTION_RUN_OBSERVED": 1, "REAL_RUN_AFTER_PROJECTION_CORRELATED": 1}
    by_id = {o["job_id"]: o for o in report["outcomes"]}
    assert by_id["2c14ca2a9bd2"]["latest_log_after_projection"] is True
    assert by_id["42baeebb646c"]["latest_log_after_projection"] is False


def test_awaiting_when_all_logs_pre_projection(tmp_path):
    mod = load()
    jobs = write_json(tmp_path / "jobs.json", jobs_payload())
    projection = write_json(tmp_path / "projection.json", projection_payload())
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    write_jsonl(log_dir / "multi-model-smoke-every4h-wrapper.jsonl", [{"timestamp": "2026-06-13T10:17:36Z", "status": "ok", "ok": True}])
    write_jsonl(log_dir / "multi-model-smoke-daily-full-wrapper.jsonl", [{"timestamp": "2026-06-13T00:35:19Z", "status": "partial_fail_recorded", "ok": False}])

    report = mod.build_report(jobs_path=jobs, projection_path=projection, log_dir=log_dir)

    assert report["status"] == "AWAITING_POST_PROJECTION_SCHEDULED_RUN"
    assert report["correlation_counts"] == {"ONLY_PRE_PROJECTION_RUN_OBSERVED": 2}


def test_missing_wrapper_log_degrades(tmp_path):
    mod = load()
    jobs = write_json(tmp_path / "jobs.json", jobs_payload())
    projection = write_json(tmp_path / "projection.json", projection_payload())
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    report = mod.build_report(jobs_path=jobs, projection_path=projection, log_dir=log_dir)

    assert report["status"] == "RUN_CORRELATION_DEGRADED"
    assert report["correlation_counts"] == {"NO_WRAPPER_LOG_FOUND": 2}


def test_latest_row_uses_timestamp_order_not_file_order():
    mod = load()
    row = mod.latest_row([
        {"timestamp": "2026-06-13T16:17:36Z", "status": "new"},
        {"timestamp": "2026-06-13T10:17:36Z", "status": "old"},
    ])
    assert row["status"] == "new"
