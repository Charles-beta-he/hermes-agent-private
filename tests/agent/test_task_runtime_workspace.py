from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import hashlib
import io
import json
import subprocess
import sys
import tarfile

import psutil
import pytest

import agent.task_runtime as runtime_module
from agent.task_runtime import _candidate_archive_manifest
from agent.task_runtime_process import ProcessIdentity, run_owned_process
from agent.task_runtime_workspace import (
    allocate_workspace,
    freeze_workspace_candidate,
    reap_workspace,
    renew_workspace_lease,
    snapshot_workspace_candidate,
)


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Runtime Test"], cwd=repo, check=True)
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "seed.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return repo, head


def _allocate(tmp_path: Path):
    repo, head = _repo(tmp_path)
    root = tmp_path / "managed"
    lease = allocate_workspace(
        managed_root=root,
        source_repo=repo,
        task_id="task-1",
        slice_id="slice-1",
        packet_id="packet-1",
        lease_token="fence-1",
        base_commit=head,
        lease_seconds=60,
        now=100.0,
    )
    return repo, root, lease


def test_allocator_is_idempotent_and_renewal_is_fenced(tmp_path):
    repo, root, lease = _allocate(tmp_path)
    workspace = Path(lease.workspace_path)

    assert workspace.is_dir()
    assert (workspace / ".git").is_file()
    assert lease.base_commit == subprocess.run(
        ["git", "-C", str(workspace), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    same = allocate_workspace(
        managed_root=root,
        source_repo=repo,
        task_id=lease.task_id,
        slice_id=lease.slice_id,
        packet_id=lease.packet_id,
        lease_token=lease.lease_token,
        base_commit=lease.base_commit,
        lease_seconds=60,
        now=101.0,
    )
    assert same == lease

    renewed = renew_workspace_lease(
        lease, managed_root=root, lease_seconds=120, now=110.0
    )
    assert renewed.expires_at == 230.0
    with pytest.raises(ValueError, match="workspace_lease_fence_mismatch"):
        renew_workspace_lease(
            lease, managed_root=root, lease_seconds=120, now=111.0
        )


def test_candidate_snapshot_binds_modes_bytes_symlinks_and_deletes(tmp_path):
    _, _, lease = _allocate(tmp_path)
    workspace = Path(lease.workspace_path)
    (workspace / "seed.txt").unlink()
    executable = workspace / "run.sh"
    executable.write_bytes(b"#!/bin/sh\nprintf ok\\n")
    executable.chmod(0o755)
    (workspace / "link").symlink_to("run.sh")

    changed = ["seed.txt", "run.sh", "link"]
    observed = snapshot_workspace_candidate(lease, changed)
    expected = hashlib.sha256()
    expected.update(b"0o120000\0link\0" + str(len(b"run.sh")).encode() + b"\0run.sh")
    script = executable.read_bytes()
    expected.update(b"0o755\0run.sh\0" + str(len(script)).encode() + b"\0" + script)
    expected.update(b"D\0seed.txt\0" + b"0\0")
    assert observed == "sha256:" + expected.hexdigest()
    assert snapshot_workspace_candidate(lease, list(reversed(changed))) == observed


def test_candidate_snapshot_uses_review_recipe_octal_mode_spelling(tmp_path):
    _, _, lease = _allocate(tmp_path)
    workspace = Path(lease.workspace_path)
    (workspace / "seed.txt").write_bytes(b"changed\n")

    observed = snapshot_workspace_candidate(lease, ["seed.txt"])
    expected = hashlib.sha256()
    expected.update(b"0o644\0seed.txt\0" + str(len(b"changed\n")).encode() + b"\0changed\n")

    assert observed == "sha256:" + expected.hexdigest()


def test_candidate_snapshot_rejects_unowned_or_unsafe_write_sets(tmp_path):
    _, _, lease = _allocate(tmp_path)
    workspace = Path(lease.workspace_path)
    (workspace / "extra.txt").write_text("extra\n", encoding="utf-8")

    with pytest.raises(ValueError, match="workspace_write_set_mismatch"):
        snapshot_workspace_candidate(lease, [])
    with pytest.raises(ValueError, match="workspace_changed_path_invalid"):
        snapshot_workspace_candidate(lease, ["../extra.txt"])
    with pytest.raises(ValueError, match="workspace_changed_path_invalid"):
        snapshot_workspace_candidate(lease, [str(workspace / "extra.txt")])
    with pytest.raises(ValueError, match="workspace_changed_path_duplicate"):
        snapshot_workspace_candidate(lease, ["extra.txt", "extra.txt"])


def test_candidate_freeze_archives_exact_content_then_reaps_workspace(tmp_path):
    _, root, lease = _allocate(tmp_path)
    workspace = Path(lease.workspace_path)
    (workspace / "new.txt").write_text("candidate\n", encoding="utf-8")
    process = run_owned_process(
        [sys.executable, "-c", "pass"], cwd=workspace, timeout_seconds=2
    )

    def freeze_once():
        return freeze_workspace_candidate(
            lease,
            managed_root=root,
            changed_artifacts=["new.txt"],
            process_receipt=process.receipt,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(lambda _: freeze_once(), range(2)))
    receipt = receipts[0]
    assert receipts[1] == receipt

    archive = Path(receipt.archive_path)
    assert receipt.candidate_digest.startswith("sha256:")
    assert archive.is_file()
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == receipt.archive_sha256
    assert receipt.workspace_reap_receipt["removed"] is True
    assert not workspace.exists()
    listing = subprocess.run(
        ["tar", "-tf", str(archive)], check=True, capture_output=True, text=True
    ).stdout.splitlines()
    assert listing == ["files/new.txt", "MANIFEST.json"]

    replay = freeze_workspace_candidate(
        lease,
        managed_root=root,
        changed_artifacts=["new.txt"],
        process_receipt=process.receipt,
    )
    assert replay == receipt
    with pytest.raises(ValueError, match="workspace_candidate_receipt_binding_mismatch"):
        freeze_workspace_candidate(
            replace(lease, lease_token="stale-fence"),
            managed_root=root,
            changed_artifacts=["new.txt"],
            process_receipt=process.receipt,
        )
    with pytest.raises(ValueError, match="workspace_candidate_receipt_binding_mismatch"):
        freeze_workspace_candidate(
            lease,
            managed_root=root,
            changed_artifacts=["other.txt"],
            process_receipt=process.receipt,
        )
    receipt_path = archive.with_suffix(".json")
    receipt_path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="workspace_candidate_receipt_invalid"):
        freeze_workspace_candidate(
            lease,
            managed_root=root,
            changed_artifacts=["new.txt"],
            process_receipt=process.receipt,
        )
    receipt_path.unlink()
    with pytest.raises(ValueError, match="workspace_candidate_incomplete_freeze"):
        freeze_workspace_candidate(
            lease,
            managed_root=root,
            changed_artifacts=["new.txt"],
            process_receipt=process.receipt,
        )


def test_reaper_requires_process_receipt_and_removes_exact_owned_workspace(tmp_path):
    _, root, lease = _allocate(tmp_path)
    workspace = Path(lease.workspace_path)
    result = run_owned_process(
        [sys.executable, "-c", "print('done')"],
        cwd=workspace,
        timeout_seconds=2,
    )

    receipt = reap_workspace(
        lease, managed_root=root, process_receipt=result.receipt
    )
    assert receipt.removed is True
    assert not workspace.exists()
    assert not list((root / "leases").glob("*.json"))


@pytest.mark.live_system_guard_bypass
def test_reaper_refuses_live_workspace_writer_then_succeeds_after_readback(tmp_path):
    _, root, lease = _allocate(tmp_path)
    workspace = Path(lease.workspace_path)
    proof = run_owned_process(
        [sys.executable, "-c", "pass"], cwd=workspace, timeout_seconds=2
    ).receipt
    writer = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=workspace,
        start_new_session=True,
    )
    try:
        writer_identity = ProcessIdentity(
            writer.pid, psutil.Process(writer.pid).create_time()
        )
        contradictory_proof = replace(
            proof,
            observed_descendants=(*proof.observed_descendants, writer_identity),
        )
        with pytest.raises(ValueError, match="workspace_residual_writers_present"):
            reap_workspace(
                lease, managed_root=root, process_receipt=contradictory_proof
            )
    finally:
        writer.terminate()
        writer.wait(timeout=5)

    receipt = reap_workspace(lease, managed_root=root, process_receipt=proof)
    assert receipt.removed is True
    assert not workspace.exists()


def test_candidate_archive_symlink_fails_closed(tmp_path):
    target = tmp_path / "target.tar"
    target.write_bytes(b"not-a-tar")
    link = tmp_path / "candidate.tar"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="workspace_candidate_archive_invalid"):
        _candidate_archive_manifest(link)


def test_candidate_archive_size_limit_fails_closed(tmp_path, monkeypatch):
    archive = tmp_path / "candidate.tar"
    archive.write_bytes(b"too-large")
    monkeypatch.setattr(runtime_module, "_MAX_CANDIDATE_ARCHIVE_BYTES", 1)
    with pytest.raises(ValueError, match="workspace_candidate_archive_size_invalid"):
        _candidate_archive_manifest(archive)


@pytest.mark.parametrize(
    "variant,error",
    [
        ("duplicate", "workspace_candidate_archive_members_invalid"),
        ("unexpected", "workspace_candidate_archive_members_invalid"),
        ("hardlink", "workspace_candidate_archive_member_invalid"),
        ("unsafe_path", "workspace_candidate_manifest_paths_invalid"),
    ],
)
def test_candidate_archive_schema_fails_closed(tmp_path, variant, error):
    archive = tmp_path / f"{variant}.tar"
    changed_path = "../escape" if variant == "unsafe_path" else "a.txt"
    manifest = {"changed_artifacts": [changed_path], "deleted_artifacts": []}
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
    with tarfile.open(archive, mode="w") as bundle:
        member = tarfile.TarInfo("MANIFEST.json")
        member.size = len(manifest_bytes)
        member.mode = 0o600
        bundle.addfile(member, io.BytesIO(manifest_bytes))
        file_name = f"files/{changed_path}"
        if variant == "hardlink":
            file_member = tarfile.TarInfo(file_name)
            file_member.type = tarfile.LNKTYPE
            file_member.linkname = "files/other.txt"
            bundle.addfile(file_member)
        else:
            content = b"content\n"
            file_member = tarfile.TarInfo(file_name)
            file_member.size = len(content)
            file_member.mode = 0o644
            bundle.addfile(file_member, io.BytesIO(content))
            if variant == "duplicate":
                duplicate = tarfile.TarInfo(file_name)
                duplicate.size = len(content)
                duplicate.mode = 0o644
                bundle.addfile(duplicate, io.BytesIO(content))
        if variant == "unexpected":
            extra = tarfile.TarInfo("files/extra.txt")
            extra.size = 1
            extra.mode = 0o644
            bundle.addfile(extra, io.BytesIO(b"x"))
    with pytest.raises(ValueError, match=error):
        _candidate_archive_manifest(archive)
