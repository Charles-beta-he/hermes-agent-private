from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from tools import delegation_tree_budget
from tools.delegation_expansion import (
    DelegationExpansionError,
    validate_delegation_expansion_v1,
)
from tools.delegation_tree_budget import (
    DelegationTreeBudgetError,
    evaluate_delegation_tree_budget_preflight_from_sources_v1,
    evaluate_delegation_tree_budget_preflight_v1,
    validate_delegation_tree_budget_preflight_v1,
)
from workflow_kernel.workflow_definition import compile_workflow_definition_v1


ROOT = Path(__file__).parents[2]
CASES_PATH = (
    ROOT
    / "tests/contracts/fixtures/delegation-tree-budget-preflight-v1/cases.json"
)
PUBLIC_SNAPSHOT_SCHEMA = (
    ROOT / "schemas/delegation-tree-budget-snapshot-v1.schema.json"
)
PACKAGED_SNAPSHOT_SCHEMA = (
    ROOT / "workflow_kernel/schemas/delegation-tree-budget-snapshot-v1.schema.json"
)
PUBLIC_PREFLIGHT_SCHEMA = (
    ROOT / "schemas/delegation-tree-budget-preflight-v1.schema.json"
)
PACKAGED_PREFLIGHT_SCHEMA = (
    ROOT / "workflow_kernel/schemas/delegation-tree-budget-preflight-v1.schema.json"
)


def _cases() -> list[dict[str, Any]]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def _valid_pair() -> tuple[dict[str, Any], dict[str, Any]]:
    case = _cases()[0]
    return copy.deepcopy(case["snapshot"]), copy.deepcopy(case["projection"])


def _source_inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    definition_cases = json.loads(
        (ROOT / "tests/contracts/fixtures/workflow-definition-v1/cases.json").read_text(
            encoding="utf-8"
        )
    )
    definition = copy.deepcopy(definition_cases[0]["value"])
    plan = next(task for task in definition["tasks"] if task["id"] == "plan")
    sibling = copy.deepcopy(plan)
    sibling["id"] = "plan-b"
    sibling["owner"] = "planner-b"
    sibling["acceptance"][0]["id"] = "plan-b-bounded"
    definition["tasks"].insert(2, sibling)
    implement = next(task for task in definition["tasks"] if task["id"] == "implement")
    implement["depends_on"] = ["plan", "plan-b"]
    proposal = {
        "schema_version": "delegation_expansion_proposal_v1",
        "plan_digest": compile_workflow_definition_v1(definition)["plan_digest"],
        "child_task_ids": ["plan-b", "plan"],
        "synthesis_task_id": "implement",
    }
    snapshot, _ = _valid_pair()
    return definition, proposal, snapshot


def test_source_bound_operation_equals_canonical_phase_two_then_phase_three() -> None:
    definition, proposal, snapshot = _source_inputs()

    expected = evaluate_delegation_tree_budget_preflight_v1(
        snapshot,
        validate_delegation_expansion_v1(definition, proposal),
    )

    assert evaluate_delegation_tree_budget_preflight_from_sources_v1(
        definition, proposal, snapshot
    ) == expected


def test_source_bound_operation_rejects_proposal_after_definition_drift() -> None:
    definition, proposal, snapshot = _source_inputs()
    definition["tasks"][1]["owner"] = "changed-owner"

    with pytest.raises(DelegationExpansionError) as exc_info:
        evaluate_delegation_tree_budget_preflight_from_sources_v1(
            definition, proposal, snapshot
        )

    assert exc_info.value.code == "plan_digest_mismatch"


def test_source_bound_operation_has_no_detached_projection_input() -> None:
    signature = inspect.signature(
        evaluate_delegation_tree_budget_preflight_from_sources_v1
    )

    assert tuple(signature.parameters) == ("definition", "proposal", "snapshot")


def test_source_bound_operation_is_publicly_exported() -> None:
    assert (
        "evaluate_delegation_tree_budget_preflight_from_sources_v1"
        in delegation_tree_budget.__all__
    )


def test_source_bound_operation_is_deterministic_and_does_not_mutate_sources() -> None:
    definition, proposal, snapshot = _source_inputs()
    before = copy.deepcopy((definition, proposal, snapshot))

    first = evaluate_delegation_tree_budget_preflight_from_sources_v1(
        definition, proposal, snapshot
    )
    second = evaluate_delegation_tree_budget_preflight_from_sources_v1(
        definition, proposal, snapshot
    )

    assert first == second
    assert (definition, proposal, snapshot) == before
    assert first["authority_granted"] is False
    assert first["executable"] is False
    assert first["reservation_committed"] is False
    assert first["ledger_mutated"] is False


def _assert_error(
    snapshot: object,
    projection: object,
    code: str,
) -> DelegationTreeBudgetError:
    with pytest.raises(DelegationTreeBudgetError) as exc_info:
        evaluate_delegation_tree_budget_preflight_v1(snapshot, projection)
    assert exc_info.value.code == code
    return exc_info.value


def test_shared_fixture_projects_exact_authority_neutral_preflight() -> None:
    case = _cases()[0]

    result = evaluate_delegation_tree_budget_preflight_v1(
        case["snapshot"], case["projection"]
    )

    assert result == case["expected"]


def test_zero_capacity_is_insufficient_not_an_error() -> None:
    case = _cases()[1]

    result = evaluate_delegation_tree_budget_preflight_v1(
        case["snapshot"], case["projection"]
    )

    assert result["capacity_fit_at_snapshot"] is False
    assert result["reason_codes"] == ["tree_capacity_insufficient_at_snapshot"]
    assert result["reservation_committed"] is False
    assert result["ledger_mutated"] is False


def test_same_snapshot_can_fit_twice_without_claiming_reservation() -> None:
    snapshot, projection = _valid_pair()

    first = evaluate_delegation_tree_budget_preflight_v1(snapshot, projection)
    second = evaluate_delegation_tree_budget_preflight_v1(snapshot, projection)

    assert first == second
    assert first["capacity_fit_at_snapshot"] is True
    assert first["reservation_committed"] is False
    assert "remaining_after" not in first
    assert "ledger_delta" not in first


def test_snapshot_digest_changes_with_revision_or_capacity() -> None:
    snapshot, projection = _valid_pair()
    baseline = evaluate_delegation_tree_budget_preflight_v1(snapshot, projection)
    revised = copy.deepcopy(snapshot)
    revised["snapshot_revision"] += 1
    reduced = copy.deepcopy(snapshot)
    reduced["remaining_node_capacity"] -= 1

    revised_result = evaluate_delegation_tree_budget_preflight_v1(revised, projection)
    reduced_result = evaluate_delegation_tree_budget_preflight_v1(reduced, projection)

    assert revised_result["budget_snapshot_digest"] != baseline["budget_snapshot_digest"]
    assert reduced_result["budget_snapshot_digest"] != baseline["budget_snapshot_digest"]


def test_child_order_uses_phase_two_proposal_identity() -> None:
    snapshot, projection = _valid_pair()
    reversed_projection = copy.deepcopy(projection)
    reversed_projection["child_task_ids"].reverse()

    assert evaluate_delegation_tree_budget_preflight_v1(
        snapshot, projection
    ) == evaluate_delegation_tree_budget_preflight_v1(snapshot, reversed_projection)


def test_changed_child_identity_with_same_count_requires_new_proposal_identity() -> None:
    snapshot, projection = _valid_pair()
    changed = copy.deepcopy(projection)
    changed["child_task_ids"] = ["plan", "plan-c"]
    changed["proposal_digest"] = (
        "sha256:02ae941d4fb023784e47849ed6452385942594c2f71ad450aaadf466793d8675"
    )

    baseline = evaluate_delegation_tree_budget_preflight_v1(snapshot, projection)
    changed_result = evaluate_delegation_tree_budget_preflight_v1(snapshot, changed)

    assert changed_result["requested_child_nodes"] == baseline["requested_child_nodes"]
    assert changed_result["proposal_digest"] != baseline["proposal_digest"]


def test_evaluator_does_not_mutate_or_alias_inputs() -> None:
    snapshot, projection = _valid_pair()
    before_snapshot = copy.deepcopy(snapshot)
    before_projection = copy.deepcopy(projection)

    result = evaluate_delegation_tree_budget_preflight_v1(snapshot, projection)
    snapshot["remaining_node_capacity"] = 0
    projection["child_task_ids"].append("later")

    assert before_snapshot != snapshot
    assert before_projection != projection
    assert result["requested_child_nodes"] == 2
    assert result["capacity_fit_at_snapshot"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("snapshot_revision", True),
        ("snapshot_revision", "1"),
        ("snapshot_revision", 1.5),
        ("snapshot_revision", float("nan")),
        ("remaining_node_capacity", False),
        ("remaining_node_capacity", "2"),
        ("remaining_node_capacity", float("inf")),
    ],
)
def test_rejects_non_integral_snapshot_numbers(field: str, value: object) -> None:
    snapshot, projection = _valid_pair()
    snapshot[field] = value

    _assert_error(snapshot, projection, "invalid_integer")


def test_integral_snapshot_numbers_normalize_before_hashing() -> None:
    snapshot, projection = _valid_pair()
    baseline = evaluate_delegation_tree_budget_preflight_v1(snapshot, projection)
    snapshot["snapshot_revision"] = 7.0
    snapshot["remaining_node_capacity"] = 2.0

    normalized = evaluate_delegation_tree_budget_preflight_v1(snapshot, projection)

    assert normalized == baseline
    assert type(normalized["snapshot_revision"]) is int


def test_integral_projection_count_normalizes_before_relation_check() -> None:
    snapshot, projection = _valid_pair()
    projection["requested_child_nodes"] = 2.0

    result = evaluate_delegation_tree_budget_preflight_v1(snapshot, projection)

    assert result["requested_child_nodes"] == 2
    assert type(result["requested_child_nodes"]) is int


@pytest.mark.parametrize("value", [True, "2", 2.5, float("nan"), float("inf")])
def test_rejects_non_integral_projection_count(value: object) -> None:
    snapshot, projection = _valid_pair()
    projection["requested_child_nodes"] = value

    _assert_error(snapshot, projection, "invalid_integer")


def test_rejects_projection_integer_subclass() -> None:
    class IntSubclass(int):
        pass

    snapshot, projection = _valid_pair()
    projection["requested_child_nodes"] = IntSubclass(2)

    _assert_error(snapshot, projection, "invalid_integer")


def test_rejects_snapshot_integer_subclass() -> None:
    class IntSubclass(int):
        pass

    snapshot, projection = _valid_pair()
    snapshot["snapshot_revision"] = IntSubclass(7)

    _assert_error(snapshot, projection, "invalid_integer")


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("tree_budget_id", "", "snapshot_schema_invalid"),
        ("snapshot_revision", 0, "integer_out_of_range"),
        ("remaining_node_capacity", -1, "integer_out_of_range"),
        ("remaining_node_capacity", 1_000_001, "integer_out_of_range"),
    ],
)
def test_rejects_invalid_snapshot_values(
    field: str, value: object, code: str
) -> None:
    snapshot, projection = _valid_pair()
    snapshot[field] = value

    _assert_error(snapshot, projection, code)


def test_rejects_unknown_snapshot_field() -> None:
    snapshot, projection = _valid_pair()
    snapshot["authority_granted"] = True

    _assert_error(snapshot, projection, "snapshot_schema_invalid")


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        ({"requested_child_nodes": 1}, "requested_child_nodes_mismatch"),
        ({"child_task_ids": ["plan", "plan"]}, "duplicate_child_task_id"),
        ({"authority_granted": True}, "projection_schema_invalid"),
        ({"executable": True}, "projection_schema_invalid"),
        ({"proposal_digest": "sha256:" + "0" * 64}, "proposal_digest_mismatch"),
    ],
)
def test_rejects_forged_or_authoritative_projection(
    mutation: dict[str, object], code: str
) -> None:
    snapshot, projection = _valid_pair()
    projection.update(mutation)

    _assert_error(snapshot, projection, code)


def test_rejects_unknown_projection_field() -> None:
    snapshot, projection = _valid_pair()
    projection["remaining_node_capacity"] = 99

    _assert_error(snapshot, projection, "projection_schema_invalid")


@pytest.mark.parametrize(
    "field",
    ["authority_granted", "executable", "reservation_committed", "ledger_mutated"],
)
def test_output_validator_rejects_authority_or_effect_claim(field: str) -> None:
    snapshot, projection = _valid_pair()
    result = evaluate_delegation_tree_budget_preflight_v1(snapshot, projection)
    result[field] = True

    with pytest.raises(DelegationTreeBudgetError) as exc_info:
        validate_delegation_tree_budget_preflight_v1(result)
    assert exc_info.value.code == "preflight_schema_invalid"


def test_output_validator_rejects_reason_drift() -> None:
    snapshot, projection = _valid_pair()
    result = evaluate_delegation_tree_budget_preflight_v1(snapshot, projection)
    result["reason_codes"] = ["tree_capacity_insufficient_at_snapshot"]

    with pytest.raises(DelegationTreeBudgetError) as exc_info:
        validate_delegation_tree_budget_preflight_v1(result)
    assert exc_info.value.code == "preflight_reason_mismatch"


def test_output_validator_rejects_unknown_field() -> None:
    snapshot, projection = _valid_pair()
    result = evaluate_delegation_tree_budget_preflight_v1(snapshot, projection)
    result["remaining_after"] = 0

    with pytest.raises(DelegationTreeBudgetError) as exc_info:
        validate_delegation_tree_budget_preflight_v1(result)
    assert exc_info.value.code == "preflight_schema_invalid"


def test_output_validator_normalizes_integral_numbers() -> None:
    snapshot, projection = _valid_pair()
    result = evaluate_delegation_tree_budget_preflight_v1(snapshot, projection)
    result["snapshot_revision"] = 7.0
    result["requested_child_nodes"] = 2.0

    normalized = validate_delegation_tree_budget_preflight_v1(result)

    assert normalized["snapshot_revision"] == 7
    assert normalized["requested_child_nodes"] == 2
    assert type(normalized["snapshot_revision"]) is int
    assert type(normalized["requested_child_nodes"]) is int


def test_generated_schemas_are_packaged_byte_identically_and_validate_fixture() -> None:
    assert PUBLIC_SNAPSHOT_SCHEMA.read_bytes() == PACKAGED_SNAPSHOT_SCHEMA.read_bytes()
    assert PUBLIC_PREFLIGHT_SCHEMA.read_bytes() == PACKAGED_PREFLIGHT_SCHEMA.read_bytes()
    snapshot_validator = jsonschema.Draft202012Validator(
        json.loads(PUBLIC_SNAPSHOT_SCHEMA.read_text(encoding="utf-8"))
    )
    preflight_validator = jsonschema.Draft202012Validator(
        json.loads(PUBLIC_PREFLIGHT_SCHEMA.read_text(encoding="utf-8"))
    )
    case = _cases()[0]
    snapshot_validator.validate(case["snapshot"])
    preflight_validator.validate(case["expected"])

    integral_snapshot = copy.deepcopy(case["snapshot"])
    integral_snapshot["snapshot_revision"] = 7.0
    integral_snapshot["remaining_node_capacity"] = 2.0
    snapshot_validator.validate(integral_snapshot)

    fractional_snapshot = copy.deepcopy(case["snapshot"])
    fractional_snapshot["snapshot_revision"] = 7.5
    with pytest.raises(jsonschema.ValidationError):
        snapshot_validator.validate(fractional_snapshot)

    boolean_snapshot = copy.deepcopy(case["snapshot"])
    boolean_snapshot["remaining_node_capacity"] = True
    with pytest.raises(jsonschema.ValidationError):
        snapshot_validator.validate(boolean_snapshot)


def test_public_preflight_schema_rejects_reason_cardinality_and_relation_drift() -> None:
    validator = jsonschema.Draft202012Validator(
        json.loads(PUBLIC_PREFLIGHT_SCHEMA.read_text(encoding="utf-8"))
    )
    expected = copy.deepcopy(_cases()[0]["expected"])
    invalid_reasons = (
        [],
        ["tree_capacity_available_at_snapshot", "tree_capacity_insufficient_at_snapshot"],
        ["tree_capacity_insufficient_at_snapshot"],
    )

    for reason_codes in invalid_reasons:
        mutant = {**expected, "reason_codes": reason_codes}
        assert not validator.is_valid(mutant), reason_codes


def test_contract_exposes_no_residual_or_runtime_handle() -> None:
    schema = json.loads(PUBLIC_PREFLIGHT_SCHEMA.read_text(encoding="utf-8"))
    assert sorted(schema["properties"]) == sorted(
        {
            "schema_version",
            "tree_budget_id",
            "snapshot_revision",
            "budget_snapshot_digest",
            "proposal_digest",
            "requested_child_nodes",
            "capacity_fit_at_snapshot",
            "reason_codes",
            "authority_granted",
            "executable",
            "reservation_committed",
            "ledger_mutated",
        }
    )
