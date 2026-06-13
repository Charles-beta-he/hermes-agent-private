#!/usr/bin/env python3
"""Read-only validator for Hermes workflow contract seed registries.

Phase 1 is intentionally conservative: this script validates static contracts,
registry shape, and fail-closed flags. It does not claim, transition, execute,
write through adapters, or send external messages.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = REPO_ROOT / "config" / "workflow_registry.json"
DEFAULT_SCHEMA = REPO_ROOT / "schemas" / "workflow_contract.schema.json"
DEFAULT_MODEL_REGISTRY = REPO_ROOT / "config" / "model_registry.json"
DEFAULT_ROUTE_TRACE_SCHEMA = REPO_ROOT / "schemas" / "route_trace.schema.json"
DEFAULT_RUN_LOCKFILE_SCHEMA = REPO_ROOT / "schemas" / "run_lockfile.schema.json"
DEFAULT_ACCEPTANCE_BUNDLE_SCHEMA = REPO_ROOT / "schemas" / "acceptance_bundle.schema.json"
DEFAULT_EXECUTOR_DRY_RUN_SCHEMA = REPO_ROOT / "schemas" / "executor_dry_run_result.schema.json"
DEFAULT_TASKPOOL_IMPORT_DRY_RUN_SCHEMA = REPO_ROOT / "schemas" / "taskpool_import_dry_run.schema.json"
DEFAULT_TASKPOOL_IMPORT_WRITE_RESULT_SCHEMA = REPO_ROOT / "schemas" / "taskpool_import_write_result.schema.json"
DEFAULT_TASKPOOL_CANDIDATE_CONSOLIDATION_SCHEMA = REPO_ROOT / "schemas" / "taskpool_candidate_consolidation_result.schema.json"
DEFAULT_RUNTIME_INTEGRATION_PACKET_SCHEMA = REPO_ROOT / "schemas" / "runtime_integration_packet.schema.json"
DEFAULT_RUNTIME_CLAIM_EXECUTION_PACKET_SCHEMA = REPO_ROOT / "schemas" / "runtime_claim_execution_packet.schema.json"
DEFAULT_RUNTIME_EXECUTION_WORKER_PACKET_SCHEMA = REPO_ROOT / "schemas" / "runtime_execution_worker_packet.schema.json"
DEFAULT_RUNTIME_MODULE_EXECUTION_PACKET_SCHEMA = REPO_ROOT / "schemas" / "runtime_module_execution_packet.schema.json"
DEFAULT_RUNTIME_REVIEW_DONE_PACKET_SCHEMA = REPO_ROOT / "schemas" / "runtime_review_done_packet.schema.json"
DEFAULT_RUNTIME_MODEL_CALL_PACKET_SCHEMA = REPO_ROOT / "schemas" / "runtime_model_call_packet.schema.json"

ALLOWED_LANES = {"work", "evolution", "advisory"}
ALLOWED_STAGES = {
    "intake",
    "classify",
    "evidence",
    "plan",
    "dispatch",
    "execute",
    "verify",
    "review",
    "writeback",
    "human_gate",
    "done",
}
ALLOWED_RISK_LEVELS = {"low", "medium", "high", "critical"}
ALLOWED_SIDE_EFFECT_LEVELS = {
    "read_only",
    "local_reversible",
    "external_write",
    "destructive",
}
ALLOWED_RUN_MODES = {"pinned", "dynamic_safe_boundary", "shadow_only"}
ALLOWED_MODEL_STATUSES = {
    "experimental",
    "candidate",
    "candidate_shadow",
    "canary",
    "stable",
    "deprecated",
    "blocked",
}
ALLOWED_EVIDENCE_PREFIXES = {
    "command:",
    "artifact:",
    "proof:",
    "classification:",
    "file:",
    "not_applicable:",
}
REQUIRED_HUMAN_PACKET_FIELDS = {
    "decision_needed",
    "confirmed_facts",
    "unconfirmed_facts",
    "options",
    "recommended_default",
    "blast_radius",
    "rollback_plan",
    "post_action_verification",
}
PHASE1_DISABLED_FLAGS = (
    "runtime_enabled",
    "write_through_enabled",
    "external_side_effects_enabled",
)


@dataclass
class ValidationResult:
    status: str = "PASS"
    registry: str = ""
    schema: str = ""
    route_trace_schema: str = ""
    run_lockfile_schema: str = ""
    acceptance_bundle_schema: str = ""
    executor_dry_run_schema: str = ""
    taskpool_import_dry_run_schema: str = ""
    taskpool_import_write_result_schema: str = ""
    taskpool_candidate_consolidation_schema: str = ""
    runtime_integration_packet_schema: str = ""
    runtime_claim_execution_packet_schema: str = ""
    runtime_execution_worker_packet_schema: str = ""
    runtime_module_execution_packet_schema: str = ""
    runtime_review_done_packet_schema: str = ""
    runtime_model_call_packet_schema: str = ""
    model_registry: str = ""
    validated_workflows: int = 0
    validated_modules: int = 0
    validated_models: int = 0
    validated_route_aliases: int = 0
    runtime_enabled: bool = False
    write_through_enabled: bool = False
    external_side_effects_enabled: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.status = "FAIL"
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "registry": self.registry,
            "schema": self.schema,
            "route_trace_schema": self.route_trace_schema,
            "run_lockfile_schema": self.run_lockfile_schema,
            "acceptance_bundle_schema": self.acceptance_bundle_schema,
            "executor_dry_run_schema": self.executor_dry_run_schema,
            "taskpool_import_dry_run_schema": self.taskpool_import_dry_run_schema,
            "taskpool_import_write_result_schema": self.taskpool_import_write_result_schema,
            "taskpool_candidate_consolidation_schema": self.taskpool_candidate_consolidation_schema,
            "runtime_integration_packet_schema": self.runtime_integration_packet_schema,
            "runtime_claim_execution_packet_schema": self.runtime_claim_execution_packet_schema,
            "runtime_execution_worker_packet_schema": self.runtime_execution_worker_packet_schema,
            "runtime_module_execution_packet_schema": self.runtime_module_execution_packet_schema,
            "runtime_review_done_packet_schema": self.runtime_review_done_packet_schema,
            "runtime_model_call_packet_schema": self.runtime_model_call_packet_schema,
            "model_registry": self.model_registry,
            "validated_workflows": self.validated_workflows,
            "validated_modules": self.validated_modules,
            "validated_models": self.validated_models,
            "validated_route_aliases": self.validated_route_aliases,
            "runtime_enabled": self.runtime_enabled,
            "write_through_enabled": self.write_through_enabled,
            "external_side_effects_enabled": self.external_side_effects_enabled,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _load_json(path: Path, result: ValidationResult, label: str) -> Any | None:
    if not path.exists():
        result.fail(f"{label} missing: {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        result.fail(f"{label} invalid JSON: {path}: {exc}")
        return None


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_module(
    workflow_id: str,
    module: dict[str, Any],
    route_aliases: set[str],
    result: ValidationResult,
) -> None:
    module_id = module.get("module_id")
    prefix = f"workflow {workflow_id} module {module_id or '<missing>'}"
    if not _is_nonempty_string(module_id):
        result.fail(f"{prefix}: missing module_id")
    if not _is_nonempty_string(module.get("capability")):
        result.fail(f"{prefix}: missing capability")
    if module.get("risk_level") not in ALLOWED_RISK_LEVELS:
        result.fail(f"{prefix}: invalid risk_level {module.get('risk_level')!r}")
    if module.get("side_effect_level") not in ALLOWED_SIDE_EFFECT_LEVELS:
        result.fail(
            f"{prefix}: invalid side_effect_level {module.get('side_effect_level')!r}"
        )
    if module.get("side_effect_level") in {"external_write", "destructive"}:
        result.fail(f"{prefix}: Phase 1 forbids external/destructive side effects")
    if not isinstance(module.get("input_schema"), dict):
        result.fail(f"{prefix}: input_schema must be an object")
    if not isinstance(module.get("output_schema"), dict):
        result.fail(f"{prefix}: output_schema must be an object")

    model_policy = module.get("model_policy")
    if not isinstance(model_policy, dict):
        result.fail(f"{prefix}: model_policy must be an object")
    else:
        route_alias = model_policy.get("route_alias")
        if not isinstance(route_alias, str) or not route_alias.strip() or not route_alias.startswith("best."):
            result.fail(f"{prefix}: model_policy.route_alias must start with 'best.'")
        elif route_alias not in route_aliases:
            result.fail(f"{prefix}: model_policy.route_alias {route_alias!r} is not registered")
        if model_policy.get("run_mode") not in ALLOWED_RUN_MODES:
            result.fail(
                f"{prefix}: invalid model_policy.run_mode {model_policy.get('run_mode')!r}"
            )
        if model_policy.get("fallback_required") is not True:
            result.fail(f"{prefix}: model_policy.fallback_required must be true")

    evidence_required = module.get("evidence_required")
    if not isinstance(evidence_required, list) or not evidence_required:
        result.fail(f"{prefix}: evidence_required must be a non-empty list")
    else:
        for item in evidence_required:
            if not _is_nonempty_string(item):
                result.fail(f"{prefix}: evidence_required contains empty/non-string item")
    blocked_actions = module.get("blocked_actions")
    if not isinstance(blocked_actions, list) or not blocked_actions:
        result.fail(f"{prefix}: blocked_actions must be a non-empty list")
    result.validated_modules += 1


def _validate_workflow(
    workflow: dict[str, Any], route_aliases: set[str], result: ValidationResult
) -> None:
    workflow_id = workflow.get("workflow_id")
    prefix = f"workflow {workflow_id or '<missing>'}"
    if not _is_nonempty_string(workflow_id):
        result.fail(f"{prefix}: missing workflow_id")
    if not _is_nonempty_string(workflow.get("version")):
        result.fail(f"{prefix}: missing version")
    if not _is_nonempty_string(workflow.get("objective")):
        result.fail(f"{prefix}: missing objective")

    for flag in PHASE1_DISABLED_FLAGS:
        value = workflow.get(flag)
        if value is not False:
            result.fail(f"{prefix}: Phase 1 requires {flag}=false, got {value!r}")
        if value:
            setattr(result, flag, True)

    lanes = workflow.get("allowed_lanes")
    if not isinstance(lanes, list) or not lanes:
        result.fail(f"{prefix}: allowed_lanes must be a non-empty list")
    else:
        invalid = sorted(set(lanes) - ALLOWED_LANES)
        if invalid:
            result.fail(f"{prefix}: invalid lanes {invalid}")

    stages = workflow.get("stages")
    if not isinstance(stages, list) or not stages:
        result.fail(f"{prefix}: stages must be a non-empty list")
    else:
        invalid = sorted(set(stages) - ALLOWED_STAGES)
        if invalid:
            result.fail(f"{prefix}: invalid stages {invalid}")
        for required in ("intake", "verify", "human_gate"):
            if required not in stages:
                result.fail(f"{prefix}: missing required stage {required!r}")

    modules = workflow.get("modules")
    if not isinstance(modules, list) or not modules:
        result.fail(f"{prefix}: modules must be a non-empty list")
    else:
        seen_modules: set[str] = set()
        for module in modules:
            if not isinstance(module, dict):
                result.fail(f"{prefix}: module entry must be an object")
                continue
            module_id = module.get("module_id")
            if module_id in seen_modules:
                result.fail(f"{prefix}: duplicate module_id {module_id!r}")
            if isinstance(module_id, str):
                seen_modules.add(module_id)
            _validate_module(str(workflow_id), module, route_aliases, result)

    acceptance = workflow.get("acceptance_policy")
    if not isinstance(acceptance, dict):
        result.fail(f"{prefix}: acceptance_policy must be an object")
    else:
        if acceptance.get("compile_before_execution") is not True:
            result.fail(f"{prefix}: acceptance compile_before_execution must be true")
        if acceptance.get("freeze_before_execution") is not True:
            result.fail(f"{prefix}: acceptance freeze_before_execution must be true")
        checks = acceptance.get("required_checks")
        if not isinstance(checks, list) or not checks:
            result.fail(f"{prefix}: acceptance required_checks must be non-empty")

    human_gate = workflow.get("human_gate")
    if not isinstance(human_gate, dict):
        result.fail(f"{prefix}: human_gate must be an object")
    else:
        fields = set(human_gate.get("packet_required_fields") or [])
        missing = sorted(REQUIRED_HUMAN_PACKET_FIELDS - fields)
        if missing:
            result.fail(f"{prefix}: human_gate missing packet fields {missing}")

    evidence_policy = workflow.get("evidence_policy")
    if not isinstance(evidence_policy, dict):
        result.fail(f"{prefix}: evidence_policy must be an object")
    else:
        prefixes = set(evidence_policy.get("accepted_prefixes") or [])
        invalid_prefixes = sorted(prefixes - ALLOWED_EVIDENCE_PREFIXES)
        if invalid_prefixes:
            result.fail(f"{prefix}: invalid evidence prefixes {invalid_prefixes}")
        if evidence_policy.get("ledger_required") is not True:
            result.fail(f"{prefix}: evidence_policy.ledger_required must be true")
        if evidence_policy.get("raw_payload_policy") != "hash_or_pointer_only":
            result.fail(
                f"{prefix}: evidence raw_payload_policy must be hash_or_pointer_only"
            )

    result.validated_workflows += 1


def _validate_model_registry(model_registry: dict[str, Any], result: ValidationResult) -> set[str]:
    controls = model_registry.get("production_controls")
    if not isinstance(controls, dict):
        result.fail("model_registry.production_controls must be an object")
    else:
        if controls.get("default_run_mode") != "pinned":
            result.fail("model_registry requires production default_run_mode=pinned")
        if controls.get("dynamic_routing_enabled") is not False:
            result.fail("model_registry requires dynamic_routing_enabled=false in Phase 1")
        if controls.get("auto_promote_enabled") is not False:
            result.fail("model_registry requires auto_promote_enabled=false in Phase 1")
        if controls.get("default_model_change_requires_human_approval") is not True:
            result.fail("model_registry requires human approval for default model changes")

    models = model_registry.get("models")
    model_ids: set[str] = set()
    if not isinstance(models, list) or not models:
        result.fail("model_registry.models must be a non-empty list")
    else:
        for model in models:
            if not isinstance(model, dict):
                result.fail("model_registry model entry must be an object")
                continue
            model_id = model.get("model_id")
            prefix = f"model {model_id or '<missing>'}"
            if not _is_nonempty_string(model_id):
                result.fail(f"{prefix}: missing model_id")
            elif model_id in model_ids:
                result.fail(f"duplicate model_id {model_id!r}")
            if isinstance(model_id, str):
                model_ids.add(model_id)
            if model.get("status") not in ALLOWED_MODEL_STATUSES:
                result.fail(f"{prefix}: invalid status {model.get('status')!r}")
            if not isinstance(model.get("capabilities"), dict):
                result.fail(f"{prefix}: capabilities must be an object")
            if not isinstance(model.get("risk_profile"), dict):
                result.fail(f"{prefix}: risk_profile must be an object")
            eval_results = model.get("eval_results")
            if not isinstance(eval_results, list) or not eval_results:
                result.fail(f"{prefix}: eval_results must be non-empty")
            result.validated_models += 1

    aliases = model_registry.get("route_aliases")
    if not isinstance(aliases, dict) or not aliases:
        result.fail("model_registry.route_aliases must be a non-empty object")
    else:
        for alias, spec in aliases.items():
            prefix = f"route_alias {alias}"
            if not isinstance(alias, str) or not alias.startswith("best."):
                result.fail(f"{prefix}: alias must start with best.")
            if not isinstance(spec, dict):
                result.fail(f"{prefix}: spec must be an object")
                continue
            primary = spec.get("primary_model_id")
            if primary not in model_ids:
                result.fail(f"{prefix}: primary_model_id {primary!r} is not registered")
            fallbacks = spec.get("fallback_model_ids")
            if not isinstance(fallbacks, list) or not fallbacks:
                result.fail(f"{prefix}: fallback_model_ids must be non-empty")
            else:
                for fallback in fallbacks:
                    if fallback not in model_ids:
                        result.fail(f"{prefix}: fallback_model_id {fallback!r} is not registered")
            required_status = set(spec.get("required_status") or [])
            if "stable" not in required_status:
                result.fail(f"{prefix}: required_status must include stable in Phase 1")
            if spec.get("run_mode") != "pinned":
                result.fail(f"{prefix}: run_mode must be pinned in Phase 1")
            if not _is_nonempty_string(spec.get("eval_suite_id")):
                result.fail(f"{prefix}: eval_suite_id is required")
            result.validated_route_aliases += 1
    return set(aliases or {}) if isinstance(aliases, dict) else set()


def validate_registry(
    registry_path: Path,
    schema_path: Path = DEFAULT_SCHEMA,
    model_registry_path: Path = DEFAULT_MODEL_REGISTRY,
    route_trace_schema_path: Path = DEFAULT_ROUTE_TRACE_SCHEMA,
    run_lockfile_schema_path: Path = DEFAULT_RUN_LOCKFILE_SCHEMA,
    acceptance_bundle_schema_path: Path = DEFAULT_ACCEPTANCE_BUNDLE_SCHEMA,
    executor_dry_run_schema_path: Path = DEFAULT_EXECUTOR_DRY_RUN_SCHEMA,
    taskpool_import_dry_run_schema_path: Path = DEFAULT_TASKPOOL_IMPORT_DRY_RUN_SCHEMA,
    taskpool_import_write_result_schema_path: Path = DEFAULT_TASKPOOL_IMPORT_WRITE_RESULT_SCHEMA,
    taskpool_candidate_consolidation_schema_path: Path = DEFAULT_TASKPOOL_CANDIDATE_CONSOLIDATION_SCHEMA,
    runtime_integration_packet_schema_path: Path = DEFAULT_RUNTIME_INTEGRATION_PACKET_SCHEMA,
    runtime_claim_execution_packet_schema_path: Path = DEFAULT_RUNTIME_CLAIM_EXECUTION_PACKET_SCHEMA,
    runtime_execution_worker_packet_schema_path: Path = DEFAULT_RUNTIME_EXECUTION_WORKER_PACKET_SCHEMA,
    runtime_module_execution_packet_schema_path: Path = DEFAULT_RUNTIME_MODULE_EXECUTION_PACKET_SCHEMA,
    runtime_review_done_packet_schema_path: Path = DEFAULT_RUNTIME_REVIEW_DONE_PACKET_SCHEMA,
    runtime_model_call_packet_schema_path: Path = DEFAULT_RUNTIME_MODEL_CALL_PACKET_SCHEMA,
) -> ValidationResult:
    result = ValidationResult(
        registry=str(registry_path),
        schema=str(schema_path),
        route_trace_schema=str(route_trace_schema_path),
        run_lockfile_schema=str(run_lockfile_schema_path),
        acceptance_bundle_schema=str(acceptance_bundle_schema_path),
        executor_dry_run_schema=str(executor_dry_run_schema_path),
        taskpool_import_dry_run_schema=str(taskpool_import_dry_run_schema_path),
        taskpool_import_write_result_schema=str(taskpool_import_write_result_schema_path),
        taskpool_candidate_consolidation_schema=str(taskpool_candidate_consolidation_schema_path),
        runtime_integration_packet_schema=str(runtime_integration_packet_schema_path),
        runtime_claim_execution_packet_schema=str(runtime_claim_execution_packet_schema_path),
        runtime_execution_worker_packet_schema=str(runtime_execution_worker_packet_schema_path),
        runtime_module_execution_packet_schema=str(runtime_module_execution_packet_schema_path),
        runtime_review_done_packet_schema=str(runtime_review_done_packet_schema_path),
        runtime_model_call_packet_schema=str(runtime_model_call_packet_schema_path),
        model_registry=str(model_registry_path),
    )
    schema = _load_json(schema_path, result, "schema")
    route_trace_schema = _load_json(route_trace_schema_path, result, "route_trace_schema")
    run_lockfile_schema = _load_json(run_lockfile_schema_path, result, "run_lockfile_schema")
    acceptance_bundle_schema = _load_json(
        acceptance_bundle_schema_path, result, "acceptance_bundle_schema"
    )
    executor_dry_run_schema = _load_json(
        executor_dry_run_schema_path, result, "executor_dry_run_schema"
    )
    taskpool_import_dry_run_schema = _load_json(
        taskpool_import_dry_run_schema_path, result, "taskpool_import_dry_run_schema"
    )
    taskpool_import_write_result_schema = _load_json(
        taskpool_import_write_result_schema_path, result, "taskpool_import_write_result_schema"
    )
    taskpool_candidate_consolidation_schema = _load_json(
        taskpool_candidate_consolidation_schema_path, result, "taskpool_candidate_consolidation_schema"
    )
    runtime_integration_packet_schema = _load_json(
        runtime_integration_packet_schema_path, result, "runtime_integration_packet_schema"
    )
    runtime_claim_execution_packet_schema = _load_json(
        runtime_claim_execution_packet_schema_path, result, "runtime_claim_execution_packet_schema"
    )
    runtime_execution_worker_packet_schema = _load_json(
        runtime_execution_worker_packet_schema_path, result, "runtime_execution_worker_packet_schema"
    )
    runtime_module_execution_packet_schema = _load_json(
        runtime_module_execution_packet_schema_path, result, "runtime_module_execution_packet_schema"
    )
    runtime_review_done_packet_schema = _load_json(
        runtime_review_done_packet_schema_path, result, "runtime_review_done_packet_schema"
    )
    runtime_model_call_packet_schema = _load_json(
        runtime_model_call_packet_schema_path, result, "runtime_model_call_packet_schema"
    )
    registry = _load_json(registry_path, result, "registry")
    model_registry = _load_json(model_registry_path, result, "model_registry")
    if (
        schema is None
        or route_trace_schema is None
        or run_lockfile_schema is None
        or acceptance_bundle_schema is None
        or executor_dry_run_schema is None
        or taskpool_import_dry_run_schema is None
        or taskpool_import_write_result_schema is None
        or taskpool_candidate_consolidation_schema is None
        or runtime_integration_packet_schema is None
        or runtime_claim_execution_packet_schema is None
        or runtime_execution_worker_packet_schema is None
        or runtime_module_execution_packet_schema is None
        or runtime_review_done_packet_schema is None
        or runtime_model_call_packet_schema is None
        or registry is None
        or model_registry is None
    ):
        return result
    route_aliases = _validate_model_registry(model_registry, result)
    if schema.get("title") != "Hermes Self-Advancing Workflow Contract":
        result.warn("schema title is unexpected; validator uses built-in Phase 1 checks")
    if route_trace_schema.get("title") != "Hermes Workflow Model Route Trace":
        result.fail("route_trace_schema title is unexpected")
    if run_lockfile_schema.get("title") != "Hermes Workflow Run Lockfile":
        result.fail("run_lockfile_schema title is unexpected")
    if acceptance_bundle_schema.get("title") != "Hermes Workflow Acceptance Bundle":
        result.fail("acceptance_bundle_schema title is unexpected")
    if executor_dry_run_schema.get("title") != "Hermes Workflow Executor Dry-Run Result":
        result.fail("executor_dry_run_schema title is unexpected")
    if taskpool_import_dry_run_schema.get("title") != "Hermes Workflow Taskpool Import Dry-Run":
        result.fail("taskpool_import_dry_run_schema title is unexpected")
    if taskpool_import_write_result_schema.get("title") != "Hermes Workflow Taskpool Import Write Result":
        result.fail("taskpool_import_write_result_schema title is unexpected")
    if taskpool_candidate_consolidation_schema.get("title") != "Hermes Workflow Taskpool Candidate Consolidation Result":
        result.fail("taskpool_candidate_consolidation_schema title is unexpected")
    if runtime_integration_packet_schema.get("title") != "Hermes Workflow Runtime Integration Packet":
        result.fail("runtime_integration_packet_schema title is unexpected")
    if runtime_claim_execution_packet_schema.get("title") != "Hermes Workflow Runtime Claim Execution Packet":
        result.fail("runtime_claim_execution_packet_schema title is unexpected")
    if runtime_execution_worker_packet_schema.get("title") != "Hermes Workflow Runtime Execution Worker Packet":
        result.fail("runtime_execution_worker_packet_schema title is unexpected")
    if runtime_module_execution_packet_schema.get("title") != "Hermes Workflow Runtime Module Execution Packet":
        result.fail("runtime_module_execution_packet_schema title is unexpected")
    if runtime_review_done_packet_schema.get("title") != "Hermes Workflow Runtime Review Done Packet":
        result.fail("runtime_review_done_packet_schema title is unexpected")
    if runtime_model_call_packet_schema.get("title") != "Hermes Workflow Runtime Model Call Packet":
        result.fail("runtime_model_call_packet_schema title is unexpected")

    workflows = registry.get("workflows") if isinstance(registry, dict) else None
    if not isinstance(workflows, list) or not workflows:
        result.fail("registry.workflows must be a non-empty list")
        return result

    defaults = registry.get("defaults", {})
    if isinstance(defaults, dict):
        for flag in PHASE1_DISABLED_FLAGS:
            if defaults.get(flag) is not False:
                result.fail(f"registry.defaults requires {flag}=false")
        if defaults.get("dynamic_routing_enabled") is not False:
            result.fail("registry.defaults requires dynamic_routing_enabled=false")
        if defaults.get("auto_promote_enabled") is not False:
            result.fail("registry.defaults requires auto_promote_enabled=false")
    else:
        result.fail("registry.defaults must be an object")

    seen_workflows: set[str] = set()
    for workflow in workflows:
        if not isinstance(workflow, dict):
            result.fail("workflow entry must be an object")
            continue
        workflow_id = workflow.get("workflow_id")
        if workflow_id in seen_workflows:
            result.fail(f"duplicate workflow_id {workflow_id!r}")
        if isinstance(workflow_id, str):
            seen_workflows.add(workflow_id)
        _validate_workflow(workflow, route_aliases, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--model-registry", type=Path, default=DEFAULT_MODEL_REGISTRY)
    parser.add_argument("--route-trace-schema", type=Path, default=DEFAULT_ROUTE_TRACE_SCHEMA)
    parser.add_argument("--run-lockfile-schema", type=Path, default=DEFAULT_RUN_LOCKFILE_SCHEMA)
    parser.add_argument("--acceptance-bundle-schema", type=Path, default=DEFAULT_ACCEPTANCE_BUNDLE_SCHEMA)
    parser.add_argument("--executor-dry-run-schema", type=Path, default=DEFAULT_EXECUTOR_DRY_RUN_SCHEMA)
    parser.add_argument("--taskpool-import-dry-run-schema", type=Path, default=DEFAULT_TASKPOOL_IMPORT_DRY_RUN_SCHEMA)
    parser.add_argument("--taskpool-import-write-result-schema", type=Path, default=DEFAULT_TASKPOOL_IMPORT_WRITE_RESULT_SCHEMA)
    parser.add_argument("--taskpool-candidate-consolidation-schema", type=Path, default=DEFAULT_TASKPOOL_CANDIDATE_CONSOLIDATION_SCHEMA)
    parser.add_argument("--runtime-integration-packet-schema", type=Path, default=DEFAULT_RUNTIME_INTEGRATION_PACKET_SCHEMA)
    parser.add_argument("--runtime-claim-execution-packet-schema", type=Path, default=DEFAULT_RUNTIME_CLAIM_EXECUTION_PACKET_SCHEMA)
    parser.add_argument("--runtime-execution-worker-packet-schema", type=Path, default=DEFAULT_RUNTIME_EXECUTION_WORKER_PACKET_SCHEMA)
    parser.add_argument("--runtime-module-execution-packet-schema", type=Path, default=DEFAULT_RUNTIME_MODULE_EXECUTION_PACKET_SCHEMA)
    parser.add_argument("--runtime-review-done-packet-schema", type=Path, default=DEFAULT_RUNTIME_REVIEW_DONE_PACKET_SCHEMA)
    parser.add_argument("--runtime-model-call-packet-schema", type=Path, default=DEFAULT_RUNTIME_MODEL_CALL_PACKET_SCHEMA)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    result = validate_registry(
        args.registry,
        args.schema,
        args.model_registry,
        args.route_trace_schema,
        args.run_lockfile_schema,
        args.acceptance_bundle_schema,
        args.executor_dry_run_schema,
        args.taskpool_import_dry_run_schema,
        args.taskpool_import_write_result_schema,
        args.taskpool_candidate_consolidation_schema,
        args.runtime_integration_packet_schema,
        args.runtime_claim_execution_packet_schema,
        args.runtime_execution_worker_packet_schema,
        args.runtime_module_execution_packet_schema,
        args.runtime_review_done_packet_schema,
        args.runtime_model_call_packet_schema,
    )
    if args.as_json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"status={result.status}")
        print(f"validated_workflows={result.validated_workflows}")
        print(f"validated_modules={result.validated_modules}")
        for error in result.errors:
            print(f"ERROR: {error}")
        for warning in result.warnings:
            print(f"WARN: {warning}")
    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
