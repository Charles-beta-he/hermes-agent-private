"""Structured cross-event relation descriptors adjacent to Python replay validators.

These descriptors are the production SSOT for cross-event replay relations:
relation_id, exact source/destination event+path bindings, comparison mode,
validator function, and validator boundary. The generator imports this module;
IDs in effect_ledger_relation_manifests are derived from these rows.

TS does not implement Python ledger replay. Any TS cross-ID list is projection-
only inventory parity and must not claim TS enforcement of these relations.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

# Canonical exact 17-key schema for every structured cross descriptor row.
# Hardcoded independent of runtime introspection of implementation dicts.
EXACT_CROSS_DESCRIPTOR_KEYS_V1: frozenset[str] = frozenset(
    {
        "relation_id",
        "scope",
        "source_bindings",
        "destination_bindings",
        "source_event",
        "source_fact",
        "source_paths",
        "destination_event",
        "destination_path",
        "destination_paths",
        "comparison",
        "validator_function",
        "validator_boundary",
        "validator",
        "focused_test_id",
        "rationale",
        "enforcement",
    }
)

# Non-inventory wildcard / projection envelope paths with closed allowlist.
# These may appear in bindings without existing in the 307 schema path set.
NON_INVENTORY_ENVELOPE_PATH_ALLOWLIST_V1: frozenset[str] = frozenset(
    {
        "projected.status",
        "canonical_event",
    }
)

# Typed receipt / original_receipt leaves (mirrors generator RECEIPT_BODY_FIELDS).
_RECEIPT_BODY_FIELDS: tuple[str, ...] = (
    "adapter_id",
    "authority_binding_digest",
    "command_digest",
    "compensation_ref",
    "effect_id",
    "fence",
    "idempotency_key",
    "operation",
    "payload_digest",
    "post_readback",
    "preflight_readback",
    "receipt_digest",
    "reconcile_ref",
    "result_classification",
    "schema_version",
    "target",
)

_OBS_BODY_FIELDS: tuple[str, ...] = (
    "adapter_result_digest",
    "classification",
    "effect_id",
    "fence",
    "observation_id",
    "observed_at",
    "readback",
    "schema_version",
)


def _receipt_leaf_paths(prefix: str) -> tuple[str, ...]:
    """Exact whole-body leaf paths under an embedded receipt root (incl. root)."""
    out: list[str] = [prefix]
    for field in _RECEIPT_BODY_FIELDS:
        out.append(f"{prefix}.{field}")
        if field == "target":
            out.append(f"{prefix}.target.target_kind")
            out.append(f"{prefix}.target.target_ref")
    return tuple(out)


def _obs_leaf_paths(prefix: str) -> tuple[str, ...]:
    out: list[str] = [prefix]
    for field in _OBS_BODY_FIELDS:
        out.append(f"{prefix}.{field}")
    return tuple(out)


def _typed_children_for_root(root: str) -> tuple[str, ...]:
    """Return all typed children for a known embedded root (excluding the root)."""
    if root.endswith(".receipt") or root.endswith(".original_receipt"):
        return tuple(p for p in _receipt_leaf_paths(root) if p != root)
    if root.endswith(".observation"):
        return tuple(p for p in _obs_leaf_paths(root) if p != root)
    if root.endswith(".readback") or root.endswith(".preflight_readback") or root.endswith(
        ".post_readback"
    ):
        # Dynamic admitted JSON roots: children not typed in path inventory.
        return ()
    return ()


# Claim-retained facts later compared on observation/reconcile/receipt (not intent).
_CLAIM_LATER_FACTS: tuple[str, ...] = (
    "payload.fence",
    "payload.command_digest",
    "payload.request_digest",
    "payload.target_digest",
    "payload.payload_digest",
    "payload.adapter_id",
    "payload.operation",
    "payload.idempotency_key",
)

# Intent-retained authority later compared on observation/reconcile/receipt.
_INTENT_AUTHORITY: tuple[str, ...] = ("payload.authority_binding_digest",)

_CLAIM_TO_INTENT_FACTS: tuple[str, ...] = (
    "payload.command_digest",
    "payload.request_digest",
    "payload.target_digest",
    "payload.payload_digest",
    "payload.adapter_id",
    "payload.operation",
    "payload.idempotency_key",
)

_TERMINAL_RECEIPT_PATHS: tuple[str, ...] = _receipt_leaf_paths("payload.receipt") + (
    "payload.receipt_digest",
)
_ORIGINAL_RECEIPT_PATHS: tuple[str, ...] = _receipt_leaf_paths("payload.original_receipt") + (
    "payload.original_receipt_digest",
)
_OBS_BODY_PATHS: tuple[str, ...] = _obs_leaf_paths("payload.observation")

# Compensation observation→receipt chain: selected comparable fields only (no incomplete roots).
_COMP_OBS_CHAIN_SRC: tuple[str, ...] = _OBS_BODY_PATHS + (
    "payload.classification",
    "payload.readback",
    "payload.readback_digest",
)
_COMP_OBS_CHAIN_DST: tuple[str, ...] = _OBS_BODY_PATHS + (
    "payload.receipt.post_readback",
    "payload.receipt.result_classification",
    "payload.receipt_digest",
    "payload.observation_digest",
)


def _binding(event: str, paths: Sequence[str]) -> dict[str, Any]:
    return {"event": event, "paths": list(paths)}


def _row(
    relation_id: str,
    *,
    sources: Sequence[Mapping[str, Any]],
    destinations: Sequence[Mapping[str, Any]],
    comparison: str,
    validator_function: str,
    validator_boundary: str,
    rationale: str,
) -> dict[str, Any]:
    src_events = sorted({str(s["event"]) for s in sources})
    dst_events = sorted({str(d["event"]) for d in destinations})
    src_paths: list[str] = []
    dst_paths: list[str] = []
    seen_s: set[str] = set()
    seen_d: set[str] = set()
    for s in sources:
        for p in s["paths"]:
            if p not in seen_s:
                seen_s.add(p)
                src_paths.append(p)
    for d in destinations:
        for p in d["paths"]:
            if p not in seen_d:
                seen_d.add(p)
                dst_paths.append(p)
    row = {
        "relation_id": relation_id,
        "scope": "cross_event_python_replay",
        "source_bindings": [dict(s) for s in sources],
        "destination_bindings": [dict(d) for d in destinations],
        "source_event": "|".join(src_events) if src_events != ["*"] else "*",
        "source_fact": ",".join(src_paths),
        "source_paths": src_paths,
        "destination_event": "|".join(dst_events) if dst_events != ["*"] else "*",
        "destination_path": ",".join(dst_paths),
        "destination_paths": dst_paths,
        "comparison": comparison,
        "validator_function": validator_function,
        "validator_boundary": validator_boundary,
        "validator": f"{validator_function} :: {validator_boundary}",
        "focused_test_id": f"test_cross_event_relation_enforced[{relation_id}]",
        "rationale": rationale,
        "enforcement": "python_replay_only",
    }
    if set(row) != EXACT_CROSS_DESCRIPTOR_KEYS_V1:
        raise AssertionError(
            f"descriptor key drift for {relation_id}: "
            f"extra={sorted(set(row) - EXACT_CROSS_DESCRIPTOR_KEYS_V1)} "
            f"missing={sorted(EXACT_CROSS_DESCRIPTOR_KEYS_V1 - set(row))}"
        )
    return row


def _assert_root_child_closure(paths: Sequence[str], *, label: str) -> None:
    """If a typed root is listed, every typed child must also be listed (or root removed)."""
    path_set = set(paths)
    for p in paths:
        children = _typed_children_for_root(p)
        if not children:
            continue
        missing = [c for c in children if c not in path_set]
        if missing:
            raise AssertionError(
                f"{label}: root {p!r} listed without full typed children; missing={missing[:8]}"
            )


# Structured descriptors adjacent to workflow_effect_ledger replay/transition validators.
# Multi-destination / multi-validator branches are split into separate relation IDs.
CROSS_EVENT_RELATION_DESCRIPTORS_V1: tuple[dict[str, Any], ...] = (
    _row(
        "cross_event.effect_id_chain_mixed_effect",
        sources=[_binding("*", ["effect_id"])],
        destinations=[_binding("*", ["effect_id"])],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger._replay",
        validator_boundary="mixed_effect:one effect ledger cannot mix effect_id values",
        rationale="one effect ledger cannot mix effect_id values across the chain",
    ),
    _row(
        "cross_event.state_event_type_transition",
        sources=[_binding("*", ["projected.status"])],
        destinations=[_binding("*", ["event_type"])],
        comparison="transition",
        validator_function="workflow_effect_ledger._validate_transition",
        validator_boundary="invalid_transition:event_type receipt_terminal is not allowed from status claimed",
        rationale="event_type must be allowed from current projected status",
    ),
    _row(
        "cross_event.sequence_contiguity",
        sources=[_binding("*", ["sequence"])],
        destinations=[_binding("*", ["sequence"])],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger._replay",
        validator_boundary="sequence_mismatch:event sequence is not contiguous",
        rationale="sequence must be contiguous per effect ledger chain",
    ),
    _row(
        "cross_event.previous_event_id_link",
        sources=[_binding("*", ["event_id"])],
        destinations=[_binding("*", ["previous_event_id"])],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger._replay",
        validator_boundary="broken_link:previous_event_id does not match tip",
        rationale="previous_event_id must equal tip event_id",
    ),
    _row(
        "cross_event.envelope_idempotency_replay_identity",
        sources=[_binding("*", ["idempotency_key", "canonical_event"])],
        destinations=[_binding("*", ["idempotency_key"])],
        comparison="digest",
        validator_function="workflow_effect_ledger._replay",
        validator_boundary="idempotency_conflict:idempotency key maps to another event",
        rationale="envelope event idempotency_key is event-replay identity",
    ),
    # First/native fence enforcement is append precheck (not later transition message).
    _row(
        "cross_event.claim_to_intent_fence",
        sources=[_binding("claimed", ["payload.fence"])],
        destinations=[_binding("intent_recorded", ["payload.fence"])],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger.append_effect_event_v1",
        validator_boundary="stale_fence:event fence does not match claimed fence",
        rationale="intent fence must match claimed fence; append precheck is first native gate",
    ),
    _row(
        "cross_event.claim_to_intent_complete_facts",
        sources=[_binding("claimed", list(_CLAIM_TO_INTENT_FACTS))],
        destinations=[_binding("intent_recorded", list(_CLAIM_TO_INTENT_FACTS))],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger._validate_transition",
        validator_boundary="payload_invalid:intent command_digest does not match claim",
        rationale="complete claim facts bind subsequent intent",
    ),
    # Exact split by native production branch/message (no combined claim/intent template).
    # Fence hits append precheck first (native boundary), not transition.
    _row(
        "cross_event.claim_to_observation_fence",
        sources=[_binding("claimed", ["payload.fence"])],
        destinations=[_binding("observation_recorded", ["payload.fence"])],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger.append_effect_event_v1",
        validator_boundary="stale_fence:event fence does not match claimed fence",
        rationale="observation fence must match claimed fence; append precheck is first native gate",
    ),
    _row(
        "cross_event.claim_to_observation_facts",
        sources=[_binding("claimed", [p for p in _CLAIM_LATER_FACTS if p != "payload.fence"])],
        destinations=[_binding("observation_recorded", [p for p in _CLAIM_LATER_FACTS if p != "payload.fence"])],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger._validate_transition",
        validator_boundary="payload_invalid:observation command_digest does not match claim",
        rationale="observation compares claim-retained non-fence facts; per-field native messages",
    ),
    _row(
        "cross_event.claim_to_observation_authority",
        sources=[_binding("intent_recorded", list(_INTENT_AUTHORITY))],
        destinations=[_binding("observation_recorded", list(_INTENT_AUTHORITY))],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger._validate_transition",
        validator_boundary="payload_invalid:observation authority_binding_digest does not match intent",
        rationale="observation authority_binding_digest binds intent only",
    ),
    _row(
        "cross_event.claim_to_reconcile_fence",
        sources=[_binding("claimed", ["payload.fence"])],
        destinations=[_binding("reconciled", ["payload.fence"])],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger.append_effect_event_v1",
        validator_boundary="stale_fence:event fence does not match claimed fence",
        rationale="reconcile fence must match claimed fence; append precheck is first native gate",
    ),
    _row(
        "cross_event.claim_to_reconcile_facts",
        sources=[
            _binding("claimed", [p for p in _CLAIM_LATER_FACTS if p != "payload.fence"]),
            _binding("intent_recorded", list(_INTENT_AUTHORITY)),
        ],
        destinations=[
            _binding(
                "reconciled",
                [p for p in _CLAIM_LATER_FACTS if p != "payload.fence"] + list(_INTENT_AUTHORITY),
            )
        ],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger._validate_transition",
        validator_boundary="payload_invalid:reconcile command_digest mismatch",
        rationale="reconcile compares claim-retained non-fence facts + intent authority; per-field messages",
    ),
    _row(
        "cross_event.claim_to_receipt_fence",
        sources=[_binding("claimed", ["payload.fence"])],
        destinations=[_binding("receipt_terminal", ["payload.fence"])],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger.append_effect_event_v1",
        validator_boundary="stale_fence:event fence does not match claimed fence",
        rationale="receipt fence must match claimed fence; append precheck is first native gate",
    ),
    _row(
        "cross_event.claim_to_receipt_facts",
        sources=[
            _binding(
                "claimed",
                [
                    "payload.command_digest",
                    "payload.request_digest",
                    "payload.target_digest",
                    "payload.payload_digest",
                ],
            )
        ],
        destinations=[
            _binding(
                "receipt_terminal",
                [
                    "payload.command_digest",
                    "payload.request_digest",
                    "payload.target_digest",
                    "payload.payload_digest",
                ],
            )
        ],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger._validate_transition",
        validator_boundary="payload_invalid:receipt command_digest does not match claim",
        rationale="receipt claim digest facts; per-field does-not-match-claim messages",
    ),
    _row(
        "cross_event.claim_to_receipt_identity_facts",
        sources=[
            _binding(
                "claimed",
                [
                    "payload.adapter_id",
                    "payload.operation",
                    "payload.idempotency_key",
                ],
            )
        ],
        destinations=[
            _binding(
                "receipt_terminal",
                [
                    "payload.adapter_id",
                    "payload.operation",
                    "payload.idempotency_key",
                ],
            )
        ],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger._validate_transition",
        validator_boundary="payload_invalid:receipt adapter_id mismatch",
        rationale="receipt claim identity facts; native mismatch messages differ from digest template",
    ),
    _row(
        "cross_event.claim_to_receipt_authority",
        sources=[_binding("intent_recorded", list(_INTENT_AUTHORITY))],
        destinations=[_binding("receipt_terminal", list(_INTENT_AUTHORITY))],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger._validate_transition",
        validator_boundary="payload_invalid:receipt authority does not match intent",
        rationale="receipt authority_binding_digest binds intent only",
    ),
    _row(
        "cross_event.receipt_target_body_digest",
        sources=[
            _binding(
                "receipt_terminal",
                [
                    "payload.receipt.target",
                    "payload.receipt.target.target_kind",
                    "payload.receipt.target.target_ref",
                ],
            )
        ],
        destinations=[_binding("receipt_terminal", ["payload.target_digest"])],
        comparison="digest",
        validator_function="effect_contracts._bind_receipt_wrapper_v1",
        validator_boundary="effect_contract_invalid:receipt target_digest does not match receipt target body",
        rationale="wrapper target_digest must equal canonical digest of receipt target body",
    ),
    _row(
        "cross_event.receipt_target_claim_binding",
        sources=[_binding("claimed", ["payload.target_digest"])],
        destinations=[
            _binding(
                "receipt_terminal",
                [
                    "payload.target_digest",
                    "payload.receipt.target",
                    "payload.receipt.target.target_kind",
                    "payload.receipt.target.target_ref",
                ],
            )
        ],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger._validate_transition",
        validator_boundary="payload_invalid:receipt target_digest does not match claim",
        rationale="receipt target body+digest must bind claimed target_digest",
    ),
    _row(
        "cross_event.claim_to_compensation_fence",
        sources=[_binding("claimed", ["payload.fence"])],
        destinations=[_binding("compensation_intent", ["payload.fence"])],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger._validate_transition",
        validator_boundary="payload_invalid:compensation intent fence mismatch",
        rationale="compensation intent fence binds claim fence inside transition (append skips compensation fence precheck)",
    ),
    _row(
        "cross_event.claim_to_compensation_facts",
        sources=[
            _binding(
                "claimed",
                [
                    "payload.command_digest",
                    "payload.target_digest",
                    "payload.payload_digest",
                ],
            )
        ],
        destinations=[
            _binding(
                "compensation_intent",
                [
                    "payload.command_digest",
                    "payload.target_digest",
                    "payload.payload_digest",
                ],
            )
        ],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger._validate_transition",
        validator_boundary="payload_invalid:compensation command_digest mismatch",
        rationale="compensation compares claim command/target/payload digests; per-field mismatch messages",
    ),
    # Split by destination: observation vs receipt (different native messages).
    # Observation validator does not consume compensation_ref — keep it only on receipt path.
    _row(
        "cross_event.compensation_intent_local_facts_to_observation",
        sources=[
            _binding(
                "compensation_intent",
                [
                    "payload.request_digest",
                    "payload.authority_binding_digest",
                    "payload.adapter_id",
                    "payload.operation",
                    "payload.idempotency_key",
                    "payload.command_digest",
                    "payload.target_digest",
                    "payload.payload_digest",
                    "payload.fence",
                ],
            )
        ],
        destinations=[
            _binding(
                "compensation_observation",
                [
                    "payload.request_digest",
                    "payload.authority_binding_digest",
                    "payload.adapter_id",
                    "payload.operation",
                    "payload.idempotency_key",
                    "payload.command_digest",
                    "payload.target_digest",
                    "payload.payload_digest",
                    "payload.fence",
                ],
            ),
        ],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger._validate_transition",
        validator_boundary="payload_invalid:compensation observation request_digest mismatch",
        rationale="compensation-observation local request/authority/adapter continuity",
    ),
    _row(
        "cross_event.compensation_intent_local_facts_to_receipt",
        sources=[
            _binding(
                "compensation_intent",
                [
                    "payload.request_digest",
                    "payload.authority_binding_digest",
                    "payload.adapter_id",
                    "payload.operation",
                    "payload.idempotency_key",
                    "payload.compensation_ref",
                    "payload.command_digest",
                    "payload.target_digest",
                    "payload.payload_digest",
                    "payload.fence",
                ],
            )
        ],
        destinations=[
            _binding(
                "compensation_receipt",
                [
                    "payload.request_digest",
                    "payload.authority_binding_digest",
                    "payload.adapter_id",
                    "payload.operation",
                    "payload.idempotency_key",
                    "payload.command_digest",
                    "payload.target_digest",
                    "payload.payload_digest",
                    "payload.fence",
                    "payload.receipt.compensation_ref",
                    "payload.receipt.target",
                    "payload.receipt.target.target_kind",
                    "payload.receipt.target.target_ref",
                ],
            ),
        ],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger._validate_compensation_receipt_binding_v1",
        validator_boundary="payload_invalid:compensation receipt request_digest mismatch",
        rationale="compensation-receipt local request/authority/adapter continuity via binding validator",
    ),
    _row(
        "cross_event.intent_preflight_to_receipt",
        sources=[
            _binding(
                "intent_recorded",
                ["payload.preflight_readback", "payload.preflight_readback_digest"],
            )
        ],
        destinations=[_binding("receipt_terminal", ["payload.receipt.preflight_readback"])],
        comparison="whole_body",
        validator_function="workflow_effect_ledger._require_equal_readback_fact",
        validator_boundary="payload_invalid:receipt.preflight_readback does not match durable fact",
        rationale="terminal preflight equals durable intent preflight",
    ),
    # Reconciled has NO observation body — only observation_recorded sources observation leaves.
    _row(
        "cross_event.observation_to_receipt_body",
        sources=[
            _binding("observation_recorded", list(_OBS_BODY_PATHS)),
        ],
        destinations=[_binding("receipt_terminal", list(_OBS_BODY_PATHS))],
        comparison="whole_body",
        validator_function="workflow_effect_ledger._validate_transition",
        validator_boundary="payload_invalid:receipt observation does not match durable observation",
        rationale="embedded observation body equals durable observation",
    ),
    # Split classification sources into separate exact boundaries.
    _row(
        "cross_event.observation_to_receipt_classification",
        sources=[
            _binding("observation_recorded", ["payload.classification"]),
        ],
        destinations=[_binding("receipt_terminal", ["payload.result_classification"])],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger._validate_transition",
        validator_boundary="payload_invalid:receipt classification does not match prior observation/reconcile",
        rationale="receipt classification matches durable observation classification",
    ),
    _row(
        "cross_event.reconcile_to_receipt_classification",
        sources=[
            _binding("reconciled", ["payload.classification"]),
        ],
        destinations=[_binding("receipt_terminal", ["payload.result_classification"])],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger._validate_transition",
        validator_boundary="payload_invalid:receipt classification does not match prior observation/reconcile",
        rationale="after reconcile, receipt classification matches durable reconcile classification",
    ),
    _row(
        "cross_event.observation_to_receipt_post_readback",
        sources=[
            _binding("observation_recorded", ["payload.readback", "payload.readback_digest"])
        ],
        destinations=[_binding("receipt_terminal", ["payload.receipt.post_readback"])],
        comparison="whole_body",
        validator_function="workflow_effect_ledger._require_equal_readback_fact",
        validator_boundary="payload_invalid:receipt.post_readback does not match durable fact",
        rationale="terminal post_readback equals durable observation readback when no reconcile",
    ),
    _row(
        "cross_event.reconcile_to_receipt_post_readback",
        sources=[_binding("reconciled", ["payload.readback", "payload.readback_digest"])],
        destinations=[_binding("receipt_terminal", ["payload.receipt.post_readback"])],
        comparison="whole_body",
        validator_function="workflow_effect_ledger._require_equal_readback_fact",
        validator_boundary="payload_invalid:receipt.post_readback does not match durable fact",
        rationale="after reconcile, terminal post_readback equals durable reconcile readback",
    ),
    # Applied-terminal: ONLY status/event_type transition inputs (no whole receipt bodies).
    _row(
        "cross_event.compensation_requires_applied_terminal_state",
        sources=[
            _binding(
                "receipt_terminal",
                ["projected.status"],
            )
        ],
        destinations=[
            _binding(
                "compensation_intent",
                ["event_type"],
            )
        ],
        comparison="transition",
        validator_function="workflow_effect_ledger._validate_transition",
        validator_boundary="invalid_transition:event_type compensation_intent is not allowed from status not_applied",
        rationale="compensation requires applied terminal projected status",
    ),
    # Whole original/terminal receipt bodies live on bind relations (not applied-terminal).
    _row(
        "cross_event.compensation_original_receipt_bind_observation",
        sources=[
            _binding("receipt_terminal", list(_TERMINAL_RECEIPT_PATHS)),
            _binding("compensation_intent", list(_ORIGINAL_RECEIPT_PATHS)),
        ],
        destinations=[
            _binding("compensation_observation", list(_ORIGINAL_RECEIPT_PATHS)),
        ],
        comparison="whole_body",
        validator_function="workflow_effect_ledger._validate_transition",
        validator_boundary="payload_invalid:compensation observation original receipt mismatch",
        rationale="compensation observation binds original applied terminal receipt body",
    ),
    _row(
        "cross_event.compensation_original_receipt_bind_receipt",
        sources=[
            _binding("receipt_terminal", list(_TERMINAL_RECEIPT_PATHS)),
            _binding("compensation_intent", list(_ORIGINAL_RECEIPT_PATHS)),
        ],
        destinations=[
            _binding("compensation_receipt", list(_ORIGINAL_RECEIPT_PATHS)),
        ],
        comparison="whole_body",
        validator_function="workflow_effect_ledger._validate_compensation_receipt_binding_v1",
        validator_boundary="payload_invalid:compensation receipt original binding mismatch",
        rationale="compensation receipt binds original applied terminal receipt via binding validator",
    ),
    _row(
        "cross_event.compensation_fence_to_observation",
        sources=[_binding("compensation_intent", ["payload.fence"])],
        destinations=[
            _binding("compensation_observation", ["payload.fence"]),
        ],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger._validate_transition",
        validator_boundary="payload_invalid:compensation observation fence mismatch",
        rationale="compensation observation fence continues compensation intent fence",
    ),
    _row(
        "cross_event.compensation_fence_to_receipt",
        sources=[_binding("compensation_intent", ["payload.fence"])],
        destinations=[
            _binding("compensation_receipt", ["payload.fence"]),
        ],
        comparison="direct_equality",
        validator_function="workflow_effect_ledger._validate_compensation_receipt_binding_v1",
        validator_boundary="payload_invalid:compensation receipt fence mismatch",
        rationale="compensation receipt fence continues compensation intent fence via binding validator",
    ),
    _row(
        "cross_event.compensation_intent_preflight_to_compensation_receipt",
        sources=[
            _binding(
                "compensation_intent",
                ["payload.preflight_readback", "payload.preflight_readback_digest"],
            )
        ],
        destinations=[
            _binding(
                "compensation_receipt",
                ["payload.receipt.preflight_readback"],
            )
        ],
        comparison="whole_body",
        validator_function="workflow_effect_ledger._require_equal_readback_fact",
        validator_boundary="payload_invalid:compensation_receipt.preflight_readback does not match durable fact",
        rationale="compensation receipt preflight equals durable compensation intent preflight",
    ),
    _row(
        "cross_event.compensation_observation_to_receipt_chain",
        sources=[_binding("compensation_observation", list(_COMP_OBS_CHAIN_SRC))],
        destinations=[_binding("compensation_receipt", list(_COMP_OBS_CHAIN_DST))],
        comparison="whole_body",
        validator_function="workflow_effect_ledger._validate_compensation_receipt_binding_v1",
        validator_boundary="payload_invalid:compensation receipt observation does not match prior",
        rationale="compensation receipt observation/classification/post_readback chain",
    ),
)


def _validate_descriptor_table() -> None:
    ids = [r["relation_id"] for r in CROSS_EVENT_RELATION_DESCRIPTORS_V1]
    if len(ids) != len(set(ids)):
        raise AssertionError(f"duplicate cross relation ids: {ids}")
    for r in CROSS_EVENT_RELATION_DESCRIPTORS_V1:
        if set(r) != EXACT_CROSS_DESCRIPTOR_KEYS_V1:
            raise AssertionError(r["relation_id"])
        for b in list(r["source_bindings"]) + list(r["destination_bindings"]):
            _assert_root_child_closure(b["paths"], label=f"{r['relation_id']}:{b['event']}")


_validate_descriptor_table()


def cross_event_relation_descriptor_ids() -> list[str]:
    return [str(r["relation_id"]) for r in CROSS_EVENT_RELATION_DESCRIPTORS_V1]


def cross_event_relation_descriptors() -> list[dict[str, Any]]:
    return [dict(r) for r in CROSS_EVENT_RELATION_DESCRIPTORS_V1]


# Hardcoded negative memberships: eight intent claim-copy fields must NOT join
# observation/reconcile/receipt claim relations (24 false pairs).
INTENT_CLAIM_COPY_PATHS: tuple[str, ...] = (
    "payload.fence",
    "payload.command_digest",
    "payload.request_digest",
    "payload.target_digest",
    "payload.payload_digest",
    "payload.adapter_id",
    "payload.operation",
    "payload.idempotency_key",
)
FALSE_INTENT_CROSS_RELATIONS: tuple[str, ...] = (
    "cross_event.claim_to_observation_facts",
    "cross_event.claim_to_reconcile_facts",
    "cross_event.claim_to_receipt_facts",
    "cross_event.receipt_target_claim_binding",
)
FALSE_INTENT_MEMBERSHIPS_V1: tuple[tuple[str, str, str], ...] = tuple(
    ("intent_recorded", path, rid)
    for path in INTENT_CLAIM_COPY_PATHS
    for rid in FALSE_INTENT_CROSS_RELATIONS
)
assert len(FALSE_INTENT_MEMBERSHIPS_V1) == 32
