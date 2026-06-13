from __future__ import annotations

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "workflow_drift_watch.py"


def load():
    spec = importlib.util.spec_from_file_location("workflow_drift_watch", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_latest_receipts_picks_newest_per_job(tmp_path):
    mod = load()
    p = tmp_path / "receipts.jsonl"
    p.write_text("\n".join([
        json.dumps({"job_id": "2c14ca2a9bd2", "created_at": "2026-06-13T10:00:00Z", "runtime_mode": "old"}),
        json.dumps({"job_id": "2c14ca2a9bd2", "created_at": "2026-06-13T11:00:00Z", "runtime_mode": "new"}),
    ]))
    latest = mod.latest_receipts(p)
    assert latest["2c14ca2a9bd2"]["runtime_mode"] == "new"


def test_artifact_drift_missing_is_stale():
    mod = load()
    state = mod.artifact_drift("x", None, None, now=mod.utc_now(), stale_hours=24)
    assert state["exists"] is False
    assert state["stale"] is True


def test_build_watch_shape_and_safe_boundaries(tmp_path):
    mod = load()
    ledger = tmp_path / "receipts.jsonl"
    ledger.write_text(json.dumps({"job_id": "2c14ca2a9bd2", "created_at": mod.iso(mod.utc_now()), "kernel_shim": True}) + "\n")
    report = mod.build_watch(receipt_ledger=ledger, stale_hours=24)
    assert report["report_id"] == "workflow_kernel_drift_watch_20260613_phase40"
    assert "no cron trigger" in report["safe_boundaries"]
    assert "42baeebb646c" in report["missing_receipt_jobs"]
    assert "production_receipts_not_complete" in report["drift_signals"]


def test_write_report_creates_artifacts(tmp_path):
    mod = load()
    report = mod.build_watch(receipt_ledger=tmp_path / "missing.jsonl", stale_hours=24)
    artifacts = mod.write_report(report, tmp_path / "out")
    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["md"]).exists()
    assert artifacts["sha256"]
