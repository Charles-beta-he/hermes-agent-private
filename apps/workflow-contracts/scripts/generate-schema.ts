import { writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

import { toClosureV1JsonSchema } from '../src/closure'
import { toEvidenceV1JsonSchema } from '../src/evidence'
import { toVerifierVoteV1JsonSchema } from '../src/verifier-vote'
import { toWorkflowRunEventV1JsonSchema } from '../src/workflow-run-event'
import { toWorkflowRunStatusV1JsonSchema } from '../src/workflow-run-status'
import { toWorkflowRunTransitionV1JsonSchema } from '../src/workflow-run-transition'

const schemaDir = fileURLToPath(new URL('../../../schemas/', import.meta.url))
const workflowKernelSchemaDir = fileURLToPath(
  new URL('../../../workflow_kernel/schemas/', import.meta.url),
)

const schemas = [
  ['evidence-v1.schema.json', toEvidenceV1JsonSchema()],
  ['verifier-vote-v1.schema.json', toVerifierVoteV1JsonSchema()],
  ['closure-v1.schema.json', toClosureV1JsonSchema()],
  ['workflow-run-status-v1.schema.json', toWorkflowRunStatusV1JsonSchema()],
  ['workflow-run-event-v1.schema.json', toWorkflowRunEventV1JsonSchema()],
  [
    'workflow-run-transition-v1.schema.json',
    toWorkflowRunTransitionV1JsonSchema(),
  ],
] as const

const serializedSchemas = new Map(
  schemas.map(([name, schema]) => [name, `${JSON.stringify(schema, null, 2)}\n`]),
)

await Promise.all([
  ...[...serializedSchemas.entries()].map(([name, content]) =>
    writeFile(`${schemaDir}${name}`, content),
  ),
  writeFile(
    `${workflowKernelSchemaDir}workflow-run-event-v1.schema.json`,
    serializedSchemas.get('workflow-run-event-v1.schema.json')!,
  ),
])
