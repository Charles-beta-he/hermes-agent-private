import { describe, expect, it } from "vitest";
import { buildAgentOsAxisCards, procedureTone } from "./agent-os-procedure";
import type { AgentOsStatusResponse } from "./api";

const response: AgentOsStatusResponse = {
  ok: true,
  project: "hermes-agent-os-control-plane",
  mode: "advisory_readback_only",
  runtime_effect: "none",
  procedure_validation: {
    available: true,
    trusted_read_only: true,
    findings: [],
    axes: {
      engineering_coverage: { systems: 27, missing_checks: 0, high_risk_systems: 0 },
      operational_health: { state: "degraded", findings: 2 },
      procedure_validation: {
        procedures: 12,
        required_procedures: 12,
        fully_connected: 7,
        blocked_by_authority: 5,
        pending: 0,
        stale: 0,
        failed: 0,
        inventory_complete: true,
        can_claim_complete_procedure_validation: false,
      },
      certification: {
        certified_healthy: false,
        eligible: false,
        boundary: "independent certification receipt required",
      },
    },
    procedures: [
      ...Array.from({ length: 7 }, (_, index) => ({
        procedure_id: `connected-${index}`, system_id: `system-${index}`, required: true,
        implementation_state: "enabled", validation_state: "e2e_passed",
        delivery_state: "fully_connected", latest_receipt_fresh: true, findings: [],
      })),
      ...Array.from({ length: 5 }, (_, index) => ({
        procedure_id: `blocked-${index}`, system_id: `blocked-system-${index}`, required: true,
        implementation_state: "landed", validation_state: "blocked_by_authority",
        delivery_state: "blocked", latest_receipt_fresh: false, findings: ["authority required"],
      })),
    ],
  },
};

describe("Agent OS procedure projection UI model", () => {
  it("keeps engineering, operations, procedure validation and certification separate", () => {
    expect(buildAgentOsAxisCards(response)).toEqual([
      { key: "engineering", label: "Engineering coverage", value: "Converged", detail: "27 systems · 0 missing · 0 high risk", tone: "success" },
      { key: "operations", label: "Operational health", value: "Degraded", detail: "2 active findings", tone: "warning" },
      { key: "procedures", label: "Procedure validation", value: "7 / 12", detail: "5 authority blocked · 0 pending · 0 failed", tone: "warning" },
      { key: "certification", label: "Certification", value: "Not certified", detail: "independent certification receipt required", tone: "destructive" },
    ]);
  });

  it("maps procedure states without treating authority blocks as runtime failures", () => {
    expect(procedureTone("e2e_passed")).toBe("success");
    expect(procedureTone("blocked_by_authority")).toBe("warning");
    expect(procedureTone("e2e_failed")).toBe("destructive");
    expect(procedureTone("untested")).toBe("secondary");
  });

  it("fails closed on contradictory certification claims", () => {
    const overclaim = structuredClone(response);
    if (overclaim.procedure_validation.axes.certification) {
      overclaim.procedure_validation.axes.certification.certified_healthy = true;
      overclaim.procedure_validation.axes.certification.eligible = true;
    }
    const certification = buildAgentOsAxisCards(overclaim)[3];
    expect(certification.value).toBe("Not certified");
    expect(certification.tone).toBe("destructive");
  });

  it("fails closed on impossible or negative aggregate counters", () => {
    const malformed = structuredClone(response) as unknown as AgentOsStatusResponse;
    const axes = malformed.procedure_validation.axes as Record<string, any>;
    axes.engineering_coverage = { systems: 27, missing_checks: -1, high_risk_systems: 0 };
    axes.operational_health = { state: "healthy", findings: 1 };
    axes.procedure_validation = {
      procedures: 12,
      required_procedures: 12,
      fully_connected: 12,
      blocked_by_authority: 12,
      pending: 12,
      stale: 12,
      failed: 12,
      inventory_complete: true,
      can_claim_complete_procedure_validation: true,
    };
    axes.certification = {
      certified_healthy: true,
      eligible: true,
      boundary: "malformed overclaim",
    };

    const cards = buildAgentOsAxisCards(malformed);
    expect(cards[0].value).toBe("Attention needed");
    expect(cards[1].value).toBe("Unknown");
    expect(cards[2].value).toBe("Unknown");
    expect(cards[3].value).toBe("Not certified");
  });

  it("rejects extra keys on every axis to match the Python projection boundary", () => {
    const malformed = structuredClone(response) as unknown as AgentOsStatusResponse;
    const axes = malformed.procedure_validation.axes as Record<string, any>;
    axes.engineering_coverage.unexpected = 1;
    axes.operational_health.unexpected = 1;
    axes.procedure_validation.unexpected = 1;
    axes.certification.unexpected = 1;

    const cards = buildAgentOsAxisCards(malformed);
    expect(cards[0].value).toBe("Attention needed");
    expect(cards[1].value).toBe("Unknown");
    expect(cards[2].value).toBe("Unknown");
    expect(cards[3].value).toBe("Not certified");
  });

  it("rejects unknown axis members instead of silently trusting schema extensions", () => {
    const malformed = structuredClone(response) as unknown as AgentOsStatusResponse;
    (malformed.procedure_validation.axes as Record<string, any>).unknown_axis = { smuggled: true };

    const cards = buildAgentOsAxisCards(malformed);
    expect(cards[1].value).toBe("Unknown");
    expect(cards[2].value).toBe("Unknown");
    expect(cards[2].tone).toBe("warning");
    expect(cards[3].value).toBe("Not certified");
  });

  it("rejects coercible non-string axis and procedure states", () => {
    const malformed = structuredClone(response) as unknown as AgentOsStatusResponse;
    (malformed.procedure_validation.axes.operational_health as any).state = { toString: () => "healthy" };
    malformed.procedure_validation.axes.procedure_validation = {
      procedures: 1, required_procedures: 1, fully_connected: 1, blocked_by_authority: 0,
      pending: 0, stale: 0, failed: 0, inventory_complete: true,
      can_claim_complete_procedure_validation: true,
    };
    malformed.procedure_validation.procedures = [{
      procedure_id: "p1", system_id: "s1", required: true, implementation_state: "enabled",
      validation_state: { toString: () => "e2e_passed" }, delivery_state: "fully_connected",
      latest_receipt_fresh: true, findings: [],
    }] as any;

    const cards = buildAgentOsAxisCards(malformed);
    expect(cards[1].value).toBe("Unknown");
    expect(cards[2].tone).toBe("warning");
  });

  it("rejects coercible non-boolean trusted_read_only values", () => {
    const malformed = structuredClone(response) as unknown as AgentOsStatusResponse;
    (malformed.procedure_validation as any).trusted_read_only = "false";
    malformed.procedure_validation.axes.procedure_validation = {
      procedures: 1, required_procedures: 1, fully_connected: 1, blocked_by_authority: 0,
      pending: 0, stale: 0, failed: 0, inventory_complete: true,
      can_claim_complete_procedure_validation: true,
    };
    malformed.procedure_validation.procedures = [{
      procedure_id: "p1", system_id: "s1", required: true, implementation_state: "enabled",
      validation_state: "e2e_passed", delivery_state: "fully_connected",
      latest_receipt_fresh: true, findings: [],
    }];

    const card = buildAgentOsAxisCards(malformed)[2];
    expect(card.value).toBe("1 / 1");
    expect(card.tone).toBe("warning");
  });

  it("fails closed when completion claim contradicts inventory and procedure totals", () => {
    const malformed = structuredClone(response) as unknown as AgentOsStatusResponse;
    const axes = malformed.procedure_validation.axes as Record<string, any>;
    axes.procedure_validation = {
      procedures: 0,
      required_procedures: 2,
      fully_connected: 0,
      blocked_by_authority: 1,
      pending: 1,
      stale: 0,
      failed: 0,
      inventory_complete: false,
      can_claim_complete_procedure_validation: true,
    };
    axes.certification = { certified_healthy: true, eligible: true, boundary: "overclaim" };

    const cards = buildAgentOsAxisCards(malformed);
    expect(cards[2].value).toBe("Unknown");
    expect(cards[3].value).toBe("Not certified");
  });

  it.each(["missing", "scaffolded"])("rejects %s implementation claimed as landed", (implementation) => {
    const malformed = structuredClone(response) as unknown as AgentOsStatusResponse;
    malformed.procedure_validation.axes.procedure_validation = {
      procedures: 1, required_procedures: 1, fully_connected: 0, blocked_by_authority: 0,
      pending: 1, stale: 0, failed: 0, inventory_complete: true,
      can_claim_complete_procedure_validation: false,
    };
    malformed.procedure_validation.procedures = [{
      procedure_id: "p1", system_id: "s1", required: true,
      implementation_state: implementation, validation_state: "untested",
      delivery_state: "landed_unverified", latest_receipt_fresh: false,
      findings: ["not yet tested"],
    }] as any;

    expect(buildAgentOsAxisCards(malformed)[2].value).toBe("Unknown");
  });

  it("reconciles procedure entries and keeps certification negative without a receipt", () => {
    const overclaim = structuredClone(response) as unknown as AgentOsStatusResponse;
    overclaim.procedure_validation.axes.operational_health = { state: "healthy", findings: 0 };
    overclaim.procedure_validation.axes.procedure_validation = {
      procedures: 1,
      required_procedures: 1,
      fully_connected: 1,
      blocked_by_authority: 0,
      pending: 0,
      stale: 0,
      failed: 0,
      inventory_complete: true,
      can_claim_complete_procedure_validation: true,
    };
    overclaim.procedure_validation.axes.certification = {
      certified_healthy: true,
      eligible: true,
      boundary: "receipt absent",
    };
    overclaim.procedure_validation.procedures = [];

    let cards = buildAgentOsAxisCards(overclaim);
    expect(cards[2].tone).toBe("warning");
    expect(cards[3].value).toBe("Not certified");

    const duplicate = {
      procedure_id: "duplicate",
      system_id: "system",
      required: true,
      implementation_state: "enabled",
      validation_state: "e2e_passed",
      delivery_state: "fully_connected",
      latest_receipt_fresh: true,
      findings: [],
    };
    overclaim.procedure_validation.axes.procedure_validation.procedures = 2;
    overclaim.procedure_validation.procedures = [duplicate, { ...duplicate }];
    cards = buildAgentOsAxisCards(overclaim);
    expect(cards[2].tone).toBe("warning");
    expect(cards[3].value).toBe("Not certified");

    overclaim.procedure_validation.axes.procedure_validation.procedures = 1;
    overclaim.procedure_validation.procedures = [{
      procedure_id: "malformed",
      required: true,
      delivery_state: "fully_connected",
      validation_state: "garbage",
    }] as any;
    cards = buildAgentOsAxisCards(overclaim);
    expect(cards[2].tone).toBe("warning");
    expect(cards[3].value).toBe("Not certified");
  });

  it("renders safe fallbacks for malformed nested axis values", () => {
    const malformed = structuredClone(response) as unknown as AgentOsStatusResponse;
    (malformed.procedure_validation.axes as Record<string, unknown>).operational_health = {};

    expect(buildAgentOsAxisCards(malformed)[1]).toEqual({
      key: "operations",
      label: "Operational health",
      value: "Unknown",
      detail: "Projection unavailable",
      tone: "secondary",
    });
  });
});
