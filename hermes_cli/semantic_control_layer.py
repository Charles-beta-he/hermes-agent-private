"""Deterministic, projection-only semantic control layer for Agent OS.

The layer joins the existing personal cognitive reconciler's three planes into a
queryable graph.  It never becomes a source of truth: every projected node keeps
its native reference, only exactly bound rows keep a native owner, unbound claims
and derived nodes are projection-owned, and no semantic relation can grant
authority or execute an action.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any

from hermes_cli.personal_cognitive_reconciler import (
    ReconciliationValidationError,
    reconcile_packet,
    trusted_row_binding,
)

ENVELOPE_SCHEMA = "SemanticControlEnvelope.v1"
CONTRACT_SCHEMA = "SemanticControlLayer.v1"
PROJECTION_SCHEMA = "SemanticControlProjection.v1"

_BOUNDARY = {
    "runtime_mutation": False,
    "capability_execution": False,
    "authority_expansion": False,
    "external_side_effects": False,
    "native_owner_replacement": False,
    "durable_semantic_writeback": False,
}
_PLANES = ("knowledge_evidence", "capability_action", "goal_evaluation")
_NATIVE_OWNERS = frozenset({
    "profile_memory",
    "project_profile",
    "skill_workflow",
    "mission",
    "taskpool",
    "runtime_probe",
    "evidence_store",
    "authority_ledger",
})


class SemanticControlValidationError(ValueError):
    """Structured, fail-closed semantic-envelope validation error."""

    def __init__(self, findings: list[dict[str, str]]):
        self.findings = sorted(
            findings, key=lambda item: (item["path"], item["code"], item["message"])
        )
        super().__init__("invalid semantic control envelope")


def _stable_id(prefix: str, *parts: Any) -> str:
    raw = json.dumps(
        parts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"{prefix}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _validate_native_owner(
    findings: list[dict[str, str]], path: str, owner: Any
) -> None:
    if type(owner) is not str or owner not in _NATIVE_OWNERS:
        findings.append(
            _finding(
                path,
                "invalid_native_owner",
                f"must be one of: {', '.join(sorted(_NATIVE_OWNERS))}",
            )
        )


def semantic_control_schema() -> dict[str, Any]:
    return {
        "ok": True,
        "schema": CONTRACT_SCHEMA,
        "artifact_kind": "versioned_contract_descriptor",
        "machine_validation": "project_semantic_control_runtime_validator",
        "json_schema": False,
        "input_schema": ENVELOPE_SCHEMA,
        "result_schema": PROJECTION_SCHEMA,
        "canonicalization": "json-sort-keys-utf8-no-nan.v1",
        "compatibility": {
            "policy": "additive_fields_require_consumer_tolerance; semantic_changes_require_v2",
            "identity": "kind+native_owner+native_ref",
            "ordering": "node_id_or_edge_id_ascending",
        },
        "input_contract": {
            "exact_fields": [
                "proof_boundaries",
                "reconcile_input",
                "schema",
                "term_mappings",
            ],
            "proof_boundary_exact_fields": [
                "cannot_prove",
                "source_owner",
                "source_ref",
            ],
            "term_mapping_exact_fields": [
                "aliases",
                "preferred",
                "scope",
                "source_owner",
                "source_ref",
            ],
        },
        "node_contract": {
            "exact_fields": [
                "attributes",
                "kind",
                "native_owner",
                "native_ref",
                "node_id",
                "plane",
            ],
            "unbound_owner": "semantic_projection",
            "unbound_owner_attribute": "claimed_native_owner",
            "ownership_status_values": [
                "bound",
                "projection_derived",
                "unverified_claim",
            ],
        },
        "trust_contract": {
            "transport": "out_of_band_exact_frozensets_only",
            "binding": "trusted-row:<24 lowercase hex>",
            "unbound_rows_emit": "claims_*",
            "cli_trust_mode": "none_claim_only",
        },
        "relation_vocabulary": [
            "addresses",
            "asserts",
            "blocked_by",
            "claims_alias_of",
            "claims_assertion",
            "claims_authority_for",
            "claims_cannot_prove",
            "claims_defines_term",
            "claims_execution_of",
            "claims_observation_of",
            "claims_production",
            "claims_readback",
            "claims_requires_capability",
            "claims_revision_of",
            "claims_serves",
            "claims_support",
            "evaluates",
            "has_gap",
            "observes",
            "produces",
            "projects_authority_for",
            "read_back_by",
            "realizes",
            "refines",
            "reports_execution_of",
            "requires_capability",
            "revises",
            "selects",
            "serves",
            "stops_at",
            "supports",
        ],
        "planes": list(_PLANES),
        "invariants": [
            "native_owners_remain_authoritative",
            "unbound_owner_claims_are_projection_owned",
            "projection_is_rebuildable",
            "claimed_cannot_prove_is_first_class",
            "semantic_projection_never_grants_authority",
            "candidate_and_intention_never_imply_execution",
            "receipt_without_fresh_bound_readback_never_proves_convergence",
            "identical_input_and_trusted_context_produce_identical_output",
        ],
        "boundary": dict(_BOUNDARY),
    }


def _finding(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _typed_json_findings(value: Any, path: str) -> list[dict[str, str]]:
    """Inspect exact JSON types without equality-sensitive dictionary lookups."""
    findings: list[dict[str, str]] = []
    stack: list[tuple[Any, str, int, frozenset[int]]] = [(value, path, 0, frozenset())]
    visited = 0
    while stack:
        current, current_path, depth, ancestors = stack.pop()
        visited += 1
        if visited > 100_000:
            findings.append(
                _finding(
                    path,
                    "json_node_limit",
                    "envelope exceeds the typed-JSON node limit",
                )
            )
            break
        if depth > 64:
            findings.append(
                _finding(
                    current_path,
                    "json_depth_limit",
                    "typed-JSON nesting exceeds 64 levels",
                )
            )
            continue
        next_ancestors = ancestors
        if type(current) in (dict, list):
            identity = id(current)
            if identity in ancestors:
                findings.append(
                    _finding(
                        current_path,
                        "invalid_json_cycle",
                        "typed JSON cannot contain cycles",
                    )
                )
                continue
            next_ancestors = ancestors | {identity}
        if type(current) is dict:
            for index, (key, child) in enumerate(current.items()):
                if type(key) is not str:
                    findings.append(
                        _finding(
                            current_path,
                            "invalid_field_name",
                            f"field name at object index {index} must be an exact string",
                        )
                    )
                stack.append((
                    child,
                    f"{current_path}.[field:{index}]",
                    depth + 1,
                    next_ancestors,
                ))
            continue
        if type(current) is list:
            for index, child in enumerate(current):
                stack.append((
                    child,
                    f"{current_path}[{index}]",
                    depth + 1,
                    next_ancestors,
                ))
            continue
        if type(current) is float and not math.isfinite(current):
            findings.append(
                _finding(
                    current_path,
                    "invalid_json_number",
                    "JSON numbers must be finite",
                )
            )
            continue
        if current is not None and type(current) not in (str, int, float, bool):
            findings.append(
                _finding(
                    current_path,
                    "invalid_json_type",
                    "value is not representable in JSON",
                )
            )
    return findings


def _validate_envelope(envelope: Any) -> dict[str, Any]:
    if type(envelope) is not dict:
        raise SemanticControlValidationError([
            _finding("envelope", "invalid_type", "must be an object")
        ])
    typed_findings = _typed_json_findings(envelope, "envelope")
    if typed_findings:
        raise SemanticControlValidationError(typed_findings)
    findings: list[dict[str, str]] = []
    required = {"schema", "reconcile_input", "proof_boundaries", "term_mappings"}
    string_keys = {key for key in envelope if type(key) is str}
    if len(string_keys) != len(envelope):
        findings.append(
            _finding(
                "envelope", "invalid_field_name", "field names must be exact strings"
            )
        )
    for key in sorted(required - string_keys):
        findings.append(
            _finding(f"envelope.{key}", "missing_field", "required field is missing")
        )
    extras = sorted(string_keys - required)
    if extras:
        findings.append(
            _finding(
                "envelope",
                "unexpected_fields",
                f"unexpected fields: {', '.join(extras)}",
            )
        )
    if envelope.get("schema") != ENVELOPE_SCHEMA:
        findings.append(
            _finding("envelope.schema", "invalid_schema", f"must be {ENVELOPE_SCHEMA}")
        )
    if type(envelope.get("reconcile_input")) is not dict:
        findings.append(
            _finding("envelope.reconcile_input", "invalid_type", "must be an object")
        )

    boundaries = envelope.get("proof_boundaries")
    if type(boundaries) is not list:
        findings.append(
            _finding("envelope.proof_boundaries", "invalid_type", "must be an array")
        )
    else:
        seen_boundaries: set[tuple[str, str]] = set()
        for index, boundary in enumerate(boundaries):
            path = f"envelope.proof_boundaries[{index}]"
            if type(boundary) is not dict:
                findings.append(_finding(path, "invalid_type", "must be an object"))
                continue
            if set(boundary) != {"source_ref", "source_owner", "cannot_prove"}:
                findings.append(
                    _finding(
                        path,
                        "invalid_fields",
                        "requires exactly source_ref, source_owner, and cannot_prove",
                    )
                )
                continue
            source = boundary.get("source_ref")
            source_owner = boundary.get("source_owner")
            claims = boundary.get("cannot_prove")
            if type(source) is not str or not source.strip():
                findings.append(
                    _finding(
                        f"{path}.source_ref",
                        "invalid_string",
                        "must be a non-empty string",
                    )
                )
            _validate_native_owner(findings, f"{path}.source_owner", source_owner)
            if type(claims) is not list or not claims:
                findings.append(
                    _finding(
                        f"{path}.cannot_prove",
                        "invalid_claims",
                        "must be a non-empty array",
                    )
                )
                continue
            for claim_index, claim in enumerate(claims):
                if type(claim) is not str or not claim.strip():
                    findings.append(
                        _finding(
                            f"{path}.cannot_prove[{claim_index}]",
                            "invalid_string",
                            "must be a non-empty string",
                        )
                    )
                    continue
                key = (source, claim) if type(source) is str else ("", claim)
                if key in seen_boundaries:
                    findings.append(
                        _finding(
                            f"{path}.cannot_prove[{claim_index}]",
                            "duplicate_boundary",
                            "duplicate proof boundary",
                        )
                    )
                seen_boundaries.add(key)

    mappings = envelope.get("term_mappings")
    if type(mappings) is not list:
        findings.append(
            _finding("envelope.term_mappings", "invalid_type", "must be an array")
        )
    else:
        aliases: dict[tuple[Any, Any], tuple[Any, Any, Any]] = {}
        for index, mapping in enumerate(mappings):
            path = f"envelope.term_mappings[{index}]"
            if type(mapping) is not dict:
                findings.append(_finding(path, "invalid_type", "must be an object"))
                continue
            if set(mapping) != {
                "preferred",
                "aliases",
                "scope",
                "source_ref",
                "source_owner",
            }:
                findings.append(
                    _finding(
                        path,
                        "invalid_fields",
                        "requires exactly preferred, aliases, scope, source_ref, and source_owner",
                    )
                )
                continue
            preferred = mapping.get("preferred")
            scope = mapping.get("scope")
            source_ref = mapping.get("source_ref")
            source_owner = mapping.get("source_owner")
            mapping_aliases = mapping.get("aliases")
            if type(preferred) is not str or not preferred.strip():
                findings.append(
                    _finding(
                        f"{path}.preferred",
                        "invalid_string",
                        "must be a non-empty string",
                    )
                )
            if type(scope) is not str or not scope.strip():
                findings.append(
                    _finding(
                        f"{path}.scope", "invalid_string", "must be a non-empty string"
                    )
                )
            if type(source_ref) is not str or not source_ref.strip():
                findings.append(
                    _finding(
                        f"{path}.source_ref",
                        "invalid_string",
                        "must be a non-empty string",
                    )
                )
            _validate_native_owner(findings, f"{path}.source_owner", source_owner)
            if type(mapping_aliases) is not list or not mapping_aliases:
                findings.append(
                    _finding(
                        f"{path}.aliases",
                        "invalid_aliases",
                        "must be a non-empty array",
                    )
                )
                continue
            for alias_index, alias in enumerate(mapping_aliases):
                if type(alias) is not str or not alias.strip():
                    findings.append(
                        _finding(
                            f"{path}.aliases[{alias_index}]",
                            "invalid_string",
                            "must be a non-empty string",
                        )
                    )
                    continue
                key = (scope, alias)
                previous = aliases.get(key)
                if previous is not None:
                    findings.append(
                        _finding(
                            f"{path}.aliases[{alias_index}]",
                            "ambiguous_alias",
                            f"alias already declared by {previous[1]}",
                        )
                    )
                aliases[key] = (preferred, source_ref, source_owner)
    if findings:
        raise SemanticControlValidationError(findings)
    return envelope


class _Graph:
    def __init__(self) -> None:
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: dict[str, dict[str, Any]] = {}

    def node(
        self,
        kind: str,
        native_ref: str,
        native_owner: str,
        plane: str,
        **attributes: Any,
    ) -> str:
        node_id = _stable_id("node", kind, native_owner, native_ref)
        value = {
            "node_id": node_id,
            "kind": kind,
            "native_ref": native_ref,
            "native_owner": native_owner,
            "plane": plane,
            "attributes": attributes,
        }
        current = self._nodes.get(node_id)
        if current is not None and current != value:
            raise SemanticControlValidationError([
                _finding(
                    native_ref,
                    "node_identity_conflict",
                    "same graph identity has conflicting semantics",
                )
            ])
        self._nodes[node_id] = value
        return node_id

    def edge(self, source: str, relation: str, target: str, **attributes: Any) -> None:
        edge_id = _stable_id("edge", source, relation, target, attributes)
        value = {
            "edge_id": edge_id,
            "source": source,
            "relation": relation,
            "target": target,
            "attributes": attributes,
        }
        current = self._edges.get(edge_id)
        if current is not None and current != value:
            raise SemanticControlValidationError([
                _finding(
                    relation,
                    "edge_identity_conflict",
                    "same graph identity has conflicting semantics",
                )
            ])
        self._edges[edge_id] = value

    def values(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return (
            sorted(self._nodes.values(), key=lambda item: item["node_id"]),
            sorted(self._edges.values(), key=lambda item: item["edge_id"]),
        )


def _source_node(graph: _Graph, source_ref: str, owner: str) -> str:
    return graph.node(
        "Source",
        source_ref,
        "semantic_projection",
        "knowledge_evidence",
        ownership_status="unverified_claim",
        claimed_native_owner=owner,
    )


def _evidence_node(graph: _Graph, evidence_ref: str) -> str:
    return graph.node(
        "Evidence",
        evidence_ref,
        "semantic_projection",
        "knowledge_evidence",
        ownership_status="unverified_claim",
        claimed_native_owner="evidence_store",
    )


def _build_graph(
    envelope: dict[str, Any],
    reconciliation: dict[str, Any],
    *,
    trusted_observation_bindings: frozenset[str],
    trusted_preference_assertion_bindings: frozenset[str],
    trusted_grant_bindings: frozenset[str],
    trusted_receipt_bindings: frozenset[str],
    trusted_post_action_observation_bindings: frozenset[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    packet = envelope["reconcile_input"]
    graph = _Graph()

    assertion_nodes: dict[str, str] = {}
    assertion_trust: dict[str, bool] = {}
    for assertion in packet["assertions"]:
        assertion_trusted = (
            trusted_row_binding("SemanticAssertion", assertion)
            in trusted_preference_assertion_bindings
        )
        assertion_trust[assertion["assertion_id"]] = assertion_trusted
        assertion_node = graph.node(
            "SemanticAssertion",
            assertion["assertion_id"],
            assertion["source_owner"] if assertion_trusted else "semantic_projection",
            "knowledge_evidence",
            epistemic_status=assertion["epistemic_status"],
            predicate=assertion["predicate"],
            confidence=assertion["confidence"],
            trusted_for_preference_resolution=assertion_trusted,
            authority_effect="none",
            ownership_status="bound" if assertion_trusted else "unverified_claim",
            **(
                {}
                if assertion_trusted
                else {"claimed_native_owner": assertion["source_owner"]}
            ),
        )
        assertion_nodes[assertion["assertion_id"]] = assertion_node
        source = _source_node(
            graph,
            assertion["source_ref"],
            assertion["source_owner"],
        )
        graph.edge(
            source,
            "asserts" if assertion_trusted else "claims_assertion",
            assertion_node,
        )

    for assertion in packet["assertions"]:
        predecessor_ref = assertion["revision_of"]
        if predecessor_ref is None:
            continue
        assertion_trusted = assertion_trust[assertion["assertion_id"]]
        predecessor = assertion_nodes.get(predecessor_ref)
        if predecessor is None:
            predecessor = graph.node(
                "SemanticAssertion",
                predecessor_ref,
                "semantic_projection",
                "knowledge_evidence",
                unresolved_projection=True,
                ownership_status="unverified_claim",
                claimed_native_owner=assertion["source_owner"],
            )
        graph.edge(
            assertion_nodes[assertion["assertion_id"]],
            "revises" if assertion_trusted else "claims_revision_of",
            predecessor,
        )

    goal = packet["goal"]
    goal_node = graph.node(
        "Goal",
        goal["goal_id"],
        "semantic_projection",
        "goal_evaluation",
        predicate=goal["predicate"],
        desired_value=goal["desired_value"],
        revision=goal["revision"],
        ownership_status="unverified_claim",
        claimed_native_owner="mission",
    )
    if goal["parent_goal_ref"] is not None:
        parent_node = graph.node(
            "Goal",
            goal["parent_goal_ref"],
            "semantic_projection",
            "goal_evaluation",
            unresolved_projection=True,
            ownership_status="unverified_claim",
            claimed_native_owner="mission",
        )
        graph.edge(goal_node, "refines", parent_node)

    observation_nodes: dict[str, str] = {}
    for observation in packet["observations"]:
        binding = trusted_row_binding("Observation", observation)
        observation_trusted = binding in trusted_observation_bindings
        post_action_trusted = binding in trusted_post_action_observation_bindings
        native_trusted = observation_trusted or post_action_trusted
        node = graph.node(
            "Observation",
            observation["observation_id"],
            "runtime_probe" if native_trusted else "semantic_projection",
            "knowledge_evidence",
            predicate=observation["predicate"],
            observed_at=observation["observed_at"],
            trusted_observation=observation_trusted,
            trusted_post_action_observation=post_action_trusted,
            ownership_status="bound" if native_trusted else "unverified_claim",
            **({} if native_trusted else {"claimed_native_owner": "runtime_probe"}),
        )
        observation_nodes[observation["observation_id"]] = node
        source = _source_node(graph, observation["source_ref"], "runtime_probe")
        evidence = _evidence_node(graph, observation["provenance_ref"])
        graph.edge(
            source,
            "produces" if observation_trusted else "claims_production",
            node,
        )
        graph.edge(
            evidence,
            "supports" if observation_trusted else "claims_support",
            node,
        )
        if (
            observation["subject_ref"] == goal["subject_ref"]
            and observation["predicate"] == goal["predicate"]
        ):
            graph.edge(
                node,
                "observes" if observation_trusted else "claims_observation_of",
                goal_node,
            )

    action_nodes: dict[str, str] = {}
    capability_nodes: dict[str, str] = {}
    for action in packet["action_contracts"]:
        action_node = graph.node(
            "ActionContract",
            action["action_id"],
            "semantic_projection",
            "capability_action",
            risk_class=action["risk_class"],
            side_effect_level=action["side_effect_level"],
            required_authority=action["required_authority"],
            ownership_status="unverified_claim",
            claimed_native_owner="skill_workflow",
        )
        capability_node = graph.node(
            "Capability",
            action["capability_ref"],
            "semantic_projection",
            "capability_action",
            ownership_status="unverified_claim",
            claimed_native_owner="skill_workflow",
        )
        action_nodes[action["action_id"]] = action_node
        capability_nodes[action["capability_ref"]] = capability_node
        graph.edge(action_node, "claims_requires_capability", capability_node)
        if goal["predicate"] in action["serves_predicates"]:
            graph.edge(action_node, "claims_serves", goal_node)

    grant_nodes: dict[str, str] = {}
    for grant in packet["authority_grants"]:
        grant_trusted = (
            trusted_row_binding("AuthorityGrant", grant) in trusted_grant_bindings
        )
        grant_node = graph.node(
            "AuthorityGrant",
            grant["grant_id"],
            "authority_ledger" if grant_trusted else "semantic_projection",
            "capability_action",
            status=grant["status"],
            side_effect_level=grant["side_effect_level"],
            trusted_authority=grant_trusted,
            ownership_status="bound" if grant_trusted else "unverified_claim",
            **({} if grant_trusted else {"claimed_native_owner": "authority_ledger"}),
        )
        grant_nodes[grant["grant_id"]] = grant_node
        capability_node = capability_nodes.get(grant["capability_ref"])
        if capability_node is not None:
            graph.edge(
                grant_node,
                "projects_authority_for" if grant_trusted else "claims_authority_for",
                capability_node,
                authority_effect="none",
            )

    receipt_nodes: dict[str, str] = {}
    trusted_receipts: set[str] = set()
    evaluated_receipt_ref = reconciliation.get("evaluated_receipt_ref")
    evaluated_post_action_observation_refs = set(
        reconciliation.get("evaluated_post_action_observation_refs", [])
    )
    for receipt in packet["receipts"]:
        receipt_trusted = (
            trusted_row_binding("ExecutionReceipt", receipt) in trusted_receipt_bindings
        )
        if receipt_trusted:
            trusted_receipts.add(receipt["receipt_id"])
        receipt_node = graph.node(
            "ExecutionReceipt",
            receipt["receipt_id"],
            "evidence_store" if receipt_trusted else "semantic_projection",
            "goal_evaluation",
            status=receipt["status"],
            trusted_receipt=receipt_trusted,
            ownership_status="bound" if receipt_trusted else "unverified_claim",
            **({} if receipt_trusted else {"claimed_native_owner": "evidence_store"}),
        )
        receipt_nodes[receipt["receipt_id"]] = receipt_node
        for evidence_ref in receipt["result_evidence_refs"]:
            graph.edge(
                _evidence_node(graph, evidence_ref),
                "supports" if receipt_trusted else "claims_support",
                receipt_node,
            )
        for observation_ref in receipt["post_action_observation_refs"]:
            observation_node = observation_nodes.get(observation_ref)
            if observation_node is not None:
                graph.edge(
                    receipt_node,
                    "read_back_by"
                    if receipt_trusted
                    and receipt["receipt_id"] == evaluated_receipt_ref
                    and observation_ref in evaluated_post_action_observation_refs
                    else "claims_readback",
                    observation_node,
                )

    gap = reconciliation.get("gap")
    if gap is not None:
        gap_node = graph.node(
            "GoalGap",
            gap["gap_id"],
            "semantic_projection",
            "goal_evaluation",
            gap_type=gap["gap_type"],
            severity=gap["severity"],
        )
        graph.edge(goal_node, "has_gap", gap_node)

    candidate_nodes: dict[str, str] = {}
    for candidate in reconciliation.get("candidates", []):
        candidate_node = graph.node(
            "ActionCandidate",
            candidate["candidate_id"],
            "semantic_projection",
            "capability_action",
            score=candidate["score"],
            authority_status=candidate["authority_status"],
            may_execute=False,
        )
        candidate_nodes[candidate["candidate_id"]] = candidate_node
        action_node = action_nodes.get(candidate["action_ref"])
        if action_node is not None:
            graph.edge(candidate_node, "realizes", action_node)
        graph.edge(candidate_node, "addresses", goal_node)

    intention = reconciliation.get("intention")
    if intention is not None:
        intention_node = graph.node(
            "Intention",
            intention["intention_id"],
            "semantic_projection",
            "capability_action",
            authority_status=intention["authority_status"],
            may_execute=False,
        )
        candidate_node = candidate_nodes.get(intention["candidate_ref"])
        if candidate_node is not None:
            graph.edge(intention_node, "selects", candidate_node)
        if reconciliation["state"] in {"waiting_authority", "blocked", "uncertain"}:
            gate_node = graph.node(
                "Gate",
                f"gate:{intention['authority_status']}",
                "semantic_projection",
                "capability_action",
                status=intention["authority_status"],
                ownership_status="projection_derived",
            )
            graph.edge(intention_node, "blocked_by", gate_node)
        elif reconciliation["state"] == "eligible_for_executor":
            gate_node = graph.node(
                "Gate",
                "gate:external-executor-boundary",
                "semantic_projection",
                "capability_action",
                status="projection_eligible_only",
                ownership_status="projection_derived",
            )
            graph.edge(intention_node, "stops_at", gate_node)
        for receipt, receipt_node in (
            (row, receipt_nodes[row["receipt_id"]]) for row in packet["receipts"]
        ):
            if (
                receipt["intention_ref"] == intention["intention_id"]
                and receipt["action_ref"] == intention["action_ref"]
            ):
                graph.edge(
                    receipt_node,
                    "reports_execution_of"
                    if receipt["receipt_id"] in trusted_receipts
                    else "claims_execution_of",
                    intention_node,
                )

    for boundary in envelope["proof_boundaries"]:
        source = _source_node(
            graph,
            boundary["source_ref"],
            boundary["source_owner"],
        )
        for claim_ref in boundary["cannot_prove"]:
            claim = graph.node(
                "ClaimType", claim_ref, "semantic_projection", "knowledge_evidence"
            )
            graph.edge(source, "claims_cannot_prove", claim, authority_effect="none")

    for mapping in envelope["term_mappings"]:
        scope = mapping["scope"]
        source_ref = mapping["source_ref"]
        source_owner = mapping["source_owner"]
        source = _source_node(graph, source_ref, source_owner)
        preferred = graph.node(
            "PreferredTerm",
            f"{source_ref}#preferred:{scope}:{mapping['preferred']}",
            "semantic_projection",
            "knowledge_evidence",
            term=mapping["preferred"],
            scope=scope,
            source_ref=source_ref,
            ownership_status="unverified_claim",
            claimed_native_owner=source_owner,
        )
        graph.edge(source, "claims_defines_term", preferred)
        for alias in mapping["aliases"]:
            alias_node = graph.node(
                "TermAlias",
                f"{source_ref}#alias:{scope}:{alias}",
                "semantic_projection",
                "knowledge_evidence",
                term=alias,
                scope=scope,
                source_ref=source_ref,
                ownership_status="unverified_claim",
                claimed_native_owner=source_owner,
            )
            graph.edge(source, "claims_defines_term", alias_node)
            graph.edge(alias_node, "claims_alias_of", preferred)

    evaluation = graph.node(
        "Evaluation",
        _stable_id("evaluation", reconciliation),
        "semantic_projection",
        "goal_evaluation",
        state=reconciliation["state"],
        receipt_evaluation=reconciliation["receipt_evaluation"],
    )
    graph.edge(evaluation, "evaluates", goal_node)
    return graph.values()


def _matching_receipt(
    packet: dict[str, Any],
    reconciliation: dict[str, Any],
    trusted_receipt_bindings: frozenset[str],
) -> dict[str, Any] | None:
    evaluated_receipt_ref = reconciliation.get("evaluated_receipt_ref")
    if not isinstance(evaluated_receipt_ref, str):
        return None
    matches = [
        receipt
        for receipt in packet["receipts"]
        if receipt["receipt_id"] == evaluated_receipt_ref
        and trusted_row_binding("ExecutionReceipt", receipt) in trusted_receipt_bindings
    ]
    return matches[0] if matches else None


def _completion_query(
    packet: dict[str, Any],
    reconciliation: dict[str, Any],
    trusted_receipt_bindings: frozenset[str],
) -> dict[str, Any]:
    observations = {row["observation_id"]: row for row in packet["observations"]}
    selected = observations.get(reconciliation.get("selected_observation_ref"))
    receipt = _matching_receipt(packet, reconciliation, trusted_receipt_bindings)
    receipt_evaluation = reconciliation["receipt_evaluation"]
    blockers: list[str] = []

    evaluated_post_action = None
    evaluated_post_action_refs = reconciliation[
        "evaluated_post_action_observation_refs"
    ]
    if evaluated_post_action_refs:
        evaluated_post_action = observations.get(evaluated_post_action_refs[0])
    if receipt_evaluation == "reobserved_converged":
        goal_verified = True
        goal_evidence = (
            [evaluated_post_action["provenance_ref"]]
            if evaluated_post_action is not None
            else []
        )
    elif receipt_evaluation == "reobserved_gap":
        goal_verified = False
        goal_evidence = []
    else:
        goal_verified = reconciliation["state"] == "converged"
        goal_evidence = (
            [selected["provenance_ref"]]
            if goal_verified and selected is not None
            else []
        )
    execution_status = "not_claimed"
    execution_evidence: list[str] = []
    if receipt is not None:
        if receipt_evaluation in {"reobserved_converged", "reobserved_gap"}:
            execution_evidence = list(receipt["result_evidence_refs"])
            for ref in reconciliation["evaluated_post_action_observation_refs"]:
                observation = observations.get(ref)
                if observation is not None:
                    execution_evidence.append(observation["provenance_ref"])
            if receipt_evaluation == "reobserved_converged":
                execution_status = "verified"
            else:
                execution_status = "verified_not_converged"
                blockers.append("post_action_goal_gap_remains")
        elif receipt_evaluation == "uncertain_without_readback":
            execution_status = "uncertain_without_readback"
            blockers.append("fresh_post_action_readback_required")
        elif receipt_evaluation in {"failed", "blocked", "uncertain"}:
            execution_status = receipt_evaluation
            blockers.append(f"execution_{receipt_evaluation}")

    if receipt_evaluation != "reobserved_converged":
        if reconciliation["state"] == "needs_observation":
            blockers.append("fresh_observation_required")
        elif reconciliation["state"] == "waiting_authority":
            blockers.append("exact_authority_required")
        elif reconciliation["state"] == "eligible_for_executor":
            blockers.append("external_executor_required")
        elif reconciliation["state"] == "blocked" and not blockers:
            blockers.append("reconciliation_blocked")
        elif reconciliation["state"] == "uncertain" and not blockers:
            blockers.append("fresh_readback_required")

    return {
        "goal_status": "verified" if goal_verified else "not_verified",
        "execution_status": execution_status,
        "goal_evidence_refs": list(dict.fromkeys(goal_evidence)),
        "execution_evidence_refs": list(dict.fromkeys(execution_evidence)),
        "blockers": sorted(set(blockers)),
    }


def _claim_verdicts(envelope: dict[str, Any]) -> dict[str, Any]:
    blocked: dict[str, set[tuple[str, str]]] = {}
    for boundary in envelope["proof_boundaries"]:
        for claim in boundary["cannot_prove"]:
            blocked.setdefault(claim, set()).add((
                boundary["source_ref"],
                boundary["source_owner"],
            ))
    return {
        claim: {
            "status": "claimed_not_proven",
            "claimed_cannot_prove_sources": [
                {
                    "source_ref": source_ref,
                    "claimed_native_owner": claimed_native_owner,
                    "ownership_status": "unverified_claim",
                }
                for source_ref, claimed_native_owner in sorted(sources)
            ],
            "proof_refs": [],
        }
        for claim, sources in sorted(blocked.items())
    }


def _terminology(envelope: dict[str, Any]) -> dict[str, dict[str, dict[str, str]]]:
    values: dict[str, dict[str, dict[str, str]]] = {}
    for mapping in envelope["term_mappings"]:
        scoped = values.setdefault(mapping["scope"], {})
        for alias in mapping["aliases"]:
            scoped[alias] = {
                "status": "claimed_mapping",
                "preferred": mapping["preferred"],
                "source_ref": mapping["source_ref"],
                "claimed_native_owner": mapping["source_owner"],
                "ownership_status": "unverified_claim",
            }
    return {
        scope: dict(sorted(aliases.items()))
        for scope, aliases in sorted(values.items())
    }


def project_semantic_control(
    envelope: Any,
    *,
    evaluation_time: datetime | None = None,
    trusted_observation_bindings: frozenset[str] = frozenset(),
    trusted_preference_assertion_bindings: frozenset[str] = frozenset(),
    trusted_grant_bindings: frozenset[str] = frozenset(),
    trusted_receipt_bindings: frozenset[str] = frozenset(),
    trusted_post_action_observation_bindings: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Build a deterministic, non-authoritative graph and fixed query bundle."""
    value = _validate_envelope(envelope)
    try:
        reconciliation = reconcile_packet(
            value["reconcile_input"],
            evaluation_time=evaluation_time,
            trusted_observation_bindings=trusted_observation_bindings,
            trusted_preference_assertion_bindings=trusted_preference_assertion_bindings,
            trusted_grant_bindings=trusted_grant_bindings,
            trusted_receipt_bindings=trusted_receipt_bindings,
            trusted_post_action_observation_bindings=trusted_post_action_observation_bindings,
        )
    except ReconciliationValidationError as exc:
        raise SemanticControlValidationError(exc.findings) from exc

    nodes, edges = _build_graph(
        value,
        reconciliation,
        trusted_observation_bindings=trusted_observation_bindings,
        trusted_preference_assertion_bindings=trusted_preference_assertion_bindings,
        trusted_grant_bindings=trusted_grant_bindings,
        trusted_receipt_bindings=trusted_receipt_bindings,
        trusted_post_action_observation_bindings=trusted_post_action_observation_bindings,
    )
    intention = reconciliation.get("intention")
    action_readiness = {
        "state": reconciliation["state"],
        "intention_ref": intention.get("intention_id") if intention else None,
        "action_ref": intention.get("action_ref") if intention else None,
        "authority_status": intention.get("authority_status") if intention else None,
        "may_execute": False,
    }
    if evaluation_time is None:
        raise SemanticControlValidationError([
            _finding(
                "evaluation_time",
                "missing_validated_evaluation_time",
                "validated evaluation time is required",
            )
        ])
    trust_context = {
        "evaluation_time": evaluation_time.isoformat(),
        "trusted_observation_bindings": sorted(trusted_observation_bindings),
        "trusted_preference_assertion_bindings": sorted(
            trusted_preference_assertion_bindings
        ),
        "trusted_grant_bindings": sorted(trusted_grant_bindings),
        "trusted_receipt_bindings": sorted(trusted_receipt_bindings),
        "trusted_post_action_observation_bindings": sorted(
            trusted_post_action_observation_bindings
        ),
    }
    return {
        "ok": True,
        "schema": PROJECTION_SCHEMA,
        "reconciliation": reconciliation,
        "nodes": nodes,
        "edges": edges,
        "queries": {
            "claim_verdicts": _claim_verdicts(value),
            "action_readiness": action_readiness,
            "completion": _completion_query(
                value["reconcile_input"], reconciliation, trusted_receipt_bindings
            ),
            "terminology": _terminology(value),
        },
        "projection": {
            "authoritative": False,
            "rebuildable": True,
            "bound_native_owners_preserved": True,
            "unbound_owner_claims_promoted": False,
            "provenance": {
                "input_schema": ENVELOPE_SCHEMA,
                "generator_contract": CONTRACT_SCHEMA,
                "evaluation_time": evaluation_time.isoformat(),
                "input_sha256": _canonical_sha256(value),
                "trust_context_sha256": _canonical_sha256(trust_context),
            },
        },
        "boundary": dict(_BOUNDARY),
    }


def semantic_validation_error_payload(
    error: SemanticControlValidationError,
) -> dict[str, Any]:
    return {
        "ok": False,
        "schema": PROJECTION_SCHEMA,
        "state": "blocked",
        "findings": error.findings,
        "boundary": dict(_BOUNDARY),
    }
