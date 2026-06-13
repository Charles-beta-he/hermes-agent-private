#!/usr/bin/env python3
"""Run the authorized single-wrapper runtime-enable shadow dry-run.

Phase 41 / option B boundary: execute the opted-in every-4h wrapper in plan-only
mode with a temporary receipt ledger, then prove cron/jobs.json did not change.
No cron trigger, scheduler mutation, credentialed generation, or external send.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflow_kernel.io import write_json_artifact

WRAPPER = Path.home() / ".hermes" / "scripts" / "multi-model-smoke-every4h-wrapper.py"
JOBS = Path.home() / ".hermes" / "cron" / "jobs.json"
DEFAULT_OUT = ROOT / "docs" / "private" / "workflow-kernel-runtime-shadow-dry-run"
JOB_ID = "2c14ca2a9bd2"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def read_last_jsonl(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    rows = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    if not rows:
        return None
    return json.loads(rows[-1])


def run_shadow(*, receipt_ledger: Path | None = None, wrapper: Path = WRAPPER, jobs: Path = JOBS) -> dict[str, Any]:
    before = sha(jobs)
    if receipt_ledger is None:
        receipt_ledger = Path(tempfile.gettempdir()) / f"phase41_shadow_receipts_{os.getpid()}.jsonl"
    env = os.environ.copy()
    env["HERMES_MULTI_MODEL_SMOKE_EVERY4H_PLAN_ONLY"] = "1"
    env["HERMES_CRON_WRAPPER_RECEIPT_LEDGER"] = str(receipt_ledger)
    proc = subprocess.run([sys.executable, str(wrapper)], text=True, capture_output=True, env=env, timeout=120)
    after = sha(jobs)
    payload = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else None
    receipt = read_last_jsonl(receipt_ledger)
    return {
        "report_id": "workflow_kernel_runtime_shadow_dry_run_20260613_phase41",
        "created_at": utc_now(),
        "status": "RUNTIME_SHADOW_DRY_RUN_READY" if proc.returncode == 0 and before == after and receipt else "RUNTIME_SHADOW_DRY_RUN_FAILED",
        "job_id": JOB_ID,
        "wrapper": str(wrapper),
        "receipt_ledger": str(receipt_ledger),
        "returncode": proc.returncode,
        "stderr_tail": proc.stderr[-2000:],
        "payload_status": (payload or {}).get("status"),
        "payload_kernel_shim": (payload or {}).get("kernel_shim"),
        "payload_model_generation_attempted": (payload or {}).get("model_generation_attempted"),
        "receipt_status": (receipt or {}).get("status"),
        "receipt_runtime_mode": (receipt or {}).get("runtime_mode"),
        "receipt_kernel_shim": (receipt or {}).get("kernel_shim"),
        "cron_jobs_sha_before": before,
        "cron_jobs_sha_after": after,
        "cron_state_mutated": before != after,
        "external_side_effects": False,
        "credentialed_generation_attempted": False,
    }


def write_report(report: dict[str, Any], out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "workflow_kernel_runtime_shadow_dry_run_20260613_phase41.json"
    md_path = out_dir / "workflow_kernel_runtime_shadow_dry_run_20260613_phase41.md"
    artifact = write_json_artifact(json_path, report)
    md_path.write_text(
        "\n".join([
            "# Workflow Kernel Runtime Shadow Dry-run — Phase 41",
            "",
            f"Status: **{report['status']}**",
            f"Job: `{report['job_id']}`",
            f"Payload status: `{report['payload_status']}`",
            f"Receipt runtime mode: `{report['receipt_runtime_mode']}`",
            f"Cron state mutated: `{report['cron_state_mutated']}`",
            f"Credentialed generation attempted: `{report['credentialed_generation_attempted']}`",
        ]) + "\n"
    )
    return {"json": str(json_path), "md": str(md_path), "sha256": artifact["sha256"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--receipt-ledger", default=None)
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = run_shadow(receipt_ledger=Path(args.receipt_ledger) if args.receipt_ledger else None)
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: cron_state_mutated={report['cron_state_mutated']}")
    return 0 if report["status"] == "RUNTIME_SHADOW_DRY_RUN_READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
