import { z } from 'zod'

export const WorkflowPortableNonBlankStringV1Schema = z
  .string()
  .min(1)
  .max(4096)
  .regex(/^[^\u0000-\u0008\u000e-\u001b\u007f-\u0084\u0086-\u009f\ud800-\udfff]*$/)
  .regex(/\S/)

export const WorkflowIdentifierV1Schema = z
  .string()
  .regex(/^[a-z][a-z0-9._-]{0,127}$/)

export const WorkflowSideEffectV1Schema = z.enum([
  'none',
  'local_read',
  'local_write',
  'external_read',
  'external_write',
])

export const WorkflowTaskRoleV1Schema = z.enum([
  'explore',
  'plan',
  'execute',
  'verify',
])

export const WorkflowEvidenceKindV1Schema = z.enum([
  'command',
  'file',
  'api',
  'screenshot',
  'log',
  'readback',
])

export const WorkflowStopConditionV1Schema = z.enum([
  'success',
  'blocked',
  'human_gate',
  'terminal_failure',
  'cancelled',
  'context_exhausted',
  'stale',
])

export const WorkflowHandoffFieldV1Schema = z.enum([
  'goal',
  'task_states',
  'artifacts',
  'evidence',
  'blockers',
  'next_step',
  'authority_state',
])

const UniqueIdentifierList = z.array(WorkflowIdentifierV1Schema).max(128)
const PortableStringList = z
  .array(WorkflowPortableNonBlankStringV1Schema)
  .max(128)

export const WorkflowAuthorityV1Schema = z.strictObject({
  max_side_effect: WorkflowSideEffectV1Schema,
  allowed_capabilities: UniqueIdentifierList,
})

export const WorkflowTaskAuthorityV1Schema = z.strictObject({
  side_effect: WorkflowSideEffectV1Schema,
  capabilities: UniqueIdentifierList,
})

export const WorkflowAcceptanceV1Schema = z.strictObject({
  id: WorkflowIdentifierV1Schema,
  claim: WorkflowPortableNonBlankStringV1Schema,
  evidence_required: z.array(WorkflowEvidenceKindV1Schema).min(1).max(16),
})

export const WorkflowTaskDefinitionV1Schema = z.strictObject({
  id: WorkflowIdentifierV1Schema,
  title: WorkflowPortableNonBlankStringV1Schema,
  role: WorkflowTaskRoleV1Schema,
  owner: WorkflowIdentifierV1Schema,
  depends_on: UniqueIdentifierList,
  inputs: PortableStringList,
  outputs: PortableStringList,
  writes: PortableStringList,
  authority: WorkflowTaskAuthorityV1Schema,
  acceptance: z.array(WorkflowAcceptanceV1Schema).min(1).max(64),
  verifies: UniqueIdentifierList,
})

export const WorkflowLoopPolicyV1Schema = z.strictObject({
  max_iterations: z.number().int().min(1).max(100),
  repeated_failure_limit: z.number().int().min(1).max(20),
  on_repeated_failure: z.enum([
    'switch_path',
    'human_gate',
    'terminal_failure',
  ]),
})

export const WorkflowDefinitionV1Schema = z
  .strictObject({
    schema_version: z.literal('workflow_definition_v1'),
    workflow_id: WorkflowIdentifierV1Schema,
    version: z.number().int().positive(),
    title: WorkflowPortableNonBlankStringV1Schema,
    goal: WorkflowPortableNonBlankStringV1Schema,
    non_goals: PortableStringList,
    constraints: PortableStringList,
    authority: WorkflowAuthorityV1Schema,
    tasks: z.array(WorkflowTaskDefinitionV1Schema).min(1).max(256),
    loop_policy: WorkflowLoopPolicyV1Schema,
    stop_conditions: z.array(WorkflowStopConditionV1Schema).min(2).max(16),
    handoff: z.strictObject({
      required_fields: z.array(WorkflowHandoffFieldV1Schema).min(1).max(16),
    }),
  })
  .meta({ title: 'Hermes WorkflowDefinitionV1' })

export type WorkflowDefinitionV1 = z.infer<typeof WorkflowDefinitionV1Schema>
export type WorkflowSideEffectV1 = z.infer<typeof WorkflowSideEffectV1Schema>
export type WorkflowTaskRoleV1 = z.infer<typeof WorkflowTaskRoleV1Schema>

export function toWorkflowDefinitionV1JsonSchema(): Record<string, unknown> {
  return {
    $id: 'https://hermes-agent.local/schemas/workflow-definition-v1.schema.json',
    ...z.toJSONSchema(WorkflowDefinitionV1Schema, { target: 'draft-2020-12' }),
  }
}
