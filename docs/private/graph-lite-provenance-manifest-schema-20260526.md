# Hermes graph-lite provenance manifest schema

Date: 2026-05-26
Status: proposed design artifact
Task: 2026-05-26-design-hermes-graph-lite-provenance-manifest-schema-from-graphiti-intake
Reference intake: `docs/private/graphiti-gap-analysis-20260525.md`

## Boundary statement

This is a Hermes-native local manifest design. It introduces no Graphiti runtime, no graph database backend, no Docker service, no third-party MCP server, and no indexing of private user data, raw taskpool logs, raw session logs, or API keys.

The manifest is a small JSON/YAML-compatible artifact that records claim provenance and temporal validity. It is intended for deterministic local checks over explicit artifacts. It is not a truth source by itself: Hermes proof logs, task lifecycle state, command artifacts, and truth-gate results remain authoritative.

All examples below are synthetic and non-private.

## Design goals

1. Capture the Graphiti-inspired concepts accepted by the intake: source episodes, fact-to-episode links, validity windows, and supersession.
2. Keep Hermes boundaries intact: profile-scoped, local files only, no automatic durable memory writes, no private log ingestion.
3. Bind every claim to a truth gate so graph-lite recall remains advisory unless fresh proof exists.
4. Make stale and superseded claims machine-checkable without a graph service.

## Manifest shape

A manifest is one local file containing a header plus arrays of source episodes, claims, truth gates, and supersession links.

```json
{
  "schema": "hermes_graph_lite_provenance_manifest_v1",
  "manifest_id": "manifest.synthetic.graph-lite.demo-20260526",
  "created_at": "2026-05-26T00:00:00Z",
  "profile": "default",
  "project": "hermes-agent",
  "privacy_class": "synthetic_non_private",
  "source_policy": {
    "allowed_source_kinds": ["artifact", "command_output", "manual_note", "synthetic_fixture"],
    "forbidden_source_kinds": ["raw_session_log", "raw_taskpool_log", "api_key", "external_graph_db"],
    "raw_private_content_embedded": false
  },
  "episodes": [],
  "claims": [],
  "truth_gates": [],
  "supersession": []
}
```

## Top-level fields

| Field | Required | Meaning |
|---|---:|---|
| `schema` | yes | Literal schema id; current value is `hermes_graph_lite_provenance_manifest_v1`. |
| `manifest_id` | yes | Stable local id for this manifest. |
| `created_at` | yes | Manifest creation time in RFC 3339 / ISO 8601 UTC. |
| `profile` | yes | Hermes profile namespace that owns the manifest. |
| `project` | yes | Project/profile target, e.g. `hermes-agent`. |
| `privacy_class` | yes | One of `synthetic_non_private`, `public_reference`, `local_non_secret_metadata`, `restricted_metadata`. Default for fixtures is `synthetic_non_private`. |
| `source_policy` | yes | Explicit allow/deny list for source kinds and whether raw private content is embedded. |
| `episodes` | yes | Source episode records. |
| `claims` | yes | Derived claim records. |
| `truth_gates` | yes | Gate records that decide whether claims may be treated as current delivery evidence. |
| `supersession` | yes | Explicit replacement/invalidation edges between claims. |

## Source episode record

A source episode is the smallest cited provenance unit. It points at an artifact or bounded note and carries observation time separately from validity time.

```json
{
  "episode_id": "episode.synthetic.command.selfcheck.001",
  "source_kind": "command_output",
  "source_ref": "artifact:/tmp/hermes-synthetic/selfcheck-output.json",
  "source_description": "Synthetic selfcheck output fixture, not a real private task log",
  "observed_at": "2026-05-26T00:01:00Z",
  "reference_time": "2026-05-26T00:00:59Z",
  "content_hash": "sha256:synthetic-placeholder-not-a-real-secret",
  "content_excerpt": "status=PASS for synthetic fixture",
  "privacy_class": "synthetic_non_private",
  "raw_content_embedded": false,
  "retention": {
    "expires_at": null,
    "delete_raw_after": null
  }
}
```

Episode rules:

- `source_ref` must be explicit and local or public-reference safe.
- `content_excerpt` must be short and non-sensitive; private raw logs are not copied into manifests.
- `observed_at` is when Hermes saw the episode; `reference_time` is the event time asserted by the source.
- `content_hash` binds the episode to external evidence without embedding that evidence.

## Claim record

A claim is an advisory statement derived from one or more episodes. It is only current if its temporal window is open, it is not superseded, and its bound truth gate is passing.

```json
{
  "claim_id": "claim.synthetic.schema.boundary.001",
  "claim_text": "The graph-lite manifest design does not require a graph database service.",
  "claim_type": "architecture_boundary",
  "episodes": ["episode.synthetic.intake.graphiti.001"],
  "derived_at": "2026-05-26T00:02:00Z",
  "valid_from": "2026-05-26T00:02:00Z",
  "valid_until": null,
  "invalid_at": null,
  "superseded_by": [],
  "confidence": "high",
  "truth_gate_id": "gate.synthetic.no-runtime-redlines.001",
  "task_id": "synthetic-task-graph-lite-demo",
  "profile": "default",
  "evidence_hashes": ["sha256:synthetic-placeholder-not-a-real-secret"],
  "privacy_class": "synthetic_non_private",
  "advisory_only": true
}
```

Claim rules:

- `episodes` must not be empty.
- `truth_gate_id` must refer to a gate in this manifest or a known local gate catalog.
- `valid_from`, `valid_until`, and `invalid_at` must use RFC 3339 timestamps when present.
- `advisory_only` is true by default. Turning a claim into delivery evidence requires the referenced truth gate to pass and the task proof log to cite the real artifact.

## Truth-gate binding record

A truth gate records the deterministic checks that make a claim safe to use. It does not rely on model agreement.

```json
{
  "truth_gate_id": "gate.synthetic.no-runtime-redlines.001",
  "gate_type": "red_line_check",
  "status": "pass",
  "checked_at": "2026-05-26T00:03:00Z",
  "required_evidence": [
    "file:docs/private/graphiti-gap-analysis-20260525.md",
    "file:docs/private/graph-lite-provenance-manifest-schema-20260526.md",
    "command:brain_task_orchestrator.py selfcheck --json"
  ],
  "pass_conditions": [
    "No Graphiti runtime introduced",
    "No graph database introduced",
    "No Docker service introduced",
    "No third-party MCP server introduced",
    "No private data indexing introduced"
  ],
  "failure_mode": "claim_is_stale_or_unusable_as_proof"
}
```

Gate rules:

- `status` is one of `pass`, `fail`, `blocked`, or `unknown`.
- `required_evidence` must contain concrete file, command, or artifact references.
- If the gate is not `pass`, all bound claims are advisory only and cannot close a task.

## Supersession record

Supersession is explicit so a newer claim can replace or narrow an older claim without deleting history.

```json
{
  "supersession_id": "supersession.synthetic.claim.001",
  "from_claim_id": "claim.synthetic.schema.boundary.001",
  "to_claim_id": "claim.synthetic.schema.boundary.002",
  "reason": "Synthetic example: design narrowed from generic graph-lite to provenance-manifest-only scope.",
  "superseded_at": "2026-05-26T00:04:00Z",
  "mode": "narrows",
  "episode_id": "episode.synthetic.design-review.001"
}
```

Supersession rules:

- `from_claim_id` and `to_claim_id` must be different.
- `mode` is one of `replaces`, `narrows`, `invalidates`, or `corrects`.
- A superseded claim remains queryable for audit, but current-claim lookup must prefer `to_claim_id` when its gate passes.

## Minimal current-claim algorithm

A deterministic graph-lite lookup can operate over one manifest file:

1. Load the manifest and reject it if `schema` is not `hermes_graph_lite_provenance_manifest_v1`.
2. Reject the manifest for proof use if `source_policy.raw_private_content_embedded` is true.
3. Build maps for episodes, claims, gates, and supersession edges.
4. For each claim:
   - require at least one existing episode id;
   - require a truth gate with `status == "pass"` for proof use;
   - require `now >= valid_from` when `valid_from` is present;
   - reject when `valid_until <= now` or `invalid_at <= now`;
   - reject when an outgoing supersession edge points to a passing replacement claim.
5. Return current claims with their episode refs and gate evidence. Return stale/superseded claims separately for audit.

## Synthetic complete fixture

```json
{
  "schema": "hermes_graph_lite_provenance_manifest_v1",
  "manifest_id": "manifest.synthetic.graph-lite.demo-20260526",
  "created_at": "2026-05-26T00:00:00Z",
  "profile": "default",
  "project": "hermes-agent",
  "privacy_class": "synthetic_non_private",
  "source_policy": {
    "allowed_source_kinds": ["artifact", "command_output", "manual_note", "synthetic_fixture"],
    "forbidden_source_kinds": ["raw_session_log", "raw_taskpool_log", "api_key", "external_graph_db"],
    "raw_private_content_embedded": false
  },
  "episodes": [
    {
      "episode_id": "episode.synthetic.intake.graphiti.001",
      "source_kind": "artifact",
      "source_ref": "file:docs/private/graphiti-gap-analysis-20260525.md",
      "source_description": "Public-reference Graphiti intake mapped to Hermes boundaries",
      "observed_at": "2026-05-26T00:01:00Z",
      "reference_time": "2026-05-26T00:00:00Z",
      "content_hash": "sha256:synthetic-placeholder-not-a-real-secret",
      "content_excerpt": "ACCEPT CONCEPT, REJECT DIRECT CORE DEPENDENCY",
      "privacy_class": "public_reference",
      "raw_content_embedded": false,
      "retention": {"expires_at": null, "delete_raw_after": null}
    },
    {
      "episode_id": "episode.synthetic.command.selfcheck.001",
      "source_kind": "command_output",
      "source_ref": "artifact:/tmp/hermes-synthetic/selfcheck-output.json",
      "source_description": "Synthetic command-output placeholder for schema example",
      "observed_at": "2026-05-26T00:03:00Z",
      "reference_time": "2026-05-26T00:03:00Z",
      "content_hash": "sha256:synthetic-placeholder-not-a-real-secret-2",
      "content_excerpt": "status=PASS",
      "privacy_class": "synthetic_non_private",
      "raw_content_embedded": false,
      "retention": {"expires_at": null, "delete_raw_after": null}
    }
  ],
  "truth_gates": [
    {
      "truth_gate_id": "gate.synthetic.no-runtime-redlines.001",
      "gate_type": "red_line_check",
      "status": "pass",
      "checked_at": "2026-05-26T00:03:00Z",
      "required_evidence": [
        "file:docs/private/graphiti-gap-analysis-20260525.md",
        "file:docs/private/graph-lite-provenance-manifest-schema-20260526.md",
        "command:brain_task_orchestrator.py selfcheck --json"
      ],
      "pass_conditions": [
        "No Graphiti runtime introduced",
        "No graph database introduced",
        "No Docker service introduced",
        "No third-party MCP server introduced",
        "No private data indexing introduced"
      ],
      "failure_mode": "claim_is_stale_or_unusable_as_proof"
    }
  ],
  "claims": [
    {
      "claim_id": "claim.synthetic.schema.boundary.001",
      "claim_text": "Hermes graph-lite provenance can be represented as local manifest files without Graphiti runtime adoption.",
      "claim_type": "architecture_boundary",
      "episodes": ["episode.synthetic.intake.graphiti.001"],
      "derived_at": "2026-05-26T00:02:00Z",
      "valid_from": "2026-05-26T00:02:00Z",
      "valid_until": null,
      "invalid_at": null,
      "superseded_by": [],
      "confidence": "high",
      "truth_gate_id": "gate.synthetic.no-runtime-redlines.001",
      "task_id": "synthetic-task-graph-lite-demo",
      "profile": "default",
      "evidence_hashes": ["sha256:synthetic-placeholder-not-a-real-secret"],
      "privacy_class": "synthetic_non_private",
      "advisory_only": true
    }
  ],
  "supersession": []
}
```

## Architecture boundaries and follow-up order

Recommended dependency order after this design artifact:

1. Add a tiny JSON-schema or Python validator for synthetic manifests only.
2. Add synthetic fixture tests for current/stale/superseded claim selection.
3. Compare deterministic manifest lookup against current task evidence/session search on synthetic cases.
4. Only after those pass, consider a Hermes-native read-only query surface. Do not add Graphiti, graph database backends, Docker services, third-party MCP servers, or private data indexing.

## Verification expectations for this artifact

- The companion intake artifact exists at `docs/private/graphiti-gap-analysis-20260525.md`.
- This schema artifact is local documentation only.
- All examples are synthetic/non-private.
- Selfcheck and proof-boundary verification must pass before handoff.
