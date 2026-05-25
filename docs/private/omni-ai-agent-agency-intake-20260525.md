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
