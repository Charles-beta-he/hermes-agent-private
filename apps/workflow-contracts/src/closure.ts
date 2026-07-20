import { z } from 'zod'

const NonBlankString = z.string().min(1).regex(/\S/)

export const ClosureV1Schema = z.strictObject({
  id: NonBlankString,
  run_id: NonBlankString,
  outcome: z.enum(['accepted', 'rejected', 'blocked']),
  summary: NonBlankString,
  evidence_refs: z.array(NonBlankString).min(1),
  verifier_vote_refs: z.array(NonBlankString).min(1),
  closed_at: z.iso.datetime({ offset: true }),
}).meta({ title: 'Hermes ClosureV1' })

export type ClosureV1 = z.infer<typeof ClosureV1Schema>

export function toClosureV1JsonSchema() {
  return {
    $id: 'https://hermes-agent.local/schemas/closure-v1.schema.json',
    ...z.toJSONSchema(ClosureV1Schema, { target: 'draft-2020-12' }),
  }
}
