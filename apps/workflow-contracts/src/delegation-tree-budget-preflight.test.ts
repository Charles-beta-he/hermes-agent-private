import { describe, expect, it } from "vitest";

import cases from "../../../tests/contracts/fixtures/delegation-tree-budget-preflight-v1/cases.json";
import generatedPreflightSchema from "../../../schemas/delegation-tree-budget-preflight-v1.schema.json";
import generatedSnapshotSchema from "../../../schemas/delegation-tree-budget-snapshot-v1.schema.json";
import {
  DelegationTreeBudgetPreflightV1Schema,
  DelegationTreeBudgetSnapshotV1Schema,
  evaluateDelegationTreeBudgetPreflightV1,
  toDelegationTreeBudgetPreflightV1JsonSchema,
  toDelegationTreeBudgetSnapshotV1JsonSchema,
} from "./delegation-tree-budget-preflight";

const firstCase = cases[0]!;
const secondCase = cases[1]!;

function clone<T>(value: T): T {
  return structuredClone(value);
}

describe("DelegationTreeBudgetPreflightV1", () => {
  it("matches the shared exact-fit fixture", () => {
    expect(
      evaluateDelegationTreeBudgetPreflightV1(
        firstCase.snapshot,
        firstCase.projection,
      ),
    ).toEqual(firstCase.expected);
  });

  it("returns deterministic insufficiency for zero capacity", () => {
    const result = evaluateDelegationTreeBudgetPreflightV1(
      secondCase.snapshot,
      secondCase.projection,
    );

    expect(result.capacity_fit_at_snapshot).toBe(false);
    expect(result.reason_codes).toEqual([
      "tree_capacity_insufficient_at_snapshot",
    ]);
    expect(result.reservation_committed).toBe(false);
    expect(result.ledger_mutated).toBe(false);
  });

  it("can fit the same snapshot twice without claiming reservation", () => {
    const first = evaluateDelegationTreeBudgetPreflightV1(
      firstCase.snapshot,
      firstCase.projection,
    );
    const second = evaluateDelegationTreeBudgetPreflightV1(
      firstCase.snapshot,
      firstCase.projection,
    );

    expect(first).toEqual(second);
    expect(first.reservation_committed).toBe(false);
    expect(first).not.toHaveProperty("remaining_after");
    expect(first).not.toHaveProperty("ledger_delta");
  });

  it("changes snapshot binding when revision or capacity changes", () => {
    const baseline = evaluateDelegationTreeBudgetPreflightV1(
      firstCase.snapshot,
      firstCase.projection,
    );
    const revised = evaluateDelegationTreeBudgetPreflightV1(
      {
        ...firstCase.snapshot,
        snapshot_revision: firstCase.snapshot.snapshot_revision + 1,
      },
      firstCase.projection,
    );
    const reduced = evaluateDelegationTreeBudgetPreflightV1(
      {
        ...firstCase.snapshot,
        remaining_node_capacity: firstCase.snapshot.remaining_node_capacity - 1,
      },
      firstCase.projection,
    );

    expect(revised.budget_snapshot_digest).not.toBe(
      baseline.budget_snapshot_digest,
    );
    expect(reduced.budget_snapshot_digest).not.toBe(
      baseline.budget_snapshot_digest,
    );
  });

  it("preserves Phase 2 proposal identity across child order", () => {
    const projection = clone(firstCase.projection);
    projection.child_task_ids.reverse();

    expect(
      evaluateDelegationTreeBudgetPreflightV1(firstCase.snapshot, projection),
    ).toEqual(
      evaluateDelegationTreeBudgetPreflightV1(
        firstCase.snapshot,
        firstCase.projection,
      ),
    );
  });

  it("binds changed child identity even when demand count stays equal", () => {
    const changed = clone(firstCase.projection);
    changed.child_task_ids = ["plan", "plan-c"];
    changed.proposal_digest =
      "sha256:02ae941d4fb023784e47849ed6452385942594c2f71ad450aaadf466793d8675";

    const baseline = evaluateDelegationTreeBudgetPreflightV1(
      firstCase.snapshot,
      firstCase.projection,
    );
    const changedResult = evaluateDelegationTreeBudgetPreflightV1(
      firstCase.snapshot,
      changed,
    );

    expect(changedResult.requested_child_nodes).toBe(
      baseline.requested_child_nodes,
    );
    expect(changedResult.proposal_digest).not.toBe(baseline.proposal_digest);
  });

  it.each([
    ["snapshot_revision", true],
    ["snapshot_revision", 1.5],
    ["snapshot_revision", "1"],
    ["remaining_node_capacity", false],
    ["remaining_node_capacity", 2.5],
    ["remaining_node_capacity", "2"],
  ])("rejects malformed snapshot integer %s=%j", (field, value) => {
    const snapshot = { ...firstCase.snapshot, [field]: value };
    expect(() =>
      evaluateDelegationTreeBudgetPreflightV1(snapshot, firstCase.projection),
    ).toThrow();
  });

  it.each([
    ["snapshot_revision", Number.NaN],
    ["snapshot_revision", Number.POSITIVE_INFINITY],
    ["snapshot_revision", Number.MAX_SAFE_INTEGER + 1],
    ["remaining_node_capacity", Number.NEGATIVE_INFINITY],
  ])("rejects non-finite or unsafe snapshot number %s", (field, value) => {
    const snapshot = { ...firstCase.snapshot, [field]: value };
    expect(() =>
      evaluateDelegationTreeBudgetPreflightV1(snapshot, firstCase.projection),
    ).toThrow();
  });

  it("uses one identity for integral numeric representations", () => {
    const snapshot = {
      ...firstCase.snapshot,
      snapshot_revision: 7.0,
      remaining_node_capacity: 2.0,
    };
    expect(
      evaluateDelegationTreeBudgetPreflightV1(snapshot, firstCase.projection),
    ).toEqual(firstCase.expected);
  });

  it.each([
    ["tree_budget_id", ""],
    ["snapshot_revision", 0],
    ["remaining_node_capacity", -1],
    ["remaining_node_capacity", 1_000_001],
  ])("rejects out-of-contract snapshot %s=%j", (field, value) => {
    const snapshot = { ...firstCase.snapshot, [field]: value };
    expect(() =>
      evaluateDelegationTreeBudgetPreflightV1(snapshot, firstCase.projection),
    ).toThrow();
  });

  it("rejects unknown snapshot and projection fields", () => {
    expect(() =>
      evaluateDelegationTreeBudgetPreflightV1(
        { ...firstCase.snapshot, authority_granted: true },
        firstCase.projection,
      ),
    ).toThrow();
    expect(() =>
      evaluateDelegationTreeBudgetPreflightV1(firstCase.snapshot, {
        ...firstCase.projection,
        remaining_node_capacity: 99,
      }),
    ).toThrow();
  });

  it("rejects forged count, duplicate children, authority, and proposal digest", () => {
    expect(() =>
      evaluateDelegationTreeBudgetPreflightV1(firstCase.snapshot, {
        ...firstCase.projection,
        requested_child_nodes: 1,
      }),
    ).toThrow();
    for (const requested_child_nodes of [true, "2", 2.5, Number.NaN]) {
      expect(() =>
        evaluateDelegationTreeBudgetPreflightV1(firstCase.snapshot, {
          ...firstCase.projection,
          requested_child_nodes,
        }),
      ).toThrow();
    }
    expect(
      evaluateDelegationTreeBudgetPreflightV1(firstCase.snapshot, {
        ...firstCase.projection,
        requested_child_nodes: 2.0,
      }).requested_child_nodes,
    ).toBe(2);
    expect(() =>
      evaluateDelegationTreeBudgetPreflightV1(firstCase.snapshot, {
        ...firstCase.projection,
        child_task_ids: ["plan", "plan"],
      }),
    ).toThrow();
    expect(() =>
      evaluateDelegationTreeBudgetPreflightV1(firstCase.snapshot, {
        ...firstCase.projection,
        authority_granted: true,
      }),
    ).toThrow();
    expect(() =>
      evaluateDelegationTreeBudgetPreflightV1(firstCase.snapshot, {
        ...firstCase.projection,
        proposal_digest: `sha256:${"0".repeat(64)}`,
      }),
    ).toThrow(/proposal_digest_mismatch/);
  });

  it("rejects output authority/effect claims and reason drift", () => {
    const result = evaluateDelegationTreeBudgetPreflightV1(
      firstCase.snapshot,
      firstCase.projection,
    );
    for (const field of [
      "authority_granted",
      "executable",
      "reservation_committed",
      "ledger_mutated",
    ] as const) {
      expect(
        DelegationTreeBudgetPreflightV1Schema.safeParse({
          ...result,
          [field]: true,
        }).success,
      ).toBe(false);
    }
    expect(
      DelegationTreeBudgetPreflightV1Schema.safeParse({
        ...result,
        reason_codes: ["tree_capacity_insufficient_at_snapshot"],
      }).success,
    ).toBe(false);
    expect(
      DelegationTreeBudgetPreflightV1Schema.safeParse({
        ...result,
        remaining_after: 0,
      }).success,
    ).toBe(false);
  });

  it("keeps checked-in snapshot and preflight schemas generated from Zod", () => {
    expect(toDelegationTreeBudgetSnapshotV1JsonSchema()).toEqual(
      generatedSnapshotSchema,
    );
    expect(toDelegationTreeBudgetPreflightV1JsonSchema()).toEqual(
      generatedPreflightSchema,
    );
    expect(generatedPreflightSchema.oneOf).toEqual([
      {
        properties: {
          capacity_fit_at_snapshot: { const: true },
          reason_codes: { const: ["tree_capacity_available_at_snapshot"] },
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
    ]);
    expect(
      DelegationTreeBudgetSnapshotV1Schema.safeParse(firstCase.snapshot)
        .success,
    ).toBe(true);
  });
});
