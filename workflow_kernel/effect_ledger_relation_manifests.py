"""Independently maintained validator relation manifests (Python).

Intra IDs are adjacent to / enforced by public parse paths.
Cross IDs are derived from structured descriptors adjacent to Python replay
validators (effect_ledger_cross_relation_descriptors). They are NOT derived
from the checked-in SSOT fixture. The SSOT generator must compare its
intra/cross sets against these manifests and import structured descriptors.
"""
from __future__ import annotations

from workflow_kernel.effect_ledger_cross_relation_descriptors import (
    CROSS_EVENT_RELATION_DESCRIPTORS_V1,
    cross_event_relation_descriptor_ids,
)

# Full intra-event relation ID set enforced by public parse (corpus twins).
# Independently maintained; generator --check requires SSOT.relations exact-equal.
INTRA_EVENT_RELATION_MANIFEST_V1: frozenset[str] = frozenset(
    {
        "claimed.envelope_effect_id_eq_payload_effect_id",
        "claimed.event_id_derivation",
        "claimed.schema_version_fixed",
        "intent_recorded.envelope_effect_id_eq_payload_effect_id",
        "intent_recorded.event_id_derivation",
        "intent_recorded.preflight_readback_digest_eq_canonical",
        "intent_recorded.schema_version_fixed",
        "observation_recorded.body_effect_id_eq_wrapper",
        "observation_recorded.envelope_effect_id_eq_payload_effect_id",
        "observation_recorded.event_id_derivation",
        "observation_recorded.readback_digest_eq_canonical",
        "observation_recorded.schema_version_fixed",
        "observation_recorded.wrapper_adapter_result_digest_eq_body",
        "observation_recorded.wrapper_classification_eq_body",
        "observation_recorded.wrapper_fence_eq_body",
        "observation_recorded.wrapper_readback_eq_body",
        "reconciled.envelope_effect_id_eq_payload_effect_id",
        "reconciled.event_id_derivation",
        "reconciled.readback_digest_eq_canonical",
        "reconciled.schema_version_fixed",
        "receipt_terminal.body_effect_id_eq_wrapper",
        "receipt_terminal.embedded_receipt_digest_hashes_body",
        "receipt_terminal.envelope_effect_id_eq_payload_effect_id",
        "receipt_terminal.event_id_derivation",
        "receipt_terminal.observation_digest_eq_canonical",
        "receipt_terminal.observation_effect_id_eq_wrapper",
        "receipt_terminal.observation_fence_eq_receipt_fence",
        "receipt_terminal.schema_version_fixed",
        "receipt_terminal.target_digest_eq_receipt_target_body",
        "receipt_terminal.wrapper_adapter_id_eq_body",
        "receipt_terminal.wrapper_authority_eq_body",
        "receipt_terminal.wrapper_classification_eq_body",
        "receipt_terminal.wrapper_command_digest_eq_body",
        "receipt_terminal.wrapper_fence_eq_body",
        "receipt_terminal.wrapper_idempotency_key_eq_body",
        "receipt_terminal.wrapper_operation_eq_body",
        "receipt_terminal.wrapper_payload_digest_eq_body",
        "receipt_terminal.wrapper_receipt_digest_eq_body",
        "compensation_intent.envelope_effect_id_eq_payload_effect_id",
        "compensation_intent.event_id_derivation",
        "compensation_intent.original_effect_id_eq_wrapper",
        "compensation_intent.original_must_be_applied",
        "compensation_intent.original_receipt_digest_eq_body",
        "compensation_intent.original_receipt_digest_hashes_body",
        "compensation_intent.preflight_readback_digest_eq_canonical",
        "compensation_intent.schema_version_fixed",
        "compensation_observation.body_effect_id_eq_wrapper",
        "compensation_observation.envelope_effect_id_eq_payload_effect_id",
        "compensation_observation.event_id_derivation",
        "compensation_observation.original_must_be_applied",
        "compensation_observation.original_receipt_digest_eq_body",
        "compensation_observation.original_receipt_digest_hashes_body",
        "compensation_observation.original_receipt_effect_id_eq_payload",
        "compensation_observation.readback_digest_eq_canonical",
        "compensation_observation.schema_version_fixed",
        "compensation_observation.wrapper_classification_eq_body",
        "compensation_observation.wrapper_fence_eq_body",
        "compensation_observation.wrapper_readback_eq_body",
        "compensation_receipt.body_effect_id_eq_wrapper",
        "compensation_receipt.embedded_receipt_digest_hashes_body",
        "compensation_receipt.envelope_effect_id_eq_payload_effect_id",
        "compensation_receipt.event_id_derivation",
        "compensation_receipt.observation_classification_eq_receipt",
        "compensation_receipt.observation_digest_eq_canonical",
        "compensation_receipt.observation_effect_id_eq_wrapper",
        "compensation_receipt.observation_fence_eq_payload",
        "compensation_receipt.original_effect_id_eq_wrapper",
        "compensation_receipt.original_must_be_applied",
        "compensation_receipt.original_receipt_digest_eq_body",
        "compensation_receipt.original_receipt_digest_hashes_body",
        "compensation_receipt.post_readback_eq_observation_readback",
        "compensation_receipt.schema_version_fixed",
        "compensation_receipt.target_digest_eq_receipt_target_body",
        "compensation_receipt.wrapper_adapter_id_eq_body",
        "compensation_receipt.wrapper_authority_eq_body",
        "compensation_receipt.wrapper_command_digest_eq_body",
        "compensation_receipt.wrapper_fence_eq_body",
        "compensation_receipt.wrapper_idempotency_key_eq_body",
        "compensation_receipt.wrapper_operation_eq_body",
        "compensation_receipt.wrapper_payload_digest_eq_body",
        "compensation_receipt.wrapper_receipt_digest_eq_body",
    }
)

# Cross-event relation IDs derived from structured descriptors adjacent to replay.
CROSS_EVENT_RELATION_MANIFEST_V1: frozenset[str] = frozenset(cross_event_relation_descriptor_ids())

# Re-export structured descriptors for generator/tests (no circular SSOT generation).
CROSS_EVENT_RELATION_DESCRIPTORS = CROSS_EVENT_RELATION_DESCRIPTORS_V1


def cross_event_relation_ids() -> list[str]:
    return sorted(CROSS_EVENT_RELATION_MANIFEST_V1)


def intra_event_relation_ids() -> list[str]:
    return sorted(INTRA_EVENT_RELATION_MANIFEST_V1)
