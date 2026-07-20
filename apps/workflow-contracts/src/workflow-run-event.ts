import { createHash } from 'node:crypto'

import { z } from 'zod'

import {
  type WorkflowRunStatusV1,
  WorkflowRunStatusV1Schema,
} from './workflow-run-status'
import {
  type WorkflowRunTransitionV1,
  WorkflowRunTransitionV1Schema,
} from './workflow-run-transition'

const PortableNonBlankString = z
  .string()
  .min(1)
  .regex(/^[^\u0000-\u0008\u000e-\u001b\u007f-\u0084\u0086-\u009f\ud800-\udfff]*$/)
  .regex(/[^\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]/)
const Sha256Digest = z.string().regex(/^sha256:[0-9a-f]{64}$/)
const WorkflowRunEventId = z.string().regex(/^wre_[0-9a-f]{64}$/)

export const WorkflowRunEventProvenanceV1Schema = z.strictObject({
  producer: PortableNonBlankString,
  artifact_path: PortableNonBlankString,
  artifact_digest: Sha256Digest,
})

export const WorkflowRunEventBodyV1Schema = z.strictObject({
  schema_version: z.literal('workflow_run_event_v1'),
  run_id: PortableNonBlankString,
  sequence: z.number().int().positive(),
  previous_event_id: WorkflowRunEventId.nullable(),
  idempotency_key: PortableNonBlankString,
  occurred_at: z.iso.datetime({ offset: true }),
  provenance: WorkflowRunEventProvenanceV1Schema,
  transition: WorkflowRunTransitionV1Schema,
})

export const WorkflowRunEventV1Schema = WorkflowRunEventBodyV1Schema.extend({
  event_id: WorkflowRunEventId,
}).meta({ title: 'Hermes WorkflowRunEventV1' })

export type WorkflowRunEventBodyV1 = z.infer<
  typeof WorkflowRunEventBodyV1Schema
>
export type WorkflowRunEventV1 = z.infer<typeof WorkflowRunEventV1Schema>

function canonicalEventBody(body: WorkflowRunEventBodyV1): string {
  return JSON.stringify({
    schema_version: body.schema_version,
    run_id: body.run_id,
    sequence: body.sequence,
    previous_event_id: body.previous_event_id,
    idempotency_key: body.idempotency_key,
    occurred_at: body.occurred_at,
    provenance: {
      producer: body.provenance.producer,
      artifact_path: body.provenance.artifact_path,
      artifact_digest: body.provenance.artifact_digest,
    },
    transition: {
      from: body.transition.from,
      to: body.transition.to,
    },
  })
}

export function deriveWorkflowRunEventIdV1(body: WorkflowRunEventBodyV1): string {
  return `wre_${createHash('sha256').update(canonicalEventBody(body)).digest('hex')}`
}

export function createWorkflowRunEventV1(input: unknown): WorkflowRunEventV1 {
  const body = WorkflowRunEventBodyV1Schema.parse(input)
  return WorkflowRunEventV1Schema.parse({
    ...body,
    event_id: deriveWorkflowRunEventIdV1(body),
  })
}

export type WorkflowRunEventLedgerReplayV1Result =
  | {
      ok: true
      run_id: string | null
      status: WorkflowRunStatusV1
      applied: number
      duplicates: number
      last_event_id: string | null
    }
  | {
      ok: false
      reason:
        | 'invalid_initial'
        | 'invalid_events'
        | 'invalid_event'
        | 'identity_mismatch'
        | 'mixed_run'
        | 'sequence_mismatch'
        | 'broken_link'
        | 'time_reversal'
        | 'idempotency_conflict'
        | 'event_id_conflict'
        | 'discontinuous_transition'
      index?: number
    }

function sameEvent(left: WorkflowRunEventV1, right: WorkflowRunEventV1): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

type PreciseInstant = {
  epochSecond: bigint
  fraction: string
}

function parsePreciseInstant(timestamp: string): PreciseInstant | null {
  const match = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})(?::(\d{2})(?:\.(\d+))?)?(Z|[+-]\d{2}:\d{2})$/.exec(
    timestamp,
  )
  if (!match) return null
  const epochMilliseconds = Date.parse(`${match[1]}:${match[2] ?? '00'}${match[4]}`)
  if (!Number.isFinite(epochMilliseconds) || epochMilliseconds % 1000 !== 0) {
    return null
  }
  return {
    epochSecond: BigInt(epochMilliseconds / 1000),
    fraction: match[3] ?? '',
  }
}

function comparePreciseInstants(left: PreciseInstant, right: PreciseInstant): number {
  if (left.epochSecond < right.epochSecond) return -1
  if (left.epochSecond > right.epochSecond) return 1
  const width = Math.max(left.fraction.length, right.fraction.length)
  const leftFraction = left.fraction.padEnd(width, '0')
  const rightFraction = right.fraction.padEnd(width, '0')
  if (leftFraction < rightFraction) return -1
  if (leftFraction > rightFraction) return 1
  return 0
}

export function replayWorkflowRunEventLedgerV1(
  initial: unknown,
  events: unknown,
): WorkflowRunEventLedgerReplayV1Result {
  const parsedInitial = WorkflowRunStatusV1Schema.safeParse(initial)
  if (!parsedInitial.success) return { ok: false, reason: 'invalid_initial' }
  if (!Array.isArray(events)) return { ok: false, reason: 'invalid_events' }

  let status: WorkflowRunStatusV1 = parsedInitial.data
  let runId: string | null = null
  let lastEventId: string | null = null
  let lastOccurredAt: PreciseInstant | null = null
  let applied = 0
  let duplicates = 0
  const byIdempotencyKey = new Map<string, WorkflowRunEventV1>()
  const byEventId = new Map<string, WorkflowRunEventV1>()

  for (const [index, candidate] of events.entries()) {
    const parsed = WorkflowRunEventV1Schema.safeParse(candidate)
    if (!parsed.success) return { ok: false, reason: 'invalid_event', index }
    const current = parsed.data
    const { event_id: _eventId, ...body } = current
    if (deriveWorkflowRunEventIdV1(body) !== current.event_id) {
      return { ok: false, reason: 'identity_mismatch', index }
    }

    const existingKey = byIdempotencyKey.get(current.idempotency_key)
    if (existingKey) {
      if (sameEvent(existingKey, current)) {
        duplicates += 1
        continue
      }
      return { ok: false, reason: 'idempotency_conflict', index }
    }
    const existingId = byEventId.get(current.event_id)
    if (existingId) {
      if (sameEvent(existingId, current)) {
        duplicates += 1
        continue
      }
      return { ok: false, reason: 'event_id_conflict', index }
    }

    if (runId !== null && current.run_id !== runId) {
      return { ok: false, reason: 'mixed_run', index }
    }
    if (current.sequence !== applied + 1) {
      return { ok: false, reason: 'sequence_mismatch', index }
    }
    if (current.previous_event_id !== lastEventId) {
      return { ok: false, reason: 'broken_link', index }
    }
    const occurredAt = parsePreciseInstant(current.occurred_at)
    if (occurredAt === null) {
      return { ok: false, reason: 'invalid_event', index }
    }
    if (
      lastOccurredAt !== null &&
      comparePreciseInstants(occurredAt, lastOccurredAt) < 0
    ) {
      return { ok: false, reason: 'time_reversal', index }
    }
    if (current.transition.from !== status) {
      return { ok: false, reason: 'discontinuous_transition', index }
    }

    runId = current.run_id
    status = current.transition.to
    lastEventId = current.event_id
    lastOccurredAt = occurredAt
    applied += 1
    byIdempotencyKey.set(current.idempotency_key, current)
    byEventId.set(current.event_id, current)
  }

  return {
    ok: true,
    run_id: runId,
    status,
    applied,
    duplicates,
    last_event_id: lastEventId,
  }
}

export function toWorkflowRunEventV1JsonSchema(): Record<string, unknown> {
  return {
    $id: 'https://hermes-agent.local/schemas/workflow-run-event-v1.schema.json',
    ...z.toJSONSchema(WorkflowRunEventV1Schema, { target: 'draft-2020-12' }),
  }
}
