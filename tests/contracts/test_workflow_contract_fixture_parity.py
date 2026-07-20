from __future__ import annotations

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


@pytest.mark.parametrize("case", _cases(EVIDENCE_CASES_PATH), ids=lambda case: case["name"])
def test_evidence_v1_fixture_parity(case: dict) -> None:
    _assert_schema_case(EVIDENCE_SCHEMA_PATH, case, "EvidenceV1")


@pytest.mark.parametrize("case", _cases(VOTE_CASES_PATH), ids=lambda case: case["name"])
def test_verifier_vote_v1_fixture_parity(case: dict) -> None:
    _assert_schema_case(VOTE_SCHEMA_PATH, case, "VerifierVoteV1")


@pytest.mark.parametrize("case", _cases(CLOSURE_CASES_PATH), ids=lambda case: case["name"])
def test_closure_v1_fixture_parity(case: dict) -> None:
    _assert_schema_case(CLOSURE_SCHEMA_PATH, case, "ClosureV1")
