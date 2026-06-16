#!/usr/bin/env python3
"""Local-only dry-run planner for Workflow Kernel task closeout evidence.

Reads an existing task note plus the acceptance-lens receipt and emits a
structured, deterministic proposal for task-note/lifecycle closeout. This is a
preview only: it never mutates the task note, taskpool lifecycle, mission ledger,
adapters, remote repos, secrets, or external services.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASK = Path(
    "/Users/charles/Documents/brain/05-实践与项目/tasks/2026-06-15-吸收-agent-native-五层闭环为-hermes-workflow-kernel-架构验收镜头.md"
)
DEFAULT_RECEIPT = REPO_ROOT / "docs/private/workflow-kernel-acceptance-lens-dry-run/agent_native_five_layer_workflow_kernel_acceptance_lens_20260615.aclens_729a9cca12a1b820.acceptance_lens_dry_run.json"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs/private/workflow-kernel-task-closeout-dry-run"
REQUIRED_CHECKLIST_SNIPPETS = (
    "映射说明覆盖 Application Surface、Runtime Host、Prompt & Capability Stack、Agent Runtime、Tool & Artifact Loop",
    "Checklist 至少包含：Surface 不做特权操作、Host 不伪装模型、Agent 不拥有最终权限、Skill 不覆盖系统规则、Artifact 不只是临时附件",
    "Dry-run harness/example 有可读输出和 evidence ref",
    "Evidence bundle 回链 intake-note、contact sheet、transcript",
)
REQUIRED_RECEIPT_FLAGS = (
    "dry_run_only",
    "local_reversible_only",
)
FORBIDDEN_TRUE_RECEIPT_FLAGS = (
    "runtime_enabled",
    "write_through_enabled",
    "external_side_effects_enabled",
    "task_lifecycle_mutation_enabled",
    "model_call_enabled",
)


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _digest(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _checkbox_lines(task_text: str) -> list[str]:
    return re.findall(r"^- \[[ xX]\] .+$", task_text, flags=re.MULTILINE)


def build_closeout_dry_run(task_path: Path, receipt_path: Path) -> dict[str, Any]:
    if not task_path.exists():
        raise SystemExit(f"task note not found: {task_path}")
    if not receipt_path.exists():
        raise SystemExit(f"acceptance receipt not found: {receipt_path}")

    task_text = task_path.read_text(encoding="utf-8")
    receipt = _load_json(receipt_path)
    missing_task_snippets = [snippet for snippet in REQUIRED_CHECKLIST_SNIPPETS if snippet not in task_text]

    receipt_failures: list[str] = []
    for flag in REQUIRED_RECEIPT_FLAGS:
        if receipt.get(flag) is not True:
            receipt_failures.append(f"required_receipt_flag_not_true:{flag}")
    for flag in FORBIDDEN_TRUE_RECEIPT_FLAGS:
        if receipt.get(flag) is not False:
            receipt_failures.append(f"forbidden_receipt_flag_not_false:{flag}")
    if receipt.get("verdict") != "implementation_ready_not_runtime_enabled":
        receipt_failures.append("receipt_verdict_not_implementation_ready_not_runtime_enabled")
    if receipt.get("failures") != []:
        receipt_failures.append("receipt_has_failures")

    preview_lines = [
        "## Proposed closeout preview (dry-run only)",
        "- Proposed lifecycle target: keep `review` until human/host-authorized closeout write; do not auto-mark done.",
        f"- Acceptance-lens receipt: `{receipt_path}` sha256={_sha256_path(receipt_path)}",
        f"- Task note source: `{task_path}` sha256={_sha256_path(task_path)}",
        "- Evidence summary: five-layer mapping, reusable checklist, dry-run receipt, intake/contact-sheet/transcript refs all present.",
        "- Boundary: preview only; no task note mutation, no taskpool lifecycle mutation, no runtime enablement, no external side effects.",
    ]

    evidence_refs = [
        f"task:{task_path} sha256={_sha256_path(task_path)}",
        f"receipt:{receipt_path} sha256={_sha256_path(receipt_path)}",
    ]
    result_seed = {
        "task": str(task_path),
        "receipt": str(receipt_path),
        "missing_task_snippets": missing_task_snippets,
        "receipt_failures": receipt_failures,
        "preview_lines": preview_lines,
    }
    status = "ready_for_human_gated_lifecycle_update" if not missing_task_snippets and not receipt_failures else "blocked_by_missing_closeout_evidence"
    return {
        "schema": "workflow_kernel_task_closeout_dry_run_v1",
        "result_id": "wkclose_" + _digest(result_seed)[:16],
        "generated_at": _now_utc(),
        "task_path": str(task_path),
        "receipt_path": str(receipt_path),
        "task_status_observed": "review" if "status: review" in task_text else "unknown_or_not_review",
        "checkbox_count_observed": len(_checkbox_lines(task_text)),
        "missing_task_snippets": missing_task_snippets,
        "receipt_failures": receipt_failures,
        "proposed_closeout_lines": preview_lines,
        "evidence_refs": evidence_refs,
        "dry_run_only": True,
        "local_reversible_only": True,
        "task_note_mutated": False,
        "task_lifecycle_mutation_enabled": False,
        "external_side_effects_enabled": False,
        "runtime_enabled": False,
        "verdict": status,
        "human_intervention_required_before_write": True,
    }


def write_result(output_dir: Path, result: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{result['result_id']}.task_closeout_dry_run.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=Path, default=DEFAULT_TASK)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    result = build_closeout_dry_run(args.task, args.receipt)
    if args.write:
        result["written"] = str(write_result(args.output_dir, result))
    if args.as_json or not args.write:
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(result.get("written", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
