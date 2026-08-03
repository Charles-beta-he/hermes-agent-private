import { describe, expect, it } from "vitest";
import { buildAgentOsObservation, deriveBoundaryText } from "./agent-os-observation";
import type { AgentOsStatusResponse } from "./api";

// Mirrors the 2026-07-30 CLI readback per contract:
// projection {path, node_count:299, runtime_mutation:false}
// doctor {path, ok:true, findings:[]}
// shadow {path, ok:true, records:6, blocked_by_policy:3, would_execute_anything:false}
// operational_health {state:"degraded", findings:3}
// procedures 12 required, 9 fully_connected, 3 pending/stale
// certification {certified_healthy:false, eligible:false, boundary:"independent certification receipt required"}
const liveStatus: AgentOsStatusResponse = {
  ok: true,
  project: "hermes-agent-os-control-plane",
  mode: "advisory_readback_only",
  runtime_effect: "none",
  projection: { path: "/agent-os", node_count: 299, runtime_mutation: false },
  doctor: { path: "/agent-os", ok: true, findings: [] },
  shadow: { path: "/agent-os", ok: true, records: 6, blocked_by_policy: 3, would_execute_anything: false },
  procedure_validation: {
    available: true,
    trusted_read_only: true,
    findings: [],
    axes: {
      engineering_coverage: { systems: 27, missing_checks: 0, high_risk_systems: 0 },
      operational_health: { state: "degraded", findings: 3 },
      procedure_validation: {
        procedures: 12,
        required_procedures: 12,
        fully_connected: 9,
        blocked_by_authority: 0,
        pending: 3,
        stale: 3,
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
      ...Array.from({ length: 9 }, (_, index) => ({
        procedure_id: `fresh-${index}`, system_id: `system-${index}`, required: true,
        implementation_state: "enabled", validation_state: "e2e_passed",
        delivery_state: "fully_connected", latest_receipt_fresh: true, findings: [],
      })),
      ...Array.from({ length: 3 }, (_, index) => ({
        procedure_id: `stale-${index}`, system_id: `stale-system-${index}`, required: true,
        implementation_state: "enabled", validation_state: "stale",
        delivery_state: "landed_unverified", latest_receipt_fresh: false,
        findings: ["receipt stale"],
      })),
    ],
  },
};

function node(observation: ReturnType<typeof buildAgentOsObservation>, id: string) {
  const found = observation.layers.flatMap((layer) => layer.nodes).find((n) => n.id === id);
  if (!found) throw new Error(`node ${id} missing`);
  return found;
}

describe("Agent OS observation layer adapter", () => {
  it("derives exactly four populated layers in Intake/Governance/Systems/Evidence order", () => {
    const observation = buildAgentOsObservation(liveStatus);
    expect(observation.layers.map((layer) => layer.label)).toEqual([
      "Intake", "Governance", "Systems", "Evidence",
    ]);
    expect(observation.layers.map((layer) => layer.key)).toEqual([
      "intake", "governance", "systems", "evidence",
    ]);
    expect(observation.layers).toHaveLength(4);
    for (const layer of observation.layers) {
      expect(layer.nodes.length).toBeGreaterThan(0);
    }
  });

  it("derives live metrics from the status response without inventing confidence", () => {
    const observation = buildAgentOsObservation(liveStatus);

    // Intake has status/project/mode readback and does not imply execution
    const intakeStatus = node(observation, "intake-status");
    expect(intakeStatus.state).toBe("ok");
    expect(intakeStatus.value).toContain("hermes-agent-os-control-plane");
    expect(intakeStatus.value).toContain("advisory_readback_only");
    expect(intakeStatus.metrics.find((m) => m.label === "Project")?.value).toBe("hermes-agent-os-control-plane");
    expect(intakeStatus.metrics.find((m) => m.label === "Mode")?.value).toBe("advisory_readback_only");
    expect(intakeStatus.metrics.find((m) => m.label === "Runtime effect")?.value).toBe("none");
    expect(intakeStatus.evidence).toContain("does not imply execution");

    // Governance shadow metrics Records=6, Policy blocked=3, and observed no execution
    const shadow = node(observation, "shadow-ledger");
    expect(shadow.state).toBe("ok");
    expect(shadow.metrics).toEqual([
      { label: "Records", value: "6" },
      { label: "Policy blocked", value: "3" },
    ]);
    expect(shadow.evidence).toContain("observed no execution");

    // Systems projection node_count=299/runtime mutation false, operational degraded/findings3
    const projection = node(observation, "projection");
    expect(projection.state).toBe("ok");
    expect(projection.metrics).toEqual([
      { label: "Projection nodes", value: "299" },
      { label: "Runtime mutation", value: "false" },
    ]);

    const operational = node(observation, "operational-health");
    expect(operational.state).toBe("degraded");
    expect(operational.value).toBe("degraded");
    expect(operational.metrics).toEqual([{ label: "Active findings", value: "3" }]);

    // Engineering coverage: contract {systems:27, missing_checks:0, high_risk_systems:0} -> ok
    const engineering = node(observation, "engineering-coverage");
    expect(engineering.state).toBe("ok");
    expect(engineering.value).toBe("ok");
    expect(engineering.metrics).toEqual([
      { label: "Systems", value: "27" },
      { label: "Missing checks", value: "0" },
      { label: "High risk", value: "0" },
    ]);

    // Evidence doctor ok, procedures 12/9/3, certification Not eligible
    const doctor = node(observation, "doctor");
    expect(doctor.state).toBe("ok");
    expect(doctor.value).toBe("ok");

    const procedures = node(observation, "procedures");
    expect(procedures.state).toBe("stale");
    expect(procedures.metrics).toEqual([
      { label: "Required procedures", value: "12" },
      { label: "Fully connected", value: "9" },
      { label: "Stale", value: "3" },
      { label: "Failed", value: "0" },
      { label: "Authority blocked", value: "0" },
      { label: "Pending", value: "3" },
    ]);

    const certification = node(observation, "certification");
    expect(certification.state).toBe("blocked");
    expect(certification.value).toBe("Not eligible");
    expect(certification.metrics).toEqual([
      { label: "Eligible", value: "false" },
      { label: "Certified", value: "false" },
    ]);
    expect(certification.evidence).toContain("independent certification receipt required");

    // No percentage values anywhere
    const rendered = observation.layers.flatMap((layer) =>
      layer.nodes.flatMap((n) => [n.value, ...n.metrics.map((m) => m.value)]),
    );
    for (const text of rendered) {
      expect(text).not.toMatch(/%/);
    }
  });

  it("marks missing optional data unknown/unavailable, never healthy", () => {
    const sparse = structuredClone(liveStatus);
    delete sparse.projection;
    delete sparse.doctor;
    delete sparse.shadow;
    (sparse.procedure_validation.axes as Record<string, unknown>).operational_health = undefined;

    const observation = buildAgentOsObservation(sparse);
    for (const id of ["shadow-ledger", "doctor", "projection", "operational-health"]) {
      const missing = node(observation, id);
      expect(missing.state).toBe("unknown");
      expect(missing.value).toBe("Unavailable");
    }
  });

  it("marks malformed data unknown instead of trusting it", () => {
    const malformed = structuredClone(liveStatus) as unknown as AgentOsStatusResponse;
    (malformed as unknown as Record<string, unknown>).projection = { path: "bad", node_count: -1, runtime_mutation: false };
    (malformed as unknown as Record<string, unknown>).doctor = { path: "bad", ok: "true", findings: [] };
    (malformed as unknown as Record<string, unknown>).shadow = { path: "bad", ok: true, records: 6.5, blocked_by_policy: 9, would_execute_anything: false };
    (malformed.procedure_validation.axes as Record<string, unknown>).operational_health =
      { state: "healthy", findings: 1 };
    (malformed.procedure_validation.axes as Record<string, unknown>).certification =
      { certified_healthy: false, eligible: false, boundary: 123 };
    (malformed.procedure_validation.axes as Record<string, unknown>).engineering_coverage =
      { systems: 27, missing_checks: -1, high_risk_systems: 0 };
    malformed.procedure_validation.procedures = [];

    const observation = buildAgentOsObservation(malformed);
    for (const id of [
      "projection", "doctor", "shadow-ledger", "operational-health", "certification", "procedures", "engineering-coverage",
    ]) {
      const suspect = node(observation, id);
      expect(suspect.state).toBe("unknown");
      expect(suspect.value).toBe("Unavailable");
    }
  });

  it("never claims certification even when the payload overclaims certified_healthy=true", () => {
    const overclaim = structuredClone(liveStatus);
    overclaim.procedure_validation.axes.certification = {
      certified_healthy: true,
      eligible: true,
      boundary: "overclaimed",
    };
    const certification = node(buildAgentOsObservation(overclaim), "certification");
    expect(certification.state).toBe("blocked");
    expect(certification.value).toBe("Not eligible");
    // fail-closed: no metric or value may display certified success
    expect(certification.metrics).toEqual([]);
    expect(certification.value).not.toMatch(/certified/i);
    expect(certification.metrics.some((m) => m.label === "Certified" && m.value === "true")).toBe(false);
  });

  it("shows an eligible candidate without claiming certification when eligible=true and certified_healthy=false", () => {
    const candidate = structuredClone(liveStatus);
    candidate.procedure_validation.axes.certification = {
      certified_healthy: false,
      eligible: true,
      boundary: "independent certification receipt required",
    };
    const certification = node(buildAgentOsObservation(candidate), "certification");
    expect(certification.state).toBe("stale");
    expect(certification.state).not.toBe("ok");
    expect(certification.value).toBe("Eligible candidate · not certified");
    expect(certification.metrics).toEqual([
      { label: "Eligible", value: "true" },
      { label: "Certified", value: "false" },
    ]);
    expect(certification.evidence).toContain("independent certification receipt required");
  });

  it("keeps eligible=false as Not eligible with explicit eligibility metrics", () => {
    const notEligible = structuredClone(liveStatus);
    notEligible.procedure_validation.axes.certification = {
      certified_healthy: false,
      eligible: false,
      boundary: "independent certification receipt required",
    };
    const certification = node(buildAgentOsObservation(notEligible), "certification");
    expect(certification.state).toBe("blocked");
    expect(certification.value).toBe("Not eligible");
    expect(certification.metrics).toEqual([
      { label: "Eligible", value: "false" },
      { label: "Certified", value: "false" },
    ]);
    expect(certification.evidence).toContain("independent certification receipt required");
  });

  it("treats doctor ok=false and operational failed as degraded or blocked, not unknown", () => {
    const failDoc = structuredClone(liveStatus);
    (failDoc as unknown as Record<string, unknown>).doctor = { path: "/x", ok: false, findings: ["issue"] };
    (failDoc.procedure_validation.axes as Record<string, unknown>).operational_health = { state: "failed", findings: 5 };

    const observation = buildAgentOsObservation(failDoc);
    const doctorNode = node(observation, "doctor");
    expect(doctorNode.state).toBe("degraded");
    expect(doctorNode.state).not.toBe("unknown");
    expect(doctorNode.value).toBe("degraded");

    const opNode = node(observation, "operational-health");
    expect(["degraded", "blocked"]).toContain(opNode.state);
    expect(opNode.state).not.toBe("unknown");
  });

  it("marks engineering coverage attention-needed (degraded) when missing or high-risk >0, ok only on zero both", () => {
    // attention-needed case: valid counts but high-risk present
    const attn = structuredClone(liveStatus);
    (attn.procedure_validation.axes as Record<string, unknown>).engineering_coverage = { systems: 27, missing_checks: 0, high_risk_systems: 2 };
    const engineeringAttn = node(buildAgentOsObservation(attn), "engineering-coverage");
    expect(engineeringAttn.state).toBe("degraded");
    expect(engineeringAttn.value).toBe("degraded");
    expect(engineeringAttn.metrics).toEqual([
      { label: "Systems", value: "27" },
      { label: "Missing checks", value: "0" },
      { label: "High risk", value: "2" },
    ]);

    // still ok only when both zero
    const clean = structuredClone(liveStatus);
    (clean.procedure_validation.axes as Record<string, unknown>).engineering_coverage = { systems: 27, missing_checks: 0, high_risk_systems: 0 };
    const engineeringClean = node(buildAgentOsObservation(clean), "engineering-coverage");
    expect(engineeringClean.state).toBe("ok");
    expect(engineeringClean.value).toBe("ok");
  });

  it("missing intake ok or runtime_effect yields unknown not blocked/none", () => {
    const base = structuredClone(liveStatus);
    const noOk = structuredClone(base); delete (noOk as unknown as Record<string, unknown>).ok; delete (noOk as unknown as Record<string, unknown>).runtime_effect;
    const obs1 = buildAgentOsObservation(noOk);
    const intake1 = node(obs1, "intake-status");
    expect(intake1.state).toBe("unknown");
    expect(intake1.metrics.find(m => m.label === "Status")?.value).toBe("unknown");
    expect(intake1.metrics.find(m => m.label === "Runtime effect")?.value).toBe("unknown");

    const noRuntime = structuredClone(base); delete (noRuntime as unknown as Record<string, unknown>).runtime_effect;
    const obs2 = buildAgentOsObservation(noRuntime);
    const intake2 = node(obs2, "intake-status");
    expect(intake2.state).toBe("ok");
    expect(intake2.metrics.find(m => m.label === "Runtime effect")?.value).toBe("unknown");
  });

  it("shadow ok only when ok===true AND would_execute_anything===false; false or true => degraded; missing => unknown", () => {
    const base = structuredClone(liveStatus);
    const good = structuredClone(base); good.shadow = { ok: true, records: 1, blocked_by_policy: 0, would_execute_anything: false };
    expect(node(buildAgentOsObservation(good), "shadow-ledger").state).toBe("ok");

    const badOk = structuredClone(base); badOk.shadow = { ok: false, records: 1, blocked_by_policy: 0, would_execute_anything: false };
    expect(node(buildAgentOsObservation(badOk), "shadow-ledger").state).toBe("degraded");

    const would = structuredClone(base); would.shadow = { ok: true, records: 1, blocked_by_policy: 0, would_execute_anything: true };
    expect(node(buildAgentOsObservation(would), "shadow-ledger").state).toBe("degraded");

    const missOk = structuredClone(base); missOk.shadow = { records: 1, blocked_by_policy: 0, would_execute_anything: false };
    expect(node(buildAgentOsObservation(missOk), "shadow-ledger").state).toBe("unknown");

    const missWould = structuredClone(base); missWould.shadow = { ok: true, records: 1, blocked_by_policy: 0 };
    expect(node(buildAgentOsObservation(missWould), "shadow-ledger").state).toBe("unknown");
  });

  it("projection ok only when runtime_mutation===false; true=>degraded; missing=>unknown", () => {
    const base = structuredClone(liveStatus);
    const okp = structuredClone(base); okp.projection = { path: "/p", node_count: 5, runtime_mutation: false };
    expect(node(buildAgentOsObservation(okp), "projection").state).toBe("ok");

    const mut = structuredClone(base); mut.projection = { path: "/p", node_count: 5, runtime_mutation: true };
    expect(node(buildAgentOsObservation(mut), "projection").state).toBe("degraded");

    const miss = structuredClone(base); miss.projection = { path: "/p", node_count: 5 };
    expect(node(buildAgentOsObservation(miss), "projection").state).toBe("unknown");
  });

  it("procedures never ok when failed/blocked/pending/stale nonzero, fully_connected<req, inventory not true, or can_claim false; failed or blocked=>blocked; otherwise incomplete=>stale", () => {
    interface ProcEntry {
      procedure_id: string;
      system_id: string;
      required: boolean;
      implementation_state: string;
      validation_state: string;
      delivery_state: string;
      latest_receipt_fresh: boolean;
      findings: string[];
    }
    const freshEntry = (id: string): ProcEntry => ({
      procedure_id: id, system_id: `sys-${id}`, required: true,
      implementation_state: "enabled", validation_state: "e2e_passed",
      delivery_state: "fully_connected", latest_receipt_fresh: true, findings: [],
    });
    const failedEntry = (id: string): ProcEntry => ({
      ...freshEntry(id), implementation_state: "landed", validation_state: "e2e_failed",
      delivery_state: "partially_landed", latest_receipt_fresh: false, findings: ["e2e failed"],
    });
    const blockedEntry = (id: string): ProcEntry => ({
      ...freshEntry(id), implementation_state: "landed", validation_state: "blocked_by_authority",
      delivery_state: "blocked", latest_receipt_fresh: false, findings: ["authority required"],
    });
    const pendingEntry = (id: string): ProcEntry => ({
      ...freshEntry(id), implementation_state: "landed", validation_state: "untested",
      delivery_state: "landed_unverified", latest_receipt_fresh: false, findings: ["not yet tested"],
    });
    const staleEntry = (id: string): ProcEntry => ({
      ...freshEntry(id), validation_state: "stale",
      delivery_state: "landed_unverified", latest_receipt_fresh: false, findings: ["receipt stale"],
    });

    // Aggregate counts are recomputed from entries so the axis reconciles with the list
    const mk = (entries: ProcEntry[], overrides: Record<string, unknown> = {}) => {
      const required = entries.filter((e) => e.required);
      const ax = {
        procedures: entries.length,
        required_procedures: required.length,
        fully_connected: required.filter((e) => e.delivery_state === "fully_connected").length,
        blocked_by_authority: required.filter((e) => e.validation_state === "blocked_by_authority").length,
        pending: required.filter((e) => e.delivery_state !== "fully_connected" && e.validation_state !== "blocked_by_authority").length,
        stale: required.filter((e) => e.validation_state === "stale").length,
        failed: required.filter((e) => e.validation_state === "e2e_failed").length,
        inventory_complete: true,
        can_claim_complete_procedure_validation: true,
        ...overrides
      };
      return {
        procedure_validation: {
          available: true,
          trusted_read_only: true,
          findings: [],
          axes: { procedure_validation: ax },
          procedures: entries
        }
      } as unknown as AgentOsStatusResponse;
    };

    expect(node(buildAgentOsObservation(mk([freshEntry("p1"), freshEntry("p2")])), "procedures").state).toBe("ok");

    expect(node(buildAgentOsObservation(mk([freshEntry("p1"), failedEntry("p2")], { can_claim_complete_procedure_validation: false })), "procedures").state).toBe("blocked");
    expect(node(buildAgentOsObservation(mk([freshEntry("p1"), blockedEntry("p2")], { can_claim_complete_procedure_validation: false })), "procedures").state).toBe("blocked");
    // pending/stale cause stale; fully_connected < required is implied by the pending entry
    expect(node(buildAgentOsObservation(mk([freshEntry("p1"), pendingEntry("p2")], { can_claim_complete_procedure_validation: false })), "procedures").state).toBe("stale");
    expect(node(buildAgentOsObservation(mk([freshEntry("p1"), staleEntry("p2")], { can_claim_complete_procedure_validation: false })), "procedures").state).toBe("stale");
    expect(node(buildAgentOsObservation(mk([freshEntry("p1"), freshEntry("p2")], { inventory_complete: false })), "procedures").state).toBe("stale");
    expect(node(buildAgentOsObservation(mk([freshEntry("p1"), freshEntry("p2")], { can_claim_complete_procedure_validation: false })), "procedures").state).toBe("stale");
  });

  it("procedures unavailable unless procedure_validation is available and trusted_read_only", () => {
    const untrusted = structuredClone(liveStatus);
    untrusted.procedure_validation.trusted_read_only = false;
    const untrustedNode = node(buildAgentOsObservation(untrusted), "procedures");
    expect(untrustedNode.state).toBe("unknown");
    expect(untrustedNode.value).toBe("Unavailable");
    expect(untrustedNode.metrics).toEqual([]);

    const unavailable = structuredClone(liveStatus);
    unavailable.procedure_validation.available = false;
    const unavailableNode = node(buildAgentOsObservation(unavailable), "procedures");
    expect(unavailableNode.state).toBe("unknown");
    expect(unavailableNode.value).toBe("Unavailable");
  });

  it("procedures unavailable on malformed entry at matching length, duplicate procedure_id, or count mismatch", () => {
    // malformed entry: invalid validation_state while list length still matches the aggregate
    const malformedEntry = structuredClone(liveStatus);
    malformedEntry.procedure_validation.procedures[0] = {
      ...malformedEntry.procedure_validation.procedures[0],
      validation_state: "not_a_state",
    };
    expect(malformedEntry.procedure_validation.procedures).toHaveLength(12);
    const malformedNode = node(buildAgentOsObservation(malformedEntry), "procedures");
    expect(malformedNode.state).toBe("unknown");
    expect(malformedNode.value).toBe("Unavailable");

    const duplicated = structuredClone(liveStatus);
    duplicated.procedure_validation.procedures[1] = {
      ...duplicated.procedure_validation.procedures[1],
      procedure_id: "fresh-0",
    };
    const duplicatedNode = node(buildAgentOsObservation(duplicated), "procedures");
    expect(duplicatedNode.state).toBe("unknown");
    expect(duplicatedNode.value).toBe("Unavailable");

    const mismatch = structuredClone(liveStatus);
    const mismatchAxis = mismatch.procedure_validation.axes.procedure_validation;
    if (mismatchAxis) mismatchAxis.fully_connected = 8;
    const mismatchNode = node(buildAgentOsObservation(mismatch), "procedures");
    expect(mismatchNode.state).toBe("unknown");
    expect(mismatchNode.value).toBe("Unavailable");
  });

  it("projection unavailable when its path is listed in status.missing", () => {
    const missingProjection = structuredClone(liveStatus);
    missingProjection.missing = ["/agent-os"];
    const projection = node(buildAgentOsObservation(missingProjection), "projection");
    expect(projection.state).toBe("unknown");
    expect(projection.value).toBe("Unavailable");
    expect(projection.metrics).toEqual([]);

    const otherMissing = structuredClone(liveStatus);
    otherMissing.missing = ["/some/other/path"];
    expect(node(buildAgentOsObservation(otherMissing), "projection").state).toBe("ok");
  });

  it("fractional operational findings or required_procedures produce unknown/Unavailable (integers required)", () => {
    const fracFindings = structuredClone(liveStatus);
    (fracFindings.procedure_validation.axes as Record<string, unknown>).operational_health = { state: "degraded", findings: 1.5 };
    const opNode = node(buildAgentOsObservation(fracFindings), "operational-health");
    expect(opNode.state).toBe("unknown");
    expect(opNode.value).toBe("Unavailable");

    const fracReq = structuredClone(liveStatus);
    const procAxis = (fracReq.procedure_validation.axes as Record<string, unknown>).procedure_validation as Record<string, unknown>;
    (fracReq.procedure_validation.axes as Record<string, unknown>).procedure_validation = {
      ...procAxis,
      required_procedures: 1.5,
    };
    const procNode = node(buildAgentOsObservation(fracReq), "procedures");
    expect(procNode.state).toBe("unknown");
    expect(procNode.value).toBe("Unavailable");
  });

  it("populates source provenance from validated artifact paths, including unavailable nodes with a path", () => {
    const observation = buildAgentOsObservation(liveStatus);
    expect(node(observation, "projection").source).toBe("/agent-os");
    expect(node(observation, "doctor").source).toBe("/agent-os");
    expect(node(observation, "shadow-ledger").source).toBe("/agent-os");

    // malformed payload -> Unavailable node still carries the validated path
    const malformed = structuredClone(liveStatus);
    (malformed as unknown as Record<string, unknown>).shadow =
      { path: "/agent-os", ok: true, records: 6.5, blocked_by_policy: 9, would_execute_anything: false };
    (malformed as unknown as Record<string, unknown>).doctor = { path: "/agent-os", ok: "true", findings: [] };
    (malformed as unknown as Record<string, unknown>).projection = { path: "/agent-os", node_count: -1 };
    const malformedObservation = buildAgentOsObservation(malformed);
    for (const id of ["shadow-ledger", "doctor", "projection"]) {
      const unavailable = node(malformedObservation, id);
      expect(unavailable.value).toBe("Unavailable");
      expect(unavailable.source).toBe("/agent-os");
    }

    // absent, non-string, or blank path -> no source
    const noPath = structuredClone(liveStatus);
    noPath.projection = { node_count: 5, runtime_mutation: false };
    (noPath as unknown as Record<string, unknown>).doctor = { path: "   ", ok: true, findings: [] };
    (noPath as unknown as Record<string, unknown>).shadow =
      { path: 42, ok: true, records: 1, blocked_by_policy: 0, would_execute_anything: false };
    const noPathObservation = buildAgentOsObservation(noPath);
    expect(node(noPathObservation, "projection").source).toBeUndefined();
    expect(node(noPathObservation, "doctor").source).toBeUndefined();
    expect(node(noPathObservation, "shadow-ledger").source).toBeUndefined();
  });

  it("surfaces validated doctor findings as evidence on degraded and ok doctor nodes", () => {
    const failDoc = structuredClone(liveStatus);
    (failDoc as unknown as Record<string, unknown>).doctor = {
      path: "/agent-os",
      ok: false,
      findings: ["projection drift", "stale receipt"],
    };
    const degradedDoctor = node(buildAgentOsObservation(failDoc), "doctor");
    expect(degradedDoctor.state).toBe("degraded");
    expect(degradedDoctor.evidence).toEqual(["projection drift", "stale receipt"]);

    // ok doctor keeps validated findings too (empty in the live readback)
    const okDoctor = node(buildAgentOsObservation(liveStatus), "doctor");
    expect(okDoctor.state).toBe("ok");
    expect(okDoctor.evidence).toEqual([]);

    const okWithFindings = structuredClone(liveStatus);
    (okWithFindings as unknown as Record<string, unknown>).doctor =
      { path: "/agent-os", ok: true, findings: ["advisory only"] };
    const okNode = node(buildAgentOsObservation(okWithFindings), "doctor");
    expect(okNode.state).toBe("ok");
    expect(okNode.evidence).toEqual(["advisory only"]);
  });

  it("formats structured {level, check, message} doctor findings into actionable evidence", () => {
    const structured = structuredClone(liveStatus);
    (structured as unknown as Record<string, unknown>).doctor = {
      path: "/agent-os",
      ok: false,
      findings: [
        { level: "warning", check: "projection", message: "drift detected" },
        { check: "receipts", message: "2 stale" },
        "plain finding",
      ],
    };
    const doctorNode = node(buildAgentOsObservation(structured), "doctor");
    expect(doctorNode.state).toBe("degraded");
    expect(doctorNode.evidence).toEqual([
      "[warning] projection: drift detected",
      "receipts: 2 stale",
      "plain finding",
    ]);
  });

  it("any unrecognized doctor finding makes the doctor node unavailable instead of being dropped", () => {
    const cases: unknown[][] = [
      ["valid finding", 42],
      ["valid finding", "   "],
      [{ check: "projection" }],
      [{ check: "", message: "empty check" }],
      [{ level: 3, check: "projection", message: "bad level" }],
      [null],
    ];
    for (const findings of cases) {
      const bad = structuredClone(liveStatus);
      (bad as unknown as Record<string, unknown>).doctor = { path: "/agent-os", ok: false, findings };
      const doctorNode = node(buildAgentOsObservation(bad), "doctor");
      expect(doctorNode.state).toBe("unknown");
      expect(doctorNode.value).toBe("Unavailable");
      expect(doctorNode.evidence ?? []).toEqual([]);
    }
  });

  it("attaches observed-no-execution evidence only when shadow ok===true and would_execute_anything===false", () => {
    const good = structuredClone(liveStatus);
    good.shadow = { ok: true, records: 1, blocked_by_policy: 0, would_execute_anything: false };
    expect(node(buildAgentOsObservation(good), "shadow-ledger").evidence).toContain("observed no execution");

    // failed shadow run must not affirm that nothing executed
    const failed = structuredClone(liveStatus);
    failed.shadow = { ok: false, records: 1, blocked_by_policy: 0, would_execute_anything: false };
    const failedNode = node(buildAgentOsObservation(failed), "shadow-ledger");
    expect(failedNode.state).toBe("degraded");
    expect(failedNode.evidence).toEqual([]);

    const missingOk = structuredClone(liveStatus);
    missingOk.shadow = { records: 1, blocked_by_policy: 0, would_execute_anything: false };
    const missingOkNode = node(buildAgentOsObservation(missingOk), "shadow-ledger");
    expect(missingOkNode.state).toBe("unknown");
    expect(missingOkNode.evidence ?? []).toEqual([]);
  });

  it("procedures unavailable on entries violating backend state invariants even when aggregates reconcile", () => {
    const fresh = (id: string) => ({
      procedure_id: id, system_id: `sys-${id}`, required: true,
      implementation_state: "enabled", validation_state: "e2e_passed",
      delivery_state: "fully_connected", latest_receipt_fresh: true, findings: [] as string[],
    });
    // Aggregate counts are recomputed from the entries themselves so the only
    // remaining failure is the per-entry state relationship.
    const mk = (entries: ReturnType<typeof fresh>[]) => {
      const required = entries.filter((e) => e.required);
      return {
        procedure_validation: {
          available: true,
          trusted_read_only: true,
          findings: [],
          axes: {
            procedure_validation: {
              procedures: entries.length,
              required_procedures: required.length,
              fully_connected: required.filter((e) => e.delivery_state === "fully_connected").length,
              blocked_by_authority: required.filter((e) => e.validation_state === "blocked_by_authority").length,
              pending: required.filter((e) => e.delivery_state !== "fully_connected" && e.validation_state !== "blocked_by_authority").length,
              stale: required.filter((e) => e.validation_state === "stale").length,
              failed: required.filter((e) => e.validation_state === "e2e_failed").length,
              inventory_complete: true,
              can_claim_complete_procedure_validation: false,
            },
          },
          procedures: entries,
        },
      } as unknown as AgentOsStatusResponse;
    };

    // fully_connected e2e_passed entry whose latest receipt is stale
    const staleReceipt = mk([fresh("p1"), { ...fresh("p2"), latest_receipt_fresh: false }]);
    // blocked_by_authority must pair with delivery_state "blocked"
    const blockedWrongDelivery = mk([fresh("p1"), {
      ...fresh("p2"), implementation_state: "landed", validation_state: "blocked_by_authority",
      delivery_state: "landed_unverified", latest_receipt_fresh: false, findings: ["authority required"],
    }]);
    // stale validation cannot carry a fresh receipt
    const staleFreshReceipt = mk([fresh("p1"), {
      ...fresh("p2"), validation_state: "stale", delivery_state: "landed_unverified",
      latest_receipt_fresh: true, findings: ["receipt stale"],
    }]);
    // fully_connected delivery requires enabled + e2e_passed + no findings
    const connectedNotPassed = mk([fresh("p1"), {
      ...fresh("p2"), implementation_state: "landed", validation_state: "untested",
      delivery_state: "fully_connected", latest_receipt_fresh: false, findings: ["not tested"],
    }]);

    for (const status of [staleReceipt, blockedWrongDelivery, staleFreshReceipt, connectedNotPassed]) {
      const procNode = node(buildAgentOsObservation(status), "procedures");
      expect(procNode.state).toBe("unknown");
      expect(procNode.value).toBe("Unavailable");
      expect(procNode.metrics).toEqual([]);
    }

    // control: the same builder with only well-formed entries stays derivable
    const control = mk([fresh("p1"), fresh("p2")]);
    expect(node(buildAgentOsObservation(control), "procedures").state).not.toBe("unknown");
  });

  it("surfaces deduped top-level and entry procedure findings as evidence without promoting state, including untrusted/unknown nodes", () => {
    // live status: 3 stale entries each carry findings ["receipt stale"]; add
    // top-level findings with a duplicate and a blank to exercise dedupe/filtering
    const withFindings = structuredClone(liveStatus);
    withFindings.procedure_validation.findings = ["top-level drift", "receipt stale", "   "];
    const procedures = node(buildAgentOsObservation(withFindings), "procedures");
    // findings never promote state: still stale, same metrics-driven derivation
    expect(procedures.state).toBe("stale");
    expect(procedures.evidence).toEqual(["top-level drift", "receipt stale"]);

    // untrusted readback: node is unknown/Unavailable but findings stay displayable
    const untrusted = structuredClone(withFindings);
    untrusted.procedure_validation.trusted_read_only = false;
    const untrustedNode = node(buildAgentOsObservation(untrusted), "procedures");
    expect(untrustedNode.state).toBe("unknown");
    expect(untrustedNode.value).toBe("Unavailable");
    expect(untrustedNode.evidence).toEqual(["top-level drift", "receipt stale"]);
  });

  it("zero-node projection (backend malformed-JSON sentinel) is unknown/Unavailable with source preserved", () => {
    const zero = structuredClone(liveStatus);
    zero.projection = { path: "/agent-os", node_count: 0, runtime_mutation: false };
    const projection = node(buildAgentOsObservation(zero), "projection");
    expect(projection.state).toBe("unknown");
    expect(projection.value).toBe("Unavailable");
    expect(projection.metrics).toEqual([]);
    expect(projection.source).toBe("/agent-os");

    // runtime_mutation=true with zero count is still unavailable, never degraded
    const zeroMut = structuredClone(liveStatus);
    zeroMut.projection = { path: "/agent-os", node_count: 0, runtime_mutation: true };
    const projectionMut = node(buildAgentOsObservation(zeroMut), "projection");
    expect(projectionMut.state).toBe("unknown");
    expect(projectionMut.value).toBe("Unavailable");
    expect(projectionMut.source).toBe("/agent-os");

    // positive counts keep their derived states
    const positive = structuredClone(liveStatus);
    positive.projection = { path: "/agent-os", node_count: 1, runtime_mutation: false };
    expect(node(buildAgentOsObservation(positive), "projection").state).toBe("ok");
    const positiveMut = structuredClone(liveStatus);
    positiveMut.projection = { path: "/agent-os", node_count: 1, runtime_mutation: true };
    expect(node(buildAgentOsObservation(positiveMut), "projection").state).toBe("degraded");
  });

});

describe("deriveBoundaryText", () => {
  it("keeps the exact dual-unknown sentence when both readbacks are unavailable", () => {
    expect(deriveBoundaryText(null, null)).toBe(
      "Shadow execution readback unavailable. Certification readback unavailable.",
    );

    const sparse = structuredClone(liveStatus);
    delete sparse.shadow;
    const observation = buildAgentOsObservation(sparse);
    const unknownShadow = node(observation, "shadow-ledger");
    expect(unknownShadow.state).toBe("unknown");
    expect(deriveBoundaryText(unknownShadow, null)).toBe(
      "Shadow execution readback unavailable. Certification readback unavailable.",
    );
  });

  it("renders the evidence-only sentence for ok shadow with explicit no-execution evidence", () => {
    const observation = buildAgentOsObservation(liveStatus);
    const text = deriveBoundaryText(node(observation, "shadow-ledger"), node(observation, "certification"));
    expect(text).toBe(
      "Configured capability is not execution. Shadow observed no execution is evidence only. Certification remains Not eligible.",
    );
  });

  it("degraded shadow renders an adverse/failed sentence, never unavailable and never no-execution", () => {
    const failed = structuredClone(liveStatus);
    failed.shadow = { ok: false, records: 1, blocked_by_policy: 0, would_execute_anything: false };
    const failedObservation = buildAgentOsObservation(failed);
    const failedShadow = node(failedObservation, "shadow-ledger");
    expect(failedShadow.state).toBe("degraded");
    const failedText = deriveBoundaryText(failedShadow, node(failedObservation, "certification"));
    expect(failedText).toContain("adverse or failed");
    const [failedShadowSentence] = failedText.split(" Certification");
    expect(failedShadowSentence).not.toMatch(/unavailable/i);
    expect(failedShadowSentence).not.toMatch(/no execution/i);

    const would = structuredClone(liveStatus);
    would.shadow = { ok: true, records: 1, blocked_by_policy: 0, would_execute_anything: true };
    const wouldShadow = node(buildAgentOsObservation(would), "shadow-ledger");
    expect(wouldShadow.state).toBe("degraded");
    const wouldText = deriveBoundaryText(wouldShadow, null);
    expect(wouldText).toContain("adverse or failed");
    expect(wouldText.split(" Certification")[0]).not.toMatch(/unavailable|no execution/i);
  });
});
