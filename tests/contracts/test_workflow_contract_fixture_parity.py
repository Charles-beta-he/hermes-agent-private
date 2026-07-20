from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


REPO_ROOT = Path(__file__).resolve().parents[2]
CASES_PATH = REPO_ROOT / "tests" / "contracts" / "fixtures" / "evidence-v1" / "cases.json"
SCHEMA_PATH = REPO_ROOT / "schemas" / "evidence-v1.schema.json"


def _cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case["name"])
def test_evidence_v1_fixture_parity(case: dict) -> None:
    assert SCHEMA_PATH.exists(), "EvidenceV1 generated schema is missing"
    validator = Draft202012Validator(
        json.loads(SCHEMA_PATH.read_text(encoding="utf-8")),
        format_checker=Draft202012Validator.FORMAT_CHECKER,
    )

    errors = list(validator.iter_errors(case["value"]))

    assert (not errors) is case["valid"]
