from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from hermes_cli.change_provenance import (
    ProvenanceError,
    build_candidate_change_request,
    canonical_json_bytes,
    sha256_hex,
    verify_candidate_change,
)


NOW = "2026-08-25T12:00:00Z"
EXPIRES = "2026-09-25T12:00:00Z"
HEX64 = "a" * 64
REPO = Path(__file__).resolve().parents[2]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout.strip()


def _linear_history() -> tuple[str, list[str]]:
    commits = _git("rev-list", "--first-parent", "HEAD").splitlines()
    for offset in range(len(commits) - 2):
        newest, middle = commits[offset], commits[offset + 1]
        baseline = commits[offset + 2]
        if len(_git("show", "-s", "--format=%P", newest).split()) == 1 and len(
            _git("show", "-s", "--format=%P", middle).split()
        ) == 1:
            return baseline, [middle, newest]
    raise AssertionError("repository lacks two consecutive non-merge commits")


def _external_key(tmp_path: Path) -> tuple[Path, bytes]:
    private_path = tmp_path / "issuer-private" / "key.seed"
    public_path = tmp_path / "issuer-public.key"
    private_path.parent.mkdir(mode=0o700)
    code = """
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import sys
key = Ed25519PrivateKey.generate()
Path(sys.argv[1]).write_bytes(key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()))
Path(sys.argv[2]).write_bytes(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
"""
    subprocess.run([sys.executable, "-c", code, str(private_path), str(public_path)], check=True)
    private_path.chmod(0o600)
    return private_path, public_path.read_bytes()


def _external_signature(private_path: Path, body: dict[str, object], tmp_path: Path) -> str:
    body_path = tmp_path / f"body-{os.urandom(6).hex()}.json"
    body_path.write_bytes(_canonical(body))
    code = """
import base64, sys
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
key = Ed25519PrivateKey.from_private_bytes(Path(sys.argv[1]).read_bytes())
print(base64.b64encode(key.sign(Path(sys.argv[2]).read_bytes())).decode('ascii'))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(private_path), str(body_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    body_path.unlink()
    return result.stdout.strip()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _diff_digest(parent: str, candidate: str) -> str:
    raw = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "diff.external=",
            "-c",
            "diff.trustExitCode=false",
            "diff-tree",
            "--binary",
            "--full-index",
            "--no-color",
            "--no-ext-diff",
            parent,
            candidate,
            "--",
        ],
        cwd=REPO,
        check=True,
        capture_output=True,
    ).stdout
    return hashlib.sha256(raw).hexdigest()


class Fixture:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.private_path, public = _external_key(tmp_path)
        self.baseline, self.commits = _linear_history()
        self.principal = "1" * 64
        self.session = "2" * 64
        self.authority = tmp_path / "issuer-authority"
        self.authority.mkdir()
        self.bundle = {
            "schema_version": "ProvenanceTrustBundleV1",
            "run_id": "HTDA-S1-20260820",
            "baseline_commit": self.baseline,
            "baseline_tree": _git("show", "-s", "--format=%T", self.baseline),
            "expires_at": EXPIRES,
            "issuer": {
                "algorithm": "Ed25519",
                "key_id": "ed25519:sha256:" + hashlib.sha256(public).hexdigest(),
                "public_key_b64": base64.b64encode(public).decode("ascii"),
                "service_digest_sha256": "3" * 64,
            },
            "authorized_producer_sessions": [
                {"principal_digest": self.principal, "session_digest": self.session}
            ],
            "receipt_contract": {
                "schema": "ChangeProvenanceReceiptV1",
                "canonicalization": "utf8-json-sort-keys-no-whitespace-v1",
                "digest": "sha256",
            },
            "transport": {
                "kind": "darwin-unix-stream-v1",
                "socket_path": str(self.authority / "issuer.sock"),
                "expected_client_uid": os.getuid(),
            },
            "issuer_log": {
                "receipt_log_path": str(self.authority / "receipts.jsonl"),
                "anchor_log_path": str(self.authority / "receipts.anchor.jsonl"),
                "checkpoint_path": str(self.authority / "checkpoint.json"),
            },
        }
        self.bundle_path = tmp_path / "trust-bundle.json"
        self.bundle_path.write_bytes(_canonical(self.bundle) + b"\n")
        self.bundle_path.chmod(0o444)
        self.bundle_digest = hashlib.sha256(_canonical(self.bundle)).hexdigest()

    def receipt(
        self,
        index: int,
        *,
        previous: str | None,
        seq: int | None = None,
        commit: str | None = None,
        parent: str | None = None,
        diff_digest: str | None = None,
        principal: str | None = None,
        session: str | None = None,
    ) -> dict[str, object]:
        candidate = commit or self.commits[index]
        actual_parent = parent or _git("show", "-s", "--format=%P", candidate).split()[0]
        body = {
            "schema_version": "ChangeProvenanceReceiptV1",
            "trust_bundle_digest": self.bundle_digest,
            "run_id": self.bundle["run_id"],
            "monotonic_seq": index + 1 if seq is None else seq,
            "prev_receipt_digest": previous,
            "baseline_commit": self.baseline,
            "parent_commit": actual_parent,
            "candidate_commit": candidate,
            "candidate_tree": _git("show", "-s", "--format=%T", candidate),
            "canonical_diff_digest": diff_digest or _diff_digest(actual_parent, candidate),
            "producer_principal_digest": principal or self.principal,
            "producer_session_digest": session or self.session,
            "issued_at": NOW,
        }
        return self.sign("SignedChangeProvenanceReceiptV1", "receipt", body)

    def sign(self, schema: str, body_name: str, body: dict[str, object]) -> dict[str, object]:
        digest_name = "receipt_digest" if body_name == "receipt" else "checkpoint_digest"
        return {
            "schema_version": schema,
            "key_id": self.bundle["issuer"]["key_id"],
            body_name: body,
            digest_name: hashlib.sha256(_canonical(body)).hexdigest(),
            "signature_b64": _external_signature(self.private_path, body, self.tmp_path),
        }

    def valid_chain(self) -> tuple[list[dict[str, object]], dict[str, object]]:
        first = self.receipt(0, previous=None)
        second = self.receipt(1, previous=first["receipt_digest"])
        receipts = [first, second]
        checkpoint_body = {
            "schema_version": "ProvenanceCheckpointV1",
            "trust_bundle_digest": self.bundle_digest,
            "run_id": self.bundle["run_id"],
            "final_sequence": 2,
            "final_receipt_digest": second["receipt_digest"],
            "candidate_commit": self.commits[-1],
            "candidate_tree": _git("show", "-s", "--format=%T", self.commits[-1]),
            "complete_producer_set_digest": hashlib.sha256(
                _canonical(
                    [
                        {
                            "producer_principal_digest": self.principal,
                            "producer_session_digest": self.session,
                        }
                    ]
                )
            ).hexdigest(),
            "issued_at": NOW,
        }
        return receipts, self.sign(
            "SignedProvenanceCheckpointV1", "checkpoint", checkpoint_body
        )

    def write_evidence(
        self, receipts: list[dict[str, object]], checkpoint: dict[str, object]
    ) -> tuple[Path, Path]:
        receipt_path = self.tmp_path / "receipt-copy.jsonl"
        checkpoint_path = self.tmp_path / "checkpoint-copy.json"
        if receipt_path.exists():
            receipt_path.chmod(0o644)
        if checkpoint_path.exists():
            checkpoint_path.chmod(0o644)
        receipt_path.write_bytes(b"".join(_canonical(value) + b"\n" for value in receipts))
        checkpoint_path.write_bytes(_canonical(checkpoint) + b"\n")
        receipt_path.chmod(0o444)
        checkpoint_path.chmod(0o444)
        return receipt_path, checkpoint_path

    def verify(
        self,
        receipts: list[dict[str, object]],
        checkpoint: dict[str, object],
        **overrides: object,
    ) -> dict[str, object]:
        receipt_path, checkpoint_path = self.write_evidence(receipts, checkpoint)
        arguments = {
            "repo": REPO,
            "bundle_path": self.bundle_path,
            "approved_bundle_digest": self.bundle_digest,
            "receipts_path": receipt_path,
            "checkpoint_path": checkpoint_path,
            "candidate_commit": self.commits[-1],
            "now": NOW,
        }
        arguments.update(overrides)
        return verify_candidate_change(**arguments)


@pytest.fixture
def provenance(tmp_path: Path) -> Fixture:
    return Fixture(tmp_path)


def test_canonical_bytes_and_digest_are_exact() -> None:
    value = {"z": "é", "a": [2, 1]}
    expected = b'{"a":[2,1],"z":"\xc3\xa9"}'
    assert canonical_json_bytes(value) == expected
    assert sha256_hex(expected) == hashlib.sha256(expected).hexdigest()
    with pytest.raises((TypeError, ValueError)):
        canonical_json_bytes({"bad": float("nan")})


def test_valid_chain_has_exact_first_parent_coverage_and_terminal_match(
    provenance: Fixture,
) -> None:
    receipts, checkpoint = provenance.valid_chain()
    result = provenance.verify(receipts, checkpoint)
    assert result == {
        "status": "PASS",
        "trust_bundle_digest": provenance.bundle_digest,
        "candidate_commit": provenance.commits[-1],
        "candidate_tree": _git("show", "-s", "--format=%T", provenance.commits[-1]),
        "receipt_count": 2,
        "final_receipt_digest": receipts[-1]["receipt_digest"],
        "complete_producer_set_digest": checkpoint["checkpoint"][
            "complete_producer_set_digest"
        ],
    }


@pytest.mark.parametrize(
    ("target", "extra"),
    [("bundle", "extra"), ("receipt", "extra"), ("checkpoint", "extra")],
)
def test_schemas_are_closed(
    provenance: Fixture, target: str, extra: str
) -> None:
    receipts, checkpoint = provenance.valid_chain()
    if target == "bundle":
        provenance.bundle[extra] = True
        provenance.bundle_path.chmod(0o644)
        provenance.bundle_path.write_bytes(_canonical(provenance.bundle) + b"\n")
        provenance.bundle_path.chmod(0o444)
        provenance.bundle_digest = hashlib.sha256(_canonical(provenance.bundle)).hexdigest()
    elif target == "receipt":
        receipts[0]["receipt"][extra] = True
        receipts[0] = provenance.sign(
            "SignedChangeProvenanceReceiptV1", "receipt", receipts[0]["receipt"]
        )
    else:
        checkpoint["checkpoint"][extra] = True
        checkpoint = provenance.sign(
            "SignedProvenanceCheckpointV1", "checkpoint", checkpoint["checkpoint"]
        )
    with pytest.raises(ProvenanceError, match="closed|schema"):
        provenance.verify(receipts, checkpoint)


def test_expired_bundle_is_rejected(provenance: Fixture) -> None:
    receipts, checkpoint = provenance.valid_chain()
    with pytest.raises(ProvenanceError, match="expired"):
        provenance.verify(receipts, checkpoint, now=EXPIRES)


def test_changed_or_unknown_issuer_bundle_is_rejected(provenance: Fixture) -> None:
    receipts, checkpoint = provenance.valid_chain()
    provenance.bundle["issuer"]["service_digest_sha256"] = "9" * 64
    provenance.bundle_path.chmod(0o644)
    provenance.bundle_path.write_bytes(_canonical(provenance.bundle) + b"\n")
    provenance.bundle_path.chmod(0o444)
    with pytest.raises(ProvenanceError, match="approval digest"):
        provenance.verify(receipts, checkpoint)


def test_unknown_key_and_forged_signature_are_rejected(provenance: Fixture) -> None:
    receipts, checkpoint = provenance.valid_chain()
    unknown = deepcopy(receipts)
    unknown[0]["key_id"] = "ed25519:sha256:" + "0" * 64
    with pytest.raises(ProvenanceError, match="key"):
        provenance.verify(unknown, checkpoint)
    forged = deepcopy(receipts)
    forged[0]["signature_b64"] = base64.b64encode(b"0" * 64).decode("ascii")
    with pytest.raises(ProvenanceError, match="signature"):
        provenance.verify(forged, checkpoint)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("gap", "sequence"),
        ("fork", "previous"),
        ("replay", "duplicate|coverage"),
        ("duplicate", "duplicate|coverage"),
        ("truncated", "checkpoint|count|sequence"),
        ("rollback", "checkpoint|sequence"),
    ],
)
def test_gap_fork_replay_duplicate_and_checkpoint_rollback_are_rejected(
    provenance: Fixture, mutation: str, message: str
) -> None:
    receipts, checkpoint = provenance.valid_chain()
    if mutation == "gap":
        body = dict(receipts[1]["receipt"], monotonic_seq=3)
        receipts[1] = provenance.sign("SignedChangeProvenanceReceiptV1", "receipt", body)
    elif mutation == "fork":
        body = dict(receipts[1]["receipt"], prev_receipt_digest="f" * 64)
        receipts[1] = provenance.sign("SignedChangeProvenanceReceiptV1", "receipt", body)
    elif mutation == "replay":
        receipts.append(deepcopy(receipts[1]))
    elif mutation == "duplicate":
        body = dict(receipts[1]["receipt"], canonical_diff_digest="e" * 64)
        receipts.append(provenance.sign("SignedChangeProvenanceReceiptV1", "receipt", body))
    elif mutation == "truncated":
        receipts.pop()
    else:
        body = dict(checkpoint["checkpoint"], final_sequence=1)
        checkpoint = provenance.sign("SignedProvenanceCheckpointV1", "checkpoint", body)
    with pytest.raises(ProvenanceError, match=message):
        provenance.verify(receipts, checkpoint)


def test_baseline_candidate_and_rewritten_history_mismatches_are_rejected(
    provenance: Fixture,
) -> None:
    receipts, checkpoint = provenance.valid_chain()
    with pytest.raises(ProvenanceError, match="baseline"):
        provenance.verify(receipts, checkpoint, candidate_commit=provenance.baseline)
    body = dict(receipts[0]["receipt"], parent_commit="0" * 40)
    receipts[0] = provenance.sign("SignedChangeProvenanceReceiptV1", "receipt", body)
    with pytest.raises(ProvenanceError, match="parent|history"):
        provenance.verify(receipts, checkpoint)


def test_uncovered_merge_is_rejected(provenance: Fixture) -> None:
    merge = _git("rev-list", "--merges", "-1", "HEAD")
    if not merge:
        pytest.skip("repository contains no existing merge commit")
    parent = _git("show", "-s", "--format=%P", merge).split()[0]
    provenance.bundle["baseline_commit"] = parent
    provenance.bundle["baseline_tree"] = _git("show", "-s", "--format=%T", parent)
    provenance.baseline = parent
    provenance.bundle_path.chmod(0o644)
    provenance.bundle_path.write_bytes(_canonical(provenance.bundle) + b"\n")
    provenance.bundle_path.chmod(0o444)
    provenance.bundle_digest = hashlib.sha256(_canonical(provenance.bundle)).hexdigest()
    receipt = provenance.receipt(0, previous=None, commit=merge, parent=parent, seq=1)
    producer_digest = hashlib.sha256(
        _canonical(
            [
                {
                    "producer_principal_digest": provenance.principal,
                    "producer_session_digest": provenance.session,
                }
            ]
        )
    ).hexdigest()
    body = {
        "schema_version": "ProvenanceCheckpointV1",
        "trust_bundle_digest": provenance.bundle_digest,
        "run_id": provenance.bundle["run_id"],
        "final_sequence": 1,
        "final_receipt_digest": receipt["receipt_digest"],
        "candidate_commit": merge,
        "candidate_tree": _git("show", "-s", "--format=%T", merge),
        "complete_producer_set_digest": producer_digest,
        "issued_at": NOW,
    }
    checkpoint = provenance.sign("SignedProvenanceCheckpointV1", "checkpoint", body)
    with pytest.raises(ProvenanceError, match="merge parent"):
        provenance.verify([receipt], checkpoint, candidate_commit=merge)


def test_cross_session_receipt_is_rejected(provenance: Fixture) -> None:
    receipts, checkpoint = provenance.valid_chain()
    body = dict(receipts[1]["receipt"], producer_session_digest="8" * 64)
    receipts[1] = provenance.sign("SignedChangeProvenanceReceiptV1", "receipt", body)
    with pytest.raises(ProvenanceError, match="producer session"):
        provenance.verify(receipts, checkpoint)


def test_writable_or_authoritative_evidence_store_is_rejected(provenance: Fixture) -> None:
    receipts, checkpoint = provenance.valid_chain()
    receipt_path, checkpoint_path = provenance.write_evidence(receipts, checkpoint)
    receipt_path.chmod(0o644)
    with pytest.raises(ProvenanceError, match="read-only"):
        verify_candidate_change(
            repo=REPO,
            bundle_path=provenance.bundle_path,
            approved_bundle_digest=provenance.bundle_digest,
            receipts_path=receipt_path,
            checkpoint_path=checkpoint_path,
            candidate_commit=provenance.commits[-1],
            now=NOW,
        )
    authority_log = Path(provenance.bundle["issuer_log"]["receipt_log_path"])
    authority_log.write_bytes(b"".join(_canonical(value) + b"\n" for value in receipts))
    authority_log.chmod(0o444)
    with pytest.raises(ProvenanceError, match="authoritative"):
        verify_candidate_change(
            repo=REPO,
            bundle_path=provenance.bundle_path,
            approved_bundle_digest=provenance.bundle_digest,
            receipts_path=authority_log,
            checkpoint_path=checkpoint_path,
            candidate_commit=provenance.commits[-1],
            now=NOW,
        )


def test_build_request_is_closed_and_binds_candidate(provenance: Fixture) -> None:
    request = build_candidate_change_request(
        repo=REPO,
        bundle_path=provenance.bundle_path,
        approved_bundle_digest=provenance.bundle_digest,
        candidate_commit=provenance.commits[0],
        producer_principal_digest=provenance.principal,
        producer_session_digest=provenance.session,
        now=NOW,
    )
    assert set(request) == {
        "schema_version",
        "operation",
        "trust_bundle_digest",
        "run_id",
        "baseline_commit",
        "parent_commit",
        "candidate_commit",
        "candidate_tree",
        "canonical_diff_digest",
        "producer_principal_digest",
        "producer_session_digest",
    }
    assert request["candidate_commit"] == provenance.commits[0]


def test_script_only_reads_and_prints_canonical_request(provenance: Fixture) -> None:
    script = REPO / "scripts" / "record_engagement_candidate_change.py"
    status_before = _git("status", "--short")
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "request",
            "--repo",
            str(REPO),
            "--bundle",
            str(provenance.bundle_path),
            "--approve-bundle-sha256",
            provenance.bundle_digest,
            "--candidate",
            provenance.commits[0],
            "--producer-principal-digest",
            provenance.principal,
            "--producer-session-digest",
            provenance.session,
            "--now",
            NOW,
        ],
        cwd=REPO,
        check=True,
        capture_output=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.stderr == b""
    assert result.stdout.endswith(b"\n") and result.stdout.count(b"\n") == 1
    value = json.loads(result.stdout)
    assert result.stdout[:-1] == _canonical(value)
    assert value["schema_version"] == "ProvenanceIssueRequestV1"
    assert _git("status", "--short") == status_before


def test_sut_contains_no_signer_or_authoritative_log_writer() -> None:
    module_source = (REPO / "hermes_cli" / "change_provenance.py").read_text()
    script_source = (REPO / "scripts" / "record_engagement_candidate_change.py").read_text()
    combined = module_source + script_source
    assert "SigningKey" not in combined
    assert "Ed25519PrivateKey" not in combined
    assert "private_key" not in combined
    assert "open(\"a\"" not in combined and "open('a'" not in combined
