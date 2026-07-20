import { z } from 'zod'

export const VerifierVoteV1Schema = z.strictObject({
  id: z.string().min(1),
  model: z.string().min(1),
  vote: z.enum(['approve', 'amend', 'reject']),
  evidence_refs: z.array(z.string().min(1)).min(1),
  reasoning: z.string().min(1),
  timestamp: z.iso.datetime({ offset: true }),
}).meta({ title: 'Hermes VerifierVoteV1' })

export type VerifierVoteV1 = z.infer<typeof VerifierVoteV1Schema>

export function toVerifierVoteV1JsonSchema() {
  return {
    $id: 'https://hermes-agent.local/schemas/verifier-vote-v1.schema.json',
    ...z.toJSONSchema(VerifierVoteV1Schema, { target: 'draft-2020-12' }),
  }
}
