import { z } from 'zod'

export const WorkflowRunTransitionV1Schema = z
  .discriminatedUnion('from', [
    z.strictObject({ from: z.literal('proposed'), to: z.literal('claimed') }),
    z.strictObject({ from: z.literal('claimed'), to: z.literal('running') }),
    z.strictObject({ from: z.literal('running'), to: z.literal('review') }),
    z.strictObject({ from: z.literal('review'), to: z.literal('done') }),
  ])
  .meta({ title: 'Hermes WorkflowRunTransitionV1' })

export type WorkflowRunTransitionV1 = z.infer<
  typeof WorkflowRunTransitionV1Schema
>

export function toWorkflowRunTransitionV1JsonSchema(): Record<string, unknown> {
  return z.toJSONSchema(WorkflowRunTransitionV1Schema, {
    target: 'draft-2020-12',
  }) as Record<string, unknown>
}
