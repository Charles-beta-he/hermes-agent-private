import { createHash } from 'node:crypto'

import { z } from 'zod'

import {
  WorkflowIdentifierV1Schema,
  WorkflowPortableNonBlankStringV1Schema,
} from './workflow-definition'
import { WorkflowLoopSignalV1Schema } from './workflow-loop'

const WorkflowLoopEventIdV1Schema = z.string().regex(/^wle_[0-9a-f]{64}$/)
const Sha256DigestV1Schema = z.string().regex(/^sha256:[0-9a-f]{64}$/)

export const WorkflowLoopEventBodyV1Schema = z.strictObject({
  schema_version: z.literal('workflow_loop_event_v1'),
  run_id: WorkflowIdentifierV1Schema,
  session_owner: WorkflowIdentifierV1Schema,
  sequence: z.number().int().positive(),
  previous_event_id: WorkflowLoopEventIdV1Schema.nullable(),
  idempotency_key: WorkflowPortableNonBlankStringV1Schema,
  occurred_at: z.iso.datetime({ offset: true }),
  expires_at: z.iso.datetime({ offset: true }),
  definition_digest: Sha256DigestV1Schema,
  plan_digest: Sha256DigestV1Schema,
  signal: WorkflowLoopSignalV1Schema,
})

export const WorkflowLoopEventV1Schema = WorkflowLoopEventBodyV1Schema.extend({
  event_id: WorkflowLoopEventIdV1Schema,
}).meta({ title: 'Hermes WorkflowLoopEventV1' })

export type WorkflowLoopEventBodyV1 = z.infer<
  typeof WorkflowLoopEventBodyV1Schema
>
export type WorkflowLoopEventV1 = z.infer<typeof WorkflowLoopEventV1Schema>

function canonicalWorkflowLoopEventBodyV1(
  body: WorkflowLoopEventBodyV1,
): string {
  return JSON.stringify({
    schema_version: body.schema_version,
    run_id: body.run_id,
    session_owner: body.session_owner,
    sequence: body.sequence,
    previous_event_id: body.previous_event_id,
    idempotency_key: body.idempotency_key,
    occurred_at: body.occurred_at,
    expires_at: body.expires_at,
    definition_digest: body.definition_digest,
    plan_digest: body.plan_digest,
    signal: {
      schema_version: body.signal.schema_version,
      signal_id: body.signal.signal_id,
      kind: body.signal.kind,
      expected_sequence: body.signal.expected_sequence,
      task_id: body.signal.task_id,
      reason: body.signal.reason,
    },
  })
}

export function deriveWorkflowLoopEventIdV1(
  body: WorkflowLoopEventBodyV1,
): string {
  return `wle_${createHash('sha256').update(canonicalWorkflowLoopEventBodyV1(body)).digest('hex')}`
}

export function createWorkflowLoopEventV1(
  input: unknown,
): WorkflowLoopEventV1 {
  const body = WorkflowLoopEventBodyV1Schema.parse(input)
  return WorkflowLoopEventV1Schema.parse({
    ...body,
    event_id: deriveWorkflowLoopEventIdV1(body),
  })
}

export function toWorkflowLoopEventV1JsonSchema(): Record<string, unknown> {
  return {
    $id: 'https://hermes-agent.local/schemas/workflow-loop-event-v1.schema.json',
    ...z.toJSONSchema(WorkflowLoopEventV1Schema, {
      target: 'draft-2020-12',
    }),
  }
}
