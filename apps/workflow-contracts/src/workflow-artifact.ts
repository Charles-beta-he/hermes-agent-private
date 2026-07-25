import { z } from 'zod'

import {
  WorkflowDefinitionV1Schema,
  WorkflowHandoffFieldV1Schema,
  WorkflowIdentifierV1Schema,
  WorkflowLoopPolicyV1Schema,
  WorkflowStopConditionV1Schema,
  WorkflowTaskDefinitionV1Schema,
} from './workflow-definition'

const DigestSchema = z.string().regex(/^sha256:[0-9a-f]{64}$/)
const NonEmptyTextSchema = z.string().trim().min(1).max(4096)
const WorkflowArtifactTimestampV1Schema = z
  .string()
  .regex(/^(?!0000-)/)
  .and(z.iso.datetime({ offset: true }))

export const WorkflowArtifactAddTaskOperationV1Schema = z.strictObject({
  op: z.literal('add_task'),
  task: WorkflowTaskDefinitionV1Schema,
})

export const WorkflowArtifactReplaceTaskOperationV1Schema = z.strictObject({
  op: z.literal('replace_task'),
  task_id: WorkflowIdentifierV1Schema,
  expected_task_digest: DigestSchema,
  task: WorkflowTaskDefinitionV1Schema,
})

export const WorkflowArtifactRemoveTaskOperationV1Schema = z.strictObject({
  op: z.literal('remove_task'),
  task_id: WorkflowIdentifierV1Schema,
  expected_task_digest: DigestSchema,
})

export const WorkflowArtifactSetMetadataOperationV1Schema = z.strictObject({
  op: z.literal('set_metadata'),
  title: NonEmptyTextSchema,
  goal: NonEmptyTextSchema,
  non_goals: z.array(NonEmptyTextSchema).max(128),
  constraints: z.array(NonEmptyTextSchema).max(128),
})

export const WorkflowArtifactSetLoopContractOperationV1Schema = z.strictObject({
  op: z.literal('set_loop_contract'),
  loop_policy: WorkflowLoopPolicyV1Schema,
  stop_conditions: z.array(WorkflowStopConditionV1Schema).min(2).max(16),
  handoff: z.strictObject({
    required_fields: z.array(WorkflowHandoffFieldV1Schema).min(1).max(16),
  }),
})

export const WorkflowArtifactMutationOperationV1Schema = z.discriminatedUnion('op', [
  WorkflowArtifactAddTaskOperationV1Schema,
  WorkflowArtifactReplaceTaskOperationV1Schema,
  WorkflowArtifactRemoveTaskOperationV1Schema,
  WorkflowArtifactSetMetadataOperationV1Schema,
  WorkflowArtifactSetLoopContractOperationV1Schema,
])

export const WorkflowMutationV1Schema = z
  .strictObject({
    schema_version: z.literal('workflow_mutation_v1'),
    artifact_id: WorkflowIdentifierV1Schema,
    base_revision: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
    base_artifact_digest: DigestSchema,
    idempotency_key: z.string().min(1).max(256),
    operations: z.array(WorkflowArtifactMutationOperationV1Schema).min(1).max(256),
  })
  .meta({ title: 'Hermes WorkflowMutationV1' })

const WorkflowArtifactEventBaseV1Shape = {
  schema_version: z.literal('workflow_artifact_event_v1'),
  artifact_id: WorkflowIdentifierV1Schema,
  sequence: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
  previous_event_id: z.union([
    z.string().regex(/^wae_[0-9a-f]{64}$/),
    z.null(),
  ]),
  idempotency_key: z.string().min(1).max(256),
  occurred_at: WorkflowArtifactTimestampV1Schema,
  event_id: z.string().regex(/^wae_[0-9a-f]{64}$/),
}

export const WorkflowArtifactEventV1Schema = z
  .discriminatedUnion('event_type', [
    z.strictObject({
      ...WorkflowArtifactEventBaseV1Shape,
      event_type: z.literal('artifact_initialized'),
      payload: z.strictObject({
        definition: WorkflowDefinitionV1Schema,
        revision: z.literal(1),
        artifact_digest: DigestSchema,
      }),
    }),
    z.strictObject({
      ...WorkflowArtifactEventBaseV1Shape,
      event_type: z.literal('mutation_committed'),
      payload: z.strictObject({
        mutation: WorkflowMutationV1Schema,
        definition: WorkflowDefinitionV1Schema,
        previous_revision: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
        new_revision: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
        previous_artifact_digest: DigestSchema,
        new_artifact_digest: DigestSchema,
      }),
    }),
  ])
  .meta({ title: 'Hermes WorkflowArtifactEventV1' })

export const WorkflowArtifactV1Schema = z
  .strictObject({
    schema_version: z.literal('workflow_artifact_v1'),
    artifact_id: WorkflowIdentifierV1Schema,
    revision: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
    artifact_digest: DigestSchema,
    definition: WorkflowDefinitionV1Schema,
    event_count: z.number().int().min(1).max(Number.MAX_SAFE_INTEGER),
    last_event_id: z.string().regex(/^wae_[0-9a-f]{64}$/),
  })
  .meta({ title: 'Hermes WorkflowArtifactV1' })

export type WorkflowMutationV1 = z.infer<typeof WorkflowMutationV1Schema>
export type WorkflowArtifactEventV1 = z.infer<typeof WorkflowArtifactEventV1Schema>
export type WorkflowArtifactV1 = z.infer<typeof WorkflowArtifactV1Schema>

function toJsonSchema(schema: z.ZodType, id: string): Record<string, unknown> {
  return {
    $id: `https://hermes-agent.local/schemas/${id}`,
    ...z.toJSONSchema(schema, { target: 'draft-2020-12' }),
  }
}

export function toWorkflowMutationV1JsonSchema(): Record<string, unknown> {
  return toJsonSchema(WorkflowMutationV1Schema, 'workflow-mutation-v1.schema.json')
}

export function toWorkflowArtifactEventV1JsonSchema(): Record<string, unknown> {
  return toJsonSchema(
    WorkflowArtifactEventV1Schema,
    'workflow-artifact-event-v1.schema.json',
  )
}

export function toWorkflowArtifactV1JsonSchema(): Record<string, unknown> {
  return toJsonSchema(WorkflowArtifactV1Schema, 'workflow-artifact-v1.schema.json')
}
