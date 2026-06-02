#!/usr/bin/env python3
"""Semantic health probes for MCP tools and servers.

Goes beyond the basic liveness check (``server.session is not None``) to
validate that MCP tools are *actually usable* from a real client perspective:

1. **Schema validity** — tool input schemas are well-formed JSON Schema that
   providers won't reject at definition time.
2. **Capability consistency** — the server's advertised capabilities (tools,
   resources, prompts) match what we can actually enumerate.
3. **Responsiveness** — a lightweight ``list_tools`` round-trip completes
   within a configurable deadline.
4. **Error propagation** — calling a tool with intentionally invalid
   arguments returns a structured error, not a hang or crash.
5. **Resource/Prompt availability** — if the server advertises resources or
   prompts, listing them succeeds.
6. **Tool roundtrip** — calls each tool with minimal valid arguments
   generated from its schema and verifies the response is structured
   content (not a hang, crash, or empty response).
7. **Description injection** — scans tool descriptions for prompt injection
   patterns that could manipulate the agent.
8. **Provider schema compatibility** — checks that tool schemas don't use
   JSON Schema features that providers (OpenAI, Anthropic) reject.
9. **Tool name collisions** — detects MCP tools whose names collide with
   built-in Hermes tools, which would cause registration failures.
10. **MCP loop health** — verifies the background event loop is alive and
    responsive, not stuck or crashed.

Usage::

    from tools.mcp_semantic_probe import run_semantic_probes, ProbeResult

    results = run_semantic_probes()           # probe all configured servers
    for r in results:
        print(r.server, r.status, r.findings)

Integration points:
    - ``get_mcp_status()`` includes ``semantic_health`` when probes are run
    - ``hermes mcp test <server>`` shows probe findings
    - Standalone script: ``python -m tools.mcp_semantic_probe``
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Probe result types
# ---------------------------------------------------------------------------

class ProbeStatus(str, Enum):
    """Overall health status from semantic probes."""
    HEALTHY = "healthy"         # all probes passed
    DEGRADED = "degraded"       # some non-critical probes failed
    UNHEALTHY = "unhealthy"     # critical probes failed (server unusable)
    UNREACHABLE = "unreachable" # could not connect at all


class FindingSeverity(str, Enum):
    """Severity of a single probe finding."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class Finding:
    """A single observation from a semantic probe."""
    probe_name: str
    severity: FindingSeverity
    message: str
    details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        d: Dict[str, Any] = {
            "probe": self.probe_name,
            "severity": self.severity.value,
            "message": self.message,
        }
        if self.details:
            d["details"] = self.details
        return d


@dataclass
class ProbeResult:
    """Aggregated result of all semantic probes for one MCP server."""
    server_name: str
    status: ProbeStatus
    findings: List[Finding] = field(default_factory=list)
    latency_ms: Optional[float] = None
    tool_count: int = 0
    resource_count: int = 0
    prompt_count: int = 0
    probes_run: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "server": self.server_name,
            "status": self.status.value,
            "latency_ms": round(self.latency_ms, 1) if self.latency_ms else None,
            "tools": self.tool_count,
            "resources": self.resource_count,
            "prompts": self.prompt_count,
            "probes_run": self.probes_run,
            "findings": [f.to_dict() for f in self.findings],
        }


# ---------------------------------------------------------------------------
# Schema validation helpers
# ---------------------------------------------------------------------------

# JSON Schema types that are valid in tool input schemas.
_VALID_SCHEMA_TYPES = {
    "object", "array", "string", "number", "integer", "boolean", "null",
}

# Regex for $ref patterns that point to valid local definitions.
_LOCAL_REF_RE = re.compile(r"^#/\$defs/[^/]+$")


def _validate_schema_node(
    node: Any, path: str, findings: List[Finding], server_name: str
) -> None:
    """Recursively validate a JSON Schema node for common issues."""
    if not isinstance(node, dict):
        return

    # Check type field
    schema_type = node.get("type")
    if schema_type is not None and schema_type not in _VALID_SCHEMA_TYPES:
        findings.append(Finding(
            probe_name="schema_validation",
            severity=FindingSeverity.WARNING,
            message=f"Invalid schema type '{schema_type}' at {path}",
            details={"path": path, "type": schema_type},
        ))

    # Check $ref format
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/") and not _LOCAL_REF_RE.match(ref):
        # Allow #/$defs/... but flag others
        if not ref.startswith("#/$defs/"):
            findings.append(Finding(
                probe_name="schema_validation",
                severity=FindingSeverity.WARNING,
                message=f"Non-standard $ref '{ref}' at {path} (expected #/$defs/...)",
                details={"path": path, "ref": ref},
            ))

    # Check properties consistency
    if "required" in node and "properties" in node:
        required = node.get("required", [])
        properties = node.get("properties", {})
        if isinstance(required, list) and isinstance(properties, dict):
            missing = [r for r in required if r not in properties]
            if missing:
                findings.append(Finding(
                    probe_name="schema_validation",
                    severity=FindingSeverity.ERROR,
                    message=f"Required fields {missing} not in properties at {path}",
                    details={"path": path, "missing": missing},
                ))

    # Recurse into nested schemas
    for key in ("properties", "$defs", "definitions", "patternProperties"):
        sub = node.get(key)
        if isinstance(sub, dict):
            for k, v in sub.items():
                _validate_schema_node(v, f"{path}.{key}.{k}", findings, server_name)

    if "items" in node:
        _validate_schema_node(node["items"], f"{path}.items", findings, server_name)

    for key in ("anyOf", "oneOf", "allOf"):
        sub = node.get(key)
        if isinstance(sub, list):
            for i, item in enumerate(sub):
                _validate_schema_node(item, f"{path}.{key}[{i}]", findings, server_name)

    if "not" in node:
        _validate_schema_node(node["not"], f"{path}.not", findings, server_name)


def _validate_tool_schemas(
    tools: List[Any], server_name: str, findings: List[Finding]
) -> int:
    """Validate all tool schemas and return count of tools with issues."""
    issues = 0
    for tool in tools:
        name = getattr(tool, "name", "unknown")
        schema = getattr(tool, "inputSchema", None)
        if schema is None:
            findings.append(Finding(
                probe_name="schema_validation",
                severity=FindingSeverity.WARNING,
                message=f"Tool '{name}' has no inputSchema",
                details={"tool": name},
            ))
            issues += 1
            continue

        before = len(findings)
        _validate_schema_node(schema, f"tool:{name}", findings, server_name)
        if len(findings) > before:
            issues += 1

    return issues


# ---------------------------------------------------------------------------
# Core probe implementations
# ---------------------------------------------------------------------------

def _probe_connectivity(
    server_name: str,
    connect_fn,
    config: dict,
    findings: List[Finding],
    timeout: float = 30.0,
) -> Optional[Any]:
    """Probe: can we connect to the server within the deadline?"""
    start = time.monotonic()
    try:
        import asyncio
        server = asyncio.get_event_loop().run_until_complete(
            asyncio.wait_for(connect_fn(server_name, config), timeout=timeout)
        )
        elapsed = (time.monotonic() - start) * 1000
        if elapsed > 5000:
            findings.append(Finding(
                probe_name="connectivity",
                severity=FindingSeverity.WARNING,
                message=f"Slow connection: {elapsed:.0f}ms (threshold: 5000ms)",
                details={"latency_ms": round(elapsed, 1)},
            ))
        return server
    except Exception as exc:
        elapsed = (time.monotonic() - start) * 1000
        findings.append(Finding(
            probe_name="connectivity",
            severity=FindingSeverity.CRITICAL,
            message=f"Connection failed: {type(exc).__name__}: {exc}",
            details={"latency_ms": round(elapsed, 1)},
        ))
        return None


def _probe_tool_enumeration(
    server: Any, server_name: str, findings: List[Finding]
) -> List[Any]:
    """Probe: can we enumerate tools from the connected server?"""
    tools = getattr(server, "_tools", [])
    if not tools:
        findings.append(Finding(
            probe_name="tool_enumeration",
            severity=FindingSeverity.WARNING,
            message="Server returned no tools",
        ))
    return tools


def _probe_schema_validity(
    tools: List[Any], server_name: str, findings: List[Finding]
) -> None:
    """Probe: are tool schemas well-formed?"""
    issues = _validate_tool_schemas(tools, server_name, findings)
    if issues == 0 and tools:
        findings.append(Finding(
            probe_name="schema_validation",
            severity=FindingSeverity.INFO,
            message=f"All {len(tools)} tool schemas valid",
        ))


def _probe_capability_consistency(
    server: Any, server_name: str, findings: List[Finding]
) -> None:
    """Probe: do advertised capabilities match actual tool list?"""
    init_result = getattr(server, "initialize_result", None)
    if init_result is None:
        return

    caps = getattr(init_result, "capabilities", None)
    if caps is None:
        return

    # Check tools capability
    tools_cap = getattr(caps, "tools", None)
    tools_list = getattr(server, "_tools", [])
    if tools_cap is not None and not tools_list:
        findings.append(Finding(
            probe_name="capability_consistency",
            severity=FindingSeverity.WARNING,
            message="Server advertises tools capability but returned no tools",
        ))

    # Check resources capability
    resources_cap = getattr(caps, "resources", None)
    if resources_cap is not None:
        # We can't enumerate resources here without a session, but we note
        # that the server claims to support them.
        findings.append(Finding(
            probe_name="capability_consistency",
            severity=FindingSeverity.INFO,
            message="Server advertises resources capability",
        ))

    # Check prompts capability
    prompts_cap = getattr(caps, "prompts", None)
    if prompts_cap is not None:
        findings.append(Finding(
            probe_name="capability_consistency",
            severity=FindingSeverity.INFO,
            message="Server advertises prompts capability",
        ))


def _probe_error_propagation(
    server: Any, server_name: str, findings: List[Finding]
) -> None:
    """Probe: does the server handle invalid arguments gracefully?

    Calls the first tool with clearly invalid arguments to verify
    the server returns a structured error rather than hanging or crashing.
    """
    tools = getattr(server, "_tools", [])
    if not tools:
        return

    session = getattr(server, "session", None)
    if session is None:
        findings.append(Finding(
            probe_name="error_propagation",
            severity=FindingSeverity.ERROR,
            message="No session available for error propagation test",
        ))
        return

    target_tool = tools[0]
    tool_name = getattr(target_tool, "name", "unknown")

    try:
        # Call with nonsense arguments — should return isError=True
        start = time.monotonic()
        result = asyncio.get_event_loop().run_until_complete(
            asyncio.wait_for(
                session.call_tool(tool_name, arguments={"__invalid_probe__": True}),
                timeout=10.0,
            )
        )
        elapsed = (time.monotonic() - start) * 1000

        if hasattr(result, "isError") and result.isError:
            # Good — server properly reports errors
            findings.append(Finding(
                probe_name="error_propagation",
                severity=FindingSeverity.INFO,
                message=f"Tool '{tool_name}' correctly returns structured errors",
                details={"latency_ms": round(elapsed, 1)},
            ))
        elif hasattr(result, "content"):
            # Server accepted invalid args — not necessarily bad, some tools
            # are lenient. Just note it.
            findings.append(Finding(
                probe_name="error_propagation",
                severity=FindingSeverity.INFO,
                message=f"Tool '{tool_name}' accepted invalid args (lenient mode)",
                details={"latency_ms": round(elapsed, 1)},
            ))
    except asyncio.TimeoutError:
        findings.append(Finding(
            probe_name="error_propagation",
            severity=FindingSeverity.ERROR,
            message=f"Tool '{tool_name}' timed out on invalid args (10s)",
        ))
    except Exception as exc:
        findings.append(Finding(
            probe_name="error_propagation",
            severity=FindingSeverity.WARNING,
            message=f"Tool '{tool_name}' raised on invalid args: {type(exc).__name__}",
            details={"error": str(exc)[:200]},
        ))


def _probe_resource_listing(
    server: Any, server_name: str, findings: List[Finding]
) -> int:
    """Probe: if resources are advertised, can we list them?"""
    init_result = getattr(server, "initialize_result", None)
    if init_result is None:
        return 0

    caps = getattr(init_result, "capabilities", None)
    if caps is None or getattr(caps, "resources", None) is None:
        return 0

    session = getattr(server, "session", None)
    if session is None:
        return 0

    try:
        result = asyncio.get_event_loop().run_until_complete(
            asyncio.wait_for(session.list_resources(), timeout=10.0)
        )
        count = len(getattr(result, "resources", []) or [])
        findings.append(Finding(
            probe_name="resource_listing",
            severity=FindingSeverity.INFO,
            message=f"Listed {count} resource(s)",
            details={"count": count},
        ))
        return count
    except asyncio.TimeoutError:
        findings.append(Finding(
            probe_name="resource_listing",
            severity=FindingSeverity.WARNING,
            message="Resource listing timed out (10s)",
        ))
        return 0
    except Exception as exc:
        # Method not found is expected for servers that don't implement it
        err_msg = str(exc).lower()
        if "method not found" in err_msg or "-32601" in err_msg:
            findings.append(Finding(
                probe_name="resource_listing",
                severity=FindingSeverity.INFO,
                message="Server does not implement list_resources (capability advertised but not implemented)",
            ))
        else:
            findings.append(Finding(
                probe_name="resource_listing",
                severity=FindingSeverity.WARNING,
                message=f"Resource listing failed: {type(exc).__name__}",
            ))
        return 0


def _probe_prompt_listing(
    server: Any, server_name: str, findings: List[Finding]
) -> int:
    """Probe: if prompts are advertised, can we list them?"""
    init_result = getattr(server, "initialize_result", None)
    if init_result is None:
        return 0

    caps = getattr(init_result, "capabilities", None)
    if caps is None or getattr(caps, "prompts", None) is None:
        return 0

    session = getattr(server, "session", None)
    if session is None:
        return 0

    try:
        result = asyncio.get_event_loop().run_until_complete(
            asyncio.wait_for(session.list_prompts(), timeout=10.0)
        )
        count = len(getattr(result, "prompts", []) or [])
        findings.append(Finding(
            probe_name="prompt_listing",
            severity=FindingSeverity.INFO,
            message=f"Listed {count} prompt(s)",
            details={"count": count},
        ))
        return count
    except asyncio.TimeoutError:
        findings.append(Finding(
            probe_name="prompt_listing",
            severity=FindingSeverity.WARNING,
            message="Prompt listing timed out (10s)",
        ))
        return 0
    except Exception as exc:
        err_msg = str(exc).lower()
        if "method not found" in err_msg or "-32601" in err_msg:
            findings.append(Finding(
                probe_name="prompt_listing",
                severity=FindingSeverity.INFO,
                message="Server does not implement list_prompts (capability advertised but not implemented)",
            ))
        else:
            findings.append(Finding(
                probe_name="prompt_listing",
                severity=FindingSeverity.WARNING,
                message=f"Prompt listing failed: {type(exc).__name__}",
            ))
        return 0


# ---------------------------------------------------------------------------
# Minimal valid argument generation from JSON Schema
# ---------------------------------------------------------------------------

def _generate_minimal_args(schema: Any, depth: int = 0) -> Any:
    """Generate minimal valid arguments from a JSON Schema.

    Returns a dict with valid values for all ``required`` fields.  For
    non-required fields, returns an empty dict.  Handles nested objects,
    arrays, and common type constraints.  Stops recursing at depth 10 to
    avoid infinite loops on self-referential schemas.
    """
    if not isinstance(schema, dict) or depth > 10:
        return {}

    schema_type = schema.get("type", "object")

    if schema_type == "object":
        result = {}
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))

        for prop_name, prop_schema in properties.items():
            if prop_name in required:
                result[prop_name] = _generate_minimal_value(prop_schema, depth)
        return result
    elif schema_type == "array":
        # Arrays as top-level schema — return empty list
        return []
    else:
        return _generate_minimal_value(schema, depth)


def _generate_minimal_value(schema: Any, depth: int = 0) -> Any:
    """Generate a single minimal valid value for a schema node."""
    if not isinstance(schema, dict) or depth > 10:
        return "probe"

    # Handle anyOf/oneOf — pick the first branch
    for key in ("anyOf", "oneOf"):
        branches = schema.get(key)
        if isinstance(branches, list) and branches:
            return _generate_minimal_value(branches[0], depth + 1)

    schema_type = schema.get("type", "string")

    # Handle enum — pick first value
    enum_vals = schema.get("enum")
    if isinstance(enum_vals, list) and enum_vals:
        return enum_vals[0]

    # Handle const
    if "const" in schema:
        return schema["const"]

    # Handle default
    if "default" in schema:
        return schema["default"]

    if schema_type == "string":
        return schema.get("default", "probe")
    elif schema_type in ("integer", "number"):
        minimum = schema.get("minimum", 1)
        return minimum
    elif schema_type == "boolean":
        return True
    elif schema_type == "array":
        items_schema = schema.get("items", {"type": "string"})
        return [_generate_minimal_value(items_schema, depth + 1)]
    elif schema_type == "object":
        return _generate_minimal_args(schema, depth + 1)
    elif schema_type == "null":
        return None
    else:
        return "probe"


# ---------------------------------------------------------------------------
# Real-client probes
# ---------------------------------------------------------------------------

def _probe_tool_roundtrip(
    server: Any, server_name: str, findings: List[Finding],
    timeout: float = 15.0, max_tools: int = 5,
) -> None:
    """Probe: call each tool with minimal valid args and verify response.

    This is the real-client semantic test — it generates valid arguments
    from the tool's input schema and calls the tool through the MCP session,
    verifying that:
    - The tool responds within the timeout
    - The response has ``content`` (not just empty)
    - The response is not an error (unless we generated bad args)

    At most ``max_tools`` tools are tested to keep probe time bounded.
    """
    tools = getattr(server, "_tools", [])
    if not tools:
        return

    session = getattr(server, "session", None)
    if session is None:
        findings.append(Finding(
            probe_name="tool_roundtrip",
            severity=FindingSeverity.ERROR,
            message="No session available for tool roundtrip test",
        ))
        return

    tested = 0
    for tool in tools:
        if tested >= max_tools:
            break

        tool_name = getattr(tool, "name", "unknown")
        schema = getattr(tool, "inputSchema", None)

        # Generate minimal valid args
        if schema and isinstance(schema, dict):
            args = _generate_minimal_args(schema)
        else:
            args = {}

        try:
            start = time.monotonic()
            result = asyncio.get_event_loop().run_until_complete(
                asyncio.wait_for(
                    session.call_tool(tool_name, arguments=args),
                    timeout=timeout,
                )
            )
            elapsed = (time.monotonic() - start) * 1000

            # Check response structure
            if hasattr(result, "content") and result.content:
                # Tool returned structured content — good
                content_types = [
                    type(c).__name__ for c in result.content
                ]
                findings.append(Finding(
                    probe_name="tool_roundtrip",
                    severity=FindingSeverity.INFO,
                    message=f"Tool '{tool_name}' roundtrip OK ({elapsed:.0f}ms, {len(result.content)} block(s))",
                    details={
                        "tool": tool_name,
                        "latency_ms": round(elapsed, 1),
                        "content_blocks": len(result.content),
                        "content_types": content_types,
                    },
                ))
            elif hasattr(result, "isError") and result.isError:
                # Tool returned an error — this is acceptable for a probe
                # since our minimal args may not be semantically valid
                err_text = ""
                if hasattr(result, "content") and result.content:
                    err_text = getattr(result.content[0], "text", "")[:100]
                findings.append(Finding(
                    probe_name="tool_roundtrip",
                    severity=FindingSeverity.INFO,
                    message=f"Tool '{tool_name}' responded with error (args may be insufficient): {err_text}",
                    details={
                        "tool": tool_name,
                        "latency_ms": round(elapsed, 1),
                        "is_error": True,
                    },
                ))
            else:
                # Empty or unexpected response
                findings.append(Finding(
                    probe_name="tool_roundtrip",
                    severity=FindingSeverity.WARNING,
                    message=f"Tool '{tool_name}' returned empty/unexpected response",
                    details={
                        "tool": tool_name,
                        "latency_ms": round(elapsed, 1),
                    },
                ))

            tested += 1

        except asyncio.TimeoutError:
            findings.append(Finding(
                probe_name="tool_roundtrip",
                severity=FindingSeverity.ERROR,
                message=f"Tool '{tool_name}' timed out on roundtrip ({timeout}s)",
                details={"tool": tool_name},
            ))
            tested += 1
        except Exception as exc:
            findings.append(Finding(
                probe_name="tool_roundtrip",
                severity=FindingSeverity.WARNING,
                message=f"Tool '{tool_name}' roundtrip failed: {type(exc).__name__}",
                details={"tool": tool_name, "error": str(exc)[:200]},
            ))
            tested += 1


def _probe_description_injection(
    tools: List[Any], server_name: str, findings: List[Finding],
) -> int:
    """Probe: scan tool descriptions for prompt injection patterns.

    Returns the number of tools with suspicious descriptions.
    Uses the same patterns as ``mcp_tool._scan_mcp_description`` but
    operates on already-parsed tool objects rather than requiring import.
    """
    injection_patterns = [
        (re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
         "prompt override attempt"),
        (re.compile(r"you\s+are\s+now\s+a", re.I),
         "identity override attempt"),
        (re.compile(r"your\s+new\s+(task|role|instructions?)\s+(is|are)", re.I),
         "task override attempt"),
        (re.compile(r"system\s*:\s*", re.I),
         "system prompt injection"),
        (re.compile(r"<\s*(system|human|assistant)\s*>", re.I),
         "role tag injection"),
        (re.compile(r"do\s+not\s+(tell|inform|mention|reveal)", re.I),
         "concealment instruction"),
        (re.compile(r"(curl|wget|fetch)\s+https?://", re.I),
         "network command in description"),
        (re.compile(r"exec\s*\(|eval\s*\(", re.I),
         "code execution reference"),
    ]

    flagged = 0
    for tool in tools:
        name = getattr(tool, "name", "unknown")
        desc = getattr(tool, "description", "") or ""
        if not desc:
            continue

        reasons = []
        for pattern, reason in injection_patterns:
            if pattern.search(desc):
                reasons.append(reason)

        if reasons:
            flagged += 1
            findings.append(Finding(
                probe_name="description_injection",
                severity=FindingSeverity.WARNING,
                message=f"Tool '{name}' description contains suspicious patterns: {'; '.join(reasons)}",
                details={"tool": name, "reasons": reasons},
            ))

    if flagged == 0 and tools:
        findings.append(Finding(
            probe_name="description_injection",
            severity=FindingSeverity.INFO,
            message=f"All {len(tools)} tool descriptions clean",
        ))

    return flagged


# Provider-specific schema constraints that cause silent failures or
# rejections at definition time.
#
# OpenAI function calling constraints (as of 2025-03):
#   - Schema must be type: object at root
#   - No `not`, `if`/`then`/`else`, `patternProperties`
#   - `$ref` only within `$defs` at root level
#   - `additionalProperties` accepted but not deeply nested
#   - Max depth ~10 levels
#
# Anthropic tool constraints (as of 2025-03):
#   - Schema must be type: object at root
#   - No `not`, `if`/`then`/`else`
#   - `$ref` not supported
#   - `additionalProperties: false` recommended

_PROVIDER_UNSUPPORTED_KEYWORDS = {"not", "if", "then", "else", "patternProperties"}
_PROVIDER_MAX_SCHEMA_DEPTH = 12


def _check_schema_depth(node: Any, current: int = 0) -> int:
    """Return the maximum nesting depth of a JSON Schema node."""
    if not isinstance(node, dict) or current > 20:
        return current
    max_d = current
    for key in ("properties", "$defs", "definitions", "patternProperties"):
        sub = node.get(key)
        if isinstance(sub, dict):
            for v in sub.values():
                max_d = max(max_d, _check_schema_depth(v, current + 1))
    if "items" in node:
        max_d = max(max_d, _check_schema_depth(node["items"], current + 1))
    for key in ("anyOf", "oneOf", "allOf"):
        sub = node.get(key)
        if isinstance(sub, list):
            for item in sub:
                max_d = max(max_d, _check_schema_depth(item, current + 1))
    return max_d


def _probe_provider_schema_compat(
    tools: List[Any], server_name: str, findings: List[Finding],
) -> int:
    """Probe: check tool schemas against provider constraints.

    OpenAI and Anthropic both reject certain JSON Schema features at
    tool-definition time.  This probe flags schemas that would cause
    silent tool drops or API errors when the agent tries to use them.

    Returns the number of tools with compatibility issues.
    """
    issues = 0
    for tool in tools:
        name = getattr(tool, "name", "unknown")
        schema = getattr(tool, "inputSchema", None)
        if not schema or not isinstance(schema, dict):
            continue

        tool_issues = []

        # Check root type
        if schema.get("type") and schema["type"] != "object":
            tool_issues.append(f"root type is '{schema['type']}' (providers expect 'object')")

        # Check for unsupported keywords (walk the whole schema)
        def _find_keywords(node: Any, path: str) -> None:
            if not isinstance(node, dict):
                return
            for kw in _PROVIDER_UNSUPPORTED_KEYWORDS:
                if kw in node:
                    tool_issues.append(f"unsupported keyword '{kw}' at {path}")
            for key in ("properties", "$defs", "definitions"):
                sub = node.get(key)
                if isinstance(sub, dict):
                    for k, v in sub.items():
                        _find_keywords(v, f"{path}.{key}.{k}")
            if "items" in node:
                _find_keywords(node["items"], f"{path}.items")
            for key in ("anyOf", "oneOf", "allOf"):
                sub = node.get(key)
                if isinstance(sub, list):
                    for i, item in enumerate(sub):
                        _find_keywords(item, f"{path}.{key}[{i}]")

        _find_keywords(schema, f"tool:{name}")

        # Check depth
        depth = _check_schema_depth(schema)
        if depth > _PROVIDER_MAX_SCHEMA_DEPTH:
            tool_issues.append(f"schema depth {depth} exceeds provider limit ({_PROVIDER_MAX_SCHEMA_DEPTH})")

        # Check for $ref outside $defs (OpenAI rejects these)
        def _find_bad_refs(node: Any, path: str) -> None:
            if not isinstance(node, dict):
                return
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/") and not ref.startswith("#/$defs/"):
                tool_issues.append(f"non-standard $ref '{ref}' at {path}")
            for key in ("properties", "$defs", "definitions"):
                sub = node.get(key)
                if isinstance(sub, dict):
                    for k, v in sub.items():
                        _find_bad_refs(v, f"{path}.{key}.{k}")
            if "items" in node:
                _find_bad_refs(node["items"], f"{path}.items")

        _find_bad_refs(schema, f"tool:{name}")

        if tool_issues:
            issues += 1
            findings.append(Finding(
                probe_name="provider_schema_compat",
                severity=FindingSeverity.WARNING,
                message=f"Tool '{name}' has {len(tool_issues)} provider compatibility issue(s)",
                details={"tool": name, "issues": tool_issues[:5]},  # cap for readability
            ))

    if issues == 0 and tools:
        findings.append(Finding(
            probe_name="provider_schema_compat",
            severity=FindingSeverity.INFO,
            message=f"All {len(tools)} tool schemas provider-compatible",
        ))

    return issues


def _probe_tool_name_collisions(
    tools: List[Any], server_name: str, findings: List[Finding],
) -> int:
    """Probe: detect MCP tools whose names collide with built-in Hermes tools.

    Name collisions cause registration failures — the MCP tool silently
    overwrites (or is overwritten by) the built-in, which breaks agent
    behavior in subtle ways.

    Returns the number of collisions detected.
    """
    # Get built-in tool names from the registry
    builtin_names: set = set()
    try:
        from tools.registry import registry
        builtin_names = set(registry._tools.keys()) if hasattr(registry, "_tools") else set()
    except (ImportError, AttributeError):
        # Fallback: check toolsets module for known core tools
        try:
            from toolsets import _HERMES_CORE_TOOLS
            builtin_names = set(_HERMES_CORE_TOOLS)
        except ImportError:
            pass

    if not builtin_names:
        # Can't check — not an error, just note it
        findings.append(Finding(
            probe_name="name_collisions",
            severity=FindingSeverity.INFO,
            message="Skipped collision check (builtin tool list unavailable)",
        ))
        return 0

    collisions = 0
    for tool in tools:
        name = getattr(tool, "name", "unknown")
        if name in builtin_names:
            collisions += 1
            findings.append(Finding(
                probe_name="name_collisions",
                severity=FindingSeverity.ERROR,
                message=f"Tool '{name}' collides with built-in Hermes tool",
                details={"tool": name, "server": server_name},
            ))

    if collisions == 0:
        findings.append(Finding(
            probe_name="name_collisions",
            severity=FindingSeverity.INFO,
            message=f"No name collisions with {len(builtin_names)} built-in tools",
        ))

    return collisions


def _probe_mcp_loop_health(
    server_name: str, findings: List[Finding],
) -> None:
    """Probe: verify the MCP background event loop is alive and responsive.

    The MCP subsystem runs a dedicated background event loop in a daemon
    thread.  If this loop is stuck or crashed, all MCP tool calls will
    hang silently.  This probe checks:
    1. The loop thread exists and is alive
    2. The loop is running (not stopped)
    3. A trivial coroutine completes within 5 seconds
    """
    try:
        from tools.mcp_tool import _mcp_loop, _mcp_thread
    except ImportError:
        findings.append(Finding(
            probe_name="mcp_loop_health",
            severity=FindingSeverity.INFO,
            message="MCP module not importable — loop health check skipped",
        ))
        return

    # Check thread liveness
    if _mcp_thread is None:
        findings.append(Finding(
            probe_name="mcp_loop_health",
            severity=FindingSeverity.INFO,
            message="MCP loop thread not started (no servers configured?)",
        ))
        return

    if not _mcp_thread.is_alive():
        findings.append(Finding(
            probe_name="mcp_loop_health",
            severity=FindingSeverity.CRITICAL,
            message="MCP loop thread is dead — all MCP tools will be unresponsive",
        ))
        return

    # Check loop state
    if _mcp_loop is None:
        findings.append(Finding(
            probe_name="mcp_loop_health",
            severity=FindingSeverity.CRITICAL,
            message="MCP loop reference is None despite thread being alive",
        ))
        return

    if _mcp_loop.is_closed():
        findings.append(Finding(
            probe_name="mcp_loop_health",
            severity=FindingSeverity.CRITICAL,
            message="MCP event loop is closed — all MCP tools will fail",
        ))
        return

    if not _mcp_loop.is_running():
        findings.append(Finding(
            probe_name="mcp_loop_health",
            severity=FindingSeverity.WARNING,
            message="MCP event loop exists but is not running",
        ))
        return

    # Responsiveness check — schedule a trivial coroutine
    try:
        import concurrent.futures  # noqa: F811 — local import for error handling

        async def _ping():
            return "pong"

        start = time.monotonic()
        future = asyncio.run_coroutine_threadsafe(_ping(), _mcp_loop)
        result = future.result(timeout=5.0)
        elapsed = (time.monotonic() - start) * 1000

        if result == "pong":
            if elapsed > 1000:
                findings.append(Finding(
                    probe_name="mcp_loop_health",
                    severity=FindingSeverity.WARNING,
                    message=f"MCP loop responsive but slow ({elapsed:.0f}ms)",
                    details={"latency_ms": round(elapsed, 1)},
                ))
            else:
                findings.append(Finding(
                    probe_name="mcp_loop_health",
                    severity=FindingSeverity.INFO,
                    message=f"MCP loop healthy and responsive ({elapsed:.0f}ms)",
                    details={"latency_ms": round(elapsed, 1)},
                ))
        else:
            findings.append(Finding(
                probe_name="mcp_loop_health",
                severity=FindingSeverity.WARNING,
                message=f"MCP loop returned unexpected value: {result!r}",
            ))
    except Exception as exc:
        err_name = type(exc).__name__
        if err_name == "TimeoutError" or "timeout" in str(exc).lower():
            findings.append(Finding(
                probe_name="mcp_loop_health",
                severity=FindingSeverity.ERROR,
                message="MCP loop ping timed out (5s) — loop may be stuck",
            ))
        else:
            findings.append(Finding(
                probe_name="mcp_loop_health",
                severity=FindingSeverity.WARNING,
                message=f"MCP loop ping failed: {err_name}: {exc}",
            ))


# ---------------------------------------------------------------------------
# Main probe orchestrator
# ---------------------------------------------------------------------------

def _determine_status(findings: List[Finding]) -> ProbeStatus:
    """Derive overall status from individual findings."""
    severities = {f.severity for f in findings}

    if FindingSeverity.CRITICAL in severities:
        return ProbeStatus.UNHEALTHY
    if FindingSeverity.ERROR in severities:
        return ProbeStatus.DEGRADED
    if FindingSeverity.WARNING in severities:
        return ProbeStatus.DEGRADED
    return ProbeStatus.HEALTHY


def run_semantic_probe_for_server(
    server_name: str,
    config: dict,
    *,
    include_error_probe: bool = False,
    include_resource_probe: bool = True,
    include_prompt_probe: bool = True,
) -> ProbeResult:
    """Run all semantic probes against a single MCP server.

    This is the main entry point for per-server probing. It connects to
    the server (or uses an already-connected session), runs probes, and
    returns a structured result.

    Args:
        server_name: Logical name of the MCP server.
        config: Server config dict from ``mcp_servers`` in config.yaml.
        include_error_probe: If True, call a tool with invalid args to
            test error propagation. Disabled by default because it has
            side effects on some servers.
        include_resource_probe: If True, attempt to list resources.
        include_prompt_probe: If True, attempt to list prompts.

    Returns:
        ProbeResult with status, findings, and metrics.
    """
    findings: List[Finding] = []
    probes_run: List[str] = []
    start = time.monotonic()

    # Import MCP internals lazily
    try:
        from tools.mcp_tool import (
            _connect_server,
            _ensure_mcp_loop,
            _run_on_mcp_loop,
            _stop_mcp_loop,
        )
    except ImportError:
        return ProbeResult(
            server_name=server_name,
            status=ProbeStatus.UNREACHABLE,
            findings=[Finding(
                probe_name="import",
                severity=FindingSeverity.CRITICAL,
                message="MCP SDK not available",
            )],
            probes_run=["import"],
        )

    _ensure_mcp_loop()

    server = None
    try:
        # Phase 1: Connectivity
        probes_run.append("connectivity")
        connect_timeout = config.get("connect_timeout", 30)

        async def _do_connect():
            return await _connect_server(server_name, config)

        try:
            server = _run_on_mcp_loop(
                asyncio.wait_for(_do_connect(), timeout=connect_timeout)
                if False else _do_connect,
                timeout=connect_timeout + 5,
            )
            elapsed = (time.monotonic() - start) * 1000

            if elapsed > 5000:
                findings.append(Finding(
                    probe_name="connectivity",
                    severity=FindingSeverity.WARNING,
                    message=f"Slow connection: {elapsed:.0f}ms",
                    details={"latency_ms": round(elapsed, 1)},
                ))
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            findings.append(Finding(
                probe_name="connectivity",
                severity=FindingSeverity.CRITICAL,
                message=f"Connection failed: {type(exc).__name__}: {exc}",
                details={"latency_ms": round(elapsed, 1)},
            ))
            return ProbeResult(
                server_name=server_name,
                status=ProbeStatus.UNREACHABLE,
                findings=findings,
                latency_ms=elapsed,
                probes_run=probes_run,
            )

        # Phase 2: Tool enumeration
        probes_run.append("tool_enumeration")
        tools = getattr(server, "_tools", [])
        tool_count = len(tools)
        if not tools:
            findings.append(Finding(
                probe_name="tool_enumeration",
                severity=FindingSeverity.WARNING,
                message="Server returned no tools",
            ))

        # Phase 3: Schema validation
        probes_run.append("schema_validation")
        _probe_schema_validity(tools, server_name, findings)

        # Phase 4: Capability consistency
        probes_run.append("capability_consistency")
        _probe_capability_consistency(server, server_name, findings)

        # Phase 5: Error propagation (optional)
        if include_error_probe:
            probes_run.append("error_propagation")
            _probe_error_propagation(server, server_name, findings)

        # Phase 6: Resource listing (optional)
        resource_count = 0
        if include_resource_probe:
            probes_run.append("resource_listing")
            resource_count = _probe_resource_listing(server, server_name, findings)

        # Phase 7: Prompt listing (optional)
        prompt_count = 0
        if include_prompt_probe:
            probes_run.append("prompt_listing")
            prompt_count = _probe_prompt_listing(server, server_name, findings)

        # Phase 8: Description injection scan
        probes_run.append("description_injection")
        _probe_description_injection(tools, server_name, findings)

        # Phase 9: Provider schema compatibility
        probes_run.append("provider_schema_compat")
        _probe_provider_schema_compat(tools, server_name, findings)

        # Phase 10: Tool name collision detection
        probes_run.append("name_collisions")
        _probe_tool_name_collisions(tools, server_name, findings)

        # Phase 11: MCP loop health
        probes_run.append("mcp_loop_health")
        _probe_mcp_loop_health(server_name, findings)

        # Phase 12: Tool roundtrip (optional — has side effects)
        if include_error_probe:
            probes_run.append("tool_roundtrip")
            _probe_tool_roundtrip(server, server_name, findings)

        total_elapsed = (time.monotonic() - start) * 1000

        return ProbeResult(
            server_name=server_name,
            status=_determine_status(findings),
            findings=findings,
            latency_ms=total_elapsed,
            tool_count=tool_count,
            resource_count=resource_count,
            prompt_count=prompt_count,
            probes_run=probes_run,
        )

    finally:
        # Always clean up the probe connection
        if server is not None:
            try:
                import asyncio
                _run_on_mcp_loop(server.shutdown, timeout=10)
            except Exception:
                pass
        try:
            _stop_mcp_loop()
        except Exception:
            pass


def run_semantic_probes(
    *,
    include_error_probe: bool = False,
    include_resource_probe: bool = True,
    include_prompt_probe: bool = True,
) -> List[ProbeResult]:
    """Run semantic probes against all configured MCP servers.

    Returns a list of ProbeResult, one per enabled server.
    """
    try:
        from tools.mcp_tool import _load_mcp_config, _parse_boolish
    except ImportError:
        logger.debug("MCP SDK not available — skipping semantic probes")
        return []

    servers = _load_mcp_config()
    if not servers:
        return []

    results = []
    for name, cfg in servers.items():
        if not _parse_boolish(cfg.get("enabled", True), default=True):
            continue
        result = run_semantic_probe_for_server(
            name, cfg,
            include_error_probe=include_error_probe,
            include_resource_probe=include_resource_probe,
            include_prompt_probe=include_prompt_probe,
        )
        results.append(result)

    return results


def get_semantic_health_summary() -> Dict[str, dict]:
    """Run probes and return a dict keyed by server name.

    Suitable for embedding in ``get_mcp_status()`` output.
    """
    results = run_semantic_probes()
    return {r.server_name: r.to_dict() for r in results}


# ---------------------------------------------------------------------------
# Probe for already-connected servers (in-process, no reconnect)
# ---------------------------------------------------------------------------

def probe_connected_server(server_name: str) -> Optional[ProbeResult]:
    """Run semantic probes against an already-connected MCP server.

    Uses the existing session — does NOT create a new connection.
    Returns None if the server is not currently connected.
    """
    try:
        from tools.mcp_tool import _servers, _lock
    except ImportError:
        return None

    with _lock:
        server = _servers.get(server_name)

    if server is None or getattr(server, "session", None) is None:
        return None

    findings: List[Finding] = []
    probes_run: List[str] = []
    start = time.monotonic()

    # Tool enumeration
    probes_run.append("tool_enumeration")
    tools = getattr(server, "_tools", [])
    tool_count = len(tools)

    # Schema validation
    probes_run.append("schema_validation")
    _probe_schema_validity(tools, server_name, findings)

    # Capability consistency
    probes_run.append("capability_consistency")
    _probe_capability_consistency(server, server_name, findings)

    # Resource listing
    probes_run.append("resource_listing")
    resource_count = _probe_resource_listing(server, server_name, findings)

    # Prompt listing
    probes_run.append("prompt_listing")
    prompt_count = _probe_prompt_listing(server, server_name, findings)

    # Description injection scan
    probes_run.append("description_injection")
    _probe_description_injection(tools, server_name, findings)

    # Provider schema compatibility
    probes_run.append("provider_schema_compat")
    _probe_provider_schema_compat(tools, server_name, findings)

    # Tool name collision detection
    probes_run.append("name_collisions")
    _probe_tool_name_collisions(tools, server_name, findings)

    # MCP loop health
    probes_run.append("mcp_loop_health")
    _probe_mcp_loop_health(server_name, findings)

    elapsed = (time.monotonic() - start) * 1000

    return ProbeResult(
        server_name=server_name,
        status=_determine_status(findings),
        findings=findings,
        latency_ms=elapsed,
        tool_count=tool_count,
        resource_count=resource_count,
        prompt_count=prompt_count,
        probes_run=probes_run,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _main():
    """Standalone CLI for running semantic probes."""
    import sys

    print("MCP Semantic Health Probe")
    print("=" * 50)

    results = run_semantic_probes()

    if not results:
        print("\nNo MCP servers configured or MCP SDK not available.")
        sys.exit(0)

    has_issues = False
    for r in results:
        status_icon = {
            ProbeStatus.HEALTHY: "✓",
            ProbeStatus.DEGRADED: "⚠",
            ProbeStatus.UNHEALTHY: "✗",
            ProbeStatus.UNREACHABLE: "✗",
        }.get(r.status, "?")

        print(f"\n{status_icon} {r.server_name} [{r.status.value}]")
        print(f"  Tools: {r.tool_count} | Resources: {r.resource_count} | Prompts: {r.prompt_count}")
        if r.latency_ms:
            print(f"  Latency: {r.latency_ms:.0f}ms")
        print(f"  Probes: {', '.join(r.probes_run)}")

        for f in r.findings:
            sev_icon = {
                FindingSeverity.INFO: "  ",
                FindingSeverity.WARNING: "⚠ ",
                FindingSeverity.ERROR: "✗ ",
                FindingSeverity.CRITICAL: "✗✗",
            }.get(f.severity, "  ")
            print(f"  {sev_icon} [{f.probe_name}] {f.message}")

        if r.status in (ProbeStatus.UNHEALTHY, ProbeStatus.UNREACHABLE):
            has_issues = True

    print()
    summary = {r.status.value: sum(1 for x in results if x.status == r.status) for r in results}
    print(f"Summary: {', '.join(f'{v} {k}' for k, v in summary.items())}")

    # Output JSON for programmatic consumption
    if "--json" in sys.argv:
        print("\n--- JSON ---")
        print(json.dumps([r.to_dict() for r in results], indent=2))

    sys.exit(1 if has_issues else 0)


if __name__ == "__main__":
    _main()
