#!/usr/bin/env python3
"""Validate the workflow-kernel phase-1 static registry.

The command is deliberately deterministic and read-only by default.  It validates
contract metadata and can optionally write the validation report as a local
artifact for taskpool evidence.  It never executes workflow modules, calls
models, writes adapters, or performs external side effects.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from workflow_kernel.io import write_json_artifact  # noqa: E402
from workflow_kernel.static_registry import (  # noqa: E402
    EVIDENCE_PREFIXES,
    HUMAN_GATE_PACKET_FIELDS,
    IMPORT_GUARDS,
    MODULE_NAMES,
    PHASE,
    WORKFLOW_CONTRACT,
    registry_snapshot,
)

DEFAULT_REPORT = (
    REPO_ROOT
    / "docs"
    / "private"
    / "workflow-kernel-static-registry-validator"
    / "workflow_kernel_static_registry_validation_20260614.json"
)


def _add(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def validate_registry(registry: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    _add(errors, registry.get("workflow_contract") == WORKFLOW_CONTRACT, "workflow_contract mismatch")
    _add(errors, registry.get("phase") == PHASE, "phase mismatch")

    flags = registry.get("safety_flags") or {}
    for flag in (
        "runtime_enabled",
        "write_through_enabled",
        "external_side_effects_enabled",
        "model_calls_enabled",
        "adapter_writes_enabled",
    ):
        _add(errors, flags.get(flag) is False, f"safety flag must stay false: {flag}")

    import_guards = registry.get("import_guards") or {}
    for guard in IMPORT_GUARDS:
        _add(errors, import_guards.get(guard) is True, f"missing/enabled import guard: {guard}")

    prefixes = registry.get("evidence_prefixes") or []
    for prefix in EVIDENCE_PREFIXES:
        _add(errors, prefix in prefixes, f"missing evidence prefix: {prefix}")

    human_gate_fields = registry.get("human_gate_packet_required_fields") or []
    for field in HUMAN_GATE_PACKET_FIELDS:
        _add(errors, field in human_gate_fields, f"missing human gate packet field: {field}")

    modules = registry.get("modules") or []
    module_by_name = {m.get("name"): m for m in modules if isinstance(m, dict)}
    routes = registry.get("model_routes") or []
    routes_by_alias = {r.get("alias"): r for r in routes if isinstance(r, dict)}

    for module_name in MODULE_NAMES:
        module = module_by_name.get(module_name)
        _add(errors, module is not None, f"missing module contract: {module_name}")
        if not module:
            continue
        route_alias = f"model_route:{module_name}"
        _add(errors, module.get("contract_id") == f"module_contract:{module_name}", f"bad contract_id: {module_name}")
        _add(errors, module.get("evidence_required") is True, f"module evidence_required must be true: {module_name}")
        _add(errors, bool(module.get("model_policy")), f"module missing model_policy: {module_name}")
        _add(errors, module.get("route_alias") == route_alias, f"module route_alias mismatch: {module_name}")
        _add(errors, module.get("runtime_allowed") is False, f"module runtime_allowed must be false: {module_name}")
        _add(errors, module.get("adapter_write_allowed") is False, f"module adapter_write_allowed must be false: {module_name}")
        _add(
            errors,
            module.get("external_side_effects_allowed") is False,
            f"module external_side_effects_allowed must be false: {module_name}",
        )
        route = routes_by_alias.get(route_alias)
        _add(errors, route is not None, f"missing model route alias: {route_alias}")
        if route:
            _add(errors, route.get("runtime_allowed") is False, f"route runtime_allowed must be false: {route_alias}")
            _add(errors, route.get("provider") == "human_gate_required", f"route provider must defer to human gate: {route_alias}")

    expected_modules = set(MODULE_NAMES)
    unexpected_modules = sorted(str(name) for name in module_by_name if name and name not in expected_modules)
    if unexpected_modules:
        warnings.append(f"unexpected module contracts present: {unexpected_modules}")

    return {
        "schema": "workflow_kernel_static_registry_validation_v1",
        "status": "PASS" if not errors else "FAIL",
        "read_only": True,
        "runtime_enabled": bool(flags.get("runtime_enabled")),
        "write_through_enabled": bool(flags.get("write_through_enabled")),
        "external_side_effects_enabled": bool(flags.get("external_side_effects_enabled")),
        "checked": {
            "module_count": len(modules),
            "model_route_count": len(routes),
            "import_guard_count": len(import_guards),
            "evidence_prefix_count": len(prefixes),
            "human_gate_packet_field_count": len(human_gate_fields),
        },
        "errors": errors,
        "warnings": warnings,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-report", action="store_true", help="write deterministic JSON report artifact")
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT, help="report path used with --write-report")
    parser.add_argument("--json", action="store_true", help="print compact JSON instead of human text")
    args = parser.parse_args(argv)

    report = validate_registry(registry_snapshot())
    if args.write_report:
        artifact = write_json_artifact(args.out, report)
        report = {**report, "artifact": artifact}

    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    else:
        print(f"workflow_kernel_static_registry_validation: {report['status']}")
        if args.write_report and "artifact" in report:
            print(f"artifact: {report['artifact']['path']}")
            print(f"sha256: {report['artifact']['sha256']}")
        for error in report["errors"]:
            print(f"ERROR: {error}")
        for warning in report["warnings"]:
            print(f"WARN: {warning}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
