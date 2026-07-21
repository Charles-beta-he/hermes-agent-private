import { z } from 'zod'

import {
  WorkflowIdentifierV1Schema,
  WorkflowPortableNonBlankStringV1Schema,
  WorkflowStopConditionV1Schema,
} from './workflow-definition'

const Sha256DigestV1Schema = z.string().regex(/^sha256:[0-9a-f]{64}$/)

export const WorkflowLoopTaskStateV1Schema = z.enum([
  'pending',
  'ready',
  'running',
  'verifying',
  'passed',
  'failed',
  'blocked',
  'cancelled',
  'stale',
])

export const WorkflowLoopStateV1Schema = z.enum([
  'ready',
  'running',
  'verifying',
  'passed',
  'blocked',
  'human_gate',
  'terminal_failure',
  'cancelled',
  'context_exhausted',
  'stale',
])

export const WorkflowLoopContinuationActionV1Schema = z.enum([
  'continue',
  'retry',
  'switch_path',
  'human_gate',
  'stop',
])

export const WorkflowLoopTaskProjectionV1Schema = z.strictObject({
  task_id: WorkflowIdentifierV1Schema,
  state: WorkflowLoopTaskStateV1Schema,
})

export const WorkflowLoopSnapshotV1Schema = z
  .strictObject({
    schema_version: z.literal('workflow_loop_snapshot_v1'),
    workflow_ref: z.strictObject({
      workflow_id: WorkflowIdentifierV1Schema,
      version: z.number().int().positive(),
    }),
    definition_digest: Sha256DigestV1Schema,
    plan_digest: Sha256DigestV1Schema,
    sequence: z.number().int().min(0),
    last_signal_id: WorkflowIdentifierV1Schema.nullable(),
    iteration: z.number().int().positive(),
    consecutive_failures: z.number().int().nonnegative(),
    task_states: z.array(WorkflowLoopTaskProjectionV1Schema).min(1).max(256),
    workflow_state: WorkflowLoopStateV1Schema,
    terminal: z.boolean(),
    stop_reason: WorkflowStopConditionV1Schema.nullable(),
    continuation_action: WorkflowLoopContinuationActionV1Schema,
    authority_granted: z.literal(false),
    executable: z.literal(false),
  })
  .meta({ title: 'Hermes WorkflowLoopSnapshotV1' })

export const WorkflowLoopSignalKindV1Schema = z.enum([
  'task_started_observed',
  'task_passed_observed',
  'task_failed_observed',
  'blocked_observed',
  'human_gate_observed',
  'cancelled_observed',
  'context_exhausted_observed',
  'stale_observed',
])

export const WorkflowLoopSignalV1Schema = z
  .strictObject({
    schema_version: z.literal('workflow_loop_signal_v1'),
    signal_id: WorkflowIdentifierV1Schema,
    kind: WorkflowLoopSignalKindV1Schema,
    expected_sequence: z.number().int().nonnegative(),
    task_id: WorkflowIdentifierV1Schema.nullable(),
    reason: WorkflowPortableNonBlankStringV1Schema,
  })
  .meta({ title: 'Hermes WorkflowLoopSignalV1' })

export type WorkflowLoopSnapshotV1 = z.infer<
  typeof WorkflowLoopSnapshotV1Schema
>
export type WorkflowLoopSignalV1 = z.infer<typeof WorkflowLoopSignalV1Schema>

export function toWorkflowLoopSnapshotV1JsonSchema(): Record<string, unknown> {
  return {
    $id: 'https://hermes-agent.local/schemas/workflow-loop-snapshot-v1.schema.json',
    ...z.toJSONSchema(WorkflowLoopSnapshotV1Schema, {
      target: 'draft-2020-12',
    }),
  }
}

export function toWorkflowLoopSignalV1JsonSchema(): Record<string, unknown> {
  return {
    $id: 'https://hermes-agent.local/schemas/workflow-loop-signal-v1.schema.json',
    ...z.toJSONSchema(WorkflowLoopSignalV1Schema, {
      target: 'draft-2020-12',
    }),
  }
}
