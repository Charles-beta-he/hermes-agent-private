import { writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

import { toClosureV1JsonSchema } from '../src/closure'
import { toEvidenceV1JsonSchema } from '../src/evidence'
import { toVerifierVoteV1JsonSchema } from '../src/verifier-vote'
import { toWorkflowRunStatusV1JsonSchema } from '../src/workflow-run-status'

const schemaDir = fileURLToPath(new URL('../../../schemas/', import.meta.url))

const schemas = [
  ['evidence-v1.schema.json', toEvidenceV1JsonSchema()],
  ['verifier-vote-v1.schema.json', toVerifierVoteV1JsonSchema()],
  ['closure-v1.schema.json', toClosureV1JsonSchema()],
  ['workflow-run-status-v1.schema.json', toWorkflowRunStatusV1JsonSchema()],
] as const

await Promise.all(
  schemas.map(([name, schema]) =>
    writeFile(`${schemaDir}${name}`, `${JSON.stringify(schema, null, 2)}\n`),
  ),
)
