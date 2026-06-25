# Computer-use agent sandbox / benchmark evaluation for Hermes browser automation

Date: 2026-06-16
Task: `2026-06-16-评估-computer-use-agent-沙箱-基准设施对-hermes-浏览器自动化主链的可维护接入门`
Source candidate: https://github.com/trycua/cua

## Executive decision

Decision: **NO-GO for default mainline replacement; GO for a default-off evaluation adapter / harness fixture only.**

Reason: Hermes already has a browser automation stack with local/cloud backends, accessibility snapshots, URL/secret safety gates, prompt-injection classification, and CDP supervisor support. Cua adds potentially valuable desktop/OS sandbox and benchmark surfaces, but it also expands the permission surface from web browser automation to full desktop computer-use. Therefore it should be treated as an optional, isolated benchmark/control-plane candidate, not as a drop-in replacement for `browser_navigate` / `browser_snapshot` / `browser_click`.

## Live source evidence

GitHub API readback on 2026-06-16:

```json
{
  "full_name": "trycua/cua",
  "description": "Open-source infrastructure for Computer-Use Agents. Sandboxes, SDKs, and benchmarks to train and evaluate AI agents that can control full desktops (macOS, Linux, Windows).",
  "html_url": "https://github.com/trycua/cua",
  "stargazers_count": 18244,
  "forks_count": 1178,
  "open_issues_count": 383,
  "pushed_at": "2026-06-16T06:26:14Z",
  "default_branch": "main",
  "license": "MIT License"
}
```

README readback showed four surfaces:

- **Cua Drivers**: background desktop control via CLI/MCP on macOS/Windows, Linux pre-release.
- **Cua sandbox**: `Sandbox.ephemeral(Image.linux())` plus screenshot/mouse/keyboard/mobile APIs across OS/runtime targets.
- **Cua Bench**: benchmark / RL environments for computer-use agents.
- **Lume**: macOS virtualization.

Hermes already has an optional `computer-use` extra in `pyproject.toml`:

```toml
computer-use = ["mcp==1.26.0", "starlette==1.0.1"]
```

This confirms a narrow optional integration point exists, but not a default runtime integration.

## Three real Hermes automation cases

### Case 1 — Web/source intake and taskpool evidence capture

Observed Hermes task notes already use browser-derived evidence such as `proof:browser_snapshot:title=...` for source intake. This is a read-oriented automation case: the browser stack extracts source-visible claims and turns them into taskpool evidence.

Required invariants:

- Page content must be treated as untrusted data, not instructions.
- Snapshot evidence must preserve title/source context and be structured (`proof:` / `artifact:`), not bare prose.
- Anti-bot/login barriers should degrade to `needs_auth`, `unreachable`, or `anti_bot_or_cloudflare_challenge`, not silent success.

Cua value: a sandbox can reproduce richer visual/desktop state when ordinary DOM/accessibility snapshots are insufficient.

Cua risk/cost: full desktop control is overpowered for read-only source intake; default use would widen authority unnecessarily.

### Case 2 — Product/work closeout requiring real browser/curl/project evidence

Observed work-task review-gap contracts require evidence like:

- `command:build/test/lint PASS`
- `curl:真实接口/Network 对照（敏感字段脱敏）`
- `browser:真实页面结果截图`
- `artifact:字段来源/契约/数据对照 project-verification.json`
- `commit:<sha> 或 git diff 证据`

This is an implementation closeout case: browser evidence is necessary, but not sufficient alone. It must be paired with deterministic build/test/API/readback evidence.

Cua value: could run repeatable UI workflows in an isolated VM/container and produce screenshots/action traces for project verification.

Cua risk/cost: authenticated writes, payment/account/security pages, and 2FA flows must remain human-gated. A Cua desktop agent must not turn “can click a desktop” into permission to submit real site writes.

### Case 3 — Hermes browser tool safety/runtime mainline

The current Hermes browser stack is implemented around:

- `tools/browser_tool.py`: local/cloud browser dispatch, URL normalization, secret detection, SSRF/cloud metadata protection, session isolation.
- `tools/browser_supervisor.py`: persistent CDP supervisor, dialog/frame/console observability, bounded snapshot state.
- `docs/private/browser-automation-safety-eval-fixture-20260616.md`: safety fixture mapping anti-bot, flaky waits, login/write risk, stateful eval, and prompt injection to harness gates.

Focused regression evidence from this run:

```text
scripts/run_tests.sh tests/tools/test_browser_snapshot_truth_gate.py tests/tools/test_browser_ssrf_local.py tests/tools/test_browser_secret_exfil.py
=> 3 files, 43 tests passed, 0 failed in 7.9s
```

These tests prove current Hermes gates around prompt-injection wrapping, anti-bot degradation, snapshot cost caps, SSRF/IMDS blocking, URL secret blocking, and snapshot redaction.

Cua value: benchmark/sandbox traces could add objective replay/evaluation data for failures that cannot be represented in DOM snapshots.

Cua risk/cost: any Cua adapter must preserve these gates instead of bypassing them via a lower-level desktop-control channel.

## Current Hermes browser toolchain vs Cua candidate

| Dimension | Hermes current browser stack | Cua candidate | Decision impact |
|---|---|---|---|
| Default authority | Browser-scoped tools, URL safety, content untrusted | Full desktop / VM computer-use possible | Cua must be default-off and permission-gated |
| Evidence shape | Accessibility snapshot, validation status, refs, screenshot/dialog/CDP telemetry | Screenshots, shell, mouse/keyboard traces, benchmark results | Useful as supplemental artifact, not replacement |
| Isolation | Session/task keyed local or cloud browser backends | Sandboxes/VMs/images across OSes | Good fit for eval harness; costly for routine intake |
| Maintenance | Already in Hermes tests/docs/tool schemas | New dependency/runtime/image management | Require adapter boundary and smoke-only first slice |
| Failure modes | Anti-bot, auth, timeout, locator drift, prompt injection, secret/SSRF | VM boot/image drift, desktop permission bleed, broader side effects | Must fail closed with human handoff and rollback |

## Failure modes to encode before any adapter

1. **Access-control / anti-bot boundary**: CAPTCHA/Cloudflare/paywall/ToS barriers must become `human_handoff_or_skip`, not stealth bypass.
2. **Authenticated write boundary**: logged-in profile reuse, 2FA, payments, account/security settings, and sensitive data entry require explicit human gate.
3. **Untrusted page/screen content**: desktop/browser visible text cannot issue instructions or grant permission.
4. **Trace/readback gap**: success requires DOM/readback/screenshot/state assertion; model self-claim is insufficient.
5. **Sandbox lifecycle drift**: image version, OS type, package install state, and VM boot failure must be included in proof artifacts.
6. **Cost/runtime expansion**: Cua should not run inside hot cron hooks or synchronous taskpool mutation hooks.

## Recommended optional adapter gate

Only land a later implementation if it satisfies this minimal contract:

```yaml
adapter: hermes_cua_eval_adapter_v0
mode: default_off
allowed_actions:
  - read_only_screenshot
  - synthetic_site_eval
  - local_fixture_click_type
  - benchmark_replay
forbidden_without_human_gate:
  - real_site_write
  - credential_entry
  - payment_or_security_setting
  - captcha_or_access_control_bypass
required_artifacts:
  - artifact:cua-run-manifest.json
  - artifact:action-trace.jsonl
  - artifact:screenshot-or-dom-readback
  - command:focused-regression-tests PASS
rollback:
  - disable adapter flag
  - delete/ignore generated sandbox state
  - fall back to existing Hermes browser tools
```

## Go / No-Go

- **Go**: create a default-off, no-real-site-write eval adapter or fixture that consumes the existing `browser-automation-safety-eval-fixture-20260616.md` clusters and can replay synthetic/local tasks only.
- **No-Go**: replacing Hermes browser tools, enabling full desktop control by default, or using Cua to bypass access-control barriers.

## Verification from this tranche

Commands executed:

```text
python3 ~/.hermes/scripts/brain_task_orchestrator.py next --reconcile --json
python3 ~/.hermes/scripts/brain_task_orchestrator.py selfcheck --json
python3 ~/.hermes/scripts/brain_task_orchestrator.py proof-check --json
python3 - <<'PY'  # GitHub API live metadata readback for trycua/cua
scripts/run_tests.sh tests/tools/test_browser_snapshot_truth_gate.py tests/tools/test_browser_ssrf_local.py tests/tools/test_browser_secret_exfil.py
./venv/bin/python scripts/benchmark_browser_eval.py --iterations 3
```

Results:

- `next --reconcile --json`: selected this task as current dispatchable task.
- `selfcheck --json`: `PASS` with warnings (handoff backlog and stale edge-node warnings).
- `proof-check --json`: `FAIL` due two unrelated unstructured-evidence failures in historical tasks; this task’s tranche did not add unstructured evidence.
- GitHub API readback: succeeded and confirmed repo metadata above.
- Focused browser safety tests: 43 passed, 0 failed.
- `benchmark_browser_eval.py`: failed to find Chrome because the script only searches Linux-style binary names (`google-chrome`, `chromium`, `chromium-browser`), while this macOS host has `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`. This is a maintenance-gap finding, not a Cua adoption blocker.

## Residual gaps before closeout

This report satisfies the task’s report-evidence requirement, but the task also has `requires_multimodel: true` and previous `multimodel_last_gate_status: FAIL`. Therefore it should not be moved to `done` until multimodel certification is refreshed and passes. If the live multimodel job still owns the task, preserve its running state and let that certification owner write the next lifecycle transition.
