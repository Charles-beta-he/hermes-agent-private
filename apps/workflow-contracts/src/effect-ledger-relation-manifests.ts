/**
 * Independently maintained validator relation manifests (TypeScript).
 *
 * Intra IDs: adjacent to / enforced by workflow-effect public parse.
 * Cross IDs: projection-only inventory parity with Python structured
 * descriptors. TypeScript does NOT implement Python ledger replay; equality
 * checks against SSOT/Python IDs must not claim TS enforces cross-event replay.
 * NOT derived from the checked-in SSOT fixture.
 */
export const INTRA_EVENT_RELATION_MANIFEST_V1: readonly string[] = [
  'claimed.envelope_effect_id_eq_payload_effect_id',
  'claimed.event_id_derivation',
  'claimed.schema_version_fixed',
  'intent_recorded.envelope_effect_id_eq_payload_effect_id',
  'intent_recorded.event_id_derivation',
  'intent_recorded.preflight_readback_digest_eq_canonical',
  'intent_recorded.schema_version_fixed',
  'observation_recorded.body_effect_id_eq_wrapper',
  'observation_recorded.envelope_effect_id_eq_payload_effect_id',
  'observation_recorded.event_id_derivation',
  'observation_recorded.readback_digest_eq_canonical',
  'observation_recorded.schema_version_fixed',
  'observation_recorded.wrapper_adapter_result_digest_eq_body',
  'observation_recorded.wrapper_classification_eq_body',
  'observation_recorded.wrapper_fence_eq_body',
  'observation_recorded.wrapper_readback_eq_body',
  'reconciled.envelope_effect_id_eq_payload_effect_id',
  'reconciled.event_id_derivation',
  'reconciled.readback_digest_eq_canonical',
  'reconciled.schema_version_fixed',
  'receipt_terminal.body_effect_id_eq_wrapper',
  'receipt_terminal.embedded_receipt_digest_hashes_body',
  'receipt_terminal.envelope_effect_id_eq_payload_effect_id',
  'receipt_terminal.event_id_derivation',
  'receipt_terminal.observation_digest_eq_canonical',
  'receipt_terminal.observation_effect_id_eq_wrapper',
  'receipt_terminal.observation_fence_eq_receipt_fence',
  'receipt_terminal.schema_version_fixed',
  'receipt_terminal.target_digest_eq_receipt_target_body',
  'receipt_terminal.wrapper_adapter_id_eq_body',
  'receipt_terminal.wrapper_authority_eq_body',
  'receipt_terminal.wrapper_classification_eq_body',
  'receipt_terminal.wrapper_command_digest_eq_body',
  'receipt_terminal.wrapper_fence_eq_body',
  'receipt_terminal.wrapper_idempotency_key_eq_body',
  'receipt_terminal.wrapper_operation_eq_body',
  'receipt_terminal.wrapper_payload_digest_eq_body',
  'receipt_terminal.wrapper_receipt_digest_eq_body',
  'compensation_intent.envelope_effect_id_eq_payload_effect_id',
  'compensation_intent.event_id_derivation',
  'compensation_intent.original_effect_id_eq_wrapper',
  'compensation_intent.original_must_be_applied',
  'compensation_intent.original_receipt_digest_eq_body',
  'compensation_intent.original_receipt_digest_hashes_body',
  'compensation_intent.preflight_readback_digest_eq_canonical',
  'compensation_intent.schema_version_fixed',
  'compensation_observation.body_effect_id_eq_wrapper',
  'compensation_observation.envelope_effect_id_eq_payload_effect_id',
  'compensation_observation.event_id_derivation',
  'compensation_observation.original_must_be_applied',
  'compensation_observation.original_receipt_digest_eq_body',
  'compensation_observation.original_receipt_digest_hashes_body',
  'compensation_observation.original_receipt_effect_id_eq_payload',
  'compensation_observation.readback_digest_eq_canonical',
  'compensation_observation.schema_version_fixed',
  'compensation_observation.wrapper_classification_eq_body',
  'compensation_observation.wrapper_fence_eq_body',
  'compensation_observation.wrapper_readback_eq_body',
  'compensation_receipt.body_effect_id_eq_wrapper',
  'compensation_receipt.embedded_receipt_digest_hashes_body',
  'compensation_receipt.envelope_effect_id_eq_payload_effect_id',
  'compensation_receipt.event_id_derivation',
  'compensation_receipt.observation_classification_eq_receipt',
  'compensation_receipt.observation_digest_eq_canonical',
  'compensation_receipt.observation_effect_id_eq_wrapper',
  'compensation_receipt.observation_fence_eq_payload',
  'compensation_receipt.original_effect_id_eq_wrapper',
  'compensation_receipt.original_must_be_applied',
  'compensation_receipt.original_receipt_digest_eq_body',
  'compensation_receipt.original_receipt_digest_hashes_body',
  'compensation_receipt.post_readback_eq_observation_readback',
  'compensation_receipt.schema_version_fixed',
  'compensation_receipt.target_digest_eq_receipt_target_body',
  'compensation_receipt.wrapper_adapter_id_eq_body',
  'compensation_receipt.wrapper_authority_eq_body',
  'compensation_receipt.wrapper_command_digest_eq_body',
  'compensation_receipt.wrapper_fence_eq_body',
  'compensation_receipt.wrapper_idempotency_key_eq_body',
  'compensation_receipt.wrapper_operation_eq_body',
  'compensation_receipt.wrapper_payload_digest_eq_body',
  'compensation_receipt.wrapper_receipt_digest_eq_body',
] as const

/**
 * Projection-only cross-event relation ID inventory (parity with Python).
 * enforcement: 'projection_only' — TS parser/intra only; Python replay enforces.
 */
export const CROSS_EVENT_RELATION_MANIFEST_ENFORCEMENT = 'projection_only' as const

export const CROSS_EVENT_RELATION_MANIFEST_V1: readonly string[] = [
  'cross_event.effect_id_chain_mixed_effect',
  'cross_event.state_event_type_transition',
  'cross_event.sequence_contiguity',
  'cross_event.previous_event_id_link',
  'cross_event.envelope_idempotency_replay_identity',
  'cross_event.claim_to_intent_fence',
  'cross_event.claim_to_intent_complete_facts',
  'cross_event.claim_to_observation_fence',
  'cross_event.claim_to_observation_facts',
  'cross_event.claim_to_observation_authority',
  'cross_event.claim_to_reconcile_fence',
  'cross_event.claim_to_reconcile_facts',
  'cross_event.claim_to_receipt_fence',
  'cross_event.claim_to_receipt_facts',
  'cross_event.claim_to_receipt_identity_facts',
  'cross_event.claim_to_receipt_authority',
  'cross_event.receipt_target_body_digest',
  'cross_event.receipt_target_claim_binding',
  'cross_event.claim_to_compensation_fence',
  'cross_event.claim_to_compensation_facts',
  'cross_event.compensation_intent_local_facts_to_observation',
  'cross_event.compensation_intent_local_facts_to_receipt',
  'cross_event.intent_preflight_to_receipt',
  'cross_event.observation_to_receipt_body',
  'cross_event.observation_to_receipt_classification',
  'cross_event.reconcile_to_receipt_classification',
  'cross_event.observation_to_receipt_post_readback',
  'cross_event.reconcile_to_receipt_post_readback',
  'cross_event.compensation_requires_applied_terminal_state',
  'cross_event.compensation_original_receipt_bind_observation',
  'cross_event.compensation_original_receipt_bind_receipt',
  'cross_event.compensation_fence_to_observation',
  'cross_event.compensation_fence_to_receipt',
  'cross_event.compensation_intent_preflight_to_compensation_receipt',
  'cross_event.compensation_observation_to_receipt_chain',
] as const

/**
 * Map parse/Zod failures to shared reason classes.
 * Structured Zod issues preferred over substring drift.
 */
export function mapWorkflowEffectLedgerTsReason(err: unknown): string {
  const issues =
    err && typeof err === 'object' && Array.isArray((err as { issues?: unknown }).issues)
      ? ((err as { issues: Array<{ path?: unknown[]; message?: string; code?: string }> }).issues)
      : null
  if (issues && issues.length > 0) {
    for (const issue of issues) {
      const path = Array.isArray(issue.path) ? issue.path.map(String) : []
      // Envelope event_type invalid → schema_invalid
      if (path.length === 1 && path[0] === 'event_type') return 'schema_invalid'
      // Unsupported/invalid schema_version → schema_invalid
      if (path.length === 1 && path[0] === 'schema_version') return 'schema_invalid'
      // Payload enum/classification (path may be relative to payload parse)
      if (path.includes('classification') || path.includes('result_classification')) {
        return 'payload_invalid'
      }
      if (path.includes('payload') || path.length === 0) {
        // continue scanning; default payload below
      }
    }
    // Any other Zod issue on envelope roots that isn't event_type/schema_version
    // is still a schema shape failure if top-level non-payload; payload paths stay payload_invalid.
    const top = issues[0]
    const p0 = Array.isArray(top?.path) && top.path.length ? String(top.path[0]) : ''
    if (p0 === 'event_type' || p0 === 'schema_version') return 'schema_invalid'
    return 'payload_invalid'
  }
  const message = err instanceof Error ? err.message : String(err)
  const m = message.toLowerCase()
  if (m.includes('event_id') && m.includes('match')) return 'identity_mismatch'
  if (m.includes('schema_version')) return 'schema_invalid'
  if (
    (m.includes('unsupported') && m.includes('event_type') && !m.includes('classification')) ||
    m.includes('unsupported effect ledger event_type')
  ) {
    return 'schema_invalid'
  }
  // ZodError stringified JSON may embed "path":["event_type"]
  if (m.includes('"path":["event_type"]') || m.includes('"path": [ "event_type" ]')) {
    return 'schema_invalid'
  }
  if (m.includes('"path":["schema_version"]') || m.includes('"path": [ "schema_version" ]')) {
    return 'schema_invalid'
  }
  return 'payload_invalid'
}
