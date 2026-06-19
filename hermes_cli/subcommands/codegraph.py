"""`hermes codegraph` subcommand parser.

Registers the local code knowledge-graph control surface backed by
codebase-memory-mcp. The command is safe-by-default: doctor and smoke are
read/check operations; enable writes only Hermes-scoped activation files unless
--merge-config is explicit.
"""

from __future__ import annotations

from typing import Callable


def build_codegraph_parser(subparsers, *, cmd_codegraph: Callable) -> None:
    parser = subparsers.add_parser(
        "codegraph",
        help="Manage local code knowledge-graph integration (codebase-memory-mcp)",
        description=(
            "Diagnose, enable, and smoke-test the local code knowledge-graph MCP "
            "integration powered by codebase-memory-mcp. Defaults are local-only "
            "and side-effect safe."
        ),
    )
    parser.add_argument(
        "--home",
        default=None,
        help="Hermes home (default: HERMES_HOME or ~/.hermes)",
    )
    parser.add_argument(
        "--binary",
        default=None,
        help="codebase-memory-mcp binary path/name (default: PATH lookup)",
    )
    sub = parser.add_subparsers(dest="codegraph_command")

    list_p = sub.add_parser(
        "list",
        aliases=["ls"],
        help="Show configured codegraph activation, snippet, cache, and merge state",
    )
    list_p.add_argument("--json", action="store_true", help="Emit JSON")
    list_p.set_defaults(func=cmd_codegraph)

    install = sub.add_parser(
        "install",
        help="Download release binary into $HERMES_HOME/bin after checksum verification",
    )
    install.add_argument("--tag", help="Release tag (default: latest)")
    install.add_argument("--asset", help="Exact release asset name (default: detected for OS/arch)")
    install.add_argument("--ui", action="store_true", help="Install UI bundle instead of stdio-only bundle")
    install.add_argument("--dry-run", action="store_true", help="Resolve release/checksum without downloading asset")
    install.add_argument("--force", action="store_true", help="Overwrite existing binary")
    install.add_argument("--json", action="store_true", help="Emit JSON")
    install.set_defaults(func=cmd_codegraph)

    doctor = sub.add_parser(
        "doctor",
        help="Check binary, version, Hermes-scoped cache, and activation state",
    )
    doctor.add_argument("--json", action="store_true", help="Emit JSON")
    doctor.set_defaults(func=cmd_codegraph)

    enable = sub.add_parser(
        "enable",
        help="Write Hermes-scoped codegraph activation files; merge config only with --merge-config",
    )
    enable.add_argument("--dry-run", action="store_true", help="Plan writes without changing files")
    enable.add_argument("--force", action="store_true", help="Overwrite existing activation snippet")
    enable.add_argument(
        "--merge-config",
        action="store_true",
        help="Also merge mcp_servers.codebase_memory into config.yaml with backup",
    )
    enable.add_argument("--json", action="store_true", help="Emit JSON")
    enable.set_defaults(func=cmd_codegraph)

    smoke = sub.add_parser(
        "smoke",
        help="Run index/list/search/trace smoke test against a repo",
    )
    smoke.add_argument("--repo", required=True, help="Repository/project directory to index")
    smoke.add_argument("--query", help="Search query for search_graph smoke")
    smoke.add_argument("--function-name", help="Function name for trace_path smoke")
    smoke.add_argument(
        "--mode",
        default="fast",
        choices=["fast", "moderate", "full"],
        help="Index mode for smoke test",
    )
    smoke.add_argument("--json", action="store_true", help="Emit JSON")
    smoke.set_defaults(func=cmd_codegraph)

    parser.set_defaults(func=cmd_codegraph)
