from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from hermes_cli.personal_cognitive_reconciler import (
    ReconciliationValidationError,
    cognitive_schema,
    reconcile_packet as _reconcile_impl,
    trusted_row_binding,
)

NOW = "2026-07-29T00:00:00+00:00"


def _scope(kind: str = "project", ref: str | None = "project:agent-os") -> dict:
    return {"kind": kind, "ref": ref}


def _base_packet(*, observed_value: int = 4, observed_at: str = "2026-07-28T23:59:30+00:00") -> dict:
    return {
        "schema": "PersonalSemanticReconcileInput.v1",
        "intent_id": "intent:stable-reconcile",
        "now": NOW,
        "assertions": [
            {
                "assertion_id": "assertion:explicit-preference",
                "subject_ref": "user:owner",
                "predicate": "decision.prefer_capability",
                "object": "capability:repair-b",
                "epistemic_status": "explicit",
                "scope": _scope(),
                "source_ref": "memory:user:preference",
                "source_owner": "user_profile",
                "asserted_by": "user",
                "observed_at": None,
                "valid_from": "2026-07-01T00:00:00+00:00",
                "valid_until": None,
                "confidence": 1.0,
                "revision_of": None,
                "authority_effect": "none",
            }
        ],
        "parent_goal": None,
        "goal": {
            "goal_id": "goal:healthy-capability-graph",
            "owner_ref": "user:owner",
            "parent_goal_ref": None,
            "subject_ref": "agent-os:capability-graph",
            "predicate": "relationship_edge_count",
            "operator": "gte",
            "desired_value": 5,
            "tolerance": 0,
            "priority": "high",
            "observation_sources": ["probe:capability-graph"],
            "max_observation_age_seconds": 300,
            "scope": _scope(),
            "budget": {"interruptions": 1, "executions": 1, "cost_units": 10},
            "side_effect_ceiling": "S2",
            "cooldown_seconds": 60,
            "stop_conditions": ["authority_missing", "source_stale"],
            "revision": "r1",
        },
        "observations": [
            {
                "observation_id": "observation:edge-count-before",
                "subject_ref": "agent-os:capability-graph",
                "predicate": "relationship_edge_count",
                "value": observed_value,
                "observed_at": observed_at,
                "source_ref": "probe:capability-graph",
                "provenance_ref": "evidence:probe-before",
            }
        ],
        "action_contracts": [
            {
                "action_id": "action:repair-a",
                "capability_ref": "capability:repair-a",
                "serves_predicates": ["relationship_edge_count"],
                "target_ref": "project:agent-os",
                "expected_effect": "add validated relationship edges",
                "risk_class": "T1",
                "side_effect_level": "S2",
                "required_authority": "local_project_write",
                "preconditions": ["projection_valid"],
                "rollback_method": "git_revert",
                "readback_method": "rebuild_projection_and_count_edges",
                "estimated_cost_units": 1,
            },
            {
                "action_id": "action:repair-b",
                "capability_ref": "capability:repair-b",
                "serves_predicates": ["relationship_edge_count"],
                "target_ref": "project:agent-os",
                "expected_effect": "add validated relationship edges with provenance",
                "risk_class": "T1",
                "side_effect_level": "S2",
                "required_authority": "local_project_write",
                "preconditions": ["projection_valid"],
                "rollback_method": "git_revert",
                "readback_method": "rebuild_projection_and_count_edges",
                "estimated_cost_units": 1,
            },
        ],
        "authority_grants": [],
        "receipts": [],
    }


def _exact_grant() -> dict:
    return {
        "grant_id": "grant:exact-project-repair",
        "granted_by": "user",
        "capability_ref": "capability:repair-b",
        "target_ref": "project:agent-os",
        "side_effect_level": "S2",
        "scope": _scope(),
        "issued_at": "2026-07-28T23:00:00+00:00",
        "expires_at": "2026-07-29T01:00:00+00:00",
        "budget": {"executions": 1, "cost_units": 10},
        "provenance_ref": "authority_ledger:grant:exact-project-repair",
        "status": "active",
    }


def _finding_codes(exc: ReconciliationValidationError) -> set[str]:
    return {item["code"] for item in exc.findings}


class _HostileReprKey:
    def __repr__(self) -> str:
        raise RuntimeError("repr must not be called")


class _HostileFormatString(str):
    def __format__(self, format_spec: str) -> str:
        raise RuntimeError("format must not be called")


def _test_trusted_bindings(entity_type: str, rows: list[Any]) -> frozenset[str]:
    bindings: list[str] = []
    for row in rows:
        if type(row) is not dict:
            continue
        try:
            bindings.append(trusted_row_binding(entity_type, row))
        except (TypeError, ValueError, RecursionError):
            # Structural validation must remain the component that reports malformed rows.
            continue
    return frozenset(bindings)


def _reconcile(packet: Any) -> dict[str, Any]:
    if type(packet) is not dict:
        evaluation_time = datetime.fromisoformat(NOW)
        trusted_preferences: frozenset[str] = frozenset()
        trusted_grants: frozenset[str] = frozenset()
        trusted_receipts: frozenset[str] = frozenset()
        trusted_post_action_observations: frozenset[str] = frozenset()
    else:
        evaluation_time = datetime.fromisoformat(packet.get("now", NOW).replace("Z", "+00:00"))
        trusted_preferences = _test_trusted_bindings("SemanticAssertion", packet.get("assertions", []))
        trusted_grants = _test_trusted_bindings("AuthorityGrant", packet.get("authority_grants", []))
        trusted_receipts = _test_trusted_bindings("ExecutionReceipt", packet.get("receipts", []))
        trusted_post_action_observations = _test_trusted_bindings("Observation", packet.get("observations", []))
    return _reconcile_impl(
        packet,
        evaluation_time=evaluation_time,
        trusted_observation_bindings=trusted_post_action_observations,
        trusted_preference_assertion_bindings=trusted_preferences,
        trusted_grant_bindings=trusted_grants,
        trusted_receipt_bindings=trusted_receipts,
        trusted_post_action_observation_bindings=trusted_post_action_observations,
    )


def _deterministic_id(prefix: str, *parts: Any) -> str:
    canonical = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return f"{prefix}:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:24]}"


def _run_cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "HERMES_HOME": str(tmp_path / "hermes-home")}
    return subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", *args],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cognitive_schema_exposes_stable_read_only_contract() -> None:
    payload = cognitive_schema()
    assert payload["ok"] is True
    assert payload["schema"] == "PersonalSemanticGoalReconciler.v1"
    assert payload["result_schema"] == "PersonalSemanticReconciliationResult.v1"
    assert payload["boundary"] == {
        "runtime_mutation": False,
        "capability_execution": False,
        "authority_expansion": False,
        "external_side_effects": False,
        "durable_semantic_writeback": False,
    }
    assert "authority_effect_must_be_none" in payload["invariants"]


def test_fresh_observation_within_tolerance_converges_without_candidate() -> None:
    result = _reconcile(_base_packet(observed_value=5))
    assert result["ok"] is True
    assert result["state"] == "converged"
    assert result["gap"] is None
    assert result["candidates"] == []
    assert result["intention"] is None
    assert result["boundary"]["capability_execution"] is False


def test_stale_observation_requires_fresh_observation() -> None:
    result = _reconcile(_base_packet(observed_at="2026-07-28T22:00:00+00:00"))
    assert result["state"] == "needs_observation"
    assert result["selected_observation_ref"] is None
    assert result["gap"]["gap_type"] == "missing_observation"
    assert result["candidates"] == []
    assert result["intention"] is None


def test_packet_local_current_observation_cannot_drive_state_without_out_of_band_trust() -> None:
    packet = _base_packet()
    result = _reconcile_impl(packet, evaluation_time=datetime.fromisoformat(NOW))
    assert result["state"] == "needs_observation"
    assert result["selected_observation_ref"] is None
    assert result["intention"] is None


def test_packet_now_cannot_roll_back_trusted_freshness_clock() -> None:
    packet = _base_packet(observed_at="2020-01-01T00:00:00+00:00", observed_value=5)
    packet["now"] = "2020-01-01T00:00:30+00:00"
    packet["assertions"][0]["observed_at"] = "2020-01-01T00:00:00+00:00"
    result = _reconcile_impl(packet, evaluation_time=datetime.fromisoformat(NOW))
    assert result["state"] == "needs_observation"
    assert result["selected_observation_ref"] is None


def test_intention_selected_at_uses_trusted_evaluation_time_not_packet_now() -> None:
    packet = _base_packet()
    packet["now"] = "2020-01-01T00:00:30+00:00"
    result = _reconcile_impl(
        packet,
        evaluation_time=datetime.fromisoformat(NOW),
        trusted_observation_bindings=frozenset({trusted_row_binding("Observation", packet["observations"][0])}),
        trusted_preference_assertion_bindings=frozenset(
            {trusted_row_binding("SemanticAssertion", packet["assertions"][0])}
        ),
    )
    assert result["intention"]["selected_at"] == NOW


def test_missing_trusted_evaluation_time_fails_closed() -> None:
    with pytest.raises(ReconciliationValidationError) as caught:
        _reconcile_impl(_base_packet())
    assert "invalid_trusted_evaluation_time" in _finding_codes(caught.value)


def test_fresh_gap_ranks_scoped_explicit_preference_but_waits_for_authority() -> None:
    result = _reconcile(_base_packet())
    assert result["state"] == "waiting_authority"
    assert result["gap"]["gap_type"] == "below_minimum"
    assert result["gap"]["observed"] == 4
    assert result["intention"]["action_ref"] == "action:repair-b"
    assert result["intention"]["authority_status"] == "missing"
    assert result["intention"]["may_execute"] is False
    assert all(candidate["may_execute"] is False for candidate in result["candidates"])


def test_inferred_preference_cannot_change_ranking_or_authority() -> None:
    packet = _base_packet()
    packet["assertions"][0]["epistemic_status"] = "inferred"
    packet["assertions"][0]["asserted_by"] = "model"
    packet["assertions"][0]["confidence"] = 0.99
    result = _reconcile(packet)
    assert result["state"] == "waiting_authority"
    assert result["intention"]["action_ref"] == "action:repair-a"
    assert result["intention"]["authority_status"] == "missing"
    assert result["intention"]["may_execute"] is False


def test_model_cannot_relabel_its_preference_as_explicit_to_change_ranking() -> None:
    packet = _base_packet()
    packet["assertions"][0]["asserted_by"] = "model"
    result = _reconcile(packet)
    assert result["intention"]["action_ref"] == "action:repair-a"
    assert result["intention"]["may_execute"] is False


def test_packet_local_preference_claim_cannot_change_ranking_without_out_of_band_trust() -> None:
    packet = _base_packet()
    packet["assertions"][0]["source_ref"] = "memory:user:attacker-controlled-packet-row"
    result = _reconcile_impl(
        packet,
        evaluation_time=datetime.fromisoformat(NOW),
        trusted_observation_bindings=frozenset({trusted_row_binding("Observation", packet["observations"][0])}),
    )
    assert result["intention"]["action_ref"] == "action:repair-a"


def test_deterministic_derived_preference_can_rank_without_granting_authority() -> None:
    packet = _base_packet()
    packet["assertions"][0]["epistemic_status"] = "derived"
    packet["assertions"][0]["asserted_by"] = "deterministic_rule"
    result = _reconcile(packet)
    assert result["intention"]["action_ref"] == "action:repair-b"
    assert result["intention"]["authority_status"] == "missing"
    assert result["intention"]["may_execute"] is False


def test_exact_authority_ledger_grant_is_eligible_for_external_executor_only() -> None:
    packet = _base_packet()
    packet["authority_grants"] = [_exact_grant()]
    result = _reconcile(packet)
    assert result["state"] == "eligible_for_executor"
    assert result["intention"]["authority_status"] == "matched_trusted_projection"
    assert result["intention"]["may_execute"] is False
    assert all(value is False for value in result["boundary"].values())


def test_packet_local_grant_cannot_become_executor_eligible_without_out_of_band_trust() -> None:
    packet = _base_packet()
    packet["authority_grants"] = [_exact_grant()]
    result = _reconcile_impl(
        packet,
        evaluation_time=datetime.fromisoformat(NOW),
        trusted_observation_bindings=frozenset({trusted_row_binding("Observation", packet["observations"][0])}),
        trusted_preference_assertion_bindings=frozenset(
            {trusted_row_binding("SemanticAssertion", packet["assertions"][0])}
        ),
    )
    assert result["state"] == "waiting_authority"
    assert result["intention"]["authority_status"] == "untrusted_projection"
    assert result["intention"]["may_execute"] is False


@pytest.mark.parametrize(
    ("mutator", "expected_status"),
    [
        (lambda grant: grant.update({"expires_at": "2026-07-28T23:30:00+00:00"}), "expired"),
        (lambda grant: grant.update({"status": "revoked"}), "revoked"),
        (lambda grant: grant.update({"target_ref": "project:other"}), "non_exact"),
        (lambda grant: grant.update({"budget": {"executions": 0, "cost_units": 0}}), "under_budget"),
        (lambda grant: grant.update({"provenance_ref": "memory:claimed-grant"}), "untrusted_provenance"),
    ],
)
def test_non_exact_or_untrusted_grants_fail_closed(mutator, expected_status: str) -> None:
    packet = _base_packet()
    grant = _exact_grant()
    mutator(grant)
    packet["authority_grants"] = [grant]
    result = _reconcile(packet)
    assert result["state"] == "waiting_authority"
    assert result["intention"]["authority_status"] == expected_status
    assert result["intention"]["may_execute"] is False


def test_completed_receipt_without_post_action_observation_is_uncertain() -> None:
    packet = _base_packet()
    first = _reconcile(packet)
    packet["receipts"] = [
        {
            "receipt_id": "receipt:repair-b",
            "intention_ref": first["intention"]["intention_id"],
            "action_ref": "action:repair-b",
            "status": "completed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:tool-return"],
            "post_action_observation_refs": [],
        }
    ]
    result = _reconcile(packet)
    assert result["state"] == "uncertain"
    assert result["receipt_evaluation"] == "uncertain_without_readback"
    assert result["intention"]["may_execute"] is False


def test_packet_local_receipt_cannot_block_selected_intention_without_out_of_band_trust() -> None:
    packet = _base_packet()
    first = _reconcile(packet)
    packet["receipts"] = [
        {
            "receipt_id": "receipt:forged-failure",
            "intention_ref": first["intention"]["intention_id"],
            "action_ref": first["intention"]["action_ref"],
            "status": "failed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:forged"],
            "post_action_observation_refs": [],
        }
    ]
    result = _reconcile_impl(
        packet,
        evaluation_time=datetime.fromisoformat(NOW),
        trusted_observation_bindings=frozenset({trusted_row_binding("Observation", packet["observations"][0])}),
        trusted_preference_assertion_bindings=frozenset(
            {trusted_row_binding("SemanticAssertion", packet["assertions"][0])}
        ),
    )
    assert result["state"] == "waiting_authority"
    assert result["receipt_evaluation"] == "none"


def test_pre_execution_converged_observation_cannot_close_exact_receipt_without_readback() -> None:
    packet = _base_packet()
    first = _reconcile(packet)
    packet["observations"][0]["value"] = 5
    packet["receipts"] = [
        {
            "receipt_id": "receipt:after-preexisting-convergence",
            "intention_ref": first["intention"]["intention_id"],
            "action_ref": first["intention"]["action_ref"],
            "status": "completed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:execution"],
            "post_action_observation_refs": [],
        }
    ]
    result = _reconcile(packet)
    assert result["state"] == "uncertain"
    assert result["receipt_evaluation"] == "uncertain_without_readback"


def test_failed_exact_receipt_blocks_pre_execution_converged_observation() -> None:
    packet = _base_packet()
    first = _reconcile(packet)
    packet["observations"][0]["value"] = 5
    packet["receipts"] = [
        {
            "receipt_id": "receipt:failed-after-preexisting-convergence",
            "intention_ref": first["intention"]["intention_id"],
            "action_ref": first["intention"]["action_ref"],
            "status": "failed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:execution-failed"],
            "post_action_observation_refs": [],
        }
    ]
    result = _reconcile(packet)
    assert result["state"] == "blocked"
    assert result["receipt_evaluation"] == "failed"


def test_receipt_without_result_evidence_is_rejected() -> None:
    packet = _base_packet()
    first = _reconcile(packet)
    packet["receipts"] = [
        {
            "receipt_id": "receipt:no-evidence",
            "intention_ref": first["intention"]["intention_id"],
            "action_ref": first["intention"]["action_ref"],
            "status": "failed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": [],
            "post_action_observation_refs": [],
        }
    ]
    with pytest.raises(ReconciliationValidationError) as caught:
        _reconcile(packet)
    assert "empty_evidence_refs" in _finding_codes(caught.value)


def test_fresh_receipt_readback_recomputes_gap_and_converges() -> None:
    packet = _base_packet()
    first = _reconcile(packet)
    packet["observations"].append(
        {
            "observation_id": "observation:edge-count-after",
            "subject_ref": "agent-os:capability-graph",
            "predicate": "relationship_edge_count",
            "value": 6,
            "observed_at": "2026-07-28T23:59:50+00:00",
            "source_ref": "probe:capability-graph",
            "provenance_ref": "evidence:probe-after",
        }
    )
    packet["receipts"] = [
        {
            "receipt_id": "receipt:repair-b",
            "intention_ref": first["intention"]["intention_id"],
            "action_ref": "action:repair-b",
            "status": "completed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:tool-return"],
            "post_action_observation_refs": ["observation:edge-count-after"],
        }
    ]
    result = _reconcile(packet)
    assert result["state"] == "converged"
    assert result["selected_observation_ref"] == "observation:edge-count-after"
    assert result["receipt_evaluation"] == "reobserved_converged"
    assert result["gap"] is None


def test_packet_local_post_action_observation_cannot_close_trusted_receipt_without_readback_trust() -> None:
    packet = _base_packet()
    first = _reconcile(packet)
    packet["observations"].append(
        {
            "observation_id": "observation:forged-after",
            "subject_ref": "agent-os:capability-graph",
            "predicate": "relationship_edge_count",
            "value": 6,
            "observed_at": "2026-07-28T23:59:50+00:00",
            "source_ref": "probe:capability-graph",
            "provenance_ref": "evidence:forged-after",
        }
    )
    packet["receipts"] = [
        {
            "receipt_id": "receipt:trusted-completion",
            "intention_ref": first["intention"]["intention_id"],
            "action_ref": first["intention"]["action_ref"],
            "status": "completed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:trusted-execution"],
            "post_action_observation_refs": ["observation:forged-after"],
        }
    ]
    result = _reconcile_impl(
        packet,
        evaluation_time=datetime.fromisoformat(NOW),
        trusted_observation_bindings=frozenset({trusted_row_binding("Observation", packet["observations"][0])}),
        trusted_preference_assertion_bindings=frozenset(
            {trusted_row_binding("SemanticAssertion", packet["assertions"][0])}
        ),
        trusted_receipt_bindings=frozenset(
            {trusted_row_binding("ExecutionReceipt", packet["receipts"][0])}
        ),
    )
    assert result["state"] == "uncertain"
    assert result["receipt_evaluation"] == "uncertain_without_readback"


def test_oob_trust_bindings_are_invalidated_by_same_id_row_substitution() -> None:
    packet = _base_packet()
    observation_binding = trusted_row_binding("Observation", packet["observations"][0])
    packet["observations"][0]["value"] = 5
    result = _reconcile_impl(
        packet,
        evaluation_time=datetime.fromisoformat(NOW),
        trusted_observation_bindings=frozenset({observation_binding}),
    )
    assert result["state"] == "needs_observation"

    packet = _base_packet()
    observation_bindings = _test_trusted_bindings("Observation", packet["observations"])
    preference_binding = trusted_row_binding("SemanticAssertion", packet["assertions"][0])
    packet["assertions"][0]["object"] = "capability:repair-a"
    result = _reconcile_impl(
        packet,
        evaluation_time=datetime.fromisoformat(NOW),
        trusted_observation_bindings=observation_bindings,
        trusted_preference_assertion_bindings=frozenset({preference_binding}),
    )
    assert result["intention"]["action_ref"] == "action:repair-a"

    packet = _base_packet()
    packet["authority_grants"] = [_exact_grant()]
    grant_binding = trusted_row_binding("AuthorityGrant", packet["authority_grants"][0])
    packet["authority_grants"][0]["provenance_ref"] = "authority_ledger:substituted"
    result = _reconcile_impl(
        packet,
        evaluation_time=datetime.fromisoformat(NOW),
        trusted_observation_bindings=_test_trusted_bindings("Observation", packet["observations"]),
        trusted_preference_assertion_bindings=_test_trusted_bindings("SemanticAssertion", packet["assertions"]),
        trusted_grant_bindings=frozenset({grant_binding}),
    )
    assert result["state"] == "waiting_authority"
    assert result["intention"]["authority_status"] == "untrusted_projection"

    packet = _base_packet()
    first = _reconcile(packet)
    packet["receipts"] = [
        {
            "receipt_id": "receipt:same-id",
            "intention_ref": first["intention"]["intention_id"],
            "action_ref": first["intention"]["action_ref"],
            "status": "completed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:execution"],
            "post_action_observation_refs": [],
        }
    ]
    receipt_binding = trusted_row_binding("ExecutionReceipt", packet["receipts"][0])
    packet["receipts"][0]["status"] = "failed"
    result = _reconcile_impl(
        packet,
        evaluation_time=datetime.fromisoformat(NOW),
        trusted_observation_bindings=_test_trusted_bindings("Observation", packet["observations"]),
        trusted_preference_assertion_bindings=_test_trusted_bindings("SemanticAssertion", packet["assertions"]),
        trusted_receipt_bindings=frozenset({receipt_binding}),
    )
    assert result["receipt_evaluation"] == "none"
    assert result["state"] == "waiting_authority"

    packet = _base_packet()
    first = _reconcile(packet)
    readback = {
        "observation_id": "observation:same-id-readback",
        "subject_ref": packet["goal"]["subject_ref"],
        "predicate": packet["goal"]["predicate"],
        "value": 4,
        "observed_at": "2026-07-28T23:59:50+00:00",
        "source_ref": "probe:capability-graph",
        "provenance_ref": "evidence:readback",
    }
    later = {**readback, "observation_id": "observation:later", "value": 5, "observed_at": "2026-07-28T23:59:55+00:00"}
    packet["observations"] += [readback, later]
    packet["receipts"] = [
        {
            "receipt_id": "receipt:readback-substitution",
            "intention_ref": first["intention"]["intention_id"],
            "action_ref": first["intention"]["action_ref"],
            "status": "completed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:execution"],
            "post_action_observation_refs": [readback["observation_id"]],
        }
    ]
    post_binding = trusted_row_binding("Observation", readback)
    receipt_binding = trusted_row_binding("ExecutionReceipt", packet["receipts"][0])
    observation_bindings = _test_trusted_bindings("Observation", packet["observations"])
    readback["value"] = 5
    result = _reconcile_impl(
        packet,
        evaluation_time=datetime.fromisoformat(NOW),
        trusted_observation_bindings=observation_bindings,
        trusted_preference_assertion_bindings=_test_trusted_bindings("SemanticAssertion", packet["assertions"]),
        trusted_receipt_bindings=frozenset({receipt_binding}),
        trusted_post_action_observation_bindings=frozenset({post_binding}),
    )
    assert result["state"] == "uncertain"
    assert result["receipt_evaluation"] == "uncertain_without_readback"


def test_candidate_identity_is_stable_across_gap_reobservation() -> None:
    packet = _base_packet()
    first = _reconcile(packet)
    first_ids = [item["candidate_id"] for item in first["candidates"]]
    packet["observations"][0]["value"] = 3
    packet["observations"][0]["observed_at"] = "2026-07-28T23:59:50+00:00"
    second = _reconcile(packet)
    assert [item["candidate_id"] for item in second["candidates"]] == first_ids
    assert second["intention"]["intention_id"] == first["intention"]["intention_id"]
    assert second["gap"]["gap_id"] != first["gap"]["gap_id"]


def test_candidate_identity_is_stable_across_equivalent_event_intents() -> None:
    packet = _base_packet()
    first = _reconcile(packet)
    packet["intent_id"] = "intent:equivalent-replay"
    second = _reconcile(packet)
    assert [item["candidate_id"] for item in second["candidates"]] == [
        item["candidate_id"] for item in first["candidates"]
    ]
    assert second["intention"]["intention_id"] != first["intention"]["intention_id"]


def test_candidate_identity_binds_complete_action_semantics() -> None:
    packet = _base_packet()
    first = _reconcile(packet)
    first_by_action = {item["action_ref"]: item["candidate_id"] for item in first["candidates"]}
    packet["action_contracts"][1]["expected_effect"] = "different evidenced effect"
    second = _reconcile(packet)
    second_by_action = {item["action_ref"]: item["candidate_id"] for item in second["candidates"]}
    assert second_by_action["action:repair-a"] == first_by_action["action:repair-a"]
    assert second_by_action["action:repair-b"] != first_by_action["action:repair-b"]


def test_receipt_for_superseded_action_semantics_cannot_affect_successor_intention() -> None:
    packet = _base_packet()
    first = _reconcile(packet)
    first_intention = first["intention"]
    packet["action_contracts"][1]["expected_effect"] = "different evidenced effect"
    packet["receipts"] = [
        {
            "receipt_id": "receipt:superseded-action",
            "intention_ref": first_intention["intention_id"],
            "action_ref": first_intention["action_ref"],
            "status": "failed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:old-contract-failure"],
            "post_action_observation_refs": [],
        }
    ]
    second = _reconcile(packet)
    assert second["intention"]["intention_id"] != first_intention["intention_id"]
    assert second["state"] == "waiting_authority"
    assert second["receipt_evaluation"] == "none"


def test_action_target_outside_non_global_goal_scope_is_rejected() -> None:
    packet = _base_packet()
    packet["action_contracts"][0]["target_ref"] = "project:other"
    with pytest.raises(ReconciliationValidationError) as caught:
        _reconcile(packet)
    assert "action_outside_goal_scope" in _finding_codes(caught.value)


def test_global_goal_scope_does_not_imply_a_single_target_reference() -> None:
    packet = _base_packet()
    packet["goal"]["scope"] = {"kind": "global", "ref": None}
    result = _reconcile(packet)
    assert result["state"] == "waiting_authority"


def test_mismatched_receipt_action_cannot_affect_current_intention() -> None:
    packet = _base_packet()
    first = _reconcile(packet)
    packet["receipts"] = [
        {
            "receipt_id": "receipt:mismatch",
            "intention_ref": first["intention"]["intention_id"],
            "action_ref": "action:repair-a",
            "status": "completed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:receipt"],
            "post_action_observation_refs": [],
        }
    ]
    result = _reconcile(packet)
    assert result["state"] == "waiting_authority"
    assert result["receipt_evaluation"] == "none"


def test_receipt_for_nonselected_candidate_cannot_affect_current_intention_or_budget() -> None:
    packet = _base_packet()
    packet["assertions"][0]["asserted_by"] = "model"
    action_a_projection = _reconcile(packet)
    assert action_a_projection["intention"]["action_ref"] == "action:repair-a"

    packet["assertions"][0]["asserted_by"] = "user"
    packet["authority_grants"] = [_exact_grant()]
    packet["receipts"] = [
        {
            "receipt_id": "receipt:nonselected",
            "intention_ref": action_a_projection["intention"]["intention_id"],
            "action_ref": "action:repair-a",
            "status": "failed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:receipt"],
            "post_action_observation_refs": [],
        }
    ]
    result = _reconcile(packet)
    assert result["intention"]["action_ref"] == "action:repair-b"
    assert result["state"] == "eligible_for_executor"
    assert result["intention"]["authority_status"] == "matched_trusted_projection"
    assert result["receipt_evaluation"] == "none"


def test_forged_nonselected_intention_id_cannot_attach_after_ranking_change() -> None:
    packet = _base_packet()
    initial = _reconcile(packet)
    nonselected = next(
        candidate
        for candidate in initial["candidates"]
        if candidate["candidate_id"] != initial["intention"]["candidate_ref"]
    )
    initial_context = _deterministic_id(
        "selection-context", [candidate["candidate_id"] for candidate in initial["candidates"]]
    )
    forged_nonselected_intention = _deterministic_id(
        "intention", packet["intent_id"], nonselected["candidate_id"], initial_context
    )
    packet["assertions"][0]["asserted_by"] = "model"
    packet["receipts"] = [
        {
            "receipt_id": "receipt:forged-nonselected",
            "intention_ref": forged_nonselected_intention,
            "action_ref": nonselected["action_ref"],
            "status": "failed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:forged"],
            "post_action_observation_refs": [],
        }
    ]
    later = _reconcile(packet)
    assert later["intention"]["action_ref"] == nonselected["action_ref"]
    assert later["intention"]["intention_id"] != forged_nonselected_intention
    assert later["receipt_evaluation"] == "none"
    assert later["state"] == "waiting_authority"


def test_pre_execution_observation_cannot_serve_as_receipt_readback() -> None:
    packet = _base_packet()
    first = _reconcile(packet)
    packet["receipts"] = [
        {
            "receipt_id": "receipt:pre-readback",
            "intention_ref": first["intention"]["intention_id"],
            "action_ref": first["intention"]["action_ref"],
            "status": "completed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:receipt"],
            "post_action_observation_refs": ["observation:edge-count-before"],
        }
    ]
    result = _reconcile(packet)
    assert result["state"] == "uncertain"
    assert result["receipt_evaluation"] == "uncertain_without_readback"


def test_readback_must_be_strictly_later_than_receipt_completion() -> None:
    packet = _base_packet(observed_at="2026-07-28T23:59:45+00:00")
    first = _reconcile(packet)
    packet["receipts"] = [
        {
            "receipt_id": "receipt:same-instant",
            "intention_ref": first["intention"]["intention_id"],
            "action_ref": first["intention"]["action_ref"],
            "status": "completed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:receipt"],
            "post_action_observation_refs": ["observation:edge-count-before"],
        }
    ]
    result = _reconcile(packet)
    assert result["state"] == "uncertain"
    assert result["receipt_evaluation"] == "uncertain_without_readback"


def test_latest_receipt_is_selected_by_instant_not_timestamp_text() -> None:
    packet = _base_packet()
    first = _reconcile(packet)
    common = {
        "intention_ref": first["intention"]["intention_id"],
        "action_ref": first["intention"]["action_ref"],
        "result_evidence_refs": ["evidence:receipt"],
        "post_action_observation_refs": [],
    }
    packet["receipts"] = [
        {
            **common,
            "receipt_id": "receipt:older-offset",
            "status": "completed",
            "started_at": "2026-07-28T23:59:50+01:00",
            "finished_at": "2026-07-29T00:00:00+01:00",
        },
        {
            **common,
            "receipt_id": "receipt:latest-utc",
            "status": "failed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
        },
    ]
    result = _reconcile(packet)
    assert result["state"] == "blocked"
    assert result["receipt_evaluation"] == "failed"


def test_converged_goal_ignores_receipt_for_unrelated_action_contract() -> None:
    packet = _base_packet()
    initial = _reconcile(packet)
    action_a = next(item for item in initial["candidates"] if item["action_ref"] == "action:repair-a")
    packet["action_contracts"][0]["serves_predicates"] = ["unrelated_predicate"]
    packet["observations"][0]["value"] = 6
    packet["observations"][0]["observed_at"] = "2026-07-28T23:59:50+00:00"
    packet["receipts"] = [
        {
            "receipt_id": "receipt:unrelated",
            "intention_ref": action_a["candidate_id"],
            "action_ref": "action:repair-a",
            "status": "completed",
            "started_at": "2026-07-28T23:59:35+00:00",
            "finished_at": "2026-07-28T23:59:40+00:00",
            "result_evidence_refs": ["evidence:receipt"],
            "post_action_observation_refs": ["observation:edge-count-before"],
        }
    ]
    result = _reconcile(packet)
    assert result["state"] == "converged"
    assert result["receipt_evaluation"] == "none"


def test_failed_exact_receipt_blocks_reselection() -> None:
    packet = _base_packet()
    first = _reconcile(packet)
    packet["receipts"] = [
        {
            "receipt_id": "receipt:failed",
            "intention_ref": first["intention"]["intention_id"],
            "action_ref": first["intention"]["action_ref"],
            "status": "failed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:failure"],
            "post_action_observation_refs": [],
        }
    ]
    result = _reconcile(packet)
    assert result["state"] == "blocked"
    assert result["receipt_evaluation"] == "failed"


def test_goal_budget_is_checked_before_projected_grant_match() -> None:
    packet = _base_packet()
    packet["goal"]["budget"]["executions"] = 0
    packet["authority_grants"] = [_exact_grant()]
    result = _reconcile(packet)
    assert result["state"] == "blocked"
    assert result["intention"]["authority_status"] == "goal_budget_exhausted"
    assert result["intention"]["may_execute"] is False


@pytest.mark.parametrize("invalid_object", [float("nan"), float("inf"), ("python", "tuple"), {1: "non-string-key"}])
def test_assertion_object_must_be_json_representable(invalid_object: Any) -> None:
    packet = _base_packet()
    packet["assertions"][0]["object"] = invalid_object
    with pytest.raises(ReconciliationValidationError) as caught:
        _reconcile(packet)
    assert _finding_codes(caught.value) & {"invalid_json_number", "invalid_json_type", "invalid_field_name"}


def test_non_string_packet_key_is_rejected_structurally() -> None:
    packet = _base_packet()
    packet[1] = "invalid"
    with pytest.raises(ReconciliationValidationError) as caught:
        _reconcile(packet)
    assert "invalid_field_name" in _finding_codes(caught.value)


def test_hostile_non_string_key_is_rejected_without_calling_repr() -> None:
    packet = _base_packet()
    packet[_HostileReprKey()] = "invalid"
    with pytest.raises(ReconciliationValidationError) as caught:
        _reconcile(packet)
    assert "invalid_field_name" in _finding_codes(caught.value)


def test_hostile_string_subclass_key_is_rejected_without_calling_format() -> None:
    packet = _base_packet()
    packet["assertions"][0]["object"] = {_HostileFormatString("hostile"): "invalid"}
    with pytest.raises(ReconciliationValidationError) as caught:
        _reconcile(packet)
    assert "invalid_field_name" in _finding_codes(caught.value)


def test_cyclic_assertion_object_is_rejected_structurally() -> None:
    packet = _base_packet()
    cycle: list[Any] = []
    cycle.append(cycle)
    packet["assertions"][0]["object"] = cycle
    with pytest.raises(ReconciliationValidationError) as caught:
        _reconcile(packet)
    assert "invalid_json_cycle" in _finding_codes(caught.value)


def test_overdeep_assertion_object_is_rejected_structurally() -> None:
    packet = _base_packet()
    nested: list[Any] = []
    cursor = nested
    for _ in range(65):
        child: list[Any] = []
        cursor.append(child)
        cursor = child
    packet["assertions"][0]["object"] = nested
    with pytest.raises(ReconciliationValidationError) as caught:
        _reconcile(packet)
    assert "json_depth_limit" in _finding_codes(caught.value)


def test_cognitive_schema_without_json_flag_still_emits_structured_contract(tmp_path: Path) -> None:
    completed = _run_cli(tmp_path, "agent-os", "cognitive-schema")
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["schema"] == "PersonalSemanticGoalReconciler.v1"


def test_unhashable_enum_value_is_structurally_rejected() -> None:
    packet = _base_packet()
    packet["goal"]["operator"] = []
    with pytest.raises(ReconciliationValidationError) as exc_info:
        _reconcile(packet)
    assert "invalid_operator" in _finding_codes(exc_info.value)


@pytest.mark.parametrize("invalid_kind", [[], {}])
def test_unhashable_goal_scope_kind_is_structurally_rejected(invalid_kind: Any) -> None:
    packet = _base_packet()
    packet["goal"]["scope"]["kind"] = invalid_kind
    with pytest.raises(ReconciliationValidationError) as exc_info:
        _reconcile(packet)
    assert "invalid_scope" in _finding_codes(exc_info.value)


def test_non_numeric_quantitative_observation_is_rejected() -> None:
    packet = _base_packet()
    packet["observations"][0]["value"] = "not-a-number"
    with pytest.raises(ReconciliationValidationError) as exc_info:
        _reconcile(packet)
    assert "invalid_number" in _finding_codes(exc_info.value)


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numeric_equality_goal_is_structurally_rejected(non_finite: float) -> None:
    packet = _base_packet()
    packet["goal"]["operator"] = "eq"
    packet["goal"]["desired_value"] = non_finite
    with pytest.raises(ReconciliationValidationError) as exc_info:
        _reconcile(packet)
    assert "invalid_json_number" in _finding_codes(exc_info.value)


@pytest.mark.parametrize("non_finite", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numeric_equality_observation_is_structurally_rejected(non_finite: float) -> None:
    packet = _base_packet()
    packet["goal"]["operator"] = "eq"
    packet["observations"][0]["value"] = non_finite
    with pytest.raises(ReconciliationValidationError) as exc_info:
        _reconcile(packet)
    assert "invalid_json_number" in _finding_codes(exc_info.value)


def test_malformed_parent_budget_fails_closed_without_internal_exception() -> None:
    packet = _base_packet()
    parent = copy.deepcopy(packet["goal"])
    parent["goal_id"] = "goal:parent"
    parent["parent_goal_ref"] = None
    parent["budget"]["executions"] = []
    packet["goal"]["parent_goal_ref"] = "goal:parent"
    packet["parent_goal"] = parent
    with pytest.raises(ReconciliationValidationError) as exc_info:
        _reconcile(packet)
    assert "invalid_integer" in _finding_codes(exc_info.value)


def test_invalid_utf8_input_returns_structured_nonzero_cli_error(tmp_path: Path) -> None:
    packet_path = tmp_path / "invalid-utf8.json"
    packet_path.write_bytes(b"\xff\xfe")
    completed = _run_cli(tmp_path, "agent-os", "reconcile", "--input", str(packet_path))
    assert completed.returncode != 0
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["findings"][0]["code"] == "input_read_error"


def test_json_integer_digit_limit_returns_structured_nonzero_cli_error(tmp_path: Path) -> None:
    packet_path = tmp_path / "huge-integer.json"
    raw = json.dumps(_base_packet())
    raw = raw.replace('"desired_value": 5', '"desired_value": ' + ('9' * 5000), 1)
    packet_path.write_text(raw, encoding="utf-8")
    completed = _run_cli(tmp_path, "agent-os", "reconcile", "--input", str(packet_path))
    assert completed.returncode != 0
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["findings"][0]["code"] == "input_read_error"


def test_assertion_authority_smuggling_is_rejected() -> None:
    packet = _base_packet()
    packet["assertions"][0]["authority_effect"] = "grant"
    with pytest.raises(ReconciliationValidationError) as caught:
        _reconcile(packet)
    assert "authority_effect_must_be_none" in _finding_codes(caught.value)


def test_missing_assertion_provenance_is_rejected() -> None:
    packet = _base_packet()
    del packet["assertions"][0]["source_ref"]
    with pytest.raises(ReconciliationValidationError) as caught:
        _reconcile(packet)
    assert "missing_field" in _finding_codes(caught.value)


def test_future_assertion_observation_is_rejected() -> None:
    packet = _base_packet()
    packet["assertions"][0]["observed_at"] = "2026-07-29T00:00:01+00:00"
    with pytest.raises(ReconciliationValidationError) as caught:
        _reconcile(packet)
    assert "future_assertion" in _finding_codes(caught.value)


def test_inverted_assertion_validity_window_is_rejected() -> None:
    packet = _base_packet()
    packet["assertions"][0]["valid_until"] = "2026-06-30T00:00:00+00:00"
    with pytest.raises(ReconciliationValidationError) as caught:
        _reconcile(packet)
    assert "invalid_assertion_window" in _finding_codes(caught.value)


def test_scope_widening_child_goal_is_rejected() -> None:
    packet = _base_packet()
    parent = copy.deepcopy(packet["goal"])
    parent.update(
        {
            "goal_id": "goal:parent",
            "parent_goal_ref": None,
            "scope": _scope("task", "task:one"),
            "budget": {"interruptions": 1, "executions": 1, "cost_units": 5},
            "side_effect_ceiling": "S1",
        }
    )
    packet["parent_goal"] = parent
    packet["goal"]["parent_goal_ref"] = "goal:parent"
    packet["goal"]["scope"] = _scope("project", "project:agent-os")
    packet["goal"]["side_effect_ceiling"] = "S2"
    with pytest.raises(ReconciliationValidationError) as caught:
        _reconcile(packet)
    codes = _finding_codes(caught.value)
    assert "child_goal_scope_widening" in codes
    assert "child_goal_side_effect_widening" in codes


def test_action_without_rollback_or_readback_is_rejected() -> None:
    packet = _base_packet()
    del packet["action_contracts"][0]["rollback_method"]
    del packet["action_contracts"][1]["readback_method"]
    with pytest.raises(ReconciliationValidationError) as caught:
        _reconcile(packet)
    assert "missing_field" in _finding_codes(caught.value)


def test_identical_input_produces_identical_semantic_json() -> None:
    packet = _base_packet()
    first = _reconcile(copy.deepcopy(packet))
    second = _reconcile(copy.deepcopy(packet))
    assert first == second
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(second, ensure_ascii=False, sort_keys=True)


def test_huge_integer_is_structurally_rejected_without_overflow() -> None:
    packet = _base_packet()
    packet["goal"]["desired_value"] = 10**400
    with pytest.raises(ReconciliationValidationError) as caught:
        _reconcile(packet)
    assert "invalid_number" in _finding_codes(caught.value)


def test_hostile_python_container_subclass_is_rejected_without_iteration() -> None:
    class HostileList(list):
        def __iter__(self):
            raise RuntimeError("hostile-iter-executed")

    packet = _base_packet()
    packet["goal"]["stop_conditions"] = HostileList(["x"])
    with pytest.raises(ReconciliationValidationError) as caught:
        _reconcile(packet)
    assert "invalid_json_type" in _finding_codes(caught.value)


def test_intention_is_distinct_from_selected_candidate() -> None:
    result = _reconcile(_base_packet())
    candidate = result["candidates"][0]
    intention = result["intention"]
    assert intention != candidate
    assert intention["candidate_ref"] == candidate["candidate_id"]
    assert intention["intention_id"] != candidate["candidate_id"]
    assert intention["action_contract_digest"] == candidate["action_contract_digest"]


def test_receipt_gap_readback_is_not_reported_as_converged_by_independent_observation() -> None:
    packet = _base_packet()
    first = _reconcile(packet)
    packet["receipts"] = [
        {
            "receipt_id": "receipt:gap-readback",
            "intention_ref": first["intention"]["intention_id"],
            "action_ref": first["intention"]["action_ref"],
            "status": "completed",
            "started_at": "2026-07-28T23:59:35+00:00",
            "finished_at": "2026-07-28T23:59:40+00:00",
            "result_evidence_refs": ["evidence:receipt-gap"],
            "post_action_observation_refs": ["observation:receipt-gap"],
        }
    ]
    packet["observations"].extend(
        [
            {
                "observation_id": "observation:receipt-gap",
                "subject_ref": packet["goal"]["subject_ref"],
                "predicate": packet["goal"]["predicate"],
                "value": 4,
                "observed_at": "2026-07-28T23:59:50+00:00",
                "source_ref": "probe:capability-graph",
                "provenance_ref": "evidence:receipt-gap-readback",
            },
            {
                "observation_id": "observation:independent-converged",
                "subject_ref": packet["goal"]["subject_ref"],
                "predicate": packet["goal"]["predicate"],
                "value": 5,
                "observed_at": "2026-07-28T23:59:55+00:00",
                "source_ref": "probe:capability-graph",
                "provenance_ref": "evidence:independent",
            },
        ]
    )
    result = _reconcile(packet)
    assert result["state"] == "uncertain"
    assert result["selected_observation_ref"] == "observation:independent-converged"
    assert result["receipt_evaluation"] == "reobserved_gap"


def _packet_with_completed_gap_receipt(*, now: str, executions: int) -> dict:
    packet = _base_packet()
    packet["goal"]["budget"]["executions"] = executions
    packet["authority_grants"] = [_exact_grant()]
    packet["authority_grants"][0]["budget"]["executions"] = executions
    first = _reconcile(packet)
    packet["now"] = now
    packet["receipts"] = [
        {
            "receipt_id": "receipt:completed-gap",
            "intention_ref": first["intention"]["intention_id"],
            "action_ref": first["intention"]["action_ref"],
            "status": "completed",
            "started_at": "2026-07-28T23:59:35+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:completed-gap"],
            "post_action_observation_refs": ["observation:gap-after"],
        }
    ]
    packet["observations"].append(
        {
            "observation_id": "observation:gap-after",
            "subject_ref": packet["goal"]["subject_ref"],
            "predicate": packet["goal"]["predicate"],
            "value": 4,
            "observed_at": "2026-07-28T23:59:50+00:00",
            "source_ref": "probe:capability-graph",
            "provenance_ref": "evidence:gap-after",
        }
    )
    return packet


def test_completed_execution_exhausts_total_goal_budget() -> None:
    result = _reconcile(_packet_with_completed_gap_receipt(now=NOW, executions=1))
    assert result["receipt_evaluation"] == "reobserved_gap"
    assert result["state"] == "blocked"
    assert result["intention"]["authority_status"] == "goal_budget_exhausted"


def test_completed_execution_respects_cooldown_and_boundary() -> None:
    during = _reconcile(_packet_with_completed_gap_receipt(now=NOW, executions=2))
    assert during["state"] == "blocked"
    assert during["intention"]["authority_status"] == "cooldown"

    boundary_packet = _packet_with_completed_gap_receipt(
        now="2026-07-29T00:00:45+00:00", executions=2
    )
    boundary = _reconcile(boundary_packet)
    assert boundary["receipt_evaluation"] == "reobserved_gap"
    assert boundary["state"] == "eligible_for_executor"
    assert boundary["intention"]["authority_status"] == "matched_trusted_projection"


@pytest.mark.parametrize(
    ("source_owner", "source_ref"),
    [
        ("runtime_probe", "probe:untrusted-claim"),
        ("evidence_store", "evidence:untrusted-claim"),
        ("authority_ledger", "authority_ledger:untrusted-claim"),
        ("mission", "mission:untrusted-claim"),
        ("taskpool", "task:untrusted-claim"),
        ("user_profile", "probe:mismatched-owner-prefix"),
    ],
)
def test_untrusted_source_cannot_change_preference_ranking(source_owner: str, source_ref: str) -> None:
    packet = _base_packet()
    packet["assertions"][0]["source_owner"] = source_owner
    packet["assertions"][0]["source_ref"] = source_ref
    result = _reconcile(packet)
    assert result["intention"]["action_ref"] == "action:repair-a"


def test_cli_huge_integer_and_deep_json_fail_closed_with_structured_output(tmp_path: Path) -> None:
    huge_packet = _base_packet()
    huge_packet["goal"]["desired_value"] = 10**400
    huge_path = tmp_path / "huge.json"
    huge_path.write_text(json.dumps(huge_packet), encoding="utf-8")
    huge_run = _run_cli(tmp_path, "agent-os", "reconcile", "--input", str(huge_path))
    assert huge_run.returncode != 0
    assert json.loads(huge_run.stdout)["state"] == "blocked"
    assert "Traceback" not in huge_run.stderr

    deep_path = tmp_path / "deep.json"
    deep_path.write_text("[" * 1100 + "0" + "]" * 1100, encoding="utf-8")
    deep_run = _run_cli(tmp_path, "agent-os", "reconcile", "--input", str(deep_path))
    assert deep_run.returncode != 0
    assert json.loads(deep_run.stdout)["findings"][0]["code"] == "input_read_error"
    assert "Traceback" not in deep_run.stderr


def test_cli_schema_and_reconcile_are_real_read_only_paths(tmp_path: Path) -> None:
    packet_path = tmp_path / "packet.json"
    packet = _base_packet()
    packet["observations"][0]["observed_at"] = datetime.now(timezone.utc).isoformat()
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    before = hashlib.sha256(packet_path.read_bytes()).hexdigest()
    env = {**os.environ, "HERMES_HOME": str(tmp_path / "hermes-home")}

    schema_run = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "agent-os", "cognitive-schema", "--json"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert schema_run.returncode == 0, schema_run.stderr
    assert json.loads(schema_run.stdout)["schema"] == "PersonalSemanticGoalReconciler.v1"

    first_run = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "agent-os", "reconcile", "--input", str(packet_path), "--json"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    second_run = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "agent-os", "reconcile", "--input", str(packet_path), "--json"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert first_run.returncode == 0, first_run.stderr
    assert second_run.returncode == 0, second_run.stderr
    first_payload = json.loads(first_run.stdout)
    second_payload = json.loads(second_run.stdout)
    assert first_payload == second_payload
    assert first_payload["state"] == "needs_observation"
    assert first_payload["selected_observation_ref"] is None
    assert first_payload["intention"] is None
    assert first_payload["candidates"] == []
    assert hashlib.sha256(packet_path.read_bytes()).hexdigest() == before
    home = tmp_path / "hermes-home"
    assert not (home / "workflows").exists()
    assert not (home / "project-profiles").exists()
    assert not (home / "cron" / "jobs.json").exists()


def test_cli_invalid_packet_exits_nonzero_with_structured_findings(tmp_path: Path) -> None:
    packet = _base_packet()
    packet["assertions"][0]["authority_effect"] = "grant"
    packet_path = tmp_path / "invalid.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    before = hashlib.sha256(packet_path.read_bytes()).hexdigest()
    env = {**os.environ, "HERMES_HOME": str(tmp_path / "hermes-home")}
    run = subprocess.run(
        [sys.executable, "-m", "hermes_cli.main", "agent-os", "reconcile", "--input", str(packet_path), "--json"],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode != 0
    payload = json.loads(run.stdout)
    assert payload["ok"] is False
    assert any(item["code"] == "authority_effect_must_be_none" for item in payload["findings"])
    assert all(value is False for value in payload["boundary"].values())
    assert hashlib.sha256(packet_path.read_bytes()).hexdigest() == before
    home = tmp_path / "hermes-home"
    assert not (home / "workflows").exists()
    assert not (home / "project-profiles").exists()
    assert not (home / "cron" / "jobs.json").exists()
