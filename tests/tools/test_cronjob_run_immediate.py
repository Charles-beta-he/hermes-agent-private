"""Tests for cronjob action='run' immediate execution (#41037).

Before this fix, `cronjob(action='run')` only set next_run_at=now and returned
success, relying on the scheduler ticker to actually run the job. With no
gateway/ticker active (e.g. a CLI-only Windows setup) the job never executed and
last_run_at stayed null forever. Now action='run' obtains a fenced claim,
blocking a concurrent healthy owner, and fires inline via shared run_one_job.
Arbitrary effects are not exactly-once across stale-lease recovery.
"""
import json
from unittest.mock import ANY, patch

from tools.cronjob_tools import cronjob, _execute_job_now


_JOB = {"id": "job-run-1", "name": "manual run", "prompt": "hi",
        "schedule": {"kind": "cron", "expr": "0 9 * * *"}}
_CLAIMED = {
    **_JOB,
    "trigger_generation": 3,
    "run_claim": {"id": "claim-3"},
    "fire_claim": {"id": "claim-3"},
}


class TestCronjobRunExecutesImmediately:
    def test_run_action_claims_and_fires_via_run_one_job(self):
        """action='run' must claim the job then fire it through run_one_job."""
        ran = {"job": "after-run", "last_status": "ok", "last_error": None}
        execution_started = []

        def _run(claimed, *, result_out=None):
            execution_started.append(claimed)
            assert result_out is not None
            result_out.update(
                completion_accepted=True,
                execution_success=True,
                error=None,
            )
            return True

        def _read_after_execution(_job_id):
            assert execution_started, "manual path re-read the store before execution"
            return ran

        with patch("tools.cronjob_tools.resolve_job_ref", return_value=dict(_JOB)), \
             patch("tools.cronjob_tools.claim_job_for_execution", return_value=dict(_CLAIMED)) as m_claim, \
             patch("cron.scheduler.run_one_job", side_effect=_run) as m_run, \
             patch("tools.cronjob_tools.get_job", side_effect=_read_after_execution) as m_get:
            out = json.loads(cronjob(action="run", job_id="job-run-1"))

        assert out["success"] is True
        assert out["job"]["executed"] is True
        assert out["job"]["execution_success"] is True
        m_claim.assert_called_once_with("job-run-1")
        m_run.assert_called_once_with(_CLAIMED, result_out=ANY)
        assert m_get.call_count == 1

    def test_run_skips_when_claim_lost(self):
        """If the scheduler already holds the fire claim, do NOT double-run."""
        with patch("tools.cronjob_tools.resolve_job_ref", return_value=dict(_JOB)), \
             patch("tools.cronjob_tools.claim_job_for_execution", return_value=None), \
             patch("cron.scheduler.run_one_job") as m_run, \
             patch("tools.cronjob_tools.get_job", return_value=dict(_JOB)):
            out = json.loads(cronjob(action="run", job_id="job-run-1"))

        assert out["success"] is True
        assert out["job"]["executed"] is False
        assert out["job"]["execution_success"] is False
        assert "execution_skipped" in out["job"]
        m_run.assert_not_called()  # claim lost -> never fired

    def test_run_reports_failure_from_last_status(self):
        """A failed run is reported via the re-read job's last_status/last_error."""
        failed = {"id": "job-run-1", "last_status": "error", "last_error": "provider 500"}

        def _failed_run(_job, *, result_out=None):
            assert result_out is not None
            result_out.update(
                completion_accepted=True,
                execution_success=False,
                error="provider 500",
            )
            return True

        with patch("tools.cronjob_tools.resolve_job_ref", return_value=dict(_JOB)), \
             patch("tools.cronjob_tools.claim_job_for_execution", return_value=dict(_CLAIMED)), \
             patch("cron.scheduler.run_one_job", side_effect=_failed_run), \
             patch("tools.cronjob_tools.get_job", return_value=failed):
            out = json.loads(cronjob(action="run", job_id="job-run-1"))

        assert out["job"]["executed"] is True
        assert out["job"]["execution_success"] is False
        assert out["job"]["execution_error"] == "provider 500"

    def test_execute_job_now_bails_without_claim(self):
        """_execute_job_now never calls run_one_job when the claim is lost."""
        with patch("tools.cronjob_tools.claim_job_for_execution", return_value=None), \
             patch("cron.scheduler.run_one_job") as m_run:
            res = _execute_job_now(dict(_JOB))
        assert res["claimed"] is False
        assert res["success"] is False
        m_run.assert_not_called()

    def test_execute_job_now_reports_claim_exception_as_not_executed(self):
        with patch(
            "tools.cronjob_tools.claim_job_for_execution",
            side_effect=RuntimeError("claim store unavailable"),
        ), patch("cron.scheduler.run_one_job") as m_run, patch(
            "tools.cronjob_tools.mark_job_run"
        ) as m_mark:
            res = _execute_job_now(dict(_JOB))

        assert res == {
            "claimed": False,
            "success": False,
            "error": "claim store unavailable",
        }
        m_run.assert_not_called()
        m_mark.assert_not_called()

    def test_execute_job_now_marks_failure_on_exception(self):
        """An exception during fire is captured, marked failed, not propagated."""
        with patch("tools.cronjob_tools.claim_job_for_execution", return_value=dict(_CLAIMED)), \
             patch("cron.scheduler.run_one_job", side_effect=RuntimeError("boom")), \
             patch("tools.cronjob_tools.mark_job_run") as m_mark, \
             patch("tools.cronjob_tools.get_job", return_value=dict(_JOB)):
            res = _execute_job_now(dict(_JOB))
        assert res["claimed"] is True
        assert res["success"] is False
        assert "boom" in res["error"]
        m_mark.assert_called_once_with(
            "job-run-1",
            False,
            "boom",
            run_trigger_generation=3,
            run_claim_id="claim-3",
        )

    def test_terminal_finite_success_survives_post_completion_deletion(self):
        claimed = {
            **_CLAIMED,
            "schedule": {"kind": "once", "run_at": "2026-08-11T00:00:00+00:00"},
            "repeat": {"times": 1, "completed": 0},
        }

        def _run(_job, *, result_out=None):
            assert result_out is not None
            result_out.update(
                completion_accepted=True,
                execution_success=True,
                error=None,
            )
            return True

        with patch("tools.cronjob_tools.claim_job_for_execution", return_value=claimed), \
             patch("cron.scheduler.run_one_job", side_effect=_run), \
             patch("tools.cronjob_tools.get_job", return_value=None):
            res = _execute_job_now(dict(_JOB))

        assert res == {"claimed": True, "success": True, "error": None}
