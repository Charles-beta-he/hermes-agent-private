from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_CASES_PATH = REPO_ROOT / "tests" / "contracts" / "fixtures" / "evidence-v1" / "cases.json"
EVIDENCE_SCHEMA_PATH = REPO_ROOT / "schemas" / "evidence-v1.schema.json"
VOTE_CASES_PATH = REPO_ROOT / "tests" / "contracts" / "fixtures" / "verifier-vote-v1" / "cases.json"
VOTE_SCHEMA_PATH = REPO_ROOT / "schemas" / "verifier-vote-v1.schema.json"
CLOSURE_CASES_PATH = REPO_ROOT / "tests" / "contracts" / "fixtures" / "closure-v1" / "cases.json"
CLOSURE_SCHEMA_PATH = REPO_ROOT / "schemas" / "closure-v1.schema.json"
LOCAL_REQUIREMENTS_AUDIT_CASES_PATH = (
    REPO_ROOT
    / "tests"
    / "contracts"
    / "fixtures"
    / "governed-local-requirements-audit-policy-v1"
    / "cases.json"
)
LOCAL_REQUIREMENTS_AUDIT_SCHEMA_PATH = (
    REPO_ROOT
    / "schemas"
    / "governed-local-requirements-audit-policy-v1.schema.json"
)
RUN_STATUS_CASES_PATH = REPO_ROOT / "tests" / "contracts" / "fixtures" / "workflow-run-status-v1" / "cases.json"
RUN_STATUS_SCHEMA_PATH = REPO_ROOT / "schemas" / "workflow-run-status-v1.schema.json"
TRANSITION_CASES_PATH = REPO_ROOT / "tests" / "contracts" / "fixtures" / "workflow-run-transition-v1" / "cases.json"
TRANSITION_SCHEMA_PATH = REPO_ROOT / "schemas" / "workflow-run-transition-v1.schema.json"
RUN_EVENT_CASES_PATH = REPO_ROOT / "tests" / "contracts" / "fixtures" / "workflow-run-event-v1" / "cases.json"
RUN_EVENT_NONBLANK_CASES_PATH = REPO_ROOT / "tests" / "contracts" / "fixtures" / "workflow-run-event-v1" / "nonblank-cases.json"
RUN_EVENT_SCHEMA_PATH = REPO_ROOT / "schemas" / "workflow-run-event-v1.schema.json"
RUN_EVENT_NONBLANK_FIELDS = (
    "run_id",
    "idempotency_key",
    "provenance.producer",
    "provenance.artifact_path",
)
RUN_STATUS_TRANSITION_SCHEMAS = (
    REPO_ROOT / "schemas" / "runtime_claim_execution_packet.schema.json",
    REPO_ROOT / "schemas" / "runtime_execution_worker_packet.schema.json",
    REPO_ROOT / "schemas" / "runtime_module_execution_packet.schema.json",
    REPO_ROOT / "schemas" / "runtime_review_done_packet.schema.json",
)


def _cases(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_schema_case(schema_path: Path, case: dict, label: str) -> None:
    assert schema_path.exists(), f"{label} generated schema is missing"
    validator = Draft202012Validator(
        json.loads(schema_path.read_text(encoding="utf-8")),
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )

    errors = list(validator.iter_errors(case["value"]))

    assert (not errors) is case["valid"]


def _event_with_nonblank_field(field: str, value: str) -> dict:
    valid_case = next(case for case in _cases(RUN_EVENT_CASES_PATH) if case["valid"])
    event = deepcopy(valid_case["value"])
    if field.startswith("provenance."):
        event["provenance"][field.removeprefix("provenance.")] = value
    else:
        event[field] = value
    return event


@pytest.mark.parametrize("case", _cases(EVIDENCE_CASES_PATH), ids=lambda case: case["name"])
def test_evidence_v1_fixture_parity(case: dict) -> None:
    _assert_schema_case(EVIDENCE_SCHEMA_PATH, case, "EvidenceV1")


@pytest.mark.parametrize("case", _cases(VOTE_CASES_PATH), ids=lambda case: case["name"])
def test_verifier_vote_v1_fixture_parity(case: dict) -> None:
    _assert_schema_case(VOTE_SCHEMA_PATH, case, "VerifierVoteV1")


@pytest.mark.parametrize("case", _cases(CLOSURE_CASES_PATH), ids=lambda case: case["name"])
def test_closure_v1_fixture_parity(case: dict) -> None:
    _assert_schema_case(CLOSURE_SCHEMA_PATH, case, "ClosureV1")


@pytest.mark.parametrize(
    "case",
    _cases(LOCAL_REQUIREMENTS_AUDIT_CASES_PATH),
    ids=lambda case: case["name"],
)
def test_governed_local_requirements_audit_policy_v1_fixture_parity(
    case: dict,
) -> None:
    _assert_schema_case(
        LOCAL_REQUIREMENTS_AUDIT_SCHEMA_PATH,
        case,
        "GovernedLocalRequirementsAuditPolicyV1",
    )


@pytest.mark.parametrize("case", _cases(RUN_STATUS_CASES_PATH), ids=lambda case: case["name"])
def test_workflow_run_status_v1_fixture_parity(case: dict) -> None:
    _assert_schema_case(RUN_STATUS_SCHEMA_PATH, case, "WorkflowRunStatusV1")


def test_workflow_run_status_v1_matches_runtime_packet_lifecycle() -> None:
    observed: list[str] = []
    for schema_path in RUN_STATUS_TRANSITION_SCHEMAS:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        transition_key = next(
            key
            for key, value in schema["properties"].items()
            if isinstance(value, dict)
            and "properties" in value
            and "previous_status" in value["properties"]
        )
        transition = schema["properties"][transition_key]["properties"]
        previous = transition["previous_status"]["const"]
        current = transition["new_status"]["const"]
        if not observed:
            observed.append(previous)
        assert observed[-1] == previous
        observed.append(current)

    generated = json.loads(RUN_STATUS_SCHEMA_PATH.read_text(encoding="utf-8"))
    assert generated["enum"] == observed

    transition_validator = Draft202012Validator(
        json.loads(TRANSITION_SCHEMA_PATH.read_text(encoding="utf-8"))
    )
    expected_edges = set(zip(observed, observed[1:]))
    for source in observed:
        for destination in observed:
            errors = list(
                transition_validator.iter_errors(
                    {"from": source, "to": destination}
                )
            )
            assert (not errors) is ((source, destination) in expected_edges)


@pytest.mark.parametrize("case", _cases(TRANSITION_CASES_PATH), ids=lambda case: case["name"])
def test_workflow_run_transition_v1_fixture_parity(case: dict) -> None:
    _assert_schema_case(TRANSITION_SCHEMA_PATH, case, "WorkflowRunTransitionV1")


@pytest.mark.parametrize("case", _cases(RUN_EVENT_CASES_PATH), ids=lambda case: case["name"])
def test_workflow_run_event_v1_fixture_parity(case: dict) -> None:
    _assert_schema_case(RUN_EVENT_SCHEMA_PATH, case, "WorkflowRunEventV1")


@pytest.mark.parametrize("field", RUN_EVENT_NONBLANK_FIELDS)
@pytest.mark.parametrize(
    "case",
    _cases(RUN_EVENT_NONBLANK_CASES_PATH),
    ids=lambda case: case["name"],
)
def test_workflow_run_event_v1_portable_nonblank_parity(
    field: str,
    case: dict,
) -> None:
    _assert_schema_case(
        RUN_EVENT_SCHEMA_PATH,
        {"value": _event_with_nonblank_field(field, case["value"]), "valid": case["valid"]},
        f"WorkflowRunEventV1.{field}",
    )


@pytest.mark.parametrize("field", RUN_EVENT_NONBLANK_FIELDS)
@pytest.mark.parametrize("codepoint", (0xD800, 0xDFFF))
def test_workflow_run_event_v1_rejects_unpaired_surrogates(
    field: str,
    codepoint: int,
) -> None:
    _assert_schema_case(
        RUN_EVENT_SCHEMA_PATH,
        {
            "value": _event_with_nonblank_field(field, f"run-{chr(codepoint)}"),
            "valid": False,
        },
        f"WorkflowRunEventV1.{field}.surrogate",
    )
