import { writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

import { toEvidenceV1JsonSchema } from '../src/evidence'

const output = fileURLToPath(
  new URL('../../../schemas/evidence-v1.schema.json', import.meta.url),
)

await writeFile(
  output,
  `${JSON.stringify(toEvidenceV1JsonSchema(), null, 2)}\n`,
  'utf8',
)
