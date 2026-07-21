import { z } from 'zod'

import {
  WorkflowHandoffFieldV1Schema,
  WorkflowIdentifierV1Schema,
  WorkflowSideEffectV1Schema,
  WorkflowStopConditionV1Schema,
  WorkflowTaskRoleV1Schema,
} from './workflow-definition'

const Sha256Digest = z.string().regex(/^sha256:[0-9a-f]{64}$/)
const IdentifierList = z.array(WorkflowIdentifierV1Schema).min(1).max(256)

export const WorkflowExecutionPlanWaveV1Schema = z.strictObject({
  index: z.number().int().positive(),
  task_ids: IdentifierList,
})

export const WorkflowExecutionTaskBindingV1Schema = z.strictObject({
  task_id: WorkflowIdentifierV1Schema,
  role: WorkflowTaskRoleV1Schema,
  owner: WorkflowIdentifierV1Schema,
  side_effect: WorkflowSideEffectV1Schema,
  capabilities: z.array(WorkflowIdentifierV1Schema).max(128),
  writes: z.array(z.string().min(1).max(4096).regex(/\S/)).max(128),
})

export const WorkflowVerifierLinkV1Schema = z.strictObject({
  execute_task_id: WorkflowIdentifierV1Schema,
  verifier_task_ids: IdentifierList,
})

export const WorkflowExecutionPlanV1Schema = z
  .strictObject({
    schema_version: z.literal('workflow_execution_plan_v1'),
    workflow_ref: z.strictObject({
      workflow_id: WorkflowIdentifierV1Schema,
      version: z.number().int().positive(),
    }),
    definition_digest: Sha256Digest,
    authority_granted: z.literal(false),
    executable: z.literal(false),
    waves: z.array(WorkflowExecutionPlanWaveV1Schema).min(1).max(256),
    task_bindings: z
      .array(WorkflowExecutionTaskBindingV1Schema)
      .min(1)
      .max(256),
    verifier_links: z.array(WorkflowVerifierLinkV1Schema).max(256),
    stop_conditions: z.array(WorkflowStopConditionV1Schema).min(2).max(16),
    handoff_required_fields: z
      .array(WorkflowHandoffFieldV1Schema)
      .min(1)
      .max(16),
  })
  .meta({ title: 'Hermes WorkflowExecutionPlanV1' })

export type WorkflowExecutionPlanV1 = z.infer<
  typeof WorkflowExecutionPlanV1Schema
>

export function toWorkflowExecutionPlanV1JsonSchema(): Record<string, unknown> {
  return {
    $id: 'https://hermes-agent.local/schemas/workflow-execution-plan-v1.schema.json',
    ...z.toJSONSchema(WorkflowExecutionPlanV1Schema, {
      target: 'draft-2020-12',
    }),
  }
}
