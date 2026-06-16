# trycua/cua Computer-Use Sandbox & Benchmark Evaluation for Hermes Browser Automation

Date: 2026-06-16

## Decision

**Go, but only as optional experimental benchmark/sandbox adapter. No-Go for default browser automation mainline replacement.**

Rationale:

- CUA has useful sandbox/benchmark concepts: isolated desktop envs, reset capability, task authoring, trace/benchmark harness.
- CUA runtime/install surface is heavy: Docker/QEMU/KVM/macOS Lume/cloud API, install scripts, VM/image supply chain, telemetry, optional local host control.
- Hermes already has a safer browser evidence chain and macOS background `computer_use`; CUA must not bypass those gates.

## Source evidence

### Candidate project

- Repo: https://github.com/trycua/cua
- GitHub/API description: `Open-source infrastructure for Computer-Use Agents. Sandboxes, SDKs, and benchmarks to train and evaluate AI agents that can control full desktops (macOS, Linux, Windows).`
- License observed: MIT.

### README / docs claims

Public docs describe:

- `Build, benchmark, and deploy agents that use computers`.
- One API for VM/container images, cloud or local.
- Desktop controls: screenshot, click, keyboard, shell API surfaces.
- CUA Bench: OSWorld, ScreenSpot, Windows Arena, custom tasks, trajectory export.
- Desktop sandbox claims: isolation, reset capability, safety, consistency, reproducibility.

### Maturity cautions

- Cua Bench docs list some benchmark adapters as `Coming soon`.
- Public registry/basic dataset is small compared with headline benchmark families.
- Windows Arena setup can require nested virtualization, Windows ISO, and 1–2 hours.
- Docs/package versions appear to drift in places.
- Open issue count is high; treat as active but not low-maintenance.

## Hermes current automation baseline

### Available now

Live runtime availability check reported:

```text
browser_requirements True
browser_vision_requirements True
browser_cdp_available False
browser_dialog_available False
computer_use_requirements True
```

### Existing browser chain

Hermes already has:

- `browser_navigate`
- `browser_snapshot`
- `browser_click`
- `browser_type`
- `browser_scroll`
- `browser_press`
- `browser_console`
- `browser_vision`
- Browser provider routing and local/private URL safety behavior.
- URL safety / SSRF tests.
- Untrusted browser content wrapping.
- Snapshot/readback truth gate.
- Secret-in-URL/exfiltration tests.

### Existing macOS background computer use

Hermes already has:

- `computer_use(action="capture", mode="som")`
- background macOS control without stealing cursor/focus;
- element-index targeting;
- hard-blocks for dangerous shortcuts/patterns;
- approval layer for mutating actions.

## Three real Hermes evaluation cases

### Case 1 — public dynamic read-only browser page

Example: GitHub Trending / public dynamic page.

Required evidence:

- navigation result;
- accessibility snapshot;
- optional console errors;
- optional screenshot/vision verification;
- no login / no form submit / no anti-bot bypass.

CUA value:

- Can compare trace replay and screenshot consistency against current browser tool.

CUA boundary:

- If CAPTCHA/Cloudflare/paywall appears, return degraded/human handoff, not bypass.

### Case 2 — synthetic local browser interaction fixture

Example: local form with JS validation/dialog/readback.

Required evidence:

- DOM/readback assertion;
- screenshot;
- action trace;
- console state;
- cleanup.

CUA value:

- Good fit for benchmark harness and reproducible synthetic tasks.

CUA boundary:

- Synthetic/local only; no real external writes or secrets.

### Case 3 — native desktop sandbox task

Example: Calculator-like deterministic task in isolated VM/container, not user host.

Required evidence:

- screenshot/readback;
- action trace;
- sandbox id/version/image digest;
- cleanup receipt.

CUA value:

- Useful only if the environment is actually sandboxed and reproducible.

CUA boundary:

- Do not use CUA Localhost/direct host control for Hermes default automation.

## Fail-closed gates

### P0 gates before any code integration

- Default off:

```yaml
browser:
  cua_sandbox:
    enabled: false
```

- Missing config/dependency/version/selfcheck -> disabled.
- No `Localhost.connect()`.
- No host desktop control by default.
- No `computer:all`, `computer:shell`, `computer:file`, `computer:clipboard` for ordinary browser tasks.
- Telemetry disabled by default:

```bash
CUA_TELEMETRY_ENABLED=false
```

- No real credentials/password/2FA/payment/account-setting flows.
- No CAPTCHA/Cloudflare/paywall/anti-bot bypass.
- No persistent sandbox by default.
- Every sandbox run has TTL, cleanup, and bounded artifact retention.
- Evidence must include action trace + screenshot/readback + validation status.

### Supply-chain gates

- No `curl | bash` in Hermes mainline setup.
- Pin CUA version/commit and image digest.
- Audit optional extras/license surface, especially AGPL-adjacent optional dependencies.
- Separate cloud vs local runtime gates.
- No automatic VM/image downloads without hash/source recording.

## Proposed integration shape

### Stage 0 — report-only / registry candidate

Current stage. No code execution, no CUA install.

### Stage 1 — schema-only benchmark fixture

Add a Hermes-local schema for:

```yaml
cua_benchmark_candidate_v1:
  provider: cua
  mode: synthetic_benchmark_only
  default_enabled: false
  prohibited_permissions:
    - localhost
    - shell
    - file
    - clipboard
    - all
  required_evidence:
    - action_trace
    - screenshot
    - readback_assertion
    - cleanup_receipt
```

No dependency on CUA package yet.

### Stage 2 — optional adapter dry-run

Only after Stage 1:

- version-pinned optional dependency;
- no host mode;
- no real website writes;
- synthetic local fixture only;
- telemetry off;
- cleanup/readback proof.

### Stage 3 — governed benchmark runner

Only if Stage 2 succeeds:

- dataset registry;
- image/version digest;
- resource quota;
- result artifact namespace: `~/.hermes/evidence/cua-bench/...`;
- no mainline replacement.

## Decision matrix

- Default browser automation replacement: **No-Go**
- Optional benchmark schema: **Go**
- Optional synthetic sandbox adapter: **Conditional Go after schema tests**
- CUA Localhost/direct host control: **No-Go**
- Cloud CUA sandbox: **Manual-gated only**
- Real-site writes/logins: **No-Go without separate human gate**

## Acceptance result for task

The task requested an evaluation record with:

- 3 real Hermes automation cases: included above.
- Existing browser chain vs candidate sandbox/benchmark comparison: included.
- Evidence gain and maintenance cost: included.
- Failure modes and permission boundaries: included.
- Rollback design: disable flag, no default dependency, artifact cleanup, no mainline replacement.
- Go/no-go: conditional Go for optional benchmark/sandbox schema; No-Go for default mainline replacement.

## Boundary not crossed

- Did not install CUA.
- Did not run CUA code.
- Did not start Docker/QEMU/VM/cloud sandbox.
- Did not change Hermes default browser provider.
- Did not modify user config.
- Did not enable any external side-effect executor.
