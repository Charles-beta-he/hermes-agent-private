import { z } from 'zod'

export const WorkflowRunStatusV1Schema = z
  .enum(['proposed', 'claimed', 'running', 'review', 'done'])
  .meta({ title: 'Hermes WorkflowRunStatusV1' })

export type WorkflowRunStatusV1 = z.infer<typeof WorkflowRunStatusV1Schema>

export function toWorkflowRunStatusV1JsonSchema(): Record<string, unknown> {
  return z.toJSONSchema(WorkflowRunStatusV1Schema, {
    target: 'draft-2020-12',
  }) as Record<string, unknown>
}
