import { describe, expect, expectTypeOf, it } from 'vitest'

import cases from '../../../tests/contracts/fixtures/verifier-vote-v1/cases.json'
import generatedSchema from '../../../schemas/verifier-vote-v1.schema.json'
import {
  type VerifierVoteV1,
  VerifierVoteV1Schema,
  toVerifierVoteV1JsonSchema,
} from './verifier-vote'

const validCase = cases.find((testCase) => testCase.valid)!

describe('VerifierVoteV1Schema', () => {
  for (const testCase of cases) {
    it(testCase.name, () => {
      expect(VerifierVoteV1Schema.safeParse(testCase.value).success).toBe(
        testCase.valid,
      )
    })
  }

  it('infers VerifierVoteV1 from the runtime schema', () => {
    expectTypeOf(VerifierVoteV1Schema.parse(validCase.value)).toEqualTypeOf<VerifierVoteV1>()
  })

  it('keeps the checked-in JSON Schema generated from Zod', () => {
    expect(toVerifierVoteV1JsonSchema()).toEqual(generatedSchema)
  })
})
