import { createHash } from "node:crypto";

import { z } from "zod";

import { DelegationExpansionProjectionV1Schema } from "./delegation-expansion-proposal";
import { WorkflowIdentifierV1Schema } from "./workflow-definition";

const Sha256DigestV1Schema = z.string().regex(/^sha256:[0-9a-f]{64}$/);

export const DelegationTreeBudgetSnapshotV1Schema = z
  .strictObject({
    schema_version: z.literal("hermes_delegation_tree_budget_snapshot_v1"),
    tree_budget_id: WorkflowIdentifierV1Schema,
    snapshot_revision: z.number().int().positive(),
    remaining_node_capacity: z.number().int().min(0).max(1_000_000),
  })
  .meta({ title: "Hermes DelegationTreeBudgetSnapshotV1" });

const DelegationTreeBudgetPreflightShapeV1Schema = z
  .strictObject({
    schema_version: z.literal("hermes_delegation_tree_budget_preflight_v1"),
    tree_budget_id: WorkflowIdentifierV1Schema,
    snapshot_revision: z.number().int().positive(),
    budget_snapshot_digest: Sha256DigestV1Schema,
    proposal_digest: Sha256DigestV1Schema,
    requested_child_nodes: z.number().int().positive().max(256),
    capacity_fit_at_snapshot: z.boolean(),
    reason_codes: z.tuple([
      z.enum([
        "tree_capacity_available_at_snapshot",
        "tree_capacity_insufficient_at_snapshot",
      ]),
    ]),
    authority_granted: z.literal(false),
    executable: z.literal(false),
    reservation_committed: z.literal(false),
    ledger_mutated: z.literal(false),
  })
  .meta({ title: "Hermes DelegationTreeBudgetPreflightV1" });

export const DelegationTreeBudgetPreflightV1Schema =
  DelegationTreeBudgetPreflightShapeV1Schema.superRefine(
    (preflight, context) => {
      const expected = preflight.capacity_fit_at_snapshot
        ? "tree_capacity_available_at_snapshot"
        : "tree_capacity_insufficient_at_snapshot";
      if (preflight.reason_codes[0] !== expected) {
        context.addIssue({
          code: "custom",
          message: "reason_codes must match capacity_fit_at_snapshot",
          path: ["reason_codes"],
        });
      }
    },
  );

export type DelegationTreeBudgetSnapshotV1 = z.infer<
  typeof DelegationTreeBudgetSnapshotV1Schema
>;
export type DelegationTreeBudgetPreflightV1 = z.infer<
  typeof DelegationTreeBudgetPreflightV1Schema
>;

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).sort(
      ([left], [right]) => (left < right ? -1 : left > right ? 1 : 0),
    );
    return `{${entries
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function digest(value: unknown): string {
  return `sha256:${createHash("sha256").update(canonicalJson(value)).digest("hex")}`;
}

export function evaluateDelegationTreeBudgetPreflightV1(
  snapshotInput: unknown,
  expansionProjectionInput: unknown,
): DelegationTreeBudgetPreflightV1 {
  const snapshot = DelegationTreeBudgetSnapshotV1Schema.parse(snapshotInput);
  const projection = DelegationExpansionProjectionV1Schema.parse(
    expansionProjectionInput,
  );
  const normalizedProposal = {
    schema_version: "delegation_expansion_proposal_v1",
    plan_digest: projection.plan_digest,
    child_task_ids: [...projection.child_task_ids].sort(),
    synthesis_task_id: projection.synthesis_task_id,
  };
  if (digest(normalizedProposal) !== projection.proposal_digest) {
    throw new Error(
      "proposal_digest_mismatch: projection is not bound to its normalized Phase 2 proposal",
    );
  }
  const fits =
    projection.requested_child_nodes <= snapshot.remaining_node_capacity;
  return DelegationTreeBudgetPreflightV1Schema.parse({
    schema_version: "hermes_delegation_tree_budget_preflight_v1",
    tree_budget_id: snapshot.tree_budget_id,
    snapshot_revision: snapshot.snapshot_revision,
    budget_snapshot_digest: digest(snapshot),
    proposal_digest: projection.proposal_digest,
    requested_child_nodes: projection.requested_child_nodes,
    capacity_fit_at_snapshot: fits,
    reason_codes: [
      fits
        ? "tree_capacity_available_at_snapshot"
        : "tree_capacity_insufficient_at_snapshot",
    ],
    authority_granted: false,
    executable: false,
    reservation_committed: false,
    ledger_mutated: false,
  });
}

function addPreflightReasonRelation(schema: Record<string, unknown>): void {
  schema.oneOf = [
    {
      properties: {
        capacity_fit_at_snapshot: { const: true },
        reason_codes: {
          const: ["tree_capacity_available_at_snapshot"],
        },
      },
      required: ["capacity_fit_at_snapshot", "reason_codes"],
    },
    {
      properties: {
        capacity_fit_at_snapshot: { const: false },
        reason_codes: {
          const: ["tree_capacity_insufficient_at_snapshot"],
        },
      },
      required: ["capacity_fit_at_snapshot", "reason_codes"],
    },
  ];
}

export function toDelegationTreeBudgetSnapshotV1JsonSchema(): Record<
  string,
  unknown
> {
  return {
    $id: "https://hermes-agent.local/schemas/delegation-tree-budget-snapshot-v1.schema.json",
    ...z.toJSONSchema(DelegationTreeBudgetSnapshotV1Schema, {
      target: "draft-2020-12",
    }),
  };
}

export function toDelegationTreeBudgetPreflightV1JsonSchema(): Record<
  string,
  unknown
> {
  const schema = z.toJSONSchema(DelegationTreeBudgetPreflightShapeV1Schema, {
    target: "draft-2020-12",
  }) as Record<string, unknown>;
  addPreflightReasonRelation(schema);
  return {
    $id: "https://hermes-agent.local/schemas/delegation-tree-budget-preflight-v1.schema.json",
    ...schema,
  };
}
