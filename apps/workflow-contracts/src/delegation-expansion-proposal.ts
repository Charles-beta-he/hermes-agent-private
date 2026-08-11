import { z } from 'zod'

import { WorkflowIdentifierV1Schema } from './workflow-definition'

const Sha256DigestV1Schema = z.string().regex(/^sha256:[0-9a-f]{64}$/)

const DelegationExpansionProposalShapeV1Schema = z
  .strictObject({
    schema_version: z.literal('delegation_expansion_proposal_v1'),
    plan_digest: Sha256DigestV1Schema,
    child_task_ids: z.array(WorkflowIdentifierV1Schema).min(1).max(256),
    synthesis_task_id: WorkflowIdentifierV1Schema,
  })
  .meta({ title: 'Hermes DelegationExpansionProposalV1' })

const DelegationExpansionProjectionShapeV1Schema = z
  .strictObject({
    schema_version: z.literal('delegation_expansion_projection_v1'),
    definition_digest: Sha256DigestV1Schema,
    plan_digest: Sha256DigestV1Schema,
    proposal_digest: Sha256DigestV1Schema,
    authority_granted: z.literal(false),
    executable: z.literal(false),
    child_task_ids: z.array(WorkflowIdentifierV1Schema).min(1).max(256),
    synthesis_task_id: WorkflowIdentifierV1Schema,
    requested_child_nodes: z.number().int().positive().max(256),
  })
  .meta({ title: 'Hermes DelegationExpansionProjectionV1' })

export const DelegationExpansionProposalV1Schema =
  DelegationExpansionProposalShapeV1Schema.superRefine((proposal, context) => {
    if (new Set(proposal.child_task_ids).size !== proposal.child_task_ids.length) {
      context.addIssue({
        code: 'custom',
        message: 'child_task_ids must be unique',
        path: ['child_task_ids'],
      })
    }
  })

export type DelegationExpansionProposalV1 = z.infer<
  typeof DelegationExpansionProposalV1Schema
>

export const DelegationExpansionProjectionV1Schema =
  DelegationExpansionProjectionShapeV1Schema.superRefine((projection, context) => {
    if (new Set(projection.child_task_ids).size !== projection.child_task_ids.length) {
      context.addIssue({
        code: 'custom',
        message: 'child_task_ids must be unique',
        path: ['child_task_ids'],
      })
    }
    if (projection.requested_child_nodes !== projection.child_task_ids.length) {
      context.addIssue({
        code: 'custom',
        message: 'requested_child_nodes must equal child_task_ids length',
        path: ['requested_child_nodes'],
      })
    }
  })

export type DelegationExpansionProjectionV1 = z.infer<
  typeof DelegationExpansionProjectionV1Schema
>

function addUniqueChildIds(schema: Record<string, unknown>): void {
  const properties = schema.properties as Record<
    string,
    Record<string, unknown>
  >
  properties.child_task_ids!.uniqueItems = true
}

export function toDelegationExpansionProposalV1JsonSchema(): Record<
  string,
  unknown
> {
  const schema = z.toJSONSchema(DelegationExpansionProposalShapeV1Schema, {
    target: 'draft-2020-12',
  }) as Record<string, unknown>
  addUniqueChildIds(schema)
  return {
    $id: 'https://hermes-agent.local/schemas/delegation-expansion-proposal-v1.schema.json',
    ...schema,
  }
}

export function toDelegationExpansionProjectionV1JsonSchema(): Record<
  string,
  unknown
> {
  const schema = z.toJSONSchema(DelegationExpansionProjectionShapeV1Schema, {
    target: 'draft-2020-12',
  }) as Record<string, unknown>
  addUniqueChildIds(schema)
  return {
    $id: 'https://hermes-agent.local/schemas/delegation-expansion-projection-v1.schema.json',
    ...schema,
  }
}
