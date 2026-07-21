from __future__ import annotations

import copy
import hashlib
import json
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from workflow_kernel.workflow_definition import (
    WorkflowDefinitionError,
    compile_workflow_definition_v1,
)


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "contracts"
    / "fixtures"
    / "workflow-definition-v1"
    / "cases.json"
)
INTEGRAL_NUMBER_PLAN_FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "contracts"
    / "fixtures"
    / "workflow-execution-plan-v1"
    / "integral-number-plan.json"
)


def _valid_definition() -> dict[str, Any]:
    cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return copy.deepcopy(cases[0]["value"])


def _assert_error(definition: dict[str, Any], code: str) -> None:
    with pytest.raises(WorkflowDefinitionError) as exc_info:
        compile_workflow_definition_v1(definition)
    assert exc_info.value.code == code


def _task(definition: dict[str, Any], task_id: str) -> dict[str, Any]:
    tasks = definition["tasks"]
    assert isinstance(tasks, list)
    return next(task for task in tasks if task["id"] == task_id)


def test_compile_valid_definition_into_authority_neutral_plan() -> None:
    definition = _valid_definition()

    plan = compile_workflow_definition_v1(definition)

    assert plan["schema_version"] == "workflow_execution_plan_v1"
    assert plan["workflow_ref"] == {"workflow_id": "deep-absorption", "version": 1}
    assert plan["definition_digest"].startswith("sha256:")
    assert plan["plan_digest"].startswith("sha256:")
    plan_body = {key: value for key, value in plan.items() if key != "plan_digest"}
    expected_plan_digest = hashlib.sha256(
        json.dumps(
            plan_body,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    assert plan["plan_digest"] == f"sha256:{expected_plan_digest}"
    assert compile_workflow_definition_v1(definition)["plan_digest"] == plan["plan_digest"]
    assert plan["authority_granted"] is False
    assert plan["executable"] is False
    assert plan["waves"] == [
        {"index": 1, "task_ids": ["inspect"]},
        {"index": 2, "task_ids": ["plan"]},
        {"index": 3, "task_ids": ["implement"]},
        {"index": 4, "task_ids": ["verify"]},
    ]
    assert plan["verifier_links"] == [
        {"execute_task_id": "implement", "verifier_task_ids": ["verify"]}
    ]


def test_compiler_normalizes_integral_json_numbers_before_digesting() -> None:
    integer_definition = _valid_definition()
    float_definition = _valid_definition()
    float_definition["version"] = 1.0
    float_definition["loop_policy"]["max_iterations"] = 3.0
    float_definition["loop_policy"]["repeated_failure_limit"] = 2.0

    integer_plan = compile_workflow_definition_v1(integer_definition)
    float_plan = compile_workflow_definition_v1(float_definition)
    shared_fixture = json.loads(
        INTEGRAL_NUMBER_PLAN_FIXTURE_PATH.read_text(encoding="utf-8")
    )

    assert float_plan == integer_plan
    assert float_plan == shared_fixture["value"]
    assert type(float_plan["workflow_ref"]["version"]) is int
    assert type(float_plan["loop_policy"]["max_iterations"]) is int
    assert type(float_plan["loop_policy"]["repeated_failure_limit"]) is int


def test_compiler_does_not_mutate_input() -> None:
    definition = _valid_definition()
    before = copy.deepcopy(definition)

    compile_workflow_definition_v1(definition)

    assert definition == before


def test_compiled_plan_matches_packaged_json_schema() -> None:
    plan = compile_workflow_definition_v1(_valid_definition())
    schema_text = resources.files("workflow_kernel.schemas").joinpath(
        "workflow-execution-plan-v1.schema.json"
    ).read_text(encoding="utf-8")

    jsonschema.Draft202012Validator(json.loads(schema_text)).validate(plan)


def test_definition_schema_is_packaged_and_enforced() -> None:
    definition = _valid_definition()
    definition["authority_granted"] = True

    _assert_error(definition, "schema_invalid")


def test_shared_definition_fixtures_match_python_compiler_boundary() -> None:
    cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    for test_case in cases:
        if test_case["valid"]:
            compile_workflow_definition_v1(test_case["value"])
        else:
            with pytest.raises(WorkflowDefinitionError):
                compile_workflow_definition_v1(test_case["value"])


def test_public_and_packaged_generated_schemas_are_byte_identical() -> None:
    repository_root = Path(__file__).parents[2]
    for name in (
        "workflow-definition-v1.schema.json",
        "workflow-execution-plan-v1.schema.json",
        "workflow-loop-snapshot-v1.schema.json",
        "workflow-loop-signal-v1.schema.json",
        "workflow-loop-event-v1.schema.json",
    ):
        public_schema = (repository_root / "schemas" / name).read_bytes()
        packaged_schema = resources.files("workflow_kernel.schemas").joinpath(name).read_bytes()
        assert public_schema == packaged_schema


def test_rejects_duplicate_task_ids() -> None:
    definition = _valid_definition()
    tasks = definition["tasks"]
    assert isinstance(tasks, list)
    duplicate = copy.deepcopy(tasks[0])
    tasks.append(duplicate)

    _assert_error(definition, "duplicate_task_id")


def test_rejects_duplicate_acceptance_ids_across_tasks() -> None:
    definition = _valid_definition()
    verify_acceptance = _task(definition, "verify")["acceptance"]
    assert isinstance(verify_acceptance, list)
    verify_acceptance[0]["id"] = "compiler-tests"

    _assert_error(definition, "duplicate_acceptance_id")


def test_rejects_unknown_dependency() -> None:
    definition = _valid_definition()
    _task(definition, "implement")["depends_on"] = ["missing"]

    _assert_error(definition, "unknown_dependency")


def test_rejects_dependency_cycle() -> None:
    definition = _valid_definition()
    _task(definition, "inspect")["depends_on"] = ["verify"]

    _assert_error(definition, "dependency_cycle")


def test_rejects_duplicate_dependency_reference() -> None:
    definition = _valid_definition()
    _task(definition, "implement")["depends_on"] = ["plan", "plan"]

    _assert_error(definition, "duplicate_list_value")


def test_rejects_side_effect_escalation() -> None:
    definition = _valid_definition()
    _task(definition, "implement")["authority"]["side_effect"] = "external_write"

    _assert_error(definition, "authority_escalation")


def test_rejects_capability_escalation() -> None:
    definition = _valid_definition()
    _task(definition, "implement")["authority"]["capabilities"].append("browser")

    _assert_error(definition, "capability_escalation")


def test_rejects_execute_task_without_verifier() -> None:
    definition = _valid_definition()
    _task(definition, "verify")["verifies"] = []

    _assert_error(definition, "verifier_target_required")


def test_rejects_verifier_same_owner_as_executor() -> None:
    definition = _valid_definition()
    _task(definition, "verify")["owner"] = "executor"

    _assert_error(definition, "verifier_owner_conflict")


def test_rejects_self_verification() -> None:
    definition = _valid_definition()
    _task(definition, "verify")["verifies"] = ["verify"]

    _assert_error(definition, "verifier_self_approval")


def test_rejects_verifier_without_dependency_path_to_target() -> None:
    definition = _valid_definition()
    _task(definition, "verify")["depends_on"] = ["plan"]

    _assert_error(definition, "verifier_dependency_missing")


def test_rejects_non_verifier_with_verifies_targets() -> None:
    definition = _valid_definition()
    _task(definition, "plan")["verifies"] = ["inspect"]

    _assert_error(definition, "non_verifier_target")


def test_rejects_unordered_write_ownership_collision() -> None:
    definition = _valid_definition()
    tasks = definition["tasks"]
    assert isinstance(tasks, list)
    parallel = copy.deepcopy(_task(definition, "implement"))
    parallel["id"] = "implement-parallel"
    parallel["owner"] = "executor-parallel"
    parallel["depends_on"] = ["plan"]
    parallel["acceptance"][0]["id"] = "parallel-tests"
    tasks.append(parallel)
    verifier = _task(definition, "verify")
    verifier["depends_on"].append("implement-parallel")
    verifier["verifies"].append("implement-parallel")

    _assert_error(definition, "write_ownership_collision")


def test_allows_ordered_reuse_of_write_ownership() -> None:
    definition = _valid_definition()
    tasks = definition["tasks"]
    assert isinstance(tasks, list)
    second = copy.deepcopy(_task(definition, "implement"))
    second["id"] = "implement-second"
    second["owner"] = "executor-second"
    second["depends_on"] = ["implement"]
    second["acceptance"][0]["id"] = "second-tests"
    tasks.append(second)
    verifier = _task(definition, "verify")
    verifier["depends_on"] = ["implement-second"]
    verifier["verifies"].append("implement-second")

    plan = compile_workflow_definition_v1(definition)

    assert plan["waves"][3]["task_ids"] == ["implement-second"]
    assert plan["waves"][4]["task_ids"] == ["verify"]


def test_rejects_repeated_failure_limit_above_iteration_bound() -> None:
    definition = _valid_definition()
    definition["loop_policy"]["max_iterations"] = 2
    definition["loop_policy"]["repeated_failure_limit"] = 3

    _assert_error(definition, "unbounded_failure_policy")


@pytest.mark.parametrize(
    ("policy", "required_stop"),
    [
        ("switch_path", "blocked"),
        ("human_gate", "human_gate"),
        ("terminal_failure", "terminal_failure"),
    ],
)
def test_repeated_failure_policy_requires_matching_stop_condition(
    policy: str, required_stop: str
) -> None:
    definition = _valid_definition()
    definition["loop_policy"]["on_repeated_failure"] = policy
    definition["stop_conditions"] = ["success", "cancelled"]
    assert required_stop not in definition["stop_conditions"]

    _assert_error(definition, "failure_policy_stop_missing")


@pytest.mark.parametrize(
    ("conditions", "code"),
    [
        (["blocked", "terminal_failure"], "success_stop_missing"),
        (["success", "blocked"], "terminal_stop_missing"),
    ],
)
def test_rejects_uncloseable_stop_conditions(
    conditions: list[str], code: str
) -> None:
    definition = _valid_definition()
    definition["stop_conditions"] = conditions

    _assert_error(definition, code)


def test_external_write_requires_human_gate() -> None:
    definition = _valid_definition()
    definition["authority"]["max_side_effect"] = "external_write"
    _task(definition, "implement")["authority"]["side_effect"] = "external_write"
    definition["stop_conditions"] = ["success", "terminal_failure"]

    _assert_error(definition, "human_gate_missing")


def test_rejects_incomplete_handoff_contract() -> None:
    definition = _valid_definition()
    definition["handoff"]["required_fields"] = ["goal", "next_step"]

    _assert_error(definition, "handoff_incomplete")
