import { describe, expect, it } from 'vitest'

import cases from '../../../tests/contracts/fixtures/workflow-loop-event-v1/cases.json'
import generatedSchema from '../../../schemas/workflow-loop-event-v1.schema.json'
import {
  createWorkflowLoopEventV1,
  toWorkflowLoopEventV1JsonSchema,
  WorkflowLoopEventV1Schema,
} from './workflow-loop-event'

const signal = {
  schema_version: 'workflow_loop_signal_v1',
  signal_id: 'signal-1',
  kind: 'task_started_observed',
  expected_sequence: 0,
  task_id: 'inspect',
  reason: 'Observed task start.',
} as const

const body = {
  schema_version: 'workflow_loop_event_v1',
  run_id: 'run-1',
  session_owner: 'session-owner',
  sequence: 1,
  previous_event_id: null,
  idempotency_key: 'run-1:signal-1',
  occurred_at: '2026-07-21T00:00:00Z',
  expires_at: '2026-07-21T01:00:00Z',
  definition_digest: `sha256:${'a'.repeat(64)}`,
  plan_digest: `sha256:${'b'.repeat(64)}`,
  signal,
} as const

describe('WorkflowLoopEventV1', () => {
  for (const testCase of cases) {
    it(`shared fixture: ${testCase.name}`, () => {
      expect(WorkflowLoopEventV1Schema.safeParse(testCase.value).success).toBe(
        testCase.valid,
      )
    })
  }

  it('creates a strict event with deterministic content identity', () => {
    const event = createWorkflowLoopEventV1(body)

    expect(WorkflowLoopEventV1Schema.parse(event)).toEqual(event)
    expect(createWorkflowLoopEventV1(body).event_id).toBe(event.event_id)
    expect(event.event_id).toMatch(/^wle_[0-9a-f]{64}$/)
  })

  it('changes identity when the bound plan changes', () => {
    const first = createWorkflowLoopEventV1(body)
    const changed = createWorkflowLoopEventV1({
      ...body,
      plan_digest: `sha256:${'c'.repeat(64)}`,
    })

    expect(changed.event_id).not.toBe(first.event_id)
  })

  it('changes identity when the observed signal changes', () => {
    const first = createWorkflowLoopEventV1(body)
    const changed = createWorkflowLoopEventV1({
      ...body,
      signal: { ...signal, reason: 'Observed a different fact.' },
    })

    expect(changed.event_id).not.toBe(first.event_id)
  })

  it.each([
    ['authority injection', { ...body, authority_granted: true }],
    ['invalid sequence', { ...body, sequence: 0 }],
    ['invalid expiration', { ...body, expires_at: 'later' }],
    ['invalid signal', { ...body, signal: { ...signal, kind: 'execute_task' } }],
  ])('rejects %s', (_name, candidate) => {
    expect(() => createWorkflowLoopEventV1(candidate)).toThrow()
  })

  it('keeps the checked-in JSON Schema generated from Zod', () => {
    expect(toWorkflowLoopEventV1JsonSchema()).toEqual(generatedSchema)
  })
})
