import { describe, expect, it } from 'vitest'

import claimReadback from '../../../docs/private/workflow-kernel-runtime-claims/decision_brief_pack.run_preview_78eee6afcec1f12a.rti_2c322fc59264ae3d.clex_22a18e493dc020c4.runtime_claim_execution_packet.json'
import executionReadback from '../../../docs/private/workflow-kernel-runtime-executions/decision_brief_pack.run_preview_78eee6afcec1f12a.rti_2c322fc59264ae3d.clex_22a18e493dc020c4.rtex_8d01cdb6170a549f.runtime_execution_worker_packet.json'
import moduleTransition from '../../../docs/private/workflow-kernel-runtime-module-executions/decision_brief_pack.run_preview_78eee6afcec1f12a.rti_2c322fc59264ae3d.clex_22a18e493dc020c4.rtex_8d01cdb6170a549f.modex_5bca069e18d6d922.runtime_module_execution_packet.json'
import reviewTransition from '../../../docs/private/workflow-kernel-runtime-review-done/decision_brief_pack.run_preview_78eee6afcec1f12a.rti_2c322fc59264ae3d.clex_22a18e493dc020c4.rtex_8d01cdb6170a549f.modex_5bca069e18d6d922.done_928841ef9a25840f.runtime_review_done_packet.json'
import { projectWorkflowRunPacketV1 } from './workflow-run-packet-projection'

describe('projectWorkflowRunPacketV1', () => {
  it('classifies the real claim artifact as a terminal readback, not a skip transition', () => {
    expect(projectWorkflowRunPacketV1(claimReadback)).toEqual({
      ok: true,
      kind: 'terminal_readback',
      stage: 'claim',
      observed_status: 'done',
    })
  })

  it('classifies the real worker artifact as a terminal readback', () => {
    expect(projectWorkflowRunPacketV1(executionReadback)).toEqual({
      ok: true,
      kind: 'terminal_readback',
      stage: 'execution',
      observed_status: 'done',
    })
  })

  it('projects the real module artifact as a canonical transition', () => {
    expect(projectWorkflowRunPacketV1(moduleTransition)).toEqual({
      ok: true,
      kind: 'transition',
      stage: 'module',
      transition: { from: 'running', to: 'review' },
    })
  })

  it('projects the real review artifact as a canonical transition', () => {
    expect(projectWorkflowRunPacketV1(reviewTransition)).toEqual({
      ok: true,
      kind: 'transition',
      stage: 'review_done',
      transition: { from: 'review', to: 'done' },
    })
  })

  it.each([
    ['missing stage', {}],
    ['malformed stage', { claim_execution: null }],
  ])('rejects an invalid packet projection: %s', (_name, packet) => {
    expect(projectWorkflowRunPacketV1(packet)).toEqual({
      ok: false,
      reason: 'invalid_packet',
    })
  })

  it('rejects packets containing more than one recognized stage', () => {
    expect(
      projectWorkflowRunPacketV1({
        claim_execution: claimReadback.claim_execution,
        runtime_execution: executionReadback.runtime_execution,
      }),
    ).toEqual({ ok: false, reason: 'ambiguous_packet' })
  })

  it('rejects out-of-domain statuses even with an exact readback marker', () => {
    expect(
      projectWorkflowRunPacketV1({
        boundary: 'runtime_claim_executor_idempotent_terminal_readback',
        claim_execution: {
          previous_status: 'proposed',
          new_status: 'archived',
          claim_scope: 'idempotent_terminal_readback_no_mutation',
        },
      }),
    ).toEqual({ ok: false, reason: 'invalid_status' })
  })

  it('rejects a noncanonical change without an exact readback marker', () => {
    expect(
      projectWorkflowRunPacketV1({
        claim_execution: {
          previous_status: 'proposed',
          new_status: 'done',
          claim_scope: 'single_task_no_worker_no_model_no_adapter',
        },
      }),
    ).toEqual({ ok: false, reason: 'unsupported_transition' })
  })

  it('rejects contradictory readback markers instead of falling back to a transition', () => {
    expect(
      projectWorkflowRunPacketV1({
        boundary: 'runtime_claim_executor_idempotent_terminal_readback',
        claim_execution: {
          previous_status: 'proposed',
          new_status: 'claimed',
          claim_scope: 'idempotent_terminal_readback_no_mutation',
        },
      }),
    ).toEqual({ ok: false, reason: 'unsupported_transition' })
  })

  it.each([
    [
      'claim boundary on module stage',
      {
        boundary: 'runtime_claim_executor_idempotent_terminal_readback',
        module_execution: {
          previous_status: 'running',
          new_status: 'review',
        },
      },
    ],
    [
      'worker boundary on review stage',
      {
        boundary: 'runtime_execution_worker_idempotent_terminal_readback',
        review_done: { previous_status: 'review', new_status: 'done' },
      },
    ],
    [
      'worker scope on claim stage',
      {
        claim_execution: {
          previous_status: 'proposed',
          new_status: 'claimed',
          worker_scope: 'idempotent_terminal_readback_no_mutation',
        },
      },
    ],
    [
      'claim scope on execution stage',
      {
        runtime_execution: {
          previous_status: 'claimed',
          new_status: 'running',
          claim_scope: 'idempotent_terminal_readback_no_mutation',
        },
      },
    ],
  ])('rejects cross-stage readback markers: %s', (_name, packet) => {
    expect(projectWorkflowRunPacketV1(packet)).toEqual({
      ok: false,
      reason: 'unsupported_transition',
    })
  })

  it.each([
    [
      'claim readback with worker scope',
      {
        ...claimReadback,
        claim_execution: {
          ...claimReadback.claim_execution,
          worker_scope: 'idempotent_terminal_readback_no_mutation',
        },
      },
    ],
    [
      'worker readback with claim scope',
      {
        ...executionReadback,
        runtime_execution: {
          ...executionReadback.runtime_execution,
          claim_scope: 'idempotent_terminal_readback_no_mutation',
        },
      },
    ],
  ])('rejects contaminated readback pairs: %s', (_name, packet) => {
    expect(projectWorkflowRunPacketV1(packet)).toEqual({
      ok: false,
      reason: 'unsupported_transition',
    })
  })

  it('requires both exact scope and exact boundary for terminal readbacks', () => {
    const wrongBoundary = structuredClone(claimReadback)
    wrongBoundary.boundary = 'runtime_claim_executor_done_before_running_or_adapter'

    expect(projectWorkflowRunPacketV1(wrongBoundary)).toEqual({
      ok: false,
      reason: 'unsupported_transition',
    })
  })

  it('is deterministic and does not mutate a historical packet', () => {
    const packet = structuredClone(executionReadback)
    const before = structuredClone(packet)
    const first = projectWorkflowRunPacketV1(packet)
    const second = projectWorkflowRunPacketV1(packet)

    expect(first).toEqual(second)
    expect(packet).toEqual(before)
  })
})
