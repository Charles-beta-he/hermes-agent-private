import { describe, expect, expectTypeOf, it } from 'vitest'

import cases from '../../../tests/contracts/fixtures/workflow-run-status-v1/cases.json'
import generatedSchema from '../../../schemas/workflow-run-status-v1.schema.json'
import {
  toWorkflowRunStatusV1JsonSchema,
  type WorkflowRunStatusV1,
  WorkflowRunStatusV1Schema,
} from './workflow-run-status'

describe('WorkflowRunStatusV1Schema', () => {
  for (const testCase of cases) {
    it(testCase.name, () => {
      expect(WorkflowRunStatusV1Schema.safeParse(testCase.value).success).toBe(
        testCase.valid,
      )
    })
  }

  it('infers only the observed runtime-packet lifecycle', () => {
    expectTypeOf<WorkflowRunStatusV1>().toEqualTypeOf<
      'proposed' | 'claimed' | 'running' | 'review' | 'done'
    >()
  })

  it('keeps the checked-in JSON Schema generated from Zod', () => {
    expect(toWorkflowRunStatusV1JsonSchema()).toEqual(generatedSchema)
  })
})
