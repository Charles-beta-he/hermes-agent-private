import { describe, expect, it } from 'vitest'

import {
  toWorkflowExecutionEventV1JsonSchema,
  toWorkflowLeafPacketV1JsonSchema,
  toWorkflowRuntimeManifestV1JsonSchema,
  WorkflowExecutionEventV1Schema,
  WorkflowLeafPacketV1Schema,
  WorkflowRuntimeLeafV1Schema,
  WorkflowRuntimeManifestV1Schema,
} from './workflow-runtime'

const usage = {
  spawns: 1,
  wall_ms: 1000,
  tool_calls: 0,
  tokens: 0,
  side_effect_ops: 0,
}

describe('governed workflow runtime contracts', () => {
  it('accepts representative runtime records', () => {
    expect(
      WorkflowRuntimeManifestV1Schema.safeParse({
        schema_version: 'workflow_runtime_manifest_v1',
        run_id: 'run-1',
        identity_schema_version: 1,
        baseline_ref: 'git:abc',
        project_scope: '/tmp/project',
        profile_scope: 'default',
        tool_policy_ref: 'policy:local-pure-v1',
        model_contract_ref: 'model:none',
        authority_ref: 'authority:local',
        max_concurrency: 1,
        reservation_ttl_ms: 60_000,
        hard_budget: usage,
        leaves: {
          inspect: {
            adapter: 'local.echo',
            isolation: 'in_process_trusted_pure',
            input: null,
            output_schema: {},
            reserve: usage,
          },
        },
      }).success,
    ).toBe(true)

    expect(
      WorkflowLeafPacketV1Schema.safeParse({
        schema_version: 'workflow_leaf_packet_v1',
        leaf_id: 'inspect',
        input_hash: `sha256:${'0'.repeat(64)}`,
        baseline_ref: 'git:abc',
        authority_ref: 'authority:local',
        status: 'completed',
        claims: [],
        evidence: [],
        uncertainty: [],
        usage,
        continuation: null,
        value_present: true,
        result: null,
      }).success,
    ).toBe(true)

    expect(
      WorkflowExecutionEventV1Schema.safeParse({
        schema_version: 'workflow_execution_event_v1',
        run_id: 'run-1',
        sequence: 1,
        previous_event_id: null,
        idempotency_key: 'run-1:accepted',
        occurred_at: '2026-07-23T05:00:00Z',
        event_type: 'run_accepted',
        payload: {},
        event_id: `wee_${'0'.repeat(64)}`,
      }).success,
    ).toBe(true)
  })

  it('requires JSON-valued input and result fields', () => {
    expect(
      WorkflowRuntimeLeafV1Schema.safeParse({
        adapter: 'local.echo',
        isolation: 'in_process_trusted_pure',
        output_schema: {},
        reserve: usage,
      }).success,
    ).toBe(false)

    expect(
      WorkflowLeafPacketV1Schema.safeParse({
        schema_version: 'workflow_leaf_packet_v1',
        leaf_id: 'inspect',
        input_hash: `sha256:${'0'.repeat(64)}`,
        baseline_ref: 'git:abc',
        authority_ref: 'authority:local',
        status: 'completed',
        claims: [],
        evidence: [],
        uncertainty: [],
        usage,
        continuation: null,
        value_present: false,
      }).success,
    ).toBe(false)
  })

  it('emits named draft-2020-12 schemas and preserves non-empty leaves', () => {
    const emptyManifest = {
      schema_version: 'workflow_runtime_manifest_v1',
      run_id: 'run-empty',
      identity_schema_version: 1,
      baseline_ref: 'git:abc',
      project_scope: '/tmp/project',
      profile_scope: 'default',
      tool_policy_ref: 'policy:local-pure-v1',
      model_contract_ref: 'model:none',
      authority_ref: 'authority:local',
      max_concurrency: 1,
      reservation_ttl_ms: 60_000,
      hard_budget: usage,
      leaves: {},
    }
    expect(WorkflowRuntimeManifestV1Schema.safeParse(emptyManifest).success).toBe(false)

    const schemas = [
      toWorkflowRuntimeManifestV1JsonSchema(),
      toWorkflowLeafPacketV1JsonSchema(),
      toWorkflowExecutionEventV1JsonSchema(),
    ]
    expect(schemas.every((schema) => schema.$schema === 'https://json-schema.org/draft/2020-12/schema')).toBe(true)

    const manifest = schemas[0] as {
      properties: { leaves: { minProperties?: number } }
    }
    expect(manifest.properties.leaves.minProperties).toBe(1)
  })
})
