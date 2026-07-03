import { useCallback, useLayoutEffect, useMemo, useState, type KeyboardEvent } from "react";
import {
  Brain,
  Command,
  FileText,
  Network,
  Search,
  ShieldCheck,
  Sparkles,
  Zap,
} from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { usePageHeader } from "@/contexts/usePageHeader";
import { PluginSlot } from "@/plugins";

interface GraphNode {
  id: string;
  label: string;
  type: "core" | "gate" | "signal" | "risk";
  x: number;
  y: number;
  score: number;
  description: string;
  evidence: string;
}

const graphNodes: GraphNode[] = [
  {
    id: "core",
    label: "Hermes Control Plane",
    type: "core",
    x: 50,
    y: 49,
    score: 98,
    description: "Conversation, tools, skills, cron, and profile scope meet here before any action crosses a boundary.",
    evidence: "Active profile + live session + dashboard shell",
  },
  {
    id: "sessions",
    label: "Session Evidence",
    type: "signal",
    x: 22,
    y: 26,
    score: 83,
    description: "Searchable transcript evidence, context handoffs, platform source, and replayable artifacts.",
    evidence: "FTS index, session DB, source tags",
  },
  {
    id: "tools",
    label: "Tool Authority",
    type: "gate",
    x: 76,
    y: 27,
    score: 91,
    description: "Every side effect routes through an explicit tool boundary with permission and readback requirements.",
    evidence: "Approval policy, tool schema, route guards",
  },
  {
    id: "skills",
    label: "Skill Memory",
    type: "signal",
    x: 28,
    y: 73,
    score: 76,
    description: "Reusable procedures and references turn repeated work into governed playbooks instead of ad hoc prompts.",
    evidence: "Skill registry, linked references",
  },
  {
    id: "profiles",
    label: "Profile Scope",
    type: "gate",
    x: 74,
    y: 70,
    score: 88,
    description: "Management pages are scoped to the selected profile so configuration and memory changes stay bounded.",
    evidence: "Profile switcher + scoped API prefixes",
  },
  {
    id: "risks",
    label: "High-risk Gates",
    type: "risk",
    x: 50,
    y: 15,
    score: 64,
    description: "Publish, deploy, credential, payment, destructive, and broad-fanout actions remain exact-target gated.",
    evidence: "Boundary policy + approval/readback contract",
  },
  {
    id: "cron",
    label: "Autonomy Scheduler",
    type: "signal",
    x: 16,
    y: 51,
    score: 72,
    description: "Recurring jobs are visible as scheduled intent, delivery target, model, and last-run receipt.",
    evidence: "Cron registry + output receipts",
  },
  {
    id: "plugins",
    label: "Edge Capability",
    type: "signal",
    x: 84,
    y: 51,
    score: 79,
    description: "Product growth happens at the edge through plugins, MCP, channels, and dashboard slots.",
    evidence: "Plugin manifests + slot projections",
  },
];

const graphEdges = [
  ["core", "sessions"],
  ["core", "tools"],
  ["core", "skills"],
  ["core", "profiles"],
  ["core", "risks"],
  ["core", "cron"],
  ["core", "plugins"],
  ["tools", "risks"],
  ["profiles", "skills"],
  ["sessions", "cron"],
];

const typeClass: Record<GraphNode["type"], string> = {
  core: "workbench-node-core",
  gate: "workbench-node-gate",
  signal: "workbench-node-signal",
  risk: "workbench-node-risk",
};

const typeLabel: Record<GraphNode["type"], string> = {
  core: "CORE",
  gate: "GATE",
  signal: "SIGNAL",
  risk: "RISK",
};

function MetricCard({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div className="rounded-xl border border-border/60 bg-card/55 p-4 shadow-[0_0_32px_rgba(52,211,153,0.06)] backdrop-blur">
      <div className="font-mono-ui text-[10px] uppercase tracking-[0.22em] text-muted-foreground">
        {label}
      </div>
      <div className="mt-2 font-mondwest text-2xl text-foreground">{value}</div>
      <div className="mt-1 text-xs text-text-tertiary">{hint}</div>
    </div>
  );
}

function EvidenceRow({ icon: Icon, title, body }: { icon: typeof FileText; title: string; body: string }) {
  return (
    <div className="flex gap-3 rounded-lg border border-border/50 bg-background/35 p-3">
      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
      <div>
        <div className="text-sm font-medium text-foreground">{title}</div>
        <div className="mt-1 text-xs leading-relaxed text-muted-foreground">{body}</div>
      </div>
    </div>
  );
}

export default function WorkbenchPage() {
  const { setAfterTitle, setEnd } = usePageHeader();
  const [activeId, setActiveId] = useState("core");
  const [command, setCommand] = useState("Map current evidence gates before action");
  const [receipt, setReceipt] = useState("Ready: projection mode keeps all execution gates closed.");
  const activeNode = useMemo(
    () => graphNodes.find((node) => node.id === activeId) ?? graphNodes[0],
    [activeId],
  );

  const selectNode = useCallback((id: string) => {
    const next = graphNodes.find((node) => node.id === id) ?? graphNodes[0];
    setActiveId(next.id);
    setReceipt(`Selected ${next.label}: read-only evidence projection updated.`);
  }, []);

  const moveSelection = useCallback((delta: number) => {
    const currentIndex = graphNodes.findIndex((node) => node.id === activeId);
    const nextIndex = (currentIndex + delta + graphNodes.length) % graphNodes.length;
    selectNode(graphNodes[nextIndex].id);
  }, [activeId, selectNode]);

  const handleNodeKeyDown = useCallback((event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      event.preventDefault();
      moveSelection(1);
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      event.preventDefault();
      moveSelection(-1);
    } else if (event.key === "Home") {
      event.preventDefault();
      selectNode(graphNodes[0].id);
    } else if (event.key === "End") {
      event.preventDefault();
      selectNode(graphNodes[graphNodes.length - 1].id);
    }
  }, [moveSelection, selectNode]);

  const runProjection = useCallback(() => {
    setReceipt(`Projected command for ${activeNode.label}: "${command}". No executor, adapter, deploy, or external write was invoked.`);
  }, [activeNode.label, command]);

  useLayoutEffect(() => {
    setAfterTitle(
      <div className="hidden items-center gap-2 rounded-full border border-primary/20 bg-primary/5 px-3 py-1 text-xs text-primary md:flex">
        <Sparkles className="h-3.5 w-3.5" aria-hidden />
        Intelligence graph workbench
      </div>,
    );
    setEnd(null);
    return () => {
      setAfterTitle(null);
      setEnd(null);
    };
  }, [setAfterTitle, setEnd]);

  return (
    <div className="workbench-shell flex flex-col gap-6">
      <PluginSlot name="workbench:top" />

      <section className="relative overflow-hidden rounded-3xl border border-primary/15 bg-[radial-gradient(circle_at_50%_10%,rgba(255,230,203,0.16),transparent_30%),linear-gradient(135deg,rgba(4,28,28,0.96),rgba(3,12,14,0.94))] p-4 shadow-[0_24px_100px_rgba(0,0,0,0.38)] sm:p-6">
        <div className="workbench-grid" aria-hidden />
        <div className="workbench-orb workbench-orb-one" aria-hidden />
        <div className="workbench-orb workbench-orb-two" aria-hidden />

        <div className="relative z-10 grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
          <div className="min-h-[520px] rounded-2xl border border-primary/15 bg-background/35 p-4 backdrop-blur-xl sm:p-5">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div>
                <div className="flex items-center gap-2 font-mono-ui text-[10px] uppercase tracking-[0.28em] text-primary/80">
                  <Network className="h-4 w-4" aria-hidden />
                  Hermes operational map
                </div>
                <h2 className="mt-3 max-w-2xl text-balance break-words font-mondwest text-3xl leading-none tracking-wide text-foreground [overflow-wrap:anywhere] sm:text-5xl">
                  Control-plane constellation
                </h2>
                <p className="mt-3 max-w-2xl text-pretty text-sm leading-6 text-muted-foreground">
                  A graph-first command surface for sessions, skills, tools, profile scope, autonomy, and exact-risk gates. Chat remains the intent input; evidence and authority become visible structure.
                </p>
              </div>
              <div className="rounded-xl border border-warning/30 bg-warning/10 px-3 py-2 text-right">
                <div className="font-mono-ui text-[10px] uppercase tracking-[0.2em] text-warning/80">Boundary</div>
                <div className="mt-1 text-sm font-medium text-warning">projection-only</div>
              </div>
            </div>

            <div className="relative mt-7 h-[360px] rounded-2xl border border-primary/10 bg-black/10 sm:h-[430px]" role="group" aria-label="Hermes control-plane knowledge graph. Use arrow keys to move between nodes.">
              <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
                <defs>
                  <linearGradient id="workbench-edge" x1="0" x2="1" y1="0" y2="1">
                    <stop offset="0%" stopColor="rgba(255,230,203,0.18)" />
                    <stop offset="50%" stopColor="rgba(52,211,153,0.38)" />
                    <stop offset="100%" stopColor="rgba(181,140,255,0.20)" />
                  </linearGradient>
                </defs>
                {graphEdges.map(([from, to]) => {
                  const a = graphNodes.find((node) => node.id === from)!;
                  const b = graphNodes.find((node) => node.id === to)!;
                  const active = from === activeId || to === activeId;
                  return (
                    <line
                      key={`${from}-${to}`}
                      x1={a.x}
                      y1={a.y}
                      x2={b.x}
                      y2={b.y}
                      stroke="url(#workbench-edge)"
                      strokeWidth={active ? 0.46 : 0.22}
                      strokeDasharray={active ? "1.4 1.1" : ""}
                      opacity={active ? 0.95 : 0.42}
                    />
                  );
                })}
              </svg>

              {graphNodes.map((node) => (
                <button
                  key={node.id}
                  type="button"
                  aria-pressed={node.id === activeId}
                  aria-label={`Inspect ${node.label}`}
                  onClick={() => selectNode(node.id)}
                  onKeyDown={handleNodeKeyDown}
                  aria-describedby="workbench-receipt"
                  className={`workbench-node ${typeClass[node.type]} ${node.id === activeId ? "is-active" : ""}`}
                  style={{ left: `${node.x}%`, top: `${node.y}%` }}
                >
                  <span className="workbench-node-pulse" aria-hidden />
                  <span className="workbench-node-dot" aria-hidden />
                  <span className="workbench-node-label">{node.label}</span>
                </button>
              ))}
            </div>

            <div className="mt-5 grid gap-3 sm:grid-cols-3">
              <MetricCard label="visible gates" value="06" hint="authority surfaces made explicit" />
              <MetricCard label="evidence classes" value="08" hint="session, tool, skill, cron, plugin" />
              <MetricCard label="risk mode" value="closed" hint="high-risk actions stay exact-gated" />
            </div>
          </div>

          <aside className="flex flex-col gap-4">
            <div className="rounded-2xl border border-primary/15 bg-card/70 p-5 backdrop-blur-xl">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="font-mono-ui text-[10px] uppercase tracking-[0.24em] text-muted-foreground">
                    Selected node
                  </div>
                  <h3 className="mt-2 font-mondwest text-2xl leading-none text-foreground">
                    {activeNode.label}
                  </h3>
                </div>
                <div className={`rounded-full border px-2.5 py-1 font-mono-ui text-[10px] tracking-[0.18em] ${activeNode.type === "risk" ? "border-destructive/40 text-destructive" : "border-primary/30 text-primary"}`}>
                  {typeLabel[activeNode.type]}
                </div>
              </div>
              <p className="mt-4 text-sm leading-6 text-muted-foreground">
                {activeNode.description}
              </p>
              <div className="mt-5 rounded-xl border border-border/60 bg-background/40 p-4">
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <span>confidence score</span>
                  <span className="font-mono-ui text-foreground">{activeNode.score}%</span>
                </div>
                <div className="mt-2 h-2 overflow-hidden rounded-full bg-muted/40">
                  <div
                    className="h-full rounded-full bg-[linear-gradient(90deg,var(--series-output-token),var(--series-input-token))]"
                    style={{ width: `${activeNode.score}%` }}
                  />
                </div>
              </div>
              <EvidenceRow icon={FileText} title="Evidence binding" body={activeNode.evidence} />
            </div>

            <div className="rounded-2xl border border-warning/20 bg-warning/5 p-5 backdrop-blur-xl">
              <div className="flex items-center gap-2 text-warning">
                <ShieldCheck className="h-4 w-4" aria-hidden />
                <h3 className="font-mondwest text-xl leading-none">Critical concerns</h3>
              </div>
              <ul className="mt-4 space-y-3 text-sm leading-6 text-muted-foreground">
                <li>• No deploy, publish, credential, destructive, or fanout action is implied by this visual surface.</li>
                <li>• Graph edges are explanatory UI projections, not proof of runtime execution.</li>
                <li>• Next rung should bind live API data before claiming operational completeness.</li>
              </ul>
            </div>
          </aside>
        </div>

        <div className="relative z-10 mt-5 rounded-2xl border border-primary/20 bg-background/75 p-3 shadow-[0_18px_60px_rgba(0,0,0,0.25)] backdrop-blur-xl">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
            <div className="flex min-w-0 flex-1 items-center gap-3 rounded-xl border border-border/70 bg-black/20 px-3 py-2.5">
              <Command className="h-4 w-4 shrink-0 text-primary" aria-hidden />
              <label htmlFor="workbench-command" className="sr-only">Workbench command</label>
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
              <Button type="button" outlined size="sm" onClick={() => { setCommand("Show stale claims and required readbacks"); setReceipt("Loaded readback command template; projection mode remains local-only."); }}>Readback</Button>
              <Button type="button" outlined size="sm" onClick={() => { setCommand("Find safe next slice without opening high-risk gates"); setReceipt("Loaded safe-slice command template; no high-risk gate was opened."); }}>Safe slice</Button>
              <Button type="button" size="sm" aria-label="Run projected workbench command" onClick={runProjection}>
                <Zap className="h-4 w-4" aria-hidden />
                Project
              </Button>
            </div>
          </div>
          <div className="mt-3 grid gap-2 text-xs text-muted-foreground md:grid-cols-[minmax(0,1fr)_minmax(0,1.35fr)]">
            <p id="workbench-command-help" className="leading-relaxed">
              Projection-only command dock: templates and receipts update locally; no tool, adapter, deploy, or external write is invoked.
            </p>
            <p id="workbench-receipt" role="status" aria-live="polite" className="rounded-lg border border-primary/15 bg-primary/5 px-3 py-2 font-mono-ui text-primary/90">
              {receipt}
            </p>
          </div>
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-3">
        <EvidenceRow icon={Search} title="Graph-first navigation" body="Make complex operational state spatial first, then drill into tables and logs only after a node is selected." />
        <EvidenceRow icon={Brain} title="Agent command dock" body="Bind user intent to the visible evidence map so chat output can land as graph updates, panels, receipts, or gates." />
        <EvidenceRow icon={Sparkles} title="Reusable visual language" body="Dark teal canvas, cream core glow, emerald signal paths, violet semantic nodes, and amber/risk boundary panels." />
      </section>

      <PluginSlot name="workbench:bottom" />
    </div>
  );
}
