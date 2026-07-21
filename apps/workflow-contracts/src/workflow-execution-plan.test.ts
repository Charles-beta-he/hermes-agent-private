import { describe, expect, it } from 'vitest'

import cases from '../../../tests/contracts/fixtures/workflow-execution-plan-v1/cases.json'
import integralNumberPlan from '../../../tests/contracts/fixtures/workflow-execution-plan-v1/integral-number-plan.json'
import generatedSchema from '../../../schemas/workflow-execution-plan-v1.schema.json'
import {
  deriveWorkflowExecutionPlanDigestV1,
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

  it('derives the shared canonical plan digest without self-reference', () => {
    const plan = cases[0].value
    expect(deriveWorkflowExecutionPlanDigestV1(plan)).toBe(plan.plan_digest)
  })

  it('does not depend on locale-sensitive key ordering', () => {
    const plan = cases[0].value
    const originalLocaleCompare = String.prototype.localeCompare
    String.prototype.localeCompare = () => {
      throw new Error('canonical ordering must not call localeCompare')
    }
    try {
      expect(deriveWorkflowExecutionPlanDigestV1(plan)).toBe(plan.plan_digest)
    } finally {
      String.prototype.localeCompare = originalLocaleCompare
    }
  })

  it('matches Python for schema-valid integral JSON numeric syntax', () => {
    expect(integralNumberPlan.source_numeric_syntax).toEqual({
      version: 1,
      max_iterations: 3,
      repeated_failure_limit: 2,
    })
    expect(deriveWorkflowExecutionPlanDigestV1(integralNumberPlan.value)).toBe(
      integralNumberPlan.value.plan_digest,
    )
  })

  it('keeps the checked-in JSON Schema generated from Zod', () => {
    expect(toWorkflowExecutionPlanV1JsonSchema()).toEqual(generatedSchema)
  })
})
