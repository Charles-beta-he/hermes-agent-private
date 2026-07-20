import { z } from 'zod'

import {
  type WorkflowRunStatusV1,
  WorkflowRunStatusV1Schema,
} from './workflow-run-status'
import {
  type WorkflowRunTransitionV1,
  WorkflowRunTransitionV1Schema,
} from './workflow-run-transition'

const StageProjectionSchema = z
  .object({
    previous_status: z.unknown(),
    new_status: z.unknown(),
    claim_scope: z.unknown().optional(),
    worker_scope: z.unknown().optional(),
  })
  .passthrough()

const PacketProjectionSchema = z
  .object({
    boundary: z.unknown().optional(),
    claim_execution: StageProjectionSchema.optional(),
    runtime_execution: StageProjectionSchema.optional(),
    module_execution: StageProjectionSchema.optional(),
    review_done: StageProjectionSchema.optional(),
  })
  .passthrough()

export type WorkflowRunPacketStageV1 =
  | 'claim'
  | 'execution'
  | 'module'
  | 'review_done'

export type WorkflowRunPacketProjectionV1 =
  | {
      ok: true
      kind: 'transition'
      stage: WorkflowRunPacketStageV1
      transition: WorkflowRunTransitionV1
    }
  | {
      ok: true
      kind: 'terminal_readback'
      stage: 'claim' | 'execution'
      observed_status: WorkflowRunStatusV1
    }
  | {
      ok: false
      reason:
        | 'invalid_packet'
        | 'ambiguous_packet'
        | 'invalid_status'
        | 'unsupported_transition'
    }

export function projectWorkflowRunPacketV1(
  packet: unknown,
): WorkflowRunPacketProjectionV1 {
  const parsedPacket = PacketProjectionSchema.safeParse(packet)
  if (!parsedPacket.success) return { ok: false, reason: 'invalid_packet' }

  const { data } = parsedPacket
  const stages = [
    { stage: 'claim' as const, value: data.claim_execution },
    { stage: 'execution' as const, value: data.runtime_execution },
    { stage: 'module' as const, value: data.module_execution },
    { stage: 'review_done' as const, value: data.review_done },
  ].filter((entry) => entry.value !== undefined)

  if (stages.length === 0) return { ok: false, reason: 'invalid_packet' }
  if (stages.length !== 1) return { ok: false, reason: 'ambiguous_packet' }

  const entry = stages[0]
  if (!entry?.value) return { ok: false, reason: 'invalid_packet' }

  const from = WorkflowRunStatusV1Schema.safeParse(
    entry.value.previous_status,
  )
  const to = WorkflowRunStatusV1Schema.safeParse(entry.value.new_status)
  if (!from.success || !to.success) {
    return { ok: false, reason: 'invalid_status' }
  }

  const claimReadbackBoundary =
    data.boundary === 'runtime_claim_executor_idempotent_terminal_readback'
  const executionReadbackBoundary =
    data.boundary === 'runtime_execution_worker_idempotent_terminal_readback'
  const claimReadbackScope =
    entry.value.claim_scope === 'idempotent_terminal_readback_no_mutation'
  const executionReadbackScope =
    entry.value.worker_scope === 'idempotent_terminal_readback_no_mutation'
  const hasKnownReadbackMarker =
    claimReadbackBoundary ||
    executionReadbackBoundary ||
    claimReadbackScope ||
    executionReadbackScope
  const claimReadback =
    entry.stage === 'claim' &&
    claimReadbackBoundary &&
    claimReadbackScope &&
    !executionReadbackBoundary &&
    !executionReadbackScope &&
    from.data === 'proposed' &&
    ['running', 'review', 'done'].includes(to.data)
  const executionReadback =
    entry.stage === 'execution' &&
    executionReadbackBoundary &&
    executionReadbackScope &&
    !claimReadbackBoundary &&
    !claimReadbackScope &&
    from.data === 'claimed' &&
    ['review', 'done'].includes(to.data)

  if (claimReadback) {
    return {
      ok: true,
      kind: 'terminal_readback',
      stage: 'claim',
      observed_status: to.data,
    }
  }
  if (executionReadback) {
    return {
      ok: true,
      kind: 'terminal_readback',
      stage: 'execution',
      observed_status: to.data,
    }
  }

  if (hasKnownReadbackMarker) {
    return { ok: false, reason: 'unsupported_transition' }
  }

  const transition = WorkflowRunTransitionV1Schema.safeParse({
    from: from.data,
    to: to.data,
  })
  if (!transition.success) {
    return { ok: false, reason: 'unsupported_transition' }
  }

  return {
    ok: true,
    kind: 'transition',
    stage: entry.stage,
    transition: transition.data,
  }
}
