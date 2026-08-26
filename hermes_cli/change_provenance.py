"""Untrusted client and validator for externally issued change provenance.

This module deliberately contains no signing or authoritative-log capability.
It accepts a frozen public trust bundle, emits requests for a separate issuer,
and validates read-only evidence copies against Git's existing object graph.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import socket
import stat
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
BUNDLE_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "baseline_commit",
        "baseline_tree",
        "expires_at",
        "issuer",
        "authorized_producer_sessions",
        "receipt_contract",
        "transport",
        "issuer_log",
    }
)
ISSUER_KEYS = frozenset(
    {"algorithm", "key_id", "public_key_b64", "service_digest_sha256"}
)
SESSION_KEYS = frozenset({"principal_digest", "session_digest"})
CONTRACT_KEYS = frozenset({"schema", "canonicalization", "digest"})
TRANSPORT_KEYS = frozenset({"kind", "socket_path", "expected_client_uid"})
LOG_KEYS = frozenset({"receipt_log_path", "anchor_log_path", "checkpoint_path"})
RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "trust_bundle_digest",
        "run_id",
        "monotonic_seq",
        "prev_receipt_digest",
        "baseline_commit",
        "parent_commit",
        "candidate_commit",
        "candidate_tree",
        "canonical_diff_digest",
        "producer_principal_digest",
        "producer_session_digest",
        "issued_at",
    }
)
CHECKPOINT_KEYS = frozenset(
    {
        "schema_version",
        "trust_bundle_digest",
        "run_id",
        "final_sequence",
        "final_receipt_digest",
        "candidate_commit",
        "candidate_tree",
        "complete_producer_set_digest",
        "issued_at",
    }
)
REQUEST_KEYS = frozenset(
    {
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
)


class ProvenanceError(ValueError):
    """A provenance input failed closed validation."""


@dataclass(frozen=True)
class PublicTrustBundle:
    value: dict[str, Any]
    digest: str
    public_key: Ed25519PublicKey
    authorized_sessions: frozenset[tuple[str, str]]
    expires_at: datetime


def canonical_json_bytes(value: Any) -> bytes:
    """Return the protocol's exact canonical JSON representation."""
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _fail(message: str) -> None:
    raise ProvenanceError(message)


def _strict_json(raw: bytes, *, source: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                _fail(f"{source} contains duplicate JSON key {key!r}")
            value[key] = item
        return value

    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=lambda value: _fail(
                f"{source} contains non-finite JSON value {value}"
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProvenanceError(f"{source} is invalid JSON") from exc


def _read_read_only(path: Path, *, source: str, maximum: int = 1_048_576) -> bytes:
    path = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProvenanceError(f"cannot read {source}") from exc
    try:
        before = os.fstat(descriptor)
        named = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o222
            or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
            or before.st_size <= 0
            or before.st_size > maximum
        ):
            _fail(f"{source} must be a stable read-only regular file")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                _fail(f"{source} was truncated while reading")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            _fail(f"{source} grew while reading")
        after = os.fstat(descriptor)
        current = path.lstat()
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
        ):
            _fail(f"{source} changed while reading")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _utc(value: Any, *, field: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        _fail(f"invalid {field}")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ProvenanceError(f"invalid {field}") from exc
    if parsed.tzinfo != timezone.utc:
        _fail(f"invalid {field}")
    return parsed


def _closed(value: Any, keys: frozenset[str], *, source: str) -> dict[str, Any]:
    if type(value) is not dict or frozenset(value) != keys:
        _fail(f"{source} is not a closed schema")
    return value


def _hex(value: Any, pattern: re.Pattern[str], *, field: str) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        _fail(f"invalid {field}")
    return value


def load_public_trust_bundle(
    bundle_path: Path,
    *,
    approved_bundle_digest: str,
    now: str,
) -> PublicTrustBundle:
    raw = _read_read_only(Path(bundle_path), source="trust bundle")
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        _fail("trust bundle must be one canonical newline-terminated object")
    value = _strict_json(raw[:-1], source="trust bundle")
    if canonical_json_bytes(value) != raw[:-1]:
        _fail("trust bundle is not canonical")
    bundle = _closed(value, BUNDLE_KEYS, source="trust bundle")
    if bundle["schema_version"] != "ProvenanceTrustBundleV1":
        _fail("trust bundle schema mismatch")
    digest = sha256_hex(raw[:-1])
    if type(approved_bundle_digest) is not str or approved_bundle_digest.lower() != digest:
        _fail("trust bundle approval digest mismatch")
    _hex(bundle["baseline_commit"], HEX40, field="baseline_commit")
    _hex(bundle["baseline_tree"], HEX40, field="baseline_tree")
    expiration = _utc(bundle["expires_at"], field="expires_at")
    if _utc(now, field="now") >= expiration:
        _fail("trust bundle expired")

    issuer = _closed(bundle["issuer"], ISSUER_KEYS, source="issuer")
    if issuer["algorithm"] != "Ed25519":
        _fail("unknown issuer algorithm")
    _hex(issuer["service_digest_sha256"], HEX64, field="issuer service digest")
    try:
        public_raw = base64.b64decode(issuer["public_key_b64"], validate=True)
        public_key = Ed25519PublicKey.from_public_bytes(public_raw)
    except (TypeError, ValueError) as exc:
        raise ProvenanceError("invalid issuer public key") from exc
    expected_key_id = "ed25519:sha256:" + sha256_hex(public_raw)
    if issuer["key_id"] != expected_key_id:
        _fail("trust bundle key id mismatch")

    contract = _closed(bundle["receipt_contract"], CONTRACT_KEYS, source="receipt contract")
    if contract != {
        "schema": "ChangeProvenanceReceiptV1",
        "canonicalization": "utf8-json-sort-keys-no-whitespace-v1",
        "digest": "sha256",
    }:
        _fail("receipt contract mismatch")
    transport = _closed(bundle["transport"], TRANSPORT_KEYS, source="transport")
    if (
        transport["kind"] not in {"darwin-unix-stream-v1", "unix-stream-v1"}
        or type(transport["socket_path"]) is not str
        or not transport["socket_path"]
        or type(transport["expected_client_uid"]) is not int
    ):
        _fail("transport schema mismatch")
    issuer_log = _closed(bundle["issuer_log"], LOG_KEYS, source="issuer log locator")
    if any(type(issuer_log[key]) is not str or not issuer_log[key] for key in LOG_KEYS):
        _fail("issuer log locator schema mismatch")

    sessions = bundle["authorized_producer_sessions"]
    if type(sessions) is not list or not sessions:
        _fail("authorized producer sessions must be non-empty")
    authorized: set[tuple[str, str]] = set()
    for item in sessions:
        session = _closed(item, SESSION_KEYS, source="authorized producer session")
        pair = (
            _hex(session["principal_digest"], HEX64, field="principal digest"),
            _hex(session["session_digest"], HEX64, field="session digest"),
        )
        if pair in authorized:
            _fail("duplicate authorized producer session")
        authorized.add(pair)
    return PublicTrustBundle(bundle, digest, public_key, frozenset(authorized), expiration)


def _git(repo: Path, *args: str, raw: bool = False) -> str | bytes:
    command = [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "diff.external=",
        "-c",
        "diff.trustExitCode=false",
        "-c",
        "gc.auto=0",
        "-c",
        "maintenance.auto=false",
        *args,
    ]
    environment = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ASKPASS": "/usr/bin/false",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "PAGER": "cat",
    }
    try:
        result = subprocess.run(
            command,
            cwd=Path(repo),
            env=environment,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProvenanceError(f"Git read failed: {' '.join(args)}") from exc
    return result.stdout if raw else result.stdout.decode("utf-8").strip()


def _commit_metadata(repo: Path, commit: str) -> tuple[str, list[str]]:
    _hex(commit, HEX40, field="commit")
    tree = _git(repo, "show", "-s", "--format=%T", commit)
    parents = _git(repo, "show", "-s", "--format=%P", commit)
    assert isinstance(tree, str) and isinstance(parents, str)
    _hex(tree, HEX40, field="commit tree")
    parent_list = parents.split() if parents else []
    for parent in parent_list:
        _hex(parent, HEX40, field="commit parent")
    return tree, parent_list


def canonical_diff_digest(repo: Path, parent_commit: str, candidate_commit: str) -> str:
    _hex(parent_commit, HEX40, field="parent_commit")
    _hex(candidate_commit, HEX40, field="candidate_commit")
    raw = _git(
        repo,
        "diff-tree",
        "--binary",
        "--full-index",
        "--no-color",
        "--no-ext-diff",
        parent_commit,
        candidate_commit,
        "--",
        raw=True,
    )
    assert isinstance(raw, bytes)
    return sha256_hex(raw)


def _history(repo: Path, baseline: str, candidate: str) -> list[str]:
    if candidate == baseline:
        _fail("candidate matches baseline; no candidate change")
    ancestry = _git(repo, "merge-base", "--is-ancestor", baseline, candidate)
    assert isinstance(ancestry, str)
    output = _git(repo, "rev-list", "--first-parent", "--reverse", f"{baseline}..{candidate}")
    assert isinstance(output, str)
    commits = output.splitlines() if output else []
    if not commits or commits[-1] != candidate:
        _fail("candidate history does not extend approved baseline")
    return commits


def build_candidate_change_request(
    *,
    repo: Path,
    bundle_path: Path,
    approved_bundle_digest: str,
    candidate_commit: str,
    producer_principal_digest: str,
    producer_session_digest: str,
    now: str,
) -> dict[str, Any]:
    bundle = load_public_trust_bundle(
        bundle_path, approved_bundle_digest=approved_bundle_digest, now=now
    )
    pair = (
        _hex(producer_principal_digest, HEX64, field="producer principal digest"),
        _hex(producer_session_digest, HEX64, field="producer session digest"),
    )
    if pair not in bundle.authorized_sessions:
        _fail("producer session is not authorized")
    history = _history(repo, bundle.value["baseline_commit"], candidate_commit)
    tree, parents = _commit_metadata(repo, candidate_commit)
    if len(parents) != 1:
        _fail("candidate merge parent ancestry is not fully covered")
    parent = parents[0]
    if history[-1] != candidate_commit:
        _fail("candidate history mismatch")
    return {
        "schema_version": "ProvenanceIssueRequestV1",
        "operation": "issue",
        "trust_bundle_digest": bundle.digest,
        "run_id": bundle.value["run_id"],
        "baseline_commit": bundle.value["baseline_commit"],
        "parent_commit": parent,
        "candidate_commit": candidate_commit,
        "candidate_tree": tree,
        "canonical_diff_digest": canonical_diff_digest(repo, parent, candidate_commit),
        "producer_principal_digest": pair[0],
        "producer_session_digest": pair[1],
    }


def _verify_envelope(
    envelope: Any,
    *,
    body_name: str,
    bundle: PublicTrustBundle,
    now: datetime,
) -> tuple[dict[str, Any], str]:
    if body_name == "receipt":
        envelope_schema = "SignedChangeProvenanceReceiptV1"
        body_schema = "ChangeProvenanceReceiptV1"
        digest_name = "receipt_digest"
        body_keys = RECEIPT_KEYS
    else:
        envelope_schema = "SignedProvenanceCheckpointV1"
        body_schema = "ProvenanceCheckpointV1"
        digest_name = "checkpoint_digest"
        body_keys = CHECKPOINT_KEYS
    closed = _closed(
        envelope,
        frozenset({"schema_version", "key_id", body_name, digest_name, "signature_b64"}),
        source=f"signed {body_name} envelope",
    )
    if closed["schema_version"] != envelope_schema:
        _fail(f"signed {body_name} envelope schema mismatch")
    if closed["key_id"] != bundle.value["issuer"]["key_id"]:
        _fail(f"signed {body_name} key id mismatch")
    body = _closed(closed[body_name], body_keys, source=body_name)
    if body["schema_version"] != body_schema:
        _fail(f"{body_name} schema mismatch")
    if (
        body["trust_bundle_digest"] != bundle.digest
        or body["run_id"] != bundle.value["run_id"]
    ):
        _fail(f"{body_name} trust context mismatch")
    issued = _utc(body["issued_at"], field=f"{body_name}.issued_at")
    if issued > now:
        _fail(f"{body_name} is from the future")
    if issued >= bundle.expires_at:
        _fail("trust bundle expired when response was issued")
    raw = canonical_json_bytes(body)
    digest = sha256_hex(raw)
    if closed[digest_name] != digest:
        _fail(f"signed {body_name} digest mismatch")
    try:
        signature = base64.b64decode(closed["signature_b64"], validate=True)
        bundle.public_key.verify(signature, raw)
    except (TypeError, ValueError, InvalidSignature) as exc:
        raise ProvenanceError(f"signed {body_name} signature mismatch") from exc
    return body, digest


def _read_json_lines(path: Path, *, source: str) -> list[Any]:
    raw = _read_read_only(path, source=source, maximum=16_777_216)
    if not raw.endswith(b"\n"):
        _fail(f"{source} is truncated or not newline terminated")
    lines = raw.splitlines()
    if not lines:
        _fail(f"{source} is empty")
    values = []
    for line in lines:
        value = _strict_json(line, source=source)
        if canonical_json_bytes(value) != line:
            _fail(f"{source} contains non-canonical JSON")
        values.append(value)
    return values


def _read_single_json(path: Path, *, source: str) -> Any:
    values = _read_json_lines(path, source=source)
    if len(values) != 1:
        _fail(f"{source} must contain exactly one object")
    return values[0]


def _reject_authoritative_paths(
    bundle: PublicTrustBundle, receipts_path: Path, checkpoint_path: Path
) -> None:
    locators = {Path(value).resolve(strict=False) for value in bundle.value["issuer_log"].values()}
    for evidence in (Path(receipts_path), Path(checkpoint_path)):
        if evidence.resolve(strict=False) in locators:
            _fail("authoritative issuer stores cannot be used as client evidence copies")


def _producer_set_digest(pairs: set[tuple[str, str]]) -> str:
    canonical_set = [
        {
            "producer_principal_digest": principal,
            "producer_session_digest": session,
        }
        for principal, session in sorted(pairs)
    ]
    return sha256_hex(canonical_json_bytes(canonical_set))


def verify_candidate_change(
    *,
    repo: Path,
    bundle_path: Path,
    approved_bundle_digest: str,
    receipts_path: Path,
    checkpoint_path: Path,
    candidate_commit: str,
    now: str,
) -> dict[str, Any]:
    current = _utc(now, field="now")
    bundle = load_public_trust_bundle(
        bundle_path, approved_bundle_digest=approved_bundle_digest, now=now
    )
    _reject_authoritative_paths(bundle, receipts_path, checkpoint_path)
    baseline = bundle.value["baseline_commit"]
    baseline_tree, _ = _commit_metadata(repo, baseline)
    if baseline_tree != bundle.value["baseline_tree"]:
        _fail("approved baseline tree mismatch")
    history = _history(repo, baseline, candidate_commit)
    receipt_values = _read_json_lines(receipts_path, source="receipt evidence copy")
    checkpoint_value = _read_single_json(checkpoint_path, source="checkpoint evidence copy")

    verified: list[tuple[dict[str, Any], str]] = []
    for value in receipt_values:
        verified.append(
            _verify_envelope(value, body_name="receipt", bundle=bundle, now=current)
        )
    if len(verified) != len(history):
        _fail("receipt coverage count does not match exact first-parent history")

    previous_digest: str | None = None
    seen_commits: set[str] = set()
    producer_pairs: set[tuple[str, str]] = set()
    for index, ((receipt, digest), commit) in enumerate(zip(verified, history), start=1):
        if type(receipt["monotonic_seq"]) is not int or receipt["monotonic_seq"] != index:
            _fail("receipt sequence has a gap or rollback")
        if receipt["prev_receipt_digest"] != previous_digest:
            _fail("receipt previous digest does not form a continuous chain")
        if receipt["baseline_commit"] != baseline:
            _fail("receipt baseline mismatch")
        if receipt["candidate_commit"] in seen_commits:
            _fail("duplicate commit or receipt replay")
        if receipt["candidate_commit"] != commit:
            _fail("receipt coverage does not match exact first-parent history")
        tree, parents = _commit_metadata(repo, commit)
        if len(parents) != 1:
            _fail("merge parent ancestry is not fully covered")
        expected_parent = baseline if index == 1 else history[index - 2]
        if parents[0] != expected_parent or receipt["parent_commit"] != expected_parent:
            _fail("receipt parent does not match candidate history")
        if receipt["candidate_tree"] != tree:
            _fail("receipt candidate tree mismatch")
        if receipt["canonical_diff_digest"] != canonical_diff_digest(
            repo, expected_parent, commit
        ):
            _fail("receipt canonical diff digest mismatch")
        pair = (
            _hex(
                receipt["producer_principal_digest"],
                HEX64,
                field="producer principal digest",
            ),
            _hex(
                receipt["producer_session_digest"], HEX64, field="producer session digest"
            ),
        )
        if pair not in bundle.authorized_sessions:
            _fail("receipt producer session is not authorized")
        producer_pairs.add(pair)
        seen_commits.add(commit)
        previous_digest = digest

    checkpoint, checkpoint_digest = _verify_envelope(
        checkpoint_value, body_name="checkpoint", bundle=bundle, now=current
    )
    final_tree, _ = _commit_metadata(repo, candidate_commit)
    expected_producer_digest = _producer_set_digest(producer_pairs)
    terminal = {
        "final_sequence": len(verified),
        "final_receipt_digest": previous_digest,
        "candidate_commit": candidate_commit,
        "candidate_tree": final_tree,
        "complete_producer_set_digest": expected_producer_digest,
    }
    for field, expected in terminal.items():
        if checkpoint[field] != expected:
            _fail(f"terminal checkpoint {field} mismatch")
    if verified and _utc(checkpoint["issued_at"], field="checkpoint.issued_at") < _utc(
        verified[-1][0]["issued_at"], field="receipt.issued_at"
    ):
        _fail("terminal checkpoint predates receipt chain")
    return {
        "status": "PASS",
        "trust_bundle_digest": bundle.digest,
        "candidate_commit": candidate_commit,
        "candidate_tree": final_tree,
        "receipt_count": len(verified),
        "final_receipt_digest": previous_digest,
        "complete_producer_set_digest": expected_producer_digest,
    }


def request_signed_receipt(
    *,
    bundle_path: Path,
    approved_bundle_digest: str,
    request: dict[str, Any],
    now: str,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """Send one canonical request to the external issuer and verify its reply."""
    bundle = load_public_trust_bundle(
        bundle_path, approved_bundle_digest=approved_bundle_digest, now=now
    )
    _closed(request, REQUEST_KEYS, source="issue request")
    if request["schema_version"] != "ProvenanceIssueRequestV1" or request["operation"] != "issue":
        _fail("wrong issue request schema")
    if (
        request["trust_bundle_digest"] != bundle.digest
        or request["run_id"] != bundle.value["run_id"]
        or request["baseline_commit"] != bundle.value["baseline_commit"]
    ):
        _fail("issue request trust context mismatch")
    transport = bundle.value["transport"]
    if os.getuid() != transport["expected_client_uid"]:  # windows-footgun: ok — darwin-unix-stream-v1 transport is POSIX-only
        _fail("client uid does not match frozen transport identity")
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    try:
        connection.connect(transport["socket_path"])
        connection.sendall(canonical_json_bytes(request) + b"\n")
        connection.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = connection.recv(65_536)
            if not chunk:
                break
            total += len(chunk)
            if total > 1_048_576:
                _fail("issuer response is too large")
            chunks.append(chunk)
    except OSError as exc:
        raise ProvenanceError("external issuer transport failed") from exc
    finally:
        connection.close()
    raw = b"".join(chunks)
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        _fail("issuer response must be one newline-terminated frame")
    envelope = _strict_json(raw[:-1], source="issuer response")
    if canonical_json_bytes(envelope) != raw[:-1]:
        _fail("issuer response is not canonical")
    _verify_envelope(
        envelope, body_name="receipt", bundle=bundle, now=_utc(now, field="now")
    )
    return envelope


__all__ = [
    "ProvenanceError",
    "build_candidate_change_request",
    "canonical_diff_digest",
    "canonical_json_bytes",
    "load_public_trust_bundle",
    "request_signed_receipt",
    "sha256_hex",
    "verify_candidate_change",
]
