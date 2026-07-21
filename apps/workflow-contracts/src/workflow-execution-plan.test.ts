import { describe, expect, it } from 'vitest'

import cases from '../../../tests/contracts/fixtures/workflow-execution-plan-v1/cases.json'
import generatedSchema from '../../../schemas/workflow-execution-plan-v1.schema.json'
import {
  toWorkflowExecutionPlanV1JsonSchema,
  WorkflowExecutionPlanV1Schema,
} from './workflow-execution-plan'

describe('WorkflowExecutionPlanV1', () => {
  for (const testCase of cases) {
    it(`shared fixture: ${testCase.name}`, () => {
      expect(WorkflowExecutionPlanV1Schema.safeParse(testCase.value).success).toBe(
        testCase.valid,
      )
    })
  }

  it('keeps the checked-in JSON Schema generated from Zod', () => {
    expect(toWorkflowExecutionPlanV1JsonSchema()).toEqual(generatedSchema)
  })
})
