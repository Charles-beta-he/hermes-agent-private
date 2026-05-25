# ECC -> Hermes self-strengthening gap analysis (2026-05-25)

Source repo: https://github.com/affaan-m/ECC
Local inspection path: /tmp/ecc-intake
Multi-model tick: /Users/charles/.hermes/tick-20260525-063929-905705-p91640-2cf90c8e-1e671567

## Executive decision

Do not install, copy, or vendor ECC into live Hermes as the first move.

Absorb the useful patterns into a Hermes-native final architecture:

Evidence-Governed Self-Strengthening Control Plane

1. Native session/run snapshot contract.
2. Evidence-linked improvement ledger.
3. Verifier-gated promotion pipeline.
4. Install/overlay state as source of truth.
5. Explicit shim expiry: degraded fallback is migration-only, never final architecture.

This is a target architecture, not a fallback optimization plan. Compatibility shims may exist only when named, dated, tested, and assigned a removal condition.

## Source-evidenced ECC mechanisms worth absorbing

### 1. Session adapter contract

Evidence:
- `docs/SESSION-ADAPTER-CONTRACT.md:22-29` defines a canonical JSON snapshot and requires `schemaVersion: "ecc.session.v1"` plus `adapterId`.
- `docs/SESSION-ADAPTER-CONTRACT.md:30-56` includes normalized `session`, `workers`, `runtime`, and `intent` fields.

Hermes absorption:
- Add a Hermes-native `hermes.run_snapshot.v1` / `hermes.session_snapshot.v1` contract for CLI, gateway, cron, taskpool, subagent, browser, and background process runs.
- Do not expose adapter-specific details as top-level fields. Keep harness details nested under `runtime`, `artifacts`, or `adapter`.
- Use the snapshot as evidence input for session_search, taskpool proof-check, multimodel certification, dashboard, and resume/handoff.

### 2. Selective install / overlay state source-of-truth

Evidence:
- `docs/SELECTIVE-INSTALL-ARCHITECTURE.md:24-35` lists module/profile manifests, schemas, install-state, and target adapters.
- `docs/SELECTIVE-INSTALL-ARCHITECTURE.md:46-56` states current capabilities: machine-readable catalogs, dependency expansion, adapter-aware operation planning, durable install-state, and lifecycle inspection/mutation via list/doctor/repair/uninstall.

Hermes absorption:
- Convert Hermes profile overlays, skills, plugins, cron jobs, scripts, and gateway integrations into manifest-described owned resources.
- The manifest/install-state must answer: requested, resolved, written/copied/generated, owned-by-Hermes, repairable, removable.
- `post-update-check.py --fix` should eventually consume the same state instead of ad-hoc patch drift heuristics.

### 3. Self-improving harness loop

Evidence:
- `docs/ECC-2.0-REFERENCE-ARCHITECTURE.md:117-128` defines the loop as observation -> proposal -> verification -> promotion -> rollback, stores proposed improvements as trace + artifact, and promotes playbooks only after verifier-proven scenario improvement without widening blast radius.

Hermes absorption:
- Hermes already has stronger primitives than ECC for this: taskpool, skills, memory, cron, multimodel ratification, proof-checks, session_search, and private repo governance.
- The missing piece is not “more skills”; it is a formal promotion ledger that links every improvement to source evidence, scenario, verifier command, metric delta, rollback condition, and blast-radius classification.

## Explicit rejection: degraded fallback is not final architecture

Evidence:
- `docs/SESSION-ADAPTER-CONTRACT.md:283-286` says the JSON fallback recorder is a temporary compatibility shim before a dedicated state store lands.

Hermes rule:
- Any fallback path must declare `shim: true`, `owner`, `introduced_at`, `expires_or_replaced_by`, and a test proving the canonical path is preferred.
- No task may mark fallback behavior as the target optimization direction.
- Any proposal that improves only the fallback path must be classified as migration support, not final architecture.

## Multi-model result

### Round A: broad ECC -> Hermes architecture packet

Five real external models were consulted: gpt-5.5, mimo-v2.5-pro, claude-opus-4-7, deepseek-v4-pro, gemini-3.1-pro-preview.

Fan-out succeeded: 5/5 planners, quorum met.
Final certification failed intentionally:
- certification status: not_certified
- reason: truth gate could not certify because the initial state bundled the evidence in a way that left C-001/C-002/C-003 as `needs_link_review`.
- round 2 agreement matrix: 0.65, below the 0.85 certification threshold.
- key critique: architecture and implementation patches must be deferred until evidence links are explicit; no-op is also invalid while the truth gate is blocked.

Artifact:
- `/Users/charles/.hermes/tick-20260525-063929-905705-p91640-2cf90c8e-1e671567/final/certification_manifest.json`

### Round B: evidence-linked advisory architecture re-certification

A narrowed, doc-first architecture packet was rerun with explicit claim-to-evidence links:
- C-001 -> E-001: `docs/SESSION-ADAPTER-CONTRACT.md:22-56`
- C-002 -> E-002: `docs/SELECTIVE-INSTALL-ARCHITECTURE.md:24-56`
- C-003 -> E-003: `docs/ECC-2.0-REFERENCE-ARCHITECTURE.md:117-128`
- C-004 -> E-004: `docs/SESSION-ADAPTER-CONTRACT.md:283-286`

Truth gate result:
- truth_status: grounded
- can_certify_truth: true
- evidence_coverage: 1.0
- verified_claim_count: 4/4

Discussion result:
- status: not_certified
- artifact: `/Users/charles/.hermes/tick-20260525-070612-879073-p15185-a608921d-a06ce1ed/final/certification_manifest.json`
- remaining blocker: not source truth, but certification ledger consistency / noop-vs-change resolution.
- round 2 agreement matrix remained 0.65.
- issue I-003 flagged a contradiction between fanout quorum certification and `planner_patch_summary.certification_intent=unresolved_noop_vs_change`.
- issue I-002 flagged unresolved noop-vs-doc-patch intent: 1 model proposed a concrete doc verifier patch, 3 models were noop/no-change, and the certification system did not classify that as no-action certification.

Actionable interpretation:
- Source truth is now grounded.
- Full multi-model certification is still not complete.
- Next safe step is not runtime implementation; it is a narrowed certification-ledger/noop-vs-doc-patch resolution packet or a doc-only architecture note explicitly marked advisory.

### Round C: certification-ledger taxonomy fix and re-certification

The certification ledger was updated so a low-risk doc/verifier-only minority patch under a no-runtime architecture packet is classified as `doc_only_advisory`, not as a concrete runtime patch. The final manifest now separates:
- `fanout_quorum`: planner fan-out quorum only.
- `truth_grounded`: claim/evidence truth gate.
- `no_action_certification`: no patch authorization.
- `doc_only_advisory`: documentation/verifier advisory only.
- `concrete_patch_authorization`: runtime/code patch authorization.

Re-run artifact:
- `/Users/charles/.hermes/tick-20260525-074353-321801-p34492-a2445e7b-e9c8c8b8/final/certification_manifest.json`

Result:
- status: certified
- can_claim_complete_certification: true
- fanout quorum: 5/5 planners, quorum met
- truth gate: grounded, verified_claim_count=4/4, evidence_coverage=1.0
- planner_patch_intent: doc_only_advisory
- doc_only_advisory: true
- concrete_patch_authorization: false
- approval_count: 5/5
- reject_count: 0
- unresolved_severity_ge_3_count: 0

Interpretation:
- The ECC-derived Hermes-native target architecture is now certified as a doc-only advisory architecture decision.
- This still does not authorize runtime implementation. Runtime changes require a later implementation slice with concrete verifier gates and a new certification packet.

## Final Hermes target architecture

### Layer A: Run/session snapshot contract

Deliverable:
- `docs/private/hermes-run-snapshot-v1.md` or public architecture doc if sanitized.
- Schema candidates for `hermes.run_snapshot.v1` and `hermes.session_snapshot.v1`.

Core fields:
- schema_version
- adapter_id
- run/session id
- source: cli/gateway/cron/taskpool/subagent/browser/background
- objective and task_id
- repo/workdir/profile
- worker/subagent/process list
- tools invoked summary
- artifacts and proof refs
- verifier results
- risk and fallback flags

Proof gate:
- fixture validation for CLI run, taskpool task, cron run, and background process.
- no adapter-specific top-level fields.

### Layer B: Evidence-linked improvement ledger

Deliverable:
- `docs/private/self-strengthening-ledger-v1.md` plus minimal JSON fixture.

Required lifecycle:
1. observe
2. propose
3. verify
4. ratify
5. promote
6. rollback/retire

Required links:
- source evidence refs
- scenario id
- capability_delta
- anti_drift_check
- verifier command
- before/after metrics
- blast radius
- rollback signal
- expiry for any shim/fallback

Proof gate:
- proof-check refuses promotion when any high-severity claim lacks supporting evidence.
- skill updates and private docs can land as advisory before code, but code promotion requires verifier results.

### Layer C: Overlay/install-state source of truth

Deliverable:
- Manifest/state model for Hermes-owned profile resources.

Scope:
- skills
- scripts
- plugins
- cron jobs
- gateway hooks/platform config templates
- private overlay patches

Proof gate:
- list/doctor/repair/uninstall dry-run can explain owned resources without guessing.
- update flow can distinguish upstream files, private overlay files, and generated files.

### Layer D: No degraded-fallback finalization gate

Deliverable:
- A deterministic check in taskpool/proof-check or post-update-check that flags fallback-as-target.

Rule:
- fallback/shim code is allowed only with canonical replacement path and expiry.
- degraded compatibility behavior cannot be the final architecture acceptance criterion.

## Implementation slices

P1. Evidence grounding and architecture docs
- Link claims to line-number evidence from ECC docs.
- Write Hermes snapshot and ledger contracts.
- Re-run multimodel certification on evidence-linked packet.

P2. Verifier gates
- Add static verifier for `shim: true` entries and unsupported high-severity claims.
- Add fixtures proving canonical path preferred over fallback path.

P2. Snapshot emitter prototype
- Emit run/session snapshot for one bounded surface first: taskpool run or background process.
- Store as artifact, not new global behavior.

P3. Overlay install-state discovery
- Inventory skills/scripts/plugins/cron resources and classify owned/generated/manual.
- No mutation; only read-only report first.

## Red lines

- Do not clone/install ECC into live Hermes.
- Do not turn temporary JSON fallback or graceful degradation into the target architecture.
- Do not promote skills/code without verifier evidence.
- Do not claim multi-model certification when discussion_result status is `not_certified`.
- Do not let cross-harness compatibility erase Hermes-native primitives.

## Verification commands used

```bash
git clone --depth 1 https://github.com/affaan-m/ECC /tmp/ecc-intake
python3 ~/.hermes/scripts/consult_models.py run /Users/charles/.hermes/state/ecc-hermes-gap-analysis-state.json --tier T2 --quorum 5 --discussion --max-rounds 2 --keep --timeout 180
python3 ~/.hermes/scripts/consult_models.py run /Users/charles/.hermes/state/ecc-hermes-evidence-linked-recert-state.json --tier T2 --quorum 5 --discussion --max-rounds 2 --keep --timeout 180
python3 ~/.hermes/scripts/consult_models.py run /Users/charles/.hermes/state/ecc-hermes-evidence-linked-recert-state.json --tier T2 --quorum 5 --discussion --max-rounds 2 --keep --timeout 180
python3 ~/.hermes/scripts/consult_models.py selfcheck --json
python3 ~/.hermes/scripts/brain_task_orchestrator.py selfcheck --json
```
