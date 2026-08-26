import { z } from 'zod'

export const GovernedLocalRequirementsAuditPolicyV1Schema = z.strictObject({
  kind: z.literal('governed_local_requirements_audit'),
  profile: z.literal('audit-local'),
  artifact_relative_path: z.literal('artifacts/audit-pack.json'),
  readback_freshness_bound_seconds: z.literal(60),
  network_adapter_calls: z.literal(false),
  external_message_calls: z.literal(false),
  calendar_calls: z.literal(false),
  notes_calls: z.literal(false),
  production_mission_writes: z.literal(false),
  active_gateway_mutations: z.literal(false),
}).meta({ title: 'Hermes GovernedLocalRequirementsAuditPolicyV1' })

export type GovernedLocalRequirementsAuditPolicyV1 = z.infer<
  typeof GovernedLocalRequirementsAuditPolicyV1Schema
>

const NonNegativeSafeIntegerSchema = z
  .number()
  .int()
  .min(0)
  .max(Number.MAX_SAFE_INTEGER)

const HostBaselineV1Shape = {
  gateway_pid: z.number().int().positive().max(Number.MAX_SAFE_INTEGER),
  gateway_source_head: z
    .string()
    .regex(/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/),
  gateway_worktree_porcelain: z.string(),
  launchagent_plist_sha256: z.string().regex(/^[0-9a-f]{64}$/),
}

const HostBaselineV1Schema = z.strictObject(HostBaselineV1Shape)

const GovernedLocalRequirementsAuditPreflightObservationV1Schema = z.strictObject({
  isolated_mission_rows: NonNegativeSafeIntegerSchema,
  isolated_work_item_rows: NonNegativeSafeIntegerSchema,
  fixed_mission_present: z.boolean(),
  fixed_work_item_present: z.boolean(),
  artifact_present: z.boolean(),
  network_adapter_calls: NonNegativeSafeIntegerSchema,
  external_message_calls: NonNegativeSafeIntegerSchema,
  calendar_calls: NonNegativeSafeIntegerSchema,
  notes_calls: NonNegativeSafeIntegerSchema,
  production_mission_writes: NonNegativeSafeIntegerSchema,
  active_gateway_mutations: NonNegativeSafeIntegerSchema,
  isolated_artifact_writes: NonNegativeSafeIntegerSchema,
  fresh_artifact_readbacks: NonNegativeSafeIntegerSchema,
  readback_freshness_bound_seconds: NonNegativeSafeIntegerSchema,
  host_baseline: HostBaselineV1Schema,
})

const PREFLIGHT_CHECK_SPECS = [
  { field: 'isolated_mission_rows', expected: 0 },
  { field: 'isolated_work_item_rows', expected: 0 },
  { field: 'fixed_mission_present', expected: false },
  { field: 'fixed_work_item_present', expected: false },
  { field: 'artifact_present', expected: false },
  { field: 'network_adapter_calls', expected: 0 },
  { field: 'external_message_calls', expected: 0 },
  { field: 'calendar_calls', expected: 0 },
  { field: 'notes_calls', expected: 0 },
  { field: 'production_mission_writes', expected: 0 },
  { field: 'active_gateway_mutations', expected: 0 },
  { field: 'isolated_artifact_writes', expected: 0 },
  { field: 'fresh_artifact_readbacks', expected: 0 },
  { field: 'readback_freshness_bound_seconds', expected: 60 },
] as const

type PreflightCheckSpec = (typeof PREFLIGHT_CHECK_SPECS)[number]

export type GovernedLocalRequirementsAuditPreflightV1Result = {
  preflight_passed: boolean
  checks: Array<{
    field: PreflightCheckSpec['field']
    expected: number | boolean
    actual: number | boolean
    pass: boolean
  }>
  observed_host_baseline: {
    gateway_pid: number
    gateway_source_head: string
    gateway_worktree_porcelain: string
    launchagent_plist_sha256: string
  }
  authority_granted: false
  executable: false
  lifecycle_mutated: false
}

export function evaluateGovernedLocalRequirementsAuditPreflightV1(
  policyInput: unknown,
  observationInput: unknown,
): GovernedLocalRequirementsAuditPreflightV1Result {
  GovernedLocalRequirementsAuditPolicyV1Schema.parse(policyInput)
  const observation =
    GovernedLocalRequirementsAuditPreflightObservationV1Schema.parse(
      observationInput,
    )
  const checks = PREFLIGHT_CHECK_SPECS.map(({ field, expected }) => {
    const actual = observation[field]
    return { field, expected, actual, pass: actual === expected }
  })

  return {
    preflight_passed: checks.every((check) => check.pass),
    checks,
    observed_host_baseline: { ...observation.host_baseline },
    authority_granted: false,
    executable: false,
    lifecycle_mutated: false,
  }
}

const GovernedLocalRequirementsAuditReadbackObservationV1Schema = z.strictObject({
  ...HostBaselineV1Shape,
  network_adapter_calls: NonNegativeSafeIntegerSchema,
  external_message_calls: NonNegativeSafeIntegerSchema,
  calendar_calls: NonNegativeSafeIntegerSchema,
  notes_calls: NonNegativeSafeIntegerSchema,
  production_mission_writes: NonNegativeSafeIntegerSchema,
  active_gateway_mutations: NonNegativeSafeIntegerSchema,
})

const READBACK_HOST_FIELDS = [
  'gateway_pid',
  'gateway_source_head',
  'gateway_worktree_porcelain',
  'launchagent_plist_sha256',
] as const

const READBACK_COUNTER_FIELDS = [
  'network_adapter_calls',
  'external_message_calls',
  'calendar_calls',
  'notes_calls',
  'production_mission_writes',
  'active_gateway_mutations',
] as const

type ReadbackComparisonField =
  | (typeof READBACK_HOST_FIELDS)[number]
  | (typeof READBACK_COUNTER_FIELDS)[number]

export type GovernedLocalRequirementsAuditReadbackComparisonV1Result = {
  comparison_passed: boolean
  checks: Array<{
    field: ReadbackComparisonField
    expected: number | string
    actual: number | string
    pass: boolean
  }>
  scope: 'host_invariance_and_forbidden_counter_comparison_only'
  authority_granted: false
  executable: false
  lifecycle_mutated: false
}

export function evaluateGovernedLocalRequirementsAuditReadbackComparisonV1(
  policyInput: unknown,
  baselineInput: unknown,
  observationInput: unknown,
): GovernedLocalRequirementsAuditReadbackComparisonV1Result {
  GovernedLocalRequirementsAuditPolicyV1Schema.parse(policyInput)
  const baseline = HostBaselineV1Schema.parse(baselineInput)
  const observation =
    GovernedLocalRequirementsAuditReadbackObservationV1Schema.parse(
      observationInput,
    )
  const checks: GovernedLocalRequirementsAuditReadbackComparisonV1Result['checks'] = [
    ...READBACK_HOST_FIELDS.map((field) => {
      const expected = baseline[field]
      const actual = observation[field]
      return { field, expected, actual, pass: actual === expected }
    }),
    ...READBACK_COUNTER_FIELDS.map((field) => {
      const actual = observation[field]
      return { field, expected: 0, actual, pass: actual === 0 }
    }),
  ]

  return {
    comparison_passed: checks.every((check) => check.pass),
    checks,
    scope: 'host_invariance_and_forbidden_counter_comparison_only',
    authority_granted: false,
    executable: false,
    lifecycle_mutated: false,
  }
}

export function toGovernedLocalRequirementsAuditPolicyV1JsonSchema() {
  return {
    $id: 'https://hermes-agent.local/schemas/governed-local-requirements-audit-policy-v1.schema.json',
    ...z.toJSONSchema(GovernedLocalRequirementsAuditPolicyV1Schema, {
      target: 'draft-2020-12',
    }),
  }
}
