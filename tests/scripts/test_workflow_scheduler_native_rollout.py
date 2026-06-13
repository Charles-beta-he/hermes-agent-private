from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_scheduler_native_rollout.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_scheduler_native_rollout", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def jobs_file(tmp_path: Path) -> Path:
    p = tmp_path / "jobs.json"
    p.write_text(json.dumps({"jobs": [
        {"id": "2c14ca2a9bd2", "script": "multi-model-smoke-every4h-wrapper.py"},
        {"id": "42baeebb646c", "script": "multi-model-smoke-daily-full-wrapper.py"},
        {"id": "other", "script": "x.py"},
    ]}, indent=2))
    return p


def test_dry_run_does_not_mutate_jobs(tmp_path):
    mod = load()
    p = jobs_file(tmp_path)
    before = p.read_text()
    report = mod.apply_rollout(jobs_path=p, dry_run=True)
    assert report["status"] == "SCHEDULER_NATIVE_ROLLOUT_DRY_RUN_READY"
    assert report["cron_state_mutated"] is False
    assert p.read_text() == before


def test_apply_mutates_one_script_and_creates_backup(tmp_path):
    mod = load()
    p = jobs_file(tmp_path)
    report = mod.apply_rollout(jobs_path=p, dry_run=False)
    assert report["status"] == "SCHEDULER_NATIVE_ROLLOUT_APPLIED"
    assert report["before_job_script"] == mod.OLD_SCRIPT
    assert report["after_job_script"] == mod.NEW_SCRIPT
    assert Path(report["backup_path"]).exists()
    assert p.read_text().count(mod.NEW_SCRIPT) == 1


def test_rollback_restores_old_script(tmp_path):
    mod = load()
    p = jobs_file(tmp_path)
    report = mod.apply_rollout(jobs_path=p, dry_run=False)
    rb = mod.rollback(jobs_path=p, backup=Path(report["backup_path"]))
    assert rb["status"] == "SCHEDULER_NATIVE_ROLLOUT_ROLLED_BACK"
    assert rb["restored_script"] == mod.OLD_SCRIPT


def test_daily_target_mutates_daily_script_only(tmp_path):
    mod = load()
    p = jobs_file(tmp_path)
    report = mod.apply_rollout(jobs_path=p, target="daily-full", dry_run=False)
    data = json.loads(p.read_text())
    by_id = {j["id"]: j for j in data["jobs"]}
    assert report["status"] == "SCHEDULER_NATIVE_ROLLOUT_APPLIED"
    assert report["phase"] == "44"
    assert by_id["42baeebb646c"]["script"] == "workflow-kernel-multi-model-smoke-daily-full-runner.py"
    assert by_id["2c14ca2a9bd2"]["script"] == "multi-model-smoke-every4h-wrapper.py"


def test_already_applied_is_idempotent(tmp_path):
    mod = load()
    p = tmp_path / "jobs.json"
    p.write_text(json.dumps({"jobs": [{"id": "2c14ca2a9bd2", "script": mod.NEW_SCRIPT}]}, indent=2))
    report = mod.apply_rollout(jobs_path=p, dry_run=False)
    assert report["status"] == "SCHEDULER_NATIVE_ROLLOUT_ALREADY_APPLIED"
    assert report["cron_state_mutated"] is False
