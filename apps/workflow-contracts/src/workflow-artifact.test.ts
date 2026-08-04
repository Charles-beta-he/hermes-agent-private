import { describe, expect, it } from 'vitest'

import cases from '../../../tests/contracts/fixtures/workflow-definition-v1/cases.json'
import {
  WorkflowArtifactEventV1Schema,
  WorkflowArtifactV1Schema,
  WorkflowMutationV1Schema,
} from './workflow-artifact'

const definition = cases[0]!.value

const digest = `sha256:${'0'.repeat(64)}`

const mutation = {
  schema_version: 'workflow_mutation_v1' as const,
  artifact_id: definition.workflow_id,
  base_revision: 1,
  base_artifact_digest: digest,
  idempotency_key: 'mutation-1',
  operations: [
    {
      op: 'set_metadata' as const,
      title: 'Artifact test v2',
      goal: definition.goal,
      non_goals: definition.non_goals,
      constraints: definition.constraints,
    },
  ],
}

describe('WorkflowArtifact contracts', () => {
  it('accepts a canonical artifact projection', () => {
    expect(
      WorkflowArtifactV1Schema.parse({
        schema_version: 'workflow_artifact_v1',
        artifact_id: definition.workflow_id,
        revision: 1,
        artifact_digest: digest,
        definition,
        event_count: 1,
        last_event_id: `wae_${'a'.repeat(64)}`,
      }),
    ).toBeTruthy()
  })

  it('accepts typed mutations and rejects untyped operations', () => {
    expect(WorkflowMutationV1Schema.parse(mutation)).toBeTruthy()
    expect(() =>
      WorkflowMutationV1Schema.parse({
        ...mutation,
        operations: [{ op: 'replace_all', definition }],
      }),
    ).toThrow()
  })

  it('keeps append-only artifact events strict at the envelope boundary', () => {
    const event = {
      schema_version: 'workflow_artifact_event_v1' as const,
      artifact_id: definition.workflow_id,
      sequence: 1,
      previous_event_id: null,
      idempotency_key: 'init-1',
      occurred_at: '2026-07-25T00:00:00Z',
      event_type: 'artifact_initialized' as const,
      payload: { revision: 1, definition, artifact_digest: digest },
      event_id: `wae_${'a'.repeat(64)}`,
    }
    expect(WorkflowArtifactEventV1Schema.parse(event)).toBeTruthy()
    expect(() => WorkflowArtifactEventV1Schema.parse({ ...event, extra: true })).toThrow()
    expect(() =>
      WorkflowArtifactEventV1Schema.parse({
        ...event,
        payload: { ...event.payload, unexpected: true },
      }),
    ).toThrow()
    expect(() =>
      WorkflowArtifactEventV1Schema.parse({
        ...event,
        event_type: 'mutation_committed',
      }),
    ).toThrow()
    expect(() =>
      WorkflowArtifactEventV1Schema.parse({
        ...event,
        occurred_at: '0000-02-29T00:00:00Z',
      }),
    ).toThrow()
  })
})
