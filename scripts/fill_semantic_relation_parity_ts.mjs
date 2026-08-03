#!/usr/bin/env node
/**
 * Check TypeScript columns of semantic-relation-parity-4a5b.json.
 * Ordinary usage is check-only. Direct write/migration mode is closed in this slice.
 * Used by scripts/generate_workflow_effect_ledger_semantic_ssot_4a5b2.py --check/--write
 * (Python ordinary --write is validation-only zero-write).
 */
import { readFileSync, writeFileSync, existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  parseWorkflowEffectLedgerEventV1,
  deriveWorkflowEffectLedgerEventIdV1,
  canonicalDigestV1,
} from '../apps/workflow-contracts/src/workflow-effect.ts'
import {
  CROSS_EVENT_RELATION_MANIFEST_V1,
  INTRA_EVENT_RELATION_MANIFEST_V1,
  mapWorkflowEffectLedgerTsReason,
} from '../apps/workflow-contracts/src/effect-ledger-relation-manifests.ts'

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..')
const wantsWrite = process.argv.includes('--write')
const mode = 'check'
if (wantsWrite) {
  console.error('TS parity helper ordinary write/migration mode is closed; running check-only')
}

function materializeSharedCorpusValue(node) {
  if (node && typeof node === 'object' && !Array.isArray(node)) {
    const keys = Object.keys(node)
    if (keys.length === 1 && keys[0] === '$materialize_utf16_units') {
      return String.fromCharCode(...node.$materialize_utf16_units.map(Number))
    }
    if (keys.length === 1 && keys[0] === '$materialize_utf16_key_entry') {
      const spec = node.$materialize_utf16_key_entry
      const key = String.fromCharCode(...spec.key_units.map(Number))
      return { [key]: materializeSharedCorpusValue(spec.value) }
    }
    if (keys.length === 1 && keys[0] === '$materialize_merge_utf16_key') {
      const spec = node.$materialize_merge_utf16_key
      const base = materializeSharedCorpusValue(spec.base)
      const key = String.fromCharCode(...spec.key_units.map(Number))
      return { ...base, [key]: materializeSharedCorpusValue(spec.value) }
    }
    const out = {}
    for (const [k, v] of Object.entries(node)) out[k] = materializeSharedCorpusValue(v)
    return out
  }
  if (Array.isArray(node)) return node.map(materializeSharedCorpusValue)
  return node
}

const ssot = JSON.parse(
  readFileSync(join(root, 'tests/workflow_kernel/fixtures/workflow-effect-ledger-semantic-relations-v1.json'), 'utf8'),
)
const corpus = JSON.parse(
  readFileSync(join(root, 'tests/workflow_kernel/fixtures/effect-contract-shared-corpus.json'), 'utf8'),
)
const parityPath = join(root, 'tests/workflow_kernel/fixtures/archives/semantic-relation-parity-4a5b.json')
if (!existsSync(parityPath)) {
  console.error('missing parity artifact')
  process.exit(1)
}
const parity = JSON.parse(readFileSync(parityPath, 'utf8'))
const relIds = ssot.relations.map((r) => r.relation_id).sort()
const ssotCross = (ssot.cross_event_relations || []).map((r) => r.relation_id).sort()
const intraManifest = [...INTRA_EVENT_RELATION_MANIFEST_V1].sort()
const crossManifest = [...CROSS_EVENT_RELATION_MANIFEST_V1].sort()
if (JSON.stringify(relIds) !== JSON.stringify(intraManifest)) {
  console.error('TS intra manifest drift', { relIds: relIds.length, manifest: intraManifest.length })
  process.exit(1)
}
if (JSON.stringify(ssotCross) !== JSON.stringify(crossManifest)) {
  console.error('TS cross manifest drift', { ssotCross, crossManifest })
  process.exit(1)
}

const matrix = corpus.cases.filter((c) => String(c.id).startsWith('rel_'))
const byRel = new Map()
for (const c of matrix) {
  const slot = byRel.get(c.relation_id) ?? {}
  slot[c.expect] = c
  byRel.set(c.relation_id, slot)
}

const pyBy = new Map((parity.python_rows || []).map((r) => [r.relation_id, r]))
const rows = []
let mismatches = 0
for (const rid of relIds) {
  const pair = byRel.get(rid)
  if (!pair?.accept || !pair?.reject) {
    console.error('missing twins', rid)
    process.exit(1)
  }
  parseWorkflowEffectLedgerEventV1(materializeSharedCorpusValue(pair.accept.value))
  let tsReason = 'payload_invalid'
  try {
    parseWorkflowEffectLedgerEventV1(materializeSharedCorpusValue(pair.reject.value))
    console.error('TS accepted reject', rid)
    process.exit(1)
  } catch (err) {
    tsReason = mapWorkflowEffectLedgerTsReason(err)
  }
  const expected = pair.reject.expected_reason_class || 'payload_invalid'
  if (tsReason !== expected) {
    console.error('TS reason mismatch', rid, tsReason, expected)
    process.exit(1)
  }
  const py = pyBy.get(rid)
  const pythonActual = py?.python_actual
  if (pythonActual == null) {
    console.error('missing python_actual', rid)
    process.exit(1)
  }
  if (pythonActual !== tsReason || pythonActual !== expected) mismatches += 1
  rows.push({
    event_type: pair.reject.event_type || ssot.relations.find((r) => r.relation_id === rid).event_type,
    relation_id: rid,
    expected,
    python_actual: pythonActual,
    ts_actual: tsReason,
  })
}

const next = {
  schema_version: 'semantic_relation_parity_v1',
  relation_count: relIds.length,
  mismatch_count: mismatches,
  python_rows: rows,
  rows,
  generator: 'scripts/generate_workflow_effect_ledger_semantic_ssot_4a5b2.py + fill_semantic_relation_parity_ts.mjs',
}

if (mode === 'check') {
  const cur = parity
  const curRows = cur.python_rows || cur.rows || []
  if (curRows.length !== rows.length) {
    console.error('parity row count drift', curRows.length, rows.length)
    process.exit(1)
  }
  if (curRows.length < 81) {
    console.error('parity row count below minimum 81', curRows.length)
    process.exit(1)
  }
  for (const r of rows) {
    const c = curRows.find((x) => x.relation_id === r.relation_id)
    if (!c || c.ts_actual !== r.ts_actual || c.python_actual !== r.python_actual) {
      console.error('parity drift', r.relation_id, c, r)
      process.exit(1)
    }
    if (c.ts_actual == null || c.python_actual == null) {
      console.error('null runtime row', r.relation_id)
      process.exit(1)
    }
  }
  const recomputed = rows.filter(
    (r) => r.python_actual !== r.expected || r.ts_actual !== r.expected || r.python_actual !== r.ts_actual,
  ).length
  if (Number(cur.mismatch_count) !== recomputed) {
    console.error('mismatch_count drift', cur.mismatch_count, recomputed)
    process.exit(1)
  }
  console.log(JSON.stringify({ check: 'ok', relation_count: relIds.length, mismatch_count: recomputed }))
  process.exit(0)
}

// Write/migration mode closed in this slice.
console.error('TS parity write mode closed')
process.exit(2)
void deriveWorkflowEffectLedgerEventIdV1
void canonicalDigestV1
