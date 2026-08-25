from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from cron.governance import (
    CronIdentityError,
    audit_jobs,
    classify_owner,
    cron_identity,
)


FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "cron" / "governance-synthetic.json"
)
RECORD_FIELDS = {
    "job_id",
    "enabled",
    "state",
    "cron_identity",
    "owner_status",
    "owner_evidence_refs",
    "mode",
    "schedule_class",
    "target_resource",
    "side_effect_class",
    "duplicate_group",
    "findings",
    "recommended_action",
}


def _job(job_id: str, **overrides: object) -> dict:
    job = {
        "id": job_id,
        "name": "synthetic display name",
        "prompt": "fixture-task-alpha",
        "schedule": {
            "kind": "interval",
            "minutes": 15,
            "display": "every fifteen synthetic minutes",
        },
        "deliver": "local",
        "origin": None,
        "skills": ["fixture-skill-a", "fixture-skill-b"],
        "workdir": "/fixture/workspace",
        "provider": "fixture-provider",
        "provider_snapshot": None,
        "model": "fixture-model",
        "model_snapshot": None,
        "base_url": "https://fixture.invalid/v1/",
        "context_from": ["fixture-dependency"],
        "enabled_toolsets": ["terminal", "file"],
        "attach_to_session": False,
        "no_agent": False,
        "enabled": True,
        "state": "scheduled",
        "created_at": "synthetic-created-a",
        "next_run_at": "synthetic-next-a",
        "last_run_at": "synthetic-last-a",
        "last_status": "synthetic-status-a",
        "owner_evidence": [
            {"principal": "fixture-principal", "ref": "fixture-evidence-a"}
        ],
    }
    job.update(overrides)
    return job


def _records(jobs: list[dict]) -> list[dict]:
    result = audit_jobs(jobs)
    assert set(result) == {"records", "duplicate_groups"}
    return result["records"]


def test_fixture_is_synthetic_and_exact_duplicates_group() -> None:
    fixture_bytes = FIXTURE.read_bytes()
    lowered = fixture_bytes.lower()
    assert b"/users/" not in lowered
    assert b"/home/" not in lowered
    assert b"api_key" not in lowered
    assert b"token" not in lowered
    assert b"password" not in lowered

    jobs = json.loads(fixture_bytes)["jobs"]
    result = audit_jobs(jobs)
    records = {record["job_id"]: record for record in result["records"]}

    assert records["fixture-a"]["cron_identity"] == records["fixture-b"]["cron_identity"]
    assert records["fixture-a"]["duplicate_group"] == records["fixture-b"]["duplicate_group"]
    assert records["fixture-a"]["duplicate_group"] is not None
    assert records["fixture-c"]["duplicate_group"] is None
    assert result["duplicate_groups"] == [
        {
            "duplicate_group": records["fixture-a"]["duplicate_group"],
            "cron_identity": records["fixture-a"]["cron_identity"],
            "job_ids": ["fixture-a", "fixture-b"],
        }
    ]


def test_identity_excludes_name_enabled_and_mutable_run_fields() -> None:
    first = _job("a")
    second = _job(
        "b",
        name="different synthetic display",
        enabled=False,
        state="paused",
        created_at="synthetic-created-b",
        next_run_at="synthetic-next-b",
        last_run_at="synthetic-last-b",
        last_status="synthetic-status-b",
    )

    assert cron_identity(first) == cron_identity(second)
    records = {record["job_id"]: record for record in _records([second, first])}
    assert records["a"]["enabled"] is True
    assert records["a"]["state"] == "scheduled"
    assert records["b"]["enabled"] is False
    assert records["b"]["state"] == "paused"


@pytest.mark.parametrize(
    "change",
    [
        {"prompt": "fixture-task-beta"},
        {"deliver": "fixture-channel-b"},
        {"origin": {"platform": "fixture-platform", "chat_id": "fixture-target-b"}},
        {"context_from": ["fixture-other-dependency"]},
        {"enabled_toolsets": ["terminal"]},
        {"workdir": "/fixture/other-workspace"},
        {"provider": "fixture-other-provider"},
        {"provider_snapshot": "fixture-provider-snapshot"},
        {"model": "fixture-other-model"},
        {"model_snapshot": "fixture-model-snapshot"},
        {"base_url": "https://other-fixture.invalid/v1"},
        {"skills": ["fixture-skill-b", "fixture-skill-a"]},
        {"attach_to_session": True},
    ],
)
def test_behavior_changes_split_identity(change: dict) -> None:
    assert cron_identity(_job("a")) != cron_identity(_job("b", **change))


def test_schedule_is_canonical_and_toolsets_are_order_insensitive() -> None:
    first = _job("a")
    second = _job(
        "b",
        schedule={"display": "different display", "minutes": 15, "kind": "interval"},
        enabled_toolsets=["file", "terminal"],
        base_url="https://fixture.invalid/v1",
    )
    assert cron_identity(first) == cron_identity(second)


def test_target_and_sensitive_body_values_are_never_emitted() -> None:
    prompt_secret = "fixture-body-must-not-appear"
    target_secret = "fixture-target-must-not-appear"
    owner_secret = "fixture-owner-must-not-appear"
    job = _job(
        "safe-output",
        prompt=prompt_secret,
        deliver="origin",
        origin={
            "platform": "fixture-platform",
            "chat_id": target_secret,
            "chat_name": "fixture-display-must-not-appear",
        },
        owner_evidence=[{"principal": owner_secret, "ref": owner_secret}],
    )

    output = json.dumps(audit_jobs([job]), sort_keys=True)
    assert prompt_secret not in output
    assert target_secret not in output
    assert owner_secret not in output
    assert "fixture-display-must-not-appear" not in output


def test_owner_evidence_requires_one_nonconflicting_principal() -> None:
    missing = classify_owner(_job("missing", owner_evidence=[]))
    conflicting = classify_owner(
        _job(
            "conflicting",
            owner_evidence=[
                {"principal": "fixture-principal-a", "ref": "fixture-ref-a"},
                {"principal": "fixture-principal-b", "ref": "fixture-ref-b"},
            ],
        )
    )
    attributed = classify_owner(_job("attributed"))

    assert missing == {"owner_status": "unattributed", "owner_evidence_refs": []}
    assert conflicting["owner_status"] == "unattributed"
    assert len(conflicting["owner_evidence_refs"]) == 2
    assert attributed["owner_status"] == "attributed"
    assert attributed["owner_evidence_refs"]
    assert "fixture-evidence-a" not in json.dumps(attributed)


def test_script_identity_uses_readable_content_not_path(tmp_path: Path) -> None:
    first_path = tmp_path / "first-fixture.sh"
    second_path = tmp_path / "second-fixture.sh"
    first_path.write_text("fixture-script-content\n", encoding="utf-8")
    second_path.write_text("fixture-script-content\n", encoding="utf-8")

    first = _job("a", no_agent=True, prompt="", script=str(first_path))
    second = _job("b", no_agent=True, prompt="", script=str(second_path))
    assert cron_identity(first) == cron_identity(second)

    second_path.write_text("fixture-script-content-changed\n", encoding="utf-8")
    assert cron_identity(first) != cron_identity(second)


def test_unreadable_script_fails_closed(tmp_path: Path) -> None:
    job = _job(
        "unreadable",
        no_agent=True,
        prompt="",
        script=str(tmp_path / "missing-fixture.sh"),
    )
    with pytest.raises(CronIdentityError, match="script content is unreadable"):
        cron_identity(job)
    with pytest.raises(CronIdentityError, match="script content is unreadable"):
        audit_jobs([job])


def test_output_is_input_order_independent_and_records_have_exact_contract() -> None:
    jobs = [_job("c", prompt="fixture-task-c"), _job("a"), _job("b")]
    forward = audit_jobs(jobs)
    reverse = audit_jobs(list(reversed(jobs)))

    assert forward == reverse
    assert [record["job_id"] for record in forward["records"]] == ["a", "b", "c"]
    assert all(set(record) == RECORD_FIELDS for record in forward["records"])
    assert {record["recommended_action"] for record in forward["records"]} <= {
        "keep",
        "convert",
        "deduplicate",
        "pause_unknown",
        "delete_candidate",
    }


def test_audit_does_not_mutate_jobs_or_serialized_job_store_bytes() -> None:
    jobs = [_job("b"), _job("a")]
    before_object = copy.deepcopy(jobs)
    before_bytes = json.dumps(jobs, separators=(",", ":"), ensure_ascii=False).encode()

    audit_jobs(jobs)

    after_bytes = json.dumps(jobs, separators=(",", ":"), ensure_ascii=False).encode()
    assert jobs == before_object
    assert after_bytes == before_bytes


def test_audit_makes_zero_model_or_api_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__
    forbidden = {"openai", "anthropic", "httpx", "requests", "run_agent"}
    attempted: list[str] = []

    def guarded_import(name: str, *args: object, **kwargs: object):
        if name.split(".", 1)[0] in forbidden:
            attempted.append(name)
            raise AssertionError(f"forbidden audit import: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    audit_jobs([_job("zero-calls")])
    assert attempted == []
