import {
  replayWorkflowRunTransitionsV1,
  type WorkflowRunReplayV1Result,
} from './workflow-run-replay'
import {
  type WorkflowRunStatusV1,
  WorkflowRunStatusV1Schema,
} from './workflow-run-status'

type ReplayFailureV1 = Extract<WorkflowRunReplayV1Result, { ok: false }>

export type WorkflowRunShadowV1Result =
  | {
      verdict: 'match' | 'mismatch'
      observed_status: WorkflowRunStatusV1
      candidate_status: WorkflowRunStatusV1
      applied: number
    }
  | { verdict: 'invalid_observed' }
  | {
      verdict: 'replay_error'
      observed_status: WorkflowRunStatusV1
      replay: ReplayFailureV1
    }

export function evaluateWorkflowRunShadowV1(
  initial: unknown,
  transitions: unknown,
  observed: unknown,
): WorkflowRunShadowV1Result {
  const parsedObserved = WorkflowRunStatusV1Schema.safeParse(observed)
  if (!parsedObserved.success) return { verdict: 'invalid_observed' }

  const replay = replayWorkflowRunTransitionsV1(initial, transitions)
  if (!replay.ok) {
    return {
      verdict: 'replay_error',
      observed_status: parsedObserved.data,
      replay,
    }
  }

  return {
    verdict: replay.status === parsedObserved.data ? 'match' : 'mismatch',
    observed_status: parsedObserved.data,
    candidate_status: replay.status,
    applied: replay.applied,
  }
}
