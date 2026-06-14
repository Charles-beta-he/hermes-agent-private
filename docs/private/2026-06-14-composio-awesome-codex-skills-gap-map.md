# ComposioHQ awesome-codex-skills → Hermes skill-graph gap map

Generated: 2026-06-14T04:34:40+08:00
Task: `2026-06-14-composiohq-awesome-codex-skills技能图谱与外部执行器治理吸收评估`
Scope: source-review / gap mapping only. No third-party skill install, Composio CLI install/login/link/execute, external app mutation, or Hermes config mutation was performed.

## 1. Live source evidence

### ComposioHQ/awesome-codex-skills

- Repo: `https://github.com/ComposioHQ/awesome-codex-skills`
- HEAD observed via `git ls-remote`: `9c9da64cf1bbea611d43dd14a10788d55369b353`
- GitHub API default branch: `master`
- Description: `A curated list of practical Codex skills for automating workflows across the Codex CLI and API.`
- README evidence: Codex skills are modular instruction bundles with `SKILL.md` metadata; Codex loads body after trigger to keep context lean.
- Install evidence: README recommends `python skill-installer/scripts/install-skill-from-github.py ...` into `$CODEX_HOME/skills`; this differs from the user-social summary's `npx skills add` framing, which appears in some external skills such as `codebase-recon-skill`.
- Top-level API evidence includes `README.md`, `connect/`, `connect-apps/`, `codebase-migrate/`, `create-plan/`, `deploy-pipeline/`, `gh-address-comments/`, `gh-fix-ci/`, `mcp-builder/`, `pr-review-ci-fix/`, `sentry-triage/`, `webapp-testing/`, `skill-creator/`, `skill-installer/`, `skill-share/`, and `template-skill`.

### brooks-lint

- Repo: `https://github.com/hyhmrright/brooks-lint`
- GitHub API evidence: default branch `main`, language `JavaScript`, license `MIT`, stars/forks observed `1041/48`, updated `2026-06-13T19:36:57Z`.
- Description evidence: `AI code reviews grounded in 12 classic engineering books — decay risk diagnostics with book citations, severity labels, and 6 analysis modes including full-sweep auto-fix`.
- README evidence: current claim is **twelve** books, not six; production-code decay dimensions and test-suite decay dimensions are citation-backed.
- Relevant structure: `.codex-plugin/`, `.claude-plugin/`, `skills/`, `commands/`, `evals/`, `hooks/`, `.brooks-lint.example.yaml`, `AGENTS.md`.

### codebase-recon-skill

- Repo: `https://github.com/yujiachen-y/codebase-recon-skill`
- GitHub API evidence: default branch `main`, license `MIT`, stars/forks observed `20/1`, updated `2026-05-30T05:46:16Z`.
- README evidence: analyzes git history before reading code; probes repo scale and runs seven dimensions: code hotspots, bus factor, bug magnets, team momentum, firefighting frequency, recently added files, and active contributors.
- Requirements evidence: only `git` and a repo with commit history.
- Install evidence: advertises `npx skills add yujiachen-y/codebase-recon-skill`, Claude plugin, and Codex plugin routes.

### connect / connect-apps

- Source: `ComposioHQ/awesome-codex-skills` folders `connect/` and `connect-apps/`.
- `connect-apps/SKILL.md` evidence: connects Claude to Gmail, Slack, GitHub, Notion, and 1000+ services via Composio CLI; setup uses `curl -fsSL https://composio.dev/install | bash`, `composio login`, `composio link <toolkit>`, and `composio execute <SLUG> -d '{...}'`.
- `connect/SKILL.md` evidence: connects Codex to any app; supports `search`, `tools info`, `execute --get-schema`, `execute --dry-run`, `run`, and `proxy`.
- Env evidence: `COMPOSIO_API_KEY`, `COMPOSIO_BASE_URL`, `COMPOSIO_SESSION_DIR`, `COMPOSIO_DISABLE_TELEMETRY=true`.

## 2. Hermes overlap / gap matrix

| External pattern | Existing Hermes overlap | Gap | Decision |
|---|---|---|---|
| Skill index with `SKILL.md` metadata and lazy loading | Hermes built-in `skills/`, optional-skills, slash skill commands, AGENTS guidance | No canonical imported external-skill review packet for third-party skill bundles | **Create workflow-pack candidate**, not bulk-import. Treat curated list as signal only. |
| brooks-lint citation-backed review | `skills/github/github-code-review/references/review-output-template.md` severity guide; `skills/software-development/requesting-code-review/SKILL.md` independent reviewer fail-closed JSON | Hermes severity gates are pragmatic but not book/citation-backed; no rubric provenance field | **Patch candidate**: add optional `rubric_source` / `citation` fields to code-review evidence templates; do not copy brooks-lint implementation. |
| codebase-recon git-history pre-read | Hermes tool/search discipline, project context, git status/history commands, optional research/codebase skills | No standard low-token pre-read command bundle that emits machine-readable repo-risk JSON before source review | **Prototype candidate**: local deterministic `git` probe pack with hotspots/bug-magnets/bus-factor/momentum fields; no LLM-only summaries. |
| connect/connect-apps side-effect executor | Hermes tools/gateway/plugins, taskpool permission tiers, workflow kernel external side-effect gate ADRs, adapter write gates | No generic Composio adapter; external side-effect semantics need allowlist, schema readback, dry-run proof, audit/idempotency before any action | **Reject direct import**. **Watchlist / gated adapter proposal only** after exact target app/tool and human approval. |

## 3. Safety / privacy / permission red lines

1. Do not install or execute Composio CLI, `npx skills add`, `curl | bash`, or third-party skills during intake/review.
2. Do not call `composio login`, `composio link`, `composio execute`, `composio run`, or `composio proxy` without explicit target/tool/rollback/human authorization.
3. Do not upload Hermes brain notes, skills, project files, session transcripts, or credentials to third-party services.
4. `connect`/`connect-apps` must be treated as an external side-effect executor, not as an ordinary prompt skill.
5. Any future app connector must require: tool allowlist, schema readback, dry-run result, idempotency key, audit artifact, permission tier check, and post-apply readback.
6. Curated list popularity/stars are not security review evidence.

## 4. Minimal prototype / skill-patch candidate

### Candidate A — `codebase-recon-local` deterministic pre-read pack

- Input: local git repo path.
- Allowed commands: read-only `git log`, `git shortlog`, `git diff --name-only`, `git ls-files`, optional bounded `git blame` for selected files.
- Output JSON fields: `commit_count_window`, `hotspots`, `bug_magnets`, `hot_bug_intersection`, `primary_owners`, `recent_files`, `firefighting_terms`, `momentum_trend`, `limitations`.
- Verification: run on Hermes repo in read-only mode and compare JSON schema to expected fixture.
- Stop boundary: no file edits, no network, no model calls in probe phase.

### Candidate B — code-review evidence-template patch

Add optional structured fields to Hermes review output:

```yaml
rubric_source:
  - source: brooks_lint_pattern
    provenance: external_reference_not_imported
    dimension: cognitive_overload | coupling | test_quality | architecture_decay
finding:
  severity: critical | warning | suggestion
  citation: optional_book_or_internal_standard
  false_positive_guard: explicit reason the finding is observable in this diff/repo
```

This should be a documentation/template change first, then only later a verifier fixture if used in automated review.

### Candidate C — Composio adapter gate (watchlist only)

A future adapter may expose only read-only schema/dry-run first:

```json
{
  "adapter": "composio",
  "mode": "schema_or_dry_run_only",
  "allowed_toolkits": [],
  "allowed_slugs": [],
  "external_side_effects": false,
  "requires_human_gate_before_execute": true,
  "required_artifacts": ["schema_readback", "dry_run_payload", "permission_approval", "audit_log", "post_apply_readback"]
}
```

No executor should be implemented until a specific app/tool use case is authorized.

## 5. Follow-up dependency order

1. **P2 / docs-template patch**: add code-review rubric provenance fields to `skills/github/github-code-review/references/review-output-template.md` and/or `skills/software-development/requesting-code-review/SKILL.md` with tests/readback limited to docs rendering.
2. **P2 / local prototype**: implement `codebase-recon-local` read-only probe as a Hermes script or workflow pack candidate; verify with a fixture repo and current Hermes repo dry-run.
3. **P3 / governance design**: draft Composio/external-app adapter contract only; stop before CLI install/login/link/execute.
4. **P3 / skill-graph import policy**: create a standard `ExternalSkillReview` artifact schema so future awesome-list/social-source intakes do not become vague tasks.

## 6. Closeout recommendation

This task's source-review/gap-map acceptance criteria are satisfied by this artifact plus the existing intake evidence artifact. It is eligible for evaluation-task closeout as a source-review artifact. The follow-up template patch, local codebase-recon prototype, and Composio adapter contract are intentionally separate future tasks, not required deliverables for this source-review task.
