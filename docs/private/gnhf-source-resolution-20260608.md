# Good Night Have Fun / gnhf source resolution — 2026-06-08

## Scope

Task: `2026-06-07-good-night-have-funai-睡眠时持续编码开源项目识别与-hermes-长时执行差距评估`

User source: WeChat Channels URL `https://weixin.qq.com/sph/AHICpdAvd1`; prior browser/vision evidence showed the visible project text `GOOD NIGHT HAVE FUN` / `晚安，玩得开心` and title “这个开源项目，可以让AI在你睡觉时一直干活”.

## Source resolution result

Confirmed GitHub repository:

- Repository: `kunchenguid/gnhf`
- URL: https://github.com/kunchenguid/gnhf
- Description from GitHub: “Before I go to bed, I tell my agents: good night, have fun”
- License: MIT
- Default branch: `main`
- Stars at inspection: 1918
- Forks at inspection: 136
- Updated at inspection: `2026-06-07T19:00:32Z`
- Root files observed by GitHub API: `.airlock`, `AGENTS.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `LICENSE`, `README.md`, `docs`, `e2e`, `package.json`, `pnpm-lock.yaml`, `pnpm-workspace.yaml`, `skills`, `src`, TS config/release files.

Commands/evidence used:

```text
gh search repos '"Good Night Have Fun"' --limit 5 --json fullName,description,stargazersCount,url,updatedAt
gh search repos '"good-night-have-fun"' --limit 5 --json fullName,description,stargazersCount,url,updatedAt
gh repo view kunchenguid/gnhf --json nameWithOwner,description,stargazerCount,forkCount,licenseInfo,defaultBranchRef,updatedAt,url
gh api repos/kunchenguid/gnhf/readme --jq .content | base64 -d
```

Key README claims inspected without installing or running code:

- “gnhf is a ralph, autoresearch-style orchestrator that keeps your agents running while you sleep.”
- “each iteration makes one small, committed, documented change towards an objective.”
- “Run multiple agents on the same repo simultaneously using worktrees.”
- “Commit directly on the current branch and push after each successful iteration.”
- README contains mechanisms relevant to Hermes: sleep/overnight loop, worktree parallelism, per-iteration commits, rollback/failure handling, exit summary, agent-agnostic CLI targets.

## Hermes gap map

Potentially useful classes to absorb conceptually, not by installing/running `gnhf`:

1. **Iteration ledger**: every autonomous step produces a commit/log/summary; Hermes analogue is taskpool evidence spine + worker log + proof artifact.
2. **Worktree isolation**: parallel agents can operate in independent git worktrees; Hermes analogue is agent-team workspace boundary and scoped commit policy.
3. **Retry/rollback semantics**: successful iterations commit; failed iterations rollback or preserve failure state for repair; Hermes analogue is fail-closed lifecycle transition and stale-claim reconciliation.
4. **Exit summary**: every long run ends with branch, elapsed time, iteration count, tokens, diff stats, review commands; Hermes analogue is task closeout evidence and dashboard projection.
5. **Agent-agnostic adapter**: Claude/Codex/OpenCode/Copilot targets; Hermes analogue is bounded delegated worker adapters and model/tool routing.

## Anti-drift boundary

- Do not install or execute `gnhf` during intake.
- Do not replace Hermes taskpool lifecycle with a third-party loop.
- Do not enable auto-push/auto-current-branch behavior for Hermes project work.
- Treat `gnhf` as reference architecture for long-running execution evidence and not as implementation authority.
- Any future implementation must preserve Hermes permission tier, proof gate, stale-claim fail-closed reconciliation, and scoped commit rules.

## Smallest safe follow-up slice

Patch or create a Hermes reference on long-running autonomous execution closeout that compares:

- gnhf-style per-iteration commit/log/summary
- Hermes taskpool `evidence`, worker job JSONL, dashboard/control-center projection
- stale running detection and fail-closed restore path

Acceptance for that follow-up: deterministic selfcheck/proof-check + a synthetic worker log fixture proving stale-claim cleanup and exit-summary projection.

## Decision

Source resolution is now sufficient to move the Good Night Have Fun task from source-blocked to review/certification. Implementation remains out of scope until a separate, proof-gated Hermes design slice is accepted.
