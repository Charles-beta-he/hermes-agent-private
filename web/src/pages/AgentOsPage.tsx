import { useCallback, useEffect, useState } from "react";
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
import { api, type AgentOsStatusResponse } from "@/lib/api";
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

export default function AgentOsPage() {
  const [state, setState] = useState<AgentOsStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

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

  useEffect(() => {
    load();
  }, [load]);

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
