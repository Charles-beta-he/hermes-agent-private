import {
  type WorkflowRunStatusV1,
  WorkflowRunStatusV1Schema,
} from './workflow-run-status'
import { WorkflowRunTransitionV1Schema } from './workflow-run-transition'

export type WorkflowRunReplayV1Result =
  | { ok: true; status: WorkflowRunStatusV1; applied: number }
  | { ok: false; reason: 'invalid_initial' | 'invalid_transitions' }
  | {
      ok: false
      reason: 'invalid_transition' | 'discontinuous_transition'
      index: number
      status: WorkflowRunStatusV1
    }

export function replayWorkflowRunTransitionsV1(
  initial: unknown,
  transitions: unknown,
): WorkflowRunReplayV1Result {
  const parsedInitial = WorkflowRunStatusV1Schema.safeParse(initial)
  if (!parsedInitial.success) return { ok: false, reason: 'invalid_initial' }
  if (!Array.isArray(transitions)) {
    return { ok: false, reason: 'invalid_transitions' }
  }

  let status: WorkflowRunStatusV1 = parsedInitial.data
  for (const [index, candidate] of transitions.entries()) {
    const parsed = WorkflowRunTransitionV1Schema.safeParse(candidate)
    if (!parsed.success) {
      return { ok: false, reason: 'invalid_transition', index, status }
    }
    if (parsed.data.from !== status) {
      return { ok: false, reason: 'discontinuous_transition', index, status }
    }
    status = parsed.data.to
  }

  return { ok: true, status, applied: transitions.length }
}
