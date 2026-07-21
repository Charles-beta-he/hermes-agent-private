import { describe, expect, it } from 'vitest'

import cases from '../../../tests/contracts/fixtures/workflow-definition-v1/cases.json'
import generatedSchema from '../../../schemas/workflow-definition-v1.schema.json'
import {
  toWorkflowDefinitionV1JsonSchema,
  WorkflowDefinitionV1Schema,
} from './workflow-definition'

describe('WorkflowDefinitionV1', () => {
  for (const testCase of cases) {
    it(`shared fixture: ${testCase.name}`, () => {
      expect(WorkflowDefinitionV1Schema.safeParse(testCase.value).success).toBe(
        testCase.valid,
      )
    })
  }

  it('keeps the checked-in JSON Schema generated from Zod', () => {
    expect(toWorkflowDefinitionV1JsonSchema()).toEqual(generatedSchema)
  })

  it('rejects an authority-grant field at every task boundary', () => {
    const valid = structuredClone(cases[0]!.value)
    ;(valid.tasks[0] as Record<string, unknown>).authority_granted = true
    expect(WorkflowDefinitionV1Schema.safeParse(valid).success).toBe(false)
  })
})
