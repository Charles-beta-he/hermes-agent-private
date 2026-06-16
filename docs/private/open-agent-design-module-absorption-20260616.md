# Open Agent Design Module Absorption — Antigravity / Kiro / Superpowers / OpenSpec / Google

Date: 2026-06-16

## Objective

Expand the Hermes open-source / public-source learning backlog from task-derived capability records into an explicit module-candidate inventory grounded in public sources.

Scope is projection/candidate only:

- no external code execution;
- no third-party install;
- no default permission expansion;
- no task lifecycle mutation;
- no production/browser/cloud side effects.

## Source set

Reviewed public sources:

- Google Antigravity: `https://antigravity.google/`
- Antigravity permissions/sandbox docs: `https://antigravity.google/docs/agent-permissions`, `https://antigravity.google/docs/sandbox-mode`
- Google Secure AI Agents: `https://research.google/pubs/an-introduction-to-googles-approach-for-secure-ai-agents/`
- Google Cloud agentic AI design patterns: `https://docs.cloud.google.com/architecture/choose-design-pattern-agentic-ai-system`
- Google ADK multi-agent patterns: `https://developers.googleblog.com/developers-guide-to-multi-agent-patterns-in-adk/`
- Google AI Principles: `https://ai.google/responsibility/principles/`
- Google PAIR Guidebook: `https://pair.withgoogle.com/guidebook/`
- AWS/Kiro specs/hooks/steering: `https://kiro.dev/docs/specs/`, `https://kiro.dev/docs/hooks/`, `https://kiro.dev/docs/steering/`
- Fission-AI OpenSpec: `https://github.com/Fission-AI/OpenSpec`, `https://openspec.dev/`
- GitHub Spec Kit: `https://github.com/github/spec-kit`
- Superpowers: `https://github.com/obra/superpowers`

## Absorbed module candidates

Supplemental registry file:

```text
/Users/charles/.hermes/hermes-agent/docs/private/capability-absorption-registry/supplemental/open-agent-design-module-candidates-20260616.json
```

It adds 31 candidate modules:

### Antigravity-derived

- Agent Manager
- Artifact + verification bundle
- Browser-in-the-loop validation
- Visual artifact feedback loop
- Unified permission model
- Sandboxed terminal executor
- Scheduled agent message workflows

### Kiro-derived

- Requirements/design/tasks triplet
- Bug-analysis spec artifact
- Steering hierarchy and inclusion modes
- AGENTS.md compatibility bridge
- Agent hooks governance
- Hook risk policy matrix

### OpenSpec / Spec Kit-derived

- Proposal/spec delta
- Capability-organized spec store
- OPSX action workflow
- Project config injection
- Constitution gate
- Executable spec pipeline

### Superpowers-derived

- Mandatory skill routing gate
- Brainstorming/design approval gate
- RED-GREEN-REFACTOR enforcement
- Systematic root-cause debugging
- Fresh subagent per task with two-stage review
- Completion evidence gate

### Google principles-derived

- Human controller invariant
- Limited powers invariant
- Plan/action observability invariant
- Context engineering layer
- Parallel fan-out/gather pattern
- Explain-for-understanding progress UI

## Code change

Registry builder now supports supplemental candidate files:

```text
scripts/capability_absorption_registry.py
```

New behavior:

- reads taskpool markdown as before;
- additionally reads JSON candidate packs under:

```text
docs/private/capability-absorption-registry/supplemental/*.json
```

- marks supplemental records with:

```json
{"supplemental": true}
```

- treats `candidate` as an open status.

## Safety boundary

Supplemental candidates are not done work and not authorization. They are explicit backlog/projection entries. Any candidate promoted to implementation still needs:

- source evidence pack;
- selected target surface;
- permission boundary;
- tests / dry-run / acceptance criteria;
- taskpool lifecycle evidence;
- human gate for external side effects, credentials, production, deployment, or destructive actions.

## Verification

Focused registry tests now cover supplemental loading:

```text
tests/scripts/test_capability_absorption_registry.py::test_registry_loads_supplemental_module_candidates
```

Full focused suite expected:

```bash
PYTEST_ADDOPTS='' ./venv/bin/python -m pytest tests/scripts/test_capability_absorption_registry.py -q -o addopts=''
```

## Decision

Recommended next module-prioritization order:

1. Artifact + verification bundle
2. Unified permission model
3. Kiro spec triplet
4. Completion evidence gate
5. Context engineering layer
6. Agent hooks risk policy
7. Browser-in-the-loop validation
8. Fresh subagent per task with two-stage review

Rationale: these directly strengthen existing Hermes taskpool / skill / browser / verification surfaces without requiring third-party runtime adoption.
