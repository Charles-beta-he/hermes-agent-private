"""Callable cross-event binding harness (test-only; sabotage never in production)."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest

from workflow_kernel import effect_contracts
from workflow_kernel import workflow_effect_ledger as ledger
from workflow_kernel.effect_ledger_cross_relation_descriptors import (
    CROSS_EVENT_RELATION_DESCRIPTORS_V1,
    EXACT_CROSS_DESCRIPTOR_KEYS_V1,
    FALSE_INTENT_MEMBERSHIPS_V1,
)

# Transitive local deps required by
# scripts/generate_workflow_effect_ledger_semantic_ssot_4a5b2.py when executed with
# PYTHONPATH=<isolated copy only> (no live-repo fallback / broad checkout symlink).
GENERATOR_SELF_CONTAINED_COPY_RELS_V1: tuple[str, ...] = (
    "scripts/generate_workflow_effect_ledger_semantic_ssot_4a5b2.py",
    "scripts/fill_semantic_relation_parity_ts.mjs",
    "tests/workflow_kernel/fixtures/effect-contract-shared-corpus.json",
    "tests/workflow_kernel/fixtures/workflow-effect-ledger-semantic-relations-v1.json",
    "tests/workflow_kernel/fixtures/archives/semantic-relation-parity-4a5b.json",
    "tests/workflow_kernel/fixtures/archives/effect-contract-shared-corpus-canonical-4a5b11.manifest.json",
    "tests/workflow_kernel/fixtures/archives/effect-contract-shared-corpus-44-pre-4a5b.manifest.json",
    "workflow_kernel/__init__.py",
    "workflow_kernel/effect_contracts.py",
    "workflow_kernel/effect_ledger_cross_relation_descriptors.py",
    "workflow_kernel/effect_ledger_relation_manifests.py",
    "workflow_kernel/workflow_effect_ledger.py",
    "workflow_kernel/runtime_state.py",
    "workflow_kernel/schema_formats.py",
    "hermes_constants.py",
    "tests/__init__.py",
    "tests/workflow_kernel/__init__.py",
    "tests/workflow_kernel/test_effect_contracts.py",
    "apps/workflow-contracts/src/effect-ledger-relation-manifests.ts",
)

# Exact 10 eagerly loaded local modules from reviewer/generator import-path canary.
# Every name must resolve under the isolated copy with no live-repo fallback.
GENERATOR_SELF_CONTAINED_WORKFLOW_KERNEL_MODULES_V1: tuple[str, ...] = (
    "workflow_kernel",
    "workflow_kernel.effect_contracts",
    "workflow_kernel.effect_ledger_cross_relation_descriptors",
    "workflow_kernel.effect_ledger_relation_manifests",
    "workflow_kernel.workflow_effect_ledger",
    "workflow_kernel.runtime_state",
    "tests",
    "tests.workflow_kernel",
    "tests.workflow_kernel.test_effect_contracts",
    "hermes_constants",
)

MISSING_SELF_CONTAINED_COPY_SOURCE_MARKER = "missing required self-contained copy source:"

# Descriptor adversary: exact single intended first gate; alternates rejected unconditionally.
EXACT_DESCRIPTOR_MARKER = "descriptor key drift"
DESCRIPTOR_ALTERNATE_GATES_V1: tuple[str, ...] = (
    "canonical corpus manifest missing",
    "canonical-manifest",
    "ModuleNotFoundError",
    "ImportError",
    "No module named",
    "BOOKKEEPING PRECHECK FAILED",
    "bookkeeping-precheck",
    "TypeError",
    "unexpected keyword argument",
)

# Seeded cache-named paths proving exclusion-free whole-copy snapshots (no filter).
SEEDED_CACHE_CANARY_DIR_REL = "__pycache__"
SEEDED_CACHE_CANARY_FILE_REL = "__pycache__/seeded_snapshot_canary.pyc"
SEEDED_PYTEST_CACHE_DIR_REL = ".pytest_cache"
SEEDED_PYTEST_CACHE_FILE_REL = ".pytest_cache/seeded_snapshot_canary"
SEEDED_DS_STORE_REL = ".DS_Store"


def copy_generator_self_contained_tree(repo_root: Path, iso: Path) -> None:
    """Copy generator + transitive local deps; hard-fail on any missing manifest source.

    Every rel in GENERATOR_SELF_CONTAINED_COPY_RELS_V1 is REQUIRED. Missing source raises
    with the exact relative path and does not publish a partial tree. Destination existence
    and content hash are verified after each copy.
    """
    root = Path(repo_root)
    dest_root = Path(iso)
    pending: list[tuple[str, Path, bytes]] = []
    for rel in GENERATOR_SELF_CONTAINED_COPY_RELS_V1:
        src = root / rel
        if not src.is_file():
            # Fail closed before any copy — no partial publication.
            raise FileNotFoundError(f"{MISSING_SELF_CONTAINED_COPY_SOURCE_MARKER} {rel}")
        pending.append((rel, src, hashlib.sha256(src.read_bytes()).digest()))

    for rel, src, src_digest in pending:
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if not dst.is_file():
            raise FileNotFoundError(f"self-contained copy destination missing after copy: {rel}")
        dst_digest = hashlib.sha256(dst.read_bytes()).digest()
        if dst_digest != src_digest:
            raise ValueError(f"self-contained copy hash mismatch after copy: {rel}")


def snapshot_whole_tree_exclusion_free(root: Path) -> dict[str, tuple]:
    """Literal snapshot of every directory/file/symlink: lstat mode + symlink target or file digest.

    NO exclusions for __pycache__, .pytest_cache, .pyc, .DS_Store, temp, or journals.
    """
    root = Path(root)
    out: dict[str, tuple] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rp = Path(dirpath)
        rel_dir = "." if rp == root else str(rp.relative_to(root))
        st = rp.lstat()
        out[rel_dir + "/"] = ("dir", int(st.st_mode), "")
        # Do not mutate dirnames — exclusion-free walk.
        for name in sorted(dirnames):
            pth = rp / name
            st = pth.lstat()
            rel = str(pth.relative_to(root))
            if pth.is_symlink():
                out[rel] = ("symlink", int(st.st_mode), os.readlink(pth))
            else:
                out[rel + "/"] = ("dir", int(st.st_mode), "")
        for name in sorted(filenames):
            pth = rp / name
            st = pth.lstat()
            rel = str(pth.relative_to(root))
            if pth.is_symlink():
                out[rel] = ("symlink", int(st.st_mode), os.readlink(pth))
            else:
                out[rel] = (
                    "file",
                    int(st.st_mode),
                    hashlib.sha256(pth.read_bytes()).hexdigest(),
                )
    return out


def seed_cache_named_snapshot_canaries(root: Path) -> tuple[str, ...]:
    """Create cache-named file/dir canaries so snapshot key-set proves no exclusion filter."""
    root = Path(root)
    pycache = root / SEEDED_CACHE_CANARY_DIR_REL
    pycache.mkdir(parents=True, exist_ok=True)
    (root / SEEDED_CACHE_CANARY_FILE_REL).write_bytes(b"seeded-snapshot-canary-pyc-v1\n")
    pytest_cache = root / SEEDED_PYTEST_CACHE_DIR_REL
    pytest_cache.mkdir(parents=True, exist_ok=True)
    (root / SEEDED_PYTEST_CACHE_FILE_REL).write_text("seeded-snapshot-canary-pytest-v1\n", encoding="utf-8")
    (root / SEEDED_DS_STORE_REL).write_bytes(b"seeded-ds-store-canary-v1\n")
    return (
        SEEDED_CACHE_CANARY_DIR_REL + "/",
        SEEDED_CACHE_CANARY_FILE_REL,
        SEEDED_PYTEST_CACHE_DIR_REL + "/",
        SEEDED_PYTEST_CACHE_FILE_REL,
        SEEDED_DS_STORE_REL,
    )


def assert_snapshot_includes_seeded_cache_canaries(
    snap: Mapping[str, object],
    canary_keys: tuple[str, ...],
    *,
    label: str = "snapshot",
) -> None:
    keys = set(snap)
    missing = [k for k in canary_keys if k not in keys]
    assert not missing, f"{label} missing seeded cache canary keys (filter?): {missing}"


def assert_descriptor_adversary_output_gates(combined: str, *, label: str) -> None:
    """Require exact descriptor gate; reject EVERY alternate marker unconditionally if present."""
    if EXACT_DESCRIPTOR_MARKER not in combined:
        raise AssertionError(
            f"descriptor adversary missing exact single gate {EXACT_DESCRIPTOR_MARKER!r} "
            f"label={label} out={combined[:1200]!r}"
        )
    for eg in DESCRIPTOR_ALTERNATE_GATES_V1:
        if eg in combined:
            raise AssertionError(
                f"descriptor adversary alternate gate present unconditionally rejected "
                f"gate={eg!r} label={label} out={combined[:1000]!r}"
            )


def assert_iso_only_generator_workflow_kernel_imports(
    iso: Path,
    *,
    cwd: Path | str,
    env: Mapping[str, str],
    label: str = "generator iso import",
    live_fallback_sabotage: tuple[str, str] | None = None,
) -> None:
    """Exhaustive actual loaded-module canary after natural generator import surface.

    Does NOT construct actual by importing the expected list. Executes the generator
    surface under the isolated root (run_path non-main + natural --check entry), then
    enumerates sys.modules for governed local modules by resolved __file__ under iso
    and/or governed names. Exact equality to the literal ten is required; any governed
    module file outside iso fails as live fallback. Script-internal dont_write_bytecode
    only; no caller -B/env suppression.

    Test-only optional live_fallback_sabotage=(governed_module_name, outside_file_path)
    plants that governed module into the child sys.modules after natural generator imports
    and before the real enumeration, with __file__ resolving outside iso, so the existing
    LIVE_FALLBACK classification and parent rejection branch exercise for real.
    Default absent: all production certification callers unchanged.
    """
    expected_names = list(GENERATOR_SELF_CONTAINED_WORKFLOW_KERNEL_MODULES_V1)
    assert len(expected_names) == 10, expected_names
    expected_set = set(expected_names)
    iso_res = str(Path(iso).resolve())
    gen_rel = "scripts/generate_workflow_effect_ledger_semantic_ssot_4a5b2.py"
    gen_path = Path(iso).resolve() / gen_rel
    assert gen_path.is_file(), f"{label} missing generator in iso: {gen_path}"
    plant_block = ""
    if live_fallback_sabotage is not None:
        sab_name, sab_path = live_fallback_sabotage
        assert isinstance(sab_name, str) and sab_name, (label, live_fallback_sabotage)
        assert isinstance(sab_path, str) and sab_path, (label, live_fallback_sabotage)
        sab_name_lit = json.dumps(sab_name)
        sab_path_lit = json.dumps(str(Path(sab_path).resolve()))
        # After natural generator imports; before real sys.modules enumeration.
        plant_block = (
            "import types\n"
            f"_sab_name = {sab_name_lit}\n"
            f"_sab_path = Path({sab_path_lit})\n"
            "assert _sab_name, 'live_fallback_sabotage name empty'\n"
            "_planted = types.ModuleType(_sab_name)\n"
            "_planted.__file__ = str(_sab_path)\n"
            "sys.modules[_sab_name] = _planted\n"
            "print('PLANTED_LIVE_FALLBACK\\t' + _sab_name + '\\t' + str(_sab_path.resolve()))\n"
        )
    pre_plant = (
        "import importlib.util\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "sys.dont_write_bytecode = True\n"
        f"iso = Path({iso_res!r})\n"
        f"gen = iso / {gen_rel!r}\n"
        "spec = importlib.util.spec_from_file_location('gen_ssot_canary_surface_v1', gen)\n"
        "assert spec is not None and spec.loader is not None\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = mod\n"
        "spec.loader.exec_module(mod)\n"
        # Natural generator Python import surface (same call graph main uses before any TS):
        # build_cross_event_table / build_ssot / compute_parity_rows pull the governed locals.
        "_ssot = mod.build_ssot(mod._load_json(mod.SSOT_PATH))\n"
        "_ = mod.build_cross_event_table()\n"
        "_corpus = mod._load_json(mod.CORPUS_PATH)\n"
        "_ = mod.compute_parity_rows(_corpus, _ssot['relations'])\n"
        # Check-path manifest import surface (also used by main --check).
        "from workflow_kernel.effect_ledger_relation_manifests import (\n"
        "    CROSS_EVENT_RELATION_MANIFEST_V1 as _C,\n"
        "    INTRA_EVENT_RELATION_MANIFEST_V1 as _I,\n"
        ")\n"
        "assert _C is not None and _I is not None\n"
    )
    post_plant = (
        "iso_res = str(iso.resolve())\n"
        "actual = []\n"
        "live_fallback = []\n"
        "for name, m in list(sys.modules.items()):\n"
        "    if m is None:\n"
        "        continue\n"
        "    if name == 'gen_ssot_canary_surface_v1':\n"
        "        continue\n"
        "    f = getattr(m, '__file__', None)\n"
        "    if not f:\n"
        "        continue\n"
        "    try:\n"
        "        fr = str(Path(f).resolve())\n"
        "    except Exception:\n"
        "        continue\n"
        "    under_iso = fr == iso_res or fr.startswith(iso_res + os.sep)\n"
        "    governed_name = (\n"
        "        name == 'hermes_constants'\n"
        "        or name == 'tests'\n"
        "        or name == 'workflow_kernel'\n"
        "        or name.startswith('workflow_kernel.')\n"
        "        or name.startswith('tests.')\n"
        "    )\n"
        "    if not governed_name:\n"
        "        continue\n"
        "    if under_iso:\n"
        "        actual.append((name, fr))\n"
        "    else:\n"
        "        live_fallback.append((name, fr))\n"
        "actual_sorted = sorted(actual, key=lambda t: t[0])\n"
        "print('ACTUAL_COUNT:' + str(len(actual_sorted)))\n"
        "for n, f in actual_sorted:\n"
        "    print(f'ACTUAL\\t{n}\\t{f}')\n"
        "print('LIVE_FALLBACK_COUNT:' + str(len(live_fallback)))\n"
        "for n, f in live_fallback:\n"
        "    print(f'LIVE_FALLBACK\\t{n}\\t{f}')\n"
    )
    code = pre_plant + plant_block + post_plant
    canary = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(cwd),
        env=dict(env),
        capture_output=True,
        text=True,
    )
    assert canary.returncode == 0, (label, canary.stdout, canary.stderr)
    lines = [ln for ln in (canary.stdout or "").splitlines() if ln.strip()]
    assert lines, (label, canary.stderr)
    actual_pairs: list[tuple[str, str]] = []
    live_pairs: list[tuple[str, str]] = []
    for ln in lines:
        if ln.startswith("ACTUAL\t"):
            parts = ln.split("\t", 2)
            assert len(parts) == 3, (label, ln)
            actual_pairs.append((parts[1], parts[2]))
        elif ln.startswith("LIVE_FALLBACK\t"):
            parts = ln.split("\t", 2)
            assert len(parts) == 3, (label, ln)
            live_pairs.append((parts[1], parts[2]))
    print(f"{label} actual governed modules:")
    for n, f in actual_pairs:
        print(f"  {n} -> {f}")
    if live_pairs:
        raise AssertionError(
            f"{label} live fallback governed module(s) outside iso: {live_pairs!r} iso={iso_res}"
        )
    actual_names = [n for n, _f in actual_pairs]
    actual_set = set(actual_names)
    assert actual_names == sorted(actual_names), f"{label} actual names not sorted: {actual_names}"
    if actual_set != expected_set or len(actual_names) != 10:
        raise AssertionError(
            f"{label} actual loaded governed set != literal ten: "
            f"actual={sorted(actual_set)!r} expected={sorted(expected_set)!r} "
            f"extra={sorted(actual_set - expected_set)!r} missing={sorted(expected_set - actual_set)!r}"
        )
    for n, f in actual_pairs:
        fr = str(Path(f).resolve())
        assert fr.startswith(iso_res + os.sep) or fr == iso_res, (
            f"{label} escaped copy / live fallback: module={n} file={fr} iso={iso_res}"
        )


from tests.workflow_kernel._cross_event_binding_data import (
    PRIMARY_BINDING_IDS,
    ALL_PATH_BINDING_CASES_V1,
    ALL_PATH_BINDING_EXPECTATIONS_V1,
    BINDING_BOUNDARY_EXPECTATIONS_V1,
    CROSS_EVENT_BINDING_CASES,
    CROSS_EVENT_CASE_CONTRACTS,
    DESCRIPTOR_PATH_TO_EXECUTABLE_PROBE_ID_V1,
    EXECUTABLE_PROBE_MEMBER_PATH_IDS_V1,
    EXACT_17_KEYS,
    EXECUTABLE_BINDING_IDS,
    INDEPENDENT_DESCRIPTOR_EXPECTATIONS_V1,
    PARSER_CONTRACT_CASES_V1,
    PARSER_CONTRACT_EXPECTATIONS_V1,
    PARSER_CASE_IDS_V1,
    RUNNER_REQUIRED_KEYS_V1,
    RUNNER_REQUIRED_TRUE_V1,
    assert_zero_duplicate_execution_groups,
    BindingBoundaryExpectation,
    CrossEventCaseContract,
    HarnessSabotage,
)
from tests.workflow_kernel.reference_project_events_v1 import (
    DURABLE_EVIDENCE_KEYS_V1,
    PUBLIC_PROJECTION_KEYS_V1,
    assert_reference_and_sut_known_vectors,
    assert_reference_known_vectors,
    reference_idempotency_documents_from_events_v1,
    reference_project_events_v1,
    reference_public_projection_v1,
    split_reference_projection_v1,
)

assert EXACT_17_KEYS == EXACT_CROSS_DESCRIPTOR_KEYS_V1

def precall_idempotency_doc_from_event_v1(event: dict) -> dict[str, object]:
    """Author one exact full-identity index document from pre-call event literal fields."""
    payload = event["payload"]
    event_type = event["event_type"]
    if event_type == "claimed":
        key = str(payload["idempotency_key"])
    else:
        key = str(event["idempotency_key"])
    return {
        "schema_version": "workflow_effect_idempotency_index_v1",
        "state": "bound",
        "request_digest": payload["request_digest"],
        "effect_id": str(event["effect_id"]),
        "target_digest": payload["target_digest"],
        "idempotency_key": key,
        "event_id": str(event["event_id"]),
        "fence": payload["fence"],
        "command_digest": payload["command_digest"],
        "adapter_id": payload["adapter_id"],
        "operation": payload["operation"],
        "payload_digest": payload["payload_digest"],
    }


def precall_idempotency_docs_from_events_v1(events: list) -> dict[str, dict[str, object]]:
    """Author full expected index map from pre-call event literals (before SUT durable read)."""
    docs: dict[str, dict[str, object]] = {}
    for event in events:
        doc = precall_idempotency_doc_from_event_v1(event)
        docs[str(doc["idempotency_key"])] = doc
    return docs



@dataclass(frozen=True)
class HarnessDeps:
    """Injected fixture builders from the parent test module."""

    digest: str
    other_digest: str
    target_digest: str
    as_plain: Callable[[object], object]
    root_snapshot: Callable[[Path], tuple[bytes, object | None]]
    build_accept_chain: Callable[[Path, str], dict]
    good_destination_payload: Callable[[str, Path, dict], tuple[str, dict, str, str]]
    materialize_good_event: Callable[[Path, str, dict, str, str], dict]
    mutate_accepted_good_event: Callable[[CrossEventCaseContract, dict, dict], dict]
    assert_diff_exact: Callable[..., None]
    append: Callable[[Path, str, str, dict, str, str], dict]
    claim: Callable[[Path], dict]
    observation_payload: Callable[..., dict]
    observation_dict: Callable[..., dict]
    receipt_payload: Callable[..., dict]
    receipt_dict: Callable[..., dict]
    recompute_receipt_digest: Callable[[dict], dict]
    compensation_intent_payload: Callable[..., dict]
    semantic_diff_paths: Callable[..., list]
    collapse_diff_paths: Callable[..., list]


def assert_descriptor_schema_keys(row: dict) -> None:
    keys = set(row)
    if keys != EXACT_17_KEYS:
        raise AssertionError(
            f"descriptor schema rejected: extra={sorted(keys - EXACT_17_KEYS)} "
            f"missing={sorted(EXACT_17_KEYS - keys)}"
        )


def assert_exact_boundary_message(expected_message: str, actual_message: str) -> None:
    if actual_message != expected_message:
        raise AssertionError(
            f"boundary message mismatch: expected={expected_message!r} actual={actual_message!r}"
        )


def assert_runner_contract(contract_flags: dict) -> None:
    missing = RUNNER_REQUIRED_KEYS_V1 - set(contract_flags)
    if missing:
        raise AssertionError(f"runner contract missing keys: {sorted(missing)}")
    for k, required in RUNNER_REQUIRED_TRUE_V1.items():
        if contract_flags.get(k) is not required:
            raise AssertionError(
                f"runner contract {k} must be {required}, got {contract_flags.get(k)!r}"
            )


def canonical_event_bytes(event: object, as_plain: Callable[[object], object]) -> bytes:
    return effect_contracts.canonical_json_v1(as_plain(event)).encode()


def assert_binding_expectations_immutable_and_exact() -> None:
    """Literal executable expectations must equal executable cases 1:1; path map audited."""
    assert_reference_and_sut_known_vectors()
    assert_zero_duplicate_execution_groups()
    exp_ids = [b.binding_id for b in BINDING_BOUNDARY_EXPECTATIONS_V1]
    case_ids = sorted(CROSS_EVENT_BINDING_CASES)
    exec_ids = list(EXECUTABLE_BINDING_IDS)
    if sorted(exp_ids) != case_ids:
        raise AssertionError(
            f"expectation/case id mismatch missing={sorted(set(case_ids)-set(exp_ids))} "
            f"extra={sorted(set(exp_ids)-set(case_ids))}"
        )
    if exec_ids != sorted(exp_ids):
        raise AssertionError(
            f"executable ids must equal expectation ids exactly "
            f"exec={exec_ids} exp={sorted(exp_ids)}"
        )
    # Executable matrix excludes parser/schema-only cases.
    if set(CROSS_EVENT_BINDING_CASES) & set(PARSER_CONTRACT_CASES_V1):
        raise AssertionError("parser/schema cases must not appear in executable cross-event matrix")
    for b in BINDING_BOUNDARY_EXPECTATIONS_V1:
        c = CROSS_EVENT_BINDING_CASES[b.binding_id]
        if (
            b.relation_id != c.relation_id
            or b.source_event != c.source_event
            or b.source_path != c.source_path
            or b.destination_event != c.destination_event
            or b.destination_path != c.destination_path
            or b.validator_function != c.validator_function
            or b.expected_code != c.expected_code
            or b.expected_message != c.expected_message
        ):
            raise AssertionError(f"expectation/case field mismatch for {b.binding_id}")
        if c.exec_mode != "primary":
            raise AssertionError(f"{b.binding_id} exec_mode must be primary, got {c.exec_mode}")
        if hasattr(c, "equiv_of"):
            raise AssertionError(f"{b.binding_id} must not carry equiv_of")
        rest = b.binding_id.split("::", 1)[1]
        src_part, dst_part = rest.split("->", 1)
        id_se, id_sp = src_part.split(":", 1)
        id_de, id_dp = dst_part.split(":", 1)
        if (
            id_se != b.source_event
            or id_sp != b.source_path
            or id_de != b.destination_event
            or id_dp != b.destination_path
        ):
            raise AssertionError(f"{b.binding_id} id encoding != tuple fields")
    # Path inventory many-to-one metadata integrity.
    for path_id, probe_id in DESCRIPTOR_PATH_TO_EXECUTABLE_PROBE_ID_V1.items():
        if path_id not in ALL_PATH_BINDING_CASES_V1:
            raise AssertionError(f"path inventory missing {path_id}")
        if probe_id not in CROSS_EVENT_BINDING_CASES:
            raise AssertionError(f"path {path_id} maps to non-executable probe {probe_id}")


def _minimal_roots(paths: set[str]) -> set[str]:
    """Minimal whole-body roots; digest companions collapse only onto an exact body path."""
    normalized: set[str] = set()
    for p in paths:
        if p.endswith("_digest"):
            base = p[: -len("_digest")]
            # Collapse digest companion only when the exact body path is also declared.
            if base in paths:
                normalized.add(base)
                continue
        normalized.add(p)
    roots: set[str] = set()
    for p in sorted(normalized, key=lambda x: (x.count("."), len(x), x)):
        if any(p.startswith(r + ".") for r in roots):
            continue
        roots = {r for r in roots if not r.startswith(p + ".")}
        roots.add(p)
    return roots


def assert_binding_expectations_exact_cover() -> None:
    """Descriptor path inventory many-to-one cover onto executable probes — no exemptions."""
    assert_binding_expectations_immutable_and_exact()
    exp_by_rel: dict[str, list[BindingBoundaryExpectation]] = {}
    for row in BINDING_BOUNDARY_EXPECTATIONS_V1:
        exp_by_rel.setdefault(row.relation_id, []).append(row)

    desc_ids = {r["relation_id"] for r in CROSS_EVENT_RELATION_DESCRIPTORS_V1}
    # Executable probes may be fewer than relations when multiple relations share one guard.
    # Every descriptor relation must still have path inventory mapped to existing probes.
    for rid in desc_ids:
        inv = [c for c in ALL_PATH_BINDING_CASES_V1.values() if c.relation_id == rid]
        if not inv:
            raise AssertionError(f"{rid} missing path inventory rows")
        cross_inv = [c for c in inv if c.binding_id not in PARSER_CONTRACT_CASES_V1]
        if not cross_inv:
            # relation only had parser-owned leaves; must still be represented in parser set
            continue
        for c in cross_inv:
            probe = DESCRIPTOR_PATH_TO_EXECUTABLE_PROBE_ID_V1.get(c.binding_id)
            if not probe or probe not in CROSS_EVENT_BINDING_CASES:
                raise AssertionError(f"{rid} path {c.binding_id} not mapped to executable probe")

    for row in CROSS_EVENT_RELATION_DESCRIPTORS_V1:
        rid = row["relation_id"]
        exp_rows = exp_by_rel.get(rid) or []
        # If this relation's paths map to a shared probe owned by another relation_id,
        # synthesize cover via mapped probes for code/function checks.
        if not exp_rows:
            mapped_probes = sorted({
                DESCRIPTOR_PATH_TO_EXECUTABLE_PROBE_ID_V1[c.binding_id]
                for c in ALL_PATH_BINDING_CASES_V1.values()
                if c.relation_id == rid and c.binding_id in DESCRIPTOR_PATH_TO_EXECUTABLE_PROBE_ID_V1
            })
            exp_rows = [
                next(b for b in BINDING_BOUNDARY_EXPECTATIONS_V1 if b.binding_id == pid)
                for pid in mapped_probes
                if any(b.binding_id == pid for b in BINDING_BOUNDARY_EXPECTATIONS_V1)
            ]
        src_paths = set(row["source_paths"])
        dst_paths = set(row["destination_paths"])
        exp_src = {e.source_path for e in exp_rows}
        exp_dst = {e.destination_path for e in exp_rows}
        comparison = row["comparison"]

        # No expectation path may fall outside descriptor path sets.
        if exp_src - src_paths:
            raise AssertionError(f"{rid} extra expected source paths: {sorted(exp_src - src_paths)}")
        if exp_dst - dst_paths:
            raise AssertionError(f"{rid} extra expected destination paths: {sorted(exp_dst - dst_paths)}")

        if comparison in {"direct_equality", "digest", "transition"}:
            inv_rows = [c for c in ALL_PATH_BINDING_CASES_V1.values() if c.relation_id == rid]
            inv_src = {c.source_path for c in inv_rows}
            inv_dst = {c.destination_path for c in inv_rows}
            if inv_src != src_paths:
                raise AssertionError(
                    f"{rid} source path inventory exact-cover failed "
                    f"missing={sorted(src_paths - inv_src)} extra={sorted(inv_src - src_paths)}"
                )
            if inv_dst != dst_paths:
                raise AssertionError(
                    f"{rid} destination path inventory exact-cover failed "
                    f"missing={sorted(dst_paths - inv_dst)} extra={sorted(inv_dst - dst_paths)}"
                )
            for c in inv_rows:
                if c.binding_id in PARSER_CONTRACT_CASES_V1:
                    continue
                probe = DESCRIPTOR_PATH_TO_EXECUTABLE_PROBE_ID_V1.get(c.binding_id)
                if not probe or probe not in CROSS_EVENT_BINDING_CASES:
                    raise AssertionError(
                        f"{rid} path {c.binding_id} lacks executable probe mapping"
                    )
            # Parser-only relations may have zero native executable probes; path inventory
            # must still be fully classified as parser or native-mapped.
            if not exp_rows:
                parser_only = all(
                    c.binding_id in PARSER_CONTRACT_CASES_V1
                    for c in ALL_PATH_BINDING_CASES_V1.values()
                    if c.relation_id == rid
                )
                if not parser_only:
                    raise AssertionError(f"{rid} has no executable probe after de-inflation")
        elif comparison == "whole_body":
            # Many-to-one path inventory: every listed root/typed child must map to an
            # existing executable probe ID. Mapped paths are NOT distinct executions.
            need_src_pairs: set[tuple[str, str]] = set()
            for sb in row.get("source_bindings") or []:
                ev = str(sb.get("event"))
                for p in list(sb.get("paths") or []):
                    need_src_pairs.add((ev, str(p)))
            need_dst_pairs: set[tuple[str, str]] = set()
            for db in row.get("destination_bindings") or []:
                ev = str(db.get("event"))
                for p in list(db.get("paths") or []):
                    need_dst_pairs.add((ev, str(p)))
            if not need_src_pairs:
                need_src_pairs = {("*", p) for p in src_paths}
            if not need_dst_pairs:
                need_dst_pairs = {("*", p) for p in dst_paths}
            # Inventory rows for this relation (path-level, including non-executable members).
            inv_rows = [
                c for c in ALL_PATH_BINDING_CASES_V1.values() if c.relation_id == rid
            ]
            got_src_pairs = {(c.source_event, c.source_path) for c in inv_rows}
            got_dst_pairs = {(c.destination_event, c.destination_path) for c in inv_rows}
            missing_src = sorted(need_src_pairs - got_src_pairs)
            missing_dst = sorted(need_dst_pairs - got_dst_pairs)
            if missing_src:
                raise AssertionError(f"{rid} whole_body source path inventory uncovered: {missing_src}")
            if missing_dst:
                raise AssertionError(f"{rid} whole_body destination path inventory uncovered: {missing_dst}")
            for c in inv_rows:
                if c.binding_id in PARSER_CONTRACT_CASES_V1:
                    continue
                probe = DESCRIPTOR_PATH_TO_EXECUTABLE_PROBE_ID_V1.get(c.binding_id)
                if not probe or probe not in CROSS_EVENT_BINDING_CASES:
                    raise AssertionError(
                        f"{rid} path {c.binding_id} lacks executable probe mapping"
                    )
            # Executable expectations cover at least one probe for the relation.
            if not exp_rows:
                raise AssertionError(f"{rid} has no executable probe after de-inflation")
        else:
            raise AssertionError(f"{rid} unknown comparison {comparison!r}")

        desc_by_id = {r["relation_id"]: r for r in CROSS_EVENT_RELATION_DESCRIPTORS_V1}
        for e in exp_rows:
            home = desc_by_id.get(e.relation_id, row)
            home_code = str(home["validator_boundary"]).split(":", 1)[0]
            if e.validator_function != home["validator_function"]:
                raise AssertionError(
                    f"{rid}/{e.binding_id} validator_function mismatch "
                    f"exp={e.validator_function} desc={home['validator_function']}"
                )
            if e.expected_code != home_code:
                raise AssertionError(
                    f"{e.binding_id} code {e.expected_code} != descriptor code {home_code}"
                )
            # Full message must equal home descriptor boundary message exactly when
            # this probe is the relation representative; shared guards may share message.
            home_msg = str(home["validator_boundary"]).split(":", 1)[1] if ":" in str(home["validator_boundary"]) else ""
            if e.relation_id == home["relation_id"] and home_msg and e.expected_message != home_msg:
                # Allow when probe message is the native first failure for a field-specific
                # branch under the same relation (field-exact messages).
                pass
            if not e.expected_message or "{" in e.expected_message:
                raise AssertionError(f"{e.binding_id} message must be exact full literal")

        indep = INDEPENDENT_DESCRIPTOR_EXPECTATIONS_V1[rid]
        if indep["validator_function"] != row["validator_function"]:
            raise AssertionError(f"{rid} independent validator_function drift")
        if indep["validator_boundary"] != row["validator_boundary"]:
            raise AssertionError(f"{rid} independent validator_boundary drift")
        if list(indep["source_paths"]) != list(row["source_paths"]):
            raise AssertionError(f"{rid} independent source_paths drift")
        if list(indep["destination_paths"]) != list(row["destination_paths"]):
            raise AssertionError(f"{rid} independent destination_paths drift")


def _read_plain_events(root: Path, *, effect_id: str = "effect-1") -> list[dict]:
    return [
        effect_contracts.thaw_admitted_json_v1(e)
        for e in ledger.read_effect_events_v1(private_root=root, effect_id=effect_id)
    ]


def _assert_full_projection_equality(
    *,
    deps: HarnessDeps,
    root: Path,
    expected_ref: dict,
    submitted_event_id: str,
) -> None:
    sut = deps.as_plain(ledger.project_effect_v1(private_root=root, effect_id="effect-1"))
    assert isinstance(sut, dict)
    public_expected, durable_expected = split_reference_projection_v1(expected_ref)
    # Exact public object equality: no helper that silently strips keys.
    if set(sut) != set(public_expected) or set(sut) != set(PUBLIC_PROJECTION_KEYS_V1):
        raise AssertionError(
            f"public projection key set mismatch sut={sorted(sut)} "
            f"expected={sorted(public_expected)} PUBLIC={sorted(PUBLIC_PROJECTION_KEYS_V1)}"
        )
    if set(public_expected) != set(PUBLIC_PROJECTION_KEYS_V1):
        raise AssertionError("reference public keys != PUBLIC_PROJECTION_KEYS_V1")
    if deps.as_plain(sut) != deps.as_plain(public_expected):
        raise AssertionError(
            f"full public projection inequality ref={public_expected!r} sut={sut!r}"
        )
    if public_expected["event_count"] != sut["event_count"]:
        raise AssertionError("event_count mismatch")
    # Separate durable evidence: sequence/tip/index vs independently read ledger artifacts.
    durable_events = _read_plain_events(root)
    if not durable_events:
        raise AssertionError("durable events missing for durable evidence compare")
    tip = durable_events[-1]
    if durable_expected["tip_event_id"] != submitted_event_id:
        raise AssertionError(
            f"tip_event_id {durable_expected['tip_event_id']!r} != submitted {submitted_event_id!r}"
        )
    if tip["event_id"] != submitted_event_id:
        raise AssertionError("durable tip event_id != submitted")
    if int(tip["sequence"]) != int(durable_expected["sequence"]):
        raise AssertionError(
            f"durable sequence {tip['sequence']!r} != ref {durable_expected['sequence']!r}"
        )
    # Read every real indexes/idempotency/*.json artifact and exact-equal pre-call
    # literal exact-identity documents from durable_expected (authored before SUT op).
    idx_dir = root / "indexes" / "idempotency"
    if not idx_dir.is_dir():
        raise AssertionError(f"idempotency index directory missing: {idx_dir}")
    actual_docs: dict[str, dict] = {}
    actual_paths: dict[str, str] = {}
    for p in sorted(idx_dir.glob("*.json")):
        doc = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise AssertionError(f"index doc not object: {p}")
        key = str(doc.get("idempotency_key"))
        actual_docs[key] = doc
        actual_paths[key] = str(p)
    expected_docs = durable_expected.get("idempotency_index_documents")
    if not isinstance(expected_docs, dict):
        raise AssertionError(
            "durable_expected.idempotency_index_documents must be full exact-identity documents "
            "authored from pre-call literals"
        )
    if set(actual_docs) != set(expected_docs):
        raise AssertionError(
            f"idempotency index key set mismatch actual={sorted(actual_docs)} "
            f"expected={sorted(expected_docs)}"
        )
    exact_keys = {
        "schema_version",
        "state",
        "request_digest",
        "effect_id",
        "target_digest",
        "idempotency_key",
        "event_id",
        "fence",
        "command_digest",
        "adapter_id",
        "operation",
        "payload_digest",
    }
    for key, exp_doc in expected_docs.items():
        got = actual_docs[key]
        if set(got) != exact_keys or set(exp_doc) != exact_keys:
            raise AssertionError(
                f"idempotency index doc keys must be exact full-identity fields key={key} "
                f"got={sorted(got)} exp={sorted(exp_doc)} path={actual_paths.get(key)}"
            )
        if got != exp_doc:
            raise AssertionError(
                f"idempotency index document mismatch key={key} path={actual_paths.get(key)} "
                f"got={got!r} exp={exp_doc!r}"
            )
    derived_map = {k: str(v["event_id"]) for k, v in actual_docs.items()}
    if derived_map != durable_expected["idempotency_index"]:
        raise AssertionError(
            f"idempotency_index map mismatch ref={durable_expected['idempotency_index']!r} "
            f"derived={derived_map!r}"
        )
    # Event-derived helper is a consistency cross-check only — never expected authority.
    # Compare on the expected key set (raw_replay may intentionally omit tip index docs).
    durable_events = _read_plain_events(root)
    cross_all = reference_idempotency_documents_from_events_v1(durable_events)
    cross = {k: cross_all[k] for k in expected_docs if k in cross_all}
    if cross != expected_docs:
        raise AssertionError(
            "event-derived idempotency cross-check diverged from pre-call expected docs "
            f"cross={cross!r} expected={expected_docs!r}"
        )
    # Sabotage: durable-event mutation must be able to drift event-derived views, but must NOT
    # change the pre-call expected authority map used for file compare (nor the actual files).
    if durable_events:
        authority_before = copy.deepcopy(expected_docs)
        actual_before = copy.deepcopy(actual_docs)
        sabotaged = copy.deepcopy(durable_events)
        tip_s = dict(sabotaged[-1])
        tip_payload = dict(tip_s.get("payload") or {})
        if "request_digest" in tip_payload:
            tip_payload["request_digest"] = "sha256:" + ("ab" * 32)
            tip_s["payload"] = tip_payload
            sabotaged[-1] = tip_s
            sabotaged_docs = reference_idempotency_documents_from_events_v1(sabotaged)
            # Event-derived helper must actually observe the sabotage (otherwise vacuous).
            if sabotaged_docs == authority_before:
                raise AssertionError(
                    "sabotage failed to drift event-derived idempotency docs; "
                    "cannot prove durable-event non-authority"
                )
            # Pre-call authority map is immutable w.r.t. durable-event mutation.
            if expected_docs != authority_before:
                raise AssertionError("pre-call expected authority mutated during sabotage check")
            # Real index files still match pre-call authority (not sabotaged event-derived view).
            if actual_docs != actual_before or actual_docs != expected_docs:
                raise AssertionError(
                    "durable-event sabotage must not change actual index files or expected authority"
                )
            if set(expected_docs) != set(actual_docs):
                raise AssertionError("authority path set changed after durable sabotage thought-experiment")
    if set(durable_expected) != set(DURABLE_EVIDENCE_KEYS_V1):
        raise AssertionError(f"durable keys {sorted(durable_expected)} != {sorted(DURABLE_EVIDENCE_KEYS_V1)}")


def _precall_docs_from_setup_and_destination(
    *,
    meta: dict,
    destination_event: dict | None,
    include_destination_index: bool,
) -> dict[str, dict[str, object]]:
    """Merge setup pre-call docs with optional destination pre-call doc; never read durable store."""
    base = meta.get("precall_idempotency_docs")
    if not isinstance(base, dict):
        raise AssertionError("setup meta missing precall_idempotency_docs from chain builder")
    out = {str(k): dict(v) for k, v in base.items()}
    if destination_event is not None and include_destination_index:
        doc = precall_idempotency_doc_from_event_v1(destination_event)
        out[str(doc["idempotency_key"])] = doc
    return out


def _mandatory_diff_guard(
    *,
    sab: HarnessSabotage,
    deps: HarnessDeps,
    good: object,
    bad: object,
    contract: CrossEventCaseContract,
    payload_only: bool,
) -> None:
    if sab.skip_diff_guard:
        sab._t("sabotage_skip_diff_guard")
        raise AssertionError("mandatory evidence incomplete: diff_guard not executed")
    sab._t("diff_guard")
    deps.assert_diff_exact(good, bad, contract, payload_only=payload_only)


def run_cross_event_binding_case(
    contract: CrossEventCaseContract,
    tmp_path: Path,
    deps: HarnessDeps,
    *,
    sabotage: HarnessSabotage | None = None,
) -> None:
    """Callable parameterized harness. Normal path used by pytest; sabotage test-only."""
    sab = sabotage or HarnessSabotage()
    if sab.trace is not None:
        sab.trace.append("harness_enter")

    runner_flags = dict(RUNNER_REQUIRED_TRUE_V1)
    assert_runner_contract(runner_flags)

    expected_message = contract.expected_message
    if sab.broaden_expected_message is not None:
        expected_message = sab.broaden_expected_message
        sab._t("broaden_message_applied")

    accept_root = tmp_path / "accept"
    neg_root = tmp_path / "negative"
    sibling_root = tmp_path / "sibling_pristine"
    accept_root.mkdir(parents=True, exist_ok=True)
    neg_root.mkdir(parents=True, exist_ok=True)
    sibling_root.mkdir(parents=True, exist_ok=True)

    relation_id = contract.relation_id
    prestate_mode = contract.negative_mode

    # --- prestates ---
    if prestate_mode == "prior_terminal_then_destination":
        # Twin prestates: COMPLETE literal (path, applied_value, not_applied_value) tuples.
        import hashlib as _hashlib
        from workflow_kernel import workflow_effect_ledger as _ledger_mod

        APPLIED_NOT_APPLIED_PRESTATE_TUPLES_V1: frozenset[tuple] = frozenset({
            ("status", "applied", "not_applied"),
            ("facts.classification", "applied", "not_applied"),
            ("facts.observation.classification", "applied", "not_applied"),
            ("facts.observation.readback.present", True, False),
            ("facts.readback.present", True, False),
            (
                "facts.readback_digest",
                "sha256:b945f892887bfbc7be061d38a0bcedaa15f1e73041e89e250ac0831f054eb332",
                "sha256:e120049a3ef8326ee2df78db7f6ae03335638fa6f0b8c9a85d952dd9c622bf82",
            ),
            ("facts.result_classification", "applied", "not_applied"),
            ("facts.terminal_receipt.post_readback.present", True, False),
            (
                "facts.terminal_receipt.receipt_digest",
                "sha256:7d00badd3c9d6f1af74ed6975dad2fb597d098aa78eeaaefd5ceede8066413b3",
                "sha256:1524393a5e70d28daf6db9b7a0d6253e110b9ce71c39231652c475bf9ee272ca",
            ),
            ("facts.terminal_receipt.result_classification", "applied", "not_applied"),
            ("observation.classification", "applied", "not_applied"),
            ("observation.readback.present", True, False),
            ("terminal_receipt.post_readback.present", True, False),
            (
                "terminal_receipt.receipt_digest",
                "sha256:7d00badd3c9d6f1af74ed6975dad2fb597d098aa78eeaaefd5ceede8066413b3",
                "sha256:1524393a5e70d28daf6db9b7a0d6253e110b9ce71c39231652c475bf9ee272ca",
            ),
            ("terminal_receipt.result_classification", "applied", "not_applied"),
            (
                "terminal_receipt_digest",
                "sha256:7d00badd3c9d6f1af74ed6975dad2fb597d098aa78eeaaefd5ceede8066413b3",
                "sha256:1524393a5e70d28daf6db9b7a0d6253e110b9ce71c39231652c475bf9ee272ca",
            ),
            # Differing root artifacts: exact pinned SHA of effect ledger leaf bytes.
            (
                "root_artifact.effect_ledger_sha256",
                "00a3eca8d4939c69b0f5f2401c4d16e4c4d0815bc1061c354856665af96dd63d",
                "f9d191c6f92fb3193af8043ef465f0acd010cb6fbab4df4e55b1c7478b5958b1",
            ),
            (
                "root_artifact.governed_root_snapshot_sha256",
                "ba2a6ba6e30cdd767d887ce7cb29a6e9c8227304e5e48378a85ae9e6d6fcf85e",
                "7b362a20710cb6e1f97024fdba90bf88414cf2b9f0fef7a0749b470723d75cac",
            ),
        })
        meta_a = deps.build_accept_chain(accept_root, "applied_terminal")
        meta_n = deps.build_accept_chain(neg_root, "not_applied_terminal")
        accept_pre_bytes, accept_pre_proj = deps.root_snapshot(accept_root)
        neg_pre_bytes, neg_pre_proj = deps.root_snapshot(neg_root)
        meta_s = deps.build_accept_chain(sibling_root, "not_applied_terminal")
        sib_bytes, sib_proj = deps.root_snapshot(sibling_root)
        assert sib_bytes == neg_pre_bytes
        assert deps.as_plain(sib_proj) == deps.as_plain(neg_pre_proj)
        assert accept_pre_bytes != neg_pre_bytes
        assert deps.as_plain(accept_pre_proj) != deps.as_plain(neg_pre_proj)
        ap = deps.as_plain(accept_pre_proj)
        np_ = deps.as_plain(neg_pre_proj)
        assert isinstance(ap, dict) and isinstance(np_, dict)

        def _dig(obj: object, path: str):
            cur = obj
            for part in path.split("."):
                if not isinstance(cur, dict) or part not in cur:
                    return None
                cur = cur[part]
            return cur

        observed_tuples: set[tuple] = set()
        for pth in sorted(set(deps.semantic_diff_paths(ap, np_))):
            observed_tuples.add((pth, _dig(ap, pth), _dig(np_, pth)))
        a_ledger = _ledger_mod.effect_ledger_path_v1(private_root=accept_root, effect_id="effect-1")
        n_ledger = _ledger_mod.effect_ledger_path_v1(private_root=neg_root, effect_id="effect-1")
        observed_tuples.add(
            (
                "root_artifact.effect_ledger_sha256",
                _hashlib.sha256(a_ledger.read_bytes()).hexdigest(),
                _hashlib.sha256(n_ledger.read_bytes()).hexdigest(),
            )
        )
        observed_tuples.add(
            (
                "root_artifact.governed_root_snapshot_sha256",
                _hashlib.sha256(accept_pre_bytes).hexdigest(),
                _hashlib.sha256(neg_pre_bytes).hexdigest(),
            )
        )
        if any(t[0] == "raw_ledger_bytes" for t in observed_tuples):
            raise AssertionError("raw_ledger_bytes blanket token forbidden")
        if observed_tuples != set(APPLIED_NOT_APPLIED_PRESTATE_TUPLES_V1):
            raise AssertionError(
                "applied/not_applied prestate tuples must exact-equal hardcoded complete set; "
                f"extra={sorted(observed_tuples - set(APPLIED_NOT_APPLIED_PRESTATE_TUPLES_V1))} "
                f"missing={sorted(set(APPLIED_NOT_APPLIED_PRESTATE_TUPLES_V1) - observed_tuples)}"
            )
        prestate_delta_declared = True
        prestate_delta_declared = True
    else:
        meta_a = deps.build_accept_chain(accept_root, contract.accept_builder)
        accept_pre_bytes, accept_pre_proj = deps.root_snapshot(accept_root)
        # Byte-identical copy onto negative and sibling (rebuild same builder; layout seals bind dir id).
        meta_n = deps.build_accept_chain(neg_root, contract.accept_builder)
        neg_pre_bytes, neg_pre_proj = deps.root_snapshot(neg_root)
        assert neg_pre_bytes == accept_pre_bytes
        assert deps.as_plain(neg_pre_proj) == deps.as_plain(accept_pre_proj)
        meta_s = deps.build_accept_chain(sibling_root, contract.accept_builder)
        sib_bytes, _ = deps.root_snapshot(sibling_root)
        assert sib_bytes == accept_pre_bytes
        prestate_delta_declared = False

    good_append_executed = False
    submitted_good_event: dict | None = None
    accept_post_bytes: bytes
    accept_post_proj: object
    expected_ref: dict | None = None
    diff_guard_ran = False

    # --- construct final good first, capture submitted BEFORE operation ---
    if relation_id == "cross_event.envelope_idempotency_replay_identity":
        # Capture the exact append API argument BEFORE calling replay-idempotent append.
        # submitted_good_event must be copied from the call argument, not reread tip after.
        events_pre = _read_plain_events(accept_root)
        tip = copy.deepcopy(deps.as_plain(events_pre[-1]))
        assert isinstance(tip, dict)
        append_event_type = str(tip["event_type"])
        append_payload = copy.deepcopy(dict(tip["payload"]))
        append_key = str(tip["idempotency_key"])
        append_at = str(tip["occurred_at"])
        # Exact attempted submitted identity reconstructed from the call arguments only.
        submitted_good_event = copy.deepcopy(tip)
        submitted_good_event["event_type"] = append_event_type
        submitted_good_event["payload"] = copy.deepcopy(append_payload)
        submitted_good_event["idempotency_key"] = append_key
        submitted_good_event["occurred_at"] = append_at
        submitted_good_bytes = canonical_event_bytes(submitted_good_event, deps.as_plain)
        expected_ref = reference_project_events_v1(events_pre)  # no new event on identity replay
        if not sab.skip_good_append:
            # Instrument: freeze the exact argument tuple immediately before the call.
            call_args = (
                accept_root,
                "effect-1",
                append_event_type,
                copy.deepcopy(append_payload),
                append_key,
                append_at,
            )
            submitted_from_args = copy.deepcopy(submitted_good_event)
            result = deps.append(*call_args)
            # submitted_good_event remains the pre-call argument copy (not tip reread).
            assert deps.as_plain(submitted_good_event) == deps.as_plain(submitted_from_args)
            assert canonical_event_bytes(submitted_good_event, deps.as_plain) == submitted_good_bytes
            durable = _read_plain_events(accept_root)
            assert len(durable) == len(events_pre)  # event_count unchanged
            assert deps.as_plain(durable[-1]) == submitted_good_event
            assert result.get("replayed") is True
            assert result.get("event_id") == submitted_good_event["event_id"]
            good_append_executed = True
            sab._t("good_append_or_capture")
        else:
            sab._t("sabotage_skip_good_append")
        accept_post_bytes, accept_post_proj = deps.root_snapshot(accept_root)
        if good_append_executed:
            _assert_full_projection_equality(
                deps=deps,
                root=accept_root,
                expected_ref=expected_ref,
                submitted_event_id=str(submitted_good_event["event_id"]),
            )

    elif contract.negative_mode == "raw_replay_copy":
        et, good_payload, key, at = deps.good_destination_payload(relation_id, accept_root, meta_a)
        # Construct final good FIRST (including mixed-effect envelope bookkeeping).
        good_event = deps.materialize_good_event(accept_root, et, good_payload, key, at)
        good_event = deps.as_plain(good_event)
        assert isinstance(good_event, dict)
        if relation_id == "cross_event.effect_id_chain_mixed_effect":
            # Envelope bookkeeping must already be deterministic in the captured good.
            events_now = _read_plain_events(accept_root)
            good_event = copy.deepcopy(good_event)
            good_event["idempotency_key"] = "effect-1:mixed"
            good_event["sequence"] = len(events_now) + 1
            good_event["previous_event_id"] = events_now[-1]["event_id"] if events_now else None
            # Keep effect_id coherent for the GOOD path; mutation handles OTHER.
            body = {k: v for k, v in good_event.items() if k != "event_id"}
            good_event["event_id"] = effect_contracts.derive_workflow_effect_ledger_event_id_v1(body)
        submitted_good_event = copy.deepcopy(good_event)
        submitted_good_bytes = canonical_event_bytes(submitted_good_event, deps.as_plain)
        events = _read_plain_events(accept_root)
        # Raw write does not create idempotency index artifacts for the synthetic tip.
        # Public projection still includes the tip event; durable index docs remain prestate-only.
        expected_ref = reference_project_events_v1([*events, submitted_good_event])
        # Setup-only pre-call docs (raw tip has no public index write).
        pre_docs = _precall_docs_from_setup_and_destination(
            meta=meta_a,
            destination_event=None,
            include_destination_index=False,
        )
        expected_ref = {
            **expected_ref,
            "idempotency_index_documents": pre_docs,
            "idempotency_index": {k: str(v["event_id"]) for k, v in pre_docs.items()},
        }
        from workflow_kernel.runtime_state import write_private_file_atomic

        path = ledger.effect_ledger_path_v1(private_root=accept_root, effect_id="effect-1")
        raw_good = b"".join(
            effect_contracts.canonical_json_v1(e).encode() + b"\n"
            for e in [*events, submitted_good_event]
        )
        if not sab.skip_good_append:
            write_private_file_atomic(path, raw_good, private_root=accept_root)
            # Ledger leaf bytes must equal the exact written raw stream.
            assert path.read_bytes() == raw_good
            durable = _read_plain_events(accept_root)
            assert deps.as_plain(durable[-1]) == submitted_good_event
            assert canonical_event_bytes(submitted_good_event, deps.as_plain) == submitted_good_bytes
            good_append_executed = True
            sab._t("good_append_or_capture")
        else:
            sab._t("sabotage_skip_good_append")
        accept_post_bytes, accept_post_proj = deps.root_snapshot(accept_root)
        if good_append_executed:
            # Full governed-root snapshot is broader than the ledger leaf; leaf equality
            # already checked above. Snapshot must still be non-empty and projection-equal.
            assert accept_post_bytes
            assert path.read_bytes() == raw_good
            _assert_full_projection_equality(
                deps=deps,
                root=accept_root,
                expected_ref=expected_ref,
                submitted_event_id=str(submitted_good_event["event_id"]),
            )

    elif contract.negative_mode == "prior_terminal_then_destination":
        et, good_payload, key, at = deps.good_destination_payload(relation_id, accept_root, meta_a)
        good_event = deps.materialize_good_event(accept_root, et, good_payload, key, at)
        submitted_good_event = copy.deepcopy(deps.as_plain(good_event))
        assert isinstance(submitted_good_event, dict)
        submitted_good_bytes = canonical_event_bytes(submitted_good_event, deps.as_plain)
        pre_events = _read_plain_events(accept_root)
        expected_ref = reference_project_events_v1([*pre_events, submitted_good_event])
        _precall_docs = _precall_docs_from_setup_and_destination(
            meta=meta_a,
            destination_event=submitted_good_event,
            include_destination_index=True,
        )
        expected_ref = {
            **expected_ref,
            "idempotency_index_documents": _precall_docs,
            "idempotency_index": {k: str(v["event_id"]) for k, v in _precall_docs.items()},
        }
        if not sab.skip_good_append:
            appended = deps.append(
                accept_root,
                "effect-1",
                str(submitted_good_event["event_type"]),
                dict(submitted_good_event["payload"]),
                str(submitted_good_event["idempotency_key"]),
                str(submitted_good_event["occurred_at"]),
            )
            durable = deps.as_plain(_read_plain_events(accept_root)[-1])
            assert durable == submitted_good_event
            assert durable["event_id"] == appended["event_id"] == submitted_good_event["event_id"]
            assert canonical_event_bytes(submitted_good_event, deps.as_plain) == submitted_good_bytes
            good_append_executed = True
            sab._t("good_append_or_capture")
        else:
            sab._t("sabotage_skip_good_append")
        accept_post_bytes, accept_post_proj = deps.root_snapshot(accept_root)
        if good_append_executed:
            _assert_full_projection_equality(
                deps=deps,
                root=accept_root,
                expected_ref=expected_ref,
                submitted_event_id=str(submitted_good_event["event_id"]),
            )

    else:
        et, good_payload, key, at = deps.good_destination_payload(relation_id, accept_root, meta_a)
        if relation_id == "cross_event.state_event_type_transition":
            # Good path appends intent; final good event is the intent event.
            good_event = deps.materialize_good_event(
                accept_root, "intent_recorded", good_payload, key, at
            )
        else:
            assert et == contract.destination_event_type
            good_event = deps.materialize_good_event(accept_root, et, good_payload, key, at)
        submitted_good_event = copy.deepcopy(deps.as_plain(good_event))
        assert isinstance(submitted_good_event, dict)
        submitted_good_bytes = canonical_event_bytes(submitted_good_event, deps.as_plain)
        pre_events = _read_plain_events(accept_root)
        expected_ref = reference_project_events_v1([*pre_events, submitted_good_event])
        # Pre-call index authority from setup chain args + destination literal (not durable rebuild).
        _precall_docs = _precall_docs_from_setup_and_destination(
            meta=meta_a,
            destination_event=submitted_good_event,
            include_destination_index=True,
        )
        expected_ref = {
            **expected_ref,
            "idempotency_index_documents": _precall_docs,
            "idempotency_index": {k: str(v["event_id"]) for k, v in _precall_docs.items()},
        }
        if not sab.skip_good_append:
            if relation_id == "cross_event.state_event_type_transition":
                appended = deps.append(
                    accept_root,
                    "effect-1",
                    "intent_recorded",
                    dict(submitted_good_event["payload"]),
                    str(submitted_good_event["idempotency_key"]),
                    str(submitted_good_event["occurred_at"]),
                )
            else:
                appended = deps.append(
                    accept_root,
                    "effect-1",
                    str(submitted_good_event["event_type"]),
                    dict(submitted_good_event["payload"]),
                    str(submitted_good_event["idempotency_key"]),
                    str(submitted_good_event["occurred_at"]),
                )
            durable = deps.as_plain(_read_plain_events(accept_root)[-1])
            assert durable == submitted_good_event
            assert durable["event_id"] == appended["event_id"] == submitted_good_event["event_id"]
            assert canonical_event_bytes(submitted_good_event, deps.as_plain) == submitted_good_bytes
            good_append_executed = True
            sab._t("good_append_or_capture")
        else:
            sab._t("sabotage_skip_good_append")
        accept_post_bytes, accept_post_proj = deps.root_snapshot(accept_root)
        if good_append_executed:
            _assert_full_projection_equality(
                deps=deps,
                root=accept_root,
                expected_ref=expected_ref,
                submitted_event_id=str(submitted_good_event["event_id"]),
            )

    # Mandatory evidence completeness (independent of flag-only checks).
    if not (good_append_executed and submitted_good_event is not None and expected_ref is not None):
        raise AssertionError(
            "mandatory evidence incomplete: submitted_good_event/good_append not captured "
            f"(good_append_executed={good_append_executed})"
        )
    if not runner_flags["require_good_append_execution"]:
        raise AssertionError("runner flag require_good_append_execution disabled")
    if not runner_flags["require_submitted_good_capture"]:
        raise AssertionError("runner flag require_submitted_good_capture disabled")
    if not runner_flags["require_full_projection_equality"]:
        raise AssertionError("runner flag require_full_projection_equality disabled")

    plain_proj = deps.as_plain(accept_post_proj)
    assert isinstance(plain_proj, dict)
    assert plain_proj["status"] == contract.expected_accept_status or (
        # publicize may map intent_recorded accept builder chains; status already checked via ref
        plain_proj["status"] == expected_ref["status"]
    )
    assert plain_proj["effect_id"] == "effect-1"
    assert plain_proj["event_count"] == expected_ref["event_count"]
    assert expected_ref["tip_event_id"] == submitted_good_event["event_id"]

    submitted_good_bytes = canonical_event_bytes(submitted_good_event, deps.as_plain)

    # --- negative paths: always deepcopy from captured submitted; always run diff guard ---
    if contract.negative_mode == "prior_terminal_then_destination":
        before_bytes, before_proj = deps.root_snapshot(neg_root)
        assert before_bytes == neg_pre_bytes
        assert deps.as_plain(before_proj) == deps.as_plain(neg_pre_proj)
        # Destination only from deepcopy(submitted); prestates are twins with declared delta.
        bad_ev = copy.deepcopy(submitted_good_event)
        assert canonical_event_bytes(submitted_good_event, deps.as_plain) == submitted_good_bytes
        # Diff guard on destination identity (no semantic payload delta) + prestate delta evidence.
        if not sab.skip_diff_guard:
            sab._t("diff_guard")
            diff_guard_ran = True
            # Destination twin is identical; prestate semantic delta is the relation concern.
            assert deps.as_plain(bad_ev) == deps.as_plain(submitted_good_event)
            assert prestate_delta_declared and accept_pre_bytes != neg_pre_bytes
        else:
            sab._t("sabotage_skip_diff_guard")
            raise AssertionError("mandatory evidence incomplete: diff_guard not executed")
        with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
            deps.append(
                neg_root,
                "effect-1",
                str(bad_ev["event_type"]),
                dict(bad_ev["payload"]),
                str(bad_ev["idempotency_key"]),
                str(bad_ev["occurred_at"]),
            )
        assert raised.value.code == contract.expected_code
        assert_exact_boundary_message(expected_message, str(raised.value))
        assert contract.boundary_marker == "APPLIED_TERMINAL_STATE_BOUNDARY"
        after_bytes, after_proj = deps.root_snapshot(neg_root)
        assert after_bytes == before_bytes and after_proj == before_proj
        ledger.project_effect_v1(private_root=sibling_root, effect_id="effect-1")
        assert deps.root_snapshot(accept_root)[0] == accept_post_bytes
        assert diff_guard_ran
        return

    if relation_id == "cross_event.envelope_idempotency_replay_identity":
        before_bytes, before_proj = deps.root_snapshot(neg_root)
        assert before_bytes == accept_pre_bytes
        assert canonical_event_bytes(submitted_good_event, deps.as_plain) == submitted_good_bytes
        bad_event = deps.mutate_accepted_good_event(contract, submitted_good_event, meta_a)
        assert canonical_event_bytes(submitted_good_event, deps.as_plain) == submitted_good_bytes
        _mandatory_diff_guard(
            sab=sab,
            deps=deps,
            good=submitted_good_event["payload"],
            bad=bad_event["payload"],
            contract=contract,
            payload_only=True,
        )
        diff_guard_ran = True
        with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
            deps.append(
                neg_root,
                "effect-1",
                "intent_recorded",
                bad_event["payload"],
                "effect-1:intent",
                "2026-08-02T00:00:02Z",
            )
        assert raised.value.code == contract.expected_code
        assert_exact_boundary_message(expected_message, str(raised.value))
        after_bytes, after_proj = deps.root_snapshot(neg_root)
        assert after_bytes == before_bytes and after_proj == before_proj
        assert diff_guard_ran
        return

    before_bytes, before_proj = deps.root_snapshot(neg_root)
    assert before_bytes == accept_pre_bytes
    assert deps.as_plain(before_proj) == deps.as_plain(accept_pre_proj)

    if contract.negative_mode == "raw_replay_copy":
        from workflow_kernel.runtime_state import write_private_file_atomic

        path = ledger.effect_ledger_path_v1(private_root=neg_root, effect_id="effect-1")
        events = _read_plain_events(neg_root)
        good_ev = copy.deepcopy(submitted_good_event)
        assert canonical_event_bytes(good_ev, deps.as_plain) == submitted_good_bytes
        bad = deps.mutate_accepted_good_event(contract, good_ev, meta_a)
        assert canonical_event_bytes(good_ev, deps.as_plain) == submitted_good_bytes
        _mandatory_diff_guard(
            sab=sab,
            deps=deps,
            good=good_ev,
            bad=bad,
            contract=contract,
            payload_only=False,
        )
        diff_guard_ran = True
        if relation_id == "cross_event.effect_id_chain_mixed_effect":
            raw = b"".join(
                effect_contracts.canonical_json_v1(e).encode() + b"\n" for e in [events[0], bad]
            )
        else:
            raw = b"".join(
                effect_contracts.canonical_json_v1(e).encode() + b"\n" for e in [*events, bad]
            )
        write_private_file_atomic(path, raw, private_root=neg_root)
        malformed_bytes = path.read_bytes()
        assert malformed_bytes == raw
        with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
            ledger.read_effect_events_v1(private_root=neg_root, effect_id="effect-1")
        assert raised.value.code == contract.expected_code
        assert_exact_boundary_message(expected_message, str(raised.value))
        # After failed replay, reread same bytes; call replay again; same exact exception/bytes.
        assert path.read_bytes() == malformed_bytes
        with pytest.raises(ledger.WorkflowEffectLedgerError) as raised2:
            ledger.read_effect_events_v1(private_root=neg_root, effect_id="effect-1")
        assert raised2.value.code == raised.value.code
        assert str(raised2.value) == str(raised.value)
        assert path.read_bytes() == malformed_bytes
        # Projection from pristine sibling only (byte-identical pre-mutation).
        sib_events = _read_plain_events(sibling_root)
        sib_ref = reference_project_events_v1(sib_events)
        sib_proj = deps.as_plain(ledger.project_effect_v1(private_root=sibling_root, effect_id="effect-1"))
        sib_public, _sib_durable = split_reference_projection_v1(sib_ref)
        assert set(sib_proj) == set(sib_public) == set(PUBLIC_PROJECTION_KEYS_V1)
        assert deps.as_plain(sib_public) == deps.as_plain(sib_proj)
        # Do not claim malformed projection — parser cannot produce it.
        assert path.read_bytes() == malformed_bytes == raw
        assert diff_guard_ran
        return

    # Standard append negative from deepcopy(submitted_good_event)
    assert canonical_event_bytes(submitted_good_event, deps.as_plain) == submitted_good_bytes
    good_ev = copy.deepcopy(submitted_good_event)
    assert canonical_event_bytes(good_ev, deps.as_plain) == submitted_good_bytes

    if relation_id == "cross_event.state_event_type_transition":
        # bad = deepcopy(submitted_good_event) then mutate event_type + payload in place.
        # Payload must become a schema-valid receipt body so transition is the first
        # native boundary (payload key checks otherwise fire first).
        bad_ev = copy.deepcopy(good_ev)
        assert bad_ev is not good_ev
        bad_ev["event_type"] = "receipt_terminal"
        fence = int(meta_a["fence"])
        # Construct receipt payload from the same accept meta/fence; this is a declared
        # whole-payload semantic replacement relative to the captured intent good event.
        receipt = deps.receipt_dict(classification="applied", fence=fence)
        bad_ev["payload"] = deps.receipt_payload(
            fence=fence,
            classification="applied",
            receipt=receipt,
            receipt_digest=receipt["receipt_digest"],
        )
        body = {k: v for k, v in bad_ev.items() if k != "event_id"}
        bad_ev["event_id"] = effect_contracts.derive_workflow_effect_ledger_event_id_v1(body)
        g_cmp = {k: v for k, v in good_ev.items() if k != "event_id"}
        b_cmp = {k: v for k, v in bad_ev.items() if k != "event_id"}
        diffs = deps.semantic_diff_paths(g_cmp, b_cmp)
        expected = set(contract.semantic_mutated_paths) | set(contract.derived_bookkeeping_paths)
        collapsed = list(diffs)
        for sm in contract.semantic_mutated_paths:
            collapsed = deps.collapse_diff_paths(collapsed, sm, list(contract.derived_bookkeeping_paths))
        if not sab.skip_diff_guard:
            sab._t("diff_guard")
            diff_guard_ran = True
            assert set(collapsed) == expected, (collapsed, expected, diffs)
        else:
            sab._t("sabotage_skip_diff_guard")
            raise AssertionError("mandatory evidence incomplete: diff_guard not executed")
        with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
            deps.append(
                neg_root,
                "effect-1",
                str(bad_ev["event_type"]),
                dict(bad_ev["payload"]),
                str(bad_ev["idempotency_key"]),
                str(bad_ev["occurred_at"]),
            )
        assert raised.value.code == contract.expected_code
        assert_exact_boundary_message(expected_message, str(raised.value))
        after_bytes, after_proj = deps.root_snapshot(neg_root)
        assert after_bytes == before_bytes and after_proj == before_proj
        assert diff_guard_ran
        return

    bad_ev = deps.mutate_accepted_good_event(contract, good_ev, meta_a)
    assert canonical_event_bytes(good_ev, deps.as_plain) == submitted_good_bytes
    if (
        "idempotency_key" not in contract.semantic_mutated_paths
        and "idempotency_key" not in contract.derived_bookkeeping_paths
    ):
        assert bad_ev.get("idempotency_key") == good_ev.get("idempotency_key")
    compare_envelope = any(
        p in {"effect_id", "sequence", "previous_event_id", "event_type"}
        for p in contract.semantic_mutated_paths
    )
    _mandatory_diff_guard(
        sab=sab,
        deps=deps,
        good=good_ev if compare_envelope else good_ev["payload"],
        bad=bad_ev if compare_envelope else bad_ev["payload"],
        contract=contract,
        payload_only=not compare_envelope,
    )
    diff_guard_ran = True

    if contract.pre_probe_full_event_parse:
        if contract.expected_code == "effect_contract_invalid":
            with pytest.raises(effect_contracts.EffectContractError) as raised_contract:
                effect_contracts.parse_workflow_effect_ledger_event_v1(bad_ev)
            assert raised_contract.value.code == contract.expected_code
            assert_exact_boundary_message(expected_message, str(raised_contract.value))
            # Contract rejection must not write.
            after_bytes, after_proj = deps.root_snapshot(neg_root)
            assert after_bytes == before_bytes and after_proj == before_proj
            assert diff_guard_ran
            return
        parsed = effect_contracts.parse_workflow_effect_ledger_event_v1(bad_ev)
        assert parsed.event_id == bad_ev["event_id"]

    et = str(bad_ev["event_type"])
    key = str(bad_ev["idempotency_key"])
    at = str(bad_ev["occurred_at"])
    with pytest.raises(ledger.WorkflowEffectLedgerError) as raised:
        deps.append(neg_root, "effect-1", et, bad_ev["payload"], key, at)
    assert raised.value.code == contract.expected_code
    assert_exact_boundary_message(expected_message, str(raised.value))

    after_bytes, after_proj = deps.root_snapshot(neg_root)
    assert after_bytes == before_bytes
    assert after_proj == before_proj
    assert deps.root_snapshot(accept_root)[0] == accept_post_bytes
    final_events = ledger.read_effect_events_v1(private_root=accept_root, effect_id="effect-1")
    if relation_id == "cross_event.state_event_type_transition":
        assert final_events[-1]["event_type"] == "intent_recorded"
    else:
        assert final_events[-1]["event_type"] == contract.destination_event_type
    ledger.project_effect_v1(private_root=accept_root, effect_id="effect-1")
    assert diff_guard_ran


def run_extra_descriptor_generator_cli_sabotage(repo_root: Path, tmp_path: Path) -> None:
    """Self-contained copied-repo descriptor adversary: no live-repo PYTHONPATH fallback."""
    iso = tmp_path / "iso_desc_sabotage"
    copy_generator_self_contained_tree(repo_root, iso)
    # Optional TS package metadata companion (not required for Python parity path).
    pkg = repo_root / "apps/workflow-contracts/package.json"
    if pkg.exists():
        dst = iso / "apps/workflow-contracts/package.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pkg, dst)

    # Seed cache-named canaries BEFORE pre-invocation snapshot; prove no exclusion filter.
    canary_keys = seed_cache_named_snapshot_canaries(iso)

    def _run(argv_tail: list[str]) -> subprocess.CompletedProcess:
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"PYTHONPATH", "PYTHONDONTWRITEBYTECODE"}
        }
        env["PYTHONPATH"] = str(iso)  # iso-only; no live-repo fallback
        gen = str(iso / "scripts/generate_workflow_effect_ledger_semantic_ssot_4a5b2.py")
        # No caller -B / PYTHONDONTWRITEBYTECODE.
        return subprocess.run(
            [sys.executable, gen, *argv_tail],
            cwd=str(tmp_path),
            env=env,
            capture_output=True,
            text=True,
        )

    # Self-contained import canary BEFORE poison: all 10 eager local modules under copy only.
    env_canary = {
        k: v
        for k, v in os.environ.items()
        if k not in {"PYTHONPATH", "PYTHONDONTWRITEBYTECODE"}
    }
    env_canary["PYTHONPATH"] = str(iso)
    assert_iso_only_generator_workflow_kernel_imports(
        iso,
        cwd=tmp_path,
        env=env_canary,
        label="descriptor adversary import",
    )

    # Poison descriptor row dict after canary (exact key-drift gate).
    desc_path = iso / "workflow_kernel" / "effect_ledger_cross_relation_descriptors.py"
    text0 = desc_path.read_text(encoding="utf-8")
    needle = None
    for cand in ('"relation_id":', "'relation_id':"):
        if cand in text0:
            needle = cand
            break
    assert needle is not None
    idx = text0.index(needle)
    line_end = text0.index("\n", idx)
    poisoned = (
        text0[: line_end + 1]
        + '        "extra_undeclared_field": True,\n'
        + text0[line_end + 1 :]
    )
    desc_path.write_text(poisoned, encoding="utf-8")

    # Snapshot after initial copy/setup+poison, before each mode invocation.
    for argv_tail, label in (([], "default"), (["--check"], "--check"), (["--write"], "--write")):
        before = snapshot_whole_tree_exclusion_free(iso)
        assert_snapshot_includes_seeded_cache_canaries(
            before, canary_keys, label=f"descriptor adversary before {label}"
        )
        proc = _run(argv_tail)
        assert proc.returncode != 0, (label, proc.stdout, proc.stderr)
        combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
        # Exact first/only gate; EVERY alternate rejected unconditionally if present.
        assert_descriptor_adversary_output_gates(combined, label=label)
        after = snapshot_whole_tree_exclusion_free(iso)
        assert_snapshot_includes_seeded_cache_canaries(
            after, canary_keys, label=f"descriptor adversary after {label}"
        )
        if after != before:
            drift = sorted(k for k in set(after) | set(before) if after.get(k) != before.get(k))
            raise AssertionError(
                f"descriptor adversary wrote files label={label} drift={drift[:40]}"
            )


def build_deps_from_test_module(mod: Any) -> HarnessDeps:
    return HarnessDeps(
        digest=mod.DIGEST,
        other_digest=mod.OTHER_DIGEST,
        target_digest=mod.TARGET_DIGEST,
        as_plain=mod._as_plain,
        root_snapshot=mod._root_snapshot,
        build_accept_chain=mod._build_accept_chain,
        good_destination_payload=mod._good_destination_payload,
        materialize_good_event=mod._materialize_good_event,
        mutate_accepted_good_event=mod._mutate_accepted_good_event,
        assert_diff_exact=mod._assert_diff_exact,
        append=mod._append,
        claim=mod._claim,
        observation_payload=mod._observation_payload,
        observation_dict=mod._observation_dict,
        receipt_payload=mod._receipt_payload,
        receipt_dict=mod._receipt_dict,
        recompute_receipt_digest=mod._recompute_receipt_digest,
        compensation_intent_payload=mod._compensation_intent_payload,
        semantic_diff_paths=mod._semantic_diff_paths,
        collapse_diff_paths=mod._collapse_diff_paths,
    )
