import { describe, expect, expectTypeOf, it } from 'vitest'

import cases from '../../../tests/contracts/fixtures/closure-v1/cases.json'
import generatedSchema from '../../../schemas/closure-v1.schema.json'
import {
  type ClosureV1,
  ClosureV1Schema,
  toClosureV1JsonSchema,
} from './closure'

const validCase = cases.find((testCase) => testCase.valid)!

describe('ClosureV1Schema', () => {
  for (const testCase of cases) {
    it(testCase.name, () => {
      expect(ClosureV1Schema.safeParse(testCase.value).success).toBe(
        testCase.valid,
      )
    })
  }

  it('infers ClosureV1 from the runtime schema', () => {
    expectTypeOf(ClosureV1Schema.parse(validCase.value)).toEqualTypeOf<ClosureV1>()
  })

  it('keeps the checked-in JSON Schema generated from Zod', () => {
    expect(toClosureV1JsonSchema()).toEqual(generatedSchema)
  })
})
