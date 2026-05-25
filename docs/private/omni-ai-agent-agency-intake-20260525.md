# omni-ai-agent/agency → Hermes taskpool intake

Date: 2026-05-25
Requested source: https://github.com/omni-ai-agent/agency
Scope: governed external-repo intake for Hermes taskpool.

## Live source status

The exact requested GitHub URL is currently not publicly resolvable from this machine:

- `https://api.github.com/repos/omni-ai-agent/agency` → HTTP 404
- `https://github.com/omni-ai-agent/agency` → HTTP 404
- `https://api.github.com/orgs/omni-ai-agent` → HTTP 404
- `https://api.github.com/users/omni-ai-agent` → HTTP 404

GitHub repository search was also checked:

- query `omni-ai-agent agency` → `0` repository results
- query `omni ai agent agency` returned unrelated low-signal repositories, not the requested owner/repo.

Taskpool duplicate check:

- no existing exact task for `omni-ai-agent`
- no existing exact task for `github.com/omni-ai-agent/agency`
- broad `agency` matched one unrelated OpenSwarm task only.

## Classification decision

Because the live source cannot be inspected, this must not be treated as a normal repo absorption task yet. The safe intake is a source-resolution / provenance-gated task:

- Priority: `P3` until the canonical repository is available or the user provides a corrected URL.
- Lane: `advisory` rather than `evolution`, because there is no inspectable architecture yet.
- Project: `hermes-agent`.
- Architecture target: `local-brain`, only as a tentative destination if the repo is an AI-agent/agency orchestration framework.
- Requires multimodel: `false` at intake time; there is no source evidence to certify. If the canonical repo becomes available and touches Hermes runtime/taskpool/permission/evidence lifecycle, upgrade to multimodel-gated `P2/evolution` before implementation.

## Anti-drift / red lines

- Do not infer architecture from the repository name alone.
- Do not substitute unrelated search results for the requested owner/repo.
- Do not clone, install, or run any third-party package until the canonical source is verified.
- Do not create implementation/prototype tasks from a 404 URL.
- Do not mark this as Graphiti/Agency integration; it is only a provenance-resolution task until live source evidence exists.

## Acceptance evidence for the taskpool item

The task is complete only when one of these outcomes is recorded:

1. Canonical repo found and inspected:
   - live URL resolves;
   - repo metadata, license, top-level structure, README/docs/package manifests are recorded;
   - Hermes gap mapping and accept/reject/watch decision is produced;
   - task is reclassified to P2/evolution if it is a high-signal agent orchestration/control-plane framework.
2. Source remains unavailable:
   - exact URL and search evidence are recorded;
   - task is closed/rejected as unavailable or awaits user-provided corrected URL.

## Next worker instruction

Start by re-checking the exact URL and GitHub search. If still 404, do not guess. Ask for or wait for a corrected canonical repository URL before any absorption analysis.

## Recheck proof log — 2026-05-26T00:53:51+08:00

Worker task: `2026-05-25-resolve-source-for-omni-ai-agent-agency-before-hermes-absorption-analysis`
Workspace: `/tmp/agent-team-2026-05-25-r`

Project preflight:

- Read project profile `/Users/charles/.hermes/project-profiles/hermes.json`.
- Read repo instructions `/Users/charles/.hermes/hermes-agent/AGENTS.md`.
- No Workpad boot/handoff path is declared in the project profile; no Workpad file was resolved, so this recheck is limited to URL/source-resolution evidence only.

Commands/checks performed:

- `python3 /Users/charles/.hermes/scripts/brain_task_orchestrator.py list --json` → duplicate/source-overlap search found 1 matching active task: this task only, status `running`, priority `P3`, project `hermes-agent`, lane `advisory`.
- `curl -sS -L -D headers_repos_omni-ai-agent_agency.txt -o body_repos_omni-ai-agent_agency.json https://api.github.com/repos/omni-ai-agent/agency` → final status `HTTP/2 404`, message `Not Found`, documentation URL `https://docs.github.com/rest/repos/repos#get-a-repository`.
- `curl -sS -L -D headers_orgs_omni-ai-agent.txt -o body_orgs_omni-ai-agent.json https://api.github.com/orgs/omni-ai-agent` → final status `HTTP/2 404`, message `Not Found`, documentation URL `https://docs.github.com/rest/orgs/orgs#get-an-organization`.
- `curl -sS -L -D headers_users_omni-ai-agent.txt -o body_users_omni-ai-agent.json https://api.github.com/users/omni-ai-agent` → final status `HTTP/2 404`, message `Not Found`.
- `curl -sS -L -I https://github.com/omni-ai-agent/agency` → final status `HTTP/2 404`, server `github.com`.
- GitHub repository search API query `"omni-ai-agent" agency in:name,description,readme` with `per_page=10` → `total_count: 0`, `incomplete_results: false`, no items.
- GitHub repository search API query `"omni-ai-agent/agency" OR "omni ai agent agency"` with `per_page=10` → `total_count: 0`, `incomplete_results: false`, no items.

Decision:

- The exact requested URL remains unavailable.
- The requested owner is not visible as a GitHub org or user from the API checks above.
- Search did not identify a corrected canonical repository.
- No unrelated search result was substituted.
- No clone/install/third-party execution was performed.
- Because no canonical source is inspectable, metadata/README/source-structure inspection and Hermes absorption analysis remain blocked until a corrected URL is provided.

Closeout recommendation:

- Treat the task as source-unavailable / awaiting corrected canonical URL.
- Do not promote to an absorption/gap-analysis task without a live inspectable repository.
