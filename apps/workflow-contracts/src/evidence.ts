import { z } from 'zod'

export const EvidenceV1Schema = z.strictObject({
  id: z.string().min(1),
  claim: z.string().min(1),
  source: z.enum([
    'command',
    'file',
    'api',
    'screenshot',
    'model_review',
    'user_statement',
  ]),
  method: z.string().min(1),
  artifact_path: z.string().min(1).optional(),
  digest: z.string().min(1).optional(),
  timestamp: z.iso.datetime({ offset: true }),
  verdict: z.enum(['pass', 'warn', 'fail', 'advisory', 'degraded']),
}).meta({ title: 'Hermes EvidenceV1' })

export type EvidenceV1 = z.infer<typeof EvidenceV1Schema>

export function toEvidenceV1JsonSchema(): Record<string, unknown> {
  return {
    $id: 'https://hermes-agent.local/schemas/evidence-v1.schema.json',
    ...z.toJSONSchema(EvidenceV1Schema, { target: 'draft-2020-12' }),
  }
}
