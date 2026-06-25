#!/usr/bin/env python3
"""Validate a Hermes ActionContract and emit a deterministic dry-run receipt.

This is a local-only ActionContract tranche inspired by BuilderIO's
`defineAction` shape, but it intentionally does not import BuilderIO packages,
open MCP/A2A/HTTP executors, mutate taskpool state, or perform external side
effects. It validates the manifest boundary and produces a stable receipt that
can be stored as evidence before any future runtime integration is considered.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "private" / "action-contract-dry-runs"
ACCEPTED_EVIDENCE_PREFIXES = ("command:", "artifact:", "proof:", "file:", "url:", "brainctl:")
REQUIRED_FIELDS = (
    "action_id",
    "version",
    "callable_surfaces",
    "authority_tier",
    "idempotency_key",
    "input_schema",
    "output_schema",
    "evidence_refs",
    "rollback_plan",
    "compensation_path",
    "external_side_effects",
)
FORBIDDEN_PACKAGE_MARKERS = ("@agent-native/core", "agent-native", "BuilderIO/agent-native")


def _canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(data: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(data).encode("utf-8")).hexdigest()


def example_contract() -> dict[str, Any]:
    return {
        "action_id": "taskpool.action_contract.validate_only",
        "version": "1.0.0",
        "callable_surfaces": ["cli", "agent", "dashboard"],
        "authority_tier": "budget-handoff",
        "idempotency_key": "action-contract-validate-only-v1",
        "input_schema": {
            "type": "object",
            "required": ["contract_path"],
            "properties": {"contract_path": {"type": "string"}},
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "required": ["status", "receipt_id", "external_side_effects"],
            "properties": {
                "status": {"enum": ["PASS", "FAIL"]},
                "receipt_id": {"type": "string"},
                "external_side_effects": {"const": False},
            },
            "additionalProperties": True,
        },
        "evidence_refs": [
            "artifact:/Users/charles/.hermes/docs/private/builderio-agent-native-net-new-recertification-20260623.md",
            "file:schemas/action_contract.schema.json",
            "file:scripts/action_contract_dry_run.py",
            "proof:external_side_effects=false no BuilderIO package install no runtime executor enabled",
        ],
        "rollback_plan": "Delete the local dry-run receipt artifact; no runtime state or external service was changed.",
        "compensation_path": "If future runtime integration is proposed, require a separate human-gated adapter authorization packet.",
        "external_side_effects": False,
        "runtime_executor_enabled": False,
        "package_imports": [],
    }


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_contract(contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in REQUIRED_FIELDS:
        if field not in contract:
            errors.append(f"missing_required_field:{field}")

    if contract.get("external_side_effects") is not False:
        errors.append("external_side_effects_must_be_false")
    if contract.get("runtime_executor_enabled") not in (None, False):
        errors.append("runtime_executor_enabled_must_be_false")

    authority = contract.get("authority_tier")
    if not _has_text(authority):
        errors.append("missing_authority_tier")
    elif authority in {"human-gated-external", "forbidden"}:
        errors.append(f"authority_tier_not_dry_run_safe:{authority}")

    surfaces = contract.get("callable_surfaces")
    if not isinstance(surfaces, list) or not surfaces:
        errors.append("missing_callable_surfaces")
    elif any(surface in {"http", "mcp", "a2a"} for surface in surfaces):
        errors.append("future_external_surfaces_must_not_be_enabled_in_dry_run")

    evidence_refs = contract.get("evidence_refs")
    if not isinstance(evidence_refs, list) or not evidence_refs:
        errors.append("missing_evidence_refs")
    else:
        for ref in evidence_refs:
            if not isinstance(ref, str) or not ref.startswith(ACCEPTED_EVIDENCE_PREFIXES):
                errors.append(f"invalid_evidence_ref:{ref}")

    if not _has_text(contract.get("rollback_plan")):
        errors.append("missing_rollback_plan")
    if not _has_text(contract.get("compensation_path")):
        errors.append("missing_compensation_path")
    if not _has_text(contract.get("idempotency_key")):
        errors.append("missing_idempotency_key")
    if not isinstance(contract.get("input_schema"), dict):
        errors.append("input_schema_must_be_object")
    if not isinstance(contract.get("output_schema"), dict):
        errors.append("output_schema_must_be_object")

    package_imports = contract.get("package_imports", [])
    if not isinstance(package_imports, list):
        errors.append("package_imports_must_be_list")
    else:
        for package in package_imports:
            text = str(package)
            if any(marker.lower() in text.lower() for marker in FORBIDDEN_PACKAGE_MARKERS):
                errors.append(f"forbidden_external_package_import:{text}")

    return errors


def build_receipt(contract: dict[str, Any]) -> dict[str, Any]:
    errors = validate_contract(contract)
    contract_digest = digest(contract)
    receipt_seed = {
        "schema": "action_contract_dry_run_receipt_v1",
        "contract_digest": contract_digest,
        "action_id": contract.get("action_id"),
        "idempotency_key": contract.get("idempotency_key"),
    }
    return {
        "schema": "action_contract_dry_run_receipt_v1",
        "status": "PASS" if not errors else "FAIL",
        "receipt_id": "acdry_" + digest(receipt_seed).split(":", 1)[1][:16],
        "contract_digest": contract_digest,
        "action_id": contract.get("action_id", ""),
        "callable_surfaces": contract.get("callable_surfaces", []),
        "authority_tier": contract.get("authority_tier", ""),
        "idempotency_key": contract.get("idempotency_key", ""),
        "dry_run_only": True,
        "runtime_executor_enabled": False,
        "write_through_enabled": False,
        "mcp_enabled": False,
        "a2a_enabled": False,
        "external_side_effects": False,
        "builderio_package_installed": False,
        "errors": errors,
        "evidence_refs": contract.get("evidence_refs", []),
        "rollback_plan": contract.get("rollback_plan", ""),
        "compensation_path": contract.get("compensation_path", ""),
        "boundary": "local_action_contract_validate_only_no_runtime_no_external_side_effects",
    }


def load_contract(path: Path | None) -> dict[str, Any]:
    if path is None:
        return example_contract()
    return json.loads(path.read_text(encoding="utf-8"))


def write_receipt(output_dir: Path, receipt: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    action_id = str(receipt.get("action_id") or "action_contract").replace("/", "_")
    path = output_dir / f"{action_id}.{receipt['receipt_id']}.action_contract_dry_run.json"
    path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, help="Path to an ActionContract JSON manifest. Defaults to a built-in Hermes dry-run example.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--write", action="store_true", help="Write the deterministic receipt to a local docs/private artifact.")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    receipt = build_receipt(load_contract(args.contract))
    if args.write:
        receipt["written"] = str(write_receipt(args.output_dir, receipt))
    if args.as_json or not args.write:
        print(json.dumps(receipt, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        print(receipt.get("written", ""))
    return 0 if receipt["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
