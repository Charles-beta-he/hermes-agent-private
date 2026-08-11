import { readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

import { toClosureV1JsonSchema } from '../src/closure'
import {
  toDelegationExpansionProjectionV1JsonSchema,
  toDelegationExpansionProposalV1JsonSchema,
} from '../src/delegation-expansion-proposal'
import { toEvidenceV1JsonSchema } from '../src/evidence'
import { toVerifierVoteV1JsonSchema } from '../src/verifier-vote'
import { toWorkflowDefinitionV1JsonSchema } from '../src/workflow-definition'
import {
  toWorkflowArtifactEventV1JsonSchema,
  toWorkflowArtifactV1JsonSchema,
  toWorkflowMutationV1JsonSchema,
} from '../src/workflow-artifact'
import { toWorkflowExecutionPlanV1JsonSchema } from '../src/workflow-execution-plan'
import {
  toWorkflowLoopSignalV1JsonSchema,
  toWorkflowLoopSnapshotV1JsonSchema,
} from '../src/workflow-loop'
import { toWorkflowLoopEventV1JsonSchema } from '../src/workflow-loop-event'
import { toWorkflowRunEventV1JsonSchema } from '../src/workflow-run-event'
import { toWorkflowRunStatusV1JsonSchema } from '../src/workflow-run-status'
import { toWorkflowRunTransitionV1JsonSchema } from '../src/workflow-run-transition'
import {
  toWorkflowExecutionEventV1JsonSchema,
  toWorkflowLeafPacketV1JsonSchema,
  toWorkflowRuntimeManifestV1JsonSchema,
} from '../src/workflow-runtime'

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
  ['workflow-artifact-v1.schema.json', toWorkflowArtifactV1JsonSchema()],
  ['workflow-mutation-v1.schema.json', toWorkflowMutationV1JsonSchema()],
  [
    'workflow-artifact-event-v1.schema.json',
    toWorkflowArtifactEventV1JsonSchema(),
  ],
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
  [
    'workflow-runtime-manifest-v1.schema.json',
    toWorkflowRuntimeManifestV1JsonSchema(),
  ],
  ['workflow-leaf-packet-v1.schema.json', toWorkflowLeafPacketV1JsonSchema()],
  [
    'workflow-execution-event-v1.schema.json',
    toWorkflowExecutionEventV1JsonSchema(),
  ],
] as const

const serializedSchemas = new Map(
  schemas.map(([name, schema]) => [name, `${JSON.stringify(schema, null, 2)}\n`]),
)

const packagedSchemaNames = [
  'workflow-run-event-v1.schema.json',
  'delegation-expansion-proposal-v1.schema.json',
  'delegation-expansion-projection-v1.schema.json',
  'workflow-definition-v1.schema.json',
  'workflow-artifact-v1.schema.json',
  'workflow-mutation-v1.schema.json',
  'workflow-artifact-event-v1.schema.json',
  'workflow-execution-plan-v1.schema.json',
  'workflow-loop-snapshot-v1.schema.json',
  'workflow-loop-signal-v1.schema.json',
  'workflow-loop-event-v1.schema.json',
  'workflow-runtime-manifest-v1.schema.json',
  'workflow-leaf-packet-v1.schema.json',
  'workflow-execution-event-v1.schema.json',
] as const

const targets = [
  ...[...serializedSchemas.entries()].map(([name, content]) => ({
    path: `${schemaDir}${name}`,
    content,
  })),
  ...packagedSchemaNames.map((name) => ({
    path: `${workflowKernelSchemaDir}${name}`,
    content: serializedSchemas.get(name)!,
  })),
]

if (process.argv.includes('--check')) {
  const drifted: string[] = []
  await Promise.all(
    targets.map(async ({ path, content }) => {
      try {
        if ((await readFile(path, 'utf8')) !== content) drifted.push(path)
      } catch {
        drifted.push(path)
      }
    }),
  )
  if (drifted.length > 0) {
    throw new Error(`generated schema drift: ${drifted.sort().join(', ')}`)
  }
} else {
  await Promise.all(targets.map(({ path, content }) => writeFile(path, content)))
}
