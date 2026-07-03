# Durable memory policy

Transfer this policy, not anyone's memory payload.

## Landing zones

- Stable user preference or red line -> user profile / user memory.
- Stable environment/tool fact -> agent memory.
- Project-specific fact -> project profile or project SSOT.
- Reusable procedure/checklist -> skill.
- Evidence/session transcript/task progress -> evidence ledger or session history, not long-term memory.
- Credentials/secrets/payment details -> never save.

## Anti-ghost gate

Before saving durable memory, ask:

1. Will this still be true in 10 sessions?
2. Is it declarative rather than imperative?
3. Is it free of task progress and artifact IDs likely stale soon?
4. Is the scope correct: user/global vs project vs session vs task?
5. Would saving it cause future sessions to repeat old work or override current instructions?

If not, do not save it as memory.
