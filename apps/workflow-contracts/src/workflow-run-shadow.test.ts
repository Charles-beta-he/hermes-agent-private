import { describe, expect, it } from 'vitest'

import claimPacketSchema from '../../../schemas/runtime_claim_execution_packet.schema.json'
import executionPacketSchema from '../../../schemas/runtime_execution_worker_packet.schema.json'
import modulePacketSchema from '../../../schemas/runtime_module_execution_packet.schema.json'
import reviewPacketSchema from '../../../schemas/runtime_review_done_packet.schema.json'
import { evaluateWorkflowRunShadowV1 } from './workflow-run-shadow'

type PacketSchema = {
  properties: Record<
    string,
    {
      properties?: {
        previous_status?: { const: string }
        new_status?: { const: string }
      }
    }
  >
}

function transitionFromPacketSchema(schema: unknown) {
  const packet = schema as PacketSchema
  const transition = Object.values(packet.properties).find(
    (value) => value.properties?.previous_status && value.properties.new_status,
  )?.properties

  if (!transition?.previous_status || !transition.new_status) {
    throw new Error('packet schema has no status transition')
  }
  return {
    from: transition.previous_status.const,
    to: transition.new_status.const,
  }
}

const observedTransitions = [
  claimPacketSchema,
  executionPacketSchema,
  modulePacketSchema,
  reviewPacketSchema,
].map(transitionFromPacketSchema)

describe('evaluateWorkflowRunShadowV1', () => {
  it('matches replay with the terminal status from packet schema snapshots', () => {
    expect(
      evaluateWorkflowRunShadowV1(
        observedTransitions[0]?.from,
        observedTransitions,
        observedTransitions.at(-1)?.to,
      ),
    ).toEqual({
      verdict: 'match',
      observed_status: 'done',
      candidate_status: 'done',
      applied: 4,
    })
  })

  it('reports a valid observed/candidate mismatch without changing either', () => {
    expect(
      evaluateWorkflowRunShadowV1('proposed', observedTransitions, 'review'),
    ).toEqual({
      verdict: 'mismatch',
      observed_status: 'review',
      candidate_status: 'done',
      applied: 4,
    })
  })

  it('matches a partial replay', () => {
    expect(
      evaluateWorkflowRunShadowV1(
        'claimed',
        observedTransitions.slice(1, 3),
        'review',
      ),
    ).toEqual({
      verdict: 'match',
      observed_status: 'review',
      candidate_status: 'review',
      applied: 2,
    })
  })

  it('rejects an invalid observed status before comparison', () => {
    expect(
      evaluateWorkflowRunShadowV1('proposed', observedTransitions, 'failed'),
    ).toEqual({ verdict: 'invalid_observed' })
  })

  it('surfaces replay failure without converting it to a mismatch', () => {
    expect(
      evaluateWorkflowRunShadowV1(
        'proposed',
        [{ from: 'proposed', to: 'running' }],
        'running',
      ),
    ).toEqual({
      verdict: 'replay_error',
      observed_status: 'running',
      replay: {
        ok: false,
        reason: 'invalid_transition',
        index: 0,
        status: 'proposed',
      },
    })
  })

  it('is deterministic and does not mutate packet-derived inputs', () => {
    const transitions = structuredClone(observedTransitions)
    const before = structuredClone(transitions)
    const first = evaluateWorkflowRunShadowV1('proposed', transitions, 'done')
    const second = evaluateWorkflowRunShadowV1('proposed', transitions, 'done')

    expect(first).toEqual(second)
    expect(transitions).toEqual(before)
  })
})
