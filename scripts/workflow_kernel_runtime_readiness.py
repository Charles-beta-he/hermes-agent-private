#!/usr/bin/env python3
"""Compile workflow-kernel runtime switch readiness.

This CLI writes readiness evidence only. It does not enable the runtime switch,
mutate cron, enable autorun, migrate config, or expand external dispatch.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from workflow_kernel.io import write_json_artifact
from workflow_kernel.ledger import append_event
from workflow_kernel.runtime import compile_runtime_readiness

DEFAULT_OUT = Path("docs/private/workflow-kernel-runtime-readiness")
DEFAULT_LEDGER = DEFAULT_OUT / "workflow_kernel_runtime_readiness_events.jsonl"


def write_report(report: dict, out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "workflow_kernel_runtime_readiness_20260613_phase27.json"
    md_path = out_dir / "workflow_kernel_runtime_readiness_20260613_phase27.md"
    artifact = write_json_artifact(json_path, report)
    lines = [
        "# Workflow Kernel Runtime Readiness — Phase 27",
        "",
        f"Status: **{report['status']}**",
        f"Switch mode: `{report['switch_mode']}`",
        f"Runtime switch enabled by default: `{report['runtime_switch_enabled_by_default']}`",
        f"Explicit enable required: `{report['explicit_enable_required']}`",
        "",
        "## Compatibility CLIs",
        *[f"- `{x}`" for x in report["compatibility_clis"]],
        "",
        "## Required Tests",
        *[f"- `{x}`" for x in report["required_tests"]],
        "",
        "## Blocked Capabilities",
        *[f"- {x}" for x in report["blocked_capabilities"]],
        "",
        "## Next Closed Boundary",
        report["next_closed_boundary"],
    ]
    md_path.write_text("\n".join(lines) + "\n")
    append_event(DEFAULT_LEDGER if out_dir == DEFAULT_OUT else out_dir / "workflow_kernel_runtime_readiness_events.jsonl", event_type="runtime_readiness_compiled", payload={"status": report["status"], "json": str(json_path), "sha256": artifact["sha256"]}, idempotency_key=report["report_id"])
    return {"json": str(json_path), "md": str(md_path), "sha256": artifact["sha256"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    report = compile_runtime_readiness()
    if args.write_report:
        report["artifacts"] = write_report(report, Path(args.out_dir))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"{report['status']}: switch_mode={report['switch_mode']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
