#!/usr/bin/env python3
"""Compile and optionally persist the Workflow Kernel fail-closed posture.

This is an activation/projection report, not an executor.  `fail_closed_enabled`
means the safe-default posture is explicitly on and observable while all broad
side-effect/apply gates remain closed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/private/workflow-kernel-fail-closed-posture"
INPUTS = {
    "runtime_readiness": ROOT / "docs/private/workflow-kernel-runtime-readiness-summary/workflow_kernel_runtime_readiness_summary_20260613_phase36.json",
    "broader_adapter_contract": ROOT / "docs/private/workflow-kernel-broader-adapter-operation-contract/workflow_kernel_broader_adapter_operation_contract_20260615.json",
    "remaining_gates_terminal_index": ROOT / "docs/private/workflow-kernel-remaining-gates-terminal-index/workflow_kernel_remaining_gates_terminal_index_20260615.json",
    "phase_g_optimization_backlog": ROOT / "docs/private/workflow-kernel-phase-g-optimization-backlog/workflow_kernel_phase_g_optimization_backlog_20260615.json",
    "publication_terminal_summary": ROOT / "docs/private/workflow-kernel-publication-governance-terminal-summary/workflow_kernel_publication_governance_terminal_summary_20260613_phase92.json",
}

FORBIDDEN_TRUE_FLAGS = (
    "external_side_effects_enabled",
    "unattended_dispatch_enabled",
    "publication_apply_enabled",
    "external_publication_enabled",
    "generic_adapter_write_through_enabled",
    "taskpool_mutated",
    "mission_mutated",
    "executor_enabled",
)


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text()) if path.exists() else None


def digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _nested_bool(data: dict[str, Any] | None, path: list[str], default: bool = False) -> bool:
    cur: Any = data or {}
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return bool(cur) if cur is not None else default


def build_report() -> dict[str, Any]:
    artifacts = {name: load(path) for name, path in INPUTS.items()}
    missing = [name for name, artifact in artifacts.items() if artifact is None]

    checks = {
        "runtime_stable_or_enabled": (artifacts["runtime_readiness"] or {}).get("status") in {
            "RUNTIME_READINESS_SUMMARY_AUTO_ENABLED",
            "RUNTIME_READINESS_SUMMARY_READY_PENDING_EXPLICIT_ENABLE",
        },
        "broader_adapter_fail_closed": (artifacts["broader_adapter_contract"] or {}).get("status") == "BROADER_ADAPTER_OPERATION_CONTRACT_READY"
        and _nested_bool(artifacts["broader_adapter_contract"], ["global_invariants", "no_generic_adapter_write_through"], False)
        and not _nested_bool(artifacts["broader_adapter_contract"], ["global_invariants", "external_side_effects_enabled"], True),
        "remaining_terminal_gates_ready": (artifacts["remaining_gates_terminal_index"] or {}).get("status") == "REMAINING_GATES_TERMINAL_INDEX_READY",
        "phase_g_candidate_only": (artifacts["phase_g_optimization_backlog"] or {}).get("status") == "PHASE_G_OPTIMIZATION_BACKLOG_READY"
        and (artifacts["phase_g_optimization_backlog"] or {}).get("checks", {}).get("candidate_only") is True,
        "publication_closed_no_apply": (artifacts["publication_terminal_summary"] or {}).get("status") == "PUBLICATION_GOVERNANCE_TERMINAL_SUMMARY_READY"
        and (artifacts["publication_terminal_summary"] or {}).get("publication_apply_enabled") is False,
        "sources_present": not missing,
    }

    forbidden_observations = {
        "runtime_readiness.external_side_effects_enabled": (artifacts["runtime_readiness"] or {}).get("external_side_effects_enabled"),
        "broader_adapter.global_external_side_effects_enabled": _nested_bool(
            artifacts["broader_adapter_contract"], ["global_invariants", "external_side_effects_enabled"], False
        ),
        "broader_adapter.global_unattended_dispatch_enabled": _nested_bool(
            artifacts["broader_adapter_contract"], ["global_invariants", "unattended_dispatch_enabled"], False
        ),
        "remaining.unattended_dispatch_enabled": (artifacts["remaining_gates_terminal_index"] or {}).get("unattended_dispatch_enabled"),
        "remaining.publication_apply_enabled": (artifacts["remaining_gates_terminal_index"] or {}).get("publication_apply_enabled"),
        "phase_g.taskpool_mutated": (artifacts["phase_g_optimization_backlog"] or {}).get("taskpool_mutated"),
        "phase_g.executor_enabled": (artifacts["phase_g_optimization_backlog"] or {}).get("executor_enabled"),
        "publication.publication_apply_enabled": (artifacts["publication_terminal_summary"] or {}).get("publication_apply_enabled"),
    }
    forbidden_enabled = [name for name, value in forbidden_observations.items() if value is True]

    enabled = all(checks.values()) and not forbidden_enabled
    report = {
        "schema": "workflow_kernel_fail_closed_posture_v1",
        "created_at": now(),
        "status": "FAIL_CLOSED_POSTURE_ENABLED" if enabled else "FAIL_CLOSED_POSTURE_DEGRADED",
        "posture_policy": "enable_fail_closed_defaults_without_opening_external_apply_or_unattended_paths",
        "fail_closed_enabled": enabled,
        "read_only": True,
        "checks": checks,
        "missing_sources": missing,
        "forbidden_true_flags": list(FORBIDDEN_TRUE_FLAGS),
        "forbidden_enabled_observations": forbidden_enabled,
        "observed_safe_defaults": {
            "external_side_effects_enabled": False,
            "unattended_dispatch_enabled": False,
            "publication_apply_enabled": False,
            "generic_adapter_write_through_enabled": False,
            "taskpool_enqueue_enabled": False,
            "executor_enabled": False,
        },
        "source_statuses": {name: (artifact or {}).get("status", "missing") for name, artifact in artifacts.items()},
        "artifact_digests": {
            name: hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
            for name, path in INPUTS.items()
        },
        "next_boundary": "fail-closed is active; any real send/publication/adapter apply/task enqueue still requires exact target, payload digest, idempotency, rollback, and human authorization",
    }
    report["posture_digest"] = digest({k: v for k, v in report.items() if k != "posture_digest"})
    return report


def write_report(report: dict[str, Any], out: Path = OUT) -> dict[str, str]:
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "workflow_kernel_fail_closed_posture_20260615.json"
    md_path = out / "workflow_kernel_fail_closed_posture_20260615.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    lines = [
        "# Workflow Kernel Fail-Closed Posture",
        "",
        f"Status: `{report['status']}`",
        f"Fail-closed enabled: `{str(report['fail_closed_enabled']).lower()}`",
        f"Digest: `{report['posture_digest']}`",
        "",
        "## Safe defaults",
    ]
    for key, value in report["observed_safe_defaults"].items():
        lines.append(f"- `{key}` = `{str(value).lower()}`")
    lines.extend(["", "## Next boundary", "", report["next_boundary"]])
    md_path.write_text("\n".join(lines) + "\n")
    return {"json": str(json_path), "markdown": str(md_path)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = build_report()
    if args.write_report:
        report["artifacts"] = write_report(report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) if args.json else report["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
