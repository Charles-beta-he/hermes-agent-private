from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_cron_runtime_projection.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_cron_runtime_projection", SCRIPT)
    assert spec is not None
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False))
    return path


def jobs_payload() -> dict:
    return {
        "jobs": [
            {
                "id": "42baeebb646c",
                "name": "multi-model-smoke-daily-full",
                "script": "multi-model-smoke-daily-full-wrapper.py",
                "enabled": True,
                "state": "scheduled",
                "last_status": "error",
                "next_run_at": "2026-06-14T00:00:00+08:00",
            },
            {
                "id": "2c14ca2a9bd2",
                "name": "multi-model-smoke-every-4h",
                "script": "multi-model-smoke-every4h-wrapper.py",
                "enabled": True,
                "state": "scheduled",
                "last_status": "error",
                "next_run_at": "2026-06-14T04:00:00+08:00",
            },
        ]
    }


def gate_payload(created_at: str) -> dict:
    return {
        "report_id": "gate_test",
        "created_at": created_at,
        "degraded_endpoints": [],
        "decisions": [
            {"job_id": "42baeebb646c", "decision": "retry_allowed_by_provider_health", "target_endpoint": "xiaomi", "endpoint_classification": "reachable"},
            {"job_id": "2c14ca2a9bd2", "decision": "retry_allowed_by_provider_health", "target_endpoint": None, "endpoint_classification": None},
        ],
    }


def probe_payload(created_at: str) -> dict:
    return {
        "report_id": "probe_test",
        "created_at": created_at,
        "results": [
            {"id": "xiaomi", "classification": "reachable"},
            {"id": "openai", "classification": "reachable"},
        ],
    }


def write_kernel_wrappers(script_dir: Path):
    script_dir.mkdir()
    for name in ["multi-model-smoke-daily-full-wrapper.py", "multi-model-smoke-every4h-wrapper.py", "workflow-kernel-multi-model-smoke-every4h-runner.py"]:
        (script_dir / name).write_text("from workflow_kernel import cron_wrappers as kernel_cron\n")


def test_projection_ready_for_two_kernel_wrappers(tmp_path):
    mod = load()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    jobs = write_json(tmp_path / "jobs.json", jobs_payload())
    gate = write_json(tmp_path / "gate.json", gate_payload(now.isoformat().replace("+00:00", "Z")))
    probe = write_json(tmp_path / "probe.json", probe_payload(now.isoformat().replace("+00:00", "Z")))
    script_dir = tmp_path / "scripts"
    write_kernel_wrappers(script_dir)

    report = mod.build_report(jobs_path=jobs, gate_path=gate, probe_path=probe, script_dir=script_dir, stale_hours=24)

    assert report["status"] == "SCHEDULER_READBACK_PROJECTION_READY"
    assert report["projection_counts"] == {"KERNEL_SHIM_SCHEDULER_READBACK_READY": 2}
    assert all(p["kernel_shim_detected"] for p in report["wrapper_projections"])
    assert report["runtime_switch_enabled_by_default"] is False


def test_projection_detects_stale_gate(tmp_path):
    mod = load()
    old = (datetime.now(timezone.utc) - timedelta(hours=48)).replace(microsecond=0)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    jobs = write_json(tmp_path / "jobs.json", jobs_payload())
    gate = write_json(tmp_path / "gate.json", gate_payload(old.isoformat().replace("+00:00", "Z")))
    probe = write_json(tmp_path / "probe.json", probe_payload(now.isoformat().replace("+00:00", "Z")))
    script_dir = tmp_path / "scripts"
    write_kernel_wrappers(script_dir)

    report = mod.build_report(jobs_path=jobs, gate_path=gate, probe_path=probe, script_dir=script_dir, stale_hours=24)

    assert report["status"] == "SCHEDULER_READBACK_PROJECTION_DEGRADED"
    assert report["gate_artifact"]["stale"] is True
    assert report["probe_artifact"]["stale"] is False


def test_projection_detects_wrapper_not_using_kernel(tmp_path):
    mod = load()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    jobs = write_json(tmp_path / "jobs.json", jobs_payload())
    gate = write_json(tmp_path / "gate.json", gate_payload(now.isoformat().replace("+00:00", "Z")))
    probe = write_json(tmp_path / "probe.json", probe_payload(now.isoformat().replace("+00:00", "Z")))
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (script_dir / "multi-model-smoke-daily-full-wrapper.py").write_text("print('old wrapper')\n")
    (script_dir / "multi-model-smoke-every4h-wrapper.py").write_text("from workflow_kernel import cron_wrappers as kernel_cron\n")

    report = mod.build_report(jobs_path=jobs, gate_path=gate, probe_path=probe, script_dir=script_dir, stale_hours=24)

    assert report["status"] == "SCHEDULER_READBACK_PROJECTION_DEGRADED"
    statuses = {p["job_id"]: p["projection_status"] for p in report["wrapper_projections"]}
    assert statuses["42baeebb646c"] == "WRAPPER_NOT_USING_KERNEL_SHIM"
    assert statuses["2c14ca2a9bd2"] == "KERNEL_SHIM_SCHEDULER_READBACK_READY"


def test_projection_ready_for_scheduler_native_every4h_script(tmp_path):
    mod = load()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    payload = jobs_payload()
    payload["jobs"][1]["script"] = "workflow-kernel-multi-model-smoke-every4h-runner.py"
    jobs = write_json(tmp_path / "jobs.json", payload)
    gate = write_json(tmp_path / "gate.json", gate_payload(now.isoformat().replace("+00:00", "Z")))
    probe = write_json(tmp_path / "probe.json", probe_payload(now.isoformat().replace("+00:00", "Z")))
    script_dir = tmp_path / "scripts"
    write_kernel_wrappers(script_dir)

    report = mod.build_report(jobs_path=jobs, gate_path=gate, probe_path=probe, script_dir=script_dir, stale_hours=24)

    assert report["status"] == "SCHEDULER_READBACK_PROJECTION_READY"
    every4h = {p["job_id"]: p for p in report["wrapper_projections"]}["2c14ca2a9bd2"]
    assert every4h["script"] == "workflow-kernel-multi-model-smoke-every4h-runner.py"
    assert every4h["script_allowed"] is True
    assert every4h["projection_status"] == "KERNEL_SHIM_SCHEDULER_READBACK_READY"


def test_newest_path_selects_latest_mtime(tmp_path):
    mod = load()
    first = write_json(tmp_path / "a.json", {"created_at": "2026-01-01T00:00:00Z"})
    second = write_json(tmp_path / "b.json", {"created_at": "2026-01-02T00:00:00Z"})
    first.touch()
    second.touch()
    assert mod.newest_path(tmp_path / "*.json") == second
