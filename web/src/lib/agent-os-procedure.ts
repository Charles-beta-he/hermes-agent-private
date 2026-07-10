import type { AgentOsStatusResponse } from "./api";

export type AgentOsTone = "success" | "warning" | "destructive" | "secondary";

export interface AgentOsAxisCard {
  key: "engineering" | "operations" | "procedures" | "certification";
  label: string;
  value: string;
  detail: string;
  tone: AgentOsTone;
}

function countLabel(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasNonNegativeIntegerCounts(value: Record<string, unknown>, fields: string[]): boolean {
  return fields.every((field) => Number.isInteger(value[field]) && (value[field] as number) >= 0);
}

function hasExactKeys(value: Record<string, unknown>, fields: string[]): boolean {
  return Object.keys(value).length === fields.length && fields.every((field) => field in value);
}

function isEngineeringAxis(value: unknown): value is { systems: number; missing_checks: number; high_risk_systems: number } {
  const fields = ["systems", "missing_checks", "high_risk_systems"];
  if (!isRecord(value) || !hasExactKeys(value, fields) || !hasNonNegativeIntegerCounts(value, fields)) return false;
  return (value.missing_checks as number) <= (value.systems as number)
    && (value.high_risk_systems as number) <= (value.systems as number);
}

function isOperationalAxis(value: unknown): value is { state: string; findings: number } {
  if (!isRecord(value)
      || !hasExactKeys(value, ["state", "findings"])
      || typeof value.state !== "string"
      || !new Set(["unknown", "ready", "healthy", "degraded", "failed", "not_running"]).has(value.state)
      || !hasNonNegativeIntegerCounts(value, ["findings"])) return false;
  if (value.state === "healthy") return value.findings === 0;
  if (value.state === "degraded" || value.state === "failed") return (value.findings as number) > 0;
  return true;
}

function isProcedureAxis(value: unknown): value is {
  procedures: number;
  required_procedures: number;
  fully_connected: number;
  blocked_by_authority: number;
  pending: number;
  stale: number;
  failed: number;
  inventory_complete: boolean;
  can_claim_complete_procedure_validation: boolean;
} {
  if (!isRecord(value)
      || !hasExactKeys(value, [
        "procedures", "required_procedures", "fully_connected", "blocked_by_authority", "pending", "stale", "failed",
        "inventory_complete", "can_claim_complete_procedure_validation",
      ])
      || !hasNonNegativeIntegerCounts(value, [
        "procedures", "required_procedures", "fully_connected", "blocked_by_authority", "pending", "stale", "failed",
      ])
      || typeof value.inventory_complete !== "boolean"
      || typeof value.can_claim_complete_procedure_validation !== "boolean") return false;
  const procedures = value.procedures as number;
  const required = value.required_procedures as number;
  const fullyConnected = value.fully_connected as number;
  const blocked = value.blocked_by_authority as number;
  const pending = value.pending as number;
  const aggregateValid = procedures >= required
    && fullyConnected + blocked + pending === required
    && (value.stale as number) <= pending
    && (value.failed as number) <= pending;
  if (!aggregateValid) return false;
  if (value.can_claim_complete_procedure_validation) {
    return required > 0
      && value.inventory_complete === true
      && fullyConnected === required
      && blocked === 0
      && pending === 0
      && value.stale === 0
      && value.failed === 0;
  }
  return true;
}

function isCertificationAxis(value: unknown): value is { certified_healthy: boolean; eligible: boolean; boundary: string } {
  return isRecord(value)
    && hasExactKeys(value, ["certified_healthy", "eligible", "boundary"])
    && typeof value.certified_healthy === "boolean"
    && typeof value.eligible === "boolean"
    && typeof value.boundary === "string";
}

function isProcedureEntry(entry: unknown): entry is Record<string, unknown> {
  if (!isRecord(entry)
      || new Set(Object.keys(entry)).size !== 8
      || !["procedure_id", "system_id", "required", "implementation_state", "validation_state", "delivery_state", "latest_receipt_fresh", "findings"].every((field) => field in entry)
      || typeof entry.procedure_id !== "string" || entry.procedure_id.trim().length === 0
      || typeof entry.system_id !== "string" || entry.system_id.trim().length === 0
      || typeof entry.required !== "boolean"
      || typeof entry.latest_receipt_fresh !== "boolean"
      || typeof entry.implementation_state !== "string"
      || typeof entry.validation_state !== "string"
      || typeof entry.delivery_state !== "string"
      || !Array.isArray(entry.findings)
      || !entry.findings.every((finding) => typeof finding === "string" && finding.trim().length > 0)) return false;
  const implementation = String(entry.implementation_state);
  const validation = String(entry.validation_state);
  const delivery = String(entry.delivery_state);
  if (!new Set(["missing", "scaffolded", "landed", "enabled"]).has(implementation)
      || !new Set(["untested", "contract_passed", "integration_passed", "e2e_passed", "e2e_failed", "stale", "blocked_by_authority"]).has(validation)
      || !new Set(["pending", "in_progress", "partially_landed", "landed_unverified", "fully_connected", "blocked", "not_applicable"]).has(delivery)) return false;
  if (["missing", "scaffolded"].includes(implementation) && validation !== "untested") return false;
  if (validation === "e2e_passed" && entry.latest_receipt_fresh !== true) return false;
  if (["untested", "contract_passed", "integration_passed", "stale", "blocked_by_authority"].includes(validation)
      && entry.latest_receipt_fresh === true) return false;
  if (delivery === "fully_connected") return implementation === "enabled" && validation === "e2e_passed" && entry.findings.length === 0;
  if (validation === "e2e_passed") return delivery === "partially_landed" && entry.findings.length > 0 && ["landed", "enabled"].includes(implementation);
  if (validation === "e2e_failed") return delivery === "partially_landed" && entry.findings.length > 0;
  if (validation === "blocked_by_authority") return delivery === "blocked" && entry.findings.length > 0;
  if (validation === "stale") return delivery === "landed_unverified" && entry.findings.length > 0;
  if (validation === "untested") {
    if (["missing", "scaffolded"].includes(implementation)) return delivery === "pending" && entry.findings.length > 0;
    return delivery === "landed_unverified" && entry.findings.length > 0;
  }
  return ["in_progress", "partially_landed", "landed_unverified"].includes(delivery);
}

function procedureEntriesMatchAggregate(
  entries: unknown,
  axis: ReturnType<typeof procedureAxisOrUndefined>,
): boolean {
  if (!axis || !Array.isArray(entries) || entries.length !== axis.procedures) return false;
  if (!entries.every(isProcedureEntry)) return false;
  const rows = entries as Array<Record<string, unknown>>;
  if (new Set(rows.map((entry) => entry.procedure_id)).size !== rows.length) return false;
  const required = rows.filter((entry) => entry.required === true);
  const fullyConnected = required.filter((entry) => entry.delivery_state === "fully_connected").length;
  const blocked = required.filter((entry) => entry.validation_state === "blocked_by_authority").length;
  const pendingRows = required.filter((entry) => entry.delivery_state !== "fully_connected"
    && entry.validation_state !== "blocked_by_authority");
  return required.length === axis.required_procedures
    && fullyConnected === axis.fully_connected
    && blocked === axis.blocked_by_authority
    && pendingRows.length === axis.pending
    && pendingRows.filter((entry) => entry.validation_state === "stale").length === axis.stale
    && pendingRows.filter((entry) => entry.validation_state === "e2e_failed").length === axis.failed;
}

function procedureAxisOrUndefined(value: unknown) {
  return isProcedureAxis(value) ? value : undefined;
}

export function buildAgentOsAxisCards(status: AgentOsStatusResponse): AgentOsAxisCard[] {
  const rawAxes: unknown = status.procedure_validation.axes;
  const axes = isRecord(rawAxes) && hasExactKeys(rawAxes, [
    "engineering_coverage", "operational_health", "procedure_validation", "certification",
  ]) ? rawAxes : {};
  const engineering = isEngineeringAxis(axes.engineering_coverage) ? axes.engineering_coverage : undefined;
  const operations = isOperationalAxis(axes.operational_health) ? axes.operational_health : undefined;
  const procedures = procedureAxisOrUndefined(axes.procedure_validation);
  const certification = isCertificationAxis(axes.certification) ? axes.certification : undefined;
  const engineeringConverged = Boolean(
    engineering && engineering.missing_checks === 0 && engineering.high_risk_systems === 0,
  );
  const operationalHealthy = operations?.state === "healthy";
  const entriesMatch = procedureEntriesMatchAggregate(status.procedure_validation.procedures, procedures);
  const reconciledProcedures = entriesMatch ? procedures : undefined;
  const proceduresComplete = Boolean(
    status.procedure_validation.trusted_read_only === true
      && reconciledProcedures?.can_claim_complete_procedure_validation,
  );
  // The current response schema has no independently verifiable certification
  // receipt, so the browser must never promote a positive certification claim.
  const certified = false;

  return [
    {
      key: "engineering",
      label: "Engineering coverage",
      value: engineeringConverged ? "Converged" : "Attention needed",
      detail: engineering
        ? `${engineering.systems} systems · ${engineering.missing_checks} missing · ${engineering.high_risk_systems} high risk`
        : "Projection unavailable",
      tone: engineeringConverged ? "success" : "warning",
    },
    {
      key: "operations",
      label: "Operational health",
      value: operations ? operations.state[0].toUpperCase() + operations.state.slice(1) : "Unknown",
      detail: operations ? countLabel(operations.findings, "active finding") : "Projection unavailable",
      tone: operationalHealthy ? "success" : operations ? "warning" : "secondary",
    },
    {
      key: "procedures",
      label: "Procedure validation",
      value: reconciledProcedures ? `${reconciledProcedures.fully_connected} / ${reconciledProcedures.required_procedures}` : "Unknown",
      detail: reconciledProcedures
        ? `${reconciledProcedures.blocked_by_authority} authority blocked · ${reconciledProcedures.pending} pending · ${reconciledProcedures.failed} failed`
        : "Projection unavailable",
      tone: proceduresComplete ? "success" : reconciledProcedures?.failed ? "destructive" : "warning",
    },
    {
      key: "certification",
      label: "Certification",
      value: certified ? "Certified healthy" : "Not certified",
      detail: certification?.boundary ?? "Independent certification unavailable",
      tone: certified ? "success" : "destructive",
    },
  ];
}

export function procedureTone(validationState: string): AgentOsTone {
  if (validationState === "e2e_passed") return "success";
  if (validationState === "blocked_by_authority" || validationState === "stale") return "warning";
  if (validationState === "e2e_failed") return "destructive";
  return "secondary";
}
