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
    procedures: [],
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
});
