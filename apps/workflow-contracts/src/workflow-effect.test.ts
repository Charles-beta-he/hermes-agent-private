import { describe, expect, it } from 'vitest'

import {
  AdmittedJsonObjectV1Schema,
  AdmittedJsonValueV1Schema,
  EffectCommandV1Schema,
  EffectObservationV1Schema,
  EffectReceiptV1Schema,
  EffectTargetV1Schema,
  WorkflowEffectLedgerEventV1ObjectSchema,
  WorkflowEffectLedgerEventV1Schema,
  WorkflowExecutionEventV2Schema,
  deepFreezeV1,
  deriveEffectCommandRequestDigestV1,
  deriveEffectReceiptDigestV1,
  deriveWorkflowEffectLedgerEventIdV1,
  deriveWorkflowExecutionEventIdV2,
  loadSharedEffectContractCorpus,
  materializeSharedCorpusValue,
  normalizeAdmittedJsonV1,
  parseEffectCommandV1,
  parseEffectObservationV1,
  parseEffectReceiptV1,
  parseWorkflowEffectLedgerEventV1,
  parseWorkflowExecutionEventV2,
  canonicalDigestV1,
  canonicalJsonV1,
  toEffectCommandV1JsonSchema,
  toEffectObservationV1JsonSchema,
  toEffectReceiptV1JsonSchema,
  toWorkflowEffectLedgerEventV1JsonSchema,
  toWorkflowExecutionEventV2JsonSchema,
} from './workflow-effect'
import { WorkflowExecutionEventV1Schema } from './workflow-runtime'

const digest = `sha256:${'ab'.repeat(32)}`

describe('effect and execution-v2 contracts', () => {
  it('accepts canonical effect command/observation/receipt records', () => {
    const command = parseEffectCommandV1({
      schema_version: 'effect_command_v1',
      command_id: 'cmd-1',
      adapter_id: 'test.injected_adapter',
      operation: 'noop_apply',
      target: { target_kind: 'injected_callable', target_ref: 'target/alpha' },
      payload_digest: digest,
      idempotency_key: 'dispatch-cmd-1',
      issuer_id: 'issuer.local-test',
      policy_revision: 'policy-rev-1',
      config_revision: 'config-rev-1',
      side_effect_tier: 'local_write',
      compensation_ref: 'compensation:test.restore',
      reconcile_ref: 'reconcile:test.readback',
    })
    expect(deriveEffectCommandRequestDigestV1(command).startsWith('sha256:')).toBe(true)

    expect(
      parseEffectObservationV1({
        schema_version: 'effect_observation_v1',
        effect_id: 'effect-1',
        observation_id: 'obs-1',
        classification: 'unknown',
        fence: 1,
        observed_at: '2026-08-02T00:00:00Z',
        adapter_result_digest: null,
        readback: { present: false },
      }).classification,
    ).toBe('unknown')

    const receiptBody = {
      schema_version: 'effect_receipt_v1' as const,
      effect_id: 'effect-1',
      command_digest: digest,
      adapter_id: 'test.injected_adapter',
      operation: 'noop_apply',
      target: { target_kind: 'injected_callable', target_ref: 'target/alpha' },
      payload_digest: digest,
      authority_binding_digest: digest,
      idempotency_key: 'dispatch-cmd-1',
      fence: 1,
      preflight_readback: { ok: true },
      post_readback: { present: true },
      result_classification: 'applied' as const,
      compensation_ref: 'compensation:test.restore',
      reconcile_ref: 'reconcile:test.readback',
    }
    const receiptDigest = deriveEffectReceiptDigestV1(receiptBody)
    expect(
      parseEffectReceiptV1({ ...receiptBody, receipt_digest: receiptDigest }).receipt_digest,
    ).toBe(receiptDigest)
  })

  it('rejects unknown as terminal receipt classification', () => {
    expect(
      EffectReceiptV1Schema.safeParse({
        schema_version: 'effect_receipt_v1',
        effect_id: 'effect-1',
        command_digest: digest,
        adapter_id: 'test.injected_adapter',
        operation: 'noop_apply',
        target: { target_kind: 'injected_callable', target_ref: 'target/alpha' },
        payload_digest: digest,
        authority_binding_digest: digest,
        idempotency_key: 'dispatch-cmd-1',
        fence: 1,
        preflight_readback: { ok: true },
        post_readback: { present: false },
        result_classification: 'unknown',
        compensation_ref: 'compensation:test.restore',
        reconcile_ref: 'reconcile:test.readback',
        receipt_digest: digest,
      }).success,
    ).toBe(false)
  })

  it('keeps v1 closed while accepting additive v2 waiting_reconcile with derived ids', () => {
    expect(
      WorkflowExecutionEventV1Schema.safeParse({
        schema_version: 'workflow_execution_event_v2',
        run_id: 'run-1',
        sequence: 1,
        previous_event_id: null,
        idempotency_key: 'run-1:waiting',
        occurred_at: '2026-08-02T00:00:00Z',
        event_type: 'run_waiting_reconcile',
        payload: {},
        event_id: `wee_${'0'.repeat(64)}`,
      }).success,
    ).toBe(false)

    const v2Body = {
      schema_version: 'workflow_execution_event_v2' as const,
      run_id: 'run-1',
      sequence: 1,
      previous_event_id: null,
      idempotency_key: 'run-1:waiting',
      occurred_at: '2026-08-02T00:00:00Z',
      event_type: 'run_waiting_reconcile' as const,
      payload: { effect_id: 'effect-1', reason: 'unknown_observation' },
    }
    const v2 = parseWorkflowExecutionEventV2(v2Body)
    expect(v2.event_id).toBe(deriveWorkflowExecutionEventIdV2(v2Body))

    expect(() =>
      parseWorkflowExecutionEventV2({
        ...v2Body,
        event_id: `we2_${'0'.repeat(64)}`,
      }),
    ).toThrow(/event_id does not match canonical body/)

    const ledgerBody = {
      schema_version: 'workflow_effect_ledger_event_v1' as const,
      effect_id: 'effect-1',
      sequence: 1,
      previous_event_id: null,
      idempotency_key: 'effect-1:claimed',
      occurred_at: '2026-08-02T00:00:00Z',
      event_type: 'claimed' as const,
      payload: {
        command_digest: digest,
        target_digest: digest,
        owner: 'owner-a',
        request_digest: digest,
        idempotency_key: 'effect-cmd-1',
        adapter_id: 'test.injected_adapter',
        operation: 'noop_apply',
        fence: 1,
        payload_digest: digest,
        effect_id: 'effect-1',
      },
    }
    const ledger = parseWorkflowEffectLedgerEventV1(ledgerBody)
    expect(ledger.event_id).toBe(deriveWorkflowEffectLedgerEventIdV1(ledgerBody))
  })

  it('rejects v1 predecessors and accepts we2-only previous_event_id', () => {
    const base = {
      schema_version: 'workflow_execution_event_v2' as const,
      run_id: 'run-1',
      sequence: 2,
      idempotency_key: 'run-1:started',
      occurred_at: '2026-08-02T00:00:01Z',
      event_type: 'run_started' as const,
      payload: { ok: true },
    }
    expect(() =>
      parseWorkflowExecutionEventV2({
        ...base,
        previous_event_id: `wee_${'a'.repeat(64)}`,
      }),
    ).toThrow()
    const parsed = parseWorkflowExecutionEventV2({
      ...base,
      previous_event_id: `we2_${'a'.repeat(64)}`,
    })
    expect(parsed.previous_event_id?.startsWith('we2_')).toBe(true)
  })

  it('rejects timestamps without seconds and unsafe integers', () => {
    expect(
      EffectObservationV1Schema.safeParse({
        schema_version: 'effect_observation_v1',
        effect_id: 'effect-1',
        observation_id: 'obs-1',
        classification: 'unknown',
        fence: 1,
        observed_at: '2026-08-02T00:00Z',
        adapter_result_digest: null,
        readback: { present: false },
      }).success,
    ).toBe(false)
    expect(
      WorkflowExecutionEventV2Schema.safeParse({
        schema_version: 'workflow_execution_event_v2',
        run_id: 'run-1',
        sequence: Number.MAX_SAFE_INTEGER + 1,
        previous_event_id: null,
        idempotency_key: 'k',
        occurred_at: '2026-08-02T00:00:00Z',
        event_type: 'run_started',
        payload: {},
        event_id: `we2_${'a'.repeat(64)}`,
      }).success,
    ).toBe(false)
  })

  it('consumes shared serialized acceptance/rejection corpus', async () => {
    const { readFileSync } = await import('node:fs')
    const { dirname, join } = await import('node:path')
    const { fileURLToPath } = await import('node:url')
    const here = dirname(fileURLToPath(import.meta.url))
    const cases = loadSharedEffectContractCorpus()
    expect(cases.length).toBeGreaterThanOrEqual(44)
    const ids = cases.map((c) => c.id)
    expect(new Set(ids).size).toBe(ids.length)
    const baseline = JSON.parse(
      readFileSync(
        join(here, '../../../tests/workflow_kernel/fixtures/archives/effect-contract-shared-corpus-44-pre-4a5b.manifest.json'),
        'utf8',
      ),
    ) as { case_count: number; case_ids: string[] }
    expect(baseline.case_count).toBe(44)
    for (const id of baseline.case_ids) expect(ids).toContain(id)
    const relCases = cases.filter((c) => String(c.id).startsWith('rel_'))
    expect(relCases.length).toBe(ids.length - 44)
    for (const item of cases) {
      let ok = false
      try {
        const value = materializeSharedCorpusValue(item.value)
        if (item.kind === 'effect_command_v1') parseEffectCommandV1(value)
        else if (item.kind === 'effect_observation_v1') parseEffectObservationV1(value)
        else if (item.kind === 'effect_receipt_v1') parseEffectReceiptV1(value)
        else if (item.kind === 'workflow_effect_ledger_event_v1')
          parseWorkflowEffectLedgerEventV1(value)
        else parseWorkflowExecutionEventV2(value)
        ok = true
      } catch {
        ok = false
      }
      expect(ok, item.id).toBe(item.expect === 'accept')
    }
  })

  it('matches shared golden canonical bytes/digests and rejects non-integers', async () => {
    const { readFileSync } = await import('node:fs')
    const { fileURLToPath } = await import('node:url')
    const { dirname, join } = await import('node:path')
    const { canonicalJsonV1, canonicalDigestV1, materializeSharedCorpusValue } = await import(
      './workflow-effect'
    )
    const here = dirname(fileURLToPath(import.meta.url))
    const corpus = JSON.parse(
      readFileSync(join(here, '../../../tests/workflow_kernel/fixtures/effect-contract-shared-corpus.json'), 'utf8'),
    ) as {
      golden_canonical: Array<{ id: string; value: unknown; canonical_json: string; digest: string }>
      canonical_reject: Array<{ id: string; value: unknown }>
    }
    for (const gold of corpus.golden_canonical) {
      const value = materializeSharedCorpusValue(gold.value)
      expect(canonicalJsonV1(value), gold.id).toBe(gold.canonical_json)
      expect(canonicalDigestV1(value), gold.id).toBe(gold.digest)
    }
    for (const bad of corpus.canonical_reject) {
      const value = materializeSharedCorpusValue(bad.value)
      expect(() => canonicalJsonV1(value), bad.id).toThrow()
    }
    expect(() => canonicalJsonV1({ n: Number.NaN })).toThrow()
    expect(() => canonicalJsonV1({ n: Number.POSITIVE_INFINITY })).toThrow()
  })

  it('emits named draft-2020-12 schemas', () => {
    for (const schema of [
      toEffectCommandV1JsonSchema(),
      toEffectObservationV1JsonSchema(),
      toEffectReceiptV1JsonSchema(),
      toWorkflowEffectLedgerEventV1JsonSchema(),
      toWorkflowExecutionEventV2JsonSchema(),
    ]) {
      expect(String(schema.$id)).toContain('https://hermes-agent.local/schemas/')
      expect(schema.$schema).toBe('https://json-schema.org/draft/2020-12/schema')
    }
  })
})


describe('parity and immutability corrections', () => {
  it('rejects invalid calendar timestamps', () => {
    expect(() =>
      parseEffectObservationV1({
        schema_version: 'effect_observation_v1',
        effect_id: 'effect-1',
        observation_id: 'obs-1',
        classification: 'applied',
        fence: 1,
        observed_at: '2026-02-30T00:00:02Z',
        adapter_result_digest: null,
        readback: { ok: true },
      }),
    ).toThrow()
  })

  it('canonicalizes 1.0 like integer 1 and rejects non-integers', () => {
    expect(canonicalDigestV1({ n: 1.0 })).toBe(canonicalDigestV1({ n: 1 }))
    expect(canonicalDigestV1({ n: -0 })).toBe(canonicalDigestV1({ n: 0 }))
    expect(() => canonicalDigestV1({ n: 1e-7 })).toThrow()
    expect(() => canonicalDigestV1({ n: 0.10000000000000002 })).toThrow()
    expect(() => canonicalDigestV1({ n: 1e21 })).toThrow()
  })

  it('deep-freezes parsed command objects', () => {
    const parsed = parseEffectCommandV1({
      schema_version: 'effect_command_v1',
      command_id: 'cmd-1',
      adapter_id: 'test.injected_adapter',
      operation: 'noop_apply',
      target: { target_kind: 'injected_callable', target_ref: 'target/alpha' },
      payload_digest: 'sha256:' + 'ab'.repeat(32),
      idempotency_key: 'k1',
      issuer_id: 'issuer.local-test',
      policy_revision: 'policy-rev-1',
      config_revision: 'config-rev-1',
      side_effect_tier: 'local_write',
      compensation_ref: 'compensation:test.restore',
      reconcile_ref: 'reconcile:test.readback',
    })
    expect(() => {
      ;(parsed as { command_id: string }).command_id = 'mutated'
    }).toThrow()
  })

  it('generated positive integer schema rejects zero and negative', async () => {
    const { readFileSync } = await import('node:fs')
    const { fileURLToPath } = await import('node:url')
    const { dirname, join } = await import('node:path')
    const here = dirname(fileURLToPath(import.meta.url))
    const schema = JSON.parse(
      readFileSync(join(here, '../../../schemas/workflow-execution-event-v2.schema.json'), 'utf8'),
    )
    expect(schema.properties.sequence.minimum).toBe(1)
    expect(schema.properties.sequence.maximum).toBe(Number.MAX_SAFE_INTEGER)
  })

  it('accepts year 0099 and rejects invalid leap calendar dates', () => {
    expect(() =>
      parseEffectObservationV1({
        schema_version: 'effect_observation_v1',
        effect_id: 'effect-1',
        observation_id: 'obs-1',
        classification: 'unknown',
        fence: 1,
        observed_at: '0099-06-01T00:00:00Z',
        adapter_result_digest: null,
        readback: { present: false },
      }),
    ).not.toThrow()
    expect(() =>
      parseEffectObservationV1({
        schema_version: 'effect_observation_v1',
        effect_id: 'effect-1',
        observation_id: 'obs-1',
        classification: 'unknown',
        fence: 1,
        observed_at: '2024-02-30T00:00:00Z',
        adapter_result_digest: null,
        readback: { present: false },
      }),
    ).toThrow()
  })

  it('rejects non-JSON JS values and nested fractional admitted domain', async () => {
    const { normalizeAdmittedJsonV1, canonicalJsonV1, canonicalDigestV1 } = await import('./workflow-effect')
    expect(() => canonicalJsonV1([undefined])).toThrow()
    expect(() => canonicalJsonV1({ a: undefined })).toThrow()
    expect(() => canonicalJsonV1(1n)).toThrow()
    expect(() => canonicalJsonV1(Symbol('x'))).toThrow()
    expect(() => canonicalJsonV1(() => 1)).toThrow()
    expect(() => canonicalJsonV1(new Date('2026-01-01T00:00:00Z'))).toThrow()
    expect(() => canonicalJsonV1(new Map())).toThrow()
    expect(() => canonicalJsonV1(new Set())).toThrow()
    class Sample {
      n = 1
    }
    expect(() => canonicalJsonV1(new Sample())).toThrow()
    const sparse: unknown[] = []
    sparse[1] = 1
    expect(() => canonicalJsonV1(sparse)).toThrow()
    const withSymbol: Record<string | symbol, unknown> = { a: 1 }
    withSymbol[Symbol('hidden')] = 2
    expect(() => canonicalJsonV1(withSymbol)).toThrow()
    expect(() => normalizeAdmittedJsonV1({ n: 1.5 })).toThrow()
    expect(() =>
      parseEffectObservationV1({
        schema_version: 'effect_observation_v1',
        effect_id: 'effect-1',
        observation_id: 'obs-1',
        classification: 'applied',
        fence: 1,
        observed_at: '2026-08-02T00:00:00Z',
        adapter_result_digest: null,
        readback: { nested: { x: 1.5 } },
      }),
    ).toThrow()
    const nested = parseEffectObservationV1({
      schema_version: 'effect_observation_v1',
      effect_id: 'effect-1',
      observation_id: 'obs-1',
      classification: 'applied',
      fence: 1.0,
      observed_at: '2026-08-02T00:00:00Z',
      adapter_result_digest: null,
      readback: { items: [['before'], { deep: [1] }] },
    })
    expect(nested.fence).toBe(1)
    expect(nested.readback).toEqual({ items: [['before'], { deep: [1] }] })
    // symbol key must not be silently dropped by digest/ID path
    expect(() => canonicalDigestV1(withSymbol)).toThrow()
  })

  it('event_id presence matrix for ledger and v2 including valid supplied ids', () => {
    const ledgerBody = {
      schema_version: 'workflow_effect_ledger_event_v1' as const,
      effect_id: 'effect-1',
      sequence: 1,
      previous_event_id: null,
      idempotency_key: 'effect-1:claimed',
      occurred_at: '2026-08-02T00:00:00Z',
      event_type: 'claimed' as const,
      payload: {
        command_digest: 'sha256:' + 'ab'.repeat(32),
        target_digest: 'sha256:' + 'ab'.repeat(32),
        owner: 'owner-a',
        request_digest: 'sha256:' + 'ab'.repeat(32),
        idempotency_key: 'effect-cmd-1',
        adapter_id: 'test.injected_adapter',
        operation: 'noop_apply',
        fence: 1,
        payload_digest: 'sha256:' + 'ab'.repeat(32),
        effect_id: 'effect-1',
      },
    }
    const omitted = parseWorkflowEffectLedgerEventV1(ledgerBody)
    expect(omitted.event_id.startsWith('wel_')).toBe(true)
    expect(
      parseWorkflowEffectLedgerEventV1({ ...ledgerBody, event_id: omitted.event_id }).event_id,
    ).toBe(omitted.event_id)
    expect(() => parseWorkflowEffectLedgerEventV1({ ...ledgerBody, event_id: null })).toThrow()
    expect(() => parseWorkflowEffectLedgerEventV1({ ...ledgerBody, event_id: '' })).toThrow()
    expect(() =>
      parseWorkflowEffectLedgerEventV1({ ...ledgerBody, event_id: 'wel_nothex' }),
    ).toThrow()
    expect(() =>
      parseWorkflowEffectLedgerEventV1({ ...ledgerBody, event_id: 'we2_' + 'a'.repeat(64) }),
    ).toThrow()
    expect(() =>
      parseWorkflowEffectLedgerEventV1({ ...ledgerBody, event_id: 'wel_' + '0'.repeat(64) }),
    ).toThrow()

    const v2Body = {
      schema_version: 'workflow_execution_event_v2' as const,
      run_id: 'run-1',
      sequence: 1,
      previous_event_id: null,
      idempotency_key: 'run-1:waiting',
      occurred_at: '2026-08-02T00:00:00Z',
      event_type: 'run_waiting_reconcile' as const,
      payload: { effect_id: 'effect-1', reason: 'unknown_observation' },
    }
    const v2 = parseWorkflowExecutionEventV2(v2Body)
    expect(parseWorkflowExecutionEventV2({ ...v2Body, event_id: v2.event_id }).event_id).toBe(
      v2.event_id,
    )
    expect(() => parseWorkflowExecutionEventV2({ ...v2Body, event_id: null })).toThrow()
    expect(() => parseWorkflowExecutionEventV2({ ...v2Body, event_id: '' })).toThrow()
    expect(() => parseWorkflowExecutionEventV2({ ...v2Body, event_id: 'we2_zzz' })).toThrow()
    expect(() =>
      parseWorkflowExecutionEventV2({ ...v2Body, event_id: 'wel_' + 'a'.repeat(64) }),
    ).toThrow()
    expect(() =>
      parseWorkflowExecutionEventV2({ ...v2Body, event_id: 'we2_' + '0'.repeat(64) }),
    ).toThrow()
  })

  it('4A.5A.2 rejects nonstandard length descriptors, frozen arrays, proxies; private freezer', async () => {
    const mod = await import('./workflow-effect')
    const { canonicalJsonV1, canonicalDigestV1, deepFreezeV1 } = mod

    const nonWritableLength = ['before']
    Object.defineProperty(nonWritableLength, 'length', { writable: false })
    for (const fn of [
      () => canonicalJsonV1(nonWritableLength),
      () => canonicalDigestV1(nonWritableLength),
      () => deepFreezeV1(nonWritableLength),
    ]) {
      expect(fn).toThrow(/non-standard length descriptor/i)
    }

    const frozenEmpty: unknown[] = []
    Object.freeze(frozenEmpty)
    expect(() => canonicalJsonV1(frozenEmpty)).toThrow(/non-standard length descriptor/i)
    expect(() => deepFreezeV1(frozenEmpty)).toThrow(/non-standard length descriptor/i)

    const frozenNonEmpty = ['x']
    Object.freeze(frozenNonEmpty)
    expect(() => canonicalJsonV1(frozenNonEmpty)).toThrow(/non-standard length descriptor|array indices/i)
    expect(() => deepFreezeV1(frozenNonEmpty)).toThrow(/non-standard length descriptor|array indices/i)

    let lengthGets = 0
    const target = ['before']
    const proxy = new Proxy(target, {
      get(t, p, r) {
        if (p === 'length') lengthGets += 1
        return Reflect.get(t, p, r)
      },
    })
    expect(() => canonicalJsonV1(proxy)).toThrow(/canonical JSON rejects proxy values/)
    expect(lengthGets).toBe(0)
    expect(() => deepFreezeV1(proxy)).toThrow(/canonical JSON rejects proxy values/)
    expect(lengthGets).toBe(0)

    // deepFreezeNormalizedAdmittedJsonV1 must not be a public export (no normalized-only bypass).
    expect(Object.prototype.hasOwnProperty.call(mod, 'deepFreezeNormalizedAdmittedJsonV1')).toBe(false)
    expect((mod as Record<string, unknown>).deepFreezeNormalizedAdmittedJsonV1).toBeUndefined()

    // Public deepFreezeV1 validates original then freezes a normalized copy.
    const ok = deepFreezeV1({ a: [1, { b: 2 }] }) as { a: unknown[] }
    expect(Object.isFrozen(ok)).toBe(true)
    expect(Object.isFrozen(ok.a)).toBe(true)
  })

  it('4A.5A.2 Zod/generated schemas reject lone surrogates in values and keys', async () => {
    const {
      AdmittedJsonValueV1Schema,
      EffectCommandV1Schema,
      EffectObservationV1Schema,
      EffectReceiptV1Schema,
      WorkflowEffectLedgerEventV1Schema,
      WorkflowExecutionEventV2Schema,
      toEffectCommandV1JsonSchema,
      toEffectObservationV1JsonSchema,
      toEffectReceiptV1JsonSchema,
      toWorkflowEffectLedgerEventV1JsonSchema,
      toWorkflowExecutionEventV2JsonSchema,
    } = await import('./workflow-effect')

    const loneHigh = String.fromCharCode(0xd800)
    const loneLow = String.fromCharCode(0xdc00)
    const schemas = [
      ['AdmittedJsonValueV1Schema', AdmittedJsonValueV1Schema],
      ['EffectObservationV1Schema', EffectObservationV1Schema],
      ['EffectReceiptV1Schema', EffectReceiptV1Schema],
      ['WorkflowEffectLedgerEventV1Schema', WorkflowEffectLedgerEventV1Schema],
      ['WorkflowExecutionEventV2Schema', WorkflowExecutionEventV2Schema],
      ['EffectCommandV1Schema', EffectCommandV1Schema],
    ] as const

    expect(AdmittedJsonValueV1Schema.safeParse({ s: loneHigh }).success).toBe(false)
    expect(AdmittedJsonValueV1Schema.safeParse({ s: loneLow }).success).toBe(false)
    expect(AdmittedJsonValueV1Schema.safeParse({ [loneHigh]: 1 }).success).toBe(false)
    expect(AdmittedJsonValueV1Schema.safeParse({ [loneLow]: 1 }).success).toBe(false)
    expect(AdmittedJsonValueV1Schema.safeParse({ emoji: '😀', plane: '\u{10000}' }).success).toBe(true)

    const DIGEST = 'sha256:' + 'ab'.repeat(32)
    const obsBase = {
      schema_version: 'effect_observation_v1' as const,
      effect_id: 'effect-1',
      observation_id: 'obs-1',
      classification: 'applied' as const,
      fence: 1,
      observed_at: '2026-08-02T00:00:00Z',
      adapter_result_digest: null,
      readback: { present: true },
    }
    const cmdBase = {
      schema_version: 'effect_command_v1' as const,
      command_id: 'cmd-1',
      adapter_id: 'test.injected_adapter',
      operation: 'noop_apply',
      target: { target_kind: 'injected_callable', target_ref: 'target/alpha' },
      payload_digest: DIGEST,
      idempotency_key: 'effect-cmd-1',
      issuer_id: 'issuer',
      policy_revision: 'pol-1',
      config_revision: 'cfg-1',
      side_effect_tier: 'local_write' as const,
      compensation_ref: 'compensation:test.restore',
      reconcile_ref: 'reconcile:test.readback',
    }
    const receiptBase = {
      schema_version: 'effect_receipt_v1' as const,
      effect_id: 'effect-1',
      command_digest: DIGEST,
      adapter_id: 'test.injected_adapter',
      operation: 'noop_apply',
      target: { target_kind: 'injected_callable', target_ref: 'target/alpha' },
      payload_digest: DIGEST,
      authority_binding_digest: DIGEST,
      idempotency_key: 'effect-cmd-1',
      fence: 1,
      preflight_readback: { ok: true },
      post_readback: { present: true },
      result_classification: 'applied' as const,
      compensation_ref: 'compensation:test.restore',
      reconcile_ref: 'reconcile:test.readback',
      receipt_digest: DIGEST,
    }
    const ledgerBase = {
      schema_version: 'workflow_effect_ledger_event_v1' as const,
      event_id: 'wel_' + 'a'.repeat(64),
      effect_id: 'effect-1',
      sequence: 2,
      previous_event_id: 'wel_' + 'b'.repeat(64),
      idempotency_key: 'effect-1:intent',
      occurred_at: '2026-08-02T00:00:01Z',
      event_type: 'intent_recorded' as const,
      payload: {
        fence: 1,
        command_digest: DIGEST,
        request_digest: DIGEST,
        target_digest: DIGEST,
        authority_binding_digest: DIGEST,
        preflight_readback: { ok: true },
        preflight_readback_digest: DIGEST,
        adapter_id: 'test.injected_adapter',
        operation: 'noop_apply',
        payload_digest: DIGEST,
        idempotency_key: 'effect-cmd-1',
        effect_id: 'effect-1',
      },
    }
    const v2Base = {
      schema_version: 'workflow_execution_event_v2' as const,
      event_id: 'we2_' + 'a'.repeat(64),
      run_id: 'run-1',
      sequence: 1,
      previous_event_id: null,
      idempotency_key: 'run-1:waiting',
      occurred_at: '2026-08-02T00:00:00Z',
      event_type: 'run_waiting_reconcile' as const,
      payload: { effect_id: 'effect-1' },
    }

    for (const [name, schema] of schemas) {
      const samples: unknown[] = []
      if (name === 'AdmittedJsonValueV1Schema') {
        samples.push({ s: loneHigh }, { s: loneLow }, { [loneHigh]: 1 }, { [loneLow]: 1 })
      } else if (name === 'EffectObservationV1Schema') {
        samples.push(
          { ...obsBase, readback: { s: loneHigh } },
          { ...obsBase, readback: { [loneHigh]: 1 } },
        )
      } else if (name === 'EffectReceiptV1Schema') {
        samples.push(
          { ...receiptBase, post_readback: { s: loneHigh } },
          { ...receiptBase, preflight_readback: { [loneLow]: 1 } },
        )
      } else if (name === 'EffectCommandV1Schema') {
        samples.push(
          { ...cmdBase, target: { ...cmdBase.target, target_ref: loneHigh } },
          { ...cmdBase, [loneHigh]: 1 },
        )
      } else if (name === 'WorkflowEffectLedgerEventV1Schema') {
        samples.push({
          ...ledgerBase,
          payload: { ...ledgerBase.payload, preflight_readback: { s: loneHigh } },
        })
      } else if (name === 'WorkflowExecutionEventV2Schema') {
        samples.push({ ...v2Base, payload: { effect_id: 'effect-1', s: loneLow } })
      }
      for (const sample of samples) {
        expect(schema.safeParse(sample).success, `${name}`).toBe(false)
      }
    }

    for (const fn of [
      toEffectCommandV1JsonSchema,
      toEffectObservationV1JsonSchema,
      toEffectReceiptV1JsonSchema,
      toWorkflowEffectLedgerEventV1JsonSchema,
      toWorkflowExecutionEventV2JsonSchema,
    ]) {
      const blob = JSON.stringify(fn())
      expect(blob).toMatch(/D800|d800/)
    }
  })

  it('consumes shared ts_non_json_reject table across parser/id/digest/freeze boundaries (non-vacuous)', async () => {
    const { readFileSync } = await import('node:fs')
    const { fileURLToPath } = await import('node:url')
    const { dirname, join } = await import('node:path')
    const nodeUtil = await import('node:util')
    const nodeTypes = nodeUtil.types
    const {
      canonicalJsonV1,
      canonicalDigestV1,
      deepFreezeV1,
      deriveEffectCommandRequestDigestV1,
      deriveEffectReceiptDigestV1,
      deriveWorkflowEffectLedgerEventIdV1,
      deriveWorkflowExecutionEventIdV2,
      parseEffectCommandV1,
      parseEffectObservationV1,
      parseEffectReceiptV1,
      parseWorkflowEffectLedgerEventV1,
      parseWorkflowExecutionEventV2,
      AdmittedJsonValueV1Schema,
      AdmittedJsonObjectV1Schema,
      EffectTargetV1Schema,
      EffectCommandV1Schema,
      EffectObservationV1Schema,
      EffectReceiptV1Schema,
      WorkflowEffectLedgerEventV1ObjectSchema,
      WorkflowEffectLedgerEventV1Schema,
      WorkflowExecutionEventV2Schema,
      normalizeAdmittedJsonV1,
      materializeSharedCorpusValue,
      toEffectCommandV1JsonSchema,
      toEffectObservationV1JsonSchema,
      toEffectReceiptV1JsonSchema,
      toWorkflowEffectLedgerEventV1JsonSchema,
      toWorkflowExecutionEventV2JsonSchema,
    } = await import('./workflow-effect')
    const here = dirname(fileURLToPath(import.meta.url))
    const corpusPath = join(here, '../../../tests/workflow_kernel/fixtures/effect-contract-shared-corpus.json')
    const corpus = JSON.parse(readFileSync(corpusPath, 'utf8')) as {
      ts_non_json_reject: Array<{ id: string; kind: string }>
      schema_boundary_vectors: Array<{ id: string; path: string; expect: string; value: unknown }>
      cases: Array<{ id: string }>
    }
    expect(corpus.cases.length).toBeGreaterThanOrEqual(44)
    expect(new Set(corpus.cases.map((c) => c.id)).size).toBe(corpus.cases.length)

    type TrapCounts = {
      get: number
      getPrototypeOf: number
      ownKeys: number
      getOwnPropertyDescriptor: number
      apply: number
      set: number
      has: number
      construct: number
    }
    const zeroTraps = (): TrapCounts => ({
      get: 0,
      getPrototypeOf: 0,
      ownKeys: 0,
      getOwnPropertyDescriptor: 0,
      apply: 0,
      set: 0,
      has: 0,
      construct: 0,
    })
    const sumTraps = (t: TrapCounts) => Object.values(t).reduce((a, b) => a + b, 0)
    const revokedTrapMap = new WeakMap<object, TrapCounts>()

    const getterHits = { count: 0 }
    class ArraySubclass extends Array {}

    const makeAccessorArray = () => {
      const a: unknown[] = []
      Object.defineProperty(a, '0', {
        get() {
          getterHits.count += 1
          return 'before'
        },
        enumerable: true,
        configurable: true,
      })
      Object.defineProperty(a, 'length', { value: 1, writable: true, enumerable: false, configurable: false })
      return a
    }

    const wrapProxy = (target: object, traps: TrapCounts) =>
      new Proxy(target, {
        get(t, p, r) {
          traps.get += 1
          if (p === 'length' || p === '0' || p === 'a') getterHits.count += 1
          return Reflect.get(t, p, r)
        },
        getPrototypeOf(t) {
          traps.getPrototypeOf += 1
          return Reflect.getPrototypeOf(t)
        },
        ownKeys(t) {
          traps.ownKeys += 1
          return Reflect.ownKeys(t)
        },
        getOwnPropertyDescriptor(t, p) {
          traps.getOwnPropertyDescriptor += 1
          return Reflect.getOwnPropertyDescriptor(t, p)
        },
        apply(t, thisArg, args) {
          traps.apply += 1
          return Reflect.apply(t as never, thisArg, args)
        },
        set(t, p, v, r) {
          traps.set += 1
          return Reflect.set(t, p, v, r)
        },
        has(t, p) {
          traps.has += 1
          return Reflect.has(t, p)
        },
        construct(t, args, n) {
          traps.construct += 1
          return Reflect.construct(t as never, args, n)
        },
      })

    const samples: Record<string, () => unknown> = {
      undefined: () => undefined,
      bigint: () => 1n,
      symbol: () => Symbol('x'),
      function: () => () => 1,
      date: () => new Date('2026-01-01T00:00:00Z'),
      map: () => new Map(),
      set: () => new Set(),
      class: () =>
        new (class X {
          n = 1
        })(),
      sparse: () => {
        const a: unknown[] = []
        a[1] = 1
        return a
      },
      symbol_key: () => {
        const o: Record<string | symbol, unknown> = { a: 1 }
        o[Symbol('h')] = 2
        return o
      },
      non_enumerable: () => {
        const o: Record<string, unknown> = { a: 1 }
        Object.defineProperty(o, 'hidden', { value: 2, enumerable: false })
        return o
      },
      accessor: () => {
        const o: Record<string, unknown> = { a: 1 }
        Object.defineProperty(o, 'x', {
          get() {
            getterHits.count += 1
            return 2
          },
          enumerable: true,
        })
        return o
      },
      array_symbol_enumerable: () => {
        const a: unknown[] = ['before']
        Object.defineProperty(a, Symbol('hidden'), { value: 2, enumerable: true })
        return a
      },
      array_symbol_nonenumerable: () => {
        const a: unknown[] = ['before']
        Object.defineProperty(a, Symbol('hidden'), { value: 2, enumerable: false })
        return a
      },
      array_extra_string_enumerable: () => {
        const a: unknown[] = ['before']
        Object.defineProperty(a, 'extra', {
          value: 2,
          enumerable: true,
          writable: true,
          configurable: true,
        })
        return a
      },
      array_extra_string_nonenumerable: () => {
        const a: unknown[] = ['before']
        Object.defineProperty(a, 'extra', {
          value: 2,
          enumerable: false,
          writable: true,
          configurable: true,
        })
        return a
      },
      array_getter_index: () => makeAccessorArray(),
      array_custom_prototype: () => {
        const a = ['before']
        Object.setPrototypeOf(a, Object.prototype)
        return a
      },
      array_subclass: () => ArraySubclass.from(['before']),
      array_index_non_enumerable: () => {
        const a: unknown[] = []
        Object.defineProperty(a, '0', {
          value: 'before',
          enumerable: false,
          writable: true,
          configurable: true,
        })
        Object.defineProperty(a, 'length', { value: 1, writable: true, enumerable: false, configurable: false })
        return a
      },
      array_index_non_writable: () => {
        const a: unknown[] = []
        Object.defineProperty(a, '0', {
          value: 'before',
          enumerable: true,
          writable: false,
          configurable: true,
        })
        Object.defineProperty(a, 'length', { value: 1, writable: true, enumerable: false, configurable: false })
        return a
      },
      array_index_non_configurable: () => {
        const a: unknown[] = []
        Object.defineProperty(a, '0', {
          value: 'before',
          enumerable: true,
          writable: true,
          configurable: false,
        })
        Object.defineProperty(a, 'length', { value: 1, writable: true, enumerable: false, configurable: false })
        return a
      },
      array_nested_getter: () => ({ items: [makeAccessorArray()] }),
      array_length_non_writable: () => {
        const a = ['before']
        Object.defineProperty(a, 'length', { writable: false })
        return a
      },
      array_frozen_empty: () => {
        const a: unknown[] = []
        Object.freeze(a)
        return a
      },
      array_frozen_nonempty: () => {
        const a = ['before']
        Object.freeze(a)
        return a
      },
      array_transparent_proxy: () => {
        const traps = zeroTraps()
        return wrapProxy(['before'], traps)
      },
      object_transparent_proxy: () => {
        const traps = zeroTraps()
        return wrapProxy({ a: 1 }, traps)
      },
      revoked_array_proxy: () => {
        const traps = zeroTraps()
        // Attach the complete instrumented handler BEFORE Proxy.revocable, then revoke.
        const handler: ProxyHandler<object> = {
          get(t, p, r) {
            traps.get += 1
            getterHits.count += 1
            return Reflect.get(t, p, r)
          },
          getPrototypeOf(t) {
            traps.getPrototypeOf += 1
            getterHits.count += 1
            return Reflect.getPrototypeOf(t)
          },
          ownKeys(t) {
            traps.ownKeys += 1
            return Reflect.ownKeys(t)
          },
          getOwnPropertyDescriptor(t, p) {
            traps.getOwnPropertyDescriptor += 1
            return Reflect.getOwnPropertyDescriptor(t, p)
          },
          apply(t, thisArg, args) {
            traps.apply += 1
            return Reflect.apply(t as never, thisArg, args)
          },
          set(t, p, v, r) {
            traps.set += 1
            return Reflect.set(t, p, v, r)
          },
          has(t, p) {
            traps.has += 1
            return Reflect.has(t, p)
          },
          construct(t, args, n) {
            traps.construct += 1
            return Reflect.construct(t as never, args, n)
          },
        }
        const rev = Proxy.revocable(['before'], handler)
        rev.revoke()
        // counters remain attached via WeakMap (cannot set props on revoked proxy)
        revokedTrapMap.set(rev.proxy as object, traps)
        return rev.proxy
      },
      revoked_object_proxy: () => {
        const traps = zeroTraps()
        const handler: ProxyHandler<object> = {
          get(t, p, r) {
            traps.get += 1
            getterHits.count += 1
            return Reflect.get(t, p, r)
          },
          getPrototypeOf(t) {
            traps.getPrototypeOf += 1
            getterHits.count += 1
            return Reflect.getPrototypeOf(t)
          },
          ownKeys(t) {
            traps.ownKeys += 1
            return Reflect.ownKeys(t)
          },
          getOwnPropertyDescriptor(t, p) {
            traps.getOwnPropertyDescriptor += 1
            return Reflect.getOwnPropertyDescriptor(t, p)
          },
          apply(t, thisArg, args) {
            traps.apply += 1
            return Reflect.apply(t as never, thisArg, args)
          },
          set(t, p, v, r) {
            traps.set += 1
            return Reflect.set(t, p, v, r)
          },
          has(t, p) {
            traps.has += 1
            return Reflect.has(t, p)
          },
          construct(t, args, n) {
            traps.construct += 1
            return Reflect.construct(t as never, args, n)
          },
        }
        const rev = Proxy.revocable({ a: 1 }, handler)
        rev.revoke()
        revokedTrapMap.set(rev.proxy as object, traps)
        return rev.proxy
      },
    }

    const DIGEST = 'sha256:' + 'ab'.repeat(32)
    const cleanTarget = { target_kind: 'injected_callable', target_ref: 'target/alpha' }
    const commandBase = {
      schema_version: 'effect_command_v1' as const,
      command_id: 'cmd-1',
      adapter_id: 'test.injected_adapter',
      operation: 'noop_apply',
      target: cleanTarget,
      payload_digest: DIGEST,
      idempotency_key: 'effect-cmd-1',
      issuer_id: 'issuer',
      policy_revision: 'pol-1',
      config_revision: 'cfg-1',
      side_effect_tier: 'local_write' as const,
      compensation_ref: 'compensation:test.restore',
      reconcile_ref: 'reconcile:test.readback',
    }
    const obsBase = {
      schema_version: 'effect_observation_v1' as const,
      effect_id: 'effect-1',
      observation_id: 'obs-1',
      classification: 'applied' as const,
      fence: 1,
      observed_at: '2026-08-02T00:00:00Z',
      adapter_result_digest: null as string | null,
      readback: { present: true as const },
    }
    const receiptCleanPost = { present: true as const, marker: 'clean' }
    const receiptBaseNoDigest = {
      schema_version: 'effect_receipt_v1' as const,
      effect_id: 'effect-1',
      command_digest: DIGEST,
      adapter_id: 'test.injected_adapter',
      operation: 'noop_apply',
      target: cleanTarget,
      payload_digest: DIGEST,
      authority_binding_digest: DIGEST,
      idempotency_key: 'effect-cmd-1',
      fence: 1,
      preflight_readback: { ok: true as const },
      post_readback: receiptCleanPost,
      result_classification: 'applied' as const,
      compensation_ref: 'compensation:test.restore',
      reconcile_ref: 'reconcile:test.readback',
    }
    const cleanReceiptDigest = deriveEffectReceiptDigestV1(receiptBaseNoDigest)
    const receiptBase = { ...receiptBaseNoDigest, receipt_digest: cleanReceiptDigest }

    const intentCleanPreflight = { ok: true as const, marker: 'clean-intent' }
    const intentLedgerCleanNoId = {
      schema_version: 'workflow_effect_ledger_event_v1' as const,
      effect_id: 'effect-1',
      sequence: 2,
      previous_event_id: 'wel_' + 'a'.repeat(64),
      idempotency_key: 'effect-1:intent',
      occurred_at: '2026-08-02T00:00:01Z',
      event_type: 'intent_recorded' as const,
      payload: {
        fence: 1,
        command_digest: DIGEST,
        request_digest: DIGEST,
        target_digest: DIGEST,
        authority_binding_digest: DIGEST,
        preflight_readback: intentCleanPreflight,
        preflight_readback_digest: canonicalDigestV1(intentCleanPreflight),
        adapter_id: 'test.injected_adapter',
        operation: 'noop_apply',
        payload_digest: DIGEST,
        idempotency_key: 'effect-cmd-1',
        effect_id: 'effect-1',
      },
    }
    const cleanLedgerEventId = deriveWorkflowEffectLedgerEventIdV1(intentLedgerCleanNoId)
    const intentLedgerBase = { ...intentLedgerCleanNoId, event_id: cleanLedgerEventId }

    const v2CleanPayload = { effect_id: 'effect-1', reason: 'unknown_observation' }
    const v2CleanNoId = {
      schema_version: 'workflow_execution_event_v2' as const,
      run_id: 'run-1',
      sequence: 1,
      previous_event_id: null as string | null,
      idempotency_key: 'run-1:waiting',
      occurred_at: '2026-08-02T00:00:00Z',
      event_type: 'run_waiting_reconcile' as const,
      payload: v2CleanPayload,
    }
    const cleanV2EventId = deriveWorkflowExecutionEventIdV2(v2CleanNoId)
    const v2Base = { ...v2CleanNoId, event_id: cleanV2EventId }

    const PROXY_ERROR = 'canonical JSON rejects proxy values'
    const exactAdmissionMessageByKind: Record<string, string> = {
      undefined: 'canonical JSON rejects undefined',
      bigint: 'canonical JSON rejects non-JSON type: bigint',
      symbol: 'canonical JSON rejects non-JSON type: symbol',
      function: 'canonical JSON rejects non-JSON type: function',
      date: 'canonical JSON rejects non-plain object types',
      map: 'canonical JSON rejects non-plain object types',
      set: 'canonical JSON rejects non-plain object types',
      class: 'canonical JSON rejects non-plain object types',
      sparse: 'canonical JSON rejects arrays with unexpected own keys',
      symbol_key: 'canonical JSON rejects symbol keys',
      non_enumerable: 'canonical JSON rejects non-enumerable keys',
      accessor: 'canonical JSON rejects accessor properties',
      array_symbol_enumerable: 'canonical JSON rejects arrays with unexpected own keys',
      array_symbol_nonenumerable: 'canonical JSON rejects arrays with unexpected own keys',
      array_extra_string_enumerable: 'canonical JSON rejects arrays with unexpected own keys',
      array_extra_string_nonenumerable: 'canonical JSON rejects arrays with unexpected own keys',
      array_getter_index: 'canonical JSON rejects accessor array indices',
      array_custom_prototype: 'canonical JSON rejects array subclass or custom prototype',
      array_subclass: 'canonical JSON rejects array subclass or custom prototype',
      array_index_non_enumerable:
        'canonical JSON requires array indices to be enumerable writable configurable data properties',
      array_index_non_writable:
        'canonical JSON requires array indices to be enumerable writable configurable data properties',
      array_index_non_configurable:
        'canonical JSON requires array indices to be enumerable writable configurable data properties',
      array_nested_getter: 'canonical JSON rejects accessor array indices',
      array_length_non_writable: 'canonical JSON rejects arrays with non-standard length descriptor',
      array_frozen_empty: 'canonical JSON rejects arrays with non-standard length descriptor',
      array_frozen_nonempty: 'canonical JSON rejects arrays with non-standard length descriptor',
      array_transparent_proxy: PROXY_ERROR,
      object_transparent_proxy: PROXY_ERROR,
      revoked_array_proxy: PROXY_ERROR,
      revoked_object_proxy: PROXY_ERROR,
    }

    const nestInAdmittedObject = (sample: unknown, kind: string): Record<string, unknown> => {
      if (kind === 'array_nested_getter') return sample as Record<string, unknown>
      if (kind.startsWith('array_') || kind === 'sparse' || kind.endsWith('_proxy')) {
        return { items: sample }
      }
      if (kind === 'class' || kind === 'date' || kind === 'map' || kind === 'set') {
        return { nest: sample }
      }
      if (kind === 'symbol_key' || kind === 'non_enumerable' || kind === 'accessor') {
        return sample as Record<string, unknown>
      }
      if (kind === 'undefined' || kind === 'bigint' || kind === 'symbol' || kind === 'function') {
        return { nest: sample }
      }
      return { nest: sample }
    }

    const items = [
      ...corpus.ts_non_json_reject,
      { id: 'array_length_non_writable', kind: 'array_length_non_writable' },
      { id: 'array_frozen_empty', kind: 'array_frozen_empty' },
      { id: 'array_frozen_nonempty', kind: 'array_frozen_nonempty' },
      { id: 'array_transparent_proxy', kind: 'array_transparent_proxy' },
      { id: 'object_transparent_proxy', kind: 'object_transparent_proxy' },
      { id: 'revoked_array_proxy', kind: 'revoked_array_proxy' },
      { id: 'revoked_object_proxy', kind: 'revoked_object_proxy' },
    ]

    const messageForKind = (
      kind: string,
      placement: 'root' | 'as_object_property' = 'root',
    ): string => {
      if (kind === 'undefined' && placement === 'as_object_property') {
        return 'canonical JSON rejects undefined object properties'
      }
      const expected = exactAdmissionMessageByKind[kind]
      expect(expected, `${kind}:exact-message-mapped`).toBeTypeOf('string')
      return expected
    }

    const expectAdmissionThrow = (
      fn: () => unknown,
      label: string,
      kind: string,
      placement: 'root' | 'as_object_property' = 'root',
    ) => {
      const before = getterHits.count
      const expected = messageForKind(kind, placement)
      try {
        fn()
        expect.fail(`${label}: expected throw`)
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        expect(msg, label).toBe(expected)
        // Must fail before Zod semantic "expected string, received array" style messages alone.
        expect(msg, `${label}:not-vacuous-zod-type-only`).not.toMatch(
          /^expected string, received/i,
        )
      }
      expect(getterHits.count, `${label}:getter_hits`).toBe(before)
    }

    // Direct canonical/digest/deepFreeze + instrumented proxy trap matrix
    for (const item of items) {
      const factory = samples[item.kind]
      expect(factory, `${item.id}:factory`).toBeTypeOf('function')
      const sample = factory()
      if (item.kind.includes('proxy')) {
        expect(nodeTypes.isProxy(sample), `${item.id}:isProxy`).toBe(true)
      }
      expectAdmissionThrow(() => canonicalJsonV1(sample), `${item.id}:canonicalJson`, item.kind)
      expectAdmissionThrow(() => canonicalDigestV1(sample as never), `${item.id}:canonicalDigest`, item.kind)
      expectAdmissionThrow(() => deepFreezeV1(sample), `${item.id}:deepFreeze`, item.kind)
      expectAdmissionThrow(() => normalizeAdmittedJsonV1(sample), `${item.id}:normalize`, item.kind)

      // Command: valid field location = target (object-bearing). Never put arrays into string fields.
      if (item.kind === 'symbol_key') {
        const tainted: Record<string | symbol, unknown> = { ...commandBase }
        tainted[Symbol('h')] = 2
        expectAdmissionThrow(() => parseEffectCommandV1(tainted), `${item.id}:parseCommand`, item.kind, 'as_object_property')
        expectAdmissionThrow(
          () => deriveEffectCommandRequestDigestV1(tainted),
          `${item.id}:commandDigest`,
          item.kind, 'as_object_property')
      } else if (item.kind === 'non_enumerable') {
        const tainted = { ...commandBase } as Record<string, unknown>
        Object.defineProperty(tainted, 'hidden', { value: 2, enumerable: false })
        expectAdmissionThrow(() => parseEffectCommandV1(tainted), `${item.id}:parseCommand`, item.kind, 'as_object_property')
        expectAdmissionThrow(
          () => deriveEffectCommandRequestDigestV1(tainted),
          `${item.id}:commandDigest`,
          item.kind, 'as_object_property')
      } else if (item.kind === 'accessor') {
        const tainted = { ...commandBase } as Record<string, unknown>
        Object.defineProperty(tainted, 'x', {
          get() {
            getterHits.count += 1
            return 2
          },
          enumerable: true,
        })
        expectAdmissionThrow(() => parseEffectCommandV1(tainted), `${item.id}:parseCommand`, item.kind, 'as_object_property')
        expectAdmissionThrow(
          () => deriveEffectCommandRequestDigestV1(tainted),
          `${item.id}:commandDigest`,
          item.kind, 'as_object_property')
      } else if (item.kind === 'class') {
        expectAdmissionThrow(() => parseEffectCommandV1(sample), `${item.id}:parseCommand`, item.kind)
        expectAdmissionThrow(
          () => deriveEffectCommandRequestDigestV1(sample),
          `${item.id}:commandDigest`,
          item.kind,
        )
      } else {
        // Representation-level substitute into target after clean bookkeeping of request digest path.
        const cleanDigest = deriveEffectCommandRequestDigestV1(commandBase)
        expect(cleanDigest.startsWith('sha256:')).toBe(true)
        const nested = nestInAdmittedObject(sample, item.kind)
        // target must remain an object location: put adversary under target via nested admitted object shape
        // by replacing target with a plain object that embeds the adversary in a nested field when needed.
        let taintedTarget: unknown
        if (
          item.kind.startsWith('array_') ||
          item.kind === 'sparse' ||
          item.kind.endsWith('_proxy') ||
          item.kind === 'array_nested_getter'
        ) {
          // Keep target as object; nest adversary under an admitted nested field position by
          // using a proxy/array as the whole target only when the sample itself is the adversary object/array.
          taintedTarget =
            item.kind === 'array_nested_getter' || item.kind === 'object_transparent_proxy'
              ? nested
              : item.kind.endsWith('_proxy') || item.kind.startsWith('array_') || item.kind === 'sparse'
                ? sample
                : nested
        } else if (item.kind === 'undefined' || item.kind === 'bigint' || item.kind === 'symbol' || item.kind === 'function') {
          taintedTarget = sample
        } else {
          taintedTarget = sample
        }
        const tainted = { ...commandBase, target: taintedTarget as typeof cleanTarget }
        expectAdmissionThrow(() => parseEffectCommandV1(tainted), `${item.id}:parseCommand`, item.kind, 'as_object_property')
        expectAdmissionThrow(
          () => deriveEffectCommandRequestDigestV1(tainted),
          `${item.id}:commandDigest`,
          item.kind, 'as_object_property')
      }

      // Observation readback (admitted JSON object field)
      if (item.kind === 'symbol_key') {
        const rb: Record<string | symbol, unknown> = { present: true }
        rb[Symbol('h')] = 2
        expectAdmissionThrow(
          () => parseEffectObservationV1({ ...obsBase, readback: rb }),
          `${item.id}:parseObs`,
          item.kind, 'as_object_property')
      } else if (item.kind === 'non_enumerable') {
        const rb: Record<string, unknown> = { present: true }
        Object.defineProperty(rb, 'hidden', { value: 2, enumerable: false })
        expectAdmissionThrow(
          () => parseEffectObservationV1({ ...obsBase, readback: rb }),
          `${item.id}:parseObs`,
          item.kind, 'as_object_property')
      } else if (item.kind === 'accessor') {
        const rb: Record<string, unknown> = { present: true }
        Object.defineProperty(rb, 'x', {
          get() {
            getterHits.count += 1
            return 2
          },
          enumerable: true,
        })
        expectAdmissionThrow(
          () => parseEffectObservationV1({ ...obsBase, readback: rb }),
          `${item.id}:parseObs`,
          item.kind, 'as_object_property')
      } else if (item.kind === 'class') {
        expectAdmissionThrow(
          () => parseEffectObservationV1({ ...obsBase, readback: sample as never }),
          `${item.id}:parseObs`,
          item.kind, 'as_object_property')
      } else {
        const nested = nestInAdmittedObject(sample, item.kind)
        expectAdmissionThrow(
          () => parseEffectObservationV1({ ...obsBase, readback: nested }),
          `${item.id}:parseObs`,
          item.kind, 'as_object_property')
      }

      // Receipt: clean twin digests first, then substitute only post_readback representation.
      {
        const nested = nestInAdmittedObject(sample, item.kind)
        if (item.kind === 'class') {
          expectAdmissionThrow(() => parseEffectReceiptV1(sample), `${item.id}:parseReceipt`, item.kind)
          expectAdmissionThrow(
            () => deriveEffectReceiptDigestV1(sample),
            `${item.id}:receiptDigest`,
            item.kind,
          )
        } else if (item.kind === 'symbol_key') {
          const body: Record<string | symbol, unknown> = { ...receiptBase }
          body[Symbol('h')] = 2
          expectAdmissionThrow(() => parseEffectReceiptV1(body), `${item.id}:parseReceipt`, item.kind, 'as_object_property')
          expectAdmissionThrow(
            () => deriveEffectReceiptDigestV1(body),
            `${item.id}:receiptDigest`,
            item.kind, 'as_object_property')
        } else if (item.kind === 'non_enumerable') {
          const body = { ...receiptBase } as Record<string, unknown>
          Object.defineProperty(body, 'hidden', { value: 2, enumerable: false })
          expectAdmissionThrow(() => parseEffectReceiptV1(body), `${item.id}:parseReceipt`, item.kind, 'as_object_property')
          expectAdmissionThrow(
            () => deriveEffectReceiptDigestV1(body),
            `${item.id}:receiptDigest`,
            item.kind, 'as_object_property')
        } else if (item.kind === 'accessor') {
          const body = { ...receiptBase } as Record<string, unknown>
          Object.defineProperty(body, 'x', {
            get() {
              getterHits.count += 1
              return 2
            },
            enumerable: true,
          })
          expectAdmissionThrow(() => parseEffectReceiptV1(body), `${item.id}:parseReceipt`, item.kind, 'as_object_property')
          expectAdmissionThrow(
            () => deriveEffectReceiptDigestV1(body),
            `${item.id}:receiptDigest`,
            item.kind, 'as_object_property')
        } else {
          // Keep clean receipt_digest bookkeeping; substitute only post_readback.
          const body = {
            ...receiptBase,
            post_readback: nested,
            receipt_digest: cleanReceiptDigest,
          }
          expectAdmissionThrow(() => parseEffectReceiptV1(body), `${item.id}:parseReceipt`, item.kind, 'as_object_property')
          expectAdmissionThrow(
            () => deriveEffectReceiptDigestV1({ ...receiptBaseNoDigest, post_readback: nested }),
            `${item.id}:receiptDigest`,
            item.kind, 'as_object_property')
        }
      }

      // Ledger: clean twin event_id + correct preflight digest, then substitute only preflight_readback.
      {
        if (item.kind === 'class') {
          expectAdmissionThrow(
            () => parseWorkflowEffectLedgerEventV1(sample),
            `${item.id}:parseLedger`,
            item.kind,
          )
          expectAdmissionThrow(
            () => deriveWorkflowEffectLedgerEventIdV1(sample as never),
            `${item.id}:ledgerId`,
            item.kind,
          )
        } else if (item.kind === 'symbol_key') {
          const body: Record<string | symbol, unknown> = { ...intentLedgerBase }
          body[Symbol('h')] = 2
          expectAdmissionThrow(
            () => parseWorkflowEffectLedgerEventV1(body),
            `${item.id}:parseLedger`,
            item.kind, 'as_object_property')
          expectAdmissionThrow(
            () => deriveWorkflowEffectLedgerEventIdV1(body),
            `${item.id}:ledgerId`,
            item.kind, 'as_object_property')
        } else if (item.kind === 'non_enumerable') {
          const body = { ...intentLedgerBase } as Record<string, unknown>
          Object.defineProperty(body, 'hidden', { value: 2, enumerable: false })
          expectAdmissionThrow(
            () => parseWorkflowEffectLedgerEventV1(body),
            `${item.id}:parseLedger`,
            item.kind, 'as_object_property')
          expectAdmissionThrow(
            () => deriveWorkflowEffectLedgerEventIdV1(body),
            `${item.id}:ledgerId`,
            item.kind, 'as_object_property')
        } else if (item.kind === 'accessor') {
          const body = { ...intentLedgerBase } as Record<string, unknown>
          Object.defineProperty(body, 'x', {
            get() {
              getterHits.count += 1
              return 2
            },
            enumerable: true,
          })
          expectAdmissionThrow(
            () => parseWorkflowEffectLedgerEventV1(body),
            `${item.id}:parseLedger`,
            item.kind, 'as_object_property')
          expectAdmissionThrow(
            () => deriveWorkflowEffectLedgerEventIdV1(body),
            `${item.id}:ledgerId`,
            item.kind, 'as_object_property')
        } else {
          const nested = nestInAdmittedObject(sample, item.kind)
          const body = {
            ...intentLedgerBase,
            event_id: cleanLedgerEventId,
            payload: {
              ...intentLedgerBase.payload,
              preflight_readback: nested,
              // keep clean twin digest so a silent admission regression cannot hide behind mismatch-only
              // unless admission error is asserted (we assert admission error explicitly).
              preflight_readback_digest: intentLedgerBase.payload.preflight_readback_digest,
            },
          }
          expectAdmissionThrow(
            () => parseWorkflowEffectLedgerEventV1(body),
            `${item.id}:parseLedger`,
            item.kind, 'as_object_property')
          expectAdmissionThrow(
            () =>
              deriveWorkflowEffectLedgerEventIdV1({
                ...intentLedgerCleanNoId,
                payload: { ...intentLedgerCleanNoId.payload, preflight_readback: nested },
              }),
            `${item.id}:ledgerId`,
            item.kind, 'as_object_property')
        }
      }

      // V2: clean twin event_id, substitute only payload admitted object.
      {
        if (item.kind === 'class') {
          expectAdmissionThrow(
            () => parseWorkflowExecutionEventV2(sample),
            `${item.id}:parseV2`,
            item.kind,
          )
          expectAdmissionThrow(
            () => deriveWorkflowExecutionEventIdV2(sample as never),
            `${item.id}:v2Id`,
            item.kind,
          )
        } else if (item.kind === 'symbol_key') {
          const body: Record<string | symbol, unknown> = { ...v2Base }
          body[Symbol('h')] = 2
          expectAdmissionThrow(() => parseWorkflowExecutionEventV2(body), `${item.id}:parseV2`, item.kind, 'as_object_property')
          expectAdmissionThrow(
            () => deriveWorkflowExecutionEventIdV2(body),
            `${item.id}:v2Id`,
            item.kind, 'as_object_property')
        } else if (item.kind === 'non_enumerable') {
          const body = { ...v2Base } as Record<string, unknown>
          Object.defineProperty(body, 'hidden', { value: 2, enumerable: false })
          expectAdmissionThrow(() => parseWorkflowExecutionEventV2(body), `${item.id}:parseV2`, item.kind, 'as_object_property')
          expectAdmissionThrow(
            () => deriveWorkflowExecutionEventIdV2(body),
            `${item.id}:v2Id`,
            item.kind, 'as_object_property')
        } else if (item.kind === 'accessor') {
          const body = { ...v2Base } as Record<string, unknown>
          Object.defineProperty(body, 'x', {
            get() {
              getterHits.count += 1
              return 2
            },
            enumerable: true,
          })
          expectAdmissionThrow(() => parseWorkflowExecutionEventV2(body), `${item.id}:parseV2`, item.kind, 'as_object_property')
          expectAdmissionThrow(
            () => deriveWorkflowExecutionEventIdV2(body),
            `${item.id}:v2Id`,
            item.kind, 'as_object_property')
        } else {
          const nested = nestInAdmittedObject(sample, item.kind)
          const body = { ...v2Base, event_id: cleanV2EventId, payload: nested }
          expectAdmissionThrow(() => parseWorkflowExecutionEventV2(body), `${item.id}:parseV2`, item.kind, 'as_object_property')
          expectAdmissionThrow(
            () => deriveWorkflowExecutionEventIdV2({ ...v2CleanNoId, payload: nested }),
            `${item.id}:v2Id`,
            item.kind, 'as_object_property')
        }
      }
    }

    // Explicit instrumented proxy trap counters across named boundaries (must all be zero).
    const boundaries: Array<{ name: string; run: (value: unknown) => unknown }> = [
      { name: 'canonicalJsonV1', run: (v) => canonicalJsonV1(v) },
      { name: 'canonicalDigestV1', run: (v) => canonicalDigestV1(v) },
      { name: 'deepFreezeV1', run: (v) => deepFreezeV1(v) },
      { name: 'normalizeAdmittedJsonV1', run: (v) => normalizeAdmittedJsonV1(v) },
      {
        name: 'parseEffectCommandV1.target',
        run: (v) => parseEffectCommandV1({ ...commandBase, target: v as never }),
      },
      {
        name: 'deriveEffectCommandRequestDigestV1.target',
        run: (v) => deriveEffectCommandRequestDigestV1({ ...commandBase, target: v as never }),
      },
      {
        name: 'parseEffectObservationV1.readback',
        run: (v) => parseEffectObservationV1({ ...obsBase, readback: v as never }),
      },
      {
        name: 'parseEffectReceiptV1.post_readback',
        run: (v) =>
          parseEffectReceiptV1({
            ...receiptBase,
            post_readback: v as never,
            receipt_digest: cleanReceiptDigest,
          }),
      },
      {
        name: 'deriveEffectReceiptDigestV1.post_readback',
        run: (v) =>
          deriveEffectReceiptDigestV1({
            ...receiptBaseNoDigest,
            post_readback: v as never,
          }),
      },
      {
        name: 'parseWorkflowEffectLedgerEventV1.preflight_readback',
        run: (v) =>
          parseWorkflowEffectLedgerEventV1({
            ...intentLedgerBase,
            payload: { ...intentLedgerBase.payload, preflight_readback: v as never },
          }),
      },
      {
        name: 'deriveWorkflowEffectLedgerEventIdV1.preflight_readback',
        run: (v) =>
          deriveWorkflowEffectLedgerEventIdV1({
            ...intentLedgerCleanNoId,
            payload: { ...intentLedgerCleanNoId.payload, preflight_readback: v as never },
          }),
      },
      {
        name: 'parseWorkflowExecutionEventV2.payload',
        run: (v) => parseWorkflowExecutionEventV2({ ...v2Base, payload: v as never }),
      },
      {
        name: 'deriveWorkflowExecutionEventIdV2.payload',
        run: (v) => deriveWorkflowExecutionEventIdV2({ ...v2CleanNoId, payload: v as never }),
      },
    ]

    const makeInstrumentedRevoked = (target: object): { proxy: object; traps: TrapCounts } => {
      const traps = zeroTraps()
      const handler: ProxyHandler<object> = {
        get(t, p, r) {
          traps.get += 1
          return Reflect.get(t, p, r)
        },
        getPrototypeOf(t) {
          traps.getPrototypeOf += 1
          return Reflect.getPrototypeOf(t)
        },
        ownKeys(t) {
          traps.ownKeys += 1
          return Reflect.ownKeys(t)
        },
        getOwnPropertyDescriptor(t, p) {
          traps.getOwnPropertyDescriptor += 1
          return Reflect.getOwnPropertyDescriptor(t, p)
        },
        apply(t, thisArg, args) {
          traps.apply += 1
          return Reflect.apply(t as never, thisArg, args)
        },
        set(t, p, v, r) {
          traps.set += 1
          return Reflect.set(t, p, v, r)
        },
        has(t, p) {
          traps.has += 1
          return Reflect.has(t, p)
        },
        construct(t, args, n) {
          traps.construct += 1
          return Reflect.construct(t as never, args, n)
        },
      }
      // Complete instrumented handler attached BEFORE revocable; then revoke.
      const rev = Proxy.revocable(target, handler)
      rev.revoke()
      return { proxy: rev.proxy as object, traps }
    }

    const proxyTargets: Array<{
      label: string
      make: (traps: TrapCounts) => object
      trapsOf?: (value: object) => TrapCounts
    }> = [
      {
        label: 'array',
        make: (traps) => wrapProxy(['before'], traps),
      },
      {
        label: 'object',
        make: (traps) => wrapProxy({ a: 1 }, traps),
      },
      {
        label: 'revoked_array',
        make: () => {
          const { proxy, traps } = makeInstrumentedRevoked(['before'])
          revokedTrapMap.set(proxy, traps)
          return proxy
        },
        trapsOf: (value) => {
          const t = revokedTrapMap.get(value)
          expect(t, 'revoked_array traps attached').toBeTruthy()
          return t as TrapCounts
        },
      },
      {
        label: 'revoked_object',
        make: () => {
          const { proxy, traps } = makeInstrumentedRevoked({ a: 1 })
          revokedTrapMap.set(proxy, traps)
          return proxy
        },
        trapsOf: (value) => {
          const t = revokedTrapMap.get(value)
          expect(t, 'revoked_object traps attached').toBeTruthy()
          return t as TrapCounts
        },
      },
    ]

    let totalMeta = 0
    let rejected = 0
    for (const boundary of boundaries) {
      for (const pt of proxyTargets) {
        const traps = zeroTraps()
        const value = pt.make(traps)
        const activeTraps = pt.trapsOf ? pt.trapsOf(value) : traps
        expect(nodeTypes.isProxy(value), `${boundary.name}/${pt.label}:isProxy`).toBe(true)
        const beforeGets = getterHits.count
        try {
          boundary.run(value)
          expect.fail(`${boundary.name}/${pt.label}: expected reject`)
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e)
          expect(msg, `${boundary.name}/${pt.label}`).toBe(PROXY_ERROR)
          rejected += 1
        }
        expect(sumTraps(activeTraps), `${boundary.name}/${pt.label}:traps`).toBe(0)
        expect(getterHits.count, `${boundary.name}/${pt.label}:getter`).toBe(beforeGets)
        totalMeta += sumTraps(activeTraps)
      }
      // accessor array at same boundary
      const acc = makeAccessorArray()
      const before = getterHits.count
      try {
        boundary.run(acc)
        expect.fail(`${boundary.name}/accessor_array: expected reject`)
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        expect(msg, `${boundary.name}/accessor_array`).toBe(
          exactAdmissionMessageByKind.array_getter_index,
        )
        rejected += 1
      }
      expect(getterHits.count, `${boundary.name}/accessor_array:getter`).toBe(before)
    }
    expect(totalMeta).toBe(0)
    expect(rejected).toBeGreaterThan(0)

    // Exhaustive unfiltered effect-contract schema export sets (module + package barrel).
    // Structural/name-based detection first — never intersect with the expected list before compare.
    const isEffectContractSchemaExportName = (name: string): boolean =>
      name.endsWith('Schema') &&
      !name.startsWith('to') &&
      (name.startsWith('AdmittedJson') ||
        name.startsWith('Effect') ||
        name.startsWith('WorkflowEffectLedger') ||
        name.startsWith('WorkflowExecutionEventV2'))

    const REQUIRED_EFFECT_CONTRACT_SCHEMA_NAMES = [
      'AdmittedJsonObjectV1Schema',
      'AdmittedJsonValueV1Schema',
      'EffectCommandV1Schema',
      'EffectObservationV1Schema',
      'EffectReceiptV1Schema',
      'EffectTargetV1Schema',
      'WorkflowEffectLedgerEventV1ObjectSchema',
      'WorkflowEffectLedgerEventV1Schema',
      'WorkflowExecutionEventV2Schema',
    ] as const
    const REQUIRED_SORTED = [...REQUIRED_EFFECT_CONTRACT_SCHEMA_NAMES].sort()

    const effectModule = await import('./workflow-effect')
    const moduleSchemaNames = Object.keys(effectModule)
      .filter((k) => isEffectContractSchemaExportName(k))
      .sort()
    expect(moduleSchemaNames, 'module unfiltered effect-contract schema exports').toEqual(REQUIRED_SORTED)

    const packageBarrel = await import('./index')
    const barrelSchemaNames = Object.keys(packageBarrel)
      .filter((k) => isEffectContractSchemaExportName(k))
      .sort()
    expect(barrelSchemaNames, 'barrel unfiltered effect-contract schema exports').toEqual(REQUIRED_SORTED)

    // Negative controls: simulated tenth and omitted barrel export must fail set equality.
    expect(
      [...moduleSchemaNames, 'EffectSimulatedTenthV1Schema'].sort(),
    ).not.toEqual(REQUIRED_SORTED)
    expect(
      barrelSchemaNames.filter((n) => n !== 'WorkflowEffectLedgerEventV1ObjectSchema'),
    ).not.toEqual(REQUIRED_SORTED)

    const loneHigh = String.fromCharCode(0xd800)
    // Hardcoded exact portable/admitted issue messages — not derived from schema under test.
    const PORTABLE_SURROGATE_ISSUE_MESSAGE =
      'Invalid string: must match pattern /^[^\\u0000-\\u0008\\u000e-\\u001b\\u007f-\\u0084\\u0086-\\u009f\\ud800-\\udfff]*$/'
    const ADMITTED_FORMAT_MSG =
      'string must be Unicode scalar values (no unpaired UTF-16 surrogates)'
    const ADMITTED_CUSTOM_MSG = 'string contains unpaired UTF-16 high surrogate'
    const LEDGER_PAYLOAD_BOOKKEEPING_MSG = 'payload does not match event_type intent_recorded'

    type IssueTuple = { message: string; path: PropertyKey[] }
    type SchemaRow = {
      name: (typeof REQUIRED_EFFECT_CONTRACT_SCHEMA_NAMES)[number]
      schema: {
        safeParse: (v: unknown) => {
          success: boolean
          error?: { issues: Array<{ message: string; path: PropertyKey[] }> }
        }
      }
      clean: unknown
      mutate: (clean: any) => unknown
      // Full ordered hardcoded issue tuples (message + full path). No substring/prefix/any-match.
      expectedIssues: IssueTuple[]
    }

    const schemaRows: SchemaRow[] = [
      {
        name: 'AdmittedJsonValueV1Schema',
        schema: AdmittedJsonValueV1Schema,
        clean: { s: 'ok' },
        mutate: () => ({ s: loneHigh }),
        expectedIssues: [
          { message: ADMITTED_FORMAT_MSG, path: ['s'] },
          { message: ADMITTED_CUSTOM_MSG, path: ['s'] },
        ],
      },
      {
        name: 'AdmittedJsonObjectV1Schema',
        schema: AdmittedJsonObjectV1Schema,
        clean: { s: 'ok' },
        mutate: () => ({ s: loneHigh }),
        expectedIssues: [
          { message: ADMITTED_FORMAT_MSG, path: ['s'] },
          { message: ADMITTED_CUSTOM_MSG, path: ['s'] },
        ],
      },
      {
        name: 'EffectTargetV1Schema',
        schema: EffectTargetV1Schema,
        clean: cleanTarget,
        mutate: (c) => ({ ...c, target_ref: loneHigh }),
        expectedIssues: [{ message: PORTABLE_SURROGATE_ISSUE_MESSAGE, path: ['target_ref'] }],
      },
      {
        name: 'EffectCommandV1Schema',
        schema: EffectCommandV1Schema,
        clean: commandBase,
        mutate: (c) => ({ ...c, target: { ...c.target, target_ref: loneHigh } }),
        expectedIssues: [
          { message: PORTABLE_SURROGATE_ISSUE_MESSAGE, path: ['target', 'target_ref'] },
        ],
      },
      {
        name: 'EffectObservationV1Schema',
        schema: EffectObservationV1Schema,
        clean: obsBase,
        mutate: (c) => ({ ...c, readback: { s: loneHigh } }),
        expectedIssues: [
          { message: ADMITTED_FORMAT_MSG, path: ['readback', 's'] },
          { message: ADMITTED_CUSTOM_MSG, path: ['readback', 's'] },
        ],
      },
      {
        name: 'EffectReceiptV1Schema',
        schema: EffectReceiptV1Schema,
        clean: receiptBase,
        mutate: (c) => ({ ...c, post_readback: { s: loneHigh } }),
        expectedIssues: [
          { message: ADMITTED_FORMAT_MSG, path: ['post_readback', 's'] },
          { message: ADMITTED_CUSTOM_MSG, path: ['post_readback', 's'] },
        ],
      },
      {
        name: 'WorkflowEffectLedgerEventV1ObjectSchema',
        schema: WorkflowEffectLedgerEventV1ObjectSchema,
        clean: intentLedgerBase,
        mutate: (c) => ({
          ...c,
          payload: { ...c.payload, preflight_readback: { s: loneHigh } },
        }),
        expectedIssues: [
          { message: ADMITTED_FORMAT_MSG, path: ['payload', 'preflight_readback', 's'] },
          { message: ADMITTED_CUSTOM_MSG, path: ['payload', 'preflight_readback', 's'] },
        ],
      },
      {
        name: 'WorkflowEffectLedgerEventV1Schema',
        schema: WorkflowEffectLedgerEventV1Schema,
        clean: intentLedgerBase,
        mutate: (c) => ({
          ...c,
          payload: { ...c.payload, preflight_readback: { s: loneHigh } },
        }),
        // Full ordered list: two admission issues + explicit allowed bookkeeping issue.
        expectedIssues: [
          { message: ADMITTED_FORMAT_MSG, path: ['payload', 'preflight_readback', 's'] },
          { message: ADMITTED_CUSTOM_MSG, path: ['payload', 'preflight_readback', 's'] },
          { message: LEDGER_PAYLOAD_BOOKKEEPING_MSG, path: ['payload'] },
        ],
      },
      {
        name: 'WorkflowExecutionEventV2Schema',
        schema: WorkflowExecutionEventV2Schema,
        clean: v2Base,
        mutate: (c) => ({ ...c, payload: { s: loneHigh } }),
        expectedIssues: [
          { message: ADMITTED_FORMAT_MSG, path: ['payload', 's'] },
          { message: ADMITTED_CUSTOM_MSG, path: ['payload', 's'] },
        ],
      },
    ]
    expect(schemaRows.map((r) => r.name).sort()).toEqual(REQUIRED_SORTED)

    for (const row of schemaRows) {
      const cleanParsed = row.schema.safeParse(row.clean)
      expect(cleanParsed.success, `${row.name}:clean`).toBe(true)
      const mutated = row.mutate(row.clean)
      const bad = row.schema.safeParse(mutated)
      expect(bad.success, `${row.name}:mutated`).toBe(false)
      const actualIssues: IssueTuple[] = (bad.error?.issues ?? []).map((i) => ({
        message: i.message,
        path: [...i.path],
      }))
      expect(actualIssues, `${row.name}:exact-issue-tuples`).toEqual(row.expectedIssues)
    }

    // Receipt additional admitted-field mutants (target/preflight); exact hardcoded tuples.
    {
      const receiptTargetMut = {
        ...receiptBase,
        target: { ...cleanTarget, target_ref: loneHigh },
      }
      const r1 = EffectReceiptV1Schema.safeParse(receiptTargetMut)
      expect(r1.success).toBe(false)
      expect(
        (r1.error?.issues ?? []).map((i) => ({ message: i.message, path: [...i.path] })),
      ).toEqual([{ message: PORTABLE_SURROGATE_ISSUE_MESSAGE, path: ['target', 'target_ref'] }])

      const receiptPreMut = {
        ...receiptBase,
        preflight_readback: { s: loneHigh },
      }
      const r2 = EffectReceiptV1Schema.safeParse(receiptPreMut)
      expect(r2.success).toBe(false)
      expect(
        (r2.error?.issues ?? []).map((i) => ({ message: i.message, path: [...i.path] })),
      ).toEqual([
        { message: ADMITTED_FORMAT_MSG, path: ['preflight_readback', 's'] },
        { message: ADMITTED_CUSTOM_MSG, path: ['preflight_readback', 's'] },
      ])
    }

    const draftFns = [
      toEffectCommandV1JsonSchema,
      toEffectObservationV1JsonSchema,
      toEffectReceiptV1JsonSchema,
      toWorkflowEffectLedgerEventV1JsonSchema,
      toWorkflowExecutionEventV2JsonSchema,
    ]
    expect(draftFns.length).toBe(5)

    // Shared schema_boundary_vectors (materialized) across all five root schemas.
    const Ajv2020 = (await import('ajv/dist/2020.js')).default
    const ajv = new Ajv2020({ allErrors: true, strict: false, validateFormats: true })
    // Minimal format support without adding ajv-formats dependency to this package.
    ajv.addFormat('date-time', {
      type: 'string',
      validate: (s: string) =>
        /^(?:000[1-9]|00[1-9]\d|0[1-9]\d{2}|[1-9]\d{3})-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})$/.test(
          s,
        ),
    })
    const schemaCache = new Map<string, ReturnType<typeof ajv.compile>>()
    const paths = new Set(corpus.schema_boundary_vectors.map((v) => v.path))
    expect([...paths].sort()).toEqual(
      [
        'effect-command-v1.schema.json',
        'effect-observation-v1.schema.json',
        'effect-receipt-v1.schema.json',
        'workflow-effect-ledger-event-v1.schema.json',
        'workflow-execution-event-v2.schema.json',
      ].sort(),
    )
    for (const vector of corpus.schema_boundary_vectors) {
      const value = materializeSharedCorpusValue(vector.value)
      let validate = schemaCache.get(vector.path)
      if (!validate) {
        const schema = JSON.parse(
          readFileSync(join(here, '../../../schemas', vector.path), 'utf8'),
        )
        validate = ajv.compile(schema)
        schemaCache.set(vector.path, validate)
      }
      const ok = Boolean(validate(value))
      if (vector.expect === 'accept') {
        expect(ok, `${vector.id}:${JSON.stringify(validate.errors)}`).toBe(true)
      } else {
        expect(ok, vector.id).toBe(false)
      }
    }
  })

  it('rejects unpaired UTF-16 surrogates across public parsers/helpers/IDs and accepts supplementary-plane scalars', () => {
    const loneHigh = String.fromCharCode(0xd800)
    const loneLow = String.fromCharCode(0xdc00)
    const badValues = [
      { s: loneHigh },
      { s: loneLow },
      { [loneHigh]: 1 },
      { [loneLow]: 1 },
      JSON.parse('{"s":"\\ud800"}'),
      JSON.parse('{"s":"\\udc00"}'),
    ]
    const commandBase = {
      schema_version: 'effect_command_v1' as const,
      command_id: 'cmd-1',
      adapter_id: 'test.injected_adapter',
      operation: 'noop_apply',
      target: { target_kind: 'injected_callable', target_ref: 'target/alpha' },
      payload_digest: 'sha256:' + 'ab'.repeat(32),
      idempotency_key: 'effect-cmd-1',
      issuer_id: 'issuer',
      policy_revision: 'pol-1',
      config_revision: 'cfg-1',
      side_effect_tier: 'local_write' as const,
      compensation_ref: 'compensation:test.restore',
      reconcile_ref: 'reconcile:test.readback',
    }
    const obsBase = {
      schema_version: 'effect_observation_v1' as const,
      effect_id: 'effect-1',
      observation_id: 'obs-1',
      classification: 'applied' as const,
      fence: 1,
      observed_at: '2026-08-02T00:00:00Z',
      adapter_result_digest: null,
      readback: { present: true },
    }
    const receiptBase = {
      schema_version: 'effect_receipt_v1' as const,
      effect_id: 'effect-1',
      command_digest: 'sha256:' + 'ab'.repeat(32),
      adapter_id: 'test.injected_adapter',
      operation: 'noop_apply',
      target: { target_kind: 'injected_callable', target_ref: 'target/alpha' },
      payload_digest: 'sha256:' + 'ab'.repeat(32),
      authority_binding_digest: 'sha256:' + 'ab'.repeat(32),
      idempotency_key: 'effect-cmd-1',
      fence: 1,
      preflight_readback: { ok: true },
      post_readback: { present: true },
      result_classification: 'applied' as const,
      compensation_ref: 'compensation:test.restore',
      reconcile_ref: 'reconcile:test.readback',
    }
    const intentBase = {
      schema_version: 'workflow_effect_ledger_event_v1' as const,
      effect_id: 'effect-1',
      sequence: 2,
      previous_event_id: 'wel_' + 'a'.repeat(64),
      idempotency_key: 'effect-1:intent',
      occurred_at: '2026-08-02T00:00:01Z',
      event_type: 'intent_recorded' as const,
      payload: {
        fence: 1,
        command_digest: 'sha256:' + 'ab'.repeat(32),
        request_digest: 'sha256:' + 'ab'.repeat(32),
        target_digest: 'sha256:' + 'ab'.repeat(32),
        authority_binding_digest: 'sha256:' + 'ab'.repeat(32),
        preflight_readback: { ok: true },
        preflight_readback_digest: 'sha256:' + 'ab'.repeat(32),
        adapter_id: 'test.injected_adapter',
        operation: 'noop_apply',
        payload_digest: 'sha256:' + 'ab'.repeat(32),
        idempotency_key: 'effect-cmd-1',
        effect_id: 'effect-1',
      },
    }
    const v2Base = {
      schema_version: 'workflow_execution_event_v2' as const,
      run_id: 'run-1',
      sequence: 1,
      previous_event_id: null,
      idempotency_key: 'run-1:waiting',
      occurred_at: '2026-08-02T00:00:00Z',
      event_type: 'run_waiting_reconcile' as const,
      payload: { effect_id: 'effect-1' },
    }

    for (const bad of badValues) {
      expect(() => canonicalJsonV1(bad)).toThrow(/surrogate/i)
      expect(() => canonicalDigestV1(bad)).toThrow(/surrogate/i)
      expect(() => deepFreezeV1(bad)).toThrow(/surrogate/i)
      expect(() => normalizeAdmittedJsonV1(bad)).toThrow(/surrogate/i)
      expect(AdmittedJsonValueV1Schema.safeParse(bad).success).toBe(false)

      // command: one-field mutation of a string field with the surrogate scalar/key under test
      if (Object.prototype.hasOwnProperty.call(bad, 's')) {
        expect(() => parseEffectCommandV1({ ...commandBase, command_id: (bad as any).s })).toThrow(/surrogate/i)
        expect(() => deriveEffectCommandRequestDigestV1({ ...commandBase, command_id: (bad as any).s })).toThrow(
          /surrogate/i,
        )
      } else {
        const key = Object.keys(bad as object)[0]
        expect(() => parseEffectCommandV1({ ...commandBase, issuer_id: key })).toThrow(/surrogate/i)
        expect(() => deriveEffectCommandRequestDigestV1({ ...commandBase, issuer_id: key })).toThrow(/surrogate/i)
      }

      expect(() => parseEffectObservationV1({ ...obsBase, readback: bad as any })).toThrow(/surrogate/i)
      expect(EffectObservationV1Schema.safeParse({ ...obsBase, readback: bad }).success).toBe(false)

      const receiptBody = {
        ...receiptBase,
        post_readback: bad as any,
        receipt_digest: 'sha256:' + '00'.repeat(32),
      }
      expect(() => parseEffectReceiptV1(receiptBody)).toThrow(/surrogate/i)
      expect(() => deriveEffectReceiptDigestV1(receiptBody)).toThrow(/surrogate/i)

      const ledgerBody = {
        ...intentBase,
        payload: {
          ...intentBase.payload,
          preflight_readback: bad as any,
          preflight_readback_digest: 'sha256:' + 'cd'.repeat(32),
        },
      }
      expect(() => parseWorkflowEffectLedgerEventV1(ledgerBody)).toThrow(/surrogate/i)
      expect(() => deriveWorkflowEffectLedgerEventIdV1(ledgerBody)).toThrow(/surrogate/i)

      const v2Body = { ...v2Base, payload: bad as any }
      expect(() => parseWorkflowExecutionEventV2(v2Body)).toThrow(/surrogate/i)
      expect(() => deriveWorkflowExecutionEventIdV2(v2Body)).toThrow(/surrogate/i)
    }

    const positive = { emoji: '😀', plane: '\u{10000}' }
    const text = canonicalJsonV1(positive)
    expect(text.includes('😀') || text.includes('\\ud83d\\ude00') || text.includes('\\uD83D\\uDE00')).toBe(
      true,
    )
    expect(canonicalDigestV1(positive).startsWith('sha256:')).toBe(true)
    expect(AdmittedJsonValueV1Schema.safeParse(positive).success).toBe(true)
    expect(parseEffectObservationV1({ ...obsBase, readback: positive }).readback).toBeTruthy()
    // UTF-16 key order retained: U+10000 before U+E000
    const ordered = canonicalJsonV1({ '\u{E000}': 2, '\u{10000}': 1 })
    expect(ordered).toBe(canonicalJsonV1({ '\u{10000}': 1, '\u{E000}': 2 }))
  })

  it('rejects any present effect-command request_digest and derives only when absent', () => {
    const commandBase = {
      schema_version: 'effect_command_v1' as const,
      command_id: 'cmd-1',
      adapter_id: 'test.injected_adapter',
      operation: 'noop_apply',
      target: { target_kind: 'injected_callable', target_ref: 'target/alpha' },
      payload_digest: 'sha256:' + 'ab'.repeat(32),
      idempotency_key: 'effect-cmd-1',
      issuer_id: 'issuer',
      policy_revision: 'pol-1',
      config_revision: 'cfg-1',
      side_effect_tier: 'local_write' as const,
      compensation_ref: 'compensation:test.restore',
      reconcile_ref: 'reconcile:test.readback',
    }
    const derived = deriveEffectCommandRequestDigestV1(commandBase)
    expect(parseEffectCommandV1(commandBase)).toBeTruthy()
    for (const present of [null, derived, 'sha256:' + 'ff'.repeat(32), 'nope'] as const) {
      const body = { ...commandBase, request_digest: present as never }
      expect(() => parseEffectCommandV1(body)).toThrow()
      expect(() => deriveEffectCommandRequestDigestV1(body)).toThrow()
    }
  })

  it('rejects timestamp year 0000 and accepts 0001/0099 leap-safe values', () => {
    const base = {
      schema_version: 'effect_observation_v1' as const,
      effect_id: 'effect-1',
      observation_id: 'obs-1',
      classification: 'unknown' as const,
      fence: 1,
      adapter_result_digest: null,
      readback: { present: false },
    }
    expect(() => parseEffectObservationV1({ ...base, observed_at: '0000-01-01T00:00:00Z' })).toThrow()
    expect(parseEffectObservationV1({ ...base, observed_at: '0001-01-01T00:00:00Z' }).observed_at).toBe(
      '0001-01-01T00:00:00Z',
    )
    expect(parseEffectObservationV1({ ...base, observed_at: '0099-12-31T23:59:59Z' }).observed_at).toBe(
      '0099-12-31T23:59:59Z',
    )
    expect(parseEffectObservationV1({ ...base, observed_at: '2024-02-29T00:00:00Z' }).observed_at).toBe(
      '2024-02-29T00:00:00Z',
    )
    expect(() => parseEffectObservationV1({ ...base, observed_at: '2026-02-29T00:00:00Z' })).toThrow()
  })

  it('4A.5A.5 focused regression: package barrel unfiltered export set + exact hardcoded issue tuples', async () => {
    const barrel = await import('./index')
    const isEffectContractSchemaExportName = (name: string): boolean =>
      name.endsWith('Schema') &&
      !name.startsWith('to') &&
      (name.startsWith('AdmittedJson') ||
        name.startsWith('Effect') ||
        name.startsWith('WorkflowEffectLedger') ||
        name.startsWith('WorkflowExecutionEventV2'))

    const REQUIRED = [
      'AdmittedJsonObjectV1Schema',
      'AdmittedJsonValueV1Schema',
      'EffectCommandV1Schema',
      'EffectObservationV1Schema',
      'EffectReceiptV1Schema',
      'EffectTargetV1Schema',
      'WorkflowEffectLedgerEventV1ObjectSchema',
      'WorkflowEffectLedgerEventV1Schema',
      'WorkflowExecutionEventV2Schema',
    ] as const
    const REQUIRED_SORTED = [...REQUIRED].sort()

    const actualBarrelSchemas = Object.keys(barrel)
      .filter((k) => isEffectContractSchemaExportName(k))
      .sort()
    expect(
      actualBarrelSchemas,
      'package barrel must export exactly the nine effect-contract schemas (unfiltered)',
    ).toEqual(REQUIRED_SORTED)

    // Would fail prior missing barrel re-export of WorkflowEffectLedgerEventV1ObjectSchema.
    expect(actualBarrelSchemas).toContain('WorkflowEffectLedgerEventV1ObjectSchema')
    expect(
      actualBarrelSchemas.filter((n) => n !== 'WorkflowEffectLedgerEventV1ObjectSchema'),
    ).not.toEqual(REQUIRED_SORTED)
    expect([...actualBarrelSchemas, 'EffectSimulatedTenthV1Schema'].sort()).not.toEqual(
      REQUIRED_SORTED,
    )

    const loneHigh = String.fromCharCode(0xd800)
    const PORTABLE_SURROGATE_ISSUE_MESSAGE =
      'Invalid string: must match pattern /^[^\\u0000-\\u0008\\u000e-\\u001b\\u007f-\\u0084\\u0086-\\u009f\\ud800-\\udfff]*$/'
    const ADMITTED_FORMAT_MSG =
      'string must be Unicode scalar values (no unpaired UTF-16 surrogates)'
    const ADMITTED_CUSTOM_MSG = 'string contains unpaired UTF-16 high surrogate'
    const LEDGER_PAYLOAD_BOOKKEEPING_MSG = 'payload does not match event_type intent_recorded'

    const DIGEST = 'sha256:' + 'ab'.repeat(32)
    const cleanTarget = { target_kind: 'injected_callable' as const, target_ref: 'target/alpha' }
    const commandBase = {
      schema_version: 'effect_command_v1' as const,
      command_id: 'cmd-1',
      adapter_id: 'test.injected_adapter',
      operation: 'noop_apply',
      target: cleanTarget,
      payload_digest: DIGEST,
      idempotency_key: 'effect-cmd-1',
      issuer_id: 'issuer',
      policy_revision: 'pol-1',
      config_revision: 'cfg-1',
      side_effect_tier: 'local_write' as const,
      compensation_ref: 'compensation:test.restore',
      reconcile_ref: 'reconcile:test.readback',
    }
    const obsBase = {
      schema_version: 'effect_observation_v1' as const,
      effect_id: 'effect-1',
      observation_id: 'obs-1',
      classification: 'applied' as const,
      fence: 1,
      observed_at: '2026-08-02T00:00:00Z',
      adapter_result_digest: null as string | null,
      readback: { present: true as const },
    }
    const receiptNo = {
      schema_version: 'effect_receipt_v1' as const,
      effect_id: 'effect-1',
      command_digest: DIGEST,
      adapter_id: 'test.injected_adapter',
      operation: 'noop_apply',
      target: cleanTarget,
      payload_digest: DIGEST,
      authority_binding_digest: DIGEST,
      idempotency_key: 'effect-cmd-1',
      fence: 1,
      preflight_readback: { ok: true as const },
      post_readback: { present: true as const, marker: 'clean' },
      result_classification: 'applied' as const,
      compensation_ref: 'compensation:test.restore',
      reconcile_ref: 'reconcile:test.readback',
    }
    const cleanReceiptDigest = barrel.deriveEffectReceiptDigestV1(receiptNo)
    const receiptBase = { ...receiptNo, receipt_digest: cleanReceiptDigest }
    const intentPre = { ok: true as const, marker: 'clean-intent' }
    const intentNo = {
      schema_version: 'workflow_effect_ledger_event_v1' as const,
      effect_id: 'effect-1',
      sequence: 2,
      previous_event_id: 'wel_' + 'a'.repeat(64),
      idempotency_key: 'effect-1:intent',
      occurred_at: '2026-08-02T00:00:01Z',
      event_type: 'intent_recorded' as const,
      payload: {
        fence: 1,
        command_digest: DIGEST,
        request_digest: DIGEST,
        target_digest: DIGEST,
        authority_binding_digest: DIGEST,
        preflight_readback: intentPre,
        preflight_readback_digest: barrel.canonicalDigestV1(intentPre),
        adapter_id: 'test.injected_adapter',
        operation: 'noop_apply',
        payload_digest: DIGEST,
        idempotency_key: 'effect-cmd-1',
        effect_id: 'effect-1',
      },
    }
    const cleanLedgerEventId = barrel.deriveWorkflowEffectLedgerEventIdV1(intentNo)
    const ledgerBase = { ...intentNo, event_id: cleanLedgerEventId }
    const v2No = {
      schema_version: 'workflow_execution_event_v2' as const,
      run_id: 'run-1',
      sequence: 1,
      previous_event_id: null as string | null,
      idempotency_key: 'run-1:waiting',
      occurred_at: '2026-08-02T00:00:00Z',
      event_type: 'run_waiting_reconcile' as const,
      payload: { effect_id: 'effect-1', reason: 'unknown_observation' },
    }
    const cleanV2EventId = barrel.deriveWorkflowExecutionEventIdV2(v2No)
    const v2Base = { ...v2No, event_id: cleanV2EventId }

    type IssueTuple = { message: string; path: PropertyKey[] }
    const rows: Array<{
      name: (typeof REQUIRED)[number]
      schema: {
        safeParse: (v: unknown) => {
          success: boolean
          error?: { issues: Array<{ message: string; path: PropertyKey[] }> }
        }
      }
      clean: unknown
      mutate: (c: any) => unknown
      expectedIssues: IssueTuple[]
    }> = [
      {
        name: 'AdmittedJsonValueV1Schema',
        schema: barrel.AdmittedJsonValueV1Schema,
        clean: { s: 'ok' },
        mutate: () => ({ s: loneHigh }),
        expectedIssues: [
          { message: ADMITTED_FORMAT_MSG, path: ['s'] },
          { message: ADMITTED_CUSTOM_MSG, path: ['s'] },
        ],
      },
      {
        name: 'AdmittedJsonObjectV1Schema',
        schema: barrel.AdmittedJsonObjectV1Schema,
        clean: { s: 'ok' },
        mutate: () => ({ s: loneHigh }),
        expectedIssues: [
          { message: ADMITTED_FORMAT_MSG, path: ['s'] },
          { message: ADMITTED_CUSTOM_MSG, path: ['s'] },
        ],
      },
      {
        name: 'EffectTargetV1Schema',
        schema: barrel.EffectTargetV1Schema,
        clean: cleanTarget,
        mutate: (c) => ({ ...c, target_ref: loneHigh }),
        expectedIssues: [{ message: PORTABLE_SURROGATE_ISSUE_MESSAGE, path: ['target_ref'] }],
      },
      {
        name: 'EffectCommandV1Schema',
        schema: barrel.EffectCommandV1Schema,
        clean: commandBase,
        mutate: (c) => ({ ...c, target: { ...c.target, target_ref: loneHigh } }),
        expectedIssues: [
          { message: PORTABLE_SURROGATE_ISSUE_MESSAGE, path: ['target', 'target_ref'] },
        ],
      },
      {
        name: 'EffectObservationV1Schema',
        schema: barrel.EffectObservationV1Schema,
        clean: obsBase,
        mutate: (c) => ({ ...c, readback: { s: loneHigh } }),
        expectedIssues: [
          { message: ADMITTED_FORMAT_MSG, path: ['readback', 's'] },
          { message: ADMITTED_CUSTOM_MSG, path: ['readback', 's'] },
        ],
      },
      {
        name: 'EffectReceiptV1Schema',
        schema: barrel.EffectReceiptV1Schema,
        clean: receiptBase,
        mutate: (c) => ({ ...c, post_readback: { s: loneHigh } }),
        expectedIssues: [
          { message: ADMITTED_FORMAT_MSG, path: ['post_readback', 's'] },
          { message: ADMITTED_CUSTOM_MSG, path: ['post_readback', 's'] },
        ],
      },
      {
        name: 'WorkflowEffectLedgerEventV1ObjectSchema',
        schema: barrel.WorkflowEffectLedgerEventV1ObjectSchema,
        clean: ledgerBase,
        mutate: (c) => ({
          ...c,
          payload: { ...c.payload, preflight_readback: { s: loneHigh } },
        }),
        expectedIssues: [
          { message: ADMITTED_FORMAT_MSG, path: ['payload', 'preflight_readback', 's'] },
          { message: ADMITTED_CUSTOM_MSG, path: ['payload', 'preflight_readback', 's'] },
        ],
      },
      {
        name: 'WorkflowEffectLedgerEventV1Schema',
        schema: barrel.WorkflowEffectLedgerEventV1Schema,
        clean: ledgerBase,
        mutate: (c) => ({
          ...c,
          payload: { ...c.payload, preflight_readback: { s: loneHigh } },
        }),
        expectedIssues: [
          { message: ADMITTED_FORMAT_MSG, path: ['payload', 'preflight_readback', 's'] },
          { message: ADMITTED_CUSTOM_MSG, path: ['payload', 'preflight_readback', 's'] },
          { message: LEDGER_PAYLOAD_BOOKKEEPING_MSG, path: ['payload'] },
        ],
      },
      {
        name: 'WorkflowExecutionEventV2Schema',
        schema: barrel.WorkflowExecutionEventV2Schema,
        clean: v2Base,
        mutate: (c) => ({ ...c, payload: { s: loneHigh } }),
        expectedIssues: [
          { message: ADMITTED_FORMAT_MSG, path: ['payload', 's'] },
          { message: ADMITTED_CUSTOM_MSG, path: ['payload', 's'] },
        ],
      },
    ]
    expect(rows.map((r) => r.name).sort()).toEqual(REQUIRED_SORTED)

    for (const row of rows) {
      // Clean root must independently pass (catches old invalid-root matrix).
      const cleanParsed = row.schema.safeParse(row.clean)
      expect(cleanParsed.success, `${row.name}:clean-twin`).toBe(true)
      // Old 4A.5A.3 invalid-root sample must NOT be treated as a valid clean twin.
      if (
        row.name === 'EffectCommandV1Schema' ||
        row.name === 'EffectObservationV1Schema' ||
        row.name === 'EffectReceiptV1Schema' ||
        row.name === 'WorkflowEffectLedgerEventV1Schema' ||
        row.name === 'WorkflowExecutionEventV2Schema'
      ) {
        expect(
          row.schema.safeParse({ s: 'ok' }).success,
          `${row.name}:old-invalid-root-not-clean`,
        ).toBe(false)
      }
      const bad = row.schema.safeParse(row.mutate(row.clean))
      expect(bad.success, `${row.name}:mut`).toBe(false)
      const actual = (bad.error?.issues ?? []).map((i) => ({
        message: i.message,
        path: [...i.path],
      }))
      expect(actual, `${row.name}:exact-issue-tuples`).toEqual(row.expectedIssues)
    }
  })
})

describe('4A.5B semantic relation matrix parity', () => {
  it('covers SSOT relations with accept/reject and zero PY/TS mismatch (read-only parity)', async () => {
    const { readFileSync } = await import('node:fs')
    const { dirname, join } = await import('node:path')
    const { fileURLToPath } = await import('node:url')
    const {
      CROSS_EVENT_RELATION_MANIFEST_V1,
      INTRA_EVENT_RELATION_MANIFEST_V1,
      mapWorkflowEffectLedgerTsReason,
    } = await import('./effect-ledger-relation-manifests')
    const here = dirname(fileURLToPath(import.meta.url))
    const ssot = JSON.parse(
      readFileSync(
        join(here, '../../../tests/workflow_kernel/fixtures/workflow-effect-ledger-semantic-relations-v1.json'),
        'utf8',
      ),
    ) as {
      relations: Array<{ relation_id: string; event_type: string; expected_reason_class: string }>
      path_inventory: Array<{ event_type: string; path: string; classification: string }>
      cross_event_relations: Array<{ relation_id: string; focused_test_id: string }>
      envelope_fields: string[]
    }
    const corpus = JSON.parse(
      readFileSync(join(here, '../../../tests/workflow_kernel/fixtures/effect-contract-shared-corpus.json'), 'utf8'),
    ) as {
      cases: Array<{
        id: string
        kind: string
        expect: 'accept' | 'reject'
        relation_id?: string
        event_type?: string
        expected_reason_class?: string | null
        mutated_path?: string | null
        bookkeeping_paths?: string[]
        value: unknown
      }>
      semantic_relation_matrix?: { relation_count?: number; cases_added?: number }
    }
    const relIds = ssot.relations.map((r) => r.relation_id).sort()
    expect(new Set(relIds).size).toBe(relIds.length)
    expect(relIds).toEqual([...INTRA_EVENT_RELATION_MANIFEST_V1].sort())
    const crossIds = ssot.cross_event_relations.map((r) => r.relation_id).sort()
    expect(crossIds).toEqual([...CROSS_EVENT_RELATION_MANIFEST_V1].sort())
    for (const x of ssot.cross_event_relations) {
      expect(x.focused_test_id).toBe(`test_cross_event_relation_enforced[${x.relation_id}]`)
    }
    expect(ssot.path_inventory.length).toBeGreaterThanOrEqual(307)
    // Exhaustive inventory presence: envelope fields × 8 events must appear.
    const inv = new Set(ssot.path_inventory.map((p) => `${p.event_type}:${p.path}`))
    for (const et of [
      'claimed',
      'intent_recorded',
      'observation_recorded',
      'reconciled',
      'receipt_terminal',
      'compensation_intent',
      'compensation_observation',
      'compensation_receipt',
    ]) {
      for (const f of ssot.envelope_fields) {
        expect(inv.has(`${et}:${f}`), `${et}:${f}`).toBe(true)
      }
    }
    // EffectTargetV1 children under the five embedded targets
    for (const p of [
      'receipt_terminal:payload.receipt.target.target_kind',
      'receipt_terminal:payload.receipt.target.target_ref',
      'compensation_intent:payload.original_receipt.target.target_kind',
      'compensation_intent:payload.original_receipt.target.target_ref',
      'compensation_observation:payload.original_receipt.target.target_kind',
      'compensation_observation:payload.original_receipt.target.target_ref',
      'compensation_receipt:payload.receipt.target.target_kind',
      'compensation_receipt:payload.receipt.target.target_ref',
      'compensation_receipt:payload.original_receipt.target.target_kind',
      'compensation_receipt:payload.original_receipt.target.target_ref',
    ]) {
      expect(inv.has(p), p).toBe(true)
    }
    // Future field additions that appear in a live accept twin payload root must already be inventoried.
    const matrix = corpus.cases.filter((c) => String(c.id).startsWith('rel_'))
    const byRel = new Map<string, { accept?: (typeof matrix)[number]; reject?: (typeof matrix)[number] }>()
    for (const c of matrix) {
      const rid = c.relation_id!
      const slot = byRel.get(rid) ?? {}
      slot[c.expect] = c
      byRel.set(rid, slot)
    }
    expect([...byRel.keys()].sort()).toEqual(relIds)
    expect(corpus.semantic_relation_matrix?.relation_count).toBe(relIds.length)
    expect(corpus.semantic_relation_matrix?.cases_added).toBe(relIds.length * 2)
    for (const rid of relIds) {
      const pair = byRel.get(rid)!
      const acc = materializeSharedCorpusValue(pair.accept!.value) as Record<string, unknown>
      for (const key of Object.keys(acc)) {
        expect(inv.has(`${pair.accept!.event_type}:${key}`) || ssot.envelope_fields.includes(key)).toBe(true)
      }
    }

    const parityPath = join(
      here,
      '../../../tests/workflow_kernel/fixtures/archives/semantic-relation-parity-4a5b.json',
    )
    const before = readFileSync(parityPath)
    const existing = JSON.parse(before.toString('utf8')) as {
      python_rows?: Array<{
        relation_id: string
        expected: string
        python_actual: string | null
        ts_actual?: string | null
        event_type: string
      }>
      relation_count?: number
      mismatch_count?: number
    }
    const pyRows = existing.python_rows ?? []
    expect(pyRows.length).toBe(relIds.length)
    expect(pyRows.length).toBeGreaterThanOrEqual(81)
    expect(pyRows.every((r) => r.python_actual != null)).toBe(true)
    expect(pyRows.every((r) => r.ts_actual != null)).toBe(true)
    const pyByRel = new Map(pyRows.map((r) => [r.relation_id, r]))
    expect([...pyByRel.keys()].sort()).toEqual(relIds)

    let mismatches = 0
    for (const rid of relIds) {
      const pair = byRel.get(rid)!
      expect(pair.accept, rid).toBeTruthy()
      expect(pair.reject, rid).toBeTruthy()
      const acceptValue = materializeSharedCorpusValue(pair.accept!.value)
      expect(() => parseWorkflowEffectLedgerEventV1(acceptValue)).not.toThrow()
      let tsReason = 'payload_invalid'
      try {
        parseWorkflowEffectLedgerEventV1(materializeSharedCorpusValue(pair.reject!.value))
        throw new Error(`TS accepted reject case for ${rid}`)
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err)
        if (message.startsWith('TS accepted')) throw err
        tsReason = mapWorkflowEffectLedgerTsReason(err)
      }
      const expected = pair.reject!.expected_reason_class || 'payload_invalid'
      expect(tsReason, rid).toBe(expected)
      const py = pyByRel.get(rid)!
      expect(py.python_actual, rid).not.toBeNull()
      expect(py.ts_actual, rid).toBe(tsReason)
      if (py.python_actual !== tsReason || py.python_actual !== expected || tsReason !== expected) {
        mismatches += 1
      }
    }
    expect(mismatches).toBe(0)
    const recomputed = pyRows.filter(
      (r) =>
        r.python_actual !== r.expected ||
        r.ts_actual !== r.expected ||
        r.python_actual !== r.ts_actual,
    ).length
    expect(existing.mismatch_count).toBe(recomputed)
    expect(existing.relation_count).toBe(relIds.length)
    // Read-only: fixture bytes unchanged.
    expect(readFileSync(parityPath).equals(before)).toBe(true)
  })

  it('maps one-field wrapper classification and envelope event_type via shared helper', async () => {
    const { readFileSync } = await import('node:fs')
    const { dirname, join } = await import('node:path')
    const { fileURLToPath } = await import('node:url')
    const { mapWorkflowEffectLedgerTsReason } = await import('./effect-ledger-relation-manifests')
    const here = dirname(fileURLToPath(import.meta.url))
    const corpus = JSON.parse(
      readFileSync(join(here, '../../../tests/workflow_kernel/fixtures/effect-contract-shared-corpus.json'), 'utf8'),
    ) as { cases: Array<{ id: string; value: unknown }> }
    const acc = corpus.cases.find(
      (c) => c.id === 'rel_observation_recorded.wrapper_classification_eq_body_accept',
    )!
    // ONLY wrapper payload.classification
    {
      const val = materializeSharedCorpusValue(structuredClone(acc.value)) as Record<string, any>
      val.payload = { ...val.payload }
      val.payload.classification = 'bogus'
      const body = { ...val }
      delete body.event_id
      val.event_id = deriveWorkflowEffectLedgerEventIdV1(body)
      try {
        parseWorkflowEffectLedgerEventV1(val)
        throw new Error('expected throw')
      } catch (err) {
        if (err instanceof Error && err.message === 'expected throw') throw err
        expect(mapWorkflowEffectLedgerTsReason(err)).toBe('payload_invalid')
      }
    }
    // envelope event_type invalid → schema_invalid
    {
      const val = materializeSharedCorpusValue(structuredClone(acc.value)) as Record<string, any>
      val.event_type = 'not_a_ledger_event'
      const body = { ...val }
      delete body.event_id
      try {
        val.event_id = deriveWorkflowEffectLedgerEventIdV1(body)
      } catch {
        // derive may still work on body
      }
      try {
        parseWorkflowEffectLedgerEventV1(val)
        throw new Error('expected throw')
      } catch (err) {
        if (err instanceof Error && err.message === 'expected throw') throw err
        expect(mapWorkflowEffectLedgerTsReason(err)).toBe('schema_invalid')
      }
    }
    // unsupported schema_version → schema_invalid
    {
      const val = materializeSharedCorpusValue(structuredClone(acc.value)) as Record<string, any>
      val.schema_version = 'nope'
      const body = { ...val }
      delete body.event_id
      try {
        val.event_id = deriveWorkflowEffectLedgerEventIdV1(body)
      } catch {
        // ignore
      }
      try {
        parseWorkflowEffectLedgerEventV1(val)
        throw new Error('expected throw')
      } catch (err) {
        if (err instanceof Error && err.message === 'expected throw') throw err
        expect(mapWorkflowEffectLedgerTsReason(err)).toBe('schema_invalid')
      }
    }
  })
})
