# UIMax A+ Acceptance Report — Hermes Workbench

Date: 2026-07-03
Target: Hermes dashboard `/workbench`
Commit under validation: current scoped commit after A+ amendment.

## Scope

Validate the graph-first **Intelligence Workbench** surface inspired by the Douyin reference: dark teal intelligence canvas, knowledge graph, selected-node details, risk boundary panel, and agent command dock.

This report certifies the **web dashboard UI surface** only. The UI remains projection-only and does not open runtime executor, deploy, publish, credential, destructive, broad-fanout, or external-adapter authority.

## Evidence artifacts

Committed report path:

- `docs/private/workbench-uimax-20260703/UIMAX_REPORT.md`

Screenshot artifacts:

- `docs/private/workbench-uimax-20260703/desktop-1440-aplus.png`
- `docs/private/workbench-uimax-20260703/narrow-500-aplus.png`
- Prior baseline/reference screenshots kept in the same directory for comparison.

Live validation route:

- `http://127.0.0.1:5181/workbench`

## Verification commands

```bash
npm run build --workspace web
npx eslint src/pages/WorkbenchPage.tsx src/App.tsx
```

Results:

- Build: **PASS**. Vite emitted only the existing large-chunk warning.
- Targeted ESLint for touched TSX files: **PASS**.
- Full workspace lint is not used as this slice gate because it still fails on pre-existing unrelated lint debt outside this workbench change.

## Browser/DOM acceptance evidence

Desktop browser harness route and interaction:

```json
{
  "path": "/workbench",
  "h1": "Workbench",
  "groupRole": "group",
  "active": "Tool Authority",
  "selectedPanel": "Tool Authority | Critical concerns",
  "receiptRole": "status",
  "inputDescribedBy": "workbench-command-help workbench-receipt",
  "horizontalOverflow": false,
  "targetMetrics": [
    { "label": "Inspect Hermes Control Plane", "w": 44, "h": 44, "pressed": "false" },
    { "label": "Inspect Session Evidence", "w": 44, "h": 44, "pressed": "false" },
    { "label": "Inspect Tool Authority", "w": 44, "h": 44, "pressed": "true" },
    { "label": "Inspect Skill Memory", "w": 44, "h": 44, "pressed": "false" },
    { "label": "Inspect Profile Scope", "w": 44, "h": 44, "pressed": "false" },
    { "label": "Inspect High-risk Gates", "w": 44, "h": 44, "pressed": "false" },
    { "label": "Inspect Autonomy Scheduler", "w": 44, "h": 44, "pressed": "false" },
    { "label": "Inspect Edge Capability", "w": 44, "h": 44, "pressed": "false" }
  ]
}
```

Projected command receipt after clicking `Project`:

```text
Projected command for Tool Authority: "Map current evidence gates before action". No executor, adapter, deploy, or external write was invoked.
```

Console health after route + node interaction + command projection:

```json
{
  "console_messages": [],
  "js_errors": [],
  "total_messages": 0,
  "total_errors": 0
}
```

Narrow viewport metric probe after responsive hardening:

```json
{
  "vw": 390,
  "clientWidth": 390,
  "docScrollWidth": 390,
  "horizontalOverflow": false
}
```

## A+ hardening changes landed

1. **Graph targets are real touch/click targets**: each node button is `44px × 44px`, while the dot/pulse remains visual-only.
2. **Semantic graph container**: changed from interactive children inside `role="img"` to `role="group"` with an explicit keyboard hint.
3. **Keyboard support**: graph nodes support arrow/Home/End selection and keep native button Tab/Enter/Space behavior.
4. **Live local receipt**: selected node and command projection produce a nearby `role="status"` receipt; the command input references both help and receipt via `aria-describedby`.
5. **Projection boundary visible at action point**: command dock copy states no tool/adapter/deploy/external write is invoked.
6. **Responsive hardening**: workbench shell contains width/overflow, title wraps safely, graph labels are constrained and reduced on narrow screens.
7. **Motion/performance gate**: looping pulse uses compositor-friendly transform/opacity and has `prefers-reduced-motion` fallback; no layout-property animation was added.
8. **Typography polish**: hero heading uses `text-balance`; explanatory copy uses `text-pretty`.

## UIMax scorecard

| Dimension | Grade | Evidence / rationale |
|---|---:|---|
| Visual Design | A+ | Desktop screenshot achieves the requested dark teal AI intelligence / knowledge-graph control-room look. Layout hierarchy is clear: hero → graph → metrics → detail/risk panels → command dock. |
| Responsiveness | A+ | Narrow screenshot at 500px is usable and readable; 390px metric probe proves no horizontal overflow. Title and labels wrap/contain safely. |
| Accessibility | A+ | Native buttons, accessible names, `aria-pressed`, labelled command input, `role="group"`, `role="status"`, `aria-live`, `aria-describedby`, 44px targets, visible focus, reduced-motion support. |
| Code Quality | A+ | Scoped workbench CSS, no target-file lint errors, theme tokens where appropriate, no unrelated file edits in this slice. |
| Interaction | A+ | Node click updates selected panel + pressed state; keyboard selection is supported; command presets update copy; `Project` produces a local receipt without side effects. |
| Console Health | A+ | Browser console after route + interaction + projection is clean: 0 JS errors/messages. |

Overall scoped grade: **A+ for the `/workbench` web dashboard UI surface**.

## Boundary statement

A+ here means the **visual/interaction/accessibility/responsive quality gate for this web surface** is complete. It does not certify any backend live-data binding or execution authority. The next safe product slice is read-only live status binding for the graph nodes while keeping the command dock projection-only until separately authorized.
