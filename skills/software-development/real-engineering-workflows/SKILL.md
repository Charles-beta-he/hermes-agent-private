---
name: real-engineering-workflows
description: "Use when importing or applying Matt Pocock-style real-engineering agent skills: plan grilling, domain-language alignment, TDD/tracer bullets, vertical issue slicing, diagnosis loops, architecture deepening, prototype branches, concise handoff, or skill-package absorption. Do not use as a replacement for project-specific preflight, tests, or existing specialist skills."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [software-development, skills, planning, tdd, architecture, diagnosis]
    related_skills: [systematic-debugging, test-driven-development, incremental-implementation, api-and-interface-design, codebase-intelligence, documentation-and-adrs, planning-with-files, hermes-agent-skill-authoring]
---

# Real Engineering Workflows

## Overview

This skill absorbs the transferable parts of Matt Pocock's `mattpocock/skills` repository into Hermes conventions. The source package is valuable because it treats agent work as engineering discipline, not vibe coding: align before acting, share domain language, build tight feedback loops, slice vertically, and keep architecture depth visible.

Use this as a synthesis layer. It does not replace Hermes project preflight, TDD, debugging, ADR, or taskpool skills; it tells you which engineering posture to apply and what evidence to leave behind.

## When to Use

- The user links or mentions `mattpocock/skills`, "real engineering", "not vibe coding", "grill me", "caveman", "vertical slices", "tracer bullet", or "deep modules".
- You are turning a vague plan into a buildable, verifiable engineering workflow.
- You need to break a PRD/spec/plan into issues or taskpool items that are independently grabbable.
- You are reviewing architecture for shallow modules, bad seams, weak locality, or pass-through abstractions.
- You are debugging a hard issue and need a disciplined reproduce/minimise/instrument/fix loop.
- You need a compact handoff while preserving decisions, evidence, and next actions.

Don't use for:

- Simple one-shot Q&A where no engineering workflow is needed.
- Replacing project-specific preflight, AGENTS/CLAUDE/Workpad reading, real tests, or browser validation.
- Blindly copying Claude Code hook/plugin commands into Hermes.
- Narrow stack-specific source skills such as `migrate-to-shoehorn` unless the current project actually uses that stack.

## Source Boundary

The source repo was reviewed at:

- Repo: `https://github.com/mattpocock/skills`
- Commit: `0288510dd61ff6ef7c2003834082ab8f2387e80e`
- Hermes SSOT review: `docs/private/mattpocock-skills-reference-review-20260528.md`
- Detailed transfer map: `references/mattpocock-skills-map.md`

This skill is a synthesized Hermes workflow. It is not a verbatim vendoring of the source package.

## Operating Principles

### 1. Grill before implementation when alignment is load-bearing

For ambiguous or high-impact work, run a short but sharp interrogation before coding:

- What user-visible outcome are we optimizing for?
- Which existing domain terms, constraints, and red lines apply?
- Which module/interface/API boundaries will be touched?
- What examples would prove the implementation is correct?
- What would make this the wrong solution even if it passes tests?

If the answers crystallize durable project language or decisions, update the project's domain docs or ADRs instead of leaving them only in chat.

### 2. Domain language is infrastructure

Treat a project glossary / `CONTEXT.md` / Workpad vocabulary as a compression and correctness primitive:

- Prefer canonical domain terms in code, issue titles, PRDs, taskpool items, and final summaries.
- If a new concept is required to explain a design, add or propose the term explicitly.
- If the user rejects a term or approach for a durable reason, record that as negative knowledge so it does not get re-proposed.

### 3. Feedback-loop quality is the speed limit

Before fixing or building, identify the smallest feedback loop that can prove progress:

1. Reproduce the user-described symptom or target behavior.
2. Minimise scope until the loop is fast enough to run repeatedly.
3. Sharpen the signal: assert the specific behavior, not merely "does not crash".
4. Make it deterministic where possible: pin time, seed randomness, isolate filesystem/network, reuse known auth state only when allowed.
5. Fix one slice, rerun the loop, then add regression coverage.

If no loop is possible, state the missing observable and leave a fallback proof plan.

### 4. Vertical slices beat horizontal slices

When decomposing work, avoid layer-by-layer tickets such as "build schema", then "build API", then "build UI" unless a real dependency requires it. Prefer slices that cross the stack narrowly:

- one user story or operation;
- schema/API/UI/test changes needed for that story;
- independently demoable or verifiable acceptance criteria;
- explicit dependencies and blocked-by fields.

Hermes taskpool items should include order/dependency and evidence requirements, not just titles.

### 5. Use tracer bullets for uncertain builds

For uncertain features, first build the thinnest real path through the system:

- real data path over mocks when feasible;
- one representative happy path;
- one critical failure path;
- enough UI/API/test wiring to validate architecture;
- no broad generalization until the tracer bullet survives feedback.

### 6. Deepen modules, don't multiply pass-throughs

When reviewing architecture, use this vocabulary:

- **Module**: anything with an interface and implementation.
- **Interface**: everything callers must know, including invariants and error modes, not just type signatures.
- **Implementation**: the hidden work behind the interface.
- **Depth**: high leverage behind a small interface.
- **Locality**: bugs and changes concentrate in one place.
- **Seam**: a place behavior can change without editing callers.
- **Adapter**: a concrete implementation at a seam.
- **Deletion test**: if deleting the module removes complexity, it was probably a pass-through; if complexity reappears across callers, the module was earning its keep.

Rule of thumb: one adapter is often a hypothetical seam; two adapters make the seam real.

### 7. Prototype in the right branch

Pick prototype mode by the uncertainty:

- State/model/business-logic uncertainty → build a tiny runnable terminal or script prototype that pushes hard cases.
- UI/product uncertainty → build several meaningfully different variations behind a switch, route, or flag.

Prototype artifacts are disposable unless the user explicitly promotes them. Never silently ship prototype shortcuts into production code.

### 8. Concision is allowed, ambiguity is not

A compact "caveman" style is useful for status, handoff, and dense technical summaries. Keep nouns and evidence; drop filler. But if ambiguity changes the work, clarity overrides compression and you must ask or inspect.

## Taskpool / Issue Shape

When converting a plan into issues or Hermes taskpool items, include:

- title;
- objective;
- acceptance criteria;
- vertical slice boundary;
- blocked-by dependencies;
- AFK/autonomous vs HITL/manual classification;
- red lines / non-goals;
- verification command or observable proof;
- evidence refs to docs, code, screenshots, or source review notes.

## Handoff Shape

For a compact handoff, preserve:

- goal and success criteria;
- current state and exact artifacts changed;
- commands run and their results;
- remaining blockers / ambiguity;
- next safe action;
- evidence paths / PRs / task IDs;
- red lines that must not be violated.

## Common Pitfalls

1. Treating "grill" as endless questioning. Ask only questions that change the solution or prevent rework.
2. Creating horizontal implementation tasks that cannot be independently verified.
3. Writing tests around imagined internals before discovering the real interface.
4. Adding seams or adapters before there are two real variants that justify the abstraction.
5. Recording a domain decision only in chat; future agents will miss it.
6. Using terse mode to hide uncertainty or skipped validation.
7. Copying Claude-specific plugin/hook mechanics instead of translating the underlying workflow into Hermes tools.

## Verification Checklist

- [ ] Project-specific preflight and repo instructions were read when a concrete project is involved.
- [ ] The work is either clearly aligned or a grilling/alignment loop resolved the open branches.
- [ ] Domain terms and durable decisions were recorded in the appropriate project doc, ADR, skill, taskpool item, or SSOT.
- [ ] A fast feedback loop or explicit fallback proof plan exists before implementation claims.
- [ ] Work decomposition uses vertical, independently verifiable slices when applicable.
- [ ] Architecture recommendations use interface depth/locality/deletion-test evidence, not style preference.
- [ ] Final handoff includes artifacts, commands, residual risk, and next action.
