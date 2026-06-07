# book-to-skill → Hermes Skill migration matrix — 2026-06-08

## Source inspected

- Repository: `virgiliojr94/book-to-skill`
- URL: https://github.com/virgiliojr94/book-to-skill
- Description: “Turn any technical book PDF into a Claude Code skill — ready to study, reference, and use while you work.”
- License: MIT
- Default branch: `master`
- Stars/forks at inspection: 4574 / 566
- Updated at inspection: `2026-06-07T19:06:30Z`
- Root files observed: `.github`, `.gitignore`, `LICENSE.md`, `README.md`, `SKILL.md`, `scripts`, `tests`, `tools`.

Commands used:

```text
gh repo view virgiliojr94/book-to-skill --json nameWithOwner,description,stargazerCount,forkCount,licenseInfo,defaultBranchRef,updatedAt,url
gh api repos/virgiliojr94/book-to-skill/contents --jq '[.[].name]'
gh api repos/virgiliojr94/book-to-skill/readme --jq .content | base64 -d
```

No third-party code was installed or executed.

## README claims relevant to Hermes

The README describes a pipeline that turns technical books/document folders into a Claude Code skill with:

- `SKILL.md` as core mental models + chapter index.
- `chapters/ch01-*.md` loaded on demand.
- `glossary.md` with term refs.
- `patterns.md` with techniques/design patterns.
- `cheatsheet.md` with quick-reference rules.
- Supported inputs include PDF, EPUB, DOCX, TXT, Markdown, reStructuredText, AsciiDoc, HTML, RTF, MOBI/AZW/AZW3.

## Class-level migration matrix

- Source ingestion:
  - Upstream pattern: many document formats.
  - Hermes decision: keep format conversion as an optional pre-step; do not make Hermes skills responsible for proprietary/copyright ingestion.
  - Boundary: local-only, user-provided documents only; no cloud upload.

- Skill skeleton:
  - Upstream pattern: generated `SKILL.md` plus topic files.
  - Hermes decision: map to Hermes skill package: `SKILL.md` + `references/chapters/` + `references/glossary.md` + `references/patterns.md` + `references/cheatsheet.md`.
  - Boundary: frontmatter must include trigger conditions, when-not-to-use, linked file guidance.

- On-demand loading:
  - Upstream pattern: chapter files loaded when relevant.
  - Hermes decision: preserve `skill_view(name, file_path=...)` as the loading mechanism; no full-book injection into system prompt.
  - Boundary: chapter summaries must cite source/chapter; model output remains subordinate to instructions.

- Provenance:
  - Upstream pattern: answers from actual content.
  - Hermes decision: every generated chunk carries document title, chapter/page/section, extraction timestamp, and hash where feasible.
  - Boundary: no uncited “AI summary” is promoted as skill authority.

- Quality gate:
  - Upstream pattern: generates useful study/reference skill.
  - Hermes decision: add deterministic validation: package tree exists, linked files load, sample query routes to the right chapter, copyright/privacy warning present.
  - Boundary: no skill creation without human review for copyrighted/private books.

## Minimal Hermes implementation slice

Create a default-off local script/prototype later, not in this intake task:

1. Accept an already-converted local Markdown folder.
2. Generate a Hermes skill directory in a temporary output path.
3. Produce `SKILL.md`, `references/chapters/`, `references/glossary.md`, `references/patterns.md`, `references/cheatsheet.md`.
4. Run a validation script that checks frontmatter, linked files, citation markers, and sample `skill_view` loadability.
5. Require explicit user confirmation before copying into active `~/.hermes/skills/`.

## Anti-drift / legal boundary

- Do not feed entire copyrighted books to external LLMs.
- Do not place private or licensed book text into persistent Hermes memory.
- Do not auto-install upstream dependencies.
- Do not directly import upstream generated Claude skill format without adapting to Hermes skill conventions.
- Do not create many narrow book skills without user intent and provenance.

## Decision

This task is advisory-intake complete: source evidence, Hermes mapping, risk boundaries, and minimal implementation slice are now recorded. Any code implementation should be a separate proof-gated task.
