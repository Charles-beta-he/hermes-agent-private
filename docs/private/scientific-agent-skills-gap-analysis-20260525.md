# Scientific Agent Skills / K-Dense BYOK → Hermes skill-graph gap analysis

Source inspected: https://github.com/K-Dense-AI/scientific-agent-skills
Commit inspected: `66d1ad45ccbfe18f8665cd72d1ecb1043cd678f9` on `main`
Inspection date: 2026-05-25
Task: `2026-05-25-评估-scientific-agent-skills-k-dense-byok-对-hermes-skill-graph-的可吸收模式`

## Live source evidence

GitHub/API snapshot collected during this tranche:

- Repository: `K-Dense-AI/scientific-agent-skills`
- Description: “A set of ready to use Agent Skills for research, science, engineering, analysis, finance and writing.”
- Default branch: `main`
- HEAD: `66d1ad45ccbfe18f8665cd72d1ecb1043cd678f9`
- Primary language: Python
- Stars observed: `25718`
- License: MIT (`LICENSE.md` also contains MIT text)
- Top-level layout: `.github/`, `docs/`, `scientific-skills/`, `README.md`, `SECURITY.md`, `scan_skills.py`, `scan_pr_skills.py`, `pyproject.toml`, `uv.lock`.
- README claims: 138 ready-to-use Agent Skills, 100+ scientific databases, compatibility with Cursor / Claude Code / Codex, and a K-Dense BYOK desktop co-scientist with local file handling plus optional Modal cloud scaling.

Representative skill samples inspected:

- `scientific-skills/database-lookup/SKILL.md`: one large unified database selector over 78 public REST APIs; frontmatter uses `name`, long `description`, and `metadata.skill-author`; body emphasizes raw JSON, endpoints used, no-result disclosure, and reading per-database references before calls.
- `scientific-skills/literature-review/SKILL.md`: richer operational skill with `allowed-tools: Read Write Edit Bash`, `license`, `metadata.skill-author`, phase workflow, generated figures requirement, scripts/assets/references structure, and cross-skill dependency on `parallel-web` and scientific schematics.
- `SECURITY.md`: generated scan report over 138 skills; 856 findings; 68 critical and 18 high; 107/138 marked safe. Several communication/research-generation skills are critical, while `database-lookup` is low and safe. This is strong evidence that direct import is unsafe even though the repo ships its own scanner.
- `scan_skills.py`: scanner invokes `skill_scanner` with behavioral, trigger, and LLM analyzers; report generation records per-skill severity, finding count, safety flag, and remediation text.

## Hermes skill graph comparison

Hermes in-repo skill authoring (`skills/software-development/hermes-agent-skill-authoring/SKILL.md`) currently expects:

- byte-0 YAML frontmatter with required `name` and `description`;
- description length ≤ 1024 chars;
- non-empty body after frontmatter;
- peer metadata fields: `version`, `author`, `license`, `metadata.hermes.tags`, and `metadata.hermes.related_skills`;
- target size roughly 8–15k chars with larger material moved to `references/`;
- categories under `skills/<category>/<name>/SKILL.md` rather than arbitrary upstream folders;
- verification by validator/read-back and, for in-repo skills, committed source changes.

Scientific Agent Skills use the same core `SKILL.md` idiom, but their frontmatter and graph metadata are weaker for Hermes:

| Surface | Scientific Agent Skills | Hermes gap |
|---|---|---|
| Metadata | `metadata.skill-author`; sometimes `allowed-tools`, `license` | Need normalized `version/author/license` and `metadata.hermes.tags/related_skills` for graph routing. |
| Size / scope | Some skills are giant domain manuals; `database-lookup` covers 78 APIs in one skill | Hermes should split high-recall references into progressive `references/` and keep trigger text short. |
| Safety | Upstream scan report lists many critical/high findings | Hermes cannot import or enable blindly; every candidate needs threat classification and sandbox/no-exec review. |
| Tools | Upstream examples assume generic Bash/file/web capability and local Python packages | Hermes needs explicit allowed surfaces, secret/API-key boundaries, no auto-install default, and proof evidence. |
| Evidence | Strong domain instructions often ask for raw JSON/results/endpoints | Good pattern to absorb: require provenance, endpoint list, no-result disclosure, and raw-response artifact links. |
| BYOK workspace | K-Dense BYOK bundles models, file handling, database access, optional Modal | Hermes can absorb the local workspace/BYOK pattern as governance, not as a dependency or runtime replacement. |

## Absorbable patterns

1. **Domain-pack inventory before skill import**
   - Maintain an inventory row per external skill: name, domain, upstream path, license, severity, external APIs, local packages, secrets required, cloud dependency, Hermes target category, decision.
   - The upstream `SECURITY.md` scan schema is a useful seed, but Hermes should preserve its own review state rather than trusting upstream safe flags.

2. **Evidence-first scientific API skills**
   - Adopt the `database-lookup` convention for Hermes research/data skills: list endpoints queried, preserve raw JSON or artifact path, state empty results, and cite the reference file used.
   - This fits Hermes proof gates better than generic “I searched” summaries.

3. **BYOK/local workspace contract**
   - Useful as a policy template: user keys stay user-owned, local files remain local, optional cloud compute must be explicit, and heavy workflows must expose cost/provider choices.
   - Hermes should map this to provider/quota governance and taskpool evidence, not install K-Dense BYOK automatically.

4. **Skill scan report as a first-class artifact**
   - K-Dense ships `scan_skills.py` and a generated `SECURITY.md`; Hermes should similarly require external skill bundles to provide or undergo a scanner pass before any activation.
   - Absorb the report columns: severity, findings, safe flag, duration, remediation.

5. **References/scripts/assets layout**
   - Many upstream skills include `references/`, `scripts/`, and `assets/`; this aligns with Hermes progressive loading if scripts are reviewed and execution-gated.
   - Recommended Hermes convention: imported/adapted domain skills may include `references/upstream.md`, `references/api-endpoints.md`, and `templates/evidence.md`, while scripts remain disabled until reviewed.

## Reject / quarantine list

Do not directly import or execute these classes during intake:

- Any upstream skill marked critical/high in `SECURITY.md`, especially generation-heavy research/communication skills such as literature review, grant writing, clinical reports, treatment plans, slides/posters, infographics, and broad “autoskill” behavior.
- Skills with hidden or broad tool execution (`allowed-tools: Bash` plus scripts) until scripts and package side effects are inspected.
- Clinical/medical decision skills as operational advice without Hermes medical safety framing and user-facing disclaimers.
- Skills requiring secrets, authenticated lab/clinical platforms, or paid databases unless they declare secret names and no-exfiltration rules.
- BYOK/Modal/cloud compute paths unless the user explicitly authorizes provider/cost and data boundary.

## Recommended Hermes integration plan

No-code conclusion for this tranche: do not import third-party skills yet. The safe next mainline is an inventory + adapter template, then one low-risk exemplar.

1. Add a local inventory artifact for Scientific Agent Skills candidates instead of copying files:
   - `upstream_name`, `upstream_path`, `domain`, `license`, `security_severity`, `safe_flag`, `external_services`, `secrets`, `python_packages`, `cloud_optional`, `hermes_category`, `decision`, `decision_reason`.
2. Prototype a single low-risk mapping from `database-lookup` into a Hermes reference pattern, not a runnable skill:
   - Evidence template: raw JSON artifact, endpoint list, databases queried, query parameters, empty-result note, citation/provenance.
   - Keep actual API calls user-triggered and task-specific.
3. Extend the Hermes skill authoring guide or a future `external-skill-to-brain-overlay` reference with external-bundle requirements:
   - upstream commit and license,
   - scanner result,
   - frontmatter normalization,
   - tool/secret/cloud boundary,
   - import decision matrix,
   - read-back verification.
4. If later converting a scientific skill into Hermes, prefer a new `research/` or `data-science/` skill only after peer survey and trigger evals; otherwise patch existing research/data workflow skills with a reference.

## Anti-drift checks

- No third-party code was installed or executed.
- No upstream `scientific-skills/*` files were copied into Hermes.
- The repo was inspected via GitHub API/raw files and `git ls-remote`; only this private analysis artifact was written.
- Upstream MIT license permits reuse, but license compatibility is not sufficient for activation because the security report contains critical/high findings.
- The task should move to review only as an analysis/gap-mapping tranche; product delivery would require a separate implementation task with tests/evals.

## Acceptance evidence delivered

- Repo structure and live metadata fixed.
- License/security boundary recorded.
- Scientific Agent Skills frontmatter/body patterns compared against Hermes `SKILL.md` authoring rules.
- Absorbable template fields and evidence conventions listed.
- Reject/quarantine list documented.
- Example decision provided: no-code import now; future low-risk exemplar should be a `database-lookup` evidence template, not direct activation.
