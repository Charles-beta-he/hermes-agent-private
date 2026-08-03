import type { AgentOsStatusResponse } from "./api";

export type ObservationState = "ok" | "degraded" | "stale" | "blocked" | "unknown";

export interface ObservationMetric {
  label: string;
  value: string;
}

export interface ObservationNode {
  id: string;
  label: string;
  state: ObservationState;
  value: string;
  metrics: ObservationMetric[];
  evidence?: string[];
  source?: string;
}

export interface ObservationLayer {
  key: "intake" | "governance" | "systems" | "evidence";
  label: string;
  nodes: ObservationNode[];
}

export interface AgentOsObservation {
  layers: ObservationLayer[];
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function safeNumber(n: unknown): number | undefined {
  return typeof n === "number" && Number.isFinite(n) && n >= 0 && Number.isInteger(n) ? n : undefined;
}

function safeBool(b: unknown): boolean | undefined {
  return typeof b === "boolean" ? b : undefined;
}

// Validated artifact path provenance: attached even when the rest of the
// artifact payload fails validation, so unavailable nodes still say where
// the readback came from.
function artifactPath(v: unknown): string | undefined {
  return isRecord(v) && typeof v.path === "string" && v.path.trim().length > 0 ? v.path : undefined;
}

const IMPLEMENTATION_STATES = new Set(["missing", "scaffolded", "landed", "enabled"]);
const VALIDATION_STATES = new Set([
  "untested", "contract_passed", "integration_passed", "e2e_passed",
  "e2e_failed", "stale", "blocked_by_authority",
]);
const DELIVERY_STATES = new Set([
  "pending", "in_progress", "partially_landed", "landed_unverified",
  "fully_connected", "blocked", "not_applicable",
]);

function isValidProcedureEntryShape(v: unknown): v is Record<string, unknown> {
  if (!isRecord(v)
      || typeof v.procedure_id !== "string" || v.procedure_id.trim().length === 0
      || typeof v.system_id !== "string" || v.system_id.trim().length === 0
      || typeof v.required !== "boolean"
      || typeof v.latest_receipt_fresh !== "boolean"
      || typeof v.implementation_state !== "string" || !IMPLEMENTATION_STATES.has(v.implementation_state)
      || typeof v.validation_state !== "string" || !VALIDATION_STATES.has(v.validation_state)
      || typeof v.delivery_state !== "string" || !DELIVERY_STATES.has(v.delivery_state)
      || !Array.isArray(v.findings)
      || !v.findings.every((f) => typeof f === "string" && f.trim().length > 0)) return false;
  // Mirrors the state relationships in agent_os_control_plane._procedure_entry_valid
  // (also mirrored in agent-os-procedure.ts isProcedureEntry): any combination the
  // backend would reject makes the entry — and thus the Procedures node — unknown.
  const implementation = v.implementation_state;
  const validation = v.validation_state;
  const delivery = v.delivery_state;
  const fresh = v.latest_receipt_fresh;
  const findingsCount = v.findings.length;
  if (["missing", "scaffolded"].includes(implementation) && validation !== "untested") return false;
  if (validation === "e2e_passed" && !fresh) return false;
  if (["untested", "contract_passed", "integration_passed", "stale", "blocked_by_authority"].includes(validation)
      && fresh) return false;
  if (delivery === "fully_connected") return implementation === "enabled" && validation === "e2e_passed" && findingsCount === 0;
  if (validation === "e2e_passed") return delivery === "partially_landed" && findingsCount > 0 && ["landed", "enabled"].includes(implementation);
  if (validation === "e2e_failed") return delivery === "partially_landed" && findingsCount > 0;
  if (validation === "blocked_by_authority") return delivery === "blocked" && findingsCount > 0;
  if (validation === "stale") return delivery === "landed_unverified" && findingsCount > 0;
  if (validation === "untested") {
    if (["missing", "scaffolded"].includes(implementation)) return delivery === "pending" && findingsCount > 0;
    return delivery === "landed_unverified" && findingsCount > 0;
  }
  return ["in_progress", "partially_landed", "landed_unverified"].includes(delivery);
}

// Mirrors the backend recompute in agent_os_control_plane._procedure_validation_status:
// every entry well formed, procedure_id unique, and the seven aggregate counts equal
// the counts recomputed from the entries themselves.
function procedureEntriesReconcile(procAxis: Record<string, unknown>, entries: unknown[]): boolean {
  if (!entries.every((entry) => isValidProcedureEntryShape(entry))) return false;
  const rows = entries as Record<string, unknown>[];
  if (new Set(rows.map((row) => row.procedure_id)).size !== rows.length) return false;
  const required = rows.filter((row) => row.required === true);
  const recomputed: Record<string, number> = {
    procedures: rows.length,
    required_procedures: required.length,
    fully_connected: required.filter((row) => row.delivery_state === "fully_connected").length,
    blocked_by_authority: required.filter((row) => row.validation_state === "blocked_by_authority").length,
    pending: required.filter(
      (row) => row.delivery_state !== "fully_connected" && row.validation_state !== "blocked_by_authority",
    ).length,
    stale: required.filter((row) => row.validation_state === "stale").length,
    failed: required.filter((row) => row.validation_state === "e2e_failed").length,
  };
  return Object.entries(recomputed).every(([name, count]) => procAxis[name] === count);
}

function isValidProjectionShape(v: unknown): boolean {
  if (!isRecord(v)) return false;
  const rec = v as Record<string, unknown>;
  const path = rec.path;
  const nodeCount = rec.node_count;
  const runtimeMutation = rec.runtime_mutation;
  const hasPath = typeof path === "string" || path === undefined;
  const hasNodeCount = typeof nodeCount === "number" && Number.isFinite(nodeCount) && nodeCount >= 0 && Number.isInteger(nodeCount);
  const hasRuntime = typeof runtimeMutation === "boolean" || runtimeMutation === undefined;
  return hasPath && hasNodeCount && hasRuntime;
}

// Doctor findings arrive as plain strings or structured {level?, check, message}
// objects depending on backend version. Returns the actionable evidence string,
// or undefined when the finding is unrecognized.
function formatDoctorFinding(f: unknown): string | undefined {
  if (typeof f === "string") return f.trim().length > 0 ? f : undefined;
  if (!isRecord(f)) return undefined;
  const check = f.check;
  const message = f.message;
  const level = f.level;
  if (typeof check !== "string" || check.trim().length === 0) return undefined;
  if (typeof message !== "string" || message.trim().length === 0) return undefined;
  if (level !== undefined && (typeof level !== "string" || level.trim().length === 0)) return undefined;
  return level !== undefined ? `[${level}] ${check}: ${message}` : `${check}: ${message}`;
}

function isValidDoctorShape(v: unknown): boolean {
  if (!isRecord(v)) return false;
  const rec = v as Record<string, unknown>;
  const ok = rec.ok;
  const findings = rec.findings;
  const hasOk = typeof ok === "boolean";
  // any unrecognized finding fails the whole shape closed instead of being dropped
  const hasFindings = findings === undefined
    || (Array.isArray(findings) && findings.every((f) => formatDoctorFinding(f) !== undefined));
  return hasOk && hasFindings;
}

function isValidShadowShape(v: unknown): boolean {
  if (!isRecord(v)) return false;
  const rec = v as Record<string, unknown>;
  const path = rec.path;
  const okv = rec.ok;
  const records = rec.records;
  const blocked = rec.blocked_by_policy;
  const would = rec.would_execute_anything;
  const hasPath = typeof path === "string" || path === undefined;
  const hasOk = typeof okv === "boolean" || okv === undefined;
  const rn = typeof records === "number" && Number.isFinite(records) && records >= 0 && Number.isInteger(records);
  const bn = typeof blocked === "number" && Number.isFinite(blocked) && blocked >= 0 && Number.isInteger(blocked);
  const hasWould = typeof would === "boolean" || would === undefined;
  return hasPath && hasOk && rn && bn && hasWould && (blocked as number) <= (records as number);
}

function isValidOperationalShape(v: unknown): boolean {
  if (!isRecord(v)) return false;
  const rec = v as Record<string, unknown>;
  const state = rec.state;
  const f = rec.findings;
  if (typeof state !== "string" || typeof f !== "number") return false;
  if (!["unknown", "ready", "healthy", "degraded", "failed", "not_running"].includes(state)) return false;
  if (!Number.isFinite(f) || f < 0 || !Number.isInteger(f)) return false;
  if (state === "healthy") return f === 0;
  if (state === "degraded" || state === "failed") return f > 0;
  return true;
}

function isValidEngineeringCoverageShape(v: unknown): boolean {
  if (!isRecord(v)) return false;
  const rec = v as Record<string, unknown>;
  const systems = rec.systems;
  const missing = rec.missing_checks;
  const high = rec.high_risk_systems;
  const s = typeof systems === "number" && Number.isFinite(systems) && systems >= 0 && Number.isInteger(systems);
  const m = typeof missing === "number" && Number.isFinite(missing) && missing >= 0 && Number.isInteger(missing);
  const h = typeof high === "number" && Number.isFinite(high) && high >= 0 && Number.isInteger(high);
  if (!s || !m || !h) return false;
  return (missing as number) <= (systems as number) && (high as number) <= (systems as number);
}

function isValidCertificationShape(v: unknown): boolean {
  if (!isRecord(v)) return false;
  const rec = v as Record<string, unknown>;
  const certified = rec.certified_healthy;
  const eligible = rec.eligible;
  const boundary = rec.boundary;
  return typeof certified === "boolean"
    && typeof eligible === "boolean"
    && typeof boundary === "string";
}

function deriveIntakeLayer(status: AgentOsStatusResponse): ObservationLayer {
  const nodes: ObservationNode[] = [];
  const rec = status as unknown as Record<string, unknown>;
  const ok = typeof rec.ok === "boolean" ? rec.ok : undefined;
  const project = typeof rec.project === "string" ? rec.project : "unknown";
  const mode = typeof rec.mode === "string" ? rec.mode : "unknown";
  const runtimeEffect = typeof rec.runtime_effect === "string" ? rec.runtime_effect : "unknown";
  let intakeState: ObservationState = "unknown";
  let statusValue = "unknown";
  if (ok === true) {
    intakeState = "ok";
    statusValue = "ok";
  } else if (ok === false) {
    intakeState = "blocked";
    statusValue = "error";
  }
  nodes.push({
    id: "intake-status",
    label: "Intake status",
    state: intakeState,
    value: `${project} / ${mode}`,
    metrics: [
      { label: "Status", value: statusValue },
      { label: "Project", value: project },
      { label: "Mode", value: mode },
      { label: "Runtime effect", value: runtimeEffect },
    ],
    evidence: ["does not imply execution"],
  });
  return { key: "intake", label: "Intake", nodes };
}

function deriveGovernanceLayer(status: AgentOsStatusResponse): ObservationLayer {
  const nodes: ObservationNode[] = [];
  const sh = status.shadow;
  const shadowSource = artifactPath(sh);
  if (isValidShadowShape(sh)) {
    const rec = sh as Record<string, unknown>;
    const recNum = safeNumber(rec.records);
    const blk = safeNumber(rec.blocked_by_policy);
    const okv = safeBool(rec.ok);
    const wouldExec = safeBool(rec.would_execute_anything);
    if (recNum !== undefined && blk !== undefined) {
      let shadowState: ObservationState = "unknown";
      if (okv === true && wouldExec === false) {
        shadowState = "ok";
      } else if (okv === false || wouldExec === true) {
        shadowState = "degraded";
      } else {
        shadowState = "unknown";
      }
      nodes.push({
        id: "shadow-ledger",
        label: "Shadow ledger",
        state: shadowState,
        value: `${recNum}`,
        metrics: [
          { label: "Records", value: String(recNum) },
          { label: "Policy blocked", value: String(blk) },
        ],
        // affirmative evidence only from a successful shadow run; a failed run
        // (ok!==true) cannot attest that nothing executed
        evidence: okv === true && wouldExec === false ? ["observed no execution"] : [],
        ...(shadowSource !== undefined ? { source: shadowSource } : {}),
      });
    } else {
      nodes.push({
        id: "shadow-ledger",
        label: "Shadow ledger",
        state: "unknown",
        value: "Unavailable",
        metrics: [],
        ...(shadowSource !== undefined ? { source: shadowSource } : {}),
      });
    }
  } else {
    nodes.push({
      id: "shadow-ledger",
      label: "Shadow ledger",
      state: "unknown",
      value: "Unavailable",
      metrics: [],
      ...(shadowSource !== undefined ? { source: shadowSource } : {}),
    });
  }
  return { key: "governance", label: "Governance", nodes };
}

function deriveSystemsLayer(status: AgentOsStatusResponse): ObservationLayer {
  const nodes: ObservationNode[] = [];
  // Projection — unavailable when the status reports the projection file itself missing
  const proj = status.projection;
  const missingPaths = Array.isArray(status.missing) ? status.missing : [];
  const projPath = isRecord(proj) && typeof proj.path === "string" ? proj.path : undefined;
  const projectionMissing = projPath !== undefined && missingPaths.includes(projPath);
  const projSource = artifactPath(proj);
  if (!projectionMissing && isValidProjectionShape(proj)) {
    const rec = proj as Record<string, unknown>;
    const nodeCount = safeNumber(rec.node_count);
    const runtimeMutation = rec.runtime_mutation;
    // node_count=0 is the backend's malformed-projection sentinel (unreadable
    // JSON), so only a positive count is a usable readback
    if (nodeCount !== undefined && nodeCount > 0) {
      const metrics: ObservationMetric[] = [
        { label: "Projection nodes", value: String(nodeCount) },
      ];
      if (typeof runtimeMutation === "boolean") {
        metrics.push({ label: "Runtime mutation", value: String(runtimeMutation) });
      }
      let projState: ObservationState = "unknown";
      if (runtimeMutation === false) {
        projState = "ok";
      } else if (runtimeMutation === true) {
        projState = "degraded";
      } else {
        projState = "unknown";
      }
      nodes.push({
        id: "projection",
        label: "Projection",
        state: projState,
        value: String(nodeCount),
        metrics,
        ...(projSource !== undefined ? { source: projSource } : {}),
      });
    } else {
      nodes.push({
        id: "projection",
        label: "Projection",
        state: "unknown",
        value: "Unavailable",
        metrics: [],
        ...(projSource !== undefined ? { source: projSource } : {}),
      });
    }
  } else {
    nodes.push({
      id: "projection",
      label: "Projection",
      state: "unknown",
      value: "Unavailable",
      metrics: [],
      ...(projSource !== undefined ? { source: projSource } : {}),
    });
  }
  // Operational health
  const axes = isRecord(status.procedure_validation?.axes) ? status.procedure_validation.axes : {};
  const op = axes.operational_health;
  if (isValidOperationalShape(op)) {
    const rec = op as Record<string, unknown>;
    const stateStr = typeof rec.state === "string" ? rec.state : "unknown";
    const findings = safeNumber(rec.findings);
    if (findings !== undefined) {
      let state: ObservationState = "unknown";
      if (stateStr === "degraded") {
        state = "degraded";
      } else if (stateStr === "failed") {
        state = "blocked";
      } else if (stateStr === "healthy") {
        state = "ok";
      } else if (stateStr === "ready" || stateStr === "not_running") {
        state = "stale";
      }
      nodes.push({
        id: "operational-health",
        label: "Operational health",
        state,
        value: stateStr,
        metrics: [{ label: "Active findings", value: String(findings) }],
      });
    } else {
      nodes.push({
        id: "operational-health",
        label: "Operational health",
        state: "unknown",
        value: "Unavailable",
        metrics: [],
      });
    }
  } else {
    nodes.push({
      id: "operational-health",
      label: "Operational health",
      state: "unknown",
      value: "Unavailable",
      metrics: [],
    });
  }
  // Engineering coverage
  const eng = axes.engineering_coverage;
  if (isValidEngineeringCoverageShape(eng)) {
    const rec = eng as Record<string, unknown>;
    const systems = safeNumber(rec.systems)!;
    const missing = safeNumber(rec.missing_checks)!;
    const high = safeNumber(rec.high_risk_systems)!;
    const state: ObservationState = missing === 0 && high === 0 ? "ok" : "degraded";
    nodes.push({
      id: "engineering-coverage",
      label: "Engineering coverage",
      state,
      value: state,
      metrics: [
        { label: "Systems", value: String(systems) },
        { label: "Missing checks", value: String(missing) },
        { label: "High risk", value: String(high) },
      ],
    });
  } else {
    nodes.push({
      id: "engineering-coverage",
      label: "Engineering coverage",
      state: "unknown",
      value: "Unavailable",
      metrics: [],
    });
  }
  return { key: "systems", label: "Systems", nodes };
}

function deriveEvidenceLayer(status: AgentOsStatusResponse): ObservationLayer {
  const nodes: ObservationNode[] = [];
  const axes = isRecord(status.procedure_validation?.axes) ? status.procedure_validation.axes : {};
  // Doctor
  const doc = status.doctor;
  const docSource = artifactPath(doc);
  if (isValidDoctorShape(doc)) {
    const rec = doc as Record<string, unknown>;
    const ok = safeBool(rec.ok);
    // isValidDoctorShape guaranteed every finding recognized; format each into evidence
    const docFindings = Array.isArray(rec.findings)
      ? rec.findings.map(formatDoctorFinding).filter((f): f is string => f !== undefined)
      : [];
    let dstate: ObservationState = "unknown";
    let dvalue = "Unavailable";
    if (ok === true) {
      dstate = "ok";
      dvalue = "ok";
    } else if (ok === false) {
      dstate = "degraded";
      dvalue = "degraded";
    }
    nodes.push({
      id: "doctor",
      label: "Doctor",
      state: dstate,
      value: dvalue,
      metrics: [],
      evidence: docFindings,
      ...(docSource !== undefined ? { source: docSource } : {}),
    });
  } else {
    nodes.push({
      id: "doctor",
      label: "Doctor",
      state: "unknown",
      value: "Unavailable",
      metrics: [],
      ...(docSource !== undefined ? { source: docSource } : {}),
    });
  }
  // Procedures — only derivable from an available, trusted read-only projection
  const trusted = status.procedure_validation?.available === true
    && status.procedure_validation?.trusted_read_only === true;
  const procAxis = isRecord(axes.procedure_validation) ? axes.procedure_validation : undefined;
  const proceduresList = Array.isArray(status.procedure_validation?.procedures)
    ? status.procedure_validation.procedures
    : [];
  // Displayable evidence: non-empty string findings from the top-level
  // procedure_validation plus each procedure entry, deduped preserving order.
  // Evidence only — findings never promote node state.
  const procedureEvidence: string[] = [];
  const seenFindings = new Set<string>();
  const collectFinding = (f: unknown) => {
    if (typeof f === "string" && f.trim().length > 0 && !seenFindings.has(f)) {
      seenFindings.add(f);
      procedureEvidence.push(f);
    }
  };
  const topFindings = status.procedure_validation?.findings;
  if (Array.isArray(topFindings)) topFindings.forEach(collectFinding);
  for (const entry of proceduresList) {
    if (isRecord(entry) && Array.isArray(entry.findings)) entry.findings.forEach(collectFinding);
  }
  if (trusted
      && procAxis
      && typeof procAxis.required_procedures === "number"
      && typeof procAxis.fully_connected === "number"
      && typeof procAxis.stale === "number"
      && typeof procAxis.procedures === "number"
      && typeof procAxis.blocked_by_authority === "number"
      && typeof procAxis.pending === "number"
      && typeof procAxis.failed === "number"
      && typeof procAxis.inventory_complete === "boolean"
      && typeof procAxis.can_claim_complete_procedure_validation === "boolean"
      && proceduresList.length === procAxis.procedures
      && procedureEntriesReconcile(procAxis, proceduresList)) {
    const req = safeNumber(procAxis.required_procedures);
    const conn = safeNumber(procAxis.fully_connected);
    const stl = safeNumber(procAxis.stale);
    const authBlk = safeNumber(procAxis.blocked_by_authority);
    const pend = safeNumber(procAxis.pending);
    const fail = safeNumber(procAxis.failed);
    const invComplete = procAxis.inventory_complete === true;
    const canClaim = procAxis.can_claim_complete_procedure_validation === true;
    const allNonneg = [req, conn, stl, authBlk, pend, fail].every(v => v !== undefined);
    // countsValid: nonnegative + fully_connected+stale <= req (pending may overlap non-connected); full invariants enforced in state
    // require integers explicitly (not merely via safeNumber) so fractional -> unknown/Unavailable
    const countsValid = allNonneg &&
      Number.isInteger(req!) && Number.isInteger(conn!) && Number.isInteger(stl!) &&
      Number.isInteger(authBlk!) && Number.isInteger(pend!) && Number.isInteger(fail!) &&
      (conn! + stl! <= req!);
    if (req !== undefined && conn !== undefined && stl !== undefined && authBlk !== undefined && pend !== undefined && fail !== undefined && countsValid) {
      let pstate: ObservationState = "unknown";
      let pvalue = "unknown";
      const hasBad = (fail! > 0) || (authBlk! > 0) || (pend! > 0) || (stl! > 0) || (conn! < req!) || !invComplete || !canClaim;
      if ((fail! > 0) || (authBlk! > 0)) {
        pstate = "blocked";
        pvalue = "blocked";
      } else if (hasBad) {
        pstate = "stale";
        pvalue = "stale";
      } else {
        pstate = "ok";
        pvalue = "ok";
      }
      nodes.push({
        id: "procedures",
        label: "Procedures",
        state: pstate,
        value: pvalue,
        metrics: [
          { label: "Required procedures", value: String(req) },
          // fully_connected counts delivery state, not receipt freshness
          { label: "Fully connected", value: String(conn) },
          { label: "Stale", value: String(stl) },
          { label: "Failed", value: String(fail) },
          { label: "Authority blocked", value: String(authBlk) },
          { label: "Pending", value: String(pend) },
        ],
        evidence: procedureEvidence,
      });
    } else {
      nodes.push({
        id: "procedures",
        label: "Procedures",
        state: "unknown",
        value: "Unavailable",
        metrics: [],
        evidence: procedureEvidence,
      });
    }
  } else {
    nodes.push({
      id: "procedures",
      label: "Procedures",
      state: "unknown",
      value: "Unavailable",
      metrics: [],
      evidence: procedureEvidence,
    });
  }
  // Certification — never claims independent certification. certified_healthy=true
  // is an overclaim (no independent receipt exists in this readback) and fails
  // closed to Not eligible; eligible=true only surfaces a candidate value.
  const cert = isRecord(axes.certification) ? axes.certification : undefined;
  if (isValidCertificationShape(cert)) {
    const rec = cert as Record<string, unknown>;
    const boundary = typeof rec.boundary === "string" ? rec.boundary : "independent certification receipt required";
    const overclaimed = rec.certified_healthy === true;
    const eligible = rec.eligible === true;
    if (!overclaimed && eligible) {
      nodes.push({
        id: "certification",
        label: "Certification",
        state: "stale",
        value: "Eligible candidate · not certified",
        metrics: [
          { label: "Eligible", value: "true" },
          { label: "Certified", value: "false" },
        ],
        evidence: [boundary],
      });
    } else {
      nodes.push({
        id: "certification",
        label: "Certification",
        state: "blocked",
        value: "Not eligible",
        metrics: overclaimed ? [] : [
          { label: "Eligible", value: "false" },
          { label: "Certified", value: "false" },
        ],
        evidence: [boundary],
      });
    }
  } else {
    nodes.push({
      id: "certification",
      label: "Certification",
      state: "unknown",
      value: "Unavailable",
      metrics: [],
    });
  }
  return { key: "evidence", label: "Evidence", nodes };
}

export function buildAgentOsObservation(status: AgentOsStatusResponse): AgentOsObservation {
  if (!status || typeof status !== "object" || !status.procedure_validation) {
    // fail closed
    return {
      layers: [
        { key: "intake", label: "Intake", nodes: [] },
        { key: "governance", label: "Governance", nodes: [] },
        { key: "systems", label: "Systems", nodes: [] },
        { key: "evidence", label: "Evidence", nodes: [] },
      ],
    };
  }

  return {
    layers: [
      deriveIntakeLayer(status),
      deriveGovernanceLayer(status),
      deriveSystemsLayer(status),
      deriveEvidenceLayer(status),
    ],
  };
}

// Pure boundary-banner derivation (kept out of the component so it is unit
// testable without DOM). Shadow sentence: unknown -> unavailable; ok with
// explicit no-execution evidence -> evidence-only; degraded -> adverse/failed
// readback, which is neither "unavailable" nor a no-execution claim.
export function deriveBoundaryText(
  shadowNode: ObservationNode | null | undefined,
  certNode: ObservationNode | null | undefined,
): string {
  let shadowSentence = "Shadow execution readback unavailable.";
  if (shadowNode?.state === "degraded") {
    shadowSentence = "Shadow readback reports an adverse or failed run; execution safety is not affirmed.";
  } else if (
    shadowNode
    && shadowNode.state === "ok"
    && shadowNode.evidence?.some((e) => e.includes("no execution"))
  ) {
    shadowSentence = "Configured capability is not execution. Shadow observed no execution is evidence only.";
  }
  const certAvailable = Boolean(
    certNode && certNode.state !== "unknown" && certNode.value && certNode.value !== "Unavailable",
  );
  const certSentence = certAvailable && certNode
    ? `Certification remains ${certNode.value}.`
    : "Certification readback unavailable.";
  return `${shadowSentence} ${certSentence}`;
}
