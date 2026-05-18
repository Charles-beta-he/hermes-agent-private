"""Tests for tools/retrieve_result_tool.py."""

import json

from agent.result_spiller import ResultSpiller
from tools.registry import registry
from tools.retrieve_result_tool import (
    RETRIEVE_RESULT_SCHEMA,
    retrieve_result_tool,
)


def test_retrieve_result_tool_is_registered():
    schema = registry.get_schema("retrieve_result")
    assert schema is not None
    assert schema == RETRIEVE_RESULT_SCHEMA
    assert schema["function"]["name"] == "retrieve_result"


def test_retrieve_result_tool_requires_hash():
    result = json.loads(retrieve_result_tool())
    assert result["error"] == "Missing required parameter: hash"


def test_retrieve_result_tool_supports_hash_and_alias(tmp_path):
    spiller = ResultSpiller(cache_dir=tmp_path)
    ref = spiller.spill(
        "alpha\nbeta\ngamma\ndelta\nepsilon",
        tool_name="read_file",
        filepath="demo.txt",
    )

    via_hash = retrieve_result_tool(hash=ref["hash"], mode="head", lines=2, spiller=spiller)
    via_alias = retrieve_result_tool(content_hash=ref["hash"], mode="lines", start=1, end=3, spiller=spiller)

    assert via_hash == "alpha\nbeta"
    assert via_alias == "beta\ngamma"


def test_retrieve_result_tool_query_mode(tmp_path):
    spiller = ResultSpiller(cache_dir=tmp_path)
    ref = spiller.spill("first\nneedle\nthird\nneedle again\nlast")

    result = retrieve_result_tool(hash=ref["hash"], mode="query", query="needle", spiller=spiller)

    assert "needle" in result
    assert "2:" in result or "4:" in result
