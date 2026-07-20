import { writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

import { toEvidenceV1JsonSchema } from '../src/evidence'
import { toVerifierVoteV1JsonSchema } from '../src/verifier-vote'

const schemas = [
  ['evidence-v1.schema.json', toEvidenceV1JsonSchema()],
  ['verifier-vote-v1.schema.json', toVerifierVoteV1JsonSchema()],
] as const

await Promise.all(
  schemas.map(async ([fileName, schema]) => {
    const output = fileURLToPath(
      new URL(`../../../schemas/${fileName}`, import.meta.url),
    )
    await writeFile(output, `${JSON.stringify(schema, null, 2)}\n`, 'utf8')
  }),
)
