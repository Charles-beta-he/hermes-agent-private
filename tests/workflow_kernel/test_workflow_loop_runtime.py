from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from workflow_kernel.workflow_definition import compile_workflow_definition_v1
from workflow_kernel.workflow_loop_event_ledger import derive_workflow_loop_event_id_v1
from workflow_kernel.workflow_loop_runtime import (
    observe_workflow_loop_shadow_v1,
    registry_snapshot,
    validate_registry,
)

FIXTURE = (
    Path(__file__).parents[1]
    / "contracts"
    / "fixtures"
    / "workflow-definition-v1"
    / "cases.json"
)


def _plan() -> dict[str, Any]:
    import json

    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return compile_workflow_definition_v1(copy.deepcopy(cases[0]["value"]))


def _events(plan: dict[str, Any]) -> list[dict[str, Any]]:
    specs = [
        ("task_started_observed", "inspect"),
        ("task_passed_observed", "inspect"),
        ("task_started_observed", "plan"),
        ("task_passed_observed", "plan"),
        ("task_started_observed", "implement"),
        ("task_passed_observed", "implement"),
        ("task_started_observed", "verify"),
        ("task_passed_observed", "verify"),
    ]
    events: list[dict[str, Any]] = []
    previous = None
    for sequence, (kind, task_id) in enumerate(specs, start=1):
        signal = {
            "schema_version": "workflow_loop_signal_v1",
            "signal_id": f"runtime-signal-{sequence}",
            "kind": kind,
            "expected_sequence": sequence - 1,
            "task_id": task_id,
            "reason": "Local shadow observation fixture.",
        }
        body = {
            "schema_version": "workflow_loop_event_v1",
            "run_id": "runtime-shadow-run",
            "session_owner": "runtime-shadow-owner",
            "sequence": sequence,
            "previous_event_id": previous,
            "idempotency_key": f"runtime-shadow-run:{sequence}",
            "occurred_at": f"2026-07-21T00:00:0{sequence}Z",
            "expires_at": "2026-07-21T01:00:00Z",
            "definition_digest": plan["definition_digest"],
            "plan_digest": plan["plan_digest"],
            "signal": signal,
        }
        event = {**body, "event_id": derive_workflow_loop_event_id_v1(body)}
        events.append(event)
        previous = event["event_id"]
    return events


def test_registry_is_default_off_authority_neutral_and_valid() -> None:
    registry = registry_snapshot()

    assert validate_registry(registry) == {"status": "PASS", "errors": []}
    assert registry["maturity"] == "registered"
    assert registry["mode"] == "local_shadow"
    assert registry["default_enabled"] is False
    assert registry["authority_granted"] is False
    assert registry["executable"] is False
    assert registry["external_side_effects_enabled"] is False


def test_registry_rejects_opened_authority_flags() -> None:
    registry = registry_snapshot()
    registry["default_enabled"] = True
    registry["authority_granted"] = True
    registry["denied_operations"] = []

    report = validate_registry(registry)

    assert report["status"] == "FAIL"
    assert set(report["errors"]) == {
        "authority_granted_must_be_false",
        "default_enabled_must_be_false",
        "denied_operations_incomplete",
    }


def test_shadow_observation_appends_replays_and_reads_back(tmp_path: Path) -> None:
    plan = _plan()
    root = tmp_path / "private"
    events = _events(plan)

    report = observe_workflow_loop_shadow_v1(
        plan,
        events,
        private_root=root,
        ledger_path=root / "workflow-loop" / "events.jsonl",
    )

    assert report["status"] == "OBSERVED_LOCAL_SHADOW"
    assert report["mode"] == "local_shadow"
    assert report["event_count"] == 8
    assert report["final_sequence"] == 8
    assert report["final_workflow_state"] == "passed"
    assert report["plan_digest"] == plan["plan_digest"]
    assert report["authority_granted"] is False
    assert report["executable"] is False
    assert report["external_side_effects_enabled"] is False
    assert report["readback_verified"] is True


def test_shadow_observation_requires_valid_closed_registry(tmp_path: Path) -> None:
    plan = _plan()
    registry = registry_snapshot()
    registry["authority_granted"] = True

    import pytest

    with pytest.raises(ValueError, match="registry validation failed"):
        observe_workflow_loop_shadow_v1(
            plan,
            _events(plan),
            private_root=tmp_path / "private",
            ledger_path=tmp_path / "private" / "events.jsonl",
            registry=registry,
        )
