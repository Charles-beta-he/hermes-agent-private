import { z } from 'zod'

import { WorkflowEvidenceKindV1Schema } from './workflow-definition'

export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [key: string]: JsonValue }

export const JsonValueSchema: z.ZodType<JsonValue> = z.lazy(() =>
  z.union([
    z.null(),
    z.boolean(),
    z.number(),
    z.string(),
    z.array(JsonValueSchema),
    z.record(z.string(), JsonValueSchema),
  ]),
)

export const WorkflowResourceUsageV1Schema = z.strictObject({
  spawns: z.number().int().min(0),
  wall_ms: z.number().int().min(0),
  tool_calls: z.number().int().min(0),
  tokens: z.number().int().min(0),
  side_effect_ops: z.number().int().min(0),
})

export const WorkflowRuntimeLeafV1Schema = z.strictObject({
  adapter: z.string().min(1),
  isolation: z.string().min(1),
  input: JsonValueSchema,
  output_schema: z.looseObject({}),
  reserve: WorkflowResourceUsageV1Schema,
  evidence_kinds: z
    .array(WorkflowEvidenceKindV1Schema)
    .min(1)
    .max(16)
    .refine(
      (kinds) => new Set(kinds).size === kinds.length,
      'evidence kinds must be unique',
    )
    .optional(),
})

export const WorkflowRuntimeManifestV1Schema = z
  .strictObject({
    schema_version: z.literal('workflow_runtime_manifest_v1'),
    run_id: z.string().min(1),
    identity_schema_version: z.literal(1),
    baseline_ref: z.string().min(1),
    project_scope: z.string().min(1),
    profile_scope: z.string().min(1),
    tool_policy_ref: z.string().min(1),
    model_contract_ref: z.string().min(1),
    authority_ref: z.string().min(1),
    max_concurrency: z.number().int().min(1).max(64),
    reservation_ttl_ms: z.number().int().min(1).max(86_400_000),
    hard_budget: WorkflowResourceUsageV1Schema,
    leaves: z
      .record(z.string(), WorkflowRuntimeLeafV1Schema)
      .refine((leaves) => Object.keys(leaves).length > 0, 'at least one leaf is required'),
  })
  .meta({ title: 'Hermes WorkflowRuntimeManifestV1' })

export const WorkflowLeafPacketV1Schema = z
  .strictObject({
    schema_version: z.literal('workflow_leaf_packet_v1'),
    leaf_id: z.string().min(1),
    input_hash: z.string().regex(/^sha256:[0-9a-f]{64}$/),
    baseline_ref: z.string().min(1),
    authority_ref: z.string().min(1),
    status: z.enum(['completed', 'failed', 'blocked', 'cancelled', 'unverified']),
    claims: z.array(z.string()),
    evidence: z.array(
      z.strictObject({
        type: z.string().min(1),
        ref: z.string().min(1),
      }),
    ),
    uncertainty: z.array(
      z.strictObject({
        code: z.string().min(1),
        message: z.string().min(1),
      }),
    ),
    usage: WorkflowResourceUsageV1Schema,
    continuation: z.union([z.looseObject({}), z.null()]),
    value_present: z.boolean(),
    result: JsonValueSchema,
  })
  .meta({ title: 'Hermes WorkflowLeafPacketV1' })

export const WorkflowExecutionEventV1Schema = z
  .strictObject({
    schema_version: z.literal('workflow_execution_event_v1'),
    run_id: z.string().min(1),
    sequence: z.number().int().min(1),
    previous_event_id: z.union([
      z.string().regex(/^wee_[0-9a-f]{64}$/),
      z.null(),
    ]),
    idempotency_key: z.string().min(1),
    occurred_at: z.iso.datetime({ offset: true }),
    event_type: z.enum([
      'run_accepted',
      'run_started',
      'run_cancel_requested',
      'budget_reserved',
      'budget_settled',
      'budget_released',
      'leaf_started',
      'leaf_packet',
      'run_verified',
      'run_completed',
    ]),
    payload: z.looseObject({}),
    event_id: z.string().regex(/^wee_[0-9a-f]{64}$/),
  })
  .meta({ title: 'Hermes WorkflowExecutionEventV1' })

export type WorkflowRuntimeManifestV1 = z.infer<typeof WorkflowRuntimeManifestV1Schema>
export type WorkflowLeafPacketV1 = z.infer<typeof WorkflowLeafPacketV1Schema>
export type WorkflowExecutionEventV1 = z.infer<typeof WorkflowExecutionEventV1Schema>

function toJsonSchema(
  schema: z.ZodType,
  id: string,
): Record<string, unknown> {
  return {
    $id: `https://hermes-agent.local/schemas/${id}`,
    ...z.toJSONSchema(schema, { target: 'draft-2020-12' }),
  }
}

export function toWorkflowRuntimeManifestV1JsonSchema(): Record<string, unknown> {
  const schema = toJsonSchema(
    WorkflowRuntimeManifestV1Schema,
    'workflow-runtime-manifest-v1.schema.json',
  )
  const properties = schema.properties as Record<string, Record<string, unknown>>
  properties.leaves.minProperties = 1
  const leaf = properties.leaves.additionalProperties as {
    properties: Record<string, Record<string, unknown>>
  }
  leaf.properties.evidence_kinds.uniqueItems = true
  return schema
}

export function toWorkflowLeafPacketV1JsonSchema(): Record<string, unknown> {
  return toJsonSchema(
    WorkflowLeafPacketV1Schema,
    'workflow-leaf-packet-v1.schema.json',
  )
}

export function toWorkflowExecutionEventV1JsonSchema(): Record<string, unknown> {
  return toJsonSchema(
    WorkflowExecutionEventV1Schema,
    'workflow-execution-event-v1.schema.json',
  )
}
