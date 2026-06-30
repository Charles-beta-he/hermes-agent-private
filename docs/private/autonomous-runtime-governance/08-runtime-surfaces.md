# Runtime Surfaces

Runtime surfaces are projections and control interfaces over ERG Runtime. They must not silently become competing sources of truth.

## Surface taxonomy

| Surface | Role | Fact authority |
|---|---|---|
| Control Tower | operator cockpit: progressability, blockers, evidence debt, human inbox, executor console | projection over ledgers/domain stores |
| Dashboard | quick read-only or limited-action status | projection only unless action is digest-bound and read back |
| CLI | doctor/audit/route/compensate/admin commands | command receipts become evidence when recorded/read back |
| Gateway | user messaging ingress/egress and delivery receipts | platform receipt/outbox is evidence for message delivery only |
| Taskpool/Kanban | task-domain lifecycle surface | task-domain SSOT; cross-system closure still requires Evidence/ClosureRecord |
| Mission board | durable objective and tick/handoff projection | mission-domain ledger; not global execution authority |
| Cron status | schedule/job projection | schedule intent + run history; not enough for business completion alone |
| Logs | debugging and clues | non-canonical unless promoted into EvidenceNode |
| Docs/ADRs | design and decision projection | not runtime proof |
| Evidence ledger | runtime evidence spine | canonical for claims it records |

## Control Tower contract

Control Tower should answer:

- Can Hermes make progress right now?
- What is blocked and why?
- What evidence is missing/stale/conflicting?
- What human decision is required?
- What is the next safe action?
- What rollback or escape path exists?

Control Tower must not hide stale signals or proof debt behind green summaries.

## Gateway contract

Gateway messages are user-facing projections. A delivery receipt/message_id proves delivery to a platform target, not the truth of the underlying task unless linked to the task's closure evidence.

## Dashboard/UI contract

No UI button should perform contextless actions like "first pending". Write actions need selected object, digest, exact mutation, role/authority, snapshot freshness, readback, and rollback.

## Public docs contract

Public architecture docs may describe stable concepts and code layout. Private authority ladders, local paths, receipts, and high-risk gates stay in private governance docs.


## Resume layer contract

Resume requests such as `继续`, `接着做`, or `刚才那个` are resolved by candidate selection, not by automatic execution.

Candidate sources:

- current conversation tail;
- project profile and progress ledger;
- mission/taskpool human gates;
- cron/background completion receipts;
- recent session_search results;
- explicit user-provided artifact links.

Safety rules:

- group chats default to private-summary redaction;
- cross-profile candidates require explicit selection;
- high-risk resume returns an intent card before execution;
- resume selection is advisory until tied to an AuthorityGrant for side effects.

## Interruptibility policy

Proactive notifications are routed through severity, timing, channel, user policy, and evidence gates before delivery.

Severity ladder:

- L0 `silent_record`: ledger only;
- L1 `digest`: batch into scheduled summary;
- L2 `normal_notice`: ordinary low-risk update;
- L3 `decision_required`: human gate or user decision;
- L4 `urgent_failure`: timely failure notice;
- L5 `safety_finance_privacy_prod`: strong notice, but no automatic high-risk action.

No proactive notification may bypass gateway policy, budget, channel scope, and evidence refs. Shadow records may recommend a severity but must not deliver.
