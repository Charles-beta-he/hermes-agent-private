# Private Hermes Stable Points

| Date | Upstream commit | Private commit | Branch / tag | Verification evidence | Notes |
| --- | --- | --- | --- | --- | --- |
| 2026-05-18 | `609c485fc6d0a0c24a023cd1349ebd6ddbf60315` | `9282d262df6c8d6a0c6c8bcc18a3db6512b1d040` | `private/main`, `private-stable-20260518-landing` | `/Users/charles/.hermes/backups/hermes-governance/landing-20260518-203325/p4-integrate-verify.txt` | First governance landing: origin/upstream split, runtime hygiene, overlay registry, integrate branch replay, post-update-check, targeted pytest, doctor, chat smoke |
| 2026-05-18 | `609c485fc6d0a0c24a023cd1349ebd6ddbf60315` | `2f28b8f6290735ea9a6dfa7638f46f544150cf8e` | `private/main` | `/Users/charles/.hermes/backups/multibrain-rollout-20260518-2130/final-verification-after-overlay-snapshot.log`; P10 `/Users/charles/.hermes/backups/multibrain-rollout-20260518-2130/p10-final-pass.log` | Multi-brain rollout: `brainctl brains validate|route`, `multi_brain_registry`, private runtime docs, personal-hermes-brain backup branch `backup/multibrain-full-rollout-20260518` at `8e5fd8ea42c6db62f92ffd599958bca8c5ff726d` |

## Evidence bundle

- Freeze directory: `/Users/charles/.hermes/backups/hermes-governance/landing-20260518-203325`
- Repo copy: `/Users/charles/.hermes/backups/hermes-agent-repo-copy-20260518-203325`
- Initial rollback branch: `backup/landing-20260518-203325`
- Initial rollback tag: `backup-landing-20260518-203325`
- Integration branch: `integrate/20260518-upstream-sync`
- Private repo: `https://github.com/Charles-beta-he/hermes-agent-private`

## Verification summary for first landing

- `post-update-check.py --fix`: 14 OK, 0 fixed, 1 known attention (`skill_quality` missing tests in unrelated skills)
- Targeted pytest: 216 passed, 1 skipped
- `git diff --check`: pass
- `hermes doctor`: one non-blocking setup issue for optional missing API keys/tool providers
- CLI smoke: `HERMES_SMOKE_OK`
