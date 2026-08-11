import { writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

import { toClosureV1JsonSchema } from '../src/closure'
import {
  toDelegationExpansionProjectionV1JsonSchema,
  toDelegationExpansionProposalV1JsonSchema,
} from '../src/delegation-expansion-proposal'
import { toEvidenceV1JsonSchema } from '../src/evidence'
import { toVerifierVoteV1JsonSchema } from '../src/verifier-vote'
import { toWorkflowDefinitionV1JsonSchema } from '../src/workflow-definition'
import { toWorkflowExecutionPlanV1JsonSchema } from '../src/workflow-execution-plan'
import {
  toWorkflowLoopSignalV1JsonSchema,
  toWorkflowLoopSnapshotV1JsonSchema,
} from '../src/workflow-loop'
import { toWorkflowLoopEventV1JsonSchema } from '../src/workflow-loop-event'
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
  [
    'delegation-expansion-proposal-v1.schema.json',
    toDelegationExpansionProposalV1JsonSchema(),
  ],
  [
    'delegation-expansion-projection-v1.schema.json',
    toDelegationExpansionProjectionV1JsonSchema(),
  ],
  ['workflow-definition-v1.schema.json', toWorkflowDefinitionV1JsonSchema()],
  [
    'workflow-execution-plan-v1.schema.json',
    toWorkflowExecutionPlanV1JsonSchema(),
  ],
  ['workflow-loop-snapshot-v1.schema.json', toWorkflowLoopSnapshotV1JsonSchema()],
  ['workflow-loop-signal-v1.schema.json', toWorkflowLoopSignalV1JsonSchema()],
  ['workflow-loop-event-v1.schema.json', toWorkflowLoopEventV1JsonSchema()],
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
  writeFile(
    `${workflowKernelSchemaDir}delegation-expansion-proposal-v1.schema.json`,
    serializedSchemas.get('delegation-expansion-proposal-v1.schema.json')!,
  ),
  writeFile(
    `${workflowKernelSchemaDir}delegation-expansion-projection-v1.schema.json`,
    serializedSchemas.get('delegation-expansion-projection-v1.schema.json')!,
  ),
  writeFile(
    `${workflowKernelSchemaDir}workflow-definition-v1.schema.json`,
    serializedSchemas.get('workflow-definition-v1.schema.json')!,
  ),
  writeFile(
    `${workflowKernelSchemaDir}workflow-execution-plan-v1.schema.json`,
    serializedSchemas.get('workflow-execution-plan-v1.schema.json')!,
  ),
  writeFile(
    `${workflowKernelSchemaDir}workflow-loop-snapshot-v1.schema.json`,
    serializedSchemas.get('workflow-loop-snapshot-v1.schema.json')!,
  ),
  writeFile(
    `${workflowKernelSchemaDir}workflow-loop-signal-v1.schema.json`,
    serializedSchemas.get('workflow-loop-signal-v1.schema.json')!,
  ),
  writeFile(
    `${workflowKernelSchemaDir}workflow-loop-event-v1.schema.json`,
    serializedSchemas.get('workflow-loop-event-v1.schema.json')!,
  ),
])
