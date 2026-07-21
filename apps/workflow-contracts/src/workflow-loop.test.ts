import { describe, expect, it } from 'vitest'

import generatedSignalSchema from '../../../schemas/workflow-loop-signal-v1.schema.json'
import generatedSnapshotSchema from '../../../schemas/workflow-loop-snapshot-v1.schema.json'
import {
  toWorkflowLoopSignalV1JsonSchema,
  toWorkflowLoopSnapshotV1JsonSchema,
  WorkflowLoopSignalV1Schema,
  WorkflowLoopSnapshotV1Schema,
} from './workflow-loop'

const snapshot = {
  schema_version: 'workflow_loop_snapshot_v1',
  workflow_ref: { workflow_id: 'deep-absorption', version: 1 },
  definition_digest:
    'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  plan_digest:
    'sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
  sequence: 0,
  last_signal_id: null,
  iteration: 1,
  consecutive_failures: 0,
  task_states: [
    { task_id: 'inspect', state: 'ready' },
    { task_id: 'implement', state: 'pending' },
  ],
  workflow_state: 'ready',
  terminal: false,
  stop_reason: null,
  continuation_action: 'continue',
  authority_granted: false,
  executable: false,
}

const signal = {
  schema_version: 'workflow_loop_signal_v1',
  signal_id: 'signal-1',
  kind: 'task_started_observed',
  expected_sequence: 0,
  task_id: 'inspect',
  reason: 'Observed local task start.',
}

describe('WorkflowLoop contracts', () => {
  it('accepts authority-neutral snapshots and observed signals', () => {
    expect(WorkflowLoopSnapshotV1Schema.safeParse(snapshot).success).toBe(true)
    expect(WorkflowLoopSignalV1Schema.safeParse(signal).success).toBe(true)
  })

  it('rejects authority or executable escalation', () => {
    expect(
      WorkflowLoopSnapshotV1Schema.safeParse({
        ...snapshot,
        authority_granted: true,
      }).success,
    ).toBe(false)
    expect(
      WorkflowLoopSnapshotV1Schema.safeParse({ ...snapshot, executable: true })
        .success,
    ).toBe(false)
  })

  it('keeps both checked-in JSON Schemas generated from Zod', () => {
    expect(toWorkflowLoopSnapshotV1JsonSchema()).toEqual(
      generatedSnapshotSchema,
    )
    expect(toWorkflowLoopSignalV1JsonSchema()).toEqual(generatedSignalSchema)
  })
})
