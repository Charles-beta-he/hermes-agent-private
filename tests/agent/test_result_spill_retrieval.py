"""Regression tests for result spilling + retrieval continuity."""

import re

import agent.result_spiller as result_spiller_module
import tools.retrieve_result_tool as retrieve_result_tool_module
from agent.result_spiller import ResultSpiller
from agent.token_compressor import compress_tool_output


def test_spill_handle_points_to_working_retrieve_tool(monkeypatch, tmp_path):
    class PatchedResultSpiller(ResultSpiller):
        def __init__(self, cache_dir=tmp_path, threshold=0):
            super().__init__(cache_dir=cache_dir, threshold=threshold)

    monkeypatch.setattr(result_spiller_module, "ResultSpiller", PatchedResultSpiller)
    monkeypatch.setattr(retrieve_result_tool_module, "ResultSpiller", PatchedResultSpiller)

    output = "\n".join(f"line {i}" for i in range(1200))
    handle = compress_tool_output(output, tool_name="terminal")

    match = re.search(r"Hash: ([0-9a-f]{12})", handle)
    assert match, handle
    assert "Retrieve with: retrieve_result(hash='" in handle
    assert "Other modes: lines/query/tail" in handle

    recovered = retrieve_result_tool_module.retrieve_result_tool(hash=match.group(1), mode="head", lines=3)

    assert recovered == "line 0\nline 1\nline 2"


def test_retrieve_tool_default_summary_mode_returns_spill_metadata(monkeypatch, tmp_path):
    class PatchedResultSpiller(ResultSpiller):
        def __init__(self, cache_dir=tmp_path, threshold=0):
            super().__init__(cache_dir=cache_dir, threshold=threshold)

    monkeypatch.setattr(retrieve_result_tool_module, "ResultSpiller", PatchedResultSpiller)

    spiller = PatchedResultSpiller()
    ref = spiller.spill("alpha\nbeta\ngamma", tool_name="search_files")

    summary = retrieve_result_tool_module.retrieve_result_tool(hash=ref["hash"])

    assert "[Spilled: 3 lines" in summary
    assert "tool=search_files" in summary
    assert "alpha" in summary
    assert "hash=" in summary


def test_retrieve_summary_mode_keeps_head_and_tail_anchors(monkeypatch, tmp_path):
    class PatchedResultSpiller(ResultSpiller):
        def __init__(self, cache_dir=tmp_path, threshold=0):
            super().__init__(cache_dir=cache_dir, threshold=threshold)

    monkeypatch.setattr(retrieve_result_tool_module, "ResultSpiller", PatchedResultSpiller)

    spiller = PatchedResultSpiller()
    payload = "\n".join(["alpha", "beta", "gamma", "delta", "omega", "tail-1", "tail-2"])
    ref = spiller.spill(payload, tool_name="terminal")

    summary = retrieve_result_tool_module.retrieve_result_tool(hash=ref["hash"], lines=4)

    assert "0000|alpha" in summary
    assert "0001|beta" in summary
    assert "0005|tail-1" in summary
    assert "0006|tail-2" in summary
    assert "middle lines omitted" in summary
    assert f"retrieve_result(hash='{ref['hash']}', mode='lines'" in summary
