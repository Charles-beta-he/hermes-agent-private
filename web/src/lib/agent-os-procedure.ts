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

export function buildAgentOsAxisCards(status: AgentOsStatusResponse): AgentOsAxisCard[] {
  const axes = status.procedure_validation.axes;
  const engineering = axes.engineering_coverage;
  const operations = axes.operational_health;
  const procedures = axes.procedure_validation;
  const certification = axes.certification;
  const engineeringConverged = Boolean(
    engineering && engineering.missing_checks === 0 && engineering.high_risk_systems === 0,
  );
  const operationalHealthy = operations?.state === "healthy";
  const proceduresComplete = Boolean(procedures?.can_claim_complete_procedure_validation);
  const certified = Boolean(certification?.certified_healthy);

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
      value: procedures ? `${procedures.fully_connected} / ${procedures.required_procedures}` : "Unknown",
      detail: procedures
        ? `${procedures.blocked_by_authority} authority blocked · ${procedures.pending} pending · ${procedures.failed} failed`
        : "Projection unavailable",
      tone: proceduresComplete ? "success" : procedures?.failed ? "destructive" : "warning",
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
