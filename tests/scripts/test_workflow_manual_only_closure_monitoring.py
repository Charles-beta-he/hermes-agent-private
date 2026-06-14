from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_manual_only_closure_monitoring.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_manual_only_closure_monitoring", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(root: Path, rel: str, payload: dict):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload))


def seed(root: Path, hermes_home: Path):
    write(root, "docs/private/workflow-kernel-manual-only-closure/workflow_kernel_manual_only_closure_20260613_phase64.json", {"status": "MANUAL_ONLY_CLOSURE_READY", "unattended_dispatch_enabled": False, "external_side_effects_executed": False, "closure_digest": "cd"})
    write(root, "docs/private/workflow-kernel-unattended-decision-record/workflow_kernel_unattended_decision_record_20260613_phase63.json", {"decision": "keep_manual_only"})
    write(root, "docs/private/workflow-kernel-guarded-manual-send-runner/workflow_kernel_one_shot_guarded_send_20260613_phase60.json", {"status": "ONE_SHOT_GUARDED_SEND_VERIFIED", "send_result": {"message_id": "4310"}})
    write(root, "docs/private/workflow-kernel-dry-run-cron-rollout/workflow_kernel_dry_run_cron_rollout_20260613_phase57.json", {"would_send": False})
    write(hermes_home, "cron/jobs.json", {"jobs": {"wk57dryrun01": {"no_agent": True, "deliver": "local", "script": "workflow-kernel-unattended-dry-run-runner.py"}}})


def test_monitoring_clear_when_manual_only_invariants_hold(tmp_path):
    mod = load()
    hermes_home = tmp_path / "home"
    seed(tmp_path, hermes_home)
    monitor = mod.build_monitor(tmp_path, hermes_home)
    assert monitor["status"] == "MANUAL_ONLY_MONITORING_CLEAR"
    assert monitor["read_only"] is True
    assert monitor["unattended_dispatch_enabled"] is False
    assert monitor["external_side_effects_executed"] is False
    assert monitor["message_sent"] is False
    assert monitor["observed_job"]["deliver"] == "local"


def test_monitoring_detects_job_drift(tmp_path):
    mod = load()
    hermes_home = tmp_path / "home"
    seed(tmp_path, hermes_home)
    write(hermes_home, "cron/jobs.json", {"jobs": {"wk57dryrun01": {"no_agent": False, "deliver": "telegram", "script": "send.py"}}})
    monitor = mod.build_monitor(tmp_path, hermes_home)
    assert monitor["status"] == "MANUAL_ONLY_MONITORING_DRIFT_DETECTED"
    assert "dry_run_job_no_agent" in monitor["drift"]
    assert "dry_run_job_local_delivery" in monitor["drift"]
