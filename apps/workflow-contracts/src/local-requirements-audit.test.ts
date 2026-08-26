import { describe, expect, expectTypeOf, it } from 'vitest'

import cases from '../../../tests/contracts/fixtures/governed-local-requirements-audit-policy-v1/cases.json'
import generatedSchema from '../../../schemas/governed-local-requirements-audit-policy-v1.schema.json'
import {
  evaluateGovernedLocalRequirementsAuditPreflightV1,
  evaluateGovernedLocalRequirementsAuditReadbackComparisonV1,
  GovernedLocalRequirementsAuditPolicyV1Schema,
  toGovernedLocalRequirementsAuditPolicyV1JsonSchema,
  type GovernedLocalRequirementsAuditPolicyV1,
  type GovernedLocalRequirementsAuditPreflightV1Result,
  type GovernedLocalRequirementsAuditReadbackComparisonV1Result,
} from './index'

const validPolicy = cases.find((testCase) => testCase.valid)!.value

const requiredFields = [
  'kind',
  'profile',
  'artifact_relative_path',
  'readback_freshness_bound_seconds',
  'network_adapter_calls',
  'external_message_calls',
  'calendar_calls',
  'notes_calls',
  'production_mission_writes',
  'active_gateway_mutations',
]

const validObservation = {
  isolated_mission_rows: 0,
  isolated_work_item_rows: 0,
  fixed_mission_present: false,
  fixed_work_item_present: false,
  artifact_present: false,
  network_adapter_calls: 0,
  external_message_calls: 0,
  calendar_calls: 0,
  notes_calls: 0,
  production_mission_writes: 0,
  active_gateway_mutations: 0,
  isolated_artifact_writes: 0,
  fresh_artifact_readbacks: 0,
  readback_freshness_bound_seconds: 60,
  host_baseline: {
    gateway_pid: 49487,
    gateway_source_head: '9e31078b6173a384bcb9b43f0818d2bc032ea605',
    gateway_worktree_porcelain: '',
    launchagent_plist_sha256:
      '0c3a714646c046c1d4a216a65681e39fdab97eca359a1a8107e401eb723a7c2d',
  },
}

const expectedChecks = [
  { field: 'isolated_mission_rows', expected: 0, actual: 0, pass: true },
  { field: 'isolated_work_item_rows', expected: 0, actual: 0, pass: true },
  { field: 'fixed_mission_present', expected: false, actual: false, pass: true },
  { field: 'fixed_work_item_present', expected: false, actual: false, pass: true },
  { field: 'artifact_present', expected: false, actual: false, pass: true },
  { field: 'network_adapter_calls', expected: 0, actual: 0, pass: true },
  { field: 'external_message_calls', expected: 0, actual: 0, pass: true },
  { field: 'calendar_calls', expected: 0, actual: 0, pass: true },
  { field: 'notes_calls', expected: 0, actual: 0, pass: true },
  { field: 'production_mission_writes', expected: 0, actual: 0, pass: true },
  { field: 'active_gateway_mutations', expected: 0, actual: 0, pass: true },
  { field: 'isolated_artifact_writes', expected: 0, actual: 0, pass: true },
  { field: 'fresh_artifact_readbacks', expected: 0, actual: 0, pass: true },
  {
    field: 'readback_freshness_bound_seconds',
    expected: 60,
    actual: 60,
    pass: true,
  },
] as const

const mismatchCases = expectedChecks.map((check) => [
  check.field,
  typeof check.expected === 'boolean' ? true : check.expected === 60 ? 61 : 1,
  check.expected,
] as const)

const numericFields = [
  'isolated_mission_rows',
  'isolated_work_item_rows',
  'network_adapter_calls',
  'external_message_calls',
  'calendar_calls',
  'notes_calls',
  'production_mission_writes',
  'active_gateway_mutations',
  'isolated_artifact_writes',
  'fresh_artifact_readbacks',
  'readback_freshness_bound_seconds',
] as const

const validBaseline = { ...validObservation.host_baseline }
const validReadbackObservation = {
  ...validBaseline,
  network_adapter_calls: 0,
  external_message_calls: 0,
  calendar_calls: 0,
  notes_calls: 0,
  production_mission_writes: 0,
  active_gateway_mutations: 0,
}

const expectedReadbackChecks = [
  {
    field: 'gateway_pid',
    expected: validBaseline.gateway_pid,
    actual: validBaseline.gateway_pid,
    pass: true,
  },
  {
    field: 'gateway_source_head',
    expected: validBaseline.gateway_source_head,
    actual: validBaseline.gateway_source_head,
    pass: true,
  },
  {
    field: 'gateway_worktree_porcelain',
    expected: validBaseline.gateway_worktree_porcelain,
    actual: validBaseline.gateway_worktree_porcelain,
    pass: true,
  },
  {
    field: 'launchagent_plist_sha256',
    expected: validBaseline.launchagent_plist_sha256,
    actual: validBaseline.launchagent_plist_sha256,
    pass: true,
  },
  { field: 'network_adapter_calls', expected: 0, actual: 0, pass: true },
  { field: 'external_message_calls', expected: 0, actual: 0, pass: true },
  { field: 'calendar_calls', expected: 0, actual: 0, pass: true },
  { field: 'notes_calls', expected: 0, actual: 0, pass: true },
  { field: 'production_mission_writes', expected: 0, actual: 0, pass: true },
  { field: 'active_gateway_mutations', expected: 0, actual: 0, pass: true },
] as const

const baselineFields = Object.keys(validBaseline) as Array<keyof typeof validBaseline>
const readbackObservationFields = Object.keys(
  validReadbackObservation,
) as Array<keyof typeof validReadbackObservation>
const forbiddenCounterFields = [
  'network_adapter_calls',
  'external_message_calls',
  'calendar_calls',
  'notes_calls',
  'production_mission_writes',
  'active_gateway_mutations',
] as const

function clone<T>(value: T): T {
  return structuredClone(value)
}

describe('GovernedLocalRequirementsAuditPolicyV1Schema', () => {
  for (const testCase of cases) {
    it(testCase.name, () => {
      expect(
        GovernedLocalRequirementsAuditPolicyV1Schema.safeParse(testCase.value)
          .success,
      ).toBe(testCase.valid)
    })
  }

  it('infers the policy type from the runtime schema', () => {
    const parsed = GovernedLocalRequirementsAuditPolicyV1Schema.parse(validPolicy)
    expectTypeOf(parsed).toEqualTypeOf<GovernedLocalRequirementsAuditPolicyV1>()
  })

  it('keeps the checked-in JSON Schema generated from Zod', () => {
    expect(toGovernedLocalRequirementsAuditPolicyV1JsonSchema()).toEqual(
      generatedSchema,
    )
    expect(generatedSchema.$schema).toBe(
      'https://json-schema.org/draft/2020-12/schema',
    )
    expect(generatedSchema.$id).toBe(
      'https://hermes-agent.local/schemas/governed-local-requirements-audit-policy-v1.schema.json',
    )
    expect(generatedSchema.additionalProperties).toBe(false)
    expect(generatedSchema.required).toEqual(requiredFields)
  })
})

describe('evaluateGovernedLocalRequirementsAuditPreflightV1', () => {
  it('returns the exact ordered passing preflight for matching observations', () => {
    const result = evaluateGovernedLocalRequirementsAuditPreflightV1(
      validPolicy,
      validObservation,
    )

    expect(result).toEqual({
      preflight_passed: true,
      checks: expectedChecks,
      observed_host_baseline: validObservation.host_baseline,
      authority_granted: false,
      executable: false,
      lifecycle_mutated: false,
    })
    expectTypeOf(result).toEqualTypeOf<GovernedLocalRequirementsAuditPreflightV1Result>()
  })

  it.each(mismatchCases)(
    'returns exactly one failed check for legal mismatch %s',
    (field, actual, expected) => {
      const result = evaluateGovernedLocalRequirementsAuditPreflightV1(
        validPolicy,
        { ...validObservation, [field]: actual },
      )

      expect(result.preflight_passed).toBe(false)
      expect(result.checks.map((check) => check.field)).toEqual(
        expectedChecks.map((check) => check.field),
      )
      expect(result.checks.filter((check) => !check.pass)).toEqual([
        { field, expected, actual, pass: false },
      ])
      expect(result.authority_granted).toBe(false)
      expect(result.executable).toBe(false)
      expect(result.lifecycle_mutated).toBe(false)
    },
  )

  it.each([undefined, null, [], 'observation'])(
    'rejects malformed observation envelope %j',
    (observation) => {
      expect(() =>
        evaluateGovernedLocalRequirementsAuditPreflightV1(
          validPolicy,
          observation,
        ),
      ).toThrow()
    },
  )

  it('rejects missing and unknown observation fields', () => {
    const missing = clone(validObservation) as Record<string, unknown>
    delete missing.notes_calls

    expect(() =>
      evaluateGovernedLocalRequirementsAuditPreflightV1(validPolicy, missing),
    ).toThrow()
    expect(() =>
      evaluateGovernedLocalRequirementsAuditPreflightV1(validPolicy, {
        ...validObservation,
        authority_granted: true,
      }),
    ).toThrow()
  })

  it.each(numericFields.flatMap((field) => [
    [field, -1],
    [field, 1.5],
    [field, '0'],
    [field, false],
    [field, Number.NaN],
    [field, Number.POSITIVE_INFINITY],
    [field, Number.MAX_SAFE_INTEGER + 1],
  ] as const))('rejects malformed numeric observation %s=%j', (field, value) => {
    expect(() =>
      evaluateGovernedLocalRequirementsAuditPreflightV1(validPolicy, {
        ...validObservation,
        [field]: value,
      }),
    ).toThrow()
  })

  it.each([null, 0, -1, 1.5, '49487', Number.MAX_SAFE_INTEGER + 1])(
    'rejects malformed gateway pid %j',
    (gateway_pid) => {
      expect(() =>
        evaluateGovernedLocalRequirementsAuditPreflightV1(validPolicy, {
          ...validObservation,
          host_baseline: { ...validObservation.host_baseline, gateway_pid },
        }),
      ).toThrow()
    },
  )

  it.each([
    '',
    'a'.repeat(39),
    'a'.repeat(41),
    'a'.repeat(63),
    'a'.repeat(65),
    'A'.repeat(40),
    `sha256:${'a'.repeat(64)}`,
  ])('rejects malformed gateway source head %j', (gateway_source_head) => {
    expect(() =>
      evaluateGovernedLocalRequirementsAuditPreflightV1(validPolicy, {
        ...validObservation,
        host_baseline: {
          ...validObservation.host_baseline,
          gateway_source_head,
        },
      }),
    ).toThrow()
  })

  it.each([
    '',
    'a'.repeat(63),
    'a'.repeat(65),
    'A'.repeat(64),
    `sha256:${'a'.repeat(64)}`,
  ])('rejects malformed launchagent plist digest %j', (launchagent_plist_sha256) => {
    expect(() =>
      evaluateGovernedLocalRequirementsAuditPreflightV1(validPolicy, {
        ...validObservation,
        host_baseline: {
          ...validObservation.host_baseline,
          launchagent_plist_sha256,
        },
      }),
    ).toThrow()
  })

  it('preserves clean and dirty host baselines without claiming conformance', () => {
    for (const gateway_source_head of [
      validObservation.host_baseline.gateway_source_head,
      'a'.repeat(64),
    ]) {
      for (const gateway_worktree_porcelain of ['', ' M file one\n?? file-two\n']) {
        const host_baseline = {
          ...validObservation.host_baseline,
          gateway_source_head,
          gateway_worktree_porcelain,
        }
        const result = evaluateGovernedLocalRequirementsAuditPreflightV1(
          validPolicy,
          { ...validObservation, host_baseline },
        )

        expect(result.preflight_passed).toBe(true)
        expect(result.observed_host_baseline).toEqual(host_baseline)
        expect(result.checks.every((check) => check.pass)).toBe(true)
      }
    }
  })

  it('is deterministic and does not mutate or alias caller input', () => {
    const policy = clone(validPolicy)
    const observation = clone(validObservation)
    const policyBefore = clone(policy)
    const observationBefore = clone(observation)

    const first = evaluateGovernedLocalRequirementsAuditPreflightV1(
      policy,
      observation,
    )
    const second = evaluateGovernedLocalRequirementsAuditPreflightV1(
      policy,
      observation,
    )

    expect(first).toEqual(second)
    expect(policy).toEqual(policyBefore)
    expect(observation).toEqual(observationBefore)
    first.observed_host_baseline.gateway_worktree_porcelain = 'changed'
    expect(observation).toEqual(observationBefore)
  })

  it('rejects malformed policy instead of weakening policy ingress', () => {
    expect(() =>
      evaluateGovernedLocalRequirementsAuditPreflightV1(
        { ...validPolicy, network_adapter_calls: true },
        validObservation,
      ),
    ).toThrow()
  })

  it('never emits authority, execution, lifecycle, receipt, or ownership claims', () => {
    for (const observation of [
      validObservation,
      { ...validObservation, artifact_present: true },
    ]) {
      const result = evaluateGovernedLocalRequirementsAuditPreflightV1(
        validPolicy,
        observation,
      )
      expect(result).toMatchObject({
        authority_granted: false,
        executable: false,
        lifecycle_mutated: false,
      })
      expect(result).not.toHaveProperty('receipt')
      expect(result).not.toHaveProperty('artifact_written')
      expect(result).not.toHaveProperty('mission_created')
      expect(result).not.toHaveProperty('runtime_owned')
    }
  })
})

describe('evaluateGovernedLocalRequirementsAuditReadbackComparisonV1', () => {
  it('returns the exact ordered passing comparison envelope', () => {
    const result = evaluateGovernedLocalRequirementsAuditReadbackComparisonV1(
      validPolicy,
      validBaseline,
      validReadbackObservation,
    )

    expect(result).toEqual({
      comparison_passed: true,
      checks: expectedReadbackChecks,
      scope: 'host_invariance_and_forbidden_counter_comparison_only',
      authority_granted: false,
      executable: false,
      lifecycle_mutated: false,
    })
    expect(Object.keys(result)).toEqual([
      'comparison_passed',
      'checks',
      'scope',
      'authority_granted',
      'executable',
      'lifecycle_mutated',
    ])
    for (const check of result.checks) {
      expect(Object.keys(check)).toEqual(['field', 'expected', 'actual', 'pass'])
    }
    expectTypeOf(result).toEqualTypeOf<GovernedLocalRequirementsAuditReadbackComparisonV1Result>()
  })

  it('returns exactly one failed check for each legal mismatch', () => {
    const mismatchCases: Array<[string, string | number, string | number]> = [
      ['gateway_pid', validBaseline.gateway_pid + 1, validBaseline.gateway_pid],
      ['gateway_source_head', 'a'.repeat(40), validBaseline.gateway_source_head],
      ['gateway_worktree_porcelain', ' M changed\n', ''],
      ['launchagent_plist_sha256', 'b'.repeat(64), validBaseline.launchagent_plist_sha256],
      ...forbiddenCounterFields.map(
        (field) => [field, 1, 0] as [string, number, number],
      ),
    ]

    for (const [field, actual, expected] of mismatchCases) {
      const result = evaluateGovernedLocalRequirementsAuditReadbackComparisonV1(
        validPolicy,
        validBaseline,
        { ...validReadbackObservation, [field]: actual },
      )
      expect(result.comparison_passed).toBe(false)
      expect(result.checks.map((check) => check.field)).toEqual(
        expectedReadbackChecks.map((check) => check.field),
      )
      expect(result.checks.filter((check) => !check.pass)).toEqual([
        { field, expected, actual, pass: false },
      ])
    }
  })

  it('rejects malformed baseline and observation envelopes', () => {
    for (const malformed of [undefined, null, [], 'envelope']) {
      expect(() =>
        evaluateGovernedLocalRequirementsAuditReadbackComparisonV1(
          validPolicy,
          malformed,
          validReadbackObservation,
        ),
      ).toThrow()
      expect(() =>
        evaluateGovernedLocalRequirementsAuditReadbackComparisonV1(
          validPolicy,
          validBaseline,
          malformed,
        ),
      ).toThrow()
    }
    expect(() =>
      evaluateGovernedLocalRequirementsAuditReadbackComparisonV1(
        validPolicy,
        validBaseline,
        { host_baseline: validBaseline },
      ),
    ).toThrow()
  })

  it('rejects every missing or unknown baseline and observation field', () => {
    for (const field of baselineFields) {
      const missing = clone(validBaseline) as Record<string, unknown>
      delete missing[field]
      expect(() =>
        evaluateGovernedLocalRequirementsAuditReadbackComparisonV1(
          validPolicy,
          missing,
          validReadbackObservation,
        ),
      ).toThrow()
    }
    for (const field of readbackObservationFields) {
      const missing = clone(validReadbackObservation) as Record<string, unknown>
      delete missing[field]
      expect(() =>
        evaluateGovernedLocalRequirementsAuditReadbackComparisonV1(
          validPolicy,
          validBaseline,
          missing,
        ),
      ).toThrow()
    }
    expect(() =>
      evaluateGovernedLocalRequirementsAuditReadbackComparisonV1(
        validPolicy,
        { ...validBaseline, extra: true },
        validReadbackObservation,
      ),
    ).toThrow()
    expect(() =>
      evaluateGovernedLocalRequirementsAuditReadbackComparisonV1(
        validPolicy,
        validBaseline,
        { ...validReadbackObservation, extra: true },
      ),
    ).toThrow()
  })

  it('rejects malformed host scalars in baseline and observation', () => {
    const malformedHostCases: Array<[keyof typeof validBaseline, unknown]> = [
      ['gateway_pid', null],
      ['gateway_pid', 0],
      ['gateway_pid', -1],
      ['gateway_pid', 1.5],
      ['gateway_pid', '49487'],
      ['gateway_pid', false],
      ['gateway_pid', Number.NaN],
      ['gateway_pid', Number.POSITIVE_INFINITY],
      ['gateway_pid', Number.MAX_SAFE_INTEGER + 1],
      ['gateway_source_head', ''],
      ['gateway_source_head', 'a'.repeat(39)],
      ['gateway_source_head', 'a'.repeat(65)],
      ['gateway_source_head', 'A'.repeat(40)],
      ['gateway_source_head', `sha256:${'a'.repeat(64)}`],
      ['gateway_source_head', `${'a'.repeat(40)} `],
      ['launchagent_plist_sha256', ''],
      ['launchagent_plist_sha256', 'a'.repeat(63)],
      ['launchagent_plist_sha256', 'a'.repeat(65)],
      ['launchagent_plist_sha256', 'A'.repeat(64)],
      ['launchagent_plist_sha256', `sha256:${'a'.repeat(64)}`],
    ]

    for (const [field, value] of malformedHostCases) {
      expect(() =>
        evaluateGovernedLocalRequirementsAuditReadbackComparisonV1(
          validPolicy,
          { ...validBaseline, [field]: value },
          validReadbackObservation,
        ),
      ).toThrow()
      expect(() =>
        evaluateGovernedLocalRequirementsAuditReadbackComparisonV1(
          validPolicy,
          validBaseline,
          { ...validReadbackObservation, [field]: value },
        ),
      ).toThrow()
    }
  })

  it('rejects malformed forbidden counters instead of reporting mismatches', () => {
    const malformedValues = [
      -1,
      1.5,
      '0',
      false,
      Number.NaN,
      Number.POSITIVE_INFINITY,
      Number.MAX_SAFE_INTEGER + 1,
    ]
    for (const field of forbiddenCounterFields) {
      for (const value of malformedValues) {
        expect(() =>
          evaluateGovernedLocalRequirementsAuditReadbackComparisonV1(
            validPolicy,
            validBaseline,
            { ...validReadbackObservation, [field]: value },
          ),
        ).toThrow()
      }
    }
  })

  it('compares porcelain as an opaque byte-for-byte string', () => {
    const dirty = ' M file-one\n?? file-two\n'
    const passing = evaluateGovernedLocalRequirementsAuditReadbackComparisonV1(
      validPolicy,
      { ...validBaseline, gateway_worktree_porcelain: dirty },
      { ...validReadbackObservation, gateway_worktree_porcelain: dirty },
    )
    expect(passing.comparison_passed).toBe(true)

    for (const actual of [
      dirty.slice(0, -1),
      '?? file-two\n M file-one\n',
      ` ${dirty}`,
      `${dirty} `,
      dirty.replaceAll('\n', '\r\n'),
    ]) {
      const result = evaluateGovernedLocalRequirementsAuditReadbackComparisonV1(
        validPolicy,
        { ...validBaseline, gateway_worktree_porcelain: dirty },
        { ...validReadbackObservation, gateway_worktree_porcelain: actual },
      )
      expect(result.checks.filter((check) => !check.pass)).toEqual([
        {
          field: 'gateway_worktree_porcelain',
          expected: dirty,
          actual,
          pass: false,
        },
      ])
    }
  })

  it('is deterministic and does not mutate or alias caller inputs', () => {
    const policy = clone(validPolicy)
    const baseline = clone(validBaseline)
    const observation = clone(validReadbackObservation)
    const before = clone({ policy, baseline, observation })

    const first = evaluateGovernedLocalRequirementsAuditReadbackComparisonV1(
      policy,
      baseline,
      observation,
    )
    const second = evaluateGovernedLocalRequirementsAuditReadbackComparisonV1(
      policy,
      baseline,
      observation,
    )

    expect(first).toEqual(second)
    expect({ policy, baseline, observation }).toEqual(before)
    first.checks[0].actual = 1
    expect({ policy, baseline, observation }).toEqual(before)
  })

  it('rejects malformed policy and emits no freshness, rollback, or runtime claims', () => {
    expect(() =>
      evaluateGovernedLocalRequirementsAuditReadbackComparisonV1(
        { ...validPolicy, active_gateway_mutations: true },
        validBaseline,
        validReadbackObservation,
      ),
    ).toThrow()

    const result = evaluateGovernedLocalRequirementsAuditReadbackComparisonV1(
      validPolicy,
      validBaseline,
      validReadbackObservation,
    )
    for (const field of [
      'fresh',
      'freshness_verified',
      'readback_age_seconds',
      'rollback_verified',
      'restored',
      'receipt',
      'runtime_owned',
      'artifact_written',
      'preflight_passed',
      'observed_host_baseline',
    ]) {
      expect(result).not.toHaveProperty(field)
    }
    expect(result).toMatchObject({
      scope: 'host_invariance_and_forbidden_counter_comparison_only',
      authority_granted: false,
      executable: false,
      lifecycle_mutated: false,
    })
  })
})
