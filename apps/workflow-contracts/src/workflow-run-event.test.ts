import { describe, expect, it } from 'vitest'

import cases from '../../../tests/contracts/fixtures/workflow-run-event-v1/cases.json'
import nonblankCases from '../../../tests/contracts/fixtures/workflow-run-event-v1/nonblank-cases.json'
import generatedSchema from '../../../schemas/workflow-run-event-v1.schema.json'
import {
  createWorkflowRunEventV1,
  replayWorkflowRunEventLedgerV1,
  toWorkflowRunEventV1JsonSchema,
  WorkflowRunEventV1Schema,
} from './workflow-run-event'

const provenance = {
  producer: 'workflow_runtime_module_executor',
  artifact_path: 'docs/private/runtime/module.json',
  artifact_digest: `sha256:${'a'.repeat(64)}`,
} as const

function event(
  sequence: number,
  from: 'proposed' | 'claimed' | 'running' | 'review',
  to: 'claimed' | 'running' | 'review' | 'done',
  previousEventId: string | null,
  idempotencyKey = `run-1:${sequence}`,
  occurredAt = `2026-07-20T00:00:0${sequence}Z`,
) {
  return createWorkflowRunEventV1({
    schema_version: 'workflow_run_event_v1',
    run_id: 'run-1',
    sequence,
    previous_event_id: previousEventId,
    idempotency_key: idempotencyKey,
    occurred_at: occurredAt,
    provenance,
    transition: { from, to } as never,
  })
}

function bodyOf(value: ReturnType<typeof event>) {
  const { event_id: _eventId, ...body } = value
  return body
}

const portableNonblankFields = [
  'run_id',
  'idempotency_key',
  'provenance.producer',
  'provenance.artifact_path',
] as const

function eventWithNonblankField(
  field: (typeof portableNonblankFields)[number],
  value: string,
) {
  const candidate = structuredClone(event(1, 'proposed', 'claimed', null))
  if (field === 'run_id' || field === 'idempotency_key') {
    candidate[field] = value
  } else if (field === 'provenance.producer') {
    candidate.provenance.producer = value
  } else {
    candidate.provenance.artifact_path = value
  }
  return candidate
}

function fullChain() {
  const first = event(1, 'proposed', 'claimed', null)
  const second = event(2, 'claimed', 'running', first.event_id)
  const third = event(3, 'running', 'review', second.event_id)
  const fourth = event(4, 'review', 'done', third.event_id)
  return [first, second, third, fourth]
}

describe('WorkflowRunEventV1', () => {
  for (const testCase of cases) {
    it(`shared fixture: ${testCase.name}`, () => {
      expect(WorkflowRunEventV1Schema.safeParse(testCase.value).success).toBe(
        testCase.valid,
      )
    })
  }

  for (const field of portableNonblankFields) {
    for (const testCase of nonblankCases) {
      it(`portable nonblank: ${field}: ${testCase.name}`, () => {
        const parsed = WorkflowRunEventV1Schema.safeParse(
          eventWithNonblankField(field, testCase.value),
        )
        expect(parsed.success).toBe(testCase.valid)
        if (parsed.success && testCase.valid) {
          const parsedValue =
            field === 'run_id' || field === 'idempotency_key'
              ? parsed.data[field]
              : field === 'provenance.producer'
                ? parsed.data.provenance.producer
                : parsed.data.provenance.artifact_path
          expect(parsedValue).toBe(testCase.value)
        }
      })
    }

    it.each([0xd800, 0xdfff])(
      `portable nonblank: ${field}: rejects unpaired surrogate U+%s`,
      (codeUnit) => {
        expect(
          WorkflowRunEventV1Schema.safeParse(
            eventWithNonblankField(
              field,
              `run-${String.fromCharCode(codeUnit)}`,
            ),
          ).success,
        ).toBe(false)
      },
    )
  }

  it('keeps the checked-in JSON Schema generated from Zod', () => {
    expect(toWorkflowRunEventV1JsonSchema()).toEqual(generatedSchema)
  })

  it('creates a strict runtime-valid event with deterministic identity', () => {
    const first = event(1, 'proposed', 'claimed', null)
    expect(WorkflowRunEventV1Schema.parse(first)).toEqual(first)
    expect(event(1, 'proposed', 'claimed', null).event_id).toBe(first.event_id)
    expect(first.event_id).toMatch(/^wre_[0-9a-f]{64}$/)
  })

  it('changes identity when provenance or transition content changes', () => {
    const first = event(1, 'proposed', 'claimed', null)
    const changed = createWorkflowRunEventV1({
      ...bodyOf(first),
      provenance: { ...provenance, artifact_digest: `sha256:${'b'.repeat(64)}` },
    })
    expect(changed.event_id).not.toBe(first.event_id)
  })

  it.each([
    ['unknown field', { ...event(1, 'proposed', 'claimed', null), extra: true }],
    ['invalid digest', { ...event(1, 'proposed', 'claimed', null), provenance: { ...provenance, artifact_digest: 'sha256:nope' } }],
    ['invalid sequence', { ...event(1, 'proposed', 'claimed', null), sequence: 0 }],
    ['invalid timestamp', { ...event(1, 'proposed', 'claimed', null), occurred_at: 'today' }],
    ['invalid transition', { ...event(1, 'proposed', 'claimed', null), transition: { from: 'proposed', to: 'done' } }],
  ])('rejects %s', (_name, candidate) => {
    expect(WorkflowRunEventV1Schema.safeParse(candidate).success).toBe(false)
  })
})

describe('workflow event ledger replay', () => {
  it('replays a complete linked chain', () => {
    expect(replayWorkflowRunEventLedgerV1('proposed', fullChain())).toEqual({
      ok: true,
      run_id: 'run-1',
      status: 'done',
      applied: 4,
      duplicates: 0,
      last_event_id: fullChain()[3]?.event_id,
    })
  })

  it('deduplicates an exact idempotent retry without applying it twice', () => {
    const chain = fullChain()
    expect(replayWorkflowRunEventLedgerV1('proposed', [chain[0], chain[0], ...chain.slice(1)])).toMatchObject({
      ok: true,
      status: 'done',
      applied: 4,
      duplicates: 1,
    })
  })

  it.each([
    ['non-array input', {}, 'invalid_events'],
    ['invalid initial status', [], 'invalid_initial'],
    ['invalid event identity', (() => { const [first] = fullChain(); return [{ ...first, event_id: `wre_${'0'.repeat(64)}` }] })(), 'identity_mismatch'],
    ['tampered first sequence', (() => { const [first] = fullChain(); return [{ ...first, sequence: 2 }] })(), 'identity_mismatch'],
    ['first sequence gap', (() => { const [first] = fullChain(); return [createWorkflowRunEventV1({ ...bodyOf(first!), sequence: 2 })] })(), 'sequence_mismatch'],
    ['tampered mixed run ids', (() => { const chain = fullChain(); return [chain[0], { ...chain[1], run_id: 'run-2' }] })(), 'identity_mismatch'],
    ['mixed run ids', (() => { const chain = fullChain(); const second = createWorkflowRunEventV1({ ...bodyOf(chain[1]!), run_id: 'run-2' }); return [chain[0], second] })(), 'mixed_run'],
    ['broken previous link', (() => { const chain = fullChain(); const second = createWorkflowRunEventV1({ ...bodyOf(chain[1]!), previous_event_id: null }); return [chain[0], second] })(), 'broken_link'],
    ['time reversal', (() => { const chain = fullChain(); const second = createWorkflowRunEventV1({ ...bodyOf(chain[1]!), occurred_at: '2026-07-19T23:59:59Z' }); return [chain[0], second] })(), 'time_reversal'],
    ['sub-millisecond time reversal', (() => { const chain = fullChain(); const first = createWorkflowRunEventV1({ ...bodyOf(chain[0]!), occurred_at: '2026-07-20T00:00:01.0009Z' }); const second = createWorkflowRunEventV1({ ...bodyOf(chain[1]!), previous_event_id: first.event_id, occurred_at: '2026-07-20T00:00:01.0001Z' }); return [first, second] })(), 'time_reversal'],
    ['idempotency conflict', (() => { const chain = fullChain(); const conflict = createWorkflowRunEventV1({ ...bodyOf(chain[1]!), idempotency_key: chain[0]!.idempotency_key }); return [chain[0], conflict] })(), 'idempotency_conflict'],
  ])('fails closed for %s', (name, events, reason) => {
    const initial = name === 'invalid initial status' ? 'ready' : 'proposed'
    expect(replayWorkflowRunEventLedgerV1(initial, events)).toMatchObject({ ok: false, reason })
  })

  it.each([
    ['Z', '2026-07-20T00:00Z', '2026-07-20T00:01Z'],
    ['offset', '2026-07-20T08:00+08:00', '2026-07-20T08:01+08:00'],
  ])('replays schema-valid timestamps with omitted seconds (%s)', (_name, firstAt, secondAt) => {
    const chain = fullChain()
    const first = createWorkflowRunEventV1({
      ...bodyOf(chain[0]!),
      occurred_at: firstAt,
    })
    const second = createWorkflowRunEventV1({
      ...bodyOf(chain[1]!),
      previous_event_id: first.event_id,
      occurred_at: secondAt,
    })
    expect(replayWorkflowRunEventLedgerV1('proposed', [first, second])).toMatchObject({
      ok: true,
      applied: 2,
      status: 'running',
    })
  })

  it('accepts equal instants expressed at different fractional precision', () => {
    const chain = fullChain()
    const first = createWorkflowRunEventV1({
      ...bodyOf(chain[0]!),
      occurred_at: '2026-07-20T00:00:01.0001Z',
    })
    const second = createWorkflowRunEventV1({
      ...bodyOf(chain[1]!),
      previous_event_id: first.event_id,
      occurred_at: '2026-07-20T00:00:01.00010Z',
    })
    expect(replayWorkflowRunEventLedgerV1('proposed', [first, second])).toMatchObject({
      ok: true,
      applied: 2,
      status: 'running',
    })
  })

  it('does not mutate caller input', () => {
    const chain = fullChain()
    const before = JSON.stringify(chain)
    replayWorkflowRunEventLedgerV1('proposed', chain)
    expect(JSON.stringify(chain)).toBe(before)
  })
})
