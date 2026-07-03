# UIMax Acceptance Report — Hermes Workbench

Date: 2026-07-03
Target: Hermes dashboard `/workbench`
Commit under validation: `67436bb28` plus amended responsive/a11y target-size fix in working tree before final amend.

## Scope

Validate the newly added graph-first Intelligence Workbench surface inspired by the Douyin reference: dark teal intelligence canvas, knowledge graph, selected-node details, risk boundaries, and agent command dock.

This report validates UI projection only. It does **not** certify runtime executor, deploy, publish, credential, destructive, or external adapter authority.

## Evidence artifacts

- Desktop screenshot: `/Users/charles/.hermes/docs/private/workbench-uimax-20260703/desktop-1440-after.png`
- Narrow headless screenshot: `/Users/charles/.hermes/docs/private/workbench-uimax-20260703/mobile-390-after.png`
- Earlier baseline screenshots: `desktop-1440.png`, `mobile-390.png`
- Browser route smoke: `http://127.0.0.1:5180/workbench`

## Verification commands

```bash
npm run build --workspace web
npx eslint src/pages/WorkbenchPage.tsx src/App.tsx
```

Build result: PASS. Vite reports only the pre-existing large chunk warning.

Targeted ESLint result: PASS for `WorkbenchPage.tsx` and `App.tsx`.

Full workspace lint was also attempted and fails on pre-existing repository lint debt unrelated to this change, including `LanguageSwitcher.tsx`, `OAuthProvidersCard.tsx`, `ProfileProvider.tsx`, `ConfigPage.tsx`, `SessionsPage.tsx`, and i18n escape warnings.

## Browser/DOM acceptance

Desktop browser route smoke via browser harness:

```json
{
  "path": "/workbench",
  "title": "Hermes Agent - Dashboard",
  "h1": "Workbench",
  "nodeCount": 8,
  "horizontalOverflow": false,
  "bodyTextIncludesBoundary": true
}
```

Graph interaction after target-size fix:

```json
{
  "active": "Tool Authority",
  "selectedPanel": "Tool Authority | Critical concerns",
  "horizontalOverflow": false,
  "targetMetrics": [
    { "label": "Inspect Hermes Control Plane", "w": 44, "h": 44 },
    { "label": "Inspect Session Evidence", "w": 44, "h": 44 },
    { "label": "Inspect Tool Authority", "w": 44, "h": 44 },
    { "label": "Inspect Skill Memory", "w": 44, "h": 44 },
    { "label": "Inspect Profile Scope", "w": 44, "h": 44 },
    { "label": "Inspect High-risk Gates", "w": 44, "h": 44 },
    { "label": "Inspect Autonomy Scheduler", "w": 44, "h": 44 },
    { "label": "Inspect Edge Capability", "w": 44, "h": 44 }
  ]
}
```

Console health after interaction: `console_messages=[]`, `js_errors=[]`.

Narrow viewport CDP metric probe:

```json
{
  "vw": 390,
  "clientWidth": 390,
  "docScrollWidth": 390,
  "horizontalOverflow": false
}
```

## Fixes made during acceptance

1. **P0 accessibility/interaction fix:** graph node buttons originally had `width: 0; height: 0`, so browser click target calculation failed. Fixed to `44px × 44px`, preserving visual dot/pulse styling.
2. **Responsive hardening:** added `max-width: 100%`, `overflow-x: hidden`, and shell-local `min-width: 0` containment to prevent page-level horizontal overflow.
3. **Narrow-title hardening:** added `break-words` and `overflow-wrap:anywhere` to the hero title.
4. **Mobile graph label hardening:** constrained node label max width and reduced label size at `max-width: 640px`.

## UIMax scorecard

| Dimension | Grade | Evidence / rationale |
|---|---:|---|
| Visual Design | A- | Desktop screenshot achieves dark teal intelligence/knowledge-graph feel; hierarchy is strong; graph, selected-node panel, risk panel, and command dock read as one system. Minor improvement possible in mobile graph density. |
| Responsiveness | B+ | DOM/CDP proves no horizontal overflow at 390px after fix. Narrow screenshot generated through Chrome `--window-size` is visually constrained by headless/min-layout behavior, so final mobile grade is not A/A+ without a true device/simulator pixel capture. |
| Accessibility | A- | Semantic h1/h2/h3, `role="img"`, named graph buttons, `aria-pressed`, labelled command input, 44px graph targets, reduced-motion rule. Could add richer keyboard roving/focus order later. |
| Code Quality | A- | New styling is scoped under `.workbench-shell`, theme-token based, no new lint issues in touched TSX. Some static color tokens are intentional art-direction accents. |
| Interaction | A- | Node click updates selected panel and pressed state; command dock buttons are reachable; console stays clean. Command `Project` is deliberately projection-only. |
| Console Health | A | Browser console after route + interaction has zero JS errors/messages. |

Overall scoped grade: **A- for desktop/operator Workbench**, **B+ for narrow/mobile evidence**.

## Non-goals / boundaries

- This is a UI projection surface; it does not open execution authority.
- It does not bind live API graph data yet; current nodes are static design/IA placeholders.
- A full A+/uIMax mobile claim still requires real mobile/simulator pixel capture, not just headless metric proof.

## Recommended next safe slice

Bind the graph to read-only live dashboard status sources: sessions count, enabled tools/toolsets, skills count, cron job count, profile scope, and high-risk gate flags. Keep it read-only and preserve the projection-only boundary until live evidence/readback is implemented.
