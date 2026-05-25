# getzep/graphiti → Hermes local-brain gap-analysis intake

Date: 2026-05-25
Source: https://github.com/getzep/graphiti
Scope: governed external-repo intake for Hermes self-strengthening. This is **not** an integration claim.

## Live source evidence

- GitHub API repo: `getzep/graphiti`
- Description: `Build Real-Time Knowledge Graphs for AI Agents`
- Observed stars/forks: 26,507 stars / 2,638 forks
- Language: Python
- License: Apache-2.0
- Default branch: `main`
- Last pushed: `2026-05-21T03:32:49Z`
- Topics: `agents`, `graph`, `llms`, `rag`
- Top-level structure observed: `graphiti_core/`, `server/`, `mcp_server/`, `examples/`, `tests/`, `spec/`, `Dockerfile`, `docker-compose.yml`, `pyproject.toml`, `README.md`, `AGENTS.md`, `CLAUDE.md`, `OTEL_TRACING.md`.

## Architecture signals from source

### README / project positioning

Graphiti describes itself as a framework for building temporal context graphs for AI agents. The README emphasizes:

- facts/relationships with temporal validity windows;
- provenance back to source episodes;
- continuously integrating user interactions, structured/unstructured enterprise data, and external information;
- incremental updates without complete graph recomputation;
- hybrid retrieval combining semantic search, keyword search, and graph traversal.

### Core code layout

`graphiti_core/` includes:

- `graphiti.py`: main orchestration entry point.
- `nodes.py`, `edges.py`, `models/`: graph primitives.
- `driver/`: database drivers, including Neo4j/FalkorDB/Kuzu/Neptune surfaces per repo guidance.
- `llm_client/`, `embedder/`, `cross_encoder/`: provider and reranker surfaces.
- `search/`: hybrid search configuration/execution.
- `prompts/`: extraction/dedup/summarization prompts.
- `migrations/`, `telemetry/`, `tracer.py`: runtime operations and observability.

### MCP and service surfaces

`mcp_server/README.md` describes an experimental MCP server exposing Graphiti capabilities to AI assistants:

- episode management;
- entity/fact/node search;
- group management;
- graph clearing/index rebuild;
- FalkorDB/Neo4j backend options;
- OpenAI/Anthropic/Gemini/Groq/Azure OpenAI LLM support;
- OpenAI/Voyage/SentenceTransformers/Gemini embeddings;
- HTTP MCP endpoint at `/mcp/`;
- queue-based async episode processing.

`server/README.md` describes a FastAPI service and Docker deployment that depends on Neo4j plus OpenAI credentials.

### Dependency signals

`pyproject.toml` package `graphiti-core` v0.29.1 requires Python `>=3.10,<4`. Core dependencies include `pydantic`, `neo4j`, `openai`, `tenacity`, `numpy`, `python-dotenv`, and `posthog`. Optional extras include Anthropic, Groq, Google GenAI, Kuzu, FalkorDB, Neo4j/OpenSearch, SentenceTransformers, Neptune, and tracing.

## Hermes gap mapping

Graphiti is relevant to Hermes in four bounded ways:

1. **Temporal/provenance-aware memory model**
   - Potential delta: Hermes memory and taskpool evidence can benefit from validity windows, source episodes, supersession, and provenance-preserving retrieval.
   - Hermes constraint: Graphiti retrieval must not become a truth source. Hermes `truth_gate`, taskpool evidence bundles, and proof logs stay authoritative.

2. **Hybrid retrieval / relationship recall**
   - Potential delta: semantic + keyword + graph traversal patterns may improve recall over flat notes/session search when relationships and time matter.
   - Hermes constraint: first slice should be a local graph-lite/provenance manifest or evaluation harness, not Neo4j/FalkorDB as a new core dependency.

3. **MCP memory interface pattern**
   - Potential delta: Graphiti MCP server is a useful reference for exposing episode/entity/fact operations to AI tools.
   - Hermes constraint: use as interface/design reference only during intake; do not run the third-party MCP server against Hermes data.

4. **Long-term local-brain dashboard/query UX**
   - Potential delta: temporal context graph concepts may improve how Hermes explains why a task, memory, or evidence chain is current/stale/superseded.
   - Hermes constraint: no automatic indexing of private user/project content into external graph DBs or telemetry-enabled runtimes.

## Multi-model intake result

Actual T1 fan-out was run from `/Users/charles/.hermes/tmp/graphiti-intake-state.json`.

- Artifact: `/Users/charles/.hermes/tick-20260525-093207-840883-p17556-f424417a-7161948a/`
- Participants observed: `claude-opus-4-7`, `gpt-5.5`, `mimo-v2.5-pro`
- Fan-out status: quorum met (`3/2`)
- Consensus: high-signal but not currently blocking; classify as `P2`, `lane=evolution`, `architecture_target=local-brain`.
- Important nuance: planners flagged evidence as pre-ingest/advisory and requested file-level grounding before any absorption claim. Therefore this intake document records a governed task, not integration.

## Intake decision

Create a taskpool item:

- Title: `Gap-analyze getzep/graphiti for Hermes temporal context graph memory absorption`
- Priority: `P2`
- Lane: `evolution`
- Project: `hermes-agent`
- Architecture target: `local-brain`
- Requires multimodel: `true` for final architecture/absorption certification, because the repo touches memory, privacy, MCP exposure, and taskpool evidence lifecycle.
- Execution shape: read-only gap analysis first; any implementation/prototype must be split into follow-up tasks with explicit verification.

## Red lines / anti-drift

- Do not clone/install/run Graphiti during intake unless explicitly authorized.
- Do not run Neo4j/FalkorDB/Kuzu/Neptune/Docker/MCP server/examples as part of intake.
- Do not store private user data, taskpool data, session logs, API keys, or secrets in Graphiti or any external graph database.
- Do not let Graphiti retrieval override Hermes memory/taskpool truth gates, proof logs, evidence bundles, or user/developer instructions.
- Do not introduce Neo4j/FalkorDB/Kuzu as a Hermes core dependency in the first slice.
- Do not pipe Hermes user data through PostHog/telemetry paths.
- Do not produce a generic repo summary; the deliverable must be a Hermes gap matrix plus selective accept/reject/watch decisions.

## Smallest verifiable next slice

A bounded worker should produce a read-only comparison artifact with:

1. file-level Graphiti evidence for temporal schema, episode provenance, search/retrieval config, MCP tools, and telemetry/dependency surfaces;
2. Hermes-side anchors for memory/session search/taskpool evidence/truth gates/native MCP integration;
3. accept/reject/watch matrix per subsystem;
4. a graph-lite prototype proposal that avoids heavy DB dependencies, or an explicit rejection if no small safe slice exists;
5. taskpool proof log and selfcheck/proof-check after any Hermes-facing task note changes.

## Initial follow-up shape

If accepted, split follow-ups in dependency order:

1. `graphiti-evidence-map`: source-code-level temporal/provenance/MCP/dependency inventory.
2. `hermes-local-brain-gap-map`: current Hermes memory/taskpool/evidence architecture comparison.
3. `graph-lite-prototype-spike`: optional stdlib/local manifest spike only if prior maps justify it.
4. `truth-gate-compat-review`: prove Graphiti-like recall cannot bypass evidence/truth gates.
