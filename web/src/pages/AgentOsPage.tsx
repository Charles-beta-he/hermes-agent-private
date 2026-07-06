import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BrainCircuit,
  Database,
  Layers3,
  ListChecks,
  Network,
  RefreshCw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { H2 } from "@nous-research/ui/ui/components/typography/h2";
import { api, type AgentOsReceiptResponse, type AgentOsStatusResponse } from "@/lib/api";
import { cn, themedBody } from "@/lib/utils";

function boolLabel(value: boolean | undefined): "yes" | "no" | "unknown" {
  if (value === true) return "yes";
  if (value === false) return "no";
  return "unknown";
}

function countMap(map: Record<string, number> | undefined): string {
  if (!map || !Object.keys(map).length) return "none";
  return Object.entries(map)
    .map(([key, value]) => `${key}: ${value}`)
    .join(" · ");
}

function asText(value: unknown, fallback = "—"): string {
  if (value === null || value === undefined || value === "") return fallback;
  if (Array.isArray(value)) return value.map((item) => asText(item)).join(" · ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function matchesQuery(value: unknown, query: string): boolean {
  const trimmed = query.trim().toLowerCase();
  if (!trimmed) return true;
  return asText(value, "").toLowerCase().includes(trimmed);
}

function getQueryValue(key: string): string {
  if (typeof window === "undefined") return "";
  return new URLSearchParams(window.location.search).get(key) ?? "";
}

function syncQueryFilters(filters: Record<string, string>): void {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  for (const [key, value] of Object.entries(filters)) {
    const trimmed = value.trim();
    if (trimmed) url.searchParams.set(key, trimmed);
    else url.searchParams.delete(key);
  }
  const next = `${url.pathname}${url.search}${url.hash}`;
  const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (next !== current) window.history.replaceState(null, "", next);
}

function FilterBox({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block text-xs font-medium uppercase tracking-wide text-muted-foreground">
      {label}
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-2 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm normal-case tracking-normal text-foreground outline-none ring-offset-background placeholder:text-muted-foreground focus:ring-2 focus:ring-ring"
        placeholder="filter by id, action, risk, target…"
      />
    </label>
  );
}

function MetricCard({
  title,
  value,
  detail,
  icon: Icon,
}: {
  title: string;
  value: string | number | undefined;
  detail: string;
  icon: typeof BrainCircuit;
}) {
  return (
    <Card>
      <CardContent className="py-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {title}
            </p>
            <p className="mt-2 text-2xl font-semibold text-foreground">
              {value ?? "—"}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">{detail}</p>
          </div>
          <span className="rounded-2xl border border-border bg-muted/40 p-2 text-muted-foreground">
            <Icon className="h-5 w-5" />
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

function BoundaryRow({ label, value }: { label: string; value: boolean | undefined }) {
  const safe = value === false;
  return (
    <div className="flex items-center justify-between gap-3 rounded-lg border border-border bg-background/60 px-3 py-2 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <Badge tone={safe ? "success" : value === true ? "destructive" : "warning"}>
        {boolLabel(value)}
      </Badge>
    </div>
  );
}

async function sha256Hex(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function buildHandoffSummary({
  receipt,
  filters,
  href,
}: {
  receipt: AgentOsReceiptResponse;
  filters: Record<string, string>;
  href: string;
}): string {
  const activeFilters = Object.entries(filters)
    .filter(([, value]) => value.trim())
    .map(([key, value]) => `${key}=${value.trim()}`)
    .join(" · ") || "none";
  const counts = Object.entries(receipt.counts)
    .map(([key, value]) => `${key}: ${value ?? "—"}`)
    .join(" · ");
  const boundaries = Object.entries(receipt.boundary)
    .map(([key, value]) => `${key}: ${value ? "OPEN" : "closed"}`)
    .join(" · ");
  const external = receipt.open_external_dependencies
    .map((item) => `${asText(item.id)} (${asText(item.status)})`)
    .join(" · ") || "none";
  return [
    "Agent OS handoff summary",
    `schema: ${receipt.schema}`,
    `ok: ${receipt.ok}`,
    `link: ${href}`,
    `filters: ${activeFilters}`,
    `counts: ${counts}`,
    `boundaries: ${boundaries}`,
    `open_external_dependencies: ${external}`,
    `side_effects_executed: ${receipt.side_effects_executed}`,
  ].join("\n");
}

export default function AgentOsPage() {
  const [state, setState] = useState<AgentOsStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [evidenceFilter, setEvidenceFilter] = useState(() => getQueryValue("evidence"));
  const [authorityFilter, setAuthorityFilter] = useState(() => getQueryValue("authority"));
  const [proposalFilter, setProposalFilter] = useState(() => getQueryValue("proposal"));
  const [deviceFilter, setDeviceFilter] = useState(() => getQueryValue("device"));
  const [receipt, setReceipt] = useState<AgentOsReceiptResponse | null>(null);
  const [receiptError, setReceiptError] = useState<string | null>(null);
  const [receiptLoading, setReceiptLoading] = useState(false);
  const [handoffSummary, setHandoffSummary] = useState("");
  const [handoffHash, setHandoffHash] = useState("");
  const [handoffLoading, setHandoffLoading] = useState(false);

  const evidenceNodes = useMemo(
    () => (state?.mission_runtime.evidence_nodes ?? []).filter((node) => matchesQuery(node, evidenceFilter)),
    [evidenceFilter, state?.mission_runtime.evidence_nodes],
  );
  const authorityRecords = useMemo(
    () => (state?.authority.records ?? []).filter((record) => matchesQuery(record, authorityFilter)),
    [authorityFilter, state?.authority.records],
  );
  const proposals = useMemo(
    () => (state?.device_agent.agent_proposal_inbox ?? []).filter((proposal) => matchesQuery(proposal, proposalFilter)),
    [proposalFilter, state?.device_agent.agent_proposal_inbox],
  );
  const devices = useMemo(
    () => (state?.device_agent.device_nodes ?? []).filter((device) => matchesQuery(device, deviceFilter)),
    [deviceFilter, state?.device_agent.device_nodes],
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setState(await api.getAgentOsStatus());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const loadReceipt = useCallback(async () => {
    setReceiptLoading(true);
    setReceiptError(null);
    try {
      setReceipt(await api.getAgentOsReceipt());
    } catch (err) {
      setReceiptError(err instanceof Error ? err.message : String(err));
    } finally {
      setReceiptLoading(false);
    }
  }, []);

  const buildHandoff = useCallback(async () => {
    setHandoffLoading(true);
    setReceiptError(null);
    try {
      const nextReceipt = receipt ?? await api.getAgentOsReceipt();
      setReceipt(nextReceipt);
      const summary = buildHandoffSummary({
        receipt: nextReceipt,
        href: typeof window === "undefined" ? "" : window.location.href,
        filters: {
          evidence: evidenceFilter,
          authority: authorityFilter,
          proposal: proposalFilter,
          device: deviceFilter,
        },
      });
      setHandoffSummary(summary);
      setHandoffHash(await sha256Hex(summary));
    } catch (err) {
      setReceiptError(err instanceof Error ? err.message : String(err));
    } finally {
      setHandoffLoading(false);
    }
  }, [authorityFilter, deviceFilter, evidenceFilter, proposalFilter, receipt]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    syncQueryFilters({
      evidence: evidenceFilter,
      authority: authorityFilter,
      proposal: proposalFilter,
      device: deviceFilter,
    });
    setHandoffSummary("");
    setHandoffHash("");
  }, [authorityFilter, deviceFilter, evidenceFilter, proposalFilter]);

  useEffect(() => {
    const onPopState = () => {
      setEvidenceFilter(getQueryValue("evidence"));
      setAuthorityFilter(getQueryValue("authority"));
      setProposalFilter(getQueryValue("proposal"));
      setDeviceFilter(getQueryValue("device"));
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  return (
    <div className="space-y-6 p-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="mb-2 flex items-center gap-2">
            <Badge tone={state?.ok ? "success" : error ? "destructive" : "warning"}>
              {state?.ok ? "projection ok" : error ? "readback failed" : "loading"}
            </Badge>
            <Badge tone="secondary">read-only</Badge>
          </div>
          <H2>Agent OS Control Plane</H2>
          <p className={cn(themedBody, "mt-2 max-w-3xl text-sm")}>
            Personal AI OS / Web4.0 Agent OS projection. This page only reads the
            local packet; it does not execute capabilities, mutate task lifecycle,
            dispatch external messages, reveal secrets, pay, or control devices.
          </p>
        </div>
        <Button ghost onClick={load} disabled={loading}>
          {loading ? <Spinner className="mr-2 h-4 w-4" /> : <RefreshCw className="mr-2 h-4 w-4" />}
          Refresh
        </Button>
      </div>

      {error && (
        <Card>
          <CardContent className="py-4 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      {!state && !error && (
        <Card>
          <CardContent className="flex items-center gap-3 py-8 text-muted-foreground">
            <Spinner className="h-4 w-4" /> Loading Agent OS projection…
          </CardContent>
        </Card>
      )}

      {state && (
        <>
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <MetricCard
              title="Capabilities"
              value={state.capabilities.node_count}
              detail={countMap(state.capabilities.source_counts)}
              icon={Network}
            />
            <MetricCard
              title="Backlog"
              value={state.backlog.count}
              detail={countMap(state.backlog.by_status)}
              icon={ListChecks}
            />
            <MetricCard
              title="Authority"
              value={state.authority.record_count}
              detail="records projected, no runtime mutation"
              icon={ShieldCheck}
            />
            <MetricCard
              title="Evidence"
              value={state.mission_runtime.evidence_node_count}
              detail={`mission stage: ${state.mission_runtime.stage ?? "unknown"}`}
              icon={Layers3}
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardContent className="space-y-3 py-4">
                <div className="flex items-center gap-2">
                  <ShieldCheck className="h-4 w-4 text-muted-foreground" />
                  <h3 className="font-semibold">Authority boundaries</h3>
                </div>
                <BoundaryRow
                  label="Capability execution"
                  value={state.capabilities.would_execute}
                />
                <BoundaryRow
                  label="External dispatch"
                  value={state.authority.external_side_effects_executed}
                />
                <BoundaryRow
                  label="Runtime mutation"
                  value={state.authority.runtime_mutation}
                />
                <BoundaryRow
                  label="Payment / credential action"
                  value={state.authority.payment_or_credential_action_executed}
                />
                <BoundaryRow
                  label="Physical device effect"
                  value={state.device_agent.device_physical_effect_executed}
                />
              </CardContent>
            </Card>

            <Card>
              <CardContent className="space-y-3 py-4">
                <div className="flex items-center gap-2">
                  <Database className="h-4 w-4 text-muted-foreground" />
                  <h3 className="font-semibold">Data / model state</h3>
                </div>
                <div className="rounded-lg border border-border bg-background/60 p-3 text-sm">
                  <p className="text-muted-foreground">Data Vault</p>
                  <p className="mt-1 font-medium">
                    {state.data_vault.item_count ?? 0} items · {countMap(state.data_vault.sensitivity_counts)}
                  </p>
                </div>
                <div className="rounded-lg border border-border bg-background/60 p-3 text-sm">
                  <p className="text-muted-foreground">Model governance</p>
                  <p className="mt-1 font-medium">
                    ceiling {state.model_review.automation_ceiling ?? "unknown"} · live fanout {boolLabel(state.model_review.live_model_fanout_executed)}
                  </p>
                </div>
                <div className="rounded-lg border border-border bg-background/60 p-3 text-sm">
                  <p className="text-muted-foreground">UX shell panels</p>
                  <p className="mt-1 font-medium">
                    {state.ux_shell.panels.length ? state.ux_shell.panels.join(" · ") : "none"}
                  </p>
                </div>
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardContent className="space-y-3 py-4">
                <div className="flex items-center gap-2">
                  <Layers3 className="h-4 w-4 text-muted-foreground" />
                  <h3 className="font-semibold">Mission evidence DAG</h3>
                </div>
                <p className="text-sm text-muted-foreground">
                  {state.mission_runtime.objective ?? "Objective not projected"}
                </p>
                <FilterBox label="Filter evidence" value={evidenceFilter} onChange={setEvidenceFilter} />
                <p className="text-xs text-muted-foreground">Showing {evidenceNodes.length} of {state.mission_runtime.evidence_nodes?.length ?? 0} evidence nodes.</p>
                {evidenceNodes.slice(0, 6).map((node, index) => (
                  <div key={asText(node.id, String(index))} className="rounded-lg border border-border bg-background/60 p-3 text-sm">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-medium">{asText(node.id)}</span>
                      <Badge tone={asText(node.verdict) === "pass" ? "success" : "warning"}>{asText(node.verdict)}</Badge>
                    </div>
                    <p className="mt-1 text-muted-foreground">{asText(node.claim)}</p>
                    <p className="mt-1 break-all text-xs text-muted-foreground">{asText(node.artifact_path)}</p>
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card>
              <CardContent className="space-y-3 py-4">
                <div className="flex items-center gap-2">
                  <ShieldCheck className="h-4 w-4 text-muted-foreground" />
                  <h3 className="font-semibold">Authority ledger detail</h3>
                </div>
                <FilterBox label="Filter authority" value={authorityFilter} onChange={setAuthorityFilter} />
                <p className="text-xs text-muted-foreground">Showing {authorityRecords.length} of {state.authority.records?.length ?? 0} authority records.</p>
                {authorityRecords.slice(0, 5).map((record, index) => (
                  <div key={asText(record.id, String(index))} className="rounded-lg border border-border bg-background/60 p-3 text-sm">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-medium">{asText(record.action)}</span>
                      <Badge tone={asText(record.status) === "verified" ? "success" : "warning"}>{asText(record.status)}</Badge>
                    </div>
                    <p className="mt-1 text-muted-foreground">{asText(record.scope)}</p>
                    <p className="mt-1 text-xs text-muted-foreground">risk {asText(record.risk_level)} · target {asText(record.target)}</p>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardContent className="space-y-3 py-4">
                <div className="flex items-center gap-2">
                  <Sparkles className="h-4 w-4 text-muted-foreground" />
                  <h3 className="font-semibold">Proposal inbox</h3>
                </div>
                <FilterBox label="Filter proposals" value={proposalFilter} onChange={setProposalFilter} />
                <p className="text-xs text-muted-foreground">Showing {proposals.length} of {state.device_agent.agent_proposal_inbox?.length ?? 0} proposals.</p>
                {proposals.map((proposal, index) => (
                  <div key={asText(proposal.id, String(index))} className="rounded-lg border border-border bg-background/60 p-3 text-sm">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-medium">{asText(proposal.id)}</span>
                      <Badge tone="secondary">{asText(proposal.status)}</Badge>
                    </div>
                    <p className="mt-1 text-muted-foreground">{asText(proposal.objective)}</p>
                    <p className="mt-1 text-xs text-muted-foreground">action {asText(proposal.proposed_action)} · risk {asText(proposal.risk)}</p>
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card>
              <CardContent className="space-y-3 py-4">
                <div className="flex items-center gap-2">
                  <Network className="h-4 w-4 text-muted-foreground" />
                  <h3 className="font-semibold">Device / service contracts</h3>
                </div>
                <FilterBox label="Filter devices" value={deviceFilter} onChange={setDeviceFilter} />
                <p className="text-xs text-muted-foreground">Showing {devices.length} of {state.device_agent.device_nodes?.length ?? 0} device nodes.</p>
                {devices.map((device, index) => (
                  <div key={asText(device.id, String(index))} className="rounded-lg border border-border bg-background/60 p-3 text-sm">
                    <div className="flex items-center justify-between gap-3">
                      <span className="font-medium">{asText(device.id)}</span>
                      <Badge tone={device.physical_effect === false ? "success" : "warning"}>physical {boolLabel(device.physical_effect as boolean | undefined)}</Badge>
                    </div>
                    <p className="mt-1 text-muted-foreground">{asText(device.capabilities)}</p>
                    <p className="mt-1 text-xs text-muted-foreground">mode {asText(device.mode)} · safety {asText(device.safety_level)}</p>
                  </div>
                ))}
                {(state.device_agent.service_contract_stubs ?? []).map((service, index) => (
                  <div key={asText(service.id, `service-${index}`)} className="rounded-lg border border-border bg-muted/30 p-3 text-sm">
                    <span className="font-medium">{asText(service.id)}</span>
                    <p className="mt-1 text-muted-foreground">{asText(service.side_effects)} · {asText(service.verification)}</p>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardContent className="space-y-3 py-4">
              <div className="flex items-center gap-2">
                <ListChecks className="h-4 w-4 text-muted-foreground" />
                <h3 className="font-semibold">External dependency inbox</h3>
              </div>
              {(state.backlog.external_dependencies ?? []).map((dependency, index) => (
                <div key={asText(dependency.id, String(index))} className="rounded-lg border border-border bg-background/60 p-3 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{asText(dependency.id)}</span>
                    <Badge tone="warning">{asText(dependency.status)}</Badge>
                    <Badge tone="secondary">{asText(dependency.automation_level)}</Badge>
                  </div>
                  <p className="mt-1 text-muted-foreground">{asText(dependency.title)}</p>
                  <p className="mt-1 text-xs text-muted-foreground">{asText(dependency.boundary)}</p>
                </div>
              ))}
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-3 py-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-center gap-2">
                  <ShieldCheck className="h-4 w-4 text-muted-foreground" />
                  <h3 className="font-semibold">Receipt export packet</h3>
                </div>
                <Button ghost onClick={loadReceipt} disabled={receiptLoading || handoffLoading}>
                  {receiptLoading ? <Spinner className="mr-2 h-4 w-4" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                  Build receipt
                </Button>
                <Button ghost onClick={buildHandoff} disabled={receiptLoading || handoffLoading}>
                  {handoffLoading ? <Spinner className="mr-2 h-4 w-4" /> : <ListChecks className="mr-2 h-4 w-4" />}
                  Build handoff
                </Button>
              </div>
              <p className="text-sm text-muted-foreground">
                Builds a local read-only receipt packet or handoff summary from the same projection data. It does not export files, dispatch messages, write clipboard content, or unlock execution.
              </p>
              {receiptError && <p className="text-sm text-destructive">{receiptError}</p>}
              {handoffHash && (
                <div className="rounded-lg border border-border bg-background/60 p-3 text-xs">
                  <p className="font-medium text-foreground">handoff_sha256</p>
                  <p className="mt-1 break-all font-mono text-muted-foreground">{handoffHash}</p>
                </div>
              )}
              {handoffSummary && (
                <textarea
                  readOnly
                  value={handoffSummary}
                  className="min-h-48 w-full resize-y rounded-lg border border-border bg-muted/30 p-3 font-mono text-xs text-muted-foreground outline-none"
                  aria-label="Agent OS handoff summary"
                />
              )}
              {receipt && (
                <pre className="max-h-80 overflow-auto rounded-lg border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
                  {JSON.stringify(receipt, null, 2)}
                </pre>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardContent className="py-4">
              <div className="flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-muted-foreground" />
                <h3 className="font-semibold">External ecosystem boundary</h3>
              </div>
              <p className="mt-2 text-sm text-muted-foreground">
                Open external dependencies remain structured backlog records, not stop conditions:
                DID/VC, wallet/payment rails, legal authority, hardware runtime, and service-contract ecosystems stay proposal-only until a safe authority adapter exists.
              </p>
              <p className="mt-3 break-all rounded-lg border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
                {state.packet_dir}
              </p>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
