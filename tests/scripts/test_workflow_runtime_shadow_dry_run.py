from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_runtime_shadow_dry_run.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_runtime_shadow_dry_run", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_run_shadow_proves_jobs_unchanged_with_fake_wrapper(tmp_path):
    mod = load()
    jobs = tmp_path / "jobs.json"
    jobs.write_text('{"jobs": []}')
    ledger = tmp_path / "receipts.jsonl"
    wrapper = tmp_path / "wrapper.py"
    wrapper.write_text(
        "import json, os\n"
        "payload={'status':'provider_health_exclusion_plan','kernel_shim':True,'model_generation_attempted':False}\n"
        "open(os.environ['HERMES_CRON_WRAPPER_RECEIPT_LEDGER'],'a').write(json.dumps({'status':'wrapper_run_receipt_recorded','runtime_mode':'plan_only','kernel_shim':True})+'\\n')\n"
        "print(json.dumps(payload))\n"
    )
    report = mod.run_shadow(receipt_ledger=ledger, wrapper=wrapper, jobs=jobs)
    assert report["status"] == "RUNTIME_SHADOW_DRY_RUN_READY"
    assert report["cron_state_mutated"] is False
    assert report["receipt_runtime_mode"] == "plan_only"


def test_read_last_jsonl_returns_latest(tmp_path):
    mod = load()
    p = tmp_path / "x.jsonl"
    p.write_text(json.dumps({"a": 1}) + "\n" + json.dumps({"a": 2}) + "\n")
    assert mod.read_last_jsonl(p) == {"a": 2}


def test_write_report_creates_artifacts(tmp_path):
    mod = load()
    report = {
        "status": "RUNTIME_SHADOW_DRY_RUN_READY",
        "job_id": "2c14ca2a9bd2",
        "payload_status": "provider_health_exclusion_plan",
        "receipt_runtime_mode": "plan_only",
        "cron_state_mutated": False,
        "credentialed_generation_attempted": False,
    }
    artifacts = mod.write_report(report, tmp_path / "out")
    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["md"]).exists()
    assert artifacts["sha256"]
