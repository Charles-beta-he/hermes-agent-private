from __future__ import annotations

import os
from pathlib import Path
import sys
import time

import psutil
import pytest

from agent.task_runtime_process import run_owned_process


def _pid_is_same(pid: int, create_time: float) -> bool:
    try:
        process = psutil.Process(pid)
        return abs(process.create_time() - create_time) < 0.001 and process.is_running()
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False


def test_owned_process_success_is_reaped_and_output_is_bounded(tmp_path):
    result = run_owned_process(
        [sys.executable, "-c", "print('owned-ok')"],
        cwd=tmp_path,
        timeout_seconds=2,
    )

    assert result.stdout.strip() == "owned-ok"
    assert result.receipt.exit_code == 0
    assert result.receipt.timed_out is False
    assert result.receipt.leader_reaped is True
    assert result.receipt.residual_probe_succeeded is True
    assert result.receipt.residual_pids == ()
    assert not _pid_is_same(
        result.receipt.leader_pid, result.receipt.leader_start_time
    )


def test_timeout_hard_kills_process_group_and_stops_residual_writer(tmp_path):
    heartbeat = tmp_path / "heartbeat.txt"
    program = """
import pathlib, signal, time
path = pathlib.Path('heartbeat.txt')
signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    path.write_text(str(time.time()))
    time.sleep(0.02)
"""
    result = run_owned_process(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        timeout_seconds=0.2,
        term_grace_seconds=0.05,
    )

    assert result.receipt.timed_out is True
    assert result.receipt.term_sent is True
    assert result.receipt.kill_sent is True
    assert result.receipt.leader_reaped is True
    assert result.receipt.residual_probe_succeeded is True
    assert result.receipt.residual_pids == ()
    before = heartbeat.read_text()
    time.sleep(0.08)
    assert heartbeat.read_text() == before


@pytest.mark.live_system_guard_bypass
def test_timeout_reaps_descendant_that_escapes_process_group(tmp_path):
    child_record = tmp_path / "child.txt"
    child_program = """
import os, pathlib, time
os.setsid()
pathlib.Path('child.txt').write_text(f'{os.getpid()} {__import__("psutil").Process().create_time()}')
while True:
    time.sleep(1)
"""
    parent_program = f"""
import subprocess, sys, time
subprocess.Popen([sys.executable, '-c', {child_program!r}], close_fds=True)
time.sleep(30)
"""
    result = run_owned_process(
        [sys.executable, "-c", parent_program],
        cwd=tmp_path,
        timeout_seconds=0.4,
        term_grace_seconds=0.05,
    )

    assert child_record.exists()
    child_pid_text, child_start_text = child_record.read_text().split()
    assert any(item.pid == int(child_pid_text) for item in result.receipt.observed_descendants)
    assert result.receipt.timed_out is True
    assert result.receipt.leader_reaped is True
    assert result.receipt.residual_probe_succeeded is True
    assert result.receipt.residual_pids == ()
    assert not _pid_is_same(int(child_pid_text), float(child_start_text))
