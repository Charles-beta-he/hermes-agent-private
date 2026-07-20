import { describe, expect, expectTypeOf, it } from 'vitest'

import cases from '../../../tests/contracts/fixtures/evidence-v1/cases.json'
import generatedSchema from '../../../schemas/evidence-v1.schema.json'
import {
  EvidenceV1Schema,
  toEvidenceV1JsonSchema,
  type EvidenceV1,
} from './evidence'

describe('EvidenceV1Schema', () => {
  for (const fixture of cases) {
    it(fixture.name, () => {
      expect(EvidenceV1Schema.safeParse(fixture.value).success).toBe(fixture.valid)
    })
  }

  it('infers the public EvidenceV1 type from the runtime schema', () => {
    const parsed = EvidenceV1Schema.parse(cases[0].value)
    expectTypeOf(parsed).toEqualTypeOf<EvidenceV1>()
  })

  it('keeps the checked-in Draft 2020-12 schema in sync', () => {
    expect(toEvidenceV1JsonSchema()).toEqual(generatedSchema)
  })
})
