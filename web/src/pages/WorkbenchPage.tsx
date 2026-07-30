import { useCallback, useEffect, useLayoutEffect, useMemo, useState } from "react";
import { AlertCircle, Command, RefreshCw, Zap } from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { usePageHeader } from "@/contexts/usePageHeader";
import { PluginSlot } from "@/plugins";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import type { AgentOsStatusResponse } from "@/lib/api";
import {
  buildAgentOsObservation,
  deriveBoundaryText,
  type AgentOsObservation,
  type ObservationLayer,
  type ObservationNode,
} from "@/lib/agent-os-observation";

function getStateClasses(state: string): string {
  switch (state) {
    case "ok":
      return "border-success/40 bg-success/10 text-success";
    case "degraded":
      return "border-warning/40 bg-warning/10 text-warning";
    case "stale":
      return "border-warning/40 bg-warning/10 text-warning";
    case "blocked":
      return "border-destructive/40 bg-destructive/10 text-destructive";
    default:
      return "border-border/40 bg-muted/10 text-muted-foreground";
  }
}

function getStateText(state: string): string {
  return state;
}

interface SummaryCard {
  label: string;
  value: string;
  state: string;
  metrics: { label: string; value: string }[];
}

export default function WorkbenchPage() {
  const { setAfterTitle, setEnd } = usePageHeader();

  const [status, setStatus] = useState<AgentOsStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [command, setCommand] = useState("Map current evidence gates before action");
  const [receipt, setReceipt] = useState("Ready: local command projection has not invoked an executor, adapter, deploy, or external write.");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const s = await api.getAgentOsStatus();
      setStatus(s);
    } catch {
      setError("Unable to load the Agent OS readback.");
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Zero-delay kickoff so the initial fetch is cancellable before it starts
    const timer = window.setTimeout(() => {
      void load();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  const observation: AgentOsObservation = useMemo(() => {
    if (!status) {
      return { layers: [] };
    }
    return buildAgentOsObservation(status);
  }, [status]);

  // No explicit selection yet -> first available node is the derived selection
  const selectedNode = useMemo((): ObservationNode | null => {
    if (!selectedId) {
      return observation.layers.flatMap((layer) => layer.nodes)[0] ?? null;
    }
    for (const layer of observation.layers) {
      const node = layer.nodes.find((n) => n.id === selectedId);
      if (node) return node;
    }
    return null;
  }, [selectedId, observation]);

  const selectNode = useCallback((id: string) => {
    setSelectedId(id);
  }, []);

  const runProjection = useCallback(() => {
    const target = selectedNode ? selectedNode.label : "no selected node";
    setReceipt(
      `Projected command for ${target}: "${command}". No executor, adapter, deploy, or external write was invoked.`,
    );
  }, [selectedNode, command]);

  const summaryCards = useMemo((): SummaryCard[] => {
    const allNodes = observation.layers.flatMap((l) => l.nodes);
    const eng = allNodes.find((n) => n.id === "engineering-coverage");
    const op = allNodes.find((n) => n.id === "operational-health");
    const proc = allNodes.find((n) => n.id === "procedures");
    const cert = allNodes.find((n) => n.id === "certification");

    const cards: SummaryCard[] = [];
    if (eng) {
      cards.push({
        label: "Engineering coverage",
        value: eng.value,
        state: eng.state,
        metrics: eng.metrics,
      });
    }
    if (op) {
      cards.push({
        label: "Operational health",
        value: op.value,
        state: op.state,
        metrics: op.metrics,
      });
    }
    if (proc) {
      cards.push({
        label: "Procedure validation",
        value: proc.value,
        state: proc.state,
        metrics: proc.metrics,
      });
    }
    if (cert) {
      cards.push({
        label: "Certification",
        value: cert.value,
        state: cert.state,
        metrics: cert.metrics,
      });
    }
    return cards;
  }, [observation]);

  const shadowNode = useMemo(() => {
    return observation.layers.find((l) => l.key === "governance")?.nodes.find((n) => n.id === "shadow-ledger") || null;
  }, [observation]);

  const certNode = useMemo(() => {
    return observation.layers.find((l) => l.key === "evidence")?.nodes.find((n) => n.id === "certification") || null;
  }, [observation]);

  const boundaryText = useMemo(
    () => deriveBoundaryText(shadowNode, certNode),
    [shadowNode, certNode],
  );

  useLayoutEffect(() => {
    setAfterTitle(
      <div className="hidden items-center gap-2 rounded-full border border-primary/20 bg-primary/5 px-3 py-1 text-xs text-primary md:flex">
        Observation layer
      </div>,
    );
    setEnd(null);
    return () => {
      setAfterTitle(null);
      setEnd(null);
    };
  }, [setAfterTitle, setEnd]);

  const hasLayers = observation.layers.length > 0;
  const hasData = hasLayers && observation.layers.some((l) => l.nodes.length > 0);

  return (
    <div className="flex flex-col gap-6">
      <PluginSlot name="workbench:top" />

      <section className="rounded-2xl border border-border/60 bg-card/60 p-4 sm:p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="font-mono-ui text-[10px] uppercase text-muted-foreground">
              Agent OS
            </div>
            <h2 className="mt-1 font-mondwest text-2xl leading-none text-foreground text-balance">
              Four-layer observation topology
            </h2>
          </div>
          <Button
            size="sm"
            outlined
            onClick={load}
            disabled={loading}
            aria-busy={loading}
            className="shrink-0"
          >
            <RefreshCw
              className={cn("mr-1.5 h-3.5 w-3.5")}
              aria-hidden
            />
            Refresh
          </Button>
        </div>

        {/* Boundary / readback semantics — derived from shadow-ledger.evidence and certification node */}
        <div className="mb-5 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2.5 text-xs leading-relaxed text-warning/90 text-pretty">
          {boundaryText}
        </div>

        {/* Top summary cards — four independent axes from derived nodes */}
        <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
          {loading ? (
            Array.from({ length: 4 }).map((_, i) => (
              <div
                key={i}
                className="h-[72px] rounded-lg border border-border/50 bg-background/40 p-3"
                aria-hidden
              />
            ))
          ) : summaryCards.length > 0 ? (
            summaryCards.map((card, index) => (
              <div
                key={index}
                className="rounded-lg border border-border/50 bg-background/40 p-3"
              >
                <div className="font-mono-ui text-[10px] uppercase text-muted-foreground">
                  {card.label}
                </div>
                <div
                  className={cn(
                    "mt-1 font-mondwest text-xl tabular-nums",
                    card.state === "ok" && "text-success",
                    (card.state === "degraded" || card.state === "stale") && "text-warning",
                    card.state === "blocked" && "text-destructive",
                  )}
                >
                  {card.value}
                </div>
                {card.metrics.length > 0 && (
                  <div className="mt-1 text-[10px] tabular-nums text-muted-foreground">
                    {card.metrics.map((m, mi) => (
                      <span key={mi}>
                        {m.label}: {m.value}
                        {mi < card.metrics.length - 1 ? " · " : ""}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))
          ) : (
            <div className="col-span-full text-xs text-muted-foreground text-pretty">
              No axis data available from status response.
            </div>
          )}
        </div>

        {/* Structural loading */}
        {loading && (
          <div
            aria-busy="true"
            aria-live="polite"
            className="flex h-40 items-center justify-center rounded-lg border border-border/40 bg-background/30 text-sm text-muted-foreground"
          >
            Loading Agent OS observation…
          </div>
        )}

        {/* Local error + Retry */}
        {error && !loading && (
          <div
            role="alert"
            className="mb-4 flex flex-col gap-3 rounded-lg border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="flex items-center gap-2">
              <AlertCircle className="h-4 w-4 shrink-0" aria-hidden />
              <span>{error}</span>
            </div>
            <Button size="sm" outlined onClick={load} className="shrink-0">
              Retry
            </Button>
          </div>
        )}

        {/* Live swimlanes + detail */}
        {!loading && !error && hasData && (
          <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
            {/* Horizontal swimlanes */}
            <div className="space-y-6">
              {observation.layers.map((layer: ObservationLayer) => (
                <div key={layer.key}>
                  <div className="mb-2 flex items-center gap-2">
                    <div className="font-mono-ui text-[10px] uppercase text-primary/70">
                      {layer.label}
                    </div>
                    <div className="h-px flex-1 bg-border/40" aria-hidden />
                  </div>

                  <div
                    className="flex flex-wrap gap-2"
                    role="group"
                    aria-label={`${layer.label} swimlane`}
                  >
                    {layer.nodes.length === 0 ? (
                      <div className="rounded-lg border border-border/40 bg-background/30 px-3 py-2 text-xs text-muted-foreground text-pretty">
                        No nodes reported
                      </div>
                    ) : (
                      layer.nodes.map((node: ObservationNode) => {
                        const isSelected = node.id === selectedNode?.id;
                        const stateCls = getStateClasses(node.state);
                        return (
                          <button
                            key={node.id}
                            type="button"
                            aria-pressed={isSelected}
                            onClick={() => selectNode(node.id)}
                            className={cn(
                              "min-h-[44px] min-w-[148px] max-w-full rounded-lg border px-3 py-2 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary",
                              "hover:bg-accent/20",
                              isSelected
                                ? "border-primary bg-primary/5"
                                : "border-border/60 bg-card/30",
                            )}
                          >
                            <div className="flex items-baseline justify-between gap-2">
                              <span className="font-medium text-foreground text-sm leading-tight">
                                {node.label}
                              </span>
                              <span
                                className={cn(
                                  "shrink-0 rounded px-1.5 py-px font-mono-ui text-[9px] uppercase border",
                                  stateCls,
                                )}
                              >
                                {getStateText(node.state)}
                              </span>
                            </div>
                            <div className="mt-0.5 font-mono-ui text-xs tabular-nums text-muted-foreground">
                              {node.value}
                            </div>
                            {node.metrics.length > 0 && (
                              <div className="mt-1 text-[10px] tabular-nums text-muted-foreground">
                                {node.metrics
                                  .map((m) => `${m.label} ${m.value}`)
                                  .join(" · ")}
                              </div>
                            )}
                          </button>
                        );
                      })
                    )}
                  </div>
                </div>
              ))}
            </div>

            {/* Right-side detail panel (xl+) */}
            <aside className="self-start rounded-2xl border border-border/60 bg-card/70 p-4 xl:sticky xl:top-4">
              <div className="font-mono-ui text-[10px] uppercase text-muted-foreground">
                Selected node
              </div>

              {!selectedNode ? (
                <div className="mt-3 text-sm text-muted-foreground text-pretty">
                  Select a node from any swimlane.
                </div>
              ) : (
                <div className="mt-2">
                  <div className="font-mondwest text-xl leading-none text-foreground text-balance">
                    {selectedNode.label}
                  </div>

                  <div
                    className={cn(
                      "mt-2 inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 font-mono-ui text-xs",
                      getStateClasses(selectedNode.state),
                    )}
                  >
                    <span>{getStateText(selectedNode.state)}</span>
                    <span className="opacity-60">·</span>
                    <span>{selectedNode.value}</span>
                  </div>

                  {/* Local selection receipt (role=status aria-live=polite) */}
                  <div
                    role="status"
                    aria-live="polite"
                    className="mt-1 text-[10px] text-muted-foreground"
                  >
                    {selectedNode.label} · {getStateText(selectedNode.state)}
                  </div>

                  {selectedNode.metrics.length > 0 && (
                    <div className="mt-4">
                      <div className="mb-1.5 font-mono-ui text-[10px] uppercase text-muted-foreground">
                        Metrics
                      </div>
                      <div className="space-y-1 text-sm tabular-nums">
                        {selectedNode.metrics.map((m, idx) => (
                          <div
                            key={idx}
                            className="flex items-baseline justify-between gap-3 border-b border-border/30 pb-1 last:border-0"
                          >
                            <span className="text-muted-foreground">{m.label}</span>
                            <span className="font-mono-ui text-foreground">{m.value}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {selectedNode.source && (
                    <div className="mt-4">
                      <div className="mb-1.5 font-mono-ui text-[10px] uppercase text-muted-foreground">
                        Source
                      </div>
                      <div className="break-all font-mono-ui text-xs text-foreground">
                        {selectedNode.source}
                      </div>
                    </div>
                  )}

                  {selectedNode.evidence && selectedNode.evidence.length > 0 && (
                    <div className="mt-4">
                      <div className="mb-1.5 font-mono-ui text-[10px] uppercase text-muted-foreground">
                        Evidence / findings
                      </div>
                      <ul className="space-y-1 text-sm leading-snug text-muted-foreground">
                        {selectedNode.evidence.map((finding, idx) => (
                          <li key={idx} className="flex gap-1.5">
                            <span aria-hidden>•</span>
                            <span>{finding}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
              )}

              <div className="mt-5 border-t border-border/40 pt-3 text-[10px] leading-relaxed text-muted-foreground text-pretty">
                Read-only projection. Values derived directly from <span className="font-mono-ui">GET /api/agent-os/status</span>. No execution, write, or side-effect surface.
              </div>
            </aside>
          </div>
        )}

        {/* Empty state when no data but no error */}
        {!loading && !error && !hasData && (
          <div className="rounded-lg border border-border/40 bg-background/30 p-4 text-sm text-muted-foreground text-pretty">
            No observation nodes derived from the current Agent OS status response.
          </div>
        )}

        {/* Local-only projection command dock — bound to the selected node */}
        <div className="mt-6 rounded-2xl border border-border/60 bg-background/40 p-3">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
            <div className="flex min-w-0 flex-1 items-center gap-3 rounded-lg border border-border/60 bg-card/40 px-3 py-2.5">
              <Command className="h-4 w-4 shrink-0 text-primary" aria-hidden />
              <label htmlFor="workbench-command" className="sr-only">
                Workbench command
              </label>
              <input
                id="workbench-command"
                value={command}
                onChange={(event) => setCommand(event.target.value)}
                aria-describedby="workbench-command-help workbench-receipt"
                className="min-w-0 flex-1 bg-transparent font-mono-ui text-sm text-foreground outline-none placeholder:text-muted-foreground"
                placeholder="Ask Hermes to map evidence, gates, and next safe action..."
              />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                outlined
                size="sm"
                onClick={() => {
                  setCommand("Show stale claims and required readbacks");
                  setReceipt("Loaded readback command template; projection mode remains local-only.");
                }}
              >
                Readback
              </Button>
              <Button
                type="button"
                outlined
                size="sm"
                onClick={() => {
                  setCommand("Find safe next slice without opening high-risk gates");
                  setReceipt("Loaded safe-slice command template; no high-risk gate was opened.");
                }}
              >
                Safe slice
              </Button>
              <Button
                type="button"
                size="sm"
                aria-label="Run projected workbench command"
                onClick={runProjection}
              >
                <Zap className="mr-1.5 h-3.5 w-3.5" aria-hidden />
                Project
              </Button>
            </div>
          </div>
          <div className="mt-3 grid gap-2 text-xs text-muted-foreground md:grid-cols-[minmax(0,1fr)_minmax(0,1.35fr)]">
            <p id="workbench-command-help" className="leading-relaxed text-pretty">
              Projection-only command dock: templates and receipts update locally; no executor, adapter, deploy, or external write is invoked.
            </p>
            <p
              id="workbench-receipt"
              role="status"
              aria-live="polite"
              className="rounded-lg border border-primary/15 bg-primary/5 px-3 py-2 font-mono-ui text-primary/90"
            >
              {receipt}
            </p>
          </div>
        </div>
      </section>

      <PluginSlot name="workbench:bottom" />
    </div>
  );
}
