import { describe, expect, it } from 'vitest'

import cases from '../../../tests/contracts/fixtures/delegation-expansion-proposal-v1/cases.json'
import generatedProjectionSchema from '../../../schemas/delegation-expansion-projection-v1.schema.json'
import generatedSchema from '../../../schemas/delegation-expansion-proposal-v1.schema.json'
import {
  DelegationExpansionProjectionV1Schema,
  DelegationExpansionProposalV1Schema,
  toDelegationExpansionProjectionV1JsonSchema,
  toDelegationExpansionProposalV1JsonSchema,
} from './delegation-expansion-proposal'

describe('DelegationExpansionProposalV1', () => {
  for (const testCase of cases) {
    it(`shared fixture: ${testCase.name}`, () => {
      expect(
        DelegationExpansionProposalV1Schema.safeParse(testCase.value).success,
      ).toBe(testCase.valid)
    })
  }

  it('keeps the checked-in JSON Schema generated from Zod', () => {
    expect(toDelegationExpansionProposalV1JsonSchema()).toEqual(generatedSchema)
  })

  it('contains no authority, budget, execution, or graph-definition field', () => {
    const keys = Object.keys(
      DelegationExpansionProposalV1Schema.parse(cases[0]!.value),
    ).sort()
    expect(keys).toEqual([
      'child_task_ids',
      'plan_digest',
      'schema_version',
      'synthesis_task_id',
    ])
  })

  it('keeps the authority-neutral projection schema generated from Zod', () => {
    expect(toDelegationExpansionProjectionV1JsonSchema()).toEqual(
      generatedProjectionSchema,
    )
  })

  it('rejects a projection whose derived child count drifts', () => {
    const result = DelegationExpansionProjectionV1Schema.safeParse({
      schema_version: 'delegation_expansion_projection_v1',
      definition_digest: `sha256:${'a'.repeat(64)}`,
      plan_digest: `sha256:${'b'.repeat(64)}`,
      proposal_digest: `sha256:${'c'.repeat(64)}`,
      authority_granted: false,
      executable: false,
      child_task_ids: ['plan', 'plan-b'],
      synthesis_task_id: 'implement',
      requested_child_nodes: 1,
    })
    expect(result.success).toBe(false)
  })
})
