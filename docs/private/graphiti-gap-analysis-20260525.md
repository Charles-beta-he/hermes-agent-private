# getzep/graphiti → Hermes local-brain temporal context graph gap analysis

Date: 2026-05-26
Task: 2026-05-25-gap-analyze-getzep-graphiti-for-hermes-temporal-context-graph-memory-absorption
Source: https://github.com/getzep/graphiti
Review mode: read-only source/provenance/architecture intake. No third-party install, service startup, database startup, Docker, MCP server, examples, or private-data indexing was performed.
Workspace evidence cache: `/tmp/agent-team-2026-05-25-g/graphiti-intake/`

## 1. Repo identity, provenance, and license verification

GitHub API evidence captured to `/tmp/agent-team-2026-05-25-g/graphiti-intake/repo_metadata.json` and `/tmp/agent-team-2026-05-25-g/graphiti-intake/default_branch_commit.json`.

Observed repo metadata:

| Field | Value | Evidence |
|---|---:|---|
| Full name | `getzep/graphiti` | `repo_metadata.json`, lines with `full_name` |
| HTML URL | `https://github.com/getzep/graphiti` | `repo_metadata.json`, `html_url` |
| Description | `Build Real-Time Knowledge Graphs for AI Agents` | `repo_metadata.json`, `description` |
| Visibility | public, non-fork | `repo_metadata.json`, `private=false`, `fork=false` |
| License | Apache-2.0 | `repo_metadata.json` license object; root `pyproject.toml:11`; root `LICENSE` |
| Language | Python | `repo_metadata.json`, `language` |
| Default branch | `main` | `repo_metadata.json`, `default_branch` |
| HEAD reviewed | `34f56e65e0fe2096132c8d16f3a1a4ac9300a5f6` | `default_branch_commit.json`; source archive root `getzep-graphiti-34f56e6` |
| Last push observed | `2026-05-21T03:32:49Z` | `repo_metadata.json:112` |
| Stars/forks observed | 26,522 / 2,640 | `repo_metadata.json:24-25`, `:116` |

Top-level source-layout evidence from the archive includes: `graphiti_core/`, `mcp_server/`, `server/`, `examples/`, `tests/`, `spec/`, `README.md`, `AGENTS.md`, `CLAUDE.md`, `pyproject.toml`, `Dockerfile`, and compose files. `AGENTS.md:3-4` states that `graphiti_core/` contains domain modules (`nodes.py`, `edges.py`, `models/`, `search/`), drivers support Neo4j/FalkorDB/Kuzu/Neptune, service glue is in `server/graph_service/`, and MCP integration is in `mcp_server/`.

## 2. Graphiti inventory mapped to Hermes-relevant patterns

### 2.1 Temporal context graph model

Graphiti’s README explicitly positions the system as temporal context graphs for AI agents:

- `README.md:42-49`: tracks how facts change over time, maintains provenance to source data, supports incremental updates and historical queries.
- `README.md:67-82`: context graph stores entities, facts/relationships with validity windows, and episodes as provenance/ground-truth stream.
- `README.md:125-134`: temporal fact management invalidates old facts rather than deleting them; hybrid retrieval combines embeddings, BM25, and graph traversal.
- `CLAUDE.md:7-12`: says Graphiti is temporally aware and uses a bi-temporal data model plus hybrid retrieval.

File-level temporal schema:

| Source surface | Observed schema | Hermes interpretation |
|---|---|---|
| `graphiti_core/nodes.py:318-326` | `EpisodicNode` has `source`, `source_description`, raw `content`, `valid_at`, and referenced `entity_edges`. | Strong pattern for a local-brain “source episode manifest” that can record where a memory/evidence claim came from without promoting it to truth. |
| `graphiti_core/edges.py:263-281` | `EntityEdge` stores `fact`, `episodes`, `expired_at`, `valid_at`, `invalid_at`, and `reference_time`. | Strong pattern for validity-window facts and supersession; useful for Hermes evidence freshness, but only if truth gates remain authoritative. |
| `graphiti_core/graph_queries.py:32-40`, `:74-81` | indexes include `Episodic.valid_at` and edge `expired_at`, `valid_at`, `invalid_at`. | Confirms temporal fields are operationally indexed in Graphiti, not just README prose. |
| `graphiti_core/graphiti.py:358-362`, `:407-412` | saga timestamps use originating episode reference time; previous episode lookup orders by `valid_at` then `created_at`. | Useful reference for preserving event time versus wall-clock ingestion time in Hermes taskpool evidence chains. |

Decision: ACCEPT CONCEPT, REJECT DIRECT CORE DEPENDENCY. Absorb the episode/fact/validity-window schema idea into Hermes-local evidence manifests; do not import Graphiti or make graph DBs truth sources.

### 2.2 Source episode provenance

Graphiti’s provenance model is explicit:

- `README.md:67-82`: every derived fact traces back to episodes, the raw data/ground-truth stream.
- `graphiti_core/nodes.py:318-326`: `EpisodicNode` stores raw episode content and source metadata.
- `graphiti_core/edges.py:267-269`: facts store episode IDs that reference them.
- `graphiti_core/graph_queries.py:101-133`: full-text indexes include episode `content`, `source`, `source_description`, and `group_id`.

Hermes mapping:

| Graphiti concept | Hermes current anchor | Gap / governed absorption |
|---|---|---|
| Episode as source/provenance | Task notes’ Proof Log, `acceptance_evidence`, `evidence_refs`; session DB (`hermes_state.py`) stores full messages with FTS5; memory entries have durable stores. | Add a local, non-secret provenance manifest format for claims: `claim_id`, source artifact path, source kind, observed_at, valid_from, valid_until/superseded_by, confidence, and truth-gate binding. |
| Fact links back to episodes | Current proof logs cite commands/artifacts manually. | Make evidence-to-claim links structured enough to verify stale/superseded claims deterministically. |
| Group partitioning | Task/project/profile fields and Hermes profile-aware paths. | Keep partitioning local and profile-scoped; never put private user/taskpool logs into external graph DBs. |

Decision: ACCEPT FOR LOCAL MANIFEST. The useful delta is provenance-link structure, not Graphiti runtime.

### 2.3 Hybrid retrieval inventory

Graphiti search/retrieval surfaces:

- `README.md:53-55` and `:131-134`: hybrid retrieval = semantic + keyword + graph traversal.
- `graphiti_core/search/search_config.py:32-46`: search methods include cosine similarity, BM25, and breadth-first search for nodes/edges; episodes use BM25.
- `graphiti_core/search/search_config.py:53-77`: rerankers include reciprocal-rank fusion, node distance, episode mentions, MMR, and cross-encoder.
- `graphiti_core/search/search_config.py:112-129`: `SearchResults` merges edges, nodes, episodes, communities with reranker scores.

Hermes anchors:

- `hermes_state.py:3-15`: SQLite `state.db` stores session metadata/history and uses FTS5 full-text search.
- `session_search` tool behavior is FTS5-backed over the SQLite message store and returns session windows/bookends, not graph facts.
- `tools/memory_tool.py:3-24`: persistent curated memory is file-backed and injected as a frozen prompt snapshot, not a query-time graph.
- `agent/memory_manager.py:39-60`: memory-provider output is fenced/sanitized, highlighting that retrieved memory is context, not untrusted new user input.

Gap:

- Hermes has strong keyword/session retrieval but no first-class relationship traversal over task/memory/evidence artifacts.
- Semantic search may exist through providers/plugins, but this task found no reason to make Graphiti-style embedding/RRF mandatory for the first slice.
- A graph-lite evaluation can compare: current FTS5/session_search versus a local provenance manifest with explicit edges (`task -> artifact -> claim -> superseded_by`) using deterministic keyword/relationship filters first.

Decision: WATCH / PROTOTYPE ONLY. Accept a small evaluation harness, reject new embedding/cross-encoder dependencies until local graph-lite proves value.

### 2.4 MCP memory server pattern

Graphiti MCP/service evidence:

- `mcp_server/pyproject.toml:7-11`: MCP server depends on `mcp`, `openai`, `graphiti-core[falkordb]`, and settings packages.
- `mcp_server/src/graphiti_mcp_server.py:321-325`: exposes `add_memory` with episode body input.
- `mcp_server/src/graphiti_mcp_server.py:407-490`: exposes node and memory-fact search tools.
- `mcp_server/src/graphiti_mcp_server.py:541-621`: exposes delete/get edge and delete/get episode tools.
- `mcp_server/src/graphiti_mcp_server.py:688-725`: exposes graph clear and status tools.
- `mcp_server/src/services/queue_service.py:12-25`, `:101-103`: queue service processes episode tasks by `group_id`.
- `mcp_server/src/services/factories.py:373-431`: driver factory supports Neo4j and FalkorDB and reads environment variables such as `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `FALKORDB_URI`, `FALKORDB_PASSWORD`.

Hermes MCP anchors:

- `mcp_serve.py:1-25`: Hermes already has a stdio MCP server for conversation/channel bridge tools.
- `mcp_serve.py:450-459`: `create_mcp_server` builds a FastMCP server.
- `mcp_serve.py:471-859`: Hermes tools include conversations, messages, attachments, events, send/list channels, and permission responses.
- `cron/scheduler.py:1541-1560`: cron initializes configured MCP tools before AIAgent construction, but handles failures non-fatally.

Gap:

- Graphiti’s MCP pattern is useful as an interface taxonomy (`add episode`, `search facts`, `get provenance`, `status`), but the Graphiti MCP server itself is unsafe for direct Hermes intake because it wants external DBs/providers and has destructive graph-clear/delete tools.
- Hermes already has MCP surfaces; any memory/provenance MCP extension should be Hermes-native, local, read-first, and permission-gated.

Decision: ACCEPT INTERFACE PATTERN ONLY. Reject running Graphiti MCP server or binding it to Hermes data.

### 2.5 Dependency, telemetry, and operations inventory

Dependency evidence:

- Root `pyproject.toml:12-20`: Python `>=3.10,<4`; core deps include `pydantic`, `neo4j`, `openai`, `tenacity`, `numpy`, `python-dotenv`, `posthog`.
- Root `pyproject.toml:27-38`: optional extras include Anthropic, Groq, Google GenAI, Kuzu, FalkorDB, VoyageAI, SentenceTransformers, Neptune, tracing.
- `mcp_server/pyproject.toml:7-11`: MCP server pulls `mcp`, `openai`, `graphiti-core[falkordb]`.
- `server/README.md:35-41` and `:55-60`: server setup expects OpenAI and Neo4j env/config and shows a Neo4j image.
- `server/README.md:78`: refers to Neo4j browser access.

Hermes risk mapping:

| Surface | Risk | Decision |
|---|---|---|
| Neo4j/FalkorDB/Kuzu/Neptune | New DB/runtime ops burden; violates intake red lines if started. | REJECT as first-slice Hermes dependency. |
| Docker/server/examples | Would execute third-party stack. | REJECT during intake; no run performed. |
| OpenAI/provider/env-key surfaces | Could route private data to external LLM/embedding providers. | REJECT for automatic Hermes memory indexing. |
| PostHog/telemetry dependency | Privacy/telemetry risk if runtime imported/executed. | REJECT any telemetry path for Hermes data. |
| Apache-2.0 codebase | License is permissive for reference/ideas. | WATCH if code copying is proposed; prefer independent local implementation. |

## 3. Hermes-side anchors and architecture boundaries

| Hermes surface | Source anchor | Boundary conclusion |
|---|---|---|
| Curated memory | `tools/memory_tool.py:3-24`, `:65-72`, `:76-118` | Memory is file-backed, prompt-injected, bounded, profile-aware, and scanned for injection/exfil. Graphiti-like ingestion must not auto-write memory or bypass memory write guards. |
| Memory retrieval providers | `agent/memory_manager.py:1-24`, `:39-60`, `:62-161` | Provider context is sanitized/fenced; any graph recall must be fenced as recalled context, not authority. |
| Session search | `hermes_state.py:3-15`, `:26-34` | Existing `state.db` + FTS5 is the baseline retrieval surface; graph-lite should complement, not replace, session_search. |
| Taskpool evidence | `/Users/charles/.hermes/scripts/brain_task_orchestrator.py:2-7`, `:26-33`, `:118-151` | Markdown task notes are canonical; taskpool acceptance/evidence fields and proof logs remain source of lifecycle truth. |
| MCP | `mcp_serve.py:1-25`, `:450-459`, `:471-859` | Hermes already exposes MCP; any provenance-memory MCP should be a Hermes-local toolset with read-only defaults and permission gates. |
| Project governance | `/Users/charles/.hermes/project-profiles/hermes.json:13-24`, `AGENTS.md` dependency/security sections | Fail-closed evidence, scoped changes, dependency ceilings, and no private-state leakage remain mandatory. |

## 4. Accept / reject / watch matrix

| Graphiti pattern | Decision | Why | Hermes follow-up shape |
|---|---|---|---|
| Episode provenance model | ACCEPT | Directly maps to proof logs/evidence refs and solves stale claim provenance. | Create local graph-lite provenance manifest design task. |
| Validity windows (`valid_at`, `invalid_at`, `expired_at`, `reference_time`) | ACCEPT | Useful for memory/task evidence freshness and supersession. | Include in provenance manifest schema; require deterministic stale/superseded checks. |
| Fact-to-episode links | ACCEPT | Enables explanation of why a claim exists and which artifact supports it. | Add claim/evidence linking design. |
| Hybrid retrieval taxonomy | WATCH | Useful, but embeddings/cross-encoders are unnecessary for first slice. | Build evaluation harness against existing FTS5/session_search before dependencies. |
| MCP memory tool taxonomy | ACCEPT INTERFACE ONLY | Good tool taxonomy; unsafe to run third-party MCP or destructive graph-clear tools. | Design Hermes-native read-first MCP/query surface after manifest exists. |
| Graph DB backends | REJECT FOR FIRST SLICE | Heavy ops/privacy burden; not needed to validate concept. | Revisit only after graph-lite benchmark proves value. |
| Graphiti server/Docker/examples | REJECT | Execution would violate intake red lines. | None. |
| Provider/embedding/cross-encoder deps | WATCH/REJECT DEFAULT | External data-routing and dependency cost; not needed for proof-gated local brain. | Optional later benchmark under explicit privacy gate. |
| PostHog/telemetry path | REJECT | No Hermes private data through telemetry-enabled third-party runtime. | None. |
| Direct memory auto-indexing of private sessions/taskpool | REJECT | Violates private data red lines and truth-gate separation. | Only local metadata/claim manifests, no raw private logs. |

## 5. Recommended bounded mainline plan

Next maintainable step is not “integrate Graphiti.” It is a governed Hermes-native graph-lite provenance spine:

1. Define a local provenance manifest schema under Hermes docs/state, not a DB service:
   - `claim_id`, `claim_text`, `source_kind`, `source_ref`, `observed_at`, `valid_from`, `valid_until`, `superseded_by`, `task_id`, `profile`, `truth_gate`, `evidence_hash`, `privacy_class`.
2. Implement no code in this intake task. Create follow-up task(s) for accepted deltas only.
3. Build a deterministic evaluation harness comparing current `session_search`/taskpool evidence lookup against graph-lite relationship lookup on synthetic/non-private fixtures only.
4. Only if graph-lite shows value, consider a Hermes-native read-only MCP query surface (`provenance_search`, `claim_get`, `stale_claims_list`) with destructive operations absent or permission-gated.
5. Keep graph recall advisory. Proof logs, task note lifecycle, command artifacts, and truth gates remain authoritative.

## 6. Follow-up task creation policy

Accepted deltas justify follow-up tasks only for:

- graph-lite provenance manifest schema;
- deterministic graph-lite-vs-FTS evaluation on synthetic fixtures;
- Hermes-native read-only MCP/query interface review after the manifest exists.

Rejected deltas must not create implementation tasks: Graphiti runtime, graph DB backends, Docker/server/examples, third-party MCP execution, telemetry, automatic private data indexing.

## 7. Red-line verification

| Red line | Status | Evidence |
|---|---|---|
| Do not run Graphiti | Safe | Only GitHub API/source archive/read_file/search_files inspection was used; no Python package import from Graphiti runtime. |
| Do not run Neo4j/FalkorDB/Kuzu/Neptune | Safe | No DB commands or service startup. |
| Do not run Docker/server/MCP/examples | Safe | No Docker/MCP/server/example command executed. |
| Do not store private user/taskpool/session/API-key data in Graphiti/external DB | Safe | Source archive stayed under `/tmp`; no private data was indexed into Graphiti or a graph DB. |
| Do not make generic repo summary | Safe | Output is Hermes gap mapping plus accept/reject/watch decisions. |

## 8. Closeout evidence

Commands/checks run:

- `python3 /Users/charles/.hermes/scripts/brain_task_orchestrator.py list --json` → OK, current task located as running.
- GitHub API metadata + default branch source archive captured with Python stdlib `urllib`/`tarfile` to `/tmp/agent-team-2026-05-25-g/graphiti-intake/`.
- Source inventory inspected with `read_file`/`search_files` against README, AGENTS, CLAUDE, pyproject, `graphiti_core`, `mcp_server`, and `server` surfaces.
- Hermes anchors inspected with `read_file`/`search_files`: `tools/memory_tool.py`, `agent/memory_manager.py`, `hermes_state.py`, `mcp_serve.py`, `cron/scheduler.py`, and `brain_task_orchestrator.py`.

Final recommendation: selectively absorb Graphiti’s temporal/provenance concepts into a Hermes-native, local, proof-gated graph-lite evidence manifest. Reject direct runtime integration, graph DB dependencies, telemetry, and third-party MCP/server execution during this phase.
