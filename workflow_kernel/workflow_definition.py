"""Deterministic, authority-neutral WorkflowDefinitionV1 compiler.

The TypeScript contract package owns the authored schemas.  This module consumes
packaged generated JSON Schemas, applies semantic checks that JSON Schema cannot
express, and emits a non-executable WorkflowExecutionPlanV1 projection.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from itertools import combinations
from typing import Any, Mapping, NoReturn

import jsonschema


_SIDE_EFFECT_RANK = {
    "none": 0,
    "local_read": 1,
    "local_write": 2,
    "external_read": 3,
    "external_write": 4,
}
_TERMINAL_STOPS = {
    "human_gate",
    "terminal_failure",
    "cancelled",
    "context_exhausted",
    "stale",
}
_REQUIRED_HANDOFF_FIELDS = {
    "goal",
    "task_states",
    "artifacts",
    "evidence",
    "blockers",
    "next_step",
    "authority_state",
}


@dataclass(frozen=True, slots=True)
class WorkflowDefinitionError(ValueError):
    """Stable fail-closed error raised by the workflow definition compiler."""

    code: str
    message: str
    path: str = "$"

    def __str__(self) -> str:
        return f"{self.code} at {self.path}: {self.message}"


def _fail(code: str, message: str, path: str = "$") -> NoReturn:
    raise WorkflowDefinitionError(code=code, message=message, path=path)


@lru_cache(maxsize=None)
def _schema_validator(schema_name: str) -> jsonschema.Draft202012Validator:
    schema_text = (
        resources.files("workflow_kernel.schemas")
        .joinpath(schema_name)
        .read_text(encoding="utf-8")
    )
    schema = json.loads(schema_text)
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def _validate_schema(value: Any, schema_name: str) -> None:
    errors = sorted(
        _schema_validator(schema_name).iter_errors(value),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if not errors:
        return
    error = errors[0]
    path = "$" + "".join(
        f"[{part}]" if isinstance(part, int) else f".{part}"
        for part in error.absolute_path
    )
    _fail("schema_invalid", error.message, path)


def _reject_duplicates(values: list[str], path: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            _fail("duplicate_list_value", f"duplicate value {value!r}", path)
        seen.add(value)


def _normalize_json_numbers(value: Any) -> Any:
    """Match JSON/JavaScript's single numeric representation before hashing."""

    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [_normalize_json_numbers(item) for item in value]
    if isinstance(value, Mapping):
        return {
            key: _normalize_json_numbers(item)
            for key, item in value.items()
        }
    return value


def _canonical_digest(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        _normalize_json_numbers(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _topological_waves(
    task_ids: set[str], dependencies: Mapping[str, set[str]]
) -> list[list[str]]:
    remaining = {task_id: set(dependencies[task_id]) for task_id in task_ids}
    waves: list[list[str]] = []

    while remaining:
        ready = sorted(
            task_id for task_id, task_dependencies in remaining.items() if not task_dependencies
        )
        if not ready:
            cycle_nodes = ", ".join(sorted(remaining))
            _fail(
                "dependency_cycle",
                f"dependency graph contains a cycle among: {cycle_nodes}",
                "$.tasks",
            )
        waves.append(ready)
        ready_set = set(ready)
        remaining = {
            task_id: task_dependencies - ready_set
            for task_id, task_dependencies in remaining.items()
            if task_id not in ready_set
        }

    return waves


def _ancestor_sets(
    task_ids: set[str], dependencies: Mapping[str, set[str]]
) -> dict[str, set[str]]:
    ancestors = {task_id: set(dependencies[task_id]) for task_id in task_ids}
    changed = True
    while changed:
        changed = False
        for task_id in sorted(task_ids):
            expanded = set(ancestors[task_id])
            for dependency in tuple(ancestors[task_id]):
                expanded.update(ancestors[dependency])
            if expanded != ancestors[task_id]:
                ancestors[task_id] = expanded
                changed = True
    return ancestors


def compile_workflow_definition_v1(
    definition: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and compile a WorkflowDefinitionV1 into a non-executable plan.

    Validation and compilation are pure: no file writes, tool calls, model calls,
    adapter calls, runtime registration, or authority grants occur here.
    """

    _validate_schema(definition, "workflow-definition-v1.schema.json")
    normalized_definition = _normalize_json_numbers(definition)
    assert isinstance(normalized_definition, Mapping)
    definition = normalized_definition

    tasks_raw = definition["tasks"]
    assert isinstance(tasks_raw, list)
    tasks: list[dict[str, Any]] = tasks_raw

    task_by_id: dict[str, dict[str, Any]] = {}
    acceptance_ids: set[str] = set()
    for index, task in enumerate(tasks):
        task_id = task["id"]
        if task_id in task_by_id:
            _fail(
                "duplicate_task_id",
                f"duplicate task id {task_id!r}",
                f"$.tasks[{index}].id",
            )
        task_by_id[task_id] = task

        for field in ("depends_on", "inputs", "outputs", "writes", "verifies"):
            _reject_duplicates(task[field], f"$.tasks[{index}].{field}")
        _reject_duplicates(
            task["authority"]["capabilities"],
            f"$.tasks[{index}].authority.capabilities",
        )
        for acceptance_index, acceptance in enumerate(task["acceptance"]):
            acceptance_id = acceptance["id"]
            if acceptance_id in acceptance_ids:
                _fail(
                    "duplicate_acceptance_id",
                    f"duplicate acceptance id {acceptance_id!r}",
                    f"$.tasks[{index}].acceptance[{acceptance_index}].id",
                )
            acceptance_ids.add(acceptance_id)
            _reject_duplicates(
                acceptance["evidence_required"],
                f"$.tasks[{index}].acceptance[{acceptance_index}].evidence_required",
            )

    _reject_duplicates(definition["non_goals"], "$.non_goals")
    _reject_duplicates(definition["constraints"], "$.constraints")
    _reject_duplicates(
        definition["authority"]["allowed_capabilities"],
        "$.authority.allowed_capabilities",
    )
    _reject_duplicates(definition["stop_conditions"], "$.stop_conditions")
    _reject_duplicates(
        definition["handoff"]["required_fields"],
        "$.handoff.required_fields",
    )

    task_ids = set(task_by_id)
    dependencies: dict[str, set[str]] = {}
    for task_id, task in task_by_id.items():
        unknown = set(task["depends_on"]) - task_ids
        if unknown:
            _fail(
                "unknown_dependency",
                f"unknown dependencies: {', '.join(sorted(unknown))}",
                f"$.tasks[{task_id}].depends_on",
            )
        if task_id in task["depends_on"]:
            _fail(
                "dependency_cycle",
                "task cannot depend on itself",
                f"$.tasks[{task_id}].depends_on",
            )
        dependencies[task_id] = set(task["depends_on"])

    waves = _topological_waves(task_ids, dependencies)
    ancestors = _ancestor_sets(task_ids, dependencies)

    max_side_effect = definition["authority"]["max_side_effect"]
    max_rank = _SIDE_EFFECT_RANK[max_side_effect]
    allowed_capabilities = set(definition["authority"]["allowed_capabilities"])
    for task_id, task in task_by_id.items():
        task_authority = task["authority"]
        if _SIDE_EFFECT_RANK[task_authority["side_effect"]] > max_rank:
            _fail(
                "authority_escalation",
                f"task side effect {task_authority['side_effect']!r} exceeds workflow maximum {max_side_effect!r}",
                f"$.tasks[{task_id}].authority.side_effect",
            )
        extra_capabilities = set(task_authority["capabilities"]) - allowed_capabilities
        if extra_capabilities:
            _fail(
                "capability_escalation",
                f"capabilities exceed workflow allowlist: {', '.join(sorted(extra_capabilities))}",
                f"$.tasks[{task_id}].authority.capabilities",
            )

    for left_id, right_id in combinations(sorted(task_ids), 2):
        if left_id in ancestors[right_id] or right_id in ancestors[left_id]:
            continue
        collision = set(task_by_id[left_id]["writes"]) & set(
            task_by_id[right_id]["writes"]
        )
        if collision:
            _fail(
                "write_ownership_collision",
                f"unordered tasks {left_id!r} and {right_id!r} share write ownership: {', '.join(sorted(collision))}",
                "$.tasks",
            )

    verifier_links: dict[str, list[str]] = {
        task_id: []
        for task_id, task in task_by_id.items()
        if task["role"] == "execute"
    }
    if not verifier_links:
        _fail("execute_task_missing", "workflow requires at least one execute task")

    for task_id, task in task_by_id.items():
        verifies = task["verifies"]
        if task["role"] == "verify":
            if not verifies:
                _fail(
                    "verifier_target_required",
                    "verify task must name at least one execute target",
                    f"$.tasks[{task_id}].verifies",
                )
        elif verifies:
            _fail(
                "non_verifier_target",
                "only verify tasks may declare verifies targets",
                f"$.tasks[{task_id}].verifies",
            )

        for target_id in verifies:
            if target_id == task_id:
                _fail(
                    "verifier_self_approval",
                    "verifier cannot verify itself",
                    f"$.tasks[{task_id}].verifies",
                )
            target = task_by_id.get(target_id)
            if target is None:
                _fail(
                    "unknown_verifier_target",
                    f"unknown verifier target {target_id!r}",
                    f"$.tasks[{task_id}].verifies",
                )
            if target["role"] != "execute":
                _fail(
                    "verifier_target_not_execute",
                    f"verifier target {target_id!r} is not an execute task",
                    f"$.tasks[{task_id}].verifies",
                )
            if target["owner"] == task["owner"]:
                _fail(
                    "verifier_owner_conflict",
                    f"verifier and execute task {target_id!r} share owner {task['owner']!r}",
                    f"$.tasks[{task_id}].owner",
                )
            if target_id not in ancestors[task_id]:
                _fail(
                    "verifier_dependency_missing",
                    f"verifier must depend on execute target {target_id!r}",
                    f"$.tasks[{task_id}].depends_on",
                )
            verifier_links[target_id].append(task_id)

    for execute_task_id, verifier_ids in verifier_links.items():
        if not verifier_ids:
            _fail(
                "execute_task_unverified",
                f"execute task {execute_task_id!r} has no independent verifier",
                f"$.tasks[{execute_task_id}]",
            )

    loop_policy = definition["loop_policy"]
    if loop_policy["repeated_failure_limit"] > loop_policy["max_iterations"]:
        _fail(
            "unbounded_failure_policy",
            "repeated_failure_limit cannot exceed max_iterations",
            "$.loop_policy",
        )

    stop_conditions = set(definition["stop_conditions"])
    if "success" not in stop_conditions:
        _fail(
            "success_stop_missing",
            "workflow requires an explicit success stop condition",
            "$.stop_conditions",
        )
    if not stop_conditions & _TERMINAL_STOPS:
        _fail(
            "terminal_stop_missing",
            "workflow requires at least one terminal non-success stop condition",
            "$.stop_conditions",
        )
    if max_side_effect == "external_write" and "human_gate" not in stop_conditions:
        _fail(
            "human_gate_missing",
            "external-write workflow requires a human_gate stop condition",
            "$.stop_conditions",
        )
    repeated_failure_stop = {
        "switch_path": "blocked",
        "human_gate": "human_gate",
        "terminal_failure": "terminal_failure",
    }[loop_policy["on_repeated_failure"]]
    if repeated_failure_stop not in stop_conditions:
        _fail(
            "failure_policy_stop_missing",
            f"repeated-failure policy requires stop condition {repeated_failure_stop!r}",
            "$.stop_conditions",
        )

    handoff_fields = set(definition["handoff"]["required_fields"])
    missing_handoff = _REQUIRED_HANDOFF_FIELDS - handoff_fields
    if missing_handoff:
        _fail(
            "handoff_incomplete",
            f"missing required handoff fields: {', '.join(sorted(missing_handoff))}",
            "$.handoff.required_fields",
        )

    plan: dict[str, Any] = {
        "schema_version": "workflow_execution_plan_v1",
        "workflow_ref": {
            "workflow_id": definition["workflow_id"],
            "version": definition["version"],
        },
        "definition_digest": _canonical_digest(definition),
        "authority_granted": False,
        "executable": False,
        "waves": [
            {"index": index, "task_ids": wave}
            for index, wave in enumerate(waves, start=1)
        ],
        "task_bindings": [
            {
                "task_id": task_id,
                "role": task_by_id[task_id]["role"],
                "owner": task_by_id[task_id]["owner"],
                "depends_on": sorted(task_by_id[task_id]["depends_on"]),
                "side_effect": task_by_id[task_id]["authority"]["side_effect"],
                "capabilities": sorted(
                    task_by_id[task_id]["authority"]["capabilities"]
                ),
                "writes": sorted(task_by_id[task_id]["writes"]),
            }
            for task_id in sorted(task_ids)
        ],
        "verifier_links": [
            {
                "execute_task_id": execute_task_id,
                "verifier_task_ids": sorted(verifier_ids),
            }
            for execute_task_id, verifier_ids in sorted(verifier_links.items())
        ],
        "loop_policy": dict(definition["loop_policy"]),
        "stop_conditions": list(definition["stop_conditions"]),
        "handoff_required_fields": list(definition["handoff"]["required_fields"]),
    }
    plan["plan_digest"] = _canonical_digest(plan)
    _validate_schema(plan, "workflow-execution-plan-v1.schema.json")
    return plan
