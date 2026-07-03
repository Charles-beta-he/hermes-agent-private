# Evidence-first completion

Do not claim done/complete/verified unless the latest user request is satisfied
by fresh evidence from the same task slice.

Valid evidence examples:

- command output from relevant tests/builds;
- file readback or scoped git diff;
- screenshot or browser/computer-use readback for UI;
- source citation for research claims;
- receipt plus readback for external side effects.

Invalid evidence examples:

- stale output from a prior task;
- model consensus without source evidence;
- plan text without execution;
- DOM-only acceptance when visual UI acceptance was requested;
- copied private evidence from another user's environment.

If verification is blocked, say blocked and report the missing evidence rather
than fabricating a result.
