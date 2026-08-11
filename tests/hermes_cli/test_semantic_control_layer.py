from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from hermes_cli.personal_cognitive_reconciler import trusted_row_binding
from hermes_cli.semantic_control_layer import (
    SemanticControlValidationError,
    project_semantic_control,
    semantic_control_schema,
)
from tests.hermes_cli.test_personal_cognitive_reconciler import (
    NOW,
    _base_packet,
    _exact_grant,
)


def _envelope(packet: dict | None = None) -> dict:
    return {
        "schema": "SemanticControlEnvelope.v1",
        "reconcile_input": packet or _base_packet(),
        "proof_boundaries": [
            {
                "source_ref": "source:page-metadata",
                "source_owner": "evidence_store",
                "cannot_prove": ["claim:media-content", "claim:external-delivery"],
            }
        ],
        "term_mappings": [
            {
                "preferred": "ExecutionReceipt",
                "aliases": ["receipt", "回执", "delivery truth"],
                "scope": "global",
                "source_ref": "skill:semantic-control-layer",
                "source_owner": "skill_workflow",
            }
        ],
    }


def _trust(packet: dict) -> dict:
    return {
        "trusted_observation_bindings": frozenset(
            trusted_row_binding("Observation", row) for row in packet["observations"]
        ),
        "trusted_preference_assertion_bindings": frozenset(
            trusted_row_binding("SemanticAssertion", row)
            for row in packet["assertions"]
        ),
        "trusted_grant_bindings": frozenset(
            trusted_row_binding("AuthorityGrant", row)
            for row in packet["authority_grants"]
        ),
        "trusted_receipt_bindings": frozenset(
            trusted_row_binding("ExecutionReceipt", row) for row in packet["receipts"]
        ),
        "trusted_post_action_observation_bindings": frozenset(
            trusted_row_binding("Observation", row) for row in packet["observations"]
        ),
    }


def _project(envelope: dict) -> dict:
    packet = envelope["reconcile_input"]
    return project_semantic_control(
        envelope,
        evaluation_time=datetime.fromisoformat(NOW),
        **_trust(packet),
    )


def test_schema_declares_projection_only_boundary_and_complete_planes() -> None:
    schema = semantic_control_schema()
    assert schema["schema"] == "SemanticControlLayer.v1"
    assert schema["planes"] == [
        "knowledge_evidence",
        "capability_action",
        "goal_evaluation",
    ]
    assert schema["boundary"] == {
        "runtime_mutation": False,
        "capability_execution": False,
        "authority_expansion": False,
        "external_side_effects": False,
        "native_owner_replacement": False,
        "durable_semantic_writeback": False,
    }
    assert "claimed_cannot_prove_is_first_class" in schema["invariants"]
    assert "semantic_projection_never_grants_authority" in schema["invariants"]
    assert schema["canonicalization"] == "json-sort-keys-utf8-no-nan.v1"
    assert schema["trust_contract"]["cli_trust_mode"] == "none_claim_only"
    assert schema["node_contract"]["unbound_owner"] == "semantic_projection"
    assert "claims_readback" in schema["relation_vocabulary"]
    assert "claims_cannot_prove" in schema["relation_vocabulary"]
    assert schema["artifact_kind"] == "versioned_contract_descriptor"
    assert schema["machine_validation"] == "project_semantic_control_runtime_validator"
    assert schema["json_schema"] is False


def test_projection_connects_three_planes_without_becoming_a_second_ssot() -> None:
    result = _project(_envelope())
    assert result["ok"] is True
    assert result["schema"] == "SemanticControlProjection.v1"
    assert result["reconciliation"]["state"] == "waiting_authority"
    assert {node["plane"] for node in result["nodes"]} == {
        "knowledge_evidence",
        "capability_action",
        "goal_evaluation",
    }
    assert all(
        "native_ref" in node and "native_owner" in node for node in result["nodes"]
    )
    assert result["projection"]["authoritative"] is False
    assert result["projection"]["rebuildable"] is True
    assert result["projection"]["bound_native_owners_preserved"] is True
    relations = {edge["relation"] for edge in result["edges"]}
    assert {
        "supports",
        "observes",
        "has_gap",
        "claims_requires_capability",
        "claims_serves",
        "selects",
        "blocked_by",
    } <= relations
    for node in result["nodes"]:
        if node["kind"] in {"Evidence", "Source"}:
            assert node["native_owner"] == "semantic_projection"
            assert node["attributes"]["ownership_status"] == "unverified_claim"


def test_untrusted_rows_are_queryable_but_never_emit_assertive_relations() -> None:
    envelope = _envelope()
    envelope["reconcile_input"]["authority_grants"] = [_exact_grant()]
    result = project_semantic_control(
        envelope, evaluation_time=datetime.fromisoformat(NOW)
    )
    strong_relations = {
        "asserts",
        "revises",
        "observes",
        "requires_capability",
        "serves",
        "projects_authority_for",
        "reports_execution_of",
        "read_back_by",
    }
    relations = {edge["relation"] for edge in result["edges"]}
    assert not relations & strong_relations
    observation = next(
        node for node in result["nodes"] if node["kind"] == "Observation"
    )
    assert observation["attributes"]["trusted_observation"] is False
    assert observation["native_owner"] == "semantic_projection"
    assert observation["attributes"]["claimed_native_owner"] == "runtime_probe"
    grant = next(node for node in result["nodes"] if node["kind"] == "AuthorityGrant")
    assert grant["native_owner"] == "semantic_projection"
    assert grant["attributes"]["claimed_native_owner"] == "authority_ledger"
    assertion = next(
        node for node in result["nodes"] if node["kind"] == "SemanticAssertion"
    )
    assert assertion["native_owner"] == "semantic_projection"
    assert assertion["attributes"]["claimed_native_owner"] == "user_profile"
    assert result["projection"]["unbound_owner_claims_promoted"] is False
    assert "claims_assertion" in relations
    assert "claims_observation_of" in relations
    assert "claims_requires_capability" in relations
    assert "claims_serves" in relations


def test_assertion_revision_relations_require_exact_out_of_band_trust() -> None:
    envelope = _envelope()
    envelope["reconcile_input"]["assertions"][0]["revision_of"] = "assertion:prior"
    untrusted = project_semantic_control(
        envelope, evaluation_time=datetime.fromisoformat(NOW)
    )
    untrusted_relations = {edge["relation"] for edge in untrusted["edges"]}
    assert {"claims_assertion", "claims_revision_of"} <= untrusted_relations
    assert not {"asserts", "revises"} & untrusted_relations

    trusted = _project(envelope)
    trusted_relations = {edge["relation"] for edge in trusted["edges"]}
    assert {"asserts", "revises"} <= trusted_relations
    assert not {"claims_assertion", "claims_revision_of"} & trusted_relations
    predecessor = next(
        node
        for node in trusted["nodes"]
        if node["kind"] == "SemanticAssertion"
        and node["native_ref"] == "assertion:prior"
    )
    assert predecessor["native_owner"] == "semantic_projection"
    assert predecessor["attributes"]["ownership_status"] == "unverified_claim"
    assert predecessor["attributes"]["claimed_native_owner"] == "user_profile"


def test_in_packet_revision_chain_projects_without_identity_conflict() -> None:
    envelope = _envelope()
    successor = envelope["reconcile_input"]["assertions"][0]
    predecessor = copy.deepcopy(successor)
    predecessor["assertion_id"] = "assertion:prior"
    predecessor["revision_of"] = None
    successor["revision_of"] = predecessor["assertion_id"]
    envelope["reconcile_input"]["assertions"].insert(0, predecessor)

    result = _project(envelope)

    assertion_nodes = [
        node for node in result["nodes"] if node["kind"] == "SemanticAssertion"
    ]
    assert {node["native_ref"] for node in assertion_nodes} >= {
        predecessor["assertion_id"],
        successor["assertion_id"],
    }
    assert "revises" in {edge["relation"] for edge in result["edges"]}


def test_scoped_terms_preserve_provenance_without_cross_scope_collision() -> None:
    envelope = _envelope()
    envelope["term_mappings"].append({
        "preferred": "ProjectReceipt",
        "aliases": ["receipt"],
        "scope": "project:alpha",
        "source_ref": "profile:project-alpha",
        "source_owner": "project_profile",
    })
    result = _project(envelope)
    assert result["queries"]["terminology"] == {
        "global": {
            alias: {
                "status": "claimed_mapping",
                "preferred": "ExecutionReceipt",
                "source_ref": "skill:semantic-control-layer",
                "claimed_native_owner": "skill_workflow",
                "ownership_status": "unverified_claim",
            }
            for alias in ["delivery truth", "receipt", "回执"]
        },
        "project:alpha": {
            "receipt": {
                "status": "claimed_mapping",
                "preferred": "ProjectReceipt",
                "source_ref": "profile:project-alpha",
                "claimed_native_owner": "project_profile",
                "ownership_status": "unverified_claim",
            }
        },
    }
    project_alias = next(
        node
        for node in result["nodes"]
        if node["kind"] == "TermAlias"
        and node["attributes"].get("scope") == "project:alpha"
    )
    assert project_alias["native_owner"] == "semantic_projection"
    assert project_alias["attributes"]["claimed_native_owner"] == "project_profile"
    assert project_alias["attributes"]["ownership_status"] == "unverified_claim"
    assert project_alias["attributes"]["source_ref"] == "profile:project-alpha"


def test_projection_binds_rebuild_provenance_to_input_and_trust_context() -> None:
    envelope = _envelope()
    first = _project(envelope)
    second = _project(copy.deepcopy(envelope))
    provenance = first["projection"]["provenance"]
    assert provenance == second["projection"]["provenance"]
    assert provenance["input_schema"] == "SemanticControlEnvelope.v1"
    assert provenance["generator_contract"] == "SemanticControlLayer.v1"
    assert len(provenance["input_sha256"]) == 64
    assert len(provenance["trust_context_sha256"]) == 64
    untrusted = project_semantic_control(
        envelope, evaluation_time=datetime.fromisoformat(NOW)
    )
    assert (
        untrusted["projection"]["provenance"]["input_sha256"]
        == provenance["input_sha256"]
    )
    assert (
        untrusted["projection"]["provenance"]["trust_context_sha256"]
        != provenance["trust_context_sha256"]
    )


@pytest.mark.parametrize(
    "binding_name",
    [
        "trusted_observation_bindings",
        "trusted_preference_assertion_bindings",
        "trusted_grant_bindings",
        "trusted_receipt_bindings",
        "trusted_post_action_observation_bindings",
    ],
)
def test_trust_bindings_reject_equality_spoofing_string_subclasses(
    binding_name: str,
) -> None:
    class SpoofStr(str):
        def __hash__(self) -> int:
            return hash("trusted-row:000000000000000000000000")

        def __eq__(self, other: object) -> bool:
            return True

    with pytest.raises(SemanticControlValidationError) as caught:
        project_semantic_control(
            _envelope(),
            evaluation_time=datetime.fromisoformat(NOW),
            **{binding_name: frozenset({SpoofStr("not-an-exact-binding")})},
        )
    assert "invalid_trust_binding" in {
        finding["code"] for finding in caught.value.findings
    }


@pytest.mark.parametrize("nested", [False, True])
def test_envelope_rejects_hostile_string_subclass_keys_with_structured_error(
    nested: bool,
) -> None:
    class HostileKey(str):
        armed = False

        def __hash__(self) -> int:
            return hash("schema" if not nested else "source_ref")

        def __eq__(self, other: object) -> bool:
            if self.armed:
                raise RuntimeError("hostile-equality-fired")
            return str.__eq__(self, other)

    envelope = _envelope()
    target = envelope if not nested else envelope["proof_boundaries"][0]
    field = "schema" if not nested else "source_ref"
    value = target.pop(field)
    key = HostileKey(field)
    target[key] = value
    key.armed = True

    with pytest.raises(SemanticControlValidationError) as caught:
        _project(envelope)
    assert "invalid_field_name" in {
        finding["code"] for finding in caught.value.findings
    }


def test_trust_binding_containers_must_be_exact_frozensets() -> None:
    packet = _envelope()["reconcile_input"]
    binding = trusted_row_binding("Observation", packet["observations"][0])
    with pytest.raises(SemanticControlValidationError) as caught:
        project_semantic_control(
            _envelope(packet),
            evaluation_time=datetime.fromisoformat(NOW),
            trusted_observation_bindings=[binding],  # type: ignore[arg-type]
        )
    assert "invalid_trust_binding_set" in {
        finding["code"] for finding in caught.value.findings
    }


@pytest.mark.parametrize("collection", ["proof_boundaries", "term_mappings"])
def test_provenance_rows_reject_unknown_native_owners(collection: str) -> None:
    envelope = _envelope()
    envelope[collection][0]["source_owner"] = "semantic_projection"
    with pytest.raises(SemanticControlValidationError) as caught:
        _project(envelope)
    assert "invalid_native_owner" in {
        finding["code"] for finding in caught.value.findings
    }


@pytest.mark.parametrize("collection", ["proof_boundaries", "term_mappings"])
def test_provenance_rows_require_exact_source_fields(collection: str) -> None:
    envelope = _envelope()
    del envelope[collection][0]["source_ref"]
    with pytest.raises(SemanticControlValidationError) as caught:
        _project(envelope)
    assert "invalid_fields" in {finding["code"] for finding in caught.value.findings}


def test_cannot_prove_boundaries_are_queryable_and_never_promote_authority() -> None:
    result = _project(_envelope())
    verdicts = result["queries"]["claim_verdicts"]
    assert verdicts["claim:media-content"] == {
        "status": "claimed_not_proven",
        "claimed_cannot_prove_sources": [
            {
                "source_ref": "source:page-metadata",
                "claimed_native_owner": "evidence_store",
                "ownership_status": "unverified_claim",
            }
        ],
        "proof_refs": [],
    }
    assert verdicts["claim:external-delivery"]["status"] == "claimed_not_proven"
    relations = {edge["relation"] for edge in result["edges"]}
    assert "claims_cannot_prove" in relations
    assert "cannot_prove" not in relations
    assert result["queries"]["action_readiness"]["state"] == "waiting_authority"
    assert result["queries"]["action_readiness"]["may_execute"] is False
    assert result["boundary"]["authority_expansion"] is False


def test_fresh_goal_observation_proves_goal_but_not_an_execution_claim() -> None:
    packet = _base_packet(observed_value=5)
    result = _project(_envelope(packet))
    completion = result["queries"]["completion"]
    assert completion["goal_status"] == "verified"
    assert completion["execution_status"] == "not_claimed"
    assert completion["goal_evidence_refs"] == ["evidence:probe-before"]
    assert completion["execution_evidence_refs"] == []


def test_receipt_without_fresh_bound_readback_cannot_prove_completion() -> None:
    packet = _base_packet()
    first = _project(_envelope(packet))
    packet["receipts"] = [
        {
            "receipt_id": "receipt:repair",
            "intention_ref": first["reconciliation"]["intention"]["intention_id"],
            "action_ref": first["reconciliation"]["intention"]["action_ref"],
            "status": "completed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:tool-return"],
            "post_action_observation_refs": [],
        }
    ]
    result = _project(_envelope(packet))
    completion = result["queries"]["completion"]
    assert result["reconciliation"]["state"] == "uncertain"
    assert completion["goal_status"] == "not_verified"
    assert completion["execution_status"] == "uncertain_without_readback"
    assert "fresh_post_action_readback_required" in completion["blockers"]


def test_exact_receipt_and_fresh_bound_readback_verify_execution_and_goal() -> None:
    packet = _base_packet()
    first = _project(_envelope(packet))
    packet["observations"].append({
        "observation_id": "observation:after",
        "subject_ref": packet["goal"]["subject_ref"],
        "predicate": packet["goal"]["predicate"],
        "value": 6,
        "observed_at": "2026-07-28T23:59:50+00:00",
        "source_ref": "probe:capability-graph",
        "provenance_ref": "evidence:probe-after",
    })
    packet["receipts"] = [
        {
            "receipt_id": "receipt:repair",
            "intention_ref": first["reconciliation"]["intention"]["intention_id"],
            "action_ref": first["reconciliation"]["intention"]["action_ref"],
            "status": "completed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:tool-return"],
            "post_action_observation_refs": ["observation:after"],
        }
    ]
    result = _project(_envelope(packet))
    completion = result["queries"]["completion"]
    assert result["reconciliation"]["receipt_evaluation"] == "reobserved_converged"
    assert result["reconciliation"]["evaluated_receipt_ref"] == "receipt:repair"
    assert completion["goal_status"] == "verified"
    assert completion["execution_status"] == "verified"
    assert completion["goal_evidence_refs"] == ["evidence:probe-after"]
    assert completion["execution_evidence_refs"] == [
        "evidence:tool-return",
        "evidence:probe-after",
    ]


def test_verified_execution_excludes_untrusted_receipt_linked_readback_evidence() -> (
    None
):
    packet = _base_packet()
    first = _project(_envelope(packet))
    trusted_after = {
        "observation_id": "observation:trusted-after",
        "subject_ref": packet["goal"]["subject_ref"],
        "predicate": packet["goal"]["predicate"],
        "value": 6,
        "observed_at": "2026-07-28T23:59:50+00:00",
        "source_ref": "probe:capability-graph",
        "provenance_ref": "evidence:trusted-after",
    }
    untrusted_after = {
        **trusted_after,
        "observation_id": "observation:untrusted-after",
        "provenance_ref": "evidence:UNTRUSTED",
    }
    trusted_but_stale = {
        **trusted_after,
        "observation_id": "observation:trusted-but-stale",
        "observed_at": "2026-07-28T23:59:44+00:00",
        "provenance_ref": "evidence:TRUSTED-BUT-STALE",
    }
    packet["observations"].extend([trusted_after, untrusted_after, trusted_but_stale])
    packet["receipts"] = [
        {
            "receipt_id": "receipt:repair",
            "intention_ref": first["reconciliation"]["intention"]["intention_id"],
            "action_ref": first["reconciliation"]["intention"]["action_ref"],
            "status": "completed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:tool-return"],
            "post_action_observation_refs": [
                "observation:trusted-after",
                "observation:untrusted-after",
                "observation:trusted-but-stale",
            ],
        }
    ]
    trust = _trust(packet)
    trust["trusted_observation_bindings"] = frozenset(
        trusted_row_binding("Observation", row)
        for row in packet["observations"]
        if row["observation_id"]
        not in {
            "observation:trusted-after",
            "observation:untrusted-after",
            "observation:trusted-but-stale",
        }
    )
    trust["trusted_post_action_observation_bindings"] = frozenset({
        trusted_row_binding("Observation", trusted_after),
        trusted_row_binding("Observation", trusted_but_stale),
    })

    result = project_semantic_control(
        _envelope(packet),
        evaluation_time=datetime.fromisoformat(NOW),
        **trust,
    )

    assert result["reconciliation"]["receipt_evaluation"] == "reobserved_converged"
    assert result["reconciliation"]["evaluated_post_action_observation_refs"] == [
        "observation:trusted-after"
    ]
    assert result["queries"]["completion"]["goal_status"] == "verified"
    assert result["queries"]["completion"]["goal_evidence_refs"] == [
        "evidence:trusted-after"
    ]
    assert result["queries"]["completion"]["execution_evidence_refs"] == [
        "evidence:tool-return",
        "evidence:trusted-after",
    ]
    node_by_native_ref = {
        node["native_ref"]: node["node_id"] for node in result["nodes"]
    }
    receipt_node = node_by_native_ref["receipt:repair"]
    readback_relations = {
        edge["target"]: edge["relation"]
        for edge in result["edges"]
        if edge["source"] == receipt_node
        and edge["relation"] in {"read_back_by", "claims_readback"}
    }
    assert readback_relations[node_by_native_ref["observation:trusted-after"]] == (
        "read_back_by"
    )
    assert readback_relations[node_by_native_ref["observation:untrusted-after"]] == (
        "claims_readback"
    )
    assert readback_relations[node_by_native_ref["observation:trusted-but-stale"]] == (
        "claims_readback"
    )


def test_latest_trusted_post_action_gap_overrides_older_convergence() -> None:
    packet = _base_packet()
    latest_gap = packet["observations"][0]
    latest_gap["observed_at"] = "2026-07-28T23:59:50+00:00"
    latest_gap["provenance_ref"] = "evidence:latest-gap"
    older_converged = {
        **latest_gap,
        "observation_id": "observation:older-converged",
        "value": 6,
        "observed_at": "2026-07-28T23:59:49+00:00",
        "provenance_ref": "evidence:older-converged",
    }
    packet["observations"].append(older_converged)
    first = _project(_envelope(packet))
    packet["receipts"] = [
        {
            "receipt_id": "receipt:repair",
            "intention_ref": first["reconciliation"]["intention"]["intention_id"],
            "action_ref": first["reconciliation"]["intention"]["action_ref"],
            "status": "completed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:tool-return"],
            "post_action_observation_refs": [
                "observation:older-converged",
                latest_gap["observation_id"],
            ],
        }
    ]

    result = _project(_envelope(packet))

    assert result["reconciliation"]["receipt_evaluation"] == "reobserved_gap"
    assert result["reconciliation"]["evaluated_post_action_observation_refs"] == [
        latest_gap["observation_id"]
    ]
    assert result["queries"]["completion"]["execution_status"] == (
        "verified_not_converged"
    )
    assert result["queries"]["completion"]["execution_evidence_refs"] == [
        "evidence:tool-return",
        "evidence:latest-gap",
    ]
    assert (
        "evidence:older-converged"
        not in result["queries"]["completion"]["execution_evidence_refs"]
    )


def test_latest_trusted_post_action_convergence_overrides_older_gap() -> None:
    packet = _base_packet()
    first = _project(_envelope(packet))
    older_gap = {
        **packet["observations"][0],
        "observation_id": "observation:older-gap",
        "observed_at": "2026-07-28T23:59:49+00:00",
        "provenance_ref": "evidence:older-gap",
    }
    latest_converged = {
        **older_gap,
        "observation_id": "observation:latest-converged",
        "value": 6,
        "observed_at": "2026-07-28T23:59:50+00:00",
        "provenance_ref": "evidence:latest-converged",
    }
    packet["observations"].extend([older_gap, latest_converged])
    packet["receipts"] = [
        {
            "receipt_id": "receipt:repair",
            "intention_ref": first["reconciliation"]["intention"]["intention_id"],
            "action_ref": first["reconciliation"]["intention"]["action_ref"],
            "status": "completed",
            "started_at": "2026-07-28T23:59:40+00:00",
            "finished_at": "2026-07-28T23:59:45+00:00",
            "result_evidence_refs": ["evidence:tool-return"],
            "post_action_observation_refs": [
                "observation:latest-converged",
                "observation:older-gap",
            ],
        }
    ]

    result = _project(_envelope(packet))

    assert result["reconciliation"]["receipt_evaluation"] == "reobserved_converged"
    assert result["reconciliation"]["evaluated_post_action_observation_refs"] == [
        "observation:latest-converged"
    ]
    assert result["queries"]["completion"]["execution_status"] == "verified"
    assert result["queries"]["completion"]["execution_evidence_refs"] == [
        "evidence:tool-return",
        "evidence:latest-converged",
    ]


def test_term_aliases_normalize_queries_without_rewriting_native_terms() -> None:
    result = _project(_envelope())
    terminology = result["queries"]["terminology"]["global"]
    assert terminology["回执"]["preferred"] == "ExecutionReceipt"
    assert terminology["回执"]["status"] == "claimed_mapping"
    assert terminology["delivery truth"]["ownership_status"] == "unverified_claim"
    alias = next(
        node
        for node in result["nodes"]
        if node["kind"] == "TermAlias"
        and node["attributes"].get("term") == "receipt"
        and node["attributes"].get("scope") == "global"
    )
    assert alias["kind"] == "TermAlias"
    assert {
        node["native_ref"]
        for node in result["nodes"]
        if node["kind"] == "ActionContract"
    } == {
        "action:repair-a",
        "action:repair-b",
    }
    assert result["projection"]["bound_native_owners_preserved"] is True


def test_duplicate_alias_conflict_fails_closed() -> None:
    envelope = _envelope()
    envelope["term_mappings"].append({
        "preferred": "AuthorityGrant",
        "aliases": ["receipt"],
        "scope": "global",
        "source_ref": "skill:authority-contract",
        "source_owner": "skill_workflow",
    })
    with pytest.raises(SemanticControlValidationError) as caught:
        _project(envelope)
    assert "ambiguous_alias" in {finding["code"] for finding in caught.value.findings}


def test_duplicate_alias_provenance_conflict_fails_closed() -> None:
    envelope = _envelope()
    envelope["term_mappings"].append({
        "preferred": "ExecutionReceipt",
        "aliases": ["receipt"],
        "scope": "global",
        "source_ref": "skill:other-semantic-source",
        "source_owner": "skill_workflow",
    })
    with pytest.raises(SemanticControlValidationError) as caught:
        _project(envelope)
    assert "ambiguous_alias" in {finding["code"] for finding in caught.value.findings}


def test_projection_is_deterministic_and_deduplicates_graph_identity() -> None:
    envelope = _envelope()
    first = _project(copy.deepcopy(envelope))
    second = _project(copy.deepcopy(envelope))
    assert first == second
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == json.dumps(
        second, ensure_ascii=False, sort_keys=True
    )
    node_ids = [node["node_id"] for node in first["nodes"]]
    edge_ids = [edge["edge_id"] for edge in first["edges"]]
    assert len(node_ids) == len(set(node_ids))
    assert len(edge_ids) == len(set(edge_ids))


def test_cli_parser_exposes_semantic_projection_as_read_only_surface() -> None:
    from hermes_cli.subcommands.agent_os import build_agent_os_parser
    import argparse

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    build_agent_os_parser(subparsers)
    args = parser.parse_args([
        "agent-os",
        "semantic-project",
        "--input",
        "/tmp/envelope.json",
        "--evaluation-time",
        NOW,
    ])
    assert args.agent_os_action == "semantic-project"
    assert args.input == "/tmp/envelope.json"
    assert args.evaluation_time == NOW

    schema_args = parser.parse_args(["agent-os", "semantic-schema"])
    assert schema_args.agent_os_action == "semantic-schema"


def test_cli_projects_valid_envelope_without_trusting_packet_local_rows(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "semantic-envelope.json"
    input_path.write_text(json.dumps(_envelope()), encoding="utf-8")
    hermes_home = tmp_path / "home"
    env = {**os.environ, "HERMES_HOME": str(hermes_home)}
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "hermes_cli.main",
            "agent-os",
            "semantic-project",
            "--input",
            str(input_path),
            "--evaluation-time",
            NOW,
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["schema"] == "SemanticControlProjection.v1"
    assert payload["reconciliation"]["state"] == "needs_observation"
    assert payload["queries"]["action_readiness"]["may_execute"] is False
    assert all(value is False for value in payload["boundary"].values())
    assert not hermes_home.exists()


def test_cli_evaluation_time_replays_byte_equivalent_projection(tmp_path: Path) -> None:
    input_path = tmp_path / "semantic-envelope.json"
    input_path.write_text(json.dumps(_envelope()), encoding="utf-8")
    env = {**os.environ, "HERMES_HOME": str(tmp_path / "home")}
    command = [
        sys.executable,
        "-m",
        "hermes_cli.main",
        "agent-os",
        "semantic-project",
        "--input",
        str(input_path),
        "--evaluation-time",
        NOW,
    ]

    first = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    second = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert first.returncode == second.returncode == 0
    assert first.stdout == second.stdout
    assert (
        json.loads(first.stdout)["projection"]["provenance"]["evaluation_time"] == NOW
    )


def test_cli_normalizes_timezone_aware_evaluation_time_for_replay(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "semantic-envelope.json"
    input_path.write_text(json.dumps(_envelope()), encoding="utf-8")
    env = {**os.environ, "HERMES_HOME": str(tmp_path / "home")}

    def project(evaluation_time: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "hermes_cli.main",
                "agent-os",
                "semantic-project",
                "--input",
                str(input_path),
                "--evaluation-time",
                evaluation_time,
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    utc = project("2026-07-29T00:00:00+00:00")
    offset = project("2026-07-29T08:00:00+08:00")

    assert utc.returncode == offset.returncode == 0
    assert utc.stdout == offset.stdout
    assert (
        json.loads(offset.stdout)["projection"]["provenance"]["evaluation_time"]
        == "2026-07-29T00:00:00+00:00"
    )
