from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from tools.delegation_expansion import (
    DelegationExpansionError,
    validate_delegation_expansion_v1,
)
from workflow_kernel.workflow_definition import compile_workflow_definition_v1


ROOT = Path(__file__).parents[2]
DEFINITION_CASES = (
    ROOT / "tests/contracts/fixtures/workflow-definition-v1/cases.json"
)
PROPOSAL_CASES = (
    ROOT
    / "tests/contracts/fixtures/delegation-expansion-proposal-v1/cases.json"
)
PUBLIC_SCHEMA = ROOT / "schemas/delegation-expansion-proposal-v1.schema.json"
PACKAGED_SCHEMA = (
    ROOT
    / "workflow_kernel/schemas/delegation-expansion-proposal-v1.schema.json"
)
PUBLIC_PROJECTION_SCHEMA = (
    ROOT / "schemas/delegation-expansion-projection-v1.schema.json"
)
PACKAGED_PROJECTION_SCHEMA = (
    ROOT
    / "workflow_kernel/schemas/delegation-expansion-projection-v1.schema.json"
)


def _definition() -> dict[str, Any]:
    cases = json.loads(DEFINITION_CASES.read_text(encoding="utf-8"))
    definition = copy.deepcopy(cases[0]["value"])
    plan = next(task for task in definition["tasks"] if task["id"] == "plan")
    plan_b = copy.deepcopy(plan)
    plan_b["id"] = "plan-b"
    plan_b["owner"] = "planner-b"
    plan_b["acceptance"][0]["id"] = "plan-b-bounded"
    definition["tasks"].insert(2, plan_b)
    implement = next(
        task for task in definition["tasks"] if task["id"] == "implement"
    )
    implement["depends_on"] = ["plan", "plan-b"]
    return definition


def _proposal(definition: dict[str, Any] | None = None) -> dict[str, Any]:
    plan = compile_workflow_definition_v1(definition or _definition())
    return {
        "schema_version": "delegation_expansion_proposal_v1",
        "plan_digest": plan["plan_digest"],
        "child_task_ids": ["plan-b", "plan"],
        "synthesis_task_id": "implement",
    }


def _assert_error(
    definition: object,
    proposal: object,
    code: str,
) -> DelegationExpansionError:
    with pytest.raises(DelegationExpansionError) as exc_info:
        validate_delegation_expansion_v1(definition, proposal)
    assert exc_info.value.code == code
    return exc_info.value


def test_validates_plan_bound_antichain_into_authority_neutral_projection() -> None:
    definition = _definition()
    proposal = _proposal(definition)

    projection = validate_delegation_expansion_v1(definition, proposal)

    plan = compile_workflow_definition_v1(definition)
    assert projection == {
        "schema_version": "delegation_expansion_projection_v1",
        "definition_digest": plan["definition_digest"],
        "plan_digest": plan["plan_digest"],
        "proposal_digest": projection["proposal_digest"],
        "authority_granted": False,
        "executable": False,
        "child_task_ids": ["plan", "plan-b"],
        "synthesis_task_id": "implement",
        "requested_child_nodes": 2,
    }
    assert projection["proposal_digest"].startswith("sha256:")


def test_projection_is_deterministic_across_child_input_order() -> None:
    definition = _definition()
    proposal = _proposal(definition)
    reversed_proposal = copy.deepcopy(proposal)
    reversed_proposal["child_task_ids"].reverse()

    assert validate_delegation_expansion_v1(
        definition, proposal
    ) == validate_delegation_expansion_v1(definition, reversed_proposal)


def test_validator_does_not_mutate_or_alias_inputs() -> None:
    definition = _definition()
    proposal = _proposal(definition)
    before_definition = copy.deepcopy(definition)
    before_proposal = copy.deepcopy(proposal)

    projection = validate_delegation_expansion_v1(definition, proposal)
    assert definition == before_definition
    assert proposal == before_proposal
    definition["tasks"][1]["id"] = "mutated"
    proposal["child_task_ids"].append("mutated")

    assert before_definition != definition
    assert before_proposal != proposal
    assert projection["child_task_ids"] == ["plan", "plan-b"]


def test_rejects_plan_digest_mismatch() -> None:
    definition = _definition()
    proposal = _proposal(definition)
    proposal["plan_digest"] = "sha256:" + "0" * 64

    _assert_error(definition, proposal, "plan_digest_mismatch")


@pytest.mark.parametrize(
    ("children", "code"),
    [
        (["plan", "missing"], "unknown_child_task"),
        (["inspect", "plan"], "child_frontier_not_antichain"),
    ],
)
def test_rejects_invalid_child_frontier(children: list[str], code: str) -> None:
    definition = _definition()
    proposal = _proposal(definition)
    proposal["child_task_ids"] = children

    _assert_error(definition, proposal, code)


def test_rejects_duplicate_child_before_graph_analysis() -> None:
    definition = _definition()
    proposal = _proposal(definition)
    proposal["child_task_ids"] = ["plan", "plan"]

    _assert_error(definition, proposal, "duplicate_child_task_id")


@pytest.mark.parametrize(
    ("synthesis", "code"),
    [
        ("missing", "unknown_synthesis_task"),
        ("plan", "synthesis_is_child"),
        ("inspect", "synthesis_not_descendant"),
        ("verify", "synthesis_role_invalid"),
    ],
)
def test_rejects_invalid_synthesis_task(synthesis: str, code: str) -> None:
    definition = _definition()
    proposal = _proposal(definition)
    proposal["synthesis_task_id"] = synthesis

    _assert_error(definition, proposal, code)


def test_rejects_synthesis_that_does_not_join_every_child() -> None:
    definition = _definition()
    implement = next(
        task for task in definition["tasks"] if task["id"] == "implement"
    )
    implement["depends_on"] = ["plan"]
    proposal = _proposal(_definition())
    proposal["plan_digest"] = compile_workflow_definition_v1(definition)["plan_digest"]

    _assert_error(definition, proposal, "synthesis_not_descendant")


def test_wraps_invalid_canonical_definition_fail_closed() -> None:
    definition = _definition()
    inspect = next(task for task in definition["tasks"] if task["id"] == "inspect")
    inspect["depends_on"] = ["implement"]

    error = _assert_error(definition, _proposal(), "definition_dependency_cycle")
    assert "dependency_cycle" in str(error)


@pytest.mark.parametrize(
    "proposal",
    [
        None,
        [],
        {"schema_version": "delegation_expansion_proposal_v1"},
        {
            "schema_version": "delegation_expansion_proposal_v1",
            "plan_digest": "sha256:" + "a" * 64,
            "child_task_ids": ["plan"],
            "synthesis_task_id": "implement",
            "authority_granted": True,
        },
    ],
)
def test_rejects_malformed_proposal_without_throwing_untyped_error(
    proposal: object,
) -> None:
    _assert_error(_definition(), proposal, "proposal_schema_invalid")


def test_rejects_non_plain_json_subclasses() -> None:
    class DictSubclass(dict):
        pass

    definition = DictSubclass(_definition())
    _assert_error(definition, _proposal(), "definition_input_not_plain_json")


def test_shared_proposal_fixtures_match_public_json_schema() -> None:
    schema = json.loads(PUBLIC_SCHEMA.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    cases = json.loads(PROPOSAL_CASES.read_text(encoding="utf-8"))

    for case in cases:
        assert validator.is_valid(case["value"]) is case["valid"], case["name"]


def test_public_and_packaged_proposal_schemas_are_byte_identical() -> None:
    assert PUBLIC_SCHEMA.read_bytes() == PACKAGED_SCHEMA.read_bytes()
    assert (
        PUBLIC_PROJECTION_SCHEMA.read_bytes()
        == PACKAGED_PROJECTION_SCHEMA.read_bytes()
    )


def test_contract_has_no_authority_budget_execution_or_graph_definition_fields() -> None:
    schema = json.loads(PUBLIC_SCHEMA.read_text(encoding="utf-8"))
    assert sorted(schema["properties"]) == [
        "child_task_ids",
        "plan_digest",
        "schema_version",
        "synthesis_task_id",
    ]
