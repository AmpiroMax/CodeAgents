"""Registers the native tool surface (filesystem / shell / web / git /
plans / workspace control / RAG).

The actual handler implementations live in dedicated subsystem modules:

- ``codeagents.tools.filesystem``      — read/write/edit/ls/grep/glob/...
- ``codeagents.tools.shell``           — bash, run_python, conda, run_tests
- ``codeagents.tools.web``             — web_fetch, web_search, curl, docs_search
- ``codeagents.tools.git``             — git_diff / git_status
- ``codeagents.tools.plans``           — create_plan / patch_plan / mark_step / list_plans
- ``codeagents.tools.workspace_ctl``   — cd / change_workspace
- ``codeagents.tools.rag``             — recall_chat / search_code

This module only wires those handlers into the ``ToolRegistry`` and owns
the "compact tool surface" decisions (``enabled=False`` to hide aliases
from the model context while keeping the python-level handler callable).
"""

from __future__ import annotations

from typing import Any

from codeagents.core.permissions import Permission
from codeagents.core.workspace import Workspace
from codeagents.tools import ToolRegistry, ToolSpec
from codeagents.tools.filesystem import (
    cat,
    create_file,
    edit_file,
    glob_files,
    grep,
    head,
    list_directory,
    ls,
    mkdir,
    mv,
    propose_patch,
    pwd,
    read_file,
    rm,
    search,
    tail,
    wc,
    with_diagnostics as _with_diagnostics,
    write_file,
)
from codeagents.tools.git import git_diff, git_status
from codeagents.tools.plans import (
    create_plan_tool,
    list_plans_tool,
    mark_step_tool,
    patch_plan_tool,
)
from codeagents.tools.rag import recall_chat, search_code
from codeagents.tools.shell import (
    SAFE_COMMANDS,
    conda_activate,
    conda_create,
    conda_deactivate,
    conda_env_list,
    conda_run,
    dangerous_shell,
    flake8,
    pip_install,
    python_module,
    run_python,
    run_tests,
    safe_shell,
)
from codeagents.tools.web import curl, docs_search, web_fetch, web_search
from codeagents.tools.workspace_ctl import cd, change_workspace

__all__ = [
    "register_code_tools",
    # Re-exports for tests/back-compat: the public symbols other modules
    # historically imported from ``codeagents.tools.native_code``.
    "SAFE_COMMANDS",
    "cat",
    "cd",
    "change_workspace",
    "conda_activate",
    "conda_create",
    "conda_deactivate",
    "conda_env_list",
    "conda_run",
    "create_file",
    "create_plan_tool",
    "curl",
    "dangerous_shell",
    "docs_search",
    "edit_file",
    "flake8",
    "git_diff",
    "git_status",
    "glob_files",
    "grep",
    "head",
    "list_directory",
    "list_plans_tool",
    "ls",
    "mark_step_tool",
    "mkdir",
    "mv",
    "patch_plan_tool",
    "pip_install",
    "propose_patch",
    "pwd",
    "python_module",
    "read_file",
    "recall_chat",
    "rm",
    "run_python",
    "run_tests",
    "safe_shell",
    "search",
    "search_code",
    "tail",
    "wc",
    "web_fetch",
    "web_search",
    "write_file",
]


def register_code_tools(
    registry: ToolRegistry, workspace: Workspace, *, lsp: Any | None = None
) -> None:
    # ── Compact tool surface ────────────────────────────────────────────
    # Visible to the model: a small core in the spirit of opencode
    # (read/write/edit/glob/grep/bash + workspace + plans + RAG + web).
    # Everything else (cat/head/tail/wc/mkdir/mv/conda_*/git_*/python_*/
    # safe_shell/list_directory/search/propose_patch/docs_search/curl)
    # stays *registered* (so direct ``agent.call_tool(...)`` and unit
    # tests keep working) but is hidden via ``enabled=False`` so its
    # description never bloats the model context.
    registry.register(
        ToolSpec(
            name="read_file",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Read a UTF-8 text file with line numbers. "
                "Params: path (required), offset=1, limit=200."
            ),
        ),
        handler=lambda args: read_file(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="pwd",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Print cwd, workspace_root, and whether the agent is read-only. Params: none."
            ),
        ),
        handler=lambda args: pwd(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="ls",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "List files in a directory. Params: path='.', all=false, long=false, max_results=200."
            ),
        ),
        handler=lambda args: ls(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="cat",
            kind="native",
            enabled=False,
            permission=Permission.READ_ONLY,
            description="Hidden: prefer read_file. Kept for direct API use.",
        ),
        handler=lambda args: cat(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="grep",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Ripgrep-style content search. Params: query (required), path='.', "
                "ignore_case=false, max_count=100."
            ),
        ),
        handler=lambda args: grep(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="head",
            kind="native",
            enabled=False,
            permission=Permission.READ_ONLY,
            description="Hidden: prefer read_file or `bash 'head -n N file'`.",
        ),
        handler=lambda args: head(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="tail",
            kind="native",
            enabled=False,
            permission=Permission.READ_ONLY,
            description="Hidden: prefer read_file or `bash 'tail -n N file'`.",
        ),
        handler=lambda args: tail(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="wc",
            kind="native",
            enabled=False,
            permission=Permission.READ_ONLY,
            description="Hidden: use `bash 'wc file'` instead.",
        ),
        handler=lambda args: wc(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="curl",
            kind="native",
            enabled=False,
            permission=Permission.NETWORK,
            description="Hidden: prefer web_fetch (handles HTML→md, caching).",
        ),
        handler=lambda args: curl(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="web_search",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Web search. Tries ollama/yandex first (round-robin), falls back to "
                "searxng/jina/brave. Params: query (required), limit=5, provider='auto'."
            ),
        ),
        handler=lambda args: web_search(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="web_fetch",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Fetch a URL and return {markdown, raw_html, links, cached}. Use to read docs, "
                "GitHub pages, blog posts; follow links[].url for navigation. "
                "PDF URLs (e.g. arxiv.org/pdf/<id> or any .pdf link) are auto-routed "
                "through read_pdf — content comes back as page-separated text in "
                "``content``/``markdown`` with extra ``pdf`` metadata. "
                "Params: url (required), provider='auto', max_chars=12000, no_cache=false."
            ),
            mcp_input_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Absolute URL (https:// or http://) of the page to fetch.",
                    },
                    "provider": {
                        "type": "string",
                        "enum": ["auto", "jina", "direct", "ollama"],
                        "description": "Fetch backend; ``auto`` picks the best available.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Cap on returned content length (defaults to ~32k).",
                    },
                    "no_cache": {
                        "type": "boolean",
                        "description": "Skip the local cache and always re-fetch.",
                    },
                },
                "required": ["url"],
                "additionalProperties": True,
            },
        ),
        handler=lambda args: web_fetch(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="docs_search",
            kind="native",
            enabled=False,
            permission=Permission.READ_ONLY,
            description="Hidden: use web_search with domain hint instead.",
        ),
        handler=lambda args: docs_search(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="recall_chat",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Semantic recall of earlier messages from the *current* chat. Use when the user "
                "references something you no longer have in context. Per-chat only. "
                "Returns hits sorted by relevance. Params: query (required), k=5."
            ),
            mcp_input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What you want to remember (plain English).",
                    },
                    "k": {
                        "type": "integer",
                        "description": "Number of recall hits (default 5, max 20).",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        handler=lambda args: recall_chat(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="search_code",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Semantic+lexical code search over the indexed workspace. Use for plain-English "
                "queries ('where do we encrypt passwords'); for exact strings prefer grep. "
                "Params: query (required), k=8, scope='workspace'|'current_dir'."
            ),
            mcp_input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Plain-language description or symbol name to search for.",
                    },
                    "k": {
                        "type": "integer",
                        "description": "Maximum number of results (default 8, capped at 50).",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["workspace", "current_dir"],
                        "description": "Restrict search to the agent's cwd subtree.",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        handler=lambda args: search_code(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="cd",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Change cwd. May leave workspace_root (then agent goes read-only). "
                "Trust boundary unchanged — use change_workspace to switch projects. "
                "Params: path (required)."
            ),
        ),
        handler=lambda args: cd(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="change_workspace",
            kind="native",
            permission=Permission.WORKSPACE_WRITE,
            description=(
                "Switch the workspace trust boundary to a new directory (resets approvals). "
                "Requires user confirmation. Params: path (required)."
            ),
        ),
        handler=lambda args: change_workspace(workspace, args),
    )

    # Plans subsystem — visible in every mode (READ_ONLY) so the agent
    # can both author plans (in plan mode) and execute them (in agent
    # mode). Plan tools declare a full mcp_input_schema (not the simple
    # TOML ParamSpec used by older tools) because their args nest
    # objects/arrays. Without this, AgentCore._invalid_tool_arguments
    # derives the allowed set from spec.params (empty here) and rejects
    # every call as "extra args".
    _plan_step_schema = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short verb-led step title (≤ 8 words).",
            },
            "detail": {
                "type": "string",
                "description": "What to do, files involved, what 'done' looks like.",
            },
        },
        "required": ["title", "detail"],
        "additionalProperties": False,
    }
    registry.register(
        ToolSpec(
            name="create_plan",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Create a plan pinned to the chat banner. Use in plan mode. "
                "Limit: 3 active per chat. Returns: id, status, markdown."
            ),
            mcp_input_schema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Plan title, ≤ 60 chars (e.g. 'Add SQLite audit log').",
                    },
                    "summary": {
                        "type": "string",
                        "description": (
                            "1–3 short paragraphs explaining context, key tradeoffs, "
                            "and the final shape of the change. NO numbered list here."
                        ),
                    },
                    "steps": {
                        "type": "array",
                        "description": (
                            "Ordered list of {title, detail} objects (≥ 1 step)."
                        ),
                        "items": _plan_step_schema,
                        "minItems": 1,
                    },
                },
                "required": ["title", "summary", "steps"],
                "additionalProperties": False,
            },
        ),
        handler=lambda args: create_plan_tool(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="patch_plan",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Edit a plan (title / summary / steps). Step status preserved by title match. "
                "At least one of title|summary|steps must be set."
            ),
            mcp_input_schema={
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string", "description": "Plan id to edit."},
                    "title": {"type": "string", "description": "New plan title."},
                    "summary": {"type": "string", "description": "New plan summary."},
                    "steps": {
                        "type": "array",
                        "description": "Replacement step list; preserves status by title match.",
                        "items": _plan_step_schema,
                    },
                },
                "required": ["plan_id"],
                "additionalProperties": False,
            },
        ),
        handler=lambda args: patch_plan_tool(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="mark_step",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Update one plan step's status (pending|in_progress|done|skipped). Call before "
                "and after each step when executing a plan."
            ),
            mcp_input_schema={
                "type": "object",
                "properties": {
                    "plan_id": {"type": "string", "description": "Plan id."},
                    "step_n": {
                        "type": "integer",
                        "description": "1-based step index.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "done", "skipped"],
                        "description": "New step status.",
                    },
                    "note": {
                        "type": "string",
                        "description": "Optional short freeform note shown under the step.",
                    },
                },
                "required": ["plan_id", "step_n", "status"],
                "additionalProperties": False,
            },
        ),
        handler=lambda args: mark_step_tool(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="list_plans",
            kind="native",
            permission=Permission.READ_ONLY,
            description="List plans (id/title/status/done count). Use to find a plan_id.",
            mcp_input_schema={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": [
                            "active",
                            "draft",
                            "building",
                            "completed",
                            "rejected",
                            "all",
                        ],
                        "description": "Optional status filter (default 'all').",
                    },
                },
                "additionalProperties": False,
            },
        ),
        handler=lambda args: list_plans_tool(workspace, args),
    )

    # Filesystem write tools.
    registry.register(
        ToolSpec(
            name="rm",
            kind="native",
            permission=Permission.SHELL_DANGEROUS,
            description=(
                "Remove a file or directory. The bash tool refuses any command containing "
                "rm; this is the only path to delete. Requires user confirmation. "
                "Params: path (required), recursive=false, force=false."
            ),
        ),
        handler=lambda args: rm(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="write_file",
            kind="native",
            permission=Permission.WORKSPACE_WRITE,
            description=(
                "Write/overwrite a file (creates parent dirs). "
                "Params: path (required), content (required)."
            ),
        ),
        handler=lambda args: _with_diagnostics(workspace, lsp, args, write_file(workspace, args)),
    )
    registry.register(
        ToolSpec(
            name="mkdir",
            kind="native",
            enabled=False,
            permission=Permission.WORKSPACE_WRITE,
            description="Hidden: use `bash 'mkdir -p path'`.",
        ),
        handler=lambda args: mkdir(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="mv",
            kind="native",
            enabled=False,
            permission=Permission.WORKSPACE_WRITE,
            description="Hidden: use `bash 'mv src dst'`.",
        ),
        handler=lambda args: mv(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="create_file",
            kind="native",
            enabled=False,
            permission=Permission.WORKSPACE_WRITE,
            description="Hidden: prefer write_file (overwrite) or edit_file.",
        ),
        handler=lambda args: create_file(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="edit_file",
            kind="native",
            permission=Permission.WORKSPACE_WRITE,
            description=(
                "Apply line-based edits to a file. Params: path (required), edits (array of "
                "{line, old_lines, new_lines}); legacy {old_text, new_text} also accepted. "
                "Atomic write, returns unified diff."
            ),
        ),
        handler=lambda args: _with_diagnostics(workspace, lsp, args, edit_file(workspace, args)),
    )
    registry.register(
        ToolSpec(
            name="list_directory",
            kind="native",
            enabled=False,
            permission=Permission.READ_ONLY,
            description="Hidden: prefer ls.",
        ),
        handler=lambda args: list_directory(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="glob_files",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Find files by glob pattern (e.g. '**/*.py'). "
                "Params: pattern (required), max_results=100."
            ),
        ),
        handler=lambda args: glob_files(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="search",
            kind="native",
            enabled=False,
            permission=Permission.READ_ONLY,
            description="Hidden: prefer grep (exact) or search_code (semantic).",
        ),
        handler=lambda args: search(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="propose_patch",
            kind="native",
            enabled=False,
            permission=Permission.PROPOSE,
            description="Hidden: prefer edit_file.",
        ),
        handler=lambda args: propose_patch(workspace, args),
    )

    # Shell / Python / Conda subsystem.
    registry.register(
        ToolSpec(
            name="safe_shell",
            kind="native",
            enabled=False,
            permission=Permission.SHELL_SAFE,
            description="Hidden: use the bash tool.",
        ),
        handler=lambda args: safe_shell(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="run_python",
            kind="native",
            enabled=False,
            permission=Permission.SHELL_SAFE,
            description="Hidden: use `bash 'python file.py ...'`.",
        ),
        handler=lambda args: run_python(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="python_module",
            kind="native",
            enabled=False,
            permission=Permission.SHELL_SAFE,
            description="Hidden: use `bash 'python -m <mod> ...'`.",
        ),
        handler=lambda args: python_module(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="flake8",
            kind="native",
            enabled=False,
            permission=Permission.SHELL_SAFE,
            description="Hidden: use `bash 'flake8 ...'`.",
        ),
        handler=lambda args: flake8(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="pip_install",
            kind="native",
            enabled=False,
            permission=Permission.NETWORK,
            description="Hidden: use `bash 'pip install ...'`.",
        ),
        handler=lambda args: pip_install(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="bash",
            kind="native",
            permission=Permission.SHELL_DANGEROUS,
            description=(
                "Run a shell command (bash). Requires user confirmation the first time per "
                "workspace. Anything that contains `rm` is rejected — use the rm tool for "
                "deletions. Params: command (required), cwd (optional), timeout=60."
            ),
        ),
        handler=lambda args: dangerous_shell(workspace, args),
    )
    # ``shell`` kept as an alias of ``bash`` for older code paths; hidden so
    # the model only sees one shell entrypoint.
    registry.register(
        ToolSpec(
            name="shell",
            kind="native",
            enabled=False,
            permission=Permission.SHELL_DANGEROUS,
            description="Hidden alias of bash.",
        ),
        handler=lambda args: dangerous_shell(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="conda_env_list",
            kind="native",
            enabled=False,
            permission=Permission.READ_ONLY,
            description="Hidden: use `bash 'conda env list'`.",
        ),
        handler=lambda args: conda_env_list(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="conda_create",
            kind="native",
            enabled=False,
            permission=Permission.NETWORK,
            description="Hidden: use `bash 'conda create ...'`.",
        ),
        handler=lambda args: conda_create(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="conda_activate",
            kind="native",
            enabled=False,
            permission=Permission.SHELL_SAFE,
            description="Hidden: use `bash 'conda activate <name>'` (per-shell).",
        ),
        handler=lambda args: conda_activate(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="conda_deactivate",
            kind="native",
            enabled=False,
            permission=Permission.SHELL_SAFE,
            description="Hidden: clears active conda env (rare, callable via API).",
        ),
        handler=lambda args: conda_deactivate(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="conda_run",
            kind="native",
            enabled=False,
            permission=Permission.SHELL_SAFE,
            description="Hidden: use bash with the activated env.",
        ),
        handler=lambda args: conda_run(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="git_diff",
            kind="native",
            enabled=False,
            permission=Permission.READ_ONLY,
            description="Hidden: use `bash 'git diff'`.",
        ),
        handler=lambda args: git_diff(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="git_status",
            kind="native",
            enabled=False,
            permission=Permission.READ_ONLY,
            description="Hidden: use `bash 'git status'`.",
        ),
        handler=lambda args: git_status(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="run_tests",
            kind="native",
            enabled=False,
            permission=Permission.SHELL_SAFE,
            description="Hidden: use `bash 'pytest ...'` / `bash 'cargo test'`.",
        ),
        handler=lambda args: run_tests(workspace, args),
    )
    # Legacy ``lsp_query`` fallback was removed in the honest refactor.
    # When ``lsp`` is None the agent simply has no LSP tools (degrades to
    # read_file / search_code).
