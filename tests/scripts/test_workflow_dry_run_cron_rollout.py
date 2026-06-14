from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_dry_run_cron_rollout.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_dry_run_cron_rollout", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_build_job_is_no_agent_local_only():
    mod = load()
    job = mod.build_job(mod.utc_now())
    assert job["id"] == mod.JOB_ID
    assert job["script"] == mod.SCRIPT_NAME
    assert job["no_agent"] is True
    assert job["deliver"] == "local"
    assert job["enabled"] is True


def test_rollout_creates_single_idempotent_job(tmp_path, monkeypatch):
    mod = load()
    home = tmp_path / ".hermes"
    cron = home / "cron"
    cron.mkdir(parents=True)
    jobs_path = cron / "jobs.json"
    jobs_path.write_text(json.dumps({"jobs": []}))
    monkeypatch.setattr(mod, "hermes_home", lambda: home)
    report = mod.rollout(dry_run=False)
    assert report["status"] == "DRY_RUN_CRON_ROLLOUT_APPLIED"
    assert report["cron_state_mutated"] is True
    assert report["would_send"] is False
    data = json.loads(jobs_path.read_text())
    assert [j["id"] for j in data["jobs"]] == [mod.JOB_ID]
    report2 = mod.rollout(dry_run=False)
    data2 = json.loads(jobs_path.read_text())
    assert [j["id"] for j in data2["jobs"]].count(mod.JOB_ID) == 1
    assert report2["action"] == "updated"


def test_dry_run_does_not_write_jobs(tmp_path, monkeypatch):
    mod = load()
    home = tmp_path / ".hermes"
    cron = home / "cron"
    cron.mkdir(parents=True)
    jobs_path = cron / "jobs.json"
    jobs_path.write_text(json.dumps({"jobs": []}))
    monkeypatch.setattr(mod, "hermes_home", lambda: home)
    report = mod.rollout(dry_run=True)
    assert report["status"] == "DRY_RUN_CRON_ROLLOUT_DRY_RUN_READY"
    assert report["cron_state_mutated"] is False
    assert json.loads(jobs_path.read_text())["jobs"] == []
