import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { types as nodeTypes } from 'node:util'

import { z } from 'zod'

import {
  WorkflowIdentifierV1Schema,
  WorkflowPortableNonBlankStringV1Schema,
  WorkflowSideEffectV1Schema,
} from './workflow-definition'

const Sha256DigestV1Schema = z.string().regex(/^sha256:[0-9a-f]{64}$/)
const EffectLedgerEventIdV1Schema = z.string().regex(/^wel_[0-9a-f]{64}$/)
const ExecutionEventV2IdSchema = z.string().regex(/^we2_[0-9a-f]{64}$/)

/** Shared with Python: seconds required, optional fraction, Z or ±HH:MM; year 0001-9999. */
export const WORKFLOW_ISO_TIMESTAMP_RE =
  /^(?:000[1-9]|00[1-9]\d|0[1-9]\d{2}|[1-9]\d{3})-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/

export const JS_SAFE_INTEGER_MAX = Number.MAX_SAFE_INTEGER
export const JS_SAFE_INTEGER_MIN = Number.MIN_SAFE_INTEGER

/** Recursive admitted JSON domain: null/bool/string/array/plain-object + JS-safe integers only. */
export type AdmittedJsonValueV1 =
  | null
  | boolean
  | string
  | number
  | AdmittedJsonValueV1[]
  | { [key: string]: AdmittedJsonValueV1 }

/**
 * Pair-aware Unicode scalar pattern for Zod + JSON Schema.
 * Rejects lone UTF-16 surrogates while accepting valid surrogate pairs / U+10000 / emoji.
 * Avoids the JS regex pitfall where a bare [\\uD800-\\uDFFF] class rejects valid pairs.
 */
export const UNICODE_SCALAR_STRING_PATTERN =
  /^(?:[^\uD800-\uDFFF]|[\uD800-\uDBFF][\uDC00-\uDFFF])*$/

const UnicodeScalarStringV1Schema = z
  .string()
  .regex(
    UNICODE_SCALAR_STRING_PATTERN,
    'string must be Unicode scalar values (no unpaired UTF-16 surrogates)',
  )
  .superRefine((value, ctx) => {
    try {
      assertUnicodeScalarStringV1(value)
    } catch (error) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: error instanceof Error ? error.message : 'unpaired UTF-16 surrogate',
      })
    }
  })

export const AdmittedJsonValueV1Schema: z.ZodType<AdmittedJsonValueV1> = z.lazy(() =>
  z.union([
    z.null(),
    z.boolean(),
    UnicodeScalarStringV1Schema,
    z.number().int().gte(JS_SAFE_INTEGER_MIN).lte(JS_SAFE_INTEGER_MAX),
    z.array(AdmittedJsonValueV1Schema),
    z.record(UnicodeScalarStringV1Schema, AdmittedJsonValueV1Schema),
  ]),
)

export const AdmittedJsonObjectV1Schema = z.record(UnicodeScalarStringV1Schema, AdmittedJsonValueV1Schema)

function assertValidWorkflowIsoTimestamp(value: string): string {
  if (!WORKFLOW_ISO_TIMESTAMP_RE.test(value)) {
    throw new Error('ISO-8601 datetime with seconds and timezone offset')
  }
  const match = value.match(
    /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(?:Z|[+-]\d{2}:\d{2})$/,
  )
  if (!match) {
    throw new Error('timestamp is not a valid calendar datetime')
  }
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  const hour = Number(match[4])
  const minute = Number(match[5])
  const second = Number(match[6])
  if (year < 1 || year > 9999) {
    throw new Error('timestamp year must be in 0001-9999 (year 0000 rejected)')
  }
  // Do NOT use Date.UTC(year, ...) for years 0-99: ECMAScript maps 0-99 -> 1900-1999.
  const utc = new Date(0)
  utc.setUTCFullYear(year, month - 1, day)
  utc.setUTCHours(hour, minute, second, 0)
  if (
    utc.getUTCFullYear() !== year ||
    utc.getUTCMonth() !== month - 1 ||
    utc.getUTCDate() !== day ||
    utc.getUTCHours() !== hour ||
    utc.getUTCMinutes() !== minute ||
    utc.getUTCSeconds() !== second
  ) {
    throw new Error('timestamp is not a valid calendar datetime')
  }
  // Also reject values Date.parse cannot represent as the same civil components.
  const normalized = value.endsWith('Z') ? `${value.slice(0, -1)}+00:00` : value
  const ms = Date.parse(normalized)
  if (Number.isNaN(ms)) {
    throw new Error('timestamp is not a valid calendar datetime')
  }
  return value
}

const WorkflowIsoTimestampV1Schema = z
  .string()
  .regex(WORKFLOW_ISO_TIMESTAMP_RE, 'ISO-8601 datetime with seconds and timezone offset')
  .superRefine((value, ctx) => {
    try {
      assertValidWorkflowIsoTimestamp(value)
    } catch (error) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: error instanceof Error ? error.message : 'invalid timestamp',
      })
    }
  })

const JsSafeIntSchema = z
  .number()
  .int()
  .gte(JS_SAFE_INTEGER_MIN)
  .lte(JS_SAFE_INTEGER_MAX)

const PositiveJsSafeIntSchema = z.number().int().gte(1).lte(JS_SAFE_INTEGER_MAX)

export const EffectTargetV1Schema = z.strictObject({
  target_kind: WorkflowPortableNonBlankStringV1Schema,
  target_ref: WorkflowPortableNonBlankStringV1Schema,
})

export const EffectCommandV1Schema = z
  .strictObject({
    schema_version: z.literal('effect_command_v1'),
    command_id: WorkflowPortableNonBlankStringV1Schema,
    adapter_id: WorkflowIdentifierV1Schema,
    operation: WorkflowIdentifierV1Schema,
    target: EffectTargetV1Schema,
    payload_digest: Sha256DigestV1Schema,
    idempotency_key: WorkflowPortableNonBlankStringV1Schema,
    issuer_id: WorkflowPortableNonBlankStringV1Schema,
    policy_revision: WorkflowPortableNonBlankStringV1Schema,
    config_revision: WorkflowPortableNonBlankStringV1Schema,
    side_effect_tier: WorkflowSideEffectV1Schema,
    compensation_ref: WorkflowPortableNonBlankStringV1Schema,
    reconcile_ref: WorkflowPortableNonBlankStringV1Schema,
  })
  .meta({ title: 'Hermes EffectCommandV1' })

export const EffectObservationV1Schema = z
  .strictObject({
    schema_version: z.literal('effect_observation_v1'),
    effect_id: WorkflowPortableNonBlankStringV1Schema,
    observation_id: WorkflowPortableNonBlankStringV1Schema,
    classification: z.enum(['applied', 'not_applied', 'unknown']),
    fence: PositiveJsSafeIntSchema,
    observed_at: WorkflowIsoTimestampV1Schema,
    adapter_result_digest: Sha256DigestV1Schema.nullable(),
    readback: AdmittedJsonObjectV1Schema,
  })
  .meta({ title: 'Hermes EffectObservationV1' })

export const EffectReceiptV1Schema = z
  .strictObject({
    schema_version: z.literal('effect_receipt_v1'),
    effect_id: WorkflowPortableNonBlankStringV1Schema,
    command_digest: Sha256DigestV1Schema,
    adapter_id: WorkflowIdentifierV1Schema,
    operation: WorkflowIdentifierV1Schema,
    target: EffectTargetV1Schema,
    payload_digest: Sha256DigestV1Schema,
    authority_binding_digest: Sha256DigestV1Schema,
    idempotency_key: WorkflowPortableNonBlankStringV1Schema,
    fence: PositiveJsSafeIntSchema,
    preflight_readback: AdmittedJsonObjectV1Schema,
    post_readback: AdmittedJsonObjectV1Schema,
    result_classification: z.enum(['applied', 'not_applied']),
    compensation_ref: WorkflowPortableNonBlankStringV1Schema,
    reconcile_ref: WorkflowPortableNonBlankStringV1Schema,
    receipt_digest: Sha256DigestV1Schema,
  })
  .meta({ title: 'Hermes EffectReceiptV1' })

const ClaimedPayloadSchema = z
  .strictObject({
    command_digest: Sha256DigestV1Schema,
    target_digest: Sha256DigestV1Schema,
    owner: WorkflowPortableNonBlankStringV1Schema,
    request_digest: Sha256DigestV1Schema,
    idempotency_key: WorkflowPortableNonBlankStringV1Schema,
    adapter_id: WorkflowIdentifierV1Schema,
    operation: WorkflowIdentifierV1Schema,
    fence: PositiveJsSafeIntSchema,
    payload_digest: Sha256DigestV1Schema,
    effect_id: WorkflowPortableNonBlankStringV1Schema,
  })
  .meta({ title: 'Hermes EffectClaimedPayloadV1' })

const IntentPayloadSchema = z
  .strictObject({
    fence: PositiveJsSafeIntSchema,
    command_digest: Sha256DigestV1Schema,
    request_digest: Sha256DigestV1Schema,
    target_digest: Sha256DigestV1Schema,
    authority_binding_digest: Sha256DigestV1Schema,
    preflight_readback: AdmittedJsonObjectV1Schema,
    preflight_readback_digest: Sha256DigestV1Schema,
    adapter_id: WorkflowIdentifierV1Schema,
    operation: WorkflowIdentifierV1Schema,
    payload_digest: Sha256DigestV1Schema,
    idempotency_key: WorkflowPortableNonBlankStringV1Schema,
    effect_id: WorkflowPortableNonBlankStringV1Schema,
  })
  .meta({ title: 'Hermes EffectIntentPayloadV1' })

const ObservationPayloadSchema = z
  .strictObject({
    fence: PositiveJsSafeIntSchema,
    classification: z.enum(['applied', 'not_applied', 'unknown']),
    adapter_result_digest: Sha256DigestV1Schema.nullable(),
    readback: AdmittedJsonObjectV1Schema,
    readback_digest: Sha256DigestV1Schema,
    observation: EffectObservationV1Schema,
    command_digest: Sha256DigestV1Schema,
    request_digest: Sha256DigestV1Schema,
    target_digest: Sha256DigestV1Schema,
    authority_binding_digest: Sha256DigestV1Schema,
    adapter_id: WorkflowIdentifierV1Schema,
    operation: WorkflowIdentifierV1Schema,
    payload_digest: Sha256DigestV1Schema,
    idempotency_key: WorkflowPortableNonBlankStringV1Schema,
    effect_id: WorkflowPortableNonBlankStringV1Schema,
  })
  .meta({ title: 'Hermes EffectObservationPayloadV1' })

const ReceiptPayloadSchema = z
  .strictObject({
    fence: PositiveJsSafeIntSchema,
    result_classification: z.enum(['applied', 'not_applied']),
    receipt_digest: Sha256DigestV1Schema,
    command_digest: Sha256DigestV1Schema,
    request_digest: Sha256DigestV1Schema,
    target_digest: Sha256DigestV1Schema,
    authority_binding_digest: Sha256DigestV1Schema,
    observation_digest: Sha256DigestV1Schema,
    receipt: EffectReceiptV1Schema,
    adapter_id: WorkflowIdentifierV1Schema,
    operation: WorkflowIdentifierV1Schema,
    payload_digest: Sha256DigestV1Schema,
    idempotency_key: WorkflowPortableNonBlankStringV1Schema,
    effect_id: WorkflowPortableNonBlankStringV1Schema,
    observation: EffectObservationV1Schema,
  })
  .meta({ title: 'Hermes EffectReceiptPayloadV1' })

const ReconciledPayloadSchema = z
  .strictObject({
    classification: z.enum(['applied', 'not_applied']),
    fence: PositiveJsSafeIntSchema,
    command_digest: Sha256DigestV1Schema,
    request_digest: Sha256DigestV1Schema,
    target_digest: Sha256DigestV1Schema,
    evidence_digest: Sha256DigestV1Schema,
    adapter_id: WorkflowIdentifierV1Schema,
    operation: WorkflowIdentifierV1Schema,
    payload_digest: Sha256DigestV1Schema,
    idempotency_key: WorkflowPortableNonBlankStringV1Schema,
    effect_id: WorkflowPortableNonBlankStringV1Schema,
    authority_binding_digest: Sha256DigestV1Schema,
    readback: AdmittedJsonObjectV1Schema,
    readback_digest: Sha256DigestV1Schema,
  })
  .meta({ title: 'Hermes EffectReconciledPayloadV1' })

const CompensationIntentPayloadSchema = z
  .strictObject({
    original_receipt_digest: Sha256DigestV1Schema,
    original_receipt: EffectReceiptV1Schema,
    compensation_ref: WorkflowPortableNonBlankStringV1Schema,
    request_digest: Sha256DigestV1Schema,
    command_digest: Sha256DigestV1Schema,
    target_digest: Sha256DigestV1Schema,
    adapter_id: WorkflowIdentifierV1Schema,
    operation: WorkflowIdentifierV1Schema,
    payload_digest: Sha256DigestV1Schema,
    idempotency_key: WorkflowPortableNonBlankStringV1Schema,
    effect_id: WorkflowPortableNonBlankStringV1Schema,
    fence: PositiveJsSafeIntSchema,
    authority_binding_digest: Sha256DigestV1Schema,
    preflight_readback: AdmittedJsonObjectV1Schema,
    preflight_readback_digest: Sha256DigestV1Schema,
  })
  .meta({ title: 'Hermes EffectCompensationIntentPayloadV1' })

const CompensationObservationPayloadSchema = z
  .strictObject({
    classification: z.enum(['applied', 'not_applied', 'unknown']),
    observation: EffectObservationV1Schema,
    readback: AdmittedJsonObjectV1Schema,
    readback_digest: Sha256DigestV1Schema,
    original_receipt_digest: Sha256DigestV1Schema,
    original_receipt: EffectReceiptV1Schema,
    fence: PositiveJsSafeIntSchema,
    command_digest: Sha256DigestV1Schema,
    request_digest: Sha256DigestV1Schema,
    target_digest: Sha256DigestV1Schema,
    adapter_id: WorkflowIdentifierV1Schema,
    operation: WorkflowIdentifierV1Schema,
    payload_digest: Sha256DigestV1Schema,
    idempotency_key: WorkflowPortableNonBlankStringV1Schema,
    effect_id: WorkflowPortableNonBlankStringV1Schema,
    authority_binding_digest: Sha256DigestV1Schema,
  })
  .meta({ title: 'Hermes EffectCompensationObservationPayloadV1' })

const CompensationReceiptPayloadSchema = z
  .strictObject({
    receipt_digest: Sha256DigestV1Schema,
    receipt: EffectReceiptV1Schema,
    original_receipt_digest: Sha256DigestV1Schema,
    original_receipt: EffectReceiptV1Schema,
    fence: PositiveJsSafeIntSchema,
    command_digest: Sha256DigestV1Schema,
    request_digest: Sha256DigestV1Schema,
    target_digest: Sha256DigestV1Schema,
    adapter_id: WorkflowIdentifierV1Schema,
    operation: WorkflowIdentifierV1Schema,
    payload_digest: Sha256DigestV1Schema,
    idempotency_key: WorkflowPortableNonBlankStringV1Schema,
    effect_id: WorkflowPortableNonBlankStringV1Schema,
    authority_binding_digest: Sha256DigestV1Schema,
    observation_digest: Sha256DigestV1Schema,
    observation: EffectObservationV1Schema,
  })
  .meta({ title: 'Hermes EffectCompensationReceiptPayloadV1' })

const LedgerPayloadByType = {
  claimed: ClaimedPayloadSchema,
  intent_recorded: IntentPayloadSchema,
  observation_recorded: ObservationPayloadSchema,
  reconciled: ReconciledPayloadSchema,
  receipt_terminal: ReceiptPayloadSchema,
  compensation_intent: CompensationIntentPayloadSchema,
  compensation_observation: CompensationObservationPayloadSchema,
  compensation_receipt: CompensationReceiptPayloadSchema,
} as const

export const WorkflowEffectLedgerEventV1ObjectSchema = z
  .strictObject({
    schema_version: z.literal('workflow_effect_ledger_event_v1'),
    effect_id: WorkflowPortableNonBlankStringV1Schema,
    sequence: PositiveJsSafeIntSchema,
    previous_event_id: EffectLedgerEventIdV1Schema.nullable(),
    idempotency_key: WorkflowPortableNonBlankStringV1Schema,
    occurred_at: WorkflowIsoTimestampV1Schema,
    event_type: z.enum([
      'claimed',
      'intent_recorded',
      'observation_recorded',
      'reconciled',
      'receipt_terminal',
      'compensation_intent',
      'compensation_observation',
      'compensation_receipt',
    ]),
    // Generated schema uses conditional exact variants via toJSONSchema override below.
    payload: AdmittedJsonObjectV1Schema,
    event_id: EffectLedgerEventIdV1Schema,
  })
  .meta({ title: 'Hermes WorkflowEffectLedgerEventV1' })

export const WorkflowEffectLedgerEventV1Schema = WorkflowEffectLedgerEventV1ObjectSchema.superRefine(
  (value, ctx) => {
    const schema = LedgerPayloadByType[value.event_type]
    const parsed = schema.safeParse(value.payload)
    if (!parsed.success) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: `payload does not match event_type ${value.event_type}`,
        path: ['payload'],
      })
    }
  },
)

export const WorkflowExecutionEventV2Schema = z
  .strictObject({
    schema_version: z.literal('workflow_execution_event_v2'),
    run_id: z.string().min(1),
    sequence: PositiveJsSafeIntSchema,
    previous_event_id: ExecutionEventV2IdSchema.nullable(),
    idempotency_key: z.string().min(1),
    occurred_at: WorkflowIsoTimestampV1Schema,
    event_type: z.enum([
      'run_accepted',
      'run_started',
      'run_cancel_requested',
      'budget_reserved',
      'budget_settled',
      'budget_released',
      'leaf_started',
      'leaf_packet',
      'leaf_waiting_reconcile',
      'effect_bound',
      'effect_resolved',
      'run_waiting_reconcile',
      'run_verified',
      'run_completed',
    ]),
    payload: AdmittedJsonObjectV1Schema,
    event_id: ExecutionEventV2IdSchema,
  })
  .meta({ title: 'Hermes WorkflowExecutionEventV2' })

export type EffectCommandV1 = z.infer<typeof EffectCommandV1Schema>
export type EffectTargetV1 = z.infer<typeof EffectTargetV1Schema>
export type EffectObservationV1 = z.infer<typeof EffectObservationV1Schema>
export type EffectReceiptV1 = z.infer<typeof EffectReceiptV1Schema>
export type WorkflowEffectLedgerEventV1 = z.infer<
  typeof WorkflowEffectLedgerEventV1Schema
>
export type WorkflowExecutionEventV2 = z.infer<typeof WorkflowExecutionEventV2Schema>

const UNICODE_SCALAR_STRING_JSON_PATTERN =
  '^(?:[^\\uD800-\\uDFFF]|[\\uD800-\\uDBFF][\\uDC00-\\uDFFF])*$'

/**
 * Ensure recursive admitted-JSON string branches and propertyNames reject lone
 * surrogate code units while remaining representable in Draft 2020-12 validators.
 * Zod refine does not always survive generation; post-process defs explicitly.
 */
function enforceUnicodeScalarPatternsInJsonSchema(schema: Record<string, unknown>): Record<string, unknown> {
  const visit = (node: unknown, underAdmitted = false): void => {
    if (!node || typeof node !== 'object') return
    if (Array.isArray(node)) {
      for (const item of node) visit(item, underAdmitted)
      return
    }
    const obj = node as Record<string, unknown>
    const isAdmittedBranch =
      underAdmitted ||
      (Array.isArray(obj.anyOf) &&
        obj.anyOf.some(
          (branch) =>
            branch &&
            typeof branch === 'object' &&
            (branch as Record<string, unknown>).type === 'null',
        ) &&
        obj.anyOf.some(
          (branch) =>
            branch &&
            typeof branch === 'object' &&
            (branch as Record<string, unknown>).type === 'string',
        ))

    if (obj.type === 'string') {
      // Do not overwrite timestamp/identifier patterns that already constrain the domain.
      const existing = typeof obj.pattern === 'string' ? obj.pattern : ''
      const looksLikeTimestamp = existing.includes('T') && existing.includes(':')
      const looksLikeDigest = existing.includes('sha256:')
      const looksLikeId =
        existing.startsWith('^wel_') ||
        existing.startsWith('^we2_') ||
        existing.startsWith('^[a-z]') ||
        existing.includes('\\ud800-\\udfff') ||
        existing.includes('\ud800-\udfff')
      if (!looksLikeTimestamp && !looksLikeDigest && !looksLikeId) {
        if (!existing || isAdmittedBranch || existing === UNICODE_SCALAR_STRING_JSON_PATTERN) {
          obj.pattern = UNICODE_SCALAR_STRING_JSON_PATTERN
        } else if (!existing.includes('D800') && !existing.includes('d800')) {
          // Compose with existing portable patterns only when they lack surrogate guards.
          obj.pattern = existing
        }
      }
    }

    if (obj.propertyNames && typeof obj.propertyNames === 'object') {
      const pn = obj.propertyNames as Record<string, unknown>
      if (pn.type === 'string' || pn.type === undefined) {
        pn.type = 'string'
        pn.pattern = UNICODE_SCALAR_STRING_JSON_PATTERN
      }
      visit(pn, true)
    }

    for (const [key, value] of Object.entries(obj)) {
      if (key === 'propertyNames') continue
      visit(value, isAdmittedBranch || key === '$defs' || key === 'additionalProperties' || key === 'items')
    }
  }
  visit(schema, false)
  return schema
}

function toJsonSchema(schema: z.ZodType, id: string): Record<string, unknown> {
  const raw = {
    $id: `https://hermes-agent.local/schemas/${id}`,
    ...z.toJSONSchema(schema, { target: 'draft-2020-12' }),
  } as Record<string, unknown>
  return enforceUnicodeScalarPatternsInJsonSchema(raw)
}

const TIMESTAMP_SCHEMA_COMMENT =
  'Grammar: pattern enforces normalized UTC/offset seconds form and year 0001-9999 (year 0000 excluded). JSON Schema cannot prove full calendar correctness (e.g. 2026-02-30). Public Python/TS parsers enforce calendar-valid timestamps and unpaired-surrogate rejection; treat schema acceptance of invalid calendar dates outside the pattern as a known limitation, not parity.'

function annotateTimestampFields(schema: Record<string, unknown>): Record<string, unknown> {
  const visit = (node: unknown): void => {
    if (!node || typeof node !== 'object') return
    if (Array.isArray(node)) {
      for (const item of node) visit(item)
      return
    }
    const obj = node as Record<string, unknown>
    if (
      typeof obj.pattern === 'string' &&
      obj.type === 'string' &&
      (obj.pattern.includes('\\d{4}-\\d{2}-\\d{2}T') ||
        obj.pattern.includes('-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}'))
    ) {
      obj.format = 'date-time'
      obj.$comment = TIMESTAMP_SCHEMA_COMMENT
    }
    for (const value of Object.values(obj)) visit(value)
  }
  visit(schema)
  return schema
}

export function toEffectCommandV1JsonSchema(): Record<string, unknown> {
  return toJsonSchema(EffectCommandV1Schema, 'effect-command-v1.schema.json')
}

export function toEffectObservationV1JsonSchema(): Record<string, unknown> {
  return annotateTimestampFields(
    toJsonSchema(EffectObservationV1Schema, 'effect-observation-v1.schema.json'),
  )
}

export function toEffectReceiptV1JsonSchema(): Record<string, unknown> {
  return toJsonSchema(EffectReceiptV1Schema, 'effect-receipt-v1.schema.json')
}

export function toWorkflowEffectLedgerEventV1JsonSchema(): Record<string, unknown> {
  const base = toJsonSchema(
    WorkflowEffectLedgerEventV1ObjectSchema,
    'workflow-effect-ledger-event-v1.schema.json',
  ) as Record<string, unknown>
  const payloadOneOf = Object.entries(LedgerPayloadByType).map(([eventType, schema]) => {
    const json = z.toJSONSchema(schema, { target: 'draft-2020-12' }) as Record<string, unknown>
    return {
      if: {
        properties: { event_type: { const: eventType } },
        required: ['event_type'],
      },
      then: {
        properties: {
          payload: {
            ...json,
            additionalProperties: false,
          },
        },
      },
    }
  })
  return annotateTimestampFields({
    ...base,
    allOf: payloadOneOf,
  })
}

export function toWorkflowExecutionEventV2JsonSchema(): Record<string, unknown> {
  return annotateTimestampFields(
    toJsonSchema(WorkflowExecutionEventV2Schema, 'workflow-execution-event-v2.schema.json'),
  )
}

function canonicalNumberV1(value: number): string {
  if (!Number.isFinite(value)) {
    throw new Error('canonical JSON rejects non-finite numbers')
  }
  // Admitted-domain fail-closed simplification shared with Python:
  // only JS-safe integers (including integer-valued 1.0) are accepted.
  // Serialize exactly as base-10 integer text; -0 and 0 become "0".
  if (Object.is(value, -0) || value === 0) {
    return '0'
  }
  if (!Number.isInteger(value)) {
    throw new Error('canonical JSON rejects non-integer numbers in admitted domain')
  }
  if (value > JS_SAFE_INTEGER_MAX || value < JS_SAFE_INTEGER_MIN) {
    throw new Error('canonical JSON integer exceeds JS-safe range')
  }
  return String(value)
}

function isPlainObjectV1(value: object): boolean {
  const proto = Object.getPrototypeOf(value)
  return proto === Object.prototype || proto === null
}

/**
 * Reject unpaired UTF-16 surrogates in admitted strings/keys before
 * canonicalize/parse/digest. Valid supplementary-plane scalars (emoji/U+10000)
 * are retained as UTF-16 pairs.
 */
function assertUnicodeScalarStringV1(text: string, label = 'string'): string {
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i)
    if (code >= 0xd800 && code <= 0xdbff) {
      if (i + 1 >= text.length) {
        throw new Error(`${label} contains unpaired UTF-16 high surrogate`)
      }
      const next = text.charCodeAt(i + 1)
      if (next < 0xdc00 || next > 0xdfff) {
        throw new Error(`${label} contains unpaired UTF-16 high surrogate`)
      }
      i += 1
      continue
    }
    if (code >= 0xdc00 && code <= 0xdfff) {
      throw new Error(`${label} contains unpaired UTF-16 low surrogate`)
    }
  }
  return text
}

function isCanonicalArrayIndexKey(key: string, length: number): boolean {
  if (!/^(0|[1-9]\d*)$/.test(key)) return false
  const index = Number(key)
  return Number.isInteger(index) && index >= 0 && index < length
}

/**
 * Validate array shape BEFORE any element read/getter evaluation:
 * exact Array.prototype, own keys only length + dense 0..length-1 indices,
 * each index an enumerable/writable/configurable data property.
 */
function normalizeAdmittedArrayV1(value: unknown[]): AdmittedJsonValueV1[] {
  // Caller (normalizeAdmittedJsonV1) already rejected proxies before Array.isArray / instanceof.
  // Keep a belt-and-suspenders guard with the same canonical error if this helper is ever reached directly.
  if (nodeTypes.isProxy(value)) {
    throw new Error('canonical JSON rejects proxy values')
  }
  if (Object.getPrototypeOf(value) !== Array.prototype) {
    throw new Error('canonical JSON rejects array subclass or custom prototype')
  }
  // Require the original length descriptor exactly: data value uint32 length,
  // writable=true, enumerable=false, configurable=false — before any property read.
  const lengthDesc = Object.getOwnPropertyDescriptor(value, 'length')
  if (
    !lengthDesc ||
    !Object.prototype.hasOwnProperty.call(lengthDesc, 'value') ||
    lengthDesc.get ||
    lengthDesc.set ||
    typeof lengthDesc.value !== 'number' ||
    !Number.isInteger(lengthDesc.value) ||
    lengthDesc.value < 0 ||
    lengthDesc.value > 0xffffffff ||
    lengthDesc.writable !== true ||
    lengthDesc.enumerable !== false ||
    lengthDesc.configurable !== false
  ) {
    throw new Error('canonical JSON rejects arrays with non-standard length descriptor')
  }
  // Rely only on the validated descriptor — never read value.length (avoids proxy/getter traps).
  const length = lengthDesc.value

  const ownKeys = Reflect.ownKeys(value)
  const expected = new Set<string>(['length'])
  for (let i = 0; i < length; i += 1) {
    expected.add(String(i))
  }
  if (ownKeys.length !== expected.size) {
    throw new Error('canonical JSON rejects arrays with unexpected own keys')
  }
  for (const key of ownKeys) {
    if (typeof key === 'symbol') {
      throw new Error('canonical JSON rejects symbol keys on arrays')
    }
    if (!expected.has(key)) {
      throw new Error('canonical JSON rejects extra array keys')
    }
    if (key === 'length') {
      continue
    }
    if (!isCanonicalArrayIndexKey(key, length)) {
      throw new Error('canonical JSON rejects non-index array keys')
    }
    const desc = Object.getOwnPropertyDescriptor(value, key)
    // Descriptor inspection only — never read value[i] / invoke getters while rejecting.
    if (!desc || !Object.prototype.hasOwnProperty.call(desc, 'value') || desc.get || desc.set) {
      throw new Error('canonical JSON rejects accessor array indices')
    }
    if (!desc.enumerable || !desc.writable || !desc.configurable) {
      throw new Error(
        'canonical JSON requires array indices to be enumerable writable configurable data properties',
      )
    }
    if (desc.value === undefined) {
      throw new Error('canonical JSON rejects undefined array elements')
    }
  }
  // Confirm every dense index exists with a standard data descriptor before reading values.
  const out: AdmittedJsonValueV1[] = []
  for (let i = 0; i < length; i += 1) {
    const key = String(i)
    const desc = Object.getOwnPropertyDescriptor(value, key)
    if (!desc || !Object.prototype.hasOwnProperty.call(desc, 'value') || desc.get || desc.set) {
      throw new Error('canonical JSON rejects sparse arrays or accessor indices')
    }
    if (!desc.enumerable || !desc.writable || !desc.configurable) {
      throw new Error(
        'canonical JSON requires array indices to be enumerable writable configurable data properties',
      )
    }
    out.push(normalizeAdmittedJsonV1(desc.value))
  }
  return out
}

/**
 * Recursive public admitted JSON validation/normalization (shared with Python).
 * Accepts null/bool/string/array/plain-object and JS-safe integer numbers (1.0/-0).
 * Rejects undefined, bigint, symbol, function, Date, Map, Set, class instances,
 * sparse arrays, array symbols/extra keys/accessors/custom prototypes,
 * symbol keys, non-enumerable string keys, accessors, and unpaired surrogates.
 */
export function normalizeAdmittedJsonV1(value: unknown): AdmittedJsonValueV1 {
  if (value === null) return null
  if (typeof value === 'boolean') return value
  if (typeof value === 'string') return assertUnicodeScalarStringV1(value)
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) {
      throw new Error('canonical JSON rejects non-finite numbers')
    }
    if (Object.is(value, -0) || value === 0) return 0
    if (!Number.isInteger(value)) {
      throw new Error('canonical JSON rejects non-integer numbers in admitted domain')
    }
    if (value > JS_SAFE_INTEGER_MAX || value < JS_SAFE_INTEGER_MIN) {
      throw new Error('canonical JSON integer exceeds JS-safe range')
    }
    return value
  }
  if (typeof value === 'undefined') {
    throw new Error('canonical JSON rejects undefined')
  }
  if (typeof value === 'bigint' || typeof value === 'symbol' || typeof value === 'function') {
    throw new Error(`canonical JSON rejects non-JSON type: ${typeof value}`)
  }
  if (typeof value !== 'object') {
    throw new Error(`canonical JSON rejects non-JSON type: ${typeof value}`)
  }
  // Node-targeted contract package: reject proxies before any trapful meta-ops
  // (instanceof / Array.isArray prototype walks / getPrototypeOf / ownKeys / descriptors / Zod).
  if (nodeTypes.isProxy(value)) {
    throw new Error('canonical JSON rejects proxy values')
  }
  if (value instanceof Date || value instanceof Map || value instanceof Set) {
    throw new Error('canonical JSON rejects non-plain object types')
  }
  if (Array.isArray(value)) {
    return normalizeAdmittedArrayV1(value)
  }
  if (!isPlainObjectV1(value)) {
    throw new Error('canonical JSON rejects non-plain object types')
  }
  const out: { [key: string]: AdmittedJsonValueV1 } = {}
  for (const key of Reflect.ownKeys(value as object)) {
    if (typeof key === 'symbol') {
      throw new Error('canonical JSON rejects symbol keys')
    }
    const desc = Object.getOwnPropertyDescriptor(value, key)
    if (!desc || !Object.prototype.hasOwnProperty.call(desc, 'value') || desc.get || desc.set) {
      throw new Error('canonical JSON rejects accessor properties')
    }
    if (!desc.enumerable) {
      throw new Error('canonical JSON rejects non-enumerable keys')
    }
    if (typeof key !== 'string') {
      throw new Error('canonical JSON object keys must be strings')
    }
    assertUnicodeScalarStringV1(key, 'object key')
    if (desc.value === undefined) {
      throw new Error('canonical JSON rejects undefined object properties')
    }
    out[key] = normalizeAdmittedJsonV1(desc.value)
  }
  return out
}

/**
 * Admitted-domain canonical JSON (shared with Python).
 * - object keys ordered by UTF-16 code units (JS string comparison / RFC 8785)
 * - numbers: JS-safe integers only; 1.0 -> "1"; -0 -> "0"; fractions/exponents rejected
 * - no whitespace
 */
export function canonicalJsonV1(value: unknown): string {
  const normalized = normalizeAdmittedJsonV1(value)
  if (Array.isArray(normalized)) {
    return `[${normalized.map((item) => canonicalJsonV1(item)).join(',')}]`
  }
  if (normalized !== null && typeof normalized === 'object') {
    const entries = Object.entries(normalized).sort(([left], [right]) =>
      left < right ? -1 : left > right ? 1 : 0,
    )
    return `{${entries
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJsonV1(item)}`)
      .join(',')}}`
  }
  if (typeof normalized === 'number') {
    return canonicalNumberV1(normalized)
  }
  if (typeof normalized === 'string') {
    return JSON.stringify(normalized)
  }
  if (typeof normalized === 'boolean') {
    return normalized ? 'true' : 'false'
  }
  return 'null'
}

/** Private: freeze a value already admitted/normalized. Not a public bypass. */
function deepFreezeNormalizedAdmittedJsonV1<T extends AdmittedJsonValueV1>(value: T): T {
  if (value && typeof value === 'object') {
    if (Array.isArray(value)) {
      for (const item of value) deepFreezeNormalizedAdmittedJsonV1(item)
    } else {
      for (const key of Object.keys(value)) {
        deepFreezeNormalizedAdmittedJsonV1((value as Record<string, AdmittedJsonValueV1>)[key])
      }
    }
    Object.freeze(value)
  }
  return value
}

/**
 * Public deep freeze: descriptor-validates the ORIGINAL complete input first,
 * freezes a normalized admitted copy, and never silently drops hidden facts.
 */
export function deepFreezeV1<T>(value: T): T {
  const normalized = normalizeAdmittedJsonV1(value)
  return deepFreezeNormalizedAdmittedJsonV1(normalized) as T
}

export function canonicalDigestV1(value: unknown): string {
  return `sha256:${createHash('sha256').update(canonicalJsonV1(value)).digest('hex')}`
}

function requirePlainObject(value: unknown, label: string): Record<string, AdmittedJsonValueV1> {
  const normalized = normalizeAdmittedJsonV1(value)
  if (normalized === null || typeof normalized !== 'object' || Array.isArray(normalized)) {
    throw new Error(`${label} must be an object`)
  }
  return normalized as Record<string, AdmittedJsonValueV1>
}

function withoutKey(
  value: Record<string, AdmittedJsonValueV1>,
  key: string,
): Record<string, AdmittedJsonValueV1> {
  const out: Record<string, AdmittedJsonValueV1> = { ...value }
  delete out[key]
  return out
}

function canonicalEffectCommandV1(body: EffectCommandV1): string {
  return canonicalJsonV1({
    adapter_id: body.adapter_id,
    command_id: body.command_id,
    compensation_ref: body.compensation_ref,
    config_revision: body.config_revision,
    idempotency_key: body.idempotency_key,
    issuer_id: body.issuer_id,
    operation: body.operation,
    payload_digest: body.payload_digest,
    policy_revision: body.policy_revision,
    reconcile_ref: body.reconcile_ref,
    schema_version: body.schema_version,
    side_effect_tier: body.side_effect_tier,
    target: {
      target_kind: body.target.target_kind,
      target_ref: body.target.target_ref,
    },
  })
}

export function deriveEffectCommandRequestDigestV1(body: unknown): string {
  const normalized = requirePlainObject(body, 'effect_command')
  const parsed = EffectCommandV1Schema.parse(normalized)
  return `sha256:${createHash('sha256').update(canonicalEffectCommandV1(parsed)).digest('hex')}`
}

export function deriveWorkflowEffectLedgerEventIdV1(body: unknown): string {
  const normalized = requirePlainObject(body, 'effect_ledger_event')
  const canonical = withoutKey(normalized, 'event_id')
  return `wel_${createHash('sha256').update(canonicalJsonV1(canonical)).digest('hex')}`
}

export function deriveWorkflowExecutionEventIdV2(body: unknown): string {
  const normalized = requirePlainObject(body, 'execution_event_v2')
  const canonical = withoutKey(normalized, 'event_id')
  return `we2_${createHash('sha256').update(canonicalJsonV1(canonical)).digest('hex')}`
}

export function deriveEffectReceiptDigestV1(body: unknown): string {
  const normalized = requirePlainObject(body, 'effect_receipt')
  const canonical = withoutKey(normalized, 'receipt_digest')
  return canonicalDigestV1(canonical)
}

function requireDigestField(value: unknown, label: string): string {
  if (typeof value !== 'string' || !/^sha256:[0-9a-f]{64}$/.test(value)) {
    throw new Error(`${label} must be a sha256 digest`)
  }
  return value
}

function requireObjectField(
  value: unknown,
  label: string,
): Record<string, AdmittedJsonValueV1> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} must be an object`)
  }
  return value as Record<string, AdmittedJsonValueV1>
}

/**
 * Canonical discriminated semantic payload validators (parity with Python
 * `_LEDGER_PAYLOAD_VALIDATORS`). Shape keys come from LedgerPayloadByType;
 * this map enforces digests and embedded body relations.
 */
function requireAppliedOriginalReceipt(
  value: unknown,
  label: string,
): EffectReceiptV1 {
  const original = requireObjectField(value, label)
  const parsedOriginal = EffectReceiptV1Schema.parse(original)
  if (parsedOriginal.receipt_digest !== deriveEffectReceiptDigestV1(parsedOriginal)) {
    throw new Error('receipt_digest does not hash the canonical receipt body')
  }
  if (parsedOriginal.result_classification !== 'applied') {
    throw new Error(`${label} original receipt must be applied`)
  }
  return parsedOriginal
}

function bindObservationWrapper(
  payload: Record<string, AdmittedJsonValueV1>,
  observation: Record<string, AdmittedJsonValueV1>,
  label: string,
): void {
  if (payload.classification !== observation.classification) {
    throw new Error(`${label} classification mismatch`)
  }
  if (payload.fence !== observation.fence) {
    throw new Error(`${label} fence mismatch`)
  }
  if (payload.effect_id !== observation.effect_id) {
    throw new Error(`${label} effect_id mismatch`)
  }
  if (
    Object.prototype.hasOwnProperty.call(payload, 'adapter_result_digest') &&
    payload.adapter_result_digest !== observation.adapter_result_digest
  ) {
    throw new Error(`${label} adapter_result_digest mismatch`)
  }
  if (canonicalDigestV1(payload.readback) !== canonicalDigestV1(observation.readback)) {
    throw new Error(`${label} wrapper/body readback mismatch`)
  }
}

function bindReceiptWrapper(
  payload: Record<string, AdmittedJsonValueV1>,
  parsedReceipt: EffectReceiptV1,
  label: string,
): void {
  if (parsedReceipt.receipt_digest !== deriveEffectReceiptDigestV1(parsedReceipt)) {
    throw new Error('receipt_digest does not hash the canonical receipt body')
  }
  if (payload.receipt_digest !== parsedReceipt.receipt_digest) {
    throw new Error(`${label} receipt_digest does not match receipt body`)
  }
  if (
    Object.prototype.hasOwnProperty.call(payload, 'result_classification') &&
    payload.result_classification !== parsedReceipt.result_classification
  ) {
    throw new Error(`${label} result_classification mismatch`)
  }
  if (payload.fence !== parsedReceipt.fence) {
    throw new Error(`${label} wrapper fence does not match receipt body fence`)
  }
  if (payload.command_digest !== parsedReceipt.command_digest) {
    throw new Error(`${label} body command_digest mismatch`)
  }
  if (payload.adapter_id !== parsedReceipt.adapter_id) {
    throw new Error(`${label} adapter_id mismatch`)
  }
  if (payload.operation !== parsedReceipt.operation) {
    throw new Error(`${label} operation mismatch`)
  }
  if (payload.payload_digest !== parsedReceipt.payload_digest) {
    throw new Error(`${label} payload_digest mismatch`)
  }
  if (payload.idempotency_key !== parsedReceipt.idempotency_key) {
    throw new Error(`${label} idempotency_key mismatch`)
  }
  if (payload.authority_binding_digest !== parsedReceipt.authority_binding_digest) {
    throw new Error(`${label} authority_binding_digest mismatch`)
  }
  if (payload.effect_id !== parsedReceipt.effect_id) {
    throw new Error(`${label} effect_id mismatch`)
  }
  const recomputedTarget = canonicalDigestV1({
    target_kind: parsedReceipt.target.target_kind,
    target_ref: parsedReceipt.target.target_ref,
  })
  if (payload.target_digest !== recomputedTarget) {
    throw new Error(`${label} target_digest does not match receipt target body`)
  }
}

/**
 * Canonical discriminated semantic payload validators (parity with Python
 * `_LEDGER_PAYLOAD_VALIDATORS`). Shape keys come from LedgerPayloadByType;
 * this map enforces digests and embedded body relations from the shared
 * workflow-effect-ledger-semantic-relations-v1 SSOT.
 */
export const LEDGER_PAYLOAD_SEMANTIC_VALIDATORS_V1: Record<
  keyof typeof LedgerPayloadByType,
  (payload: Record<string, AdmittedJsonValueV1>) => void
> = {
  claimed: () => {
    /* exact keys/types enforced by ClaimedPayloadSchema; envelope binding in parse */
  },
  intent_recorded: (payload) => {
    const preflight = requireObjectField(payload.preflight_readback, 'preflight_readback')
    const digest = requireDigestField(payload.preflight_readback_digest, 'preflight_readback_digest')
    if (digest !== canonicalDigestV1(preflight)) {
      throw new Error('preflight_readback_digest mismatch')
    }
  },
  observation_recorded: (payload) => {
    const readback = requireObjectField(payload.readback, 'readback')
    const digest = requireDigestField(payload.readback_digest, 'readback_digest')
    if (digest !== canonicalDigestV1(readback)) {
      throw new Error('readback_digest mismatch')
    }
    const observation = requireObjectField(payload.observation, 'observation')
    EffectObservationV1Schema.parse(observation)
    bindObservationWrapper(payload, observation, 'observation')
  },
  receipt_terminal: (payload) => {
    const receipt = requireObjectField(payload.receipt, 'receipt')
    const parsedReceipt = EffectReceiptV1Schema.parse(receipt)
    bindReceiptWrapper(payload, parsedReceipt, 'receipt')
    const observation = requireObjectField(payload.observation, 'observation')
    EffectObservationV1Schema.parse(observation)
    const obsDigest = requireDigestField(payload.observation_digest, 'observation_digest')
    if (obsDigest !== canonicalDigestV1(observation)) {
      throw new Error('receipt observation_digest mismatch')
    }
    if (observation.effect_id !== payload.effect_id) {
      throw new Error('receipt observation effect_id mismatch')
    }
    if (observation.fence !== payload.fence) {
      throw new Error('receipt observation fence/effect binding mismatch')
    }
    // post_readback binds cross-event to observation/reconcile durable facts.
  },
  reconciled: (payload) => {
    const readback = requireObjectField(payload.readback, 'readback')
    const digest = requireDigestField(payload.readback_digest, 'readback_digest')
    if (digest !== canonicalDigestV1(readback)) {
      throw new Error('reconcile readback_digest mismatch')
    }
  },
  compensation_intent: (payload) => {
    const parsedOriginal = requireAppliedOriginalReceipt(payload.original_receipt, 'original_receipt')
    if (payload.original_receipt_digest !== parsedOriginal.receipt_digest) {
      throw new Error('original_receipt_digest mismatch')
    }
    if (parsedOriginal.effect_id !== payload.effect_id) {
      throw new Error('compensation original effect_id mismatch')
    }
    const preflight = requireObjectField(payload.preflight_readback, 'preflight_readback')
    const digest = requireDigestField(payload.preflight_readback_digest, 'preflight_readback_digest')
    if (digest !== canonicalDigestV1(preflight)) {
      throw new Error('compensation preflight_readback_digest mismatch')
    }
  },
  compensation_observation: (payload) => {
    const readback = requireObjectField(payload.readback, 'readback')
    const digest = requireDigestField(payload.readback_digest, 'readback_digest')
    if (digest !== canonicalDigestV1(readback)) {
      throw new Error('compensation readback_digest mismatch')
    }
    const observation = requireObjectField(payload.observation, 'observation')
    EffectObservationV1Schema.parse(observation)
    bindObservationWrapper(payload, observation, 'compensation observation')
    const parsedOriginal = requireAppliedOriginalReceipt(payload.original_receipt, 'original_receipt')
    if (payload.original_receipt_digest !== parsedOriginal.receipt_digest) {
      throw new Error('original_receipt_digest mismatch')
    }
    if (parsedOriginal.effect_id !== payload.effect_id) {
      throw new Error('compensation observation original effect_id mismatch')
    }
  },
  compensation_receipt: (payload) => {
    const receipt = requireObjectField(payload.receipt, 'receipt')
    const parsedReceipt = EffectReceiptV1Schema.parse(receipt)
    bindReceiptWrapper(payload, parsedReceipt, 'compensation receipt')
    const parsedOriginal = requireAppliedOriginalReceipt(payload.original_receipt, 'original_receipt')
    if (payload.original_receipt_digest !== parsedOriginal.receipt_digest) {
      throw new Error('original_receipt_digest mismatch')
    }
    if (parsedOriginal.effect_id !== payload.effect_id) {
      throw new Error('compensation receipt original effect_id mismatch')
    }
    const observation = requireObjectField(payload.observation, 'observation')
    EffectObservationV1Schema.parse(observation)
    const obsDigest = requireDigestField(payload.observation_digest, 'observation_digest')
    if (obsDigest !== canonicalDigestV1(observation)) {
      throw new Error('compensation observation_digest mismatch')
    }
    if (observation.effect_id !== payload.effect_id) {
      throw new Error('compensation receipt observation effect_id mismatch')
    }
    if (observation.fence !== payload.fence) {
      throw new Error('compensation receipt observation fence mismatch')
    }
    if (observation.classification !== parsedReceipt.result_classification) {
      throw new Error('compensation receipt observation classification mismatch')
    }
    if (canonicalDigestV1(parsedReceipt.post_readback) !== canonicalDigestV1(observation.readback)) {
      throw new Error('compensation receipt post_readback does not match observation readback')
    }
  },
}

export function validateWorkflowEffectLedgerPayloadV1(
  eventType: keyof typeof LedgerPayloadByType,
  payload: unknown,
): Record<string, AdmittedJsonValueV1> {
  const shape = LedgerPayloadByType[eventType].parse(payload) as Record<string, AdmittedJsonValueV1>
  LEDGER_PAYLOAD_SEMANTIC_VALIDATORS_V1[eventType](shape)
  return shape
}

export function parseEffectCommandV1(value: unknown): EffectCommandV1 {
  const normalized = normalizeAdmittedJsonV1(value)
  return deepFreezeNormalizedAdmittedJsonV1(
    EffectCommandV1Schema.parse(normalized) as AdmittedJsonValueV1,
  ) as EffectCommandV1
}

export function parseEffectObservationV1(value: unknown): EffectObservationV1 {
  const normalized = normalizeAdmittedJsonV1(value)
  return deepFreezeNormalizedAdmittedJsonV1(
    EffectObservationV1Schema.parse(normalized) as AdmittedJsonValueV1,
  ) as EffectObservationV1
}

export function parseEffectReceiptV1(value: unknown): EffectReceiptV1 {
  const normalized = normalizeAdmittedJsonV1(value)
  const parsed = EffectReceiptV1Schema.parse(normalized)
  const expected = deriveEffectReceiptDigestV1(parsed)
  if (parsed.receipt_digest !== expected) {
    throw new Error('receipt_digest does not hash the canonical receipt body')
  }
  return deepFreezeNormalizedAdmittedJsonV1(parsed as AdmittedJsonValueV1) as EffectReceiptV1
}

export function parseWorkflowEffectLedgerEventV1(value: unknown): WorkflowEffectLedgerEventV1 {
  const normalized = requirePlainObject(value, 'effect ledger event')
  // Presence semantics: derive only when key is ABSENT. Present null/empty/malformed rejects.
  const hasEventId = Object.prototype.hasOwnProperty.call(normalized, 'event_id')
  const provided = hasEventId ? normalized.event_id : undefined
  const withoutId = withoutKey(normalized, 'event_id')
  const shape = WorkflowEffectLedgerEventV1ObjectSchema.omit({ event_id: true }).parse(withoutId)
  // Semantic payload validation before event-id verification/freezing.
  const payload = validateWorkflowEffectLedgerPayloadV1(
    shape.event_type as keyof typeof LedgerPayloadByType,
    shape.payload,
  )
  if (payload.effect_id !== shape.effect_id) {
    throw new Error('payload.effect_id does not match envelope effect_id')
  }
  const eventId = deriveWorkflowEffectLedgerEventIdV1(shape)
  if (hasEventId && provided !== eventId) {
    throw new Error('event_id does not match canonical body')
  }
  return deepFreezeNormalizedAdmittedJsonV1(
    WorkflowEffectLedgerEventV1Schema.parse({ ...shape, event_id: eventId }) as AdmittedJsonValueV1,
  ) as WorkflowEffectLedgerEventV1
}

export function parseWorkflowExecutionEventV2(value: unknown): WorkflowExecutionEventV2 {
  const normalized = requirePlainObject(value, 'execution event v2')
  // Presence semantics: derive only when key is ABSENT. Present null/empty/malformed rejects.
  const hasEventId = Object.prototype.hasOwnProperty.call(normalized, 'event_id')
  const provided = hasEventId ? normalized.event_id : undefined
  const withoutId = withoutKey(normalized, 'event_id')
  const shape = WorkflowExecutionEventV2Schema.omit({ event_id: true }).parse(withoutId)
  const eventId = deriveWorkflowExecutionEventIdV2(shape)
  if (hasEventId && provided !== eventId) {
    throw new Error('event_id does not match canonical body')
  }
  return deepFreezeNormalizedAdmittedJsonV1(
    WorkflowExecutionEventV2Schema.parse({ ...shape, event_id: eventId }) as AdmittedJsonValueV1,
  ) as WorkflowExecutionEventV2
}

export type SharedContractCorpusCase = {
  id: string
  kind:
    | 'effect_command_v1'
    | 'effect_observation_v1'
    | 'effect_receipt_v1'
    | 'workflow_effect_ledger_event_v1'
    | 'workflow_execution_event_v2'
  expect: 'accept' | 'reject'
  value: unknown
}

/**
 * Materialize fixture-only UTF-16 code-unit recipes from the shared corpus.
 * Recipe markers never enter the admitted JSON domain; harnesses expand them in-memory.
 */
export function materializeSharedCorpusValue(node: unknown): unknown {
  if (node !== null && typeof node === 'object') {
    if (Array.isArray(node)) {
      return node.map((item) => materializeSharedCorpusValue(item))
    }
    const obj = node as Record<string, unknown>
    const keys = Object.keys(obj)
    if (keys.length === 1 && keys[0] === '$materialize_utf16_units') {
      const units = obj.$materialize_utf16_units
      if (!Array.isArray(units) || !units.every((u) => typeof u === 'number' && Number.isInteger(u))) {
        throw new Error('invalid $materialize_utf16_units recipe')
      }
      return String.fromCharCode(...(units as number[]))
    }
    if (keys.length === 1 && keys[0] === '$materialize_utf16_key_entry') {
      const spec = obj.$materialize_utf16_key_entry as {
        key_units: number[]
        value: unknown
      }
      const key = String.fromCharCode(...spec.key_units)
      return { [key]: materializeSharedCorpusValue(spec.value) }
    }
    if (keys.length === 1 && keys[0] === '$materialize_merge_utf16_key') {
      const spec = obj.$materialize_merge_utf16_key as {
        base: unknown
        key_units: number[]
        value: unknown
      }
      const base = materializeSharedCorpusValue(spec.base) as Record<string, unknown>
      const key = String.fromCharCode(...spec.key_units)
      return { ...base, [key]: materializeSharedCorpusValue(spec.value) }
    }
    const out: Record<string, unknown> = {}
    for (const [key, value] of Object.entries(obj)) {
      out[key] = materializeSharedCorpusValue(value)
    }
    return out
  }
  return node
}

export function loadSharedEffectContractCorpus(): SharedContractCorpusCase[] {
  const here = dirname(fileURLToPath(import.meta.url))
  const corpusPath = join(
    here,
    '../../../tests/workflow_kernel/fixtures/effect-contract-shared-corpus.json',
  )
  const raw = JSON.parse(readFileSync(corpusPath, 'utf8')) as {
    cases: SharedContractCorpusCase[]
  }
  return raw.cases.map((item) => ({
    ...item,
    value: materializeSharedCorpusValue(item.value),
  }))
}
