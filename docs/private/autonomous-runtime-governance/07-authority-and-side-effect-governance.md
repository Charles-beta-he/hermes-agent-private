# Authority and Side-Effect Governance

## Side-effect taxonomy

| Class | Meaning | Default |
|---|---|---|
| S0 | no side effect / advisory | allowed |
| S1 | local read | allowed with privacy boundary |
| S2 | local write | allowed only when reversible/path-scoped/readbacked |
| S3 | external read | requires privacy/rate/auth boundary |
| S4 | external write | exact target + payload + AuthorityGrant + receipt + readback |
| S5 | publish/deploy/push | exact target + rollback + post-action readback |
| S6 | destructive | exact resource + backup/restore + human gate |
| S7 | physical/payment/credential | human-only for secret/payment entry; agent stops at gate |

## Authority rule

A broad product direction does not automatically open a side-effect class. Each side effect needs an exact target, intended mutation, evidence, rollback/escape path, and readback.

## Worker/agent rule

Workers and subagents can execute bounded capability nodes. They cannot own lifecycle mutation, external send authority, memory writes, or final completion unless a separate gate explicitly grants that exact action.

## External dispatch rule

External sends must include:

- target platform/channel/user;
- payload hash/summary;
- idempotency key;
- authorization receipt;
- platform receipt/message_id;
- readback or dead_letter state.

## Compensation authority

Compensation never expands authority. It can only perform actions already allowed by the original or a fresh narrower AuthorityGrant.


## Default-off and shadow-first classes

Default-off capabilities:

- always-on microphone/camera/screen observation;
- automatic long-term memory writes;
- automatic external sends;
- automatic cron creation/modification;
- automatic production deploy/publish/push;
- destructive actions;
- payment, physical action, life-service booking;
- worker lifecycle mutation authority.

Shadow-first capabilities:

- intent routing;
- memory/skill/task candidate extraction;
- proactive suggestion;
- model routing;
- environment understanding;
- edge event classification.

Promotion from shadow to active requires:

1. declared CapabilityNode;
2. side-effect class;
3. AuthorityGrant scope;
4. evidence requirements;
5. tests or dry-run proof;
6. rollback / kill switch;
7. user-visible receipt;
8. cost budget;
9. stale/failure behavior.

## Exact high-risk execution requirements

Destructive operations require exact path/resource, operation verb, pre-action backup/snapshot, rollback/restore command, expected post-action state, readback command, blast radius, and explicit confirmation containing the exact target.

Payment/credential actions require human-side ownership and redacted receipt shape. The agent must not type passwords, API keys, cards, OTPs, seed phrases, or other secrets.

Broad fanout requires exact channel set, exact payload text, idempotency key or duplicate policy, per-target receipt requirement, post-send readback, and correction/rollback message policy. Vague targets like "all contacts" are not acceptable.

Publish/deploy/push requires exact repository/environment/remote, operation, rollback command, and readback command. Prior certified private-repo push smoke does not authorize future unrelated pushes or deployments.
