import { describe, expect, expectTypeOf, it } from 'vitest'

import cases from '../../../tests/contracts/fixtures/workflow-run-transition-v1/cases.json'
import generatedSchema from '../../../schemas/workflow-run-transition-v1.schema.json'
import {
  toWorkflowRunTransitionV1JsonSchema,
  type WorkflowRunTransitionV1,
  WorkflowRunTransitionV1Schema,
} from './workflow-run-transition'

describe('WorkflowRunTransitionV1Schema', () => {
  for (const testCase of cases) {
    it(testCase.name, () => {
      expect(WorkflowRunTransitionV1Schema.safeParse(testCase.value).success).toBe(
        testCase.valid,
      )
    })
  }

  it('infers only adjacent runtime-packet transitions', () => {
    expectTypeOf<WorkflowRunTransitionV1>().toMatchTypeOf<{
      from: 'proposed' | 'claimed' | 'running' | 'review'
      to: 'claimed' | 'running' | 'review' | 'done'
    }>()
  })

  it('keeps the checked-in JSON Schema generated from Zod', () => {
    expect(toWorkflowRunTransitionV1JsonSchema()).toEqual(generatedSchema)
  })
})
