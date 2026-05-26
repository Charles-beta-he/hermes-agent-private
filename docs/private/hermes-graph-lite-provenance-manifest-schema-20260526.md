# Hermes graph-lite provenance manifest schema

Date: 2026-05-26
Task: `2026-05-26-design-hermes-graph-lite-provenance-manifest-schema-from-graphiti-intake`
Source intake: `docs/private/graphiti-gap-analysis-20260525.md`
Mode: Hermes-native design only. No Graphiti runtime, graph database, Docker, MCP server, telemetry, external provider call, private data indexing, or automatic memory write is introduced.

## Objective

Define a small local manifest that can explain why a taskpool/local-brain claim exists, when it is valid, which synthetic/non-private source episode supports it, and which Hermes truth gate must remain authoritative before lifecycle advancement.

## Non-goals and red lines

- No Graphiti package import or runtime adoption.
- No Neo4j/FalkorDB/Kuzu/Neptune, Docker, MCP server, examples, or service startup.
- No raw session logs, taskpool logs, API keys, private user messages, or private data indexing.
- No automatic long-term memory writes.
- The manifest is advisory provenance metadata; task notes, proof logs, command artifacts, and truth gates remain authoritative.

## Manifest envelope: `graph_lite_manifest_v1`

A manifest is a local JSON/YAML document or embedded task artifact with these fields:

| Field | Type | Required | Meaning | Gate |
|---|---|---:|---|---|
| `schema` | string | yes | Must equal `hermes_graph_lite_provenance_manifest_v1`. | Reject unknown schema for automated consumers. |
| `manifest_id` | string | yes | Stable local id; recommended `glm-<date>-<slug>`. | Must not contain secrets/private content. |
| `profile` | string | yes | Hermes profile or brain profile scope. | Cross-profile writes remain forbidden unless explicitly authorized. |
| `privacy_class` | enum | yes | `synthetic`, `public_reference`, `private_metadata`, `private_content`. | First implementation may only accept `synthetic` and `public_reference`. |
| `source_episodes` | array | yes | Non-private source anchors that produced claims. | Each episode must have source ref and observed/reference time. |
| `claims` | array | yes | Atomic advisory claims derived from episodes. | Each claim must bind to at least one episode and one truth gate. |
| `relationships` | array | no | Optional graph-lite edges (`supports`, `supersedes`, `contradicts`, `depends_on`). | Consumers must treat unknown relation as advisory only. |
| `truth_gates` | array | yes | Hermes gates that must pass before claim affects lifecycle. | Manifest cannot bypass gate outcome. |
| `generated_by` | object | yes | Tool/actor and command that produced manifest. | Must be reproducible or explicitly manual. |
| `created_at` | datetime | yes | Wall-clock manifest creation time. | ISO-8601 with timezone. |

## Source episode object

```json
{
  "episode_id": "episode-graphiti-intake-public-readme",
  "source_kind": "public_reference_doc",
  "source_ref": "docs/private/graphiti-gap-analysis-20260525.md#2.1",
  "source_title": "Graphiti temporal context graph intake summary",
  "observed_at": "2026-05-26T00:00:00+08:00",
  "reference_time": "2026-05-25T00:00:00+08:00",
  "content_digest": "sha256:<digest-of-non-private-artifact>",
  "privacy_class": "public_reference",
  "retention": "local_reference_only"
}
```

Rules:

- `observed_at` is when Hermes observed/captured the source.
- `reference_time` is the event/document time the source describes.
- `content_digest` is optional for hand-authored design notes but required for machine-generated manifests.
- `privacy_class=private_content` is prohibited for this first slice.

## Claim object

```json
{
  "claim_id": "claim-validity-windows-useful-for-evidence-freshness",
  "claim_text": "Validity windows can help Hermes mark advisory claims stale or superseded without changing proof-log authority.",
  "claim_kind": "architecture_delta",
  "episode_ids": ["episode-graphiti-intake-public-readme"],
  "task_id": "2026-05-26-design-hermes-graph-lite-provenance-manifest-schema-from-graphiti-intake",
  "valid_from": "2026-05-26T00:00:00+08:00",
  "valid_until": null,
  "invalid_at": null,
  "superseded_by": null,
  "confidence": "medium",
  "truth_gate_ids": ["gate-no-runtime-no-private-indexing", "gate-proof-log-authoritative"],
  "advisory_only": true
}
```

Rules:

- Claims are advisory until their truth gates pass.
- `valid_until`/`invalid_at` mark temporal decay or rejection; `superseded_by` points to a newer `claim_id`.
- `confidence` is not a lifecycle gate and cannot close a task.
- `advisory_only=true` is required unless a future ratified gate explicitly allows lifecycle use.

## Relationship object

```json
{
  "relationship_id": "rel-claim-supported-by-episode",
  "from": "claim-validity-windows-useful-for-evidence-freshness",
  "to": "episode-graphiti-intake-public-readme",
  "type": "supported_by",
  "created_at": "2026-05-26T09:25:00+08:00"
}
```

Allowed first-slice relationship types: `supported_by`, `supersedes`, `contradicts`, `depends_on`, `mentions_task`, `binds_truth_gate`.

## Truth gate binding

```json
{
  "truth_gate_id": "gate-no-runtime-no-private-indexing",
  "gate_kind": "red_line",
  "authority": "taskpool_proof_check_and_manual_review",
  "required_status": "PASS",
  "failure_action": "block_review_closeout",
  "description": "Reject any implementation that starts Graphiti/graph DB/Docker/MCP or indexes private data."
}
```

Required first-slice gates:

1. `gate-no-runtime-no-private-indexing`: no Graphiti runtime, graph DB, Docker, MCP server, telemetry, private/session/taskpool raw-data indexing, or API-key exposure.
2. `gate-proof-log-authoritative`: proof logs, command artifacts, and task lifecycle gates remain authoritative over manifest claims.
3. `gate-synthetic-fixture-only`: any fixture must be synthetic or public-reference metadata only.
4. `gate-review-before-done`: evolution tasks can move to done only after their task-specific evidence and governing review/ratification requirements are met.

## Deterministic consumer behavior

A future reader should implement these checks before using a manifest:

- Reject if `schema` is not `hermes_graph_lite_provenance_manifest_v1`.
- Reject if any source episode has `privacy_class=private_content`.
- Warn, do not fail closed, if an advisory relationship type is unknown.
- Treat claims with `invalid_at` or `superseded_by` as stale.
- Never change task lifecycle based on manifest presence alone; require the taskpool transition/proof path.

## Synthetic fixture

A companion fixture was written at:

`docs/private/hermes-graph-lite-provenance-manifest-synthetic-fixture-20260526.json`

It uses synthetic/public-reference metadata only and demonstrates:

- one source episode anchored to the Graphiti gap-analysis note;
- two advisory claims;
- truth-gate bindings for red-line and proof-log authority;
- one support relationship and one truth-gate binding relationship.

## Bounded closeout status

This tranche satisfies the task-specific design artifact requirement by producing a local manifest schema and synthetic fixture. It does not implement a runtime consumer, MCP surface, graph database, memory write path, or automated lifecycle closer. Remaining work, if desired, should be a separate implementation/evaluation task with synthetic fixtures and focused tests.
