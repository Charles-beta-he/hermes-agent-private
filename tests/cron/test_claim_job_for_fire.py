"""Tests for the store-level CAS fire claim (Phase 4C).

`claim_job_for_fire` gives a fenced execution lease when an external scheduler
(Chronos) fires a job. One healthy lease owns Hermes-managed completion and
post-run delivery, but arbitrary effects inside the agent are not exactly-once
across crash or lease-loss recovery.

These exercise the real store against a temp HERMES_HOME (no mocks) per the
E2E-over-mocks discipline for file-touching code.
"""
import json
import os
import subprocess
import sys
import threading
import time

import pytest


@pytest.fixture
def temp_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME so jobs.json doesn't touch the real store."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import cron.jobs as jobs
    monkeypatch.setattr(jobs, "HERMES_DIR", tmp_path)
    monkeypatch.setattr(jobs, "CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr(jobs, "JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr(jobs, "OUTPUT_DIR", tmp_path / "cron" / "output")
    yield tmp_path


def test_nonfinite_timeout_uses_finite_claim_ttl_floor(monkeypatch):
    import cron.jobs as jobs

    for value in ("nan", "inf", "-inf", "1e308"):
        monkeypatch.setenv("HERMES_CRON_TIMEOUT", value)
        assert jobs._oneshot_run_claim_ttl_seconds() == float(
            jobs.ONESHOT_RUN_CLAIM_TTL_SECONDS
        )


def test_claim_succeeds_once_then_blocks(temp_home):
    """First claim for a fire wins; a second claim for the same fire loses, and
    next_run_at is advanced (a re-delivery for the old time can't re-fire)."""
    from cron.jobs import create_job, claim_job_for_fire, get_job

    job = create_job(prompt="x", schedule="every 5m", name="t")
    jid = job["id"]
    before = get_job(jid)["next_run_at"]

    assert claim_job_for_fire(jid) is True
    assert claim_job_for_fire(jid) is False
    assert get_job(jid)["next_run_at"] != before


def test_atomic_execution_claim_returns_its_own_generation_and_token(temp_home):
    from cron.jobs import claim_job_for_execution, create_job, get_job, trigger_job

    job = create_job(prompt="x", schedule="every 5m", name="atomic-snapshot")
    trigger_job(job["id"])
    persisted_before = get_job(job["id"])
    assert persisted_before is not None
    expected_generation = persisted_before["trigger_generation"]

    claimed = claim_job_for_execution(job["id"])

    assert claimed is not None
    assert claimed["trigger_generation"] == expected_generation
    assert claimed["run_claim"]["id"]
    assert claimed["fire_claim"] == claimed["run_claim"]
    persisted_after = get_job(job["id"])
    assert persisted_after is not None
    assert persisted_after["run_claim"] == claimed["run_claim"]


def test_claimed_snapshot_never_adopts_recovery_successor_token(temp_home, monkeypatch):
    import cron.jobs as jobs
    from cron.jobs import (
        claim_job_for_execution,
        create_job,
        get_due_jobs,
        get_job,
        mark_job_run,
        trigger_job,
    )

    job = create_job(prompt="x", schedule="every 5m", name="no-token-adoption")
    first = claim_job_for_execution(job["id"])
    assert first is not None
    trigger_job(job["id"])
    after_trigger = get_job(job["id"])
    assert after_trigger is not None
    assert after_trigger["trigger_generation"] == first["trigger_generation"] + 1

    monkeypatch.setattr(jobs, "_recurring_claim_stale_seconds", lambda ttl: 10.0)
    monotonic_values = iter((100.0, 111.0))
    monkeypatch.setattr(jobs.time, "monotonic", lambda: next(monotonic_values))
    assert claim_job_for_execution(job["id"], claim_ttl_seconds=0) is None
    successor = claim_job_for_execution(job["id"], claim_ttl_seconds=0)

    assert successor is not None
    assert first["run_claim"]["id"] != successor["run_claim"]["id"]
    assert first["trigger_generation"] == successor["trigger_generation"]
    after_recovery = get_job(job["id"])
    assert after_recovery is not None
    assert after_recovery["trigger_generation"] == after_trigger["trigger_generation"]
    assert after_recovery["next_run_at"] == after_trigger["next_run_at"]

    import cron.scheduler as scheduler
    side_effects = []
    monkeypatch.setattr(
        scheduler,
        "run_job",
        lambda *_args, **_kwargs: side_effects.append("ran"),
    )
    assert scheduler.run_one_job(first) is False
    assert side_effects == []

    assert mark_job_run(
        job["id"],
        True,
        run_trigger_generation=successor["trigger_generation"],
        run_claim_id=successor["run_claim"]["id"],
    ) is True
    continuation = get_due_jobs()
    assert [item["id"] for item in continuation] == [job["id"]]
    assert continuation[0]["trigger_generation"] == after_trigger["trigger_generation"]


@pytest.mark.parametrize("bad_value", ["malformed", float("nan"), float("inf"), float("-inf")])
def test_malformed_trigger_generation_is_normalized_before_completion(
    temp_home, bad_value
):
    from cron.jobs import (
        claim_job_for_execution,
        create_job,
        get_job,
        mark_job_run,
        update_job,
    )

    job = create_job(prompt="x", schedule="every 5m", name="bad-generation")
    update_job(job["id"], {"trigger_generation": bad_value})
    claimed = claim_job_for_execution(job["id"])
    assert claimed is not None
    assert claimed["trigger_generation"] == 0
    persisted = get_job(job["id"])
    assert persisted is not None
    assert persisted["trigger_generation"] == 0
    assert mark_job_run(
        job["id"],
        True,
        run_trigger_generation=claimed["trigger_generation"],
        run_claim_id=claimed["run_claim"]["id"],
    ) is True
    completed = get_job(job["id"])
    assert completed is not None
    assert completed["trigger_generation"] == 0
    assert completed["run_claim"] is None


def test_claim_oneshot_cannot_be_double_claimed(temp_home):
    """A one-shot can't be double-claimed (the fresh claim blocks the retry)."""
    from cron.jobs import create_job, claim_job_for_fire

    job = create_job(prompt="x", schedule="30m", name="o")
    assert claim_job_for_fire(job["id"]) is True
    assert claim_job_for_fire(job["id"]) is False


def test_claim_unknown_job_returns_false(temp_home):
    from cron.jobs import claim_job_for_fire

    assert claim_job_for_fire("nope-does-not-exist") is False


def test_claim_paused_job_returns_false(temp_home):
    """A paused job can't be claimed."""
    from cron.jobs import create_job, claim_job_for_fire, pause_job

    job = create_job(prompt="x", schedule="every 5m", name="p")
    pause_job(job["id"])
    assert claim_job_for_fire(job["id"]) is False


def test_live_recurring_claim_is_not_reclaimed_by_ttl(temp_home):
    """A healthy recurring owner is authoritative; wall-clock TTL cannot fence it."""
    from cron.jobs import create_job, claim_job_for_fire

    job = create_job(prompt="x", schedule="every 5m", name="s")
    jid = job["id"]
    assert claim_job_for_fire(jid) is True
    assert claim_job_for_fire(jid, claim_ttl_seconds=0) is False


def test_unchanged_recurring_heartbeat_reclaims_after_monotonic_window(
    temp_home, monkeypatch
):
    """Crash recovery depends on observed heartbeat stasis, not PID or wall time."""
    import cron.jobs as jobs
    from cron.jobs import claim_job_for_fire, create_job

    job = create_job(prompt="x", schedule="every 5m", name="stale-heartbeat")
    assert claim_job_for_fire(job["id"]) is True
    monkeypatch.setattr(jobs, "_recurring_claim_stale_seconds", lambda ttl: 10.0)
    monotonic_values = iter((100.0, 111.0))
    monkeypatch.setattr(jobs.time, "monotonic", lambda: next(monotonic_values))

    assert claim_job_for_fire(job["id"], claim_ttl_seconds=0) is False
    assert claim_job_for_fire(job["id"], claim_ttl_seconds=0) is True


def test_heartbeat_renewal_preserves_fenced_owner(temp_home):
    from cron.jobs import (
        claim_job_for_fire,
        create_job,
        get_job,
        renew_job_run_claim,
    )

    job = create_job(prompt="x", schedule="every 5m", name="heartbeat")
    assert claim_job_for_fire(job["id"]) is True
    current = get_job(job["id"])
    assert current is not None
    token = current["run_claim"]["id"]
    before = current["run_claim"]["heartbeat"]

    assert renew_job_run_claim(job["id"], token) is True
    renewed = get_job(job["id"])
    assert renewed is not None
    assert renewed["run_claim"]["heartbeat"] == before + 1
    assert renewed["fire_claim"] == renewed["run_claim"]
    assert renew_job_run_claim(job["id"], "wrong-token") is False


def test_malformed_heartbeat_renewal_fails_closed_then_stale_claim_recovers(
    temp_home, monkeypatch
):
    import cron.jobs as jobs
    from cron.jobs import (
        claim_job_for_execution,
        claim_job_for_fire,
        create_job,
        get_job,
        renew_job_run_claim,
        update_job,
    )

    job = create_job(prompt="x", schedule="every 5m", name="bad-heartbeat")
    assert claim_job_for_fire(job["id"]) is True
    current = get_job(job["id"])
    assert current is not None
    token = current["run_claim"]["id"]
    malformed = dict(current["run_claim"])
    malformed["heartbeat"] = "not-an-int"
    update_job(job["id"], {"run_claim": malformed, "fire_claim": malformed})

    assert renew_job_run_claim(job["id"], token) is False
    monkeypatch.setattr(jobs, "_recurring_claim_stale_seconds", lambda ttl: 10.0)
    monotonic_values = iter((100.0, 111.0))
    monkeypatch.setattr(jobs.time, "monotonic", lambda: next(monotonic_values))
    assert claim_job_for_execution(job["id"], claim_ttl_seconds=0) is None
    recovered = claim_job_for_execution(job["id"], claim_ttl_seconds=0)
    assert recovered is not None
    assert recovered["run_claim"]["id"] != token
    persisted = get_job(job["id"])
    assert persisted is not None
    assert persisted["run_claim"] == recovered["run_claim"]


@pytest.mark.parametrize("malformed", [True, 1.5, "2", -1])
def test_coercible_or_negative_heartbeat_renewal_fails_closed_then_recovers(
    temp_home, monkeypatch, malformed
):
    import cron.jobs as jobs
    from cron.jobs import (
        claim_job_for_fire,
        create_job,
        get_job,
        mark_job_run,
        renew_job_run_claim,
        update_job,
    )

    job = create_job(prompt="x", schedule="every 5m", name="strict-heartbeat")
    assert claim_job_for_fire(job["id"]) is True
    current = get_job(job["id"])
    assert current is not None
    token = current["run_claim"]["id"]
    forged = dict(current["run_claim"], heartbeat=malformed)
    update_job(job["id"], {"run_claim": forged, "fire_claim": forged})
    assert renew_job_run_claim(job["id"], token) is False
    assert mark_job_run(job["id"], True, run_claim_id=token) is False
    persisted = get_job(job["id"])
    assert persisted is not None
    assert persisted["run_claim"] == forged

    monkeypatch.setattr(jobs, "_recurring_claim_stale_seconds", lambda ttl: 10.0)
    monotonic_values = iter((100.0, 111.0))
    monkeypatch.setattr(jobs.time, "monotonic", lambda: next(monotonic_values))
    assert jobs.claim_job_for_execution(job["id"], claim_ttl_seconds=0) is None
    recovered = jobs.claim_job_for_execution(job["id"], claim_ttl_seconds=0)
    assert recovered is not None
    assert recovered["run_claim"]["id"] != token


def test_future_protocol_cannot_be_renewed_or_completed_by_v2_owner(temp_home):
    from cron.jobs import claim_job_for_fire, create_job, get_job, mark_job_run, renew_job_run_claim, update_job

    job = create_job(prompt="x", schedule="every 5m", name="future-protocol")
    assert claim_job_for_fire(job["id"]) is True
    current = get_job(job["id"])
    assert current is not None
    token = current["run_claim"]["id"]
    future = dict(current["run_claim"], protocol=3)
    update_job(job["id"], {"run_claim": future, "fire_claim": future})

    assert renew_job_run_claim(job["id"], token) is False
    assert mark_job_run(job["id"], True, run_claim_id=token) is False
    persisted = get_job(job["id"])
    assert persisted is not None
    assert persisted["run_claim"] == future
    assert persisted["fire_claim"] == future


def test_future_protocol_mirror_cannot_hide_behind_same_v2_uuid(
    temp_home, monkeypatch
):
    import cron.jobs as jobs
    from cron.jobs import claim_job_for_execution, create_job, get_job, update_job

    job = create_job(prompt="x", schedule="every 5m", name="future-mirror")
    claimed = claim_job_for_execution(job["id"])
    assert claimed is not None
    current = claimed["run_claim"]
    future = dict(current, protocol=3)
    update_job(job["id"], {"run_claim": current, "fire_claim": future})

    monkeypatch.setattr(jobs, "_recurring_claim_stale_seconds", lambda ttl: 10.0)
    monotonic_values = iter((100.0, 111.0))
    monkeypatch.setattr(jobs.time, "monotonic", lambda: next(monotonic_values))
    assert claim_job_for_execution(job["id"], claim_ttl_seconds=0) is None
    assert claim_job_for_execution(job["id"], claim_ttl_seconds=0) is None
    persisted = get_job(job["id"])
    assert persisted is not None
    assert persisted["run_claim"] == current
    assert persisted["fire_claim"] == future


@pytest.mark.parametrize("field", ["heartbeat", "trigger_generation", "at"])
@pytest.mark.parametrize("operation", ["admission", "renewal", "completion"])
def test_same_token_mismatched_v2_mirrors_fail_closed(
    temp_home, monkeypatch, field, operation
):
    import cron.jobs as jobs
    from cron.jobs import (
        claim_job_for_execution,
        create_job,
        get_job,
        mark_job_run,
        renew_job_run_claim,
        update_job,
    )

    job = create_job(prompt="x", schedule="every 5m", name="mismatched-mirrors")
    claimed = claim_job_for_execution(job["id"])
    assert claimed is not None
    current = claimed["run_claim"]
    mismatched = dict(current)
    if field in {"heartbeat", "trigger_generation"}:
        mismatched[field] = current.get(field, 0) + 1
    else:
        mismatched[field] = "forged-different-value"
    update_job(job["id"], {"run_claim": current, "fire_claim": mismatched})

    if operation == "admission":
        monkeypatch.setattr(jobs, "_recurring_claim_stale_seconds", lambda ttl: 10.0)
        monotonic_values = iter((100.0, 111.0))
        monkeypatch.setattr(jobs.time, "monotonic", lambda: next(monotonic_values))
        assert claim_job_for_execution(job["id"], claim_ttl_seconds=0) is None
        assert claim_job_for_execution(job["id"], claim_ttl_seconds=0) is None
    elif operation == "renewal":
        assert renew_job_run_claim(job["id"], current["id"]) is False
    else:
        assert mark_job_run(job["id"], True, run_claim_id=current["id"]) is False

    persisted = get_job(job["id"])
    assert persisted is not None
    assert persisted["run_claim"] == current
    assert persisted["fire_claim"] == mismatched
    assert persisted.get("last_run_at") is None


@pytest.mark.parametrize(
    "missing_field",
    ["id", "protocol", "trigger_generation", "heartbeat", "at", "by", "host", "pid"],
)
@pytest.mark.parametrize("operation", ["admission", "renewal", "completion"])
def test_identical_v2_mirrors_missing_required_field_fail_closed(
    temp_home, monkeypatch, missing_field, operation
):
    import cron.jobs as jobs
    from cron.jobs import (
        claim_job_for_execution,
        create_job,
        get_job,
        mark_job_run,
        renew_job_run_claim,
        update_job,
    )

    job = create_job(prompt="x", schedule="every 5m", name="missing-claim-field")
    claimed = claim_job_for_execution(job["id"])
    assert claimed is not None
    malformed = dict(claimed["run_claim"])
    claim_id = malformed["id"]
    malformed.pop(missing_field)
    update_job(job["id"], {"run_claim": malformed, "fire_claim": malformed})
    before = get_job(job["id"])

    if operation == "admission":
        monkeypatch.setattr(jobs, "_recurring_claim_stale_seconds", lambda ttl: 10.0)
        monotonic_values = iter((100.0, 111.0))
        monkeypatch.setattr(jobs.time, "monotonic", lambda: next(monotonic_values))
        assert claim_job_for_execution(job["id"], claim_ttl_seconds=0) is None
        assert claim_job_for_execution(job["id"], claim_ttl_seconds=0) is None
    elif operation == "renewal":
        assert renew_job_run_claim(job["id"], claim_id) is False
    else:
        assert mark_job_run(job["id"], True, run_claim_id=claim_id) is False

    assert get_job(job["id"]) == before


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("unexpected", "extra-field"),
        ("id", "not-a-uuid"),
        ("protocol", True),
        ("trigger_generation", True),
        ("trigger_generation", -1),
        ("at", "not-a-timestamp"),
        ("at", "2026-08-11T12:00:00"),
        ("by", ""),
        ("host", ""),
        ("pid", True),
        ("pid", -1),
    ],
)
@pytest.mark.parametrize("operation", ["admission", "renewal", "completion"])
def test_identical_v2_mirrors_with_invalid_immutable_schema_fail_closed(
    temp_home, monkeypatch, field, invalid_value, operation
):
    import cron.jobs as jobs
    from cron.jobs import (
        claim_job_for_execution,
        create_job,
        get_job,
        mark_job_run,
        renew_job_run_claim,
        update_job,
    )

    job = create_job(prompt="x", schedule="every 5m", name="invalid-claim-schema")
    claimed = claim_job_for_execution(job["id"])
    assert claimed is not None
    malformed = dict(claimed["run_claim"])
    claim_id = malformed["id"]
    malformed[field] = invalid_value
    update_job(job["id"], {"run_claim": malformed, "fire_claim": malformed})
    before = get_job(job["id"])

    if operation == "admission":
        monkeypatch.setattr(jobs, "_recurring_claim_stale_seconds", lambda ttl: 10.0)
        monotonic_values = iter((100.0, 111.0))
        monkeypatch.setattr(jobs.time, "monotonic", lambda: next(monotonic_values))
        assert claim_job_for_execution(job["id"], claim_ttl_seconds=0) is None
        assert claim_job_for_execution(job["id"], claim_ttl_seconds=0) is None
    elif operation == "renewal":
        assert renew_job_run_claim(job["id"], claim_id) is False
    else:
        assert mark_job_run(job["id"], True, run_claim_id=claim_id) is False

    assert get_job(job["id"]) == before


def test_completion_without_claim_and_token_fails_closed(temp_home):
    from cron.jobs import create_job, get_job, mark_job_run

    job = create_job(prompt="x", schedule="every 5m", name="unfenced-completion")
    before = get_job(job["id"])
    assert mark_job_run(job["id"], True, run_claim_id=None) is False
    assert get_job(job["id"]) == before


@pytest.mark.parametrize("corrupt", [None, "corrupt", ["corrupt"], 7])
def test_missing_or_nonobject_mirror_blocks_renewal_and_completion(temp_home, corrupt):
    from cron.jobs import (
        claim_job_for_execution,
        create_job,
        get_job,
        mark_job_run,
        renew_job_run_claim,
        update_job,
    )

    job = create_job(prompt="x", schedule="every 5m", name="scalar-mirror")
    claimed = claim_job_for_execution(job["id"])
    assert claimed is not None
    token = claimed["run_claim"]["id"]
    update_job(job["id"], {"fire_claim": corrupt})

    assert renew_job_run_claim(job["id"], token) is False
    assert mark_job_run(job["id"], True, run_claim_id=token) is False
    persisted = get_job(job["id"])
    assert persisted is not None
    assert persisted["run_claim"] == claimed["run_claim"]
    assert persisted["fire_claim"] == corrupt
    assert persisted.get("last_run_at") is None


def test_transient_heartbeat_store_error_keeps_live_owner_fenced(
    temp_home, monkeypatch
):
    import cron.jobs as jobs
    import cron.scheduler as scheduler
    from cron.jobs import claim_job_for_execution, create_job, get_job

    job = create_job(prompt="x", schedule="every 5m", name="flaky-heartbeat")
    claimed = claim_job_for_execution(job["id"])
    assert claimed is not None
    token = claimed["run_claim"]["id"]
    original_renew = scheduler.renew_job_run_claim
    renew_calls = [0]

    def flaky_renew(job_id, claim_id, **kwargs):
        renew_calls[0] += 1
        if renew_calls[0] == 2:
            raise OSError("transient test write failure")
        return original_renew(job_id, claim_id)

    monkeypatch.setattr(scheduler, "renew_job_run_claim", flaky_renew)
    monkeypatch.setattr(scheduler, "_RUN_CLAIM_HEARTBEAT_INTERVAL_SECONDS", 0.005)
    monkeypatch.setattr(jobs, "_recurring_claim_stale_seconds", lambda ttl: 0.03)
    stop, ownership_lost, worker = scheduler._start_run_claim_heartbeat(job["id"], token)
    assert stop is not None and ownership_lost is not None and worker is not None

    deadline = time.monotonic() + 1.0
    while renew_calls[0] < 4 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert renew_calls[0] >= 4
    assert worker.is_alive()
    assert claim_job_for_execution(job["id"], claim_ttl_seconds=0) is None
    time.sleep(0.05)
    assert claim_job_for_execution(job["id"], claim_ttl_seconds=0) is None
    current = get_job(job["id"])
    assert current is not None
    assert current["run_claim"]["heartbeat"] >= 3

    stop.set()
    worker.join(timeout=1.0)
    assert not worker.is_alive()


def test_initial_heartbeat_retries_transient_store_error(monkeypatch):
    import cron.scheduler as scheduler

    calls = [0]

    def flaky_renew(job_id, claim_id, **kwargs):
        calls[0] += 1
        if calls[0] == 1:
            raise OSError("transient initial write")
        return True

    monkeypatch.setattr(scheduler, "renew_job_run_claim", flaky_renew)
    stop, lost, worker = scheduler._start_run_claim_heartbeat("job", "token")
    assert calls[0] == 2
    assert stop is not None and lost is not None and worker is not None
    stop.set()
    worker.join(timeout=1.0)


def test_real_heartbeat_stops_on_baseexception_then_other_process_recovers(
    temp_home, monkeypatch
):
    import cron.scheduler as scheduler
    from cron.jobs import claim_job_for_execution, create_job, get_job

    job = create_job(prompt="x", schedule="every 5m", name="heartbeat-lifecycle")
    claimed = claim_job_for_execution(job["id"])
    assert claimed is not None
    token = claimed["run_claim"]["id"]
    monkeypatch.setattr(scheduler, "_RUN_CLAIM_HEARTBEAT_INTERVAL_SECONDS", 0.01)

    def interrupted(_job, *, defer_agent_teardown=None):
        time.sleep(0.06)
        raise KeyboardInterrupt("test interruption")

    monkeypatch.setattr(scheduler, "run_job", interrupted)
    with pytest.raises(KeyboardInterrupt):
        scheduler.run_one_job(claimed)

    stopped = get_job(job["id"])
    assert stopped is not None
    stopped_heartbeat = stopped["run_claim"]["heartbeat"]
    assert stopped_heartbeat >= 2
    time.sleep(0.05)
    unchanged = get_job(job["id"])
    assert unchanged is not None
    assert unchanged["run_claim"]["heartbeat"] == stopped_heartbeat

    code = """
import json
import time
import cron.jobs as jobs
jobs._recurring_claim_stale_seconds = lambda ttl: 0.05
first = jobs.claim_job_for_execution(%r, claim_ttl_seconds=0)
time.sleep(0.07)
second = jobs.claim_job_for_execution(%r, claim_ttl_seconds=0)
print(json.dumps({
    "first": first is not None,
    "second": second is not None,
    "token": (second or {}).get("run_claim", {}).get("id"),
}))
""" % (job["id"], job["id"])
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=os.getcwd(),
        env={**os.environ, "HERMES_HOME": str(temp_home)},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    recovered = json.loads(result.stdout.strip())
    assert recovered == {
        "first": False,
        "second": True,
        "token": recovered["token"],
    }
    assert recovered["token"] != token


def test_baseexception_waits_for_blocked_heartbeat_without_post_stop_write(
    temp_home, monkeypatch
):
    import cron.jobs as jobs
    import cron.scheduler as scheduler
    from cron.jobs import claim_job_for_execution, create_job, get_job

    job = create_job(prompt="x", schedule="every 5m", name="blocked-heartbeat")
    claimed = claim_job_for_execution(job["id"])
    assert claimed is not None
    token = claimed["run_claim"]["id"]
    monkeypatch.setattr(scheduler, "_RUN_CLAIM_HEARTBEAT_INTERVAL_SECONDS", 0.005)

    holder_release = threading.Event()
    holder_ready = threading.Event()
    renewal_blocked = threading.Event()
    renew_calls = [0]
    original_renew = jobs.renew_job_run_claim

    def tracked_renew(job_id, claim_id, **kwargs):
        renew_calls[0] += 1
        if renew_calls[0] >= 2:
            renewal_blocked.set()
        return original_renew(job_id, claim_id, **kwargs)

    monkeypatch.setattr(scheduler, "renew_job_run_claim", tracked_renew)

    def hold_store_lock():
        with jobs._jobs_lock():
            holder_ready.set()
            assert holder_release.wait(timeout=2.0)

    holder = threading.Thread(target=hold_store_lock, daemon=True)

    def interrupted(_job, *, defer_agent_teardown=None):
        holder.start()
        assert holder_ready.wait(timeout=1.0)
        assert renewal_blocked.wait(timeout=1.0)
        raise KeyboardInterrupt("blocked renewal interruption")

    monkeypatch.setattr(scheduler, "run_job", interrupted)
    outcome = []

    def run():
        try:
            scheduler.run_one_job(claimed)
        except BaseException as exc:
            outcome.append(exc)

    runner = threading.Thread(target=run)
    runner.start()
    assert renewal_blocked.wait(timeout=1.0)
    time.sleep(0.02)
    assert runner.is_alive(), "cleanup must wait for the blocked heartbeat worker"
    expected_heartbeat = claimed["run_claim"]["heartbeat"] + 1
    holder_release.set()
    holder.join(timeout=1.0)
    runner.join(timeout=1.0)
    assert not runner.is_alive()
    assert isinstance(outcome[0], KeyboardInterrupt)
    persisted = get_job(job["id"])
    assert persisted is not None
    assert persisted["run_claim"]["id"] == token
    # The blocked renewal was already queued on the store lock before cleanup.
    # It may linearize first, but cancellation then acquires the same lock and
    # fences every write before run_one_job returns.
    assert persisted["run_claim"]["heartbeat"] in {
        expected_heartbeat,
        expected_heartbeat + 1,
    }


def test_heartbeat_cancel_is_linearized_with_final_persistent_write(
    temp_home, monkeypatch
):
    import cron.jobs as jobs

    job = jobs.create_job(prompt="x", schedule="every 5m", name="cancel-fence")
    claimed = jobs.claim_job_for_execution(job["id"])
    assert claimed is not None
    token = claimed["run_claim"]["id"]
    cancel_event = threading.Event()
    save_entered = threading.Event()
    save_release = threading.Event()
    original_save = jobs.save_jobs

    def blocking_save(records):
        save_entered.set()
        assert save_release.wait(timeout=2.0)
        original_save(records)

    monkeypatch.setattr(jobs, "save_jobs", blocking_save)
    renewal_result = []
    renewal = threading.Thread(
        target=lambda: renewal_result.append(
            jobs.renew_job_run_claim(
                job["id"], token, cancel_event=cancel_event
            )
        )
    )
    renewal.start()
    assert save_entered.wait(timeout=1.0)

    cancellation = threading.Thread(
        target=jobs.cancel_job_run_claim_heartbeat,
        args=(cancel_event,),
    )
    cancellation.start()
    time.sleep(0.02)
    assert not cancel_event.is_set(), (
        "cancellation must wait for an already-linearized persistent renewal"
    )

    save_release.set()
    renewal.join(timeout=1.0)
    cancellation.join(timeout=1.0)
    assert renewal_result == [True]
    assert cancel_event.is_set()

    persisted = jobs.get_job(job["id"])
    assert persisted is not None
    heartbeat_after_cancel = persisted["run_claim"]["heartbeat"]
    assert jobs.renew_job_run_claim(
        job["id"], token, cancel_event=cancel_event
    ) is False
    after_rejected_renewal = jobs.get_job(job["id"])
    assert after_rejected_renewal is not None
    assert (
        after_rejected_renewal["run_claim"]["heartbeat"]
        == heartbeat_after_cancel
    )


def test_observer_cache_tracks_all_persisted_claims_without_starving_overflow(
    temp_home, monkeypatch
):
    import cron.jobs as jobs

    monkeypatch.setattr(jobs, "_recurring_claim_stale_seconds", lambda ttl: 10.0)
    clock = [0.0]
    monkeypatch.setattr(jobs.time, "monotonic", lambda: clock[0])
    claims = [
        {"id": f"claim-{index}", "heartbeat": 0, "protocol": 2}
        for index in range(5000)
    ]
    persisted = [
        {"id": f"job-{index}", "run_claim": claim, "fire_claim": claim}
        for index, claim in enumerate(claims)
    ]
    with jobs._run_claim_observations_lock:
        jobs._run_claim_observations.clear()

    jobs._sync_run_claim_observations(persisted)
    assert all(jobs._recurring_claim_blocks_reclaim(claim, 0) for claim in claims)
    with jobs._run_claim_observations_lock:
        assert len(jobs._run_claim_observations) == len(persisted)
        jobs._run_claim_observations["removed-claim"] = ("opaque", 0.0, 0.0)

    # Store synchronization removes observations for claims no longer persisted,
    # bounding memory by current jobs.json claim cardinality rather than a fixed
    # admission ceiling that can permanently starve overflow claims.
    jobs._sync_run_claim_observations(persisted[:2])
    with jobs._run_claim_observations_lock:
        assert set(jobs._run_claim_observations) == {"claim-0", "claim-1"}

    clock[0] = 11.0
    assert all(
        not jobs._recurring_claim_blocks_reclaim(claim, 0)
        for claim in claims[:2]
    )
    with jobs._run_claim_observations_lock:
        assert jobs._run_claim_observations == {}


def test_remote_recurring_owner_fails_closed_across_clock_skew(temp_home):
    """A remote owner is not declared dead from this machine's wall clock."""
    from cron.jobs import claim_job_for_fire, create_job, get_job, update_job

    job = create_job(prompt="x", schedule="every 5m", name="remote")
    assert claim_job_for_fire(job["id"]) is True
    current = get_job(job["id"])
    assert current is not None
    remote = dict(current["run_claim"])
    remote.update({
        "host": "remote-host.example",
        "pid": 1234,
        "at": "2000-01-01T00:00:00+00:00",
    })
    update_job(job["id"], {"run_claim": remote, "fire_claim": remote})
    assert claim_job_for_fire(job["id"], claim_ttl_seconds=0) is False


def test_protocol_v1_crash_residue_requires_explicit_post_drain_migration(
    temp_home,
):
    from cron.jobs import (
        claim_job_for_execution,
        create_job,
        migrate_legacy_run_claims_after_drain,
        update_job,
    )

    job = create_job(prompt="x", schedule="every 5m", name="v1-residue")
    legacy = {"at": "2000-01-01T00:00:00+00:00", "by": "old-v1"}
    update_job(job["id"], {"run_claim": legacy, "fire_claim": legacy})

    assert claim_job_for_execution(job["id"], claim_ttl_seconds=0) is None
    assert migrate_legacy_run_claims_after_drain() == [job["id"]]
    migrated = claim_job_for_execution(job["id"], claim_ttl_seconds=0)
    assert migrated is not None
    assert migrated["run_claim"]["protocol"] == 2
    assert migrated["run_claim"]["id"]


def test_v1_migration_preserves_current_and_future_protocol_claims(temp_home):
    from cron.jobs import (
        create_job,
        get_job,
        migrate_legacy_run_claims_after_drain,
        update_job,
    )

    job = create_job(prompt="x", schedule="every 5m", name="mixed-protocol")
    v2 = {"protocol": 2, "id": "current", "heartbeat": 1}
    future = {"protocol": 3, "id": "future", "heartbeat": 1}
    update_job(job["id"], {"run_claim": v2, "fire_claim": future})

    assert migrate_legacy_run_claims_after_drain() == []
    persisted = get_job(job["id"])
    assert persisted is not None
    assert persisted["run_claim"] == v2
    assert persisted["fire_claim"] == future


@pytest.mark.parametrize("protocol", [True, 1.0, None])
def test_v1_migration_preserves_non_integer_v1_lookalikes(temp_home, protocol):
    from cron.jobs import (
        create_job,
        get_job,
        migrate_legacy_run_claims_after_drain,
        update_job,
    )

    job = create_job(prompt="x", schedule="every 5m", name="v1-lookalike")
    lookalike = {"protocol": protocol, "id": "future-like"}
    update_job(job["id"], {"run_claim": lookalike, "fire_claim": lookalike})
    assert migrate_legacy_run_claims_after_drain() == []
    persisted = get_job(job["id"])
    assert persisted is not None
    assert persisted["run_claim"] == lookalike


def test_v1_migration_clears_only_legacy_field_in_mixed_job(temp_home):
    from cron.jobs import (
        create_job,
        get_job,
        migrate_legacy_run_claims_after_drain,
        update_job,
    )

    job = create_job(prompt="x", schedule="every 5m", name="legacy-and-current")
    legacy = {"protocol": 1, "id": "legacy"}
    current = {"protocol": 2, "id": "current", "heartbeat": 1}
    update_job(job["id"], {"run_claim": legacy, "fire_claim": current})

    assert migrate_legacy_run_claims_after_drain() == [job["id"]]
    persisted = get_job(job["id"])
    assert persisted is not None
    assert persisted["run_claim"] is None
    assert persisted["fire_claim"] == current


def test_oneshot_uses_monotonic_heartbeat_crash_recovery(
    temp_home, monkeypatch
):
    import cron.jobs as jobs
    from cron.jobs import claim_job_for_fire, create_job

    job = create_job(prompt="x", schedule="30m", name="oneshot-recovery")
    assert claim_job_for_fire(job["id"]) is True
    monkeypatch.setattr(jobs, "_recurring_claim_stale_seconds", lambda ttl: 10.0)
    monotonic_values = iter((100.0, 111.0))
    monkeypatch.setattr(jobs.time, "monotonic", lambda: next(monotonic_values))
    assert claim_job_for_fire(job["id"], claim_ttl_seconds=0) is False
    assert claim_job_for_fire(job["id"], claim_ttl_seconds=0) is True


def test_builtin_and_external_paths_share_one_claim(temp_home):
    """A built-in predecessor blocks external/manual fire, and vice versa."""
    from cron.jobs import (
        claim_job_for_fire,
        create_job,
        get_due_jobs,
        trigger_job,
    )

    builtin = create_job(prompt="x", schedule="every 5m", name="builtin")
    trigger_job(builtin["id"])
    assert [job["id"] for job in get_due_jobs()] == [builtin["id"]]
    assert claim_job_for_fire(builtin["id"]) is False

    external = create_job(prompt="x", schedule="every 5m", name="external")
    assert claim_job_for_fire(external["id"]) is True
    trigger_job(external["id"])
    assert all(job["id"] != external["id"] for job in get_due_jobs())


def test_builtin_claim_blocks_external_fire_in_second_process(temp_home):
    """The unified claim is persisted, not merely process-local state."""
    from cron.jobs import create_job, get_due_jobs, trigger_job

    job = create_job(prompt="x", schedule="every 5m", name="cross-process")
    trigger_job(job["id"])
    due = get_due_jobs()
    assert [candidate["id"] for candidate in due] == [job["id"]]

    env = dict(os.environ, HERMES_HOME=str(temp_home), PYTHONPATH=os.getcwd())
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from cron.jobs import claim_job_for_fire; "
                f"print(claim_job_for_fire({job['id']!r}))"
            ),
        ],
        cwd=os.getcwd(),
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    assert result.stdout.strip() == "False"


def test_stale_completion_cannot_clear_successor_claim(temp_home):
    """A fenced predecessor cannot mutate state or release a replacement owner."""
    from cron.jobs import (
        claim_job_for_fire,
        create_job,
        get_job,
        mark_job_run,
        update_job,
    )

    job = create_job(prompt="x", schedule="every 5m", name="fenced")
    assert claim_job_for_fire(job["id"]) is True
    claimed = get_job(job["id"])
    assert claimed is not None
    predecessor = claimed["run_claim"]
    successor = dict(predecessor, id="successor-token")
    update_job(job["id"], {"run_claim": successor, "fire_claim": successor})

    assert mark_job_run(
        job["id"], success=True, run_claim_id=predecessor["id"]
    ) is False
    current = get_job(job["id"])
    assert current["run_claim"] == successor
    assert current["fire_claim"] == successor
    assert current.get("last_run_at") is None


def test_mark_job_run_clears_claim(temp_home):
    """After a recurring job completes, its claim is cleared so the next fire
    can be claimed again."""
    from cron.jobs import create_job, claim_job_for_fire, mark_job_run, get_job

    job = create_job(prompt="x", schedule="every 5m", name="c")
    jid = job["id"]
    assert claim_job_for_fire(jid) is True
    claimed = get_job(jid)
    assert claimed is not None
    claim = claimed["run_claim"]
    assert claim["id"]
    assert claimed.get("fire_claim") == claim

    assert mark_job_run(jid, success=True, run_claim_id=claim["id"]) is True
    completed = get_job(jid)
    assert completed is not None
    assert completed.get("run_claim") is None
    assert completed.get("fire_claim") is None
    # …and the re-armed recurring job is claimable again.
    assert claim_job_for_fire(jid) is True
