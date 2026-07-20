import { describe, expect, it } from 'vitest'

import { replayWorkflowRunTransitionsV1 } from './workflow-run-replay'

const fullChain = [
  { from: 'proposed', to: 'claimed' },
  { from: 'claimed', to: 'running' },
  { from: 'running', to: 'review' },
  { from: 'review', to: 'done' },
] as const

describe('replayWorkflowRunTransitionsV1', () => {
  it('replays the complete observed chain', () => {
    expect(replayWorkflowRunTransitionsV1('proposed', fullChain)).toEqual({
      ok: true,
      status: 'done',
      applied: 4,
    })
  })

  it('replays a valid partial chain', () => {
    expect(replayWorkflowRunTransitionsV1('claimed', fullChain.slice(1, 3))).toEqual({
      ok: true,
      status: 'review',
      applied: 2,
    })
  })

  it('accepts an empty replay without changing a valid initial status', () => {
    expect(replayWorkflowRunTransitionsV1('done', [])).toEqual({
      ok: true,
      status: 'done',
      applied: 0,
    })
  })

  it('rejects a non-array transition collection', () => {
    expect(replayWorkflowRunTransitionsV1('proposed', null as never)).toEqual({
      ok: false,
      reason: 'invalid_transitions',
    })
  })

  it('rejects an invalid initial status', () => {
    expect(replayWorkflowRunTransitionsV1('todo', fullChain)).toEqual({
      ok: false,
      reason: 'invalid_initial',
    })
  })

  it('rejects an invalid transition shape', () => {
    expect(
      replayWorkflowRunTransitionsV1('proposed', [
        { from: 'proposed', to: 'running' },
      ]),
    ).toEqual({
      ok: false,
      reason: 'invalid_transition',
      index: 0,
      status: 'proposed',
    })
  })

  it('rejects a valid edge that is discontinuous with replay state', () => {
    expect(
      replayWorkflowRunTransitionsV1('running', [
        { from: 'proposed', to: 'claimed' },
      ]),
    ).toEqual({
      ok: false,
      reason: 'discontinuous_transition',
      index: 0,
      status: 'running',
    })
  })

  it('is deterministic and does not mutate inputs', () => {
    const transitions = structuredClone(fullChain)
    const before = structuredClone(transitions)
    const first = replayWorkflowRunTransitionsV1('proposed', transitions)
    const second = replayWorkflowRunTransitionsV1('proposed', transitions)

    expect(first).toEqual(second)
    expect(transitions).toEqual(before)
  })
})
