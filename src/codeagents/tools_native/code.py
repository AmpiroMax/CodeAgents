from __future__ import annotations

import base64
import difflib
import fnmatch
import gzip
import json
import os
import shlex
import shutil
import ssl
import sqlite3
import subprocess
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from codeagents.permissions import Permission
from codeagents.tools import ToolRegistry, ToolSpec
from codeagents.workspace import Workspace, WorkspaceError


PROJECT_ROOT = Path(__file__).resolve().parents[3]
_LOCAL_CONFIG_CACHE: dict[str, Any] | None = None
_GIGACHAT_TOKEN_CACHE: dict[str, Any] = {}


SAFE_COMMANDS = {
    "cargo",
    "git",
    "ls",
    "python",
    "python3",
    "pytest",
    "rg",
    "wc",
    "head",
    "tail",
    "cat",
    "curl",
    "find",
    "grep",
    "echo",
    "flake8",
    "pip",
}


def register_code_tools(registry: ToolRegistry, workspace: Workspace) -> None:
    registry.register(
        ToolSpec(
            name="read_file",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Read a UTF-8 text file. Returns numbered lines. "
                "Params: path (required), offset (line#, default 1), limit (max lines, default 200)."
            ),
        ),
        handler=lambda args: read_file(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="pwd",
            kind="native",
            permission=Permission.READ_ONLY,
            description="Print the current workspace directory. Params: none.",
        ),
        handler=lambda args: pwd(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="ls",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "List files and directories like ls. "
                "Params: path (default '.'), all (bool), long (bool), max_results (default 200)."
            ),
        ),
        handler=lambda args: ls(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="cat",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Read a UTF-8 text file like cat. "
                "Params: path (required), offset (line#, default 1), limit (max lines, default 400)."
            ),
        ),
        handler=lambda args: cat(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="grep",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Search text like grep/rg inside the workspace. "
                "Params: query (required), path (default '.'), ignore_case (bool), max_count (default 100)."
            ),
        ),
        handler=lambda args: grep(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="head",
            kind="native",
            permission=Permission.READ_ONLY,
            description="Read the first lines of a text file. Params: path (required), lines (default 20).",
        ),
        handler=lambda args: head(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="tail",
            kind="native",
            permission=Permission.READ_ONLY,
            description="Read the last lines of a text file. Params: path (required), lines (default 20).",
        ),
        handler=lambda args: tail(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="wc",
            kind="native",
            permission=Permission.READ_ONLY,
            description="Count lines, words, and bytes for a text file. Params: path (required).",
        ),
        handler=lambda args: wc(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="curl",
            kind="native",
            permission=Permission.NETWORK,
            description=(
                "Make an HTTP request like curl. "
                "Params: url (required), method, headers, data, json, output_path, timeout, max_chars."
            ),
        ),
        handler=lambda args: curl(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="web_search",
            kind="native",
            permission=Permission.NETWORK,
            description=(
                "Search the web using cheap/free providers. "
                "Params: query (required), limit, provider=auto|searxng|jina|brave, language, time_range."
            ),
        ),
        handler=lambda args: web_search(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="docs_search",
            kind="native",
            permission=Permission.NETWORK,
            description=(
                "Search documentation-focused web results, optionally restricted to a domain. "
                "Params: query (required), domain, limit, fetch_results."
            ),
        ),
        handler=lambda args: docs_search(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="rm",
            kind="native",
            permission=Permission.SHELL_DANGEROUS,
            description=(
                "Remove a file or directory inside the current workspace. "
                "Requires user confirmation. Params: path (required), recursive (bool), force (bool)."
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
                "Write full content to a file, creating parent dirs. Overwrites existing. "
                "Params: path (required), content (required)."
            ),
        ),
        handler=lambda args: write_file(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="mkdir",
            kind="native",
            permission=Permission.WORKSPACE_WRITE,
            description=(
                "Create a directory inside the current workspace. "
                "Params: path (required), parents (bool, default true), exist_ok (bool, default true)."
            ),
        ),
        handler=lambda args: mkdir(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="mv",
            kind="native",
            permission=Permission.WORKSPACE_WRITE,
            description=(
                "Move or rename a file or directory inside the current workspace. "
                "Params: source (required), destination (required), overwrite (bool, default false)."
            ),
        ),
        handler=lambda args: mv(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="create_file",
            kind="native",
            permission=Permission.WORKSPACE_WRITE,
            description=(
                "Create a new file. Fails if file exists (use write_file to overwrite). "
                "Params: path (required), content (required)."
            ),
        ),
        handler=lambda args: create_file(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="edit_file",
            kind="native",
            permission=Permission.WORKSPACE_WRITE,
            description=(
                "Apply line-based edits to a file. Preferred params: "
                "path (required), edits (array of {line, old_lines, new_lines}). "
                "Legacy form still supported: path + old_text + new_text. "
                "Atomic write; returns a unified diff. `line` is 1-based in the "
                "original file; multiple edits are processed bottom-up so line "
                "numbers stay valid."
            ),
        ),
        handler=lambda args: edit_file(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="list_directory",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "List files/dirs at a path. Shows type, size, name. "
                "Params: path (default '.'), recursive (bool, default false), max_depth (int, default 2)."
            ),
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
                "Params: pattern (required), max_results (default 100)."
            ),
        ),
        handler=lambda args: glob_files(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="search",
            kind="native",
            permission=Permission.READ_ONLY,
            description=(
                "Search text/code with ripgrep. Returns file:line:match. "
                "Params: query (required string/regex), max_count (default 50)."
            ),
        ),
        handler=lambda args: search(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="propose_patch",
            kind="native",
            permission=Permission.PROPOSE,
            description=(
                "Generate a unified diff for review, does not apply. "
                "Params: path (required), new_text (required full content)."
            ),
        ),
        handler=lambda args: propose_patch(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="safe_shell",
            kind="native",
            permission=Permission.SHELL_SAFE,
            description=(
                "Run a shell command (allowlisted: git, python, cargo, rg, pytest, ls, etc). "
                "Params: command (required string). 60s timeout."
            ),
        ),
        handler=lambda args: safe_shell(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="run_python",
            kind="native",
            permission=Permission.SHELL_SAFE,
            description=(
                "Run a Python file inside the workspace. "
                "Params: path (required), args (array), module (bool), timeout (default 60). "
                "Uses the active conda env if one is selected."
            ),
        ),
        handler=lambda args: run_python(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="python_module",
            kind="native",
            permission=Permission.SHELL_SAFE,
            description=(
                "Run `python -m <module>` inside the workspace. "
                "Params: module (required), args (array), timeout (default 60). "
                "Uses the active conda env if one is selected."
            ),
        ),
        handler=lambda args: python_module(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="flake8",
            kind="native",
            permission=Permission.SHELL_SAFE,
            description=(
                "Run flake8 on a workspace path. "
                "Params: path (default '.'), args (array), timeout (default 60). "
                "Uses the active conda env if one is selected."
            ),
        ),
        handler=lambda args: flake8(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="pip_install",
            kind="native",
            permission=Permission.NETWORK,
            description=(
                "Install Python dependencies with pip in the active interpreter/env. "
                "Params: packages (array) or requirements (path), upgrade (bool)."
            ),
        ),
        handler=lambda args: pip_install(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="shell",
            kind="native",
            permission=Permission.SHELL_DANGEROUS,
            description=(
                "Run an arbitrary shell command. Requires user confirmation. "
                "Use for commands not covered by safe_shell (rm, brew, system tools). "
                "Params: command (required string). 60s timeout."
            ),
        ),
        handler=lambda args: dangerous_shell(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="conda_env_list",
            kind="native",
            permission=Permission.READ_ONLY,
            description="List conda environments. Params: none.",
        ),
        handler=lambda args: conda_env_list(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="conda_create",
            kind="native",
            permission=Permission.NETWORK,
            description=(
                "Create a conda environment. "
                "Params: name (required), python (default current), packages (array)."
            ),
        ),
        handler=lambda args: conda_create(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="conda_activate",
            kind="native",
            permission=Permission.SHELL_SAFE,
            description=(
                "Select a conda environment for future Python tools in this workspace. "
                "Params: name (required)."
            ),
        ),
        handler=lambda args: conda_activate(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="conda_deactivate",
            kind="native",
            permission=Permission.SHELL_SAFE,
            description="Clear the active conda environment for this workspace. Params: none.",
        ),
        handler=lambda args: conda_deactivate(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="conda_run",
            kind="native",
            permission=Permission.SHELL_SAFE,
            description=(
                "Run an allowlisted command in a conda env. "
                "Params: command (array or string), name (optional), timeout (default 60)."
            ),
        ),
        handler=lambda args: conda_run(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="git_diff",
            kind="native",
            permission=Permission.READ_ONLY,
            description="Show git diff. Params: staged (bool, default false).",
        ),
        handler=lambda args: git_diff(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="git_status",
            kind="native",
            permission=Permission.READ_ONLY,
            description="Show git status (modified, added, untracked files).",
        ),
        handler=lambda args: git_status(workspace, args),
    )
    registry.register(
        ToolSpec(
            name="run_tests",
            kind="native",
            permission=Permission.SHELL_SAFE,
            description=(
                "Run tests. suite: python|pytest|rust|cargo-check (default python)."
            ),
        ),
        handler=lambda args: run_tests(workspace, args),
    )


# ── Tool implementations ──────────────────────────────────────────────


def read_file(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    path = workspace.resolve_inside(_require_str(args, "path"))
    if not path.exists():
        return {"error": f"File not found: {path.relative_to(workspace.root)}"}
    if not path.is_file():
        return {"error": f"Not a file: {path.relative_to(workspace.root)}"}
    offset = int(args.get("offset", 1))
    limit = int(args.get("limit", 200))
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    total = len(lines)
    selected = lines[max(offset - 1, 0) : max(offset - 1, 0) + limit]
    numbered = "\n".join(f"{index + offset}|{line}" for index, line in enumerate(selected))
    return {
        "path": str(path.relative_to(workspace.root)),
        "total_lines": total,
        "content": numbered,
    }


def pwd(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    return {"cwd": str(workspace.root), "path": "."}


def ls(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    relative = str(args.get("path", "."))
    show_all = bool(args.get("all", False))
    long = bool(args.get("long", False))
    max_results = int(args.get("max_results", 200))
    target = workspace.resolve_inside(relative)
    if not target.exists():
        return {"error": f"Path not found: {relative}"}
    if target.is_file():
        return {"path": relative, "entries": [_ls_entry(target, workspace.root, long=long)], "count": 1}
    entries: list[str] = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if not show_all and child.name.startswith("."):
            continue
        entries.append(_ls_entry(child, workspace.root, long=long))
        if len(entries) >= max_results:
            entries.append("... (truncated)")
            break
    return {"path": relative, "entries": entries, "count": len(entries)}


def cat(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    path = workspace.resolve_inside(_require_str(args, "path"))
    if not path.exists():
        return {"error": f"File not found: {path.relative_to(workspace.root)}"}
    if not path.is_file():
        return {"error": f"Not a file: {path.relative_to(workspace.root)}"}
    offset = int(args.get("offset", 1))
    limit = int(args.get("limit", 400))
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    selected = lines[max(offset - 1, 0) : max(offset - 1, 0) + limit]
    return {
        "path": str(path.relative_to(workspace.root)),
        "offset": offset,
        "limit": limit,
        "total_lines": len(lines),
        "content": "\n".join(selected),
    }


def grep(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    query = _require_str(args, "query")
    relative = str(args.get("path", "."))
    ignore_case = bool(args.get("ignore_case", False))
    max_count = int(args.get("max_count", 100))
    target = workspace.resolve_inside(relative)
    if not target.exists():
        return {"error": f"Path not found: {relative}"}
    if shutil.which("rg") is not None:
        argv = ["rg", "--line-number", "--max-count", str(max_count)]
        if ignore_case:
            argv.append("--ignore-case")
        argv.extend([query, str(target)])
        return _run(argv, cwd=workspace.root)
    return _python_search(workspace, query=query, max_count=max_count, root=target, ignore_case=ignore_case)


def head(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    limit = int(args.get("lines", 20))
    return cat(workspace, {"path": _require_str(args, "path"), "offset": 1, "limit": limit})


def tail(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    path = workspace.resolve_inside(_require_str(args, "path"))
    if not path.exists():
        return {"error": f"File not found: {path.relative_to(workspace.root)}"}
    if not path.is_file():
        return {"error": f"Not a file: {path.relative_to(workspace.root)}"}
    limit = int(args.get("lines", 20))
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    selected = lines[-limit:] if limit > 0 else []
    start = max(len(lines) - len(selected) + 1, 1)
    return {
        "path": str(path.relative_to(workspace.root)),
        "offset": start,
        "limit": limit,
        "total_lines": len(lines),
        "content": "\n".join(selected),
    }


def wc(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    path = workspace.resolve_inside(_require_str(args, "path"))
    if not path.exists():
        return {"error": f"File not found: {path.relative_to(workspace.root)}"}
    if not path.is_file():
        return {"error": f"Not a file: {path.relative_to(workspace.root)}"}
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    return {
        "path": str(path.relative_to(workspace.root)),
        "lines": len(text.splitlines()),
        "words": len(text.split()),
        "bytes": len(raw),
    }


def web_fetch(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    url = _require_url(args, "url")
    max_chars = int(args.get("max_chars", 12000))
    timeout = int(args.get("timeout", 30))
    retry_attempts = _retry_attempts(args)
    retry_delay_seconds = _retry_delay_seconds(args)
    no_cache = bool(args.get("no_cache", False))
    provider = str(args.get("provider", "auto")).lower()
    if provider not in {"auto", "jina", "direct"}:
        raise ValueError("web_fetch provider must be auto, jina, or direct")

    cache_key = f"fetch:{provider}:{url}"
    ttl_seconds = int(args.get("ttl_seconds", 86_400))
    if not no_cache:
        cached = _web_cache_get(workspace, cache_key, ttl_seconds=ttl_seconds)
        if cached is not None:
            cached["cached"] = True
            cached["content"] = str(cached.get("content", ""))[:max_chars]
            return cached

    errors: list[str] = []
    status = 0
    text = ""
    used_provider = provider
    if provider in {"auto", "jina"}:
        try:
            status, text = _call_with_retries(
                lambda: _jina_reader_fetch(url, timeout=timeout),
                attempts=retry_attempts,
                delay_seconds=retry_delay_seconds,
            )
            used_provider = "jina"
        except Exception as exc:
            errors.append(f"jina: {exc}")
            if provider == "jina":
                return {"error": f"web_fetch failed: {exc}", "url": url, "provider": provider}
    if provider in {"auto", "direct"} and not text:
        try:
            status, text = _call_with_retries(
                lambda: _http_get_text(url, timeout=timeout),
                attempts=retry_attempts,
                delay_seconds=retry_delay_seconds,
            )
            used_provider = "direct"
        except Exception as exc:
            errors.append(f"direct: {exc}")
            return {"error": "web_fetch failed", "url": url, "provider": provider, "errors": errors}
    cleaned = _clean_html_content(text)
    content = cleaned["text"] if cleaned["is_html"] else text
    result = {
        "url": url,
        "provider": used_provider,
        "status": status,
        "content": content[:max_chars],
        "content_chars": len(text),
        "cleaned": cleaned["is_html"],
        "links": cleaned["links"],
        "cached": False,
        "errors": errors,
    }
    _web_cache_put(workspace, cache_key, result)
    return result


def curl(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    url = _require_url(args, "url")
    method = str(args.get("method", "GET")).upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
        raise ValueError("method must be GET, POST, PUT, PATCH, DELETE, or HEAD")

    timeout = int(args.get("timeout", 30))
    max_chars = int(args.get("max_chars", 12000))
    headers = _string_dict(args.get("headers", {}), "headers")
    data = _curl_body(args)
    if args.get("json") is not None and not any(k.lower() == "content-type" for k in headers):
        headers["Content-Type"] = "application/json"
    output_path = args.get("output_path")
    output_file = _curl_output_path(workspace, str(output_path)) if output_path else None

    status, response_headers, body = _http_request_bytes(
        url,
        method=method,
        headers=headers,
        data=data,
        timeout=timeout,
    )
    content_type = response_headers.get("Content-Type", "")
    if output_file is not None:
        body = _decode_http_bytes(body, headers=response_headers)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_bytes(body)
        return {
            "url": url,
            "method": method,
            "status": status,
            "content_type": content_type,
            "bytes": len(body),
            "output_path": str(output_file.relative_to(workspace.root)),
            "headers": _public_response_headers(response_headers),
        }

    text = _decode_http_body(body, headers=response_headers)
    cleaned = _clean_html_content(text)
    content = cleaned["text"] if cleaned["is_html"] else text
    return {
        "url": url,
        "method": method,
        "status": status,
        "content_type": content_type,
        "bytes": len(body),
        "content": content[:max_chars],
        "content_chars": len(content),
        "cleaned": cleaned["is_html"],
        "links": cleaned["links"],
        "headers": _public_response_headers(response_headers),
    }


def web_search(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    query = _require_str(args, "query")
    limit = max(1, min(int(args.get("limit", 5)), 20))
    provider = str(args.get("provider", "auto")).lower()
    retry_attempts = _retry_attempts(args)
    retry_delay_seconds = _retry_delay_seconds(args)
    no_cache = bool(args.get("no_cache", False))
    ttl_seconds = int(args.get("ttl_seconds", 3600))
    cache_key = "search:" + json.dumps(
        {
            "provider": provider,
            "query": query,
            "limit": limit,
            "language": args.get("language"),
            "time_range": args.get("time_range"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    if not no_cache:
        cached = _web_cache_get(workspace, cache_key, ttl_seconds=ttl_seconds)
        if cached is not None:
            cached["cached"] = True
            return cached

    errors: list[str] = []
    if provider == "auto":
        providers = []
        if _yandex_search_configured():
            providers.append("yandex")
        providers.extend(["searxng", "jina"])
        if _brave_api_key():
            providers.append("brave")
        if _rambler_proxy_configured():
            providers.append("rambler_proxy")
        if _gigachat_configured():
            providers.append("gigachat")
    else:
        providers = [provider]

    for candidate in providers:
        try:
            if candidate == "searxng":
                result = _call_with_retries(
                    lambda: _searxng_search(args, query=query, limit=limit),
                    attempts=retry_attempts,
                    delay_seconds=retry_delay_seconds,
                )
            elif candidate == "jina":
                result = _call_with_retries(
                    lambda: _jina_search(query=query, limit=limit, timeout=int(args.get("timeout", 30))),
                    attempts=retry_attempts,
                    delay_seconds=retry_delay_seconds,
                )
            elif candidate == "brave":
                result = _call_with_retries(
                    lambda: _brave_search(args, query=query, limit=limit),
                    attempts=retry_attempts,
                    delay_seconds=retry_delay_seconds,
                )
            elif candidate == "gigachat":
                result = _call_with_retries(
                    lambda: _gigachat_search(args, query=query, limit=limit),
                    attempts=retry_attempts,
                    delay_seconds=retry_delay_seconds,
                )
            elif candidate in {"rambler", "rambler_proxy"}:
                result = _call_with_retries(
                    lambda: _rambler_proxy_search(args, query=query, limit=limit),
                    attempts=retry_attempts,
                    delay_seconds=retry_delay_seconds,
                )
            elif candidate in {"yandex", "yandex_search"}:
                result = _call_with_retries(
                    lambda: _yandex_search(args, query=query, limit=limit),
                    attempts=retry_attempts,
                    delay_seconds=retry_delay_seconds,
                )
            else:
                raise ValueError(f"Unknown web_search provider: {candidate}")
            result["cached"] = False
            _web_cache_put(workspace, cache_key, result)
            return result
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
            if provider != "auto":
                break
    return {
        "error": "web_search failed",
        "query": query,
        "provider": provider,
        "errors": errors,
        "results": [],
    }


def docs_search(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    query = _require_str(args, "query")
    domain = str(args.get("domain", "")).strip()
    limit = max(1, min(int(args.get("limit", 5)), 10))
    docs_query = f"site:{domain} {query}" if domain else f"{query} documentation docs"
    search_result = web_search(
        workspace,
        {
            "query": docs_query,
            "limit": limit,
            "provider": args.get("provider", "auto"),
            "language": args.get("language"),
            "time_range": args.get("time_range"),
            "timeout": args.get("timeout", 30),
            "ttl_seconds": args.get("ttl_seconds", 3600),
            "retry_attempts": args.get("retry_attempts", 5),
            "retry_delay_seconds": args.get("retry_delay_seconds", 0.25),
        },
    )
    fetched: list[dict[str, Any]] = []
    if bool(args.get("fetch_results", False)):
        max_fetch = max(1, min(int(args.get("max_fetch", 2)), 5))
        for item in search_result.get("results", [])[:max_fetch]:
            url = item.get("url")
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                fetched.append(
                    web_fetch(
                        workspace,
                        {
                            "url": url,
                            "max_chars": args.get("max_chars", 6000),
                            "timeout": args.get("timeout", 30),
                            "retry_attempts": args.get("retry_attempts", 5),
                            "retry_delay_seconds": args.get("retry_delay_seconds", 0.25),
                        },
                    )
                )
    return {
        "query": query,
        "docs_query": docs_query,
        "domain": domain,
        "search": search_result,
        "fetched": fetched,
    }


def rm(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    relative = _require_str(args, "path")
    recursive = bool(args.get("recursive", False))
    force = bool(args.get("force", False))
    try:
        path = workspace.resolve_inside(relative)
    except WorkspaceError as exc:
        return {"error": str(exc)}
    if path == workspace.root:
        return {"error": "Refusing to remove the workspace root"}
    try:
        rel_path = path.relative_to(workspace.root)
    except ValueError:
        return {"error": f"Path escapes workspace: {relative}"}
    if rel_path.parts and rel_path.parts[0] == ".codeagents":
        return {"error": "Refusing to remove CodeAgents internal state"}
    if not path.exists():
        if force:
            return {"status": "missing", "path": str(rel_path)}
        return {"error": f"Path not found: {relative}"}
    if path.is_dir():
        if not recursive:
            return {"error": f"Is a directory: {relative}. Pass recursive=true to remove directories."}
        shutil.rmtree(path)
        return {"status": "removed", "path": str(rel_path), "kind": "directory", "recursive": True}
    path.unlink()
    return {"status": "removed", "path": str(rel_path), "kind": "file", "recursive": False}


def write_file(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    relative = _require_str(args, "path")
    content = _require_str(args, "content")
    path = workspace.resolve_inside(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    path.write_text(content, encoding="utf-8")
    return {
        "status": "overwritten" if existed else "created",
        "path": relative,
        "bytes": len(content.encode("utf-8")),
    }


def mkdir(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    relative = _require_str(args, "path")
    parents = bool(args.get("parents", True))
    exist_ok = bool(args.get("exist_ok", True))
    try:
        path = workspace.resolve_inside(relative)
    except WorkspaceError as exc:
        return {"error": str(exc)}
    try:
        rel_path = path.relative_to(workspace.root)
    except ValueError:
        return {"error": f"Path escapes workspace: {relative}"}
    if path == workspace.root:
        return {"status": "exists", "path": ".", "kind": "directory"}
    if rel_path.parts and rel_path.parts[0] == ".codeagents":
        return {"error": "Refusing to create directories inside CodeAgents internal state"}
    if path.exists() and not path.is_dir():
        return {"error": f"Path exists and is not a directory: {relative}"}
    existed = path.exists()
    try:
        path.mkdir(parents=parents, exist_ok=exist_ok)
    except FileNotFoundError:
        return {"error": f"Parent directory does not exist: {relative}. Pass parents=true."}
    except FileExistsError:
        return {"error": f"Directory already exists: {relative}. Pass exist_ok=true."}
    return {
        "status": "exists" if existed else "created",
        "path": str(rel_path),
        "kind": "directory",
        "parents": parents,
        "exist_ok": exist_ok,
    }


def mv(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    source_arg = _require_str(args, "source")
    destination_arg = _require_str(args, "destination")
    overwrite = bool(args.get("overwrite", False))
    try:
        source = workspace.resolve_inside(source_arg)
        destination = workspace.resolve_inside(destination_arg)
    except WorkspaceError as exc:
        return {"error": str(exc)}
    source_rel = _workspace_relative_or_error(workspace, source, source_arg)
    if isinstance(source_rel, dict):
        return source_rel
    destination_rel = _workspace_relative_or_error(workspace, destination, destination_arg)
    if isinstance(destination_rel, dict):
        return destination_rel
    if source == workspace.root:
        return {"error": "Refusing to move the workspace root"}
    if _is_internal_codeagents_path(source_rel) or _is_internal_codeagents_path(destination_rel):
        return {"error": "Refusing to move CodeAgents internal state"}
    if not source.exists():
        return {"error": f"Source not found: {source_arg}"}
    if destination.exists() and not overwrite:
        return {"error": f"Destination already exists: {destination_arg}. Pass overwrite=true to replace it."}
    if destination.exists() and overwrite:
        if destination.is_dir():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    kind = "directory" if source.is_dir() else "file"
    shutil.move(str(source), str(destination))
    return {
        "status": "moved",
        "source": str(source_rel),
        "destination": str(destination_rel),
        "kind": kind,
        "overwrite": overwrite,
    }


def create_file(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    relative = _require_str(args, "path")
    content = _require_str(args, "content")
    path = workspace.resolve_inside(relative)
    if path.exists():
        return {"error": f"File already exists: {relative}. Use write_file to overwrite."}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return {"status": "created", "path": relative, "bytes": len(content.encode("utf-8"))}


def edit_file(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    """Edit a file with either a list of line-based edits or a legacy
    old_text/new_text substitution. Writes atomically and returns a unified
    diff so callers can preview the change.
    """
    relative = _require_str(args, "path")
    path = workspace.resolve_inside(relative)
    if not path.exists():
        return {"error": f"File not found: {relative}"}

    original_text = path.read_text(encoding="utf-8")
    original_lines = original_text.splitlines(keepends=True)

    edits = args.get("edits")
    if isinstance(edits, list) and edits:
        new_lines, applied, error = _apply_line_edits(original_lines, edits)
        if error is not None:
            return {"error": error, "path": relative}
    elif "old_text" in args:
        # Legacy fallback: single exact-string substitution.
        old_text = _require_str(args, "old_text")
        new_text = args.get("new_text", "")
        if not isinstance(new_text, str):
            raise ValueError("new_text must be a string")
        count = original_text.count(old_text)
        if count == 0:
            return {"error": "old_text not found in file", "path": relative}
        if count > 1:
            return {
                "error": f"old_text matches {count} locations — provide more context",
                "path": relative,
            }
        updated = original_text.replace(old_text, new_text, 1)
        new_lines = updated.splitlines(keepends=True)
        applied = 1
    else:
        return {
            "error": (
                "Provide either `edits` (list of {line, old_lines, new_lines}) "
                "or legacy `old_text`/`new_text`"
            ),
            "path": relative,
        }

    new_text_full = "".join(new_lines)
    if new_text_full == original_text:
        return {
            "status": "noop",
            "path": relative,
            "edits_applied": applied,
            "diff": "",
        }

    diff = "".join(
        difflib.unified_diff(
            original_lines,
            new_lines,
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
            n=3,
        )
    )

    # Surface the proposed change to a Cursor extension (best-effort, never
    # blocks the actual edit). The extension watches `.codeagents/pending_edits/`.
    pending_meta = _publish_pending_edit(
        workspace=workspace,
        relative=relative,
        original_text=original_text,
        new_text=new_text_full,
        diff=diff,
        edits_applied=applied,
    )

    # Atomic write: same-directory temp file + os.replace().
    tmp = path.with_suffix(path.suffix + f".codeagents-{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(new_text_full, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise

    result: dict[str, Any] = {
        "status": "edited",
        "path": relative,
        "edits_applied": applied,
        "diff": diff,
    }
    if pending_meta is not None:
        result["pending_edit_id"] = pending_meta
    return result


def _apply_line_edits(
    original_lines: list[str], edits: list[Any]
) -> tuple[list[str], int, str | None]:
    """Validate and apply line-based edits bottom-up.

    Returns (new_lines, num_edits_applied, error_message_or_None).
    """
    normalized: list[dict[str, Any]] = []
    for idx, raw in enumerate(edits):
        if not isinstance(raw, dict):
            return [], 0, f"edits[{idx}] must be an object"
        if "line" not in raw:
            return [], 0, f"edits[{idx}] missing required 'line'"
        try:
            line = int(raw["line"])
        except (TypeError, ValueError):
            return [], 0, f"edits[{idx}].line must be an integer"
        if line < 1 or line > len(original_lines) + 1:
            return [], 0, (
                f"edits[{idx}].line {line} out of range (file has "
                f"{len(original_lines)} lines)"
            )
        old_lines = raw.get("old_lines", [])
        new_lines = raw.get("new_lines", [])
        if not isinstance(old_lines, list) or not all(isinstance(s, str) for s in old_lines):
            return [], 0, f"edits[{idx}].old_lines must be a list of strings"
        if not isinstance(new_lines, list) or not all(isinstance(s, str) for s in new_lines):
            return [], 0, f"edits[{idx}].new_lines must be a list of strings"
        normalized.append({"line": line, "old_lines": old_lines, "new_lines": new_lines})

    # Detect overlapping ranges before mutating; overlap would corrupt edits.
    ranges = sorted(
        ((e["line"], e["line"] + len(e["old_lines"])) for e in normalized),
        key=lambda r: r[0],
    )
    for (a_start, a_end), (b_start, _b_end) in zip(ranges, ranges[1:]):
        if b_start < a_end:
            return [], 0, (
                f"overlapping edits: lines [{a_start}, {a_end}) and [{b_start}, …)"
            )

    working = list(original_lines)
    # Apply bottom-up so prior line indices remain valid.
    for edit in sorted(normalized, key=lambda e: e["line"], reverse=True):
        line = edit["line"]
        old = edit["old_lines"]
        start = line - 1
        end = start + len(old)
        if end > len(working):
            return [], 0, (
                f"edit at line {line}: file has only {len(working)} lines, "
                f"cannot match {len(old)} lines"
            )
        actual = [working[i].rstrip("\r\n") for i in range(start, end)]
        if actual != old:
            return [], 0, (
                f"edit at line {line}: old_lines do not match. Expected:\n"
                + "\n".join(f"  {s!r}" for s in old)
                + "\nActual:\n"
                + "\n".join(f"  {s!r}" for s in actual)
            )
        replacement = [s + "\n" for s in edit["new_lines"]]
        working[start:end] = replacement

    return working, len(normalized), None


def _publish_pending_edit(
    *,
    workspace: Workspace,
    relative: str,
    original_text: str,
    new_text: str,
    diff: str,
    edits_applied: int,
) -> str | None:
    """Write proposed edit snapshots so a Cursor extension can show the diff.

    Best-effort: any failure here is swallowed — we never block the actual
    file write. Returns the edit id on success, otherwise None.
    """
    try:
        pending_dir = workspace.root / ".codeagents" / "pending_edits"
        pending_dir.mkdir(parents=True, exist_ok=True)
        edit_id = uuid.uuid4().hex[:16]
        proposed_path = pending_dir / f"{edit_id}.proposed"
        original_snapshot = pending_dir / f"{edit_id}.original"
        meta_path = pending_dir / f"{edit_id}.json"
        proposed_path.write_text(new_text, encoding="utf-8")
        original_snapshot.write_text(original_text, encoding="utf-8")
        meta = {
            "id": edit_id,
            "path": relative,
            "absolute_path": str((workspace.root / relative).resolve()),
            "original": str(original_snapshot),
            "proposed": str(proposed_path),
            "diff": diff,
            "edits_applied": edits_applied,
            "created_at": time.time(),
            "tool": "edit_file",
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return edit_id
    except Exception:
        return None


def list_directory(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    relative = str(args.get("path", "."))
    recursive = bool(args.get("recursive", False))
    max_depth = int(args.get("max_depth", 2))
    target = workspace.resolve_inside(relative)
    if not target.exists():
        return {"error": f"Path not found: {relative}"}
    if not target.is_dir():
        return {"error": f"Not a directory: {relative}"}

    entries: list[str] = []
    _walk_dir(target, workspace.root, entries, depth=0, max_depth=max_depth if recursive else 1, limit=500)
    return {"path": relative, "entries": entries, "count": len(entries)}


def _walk_dir(
    target: Path, root: Path, out: list[str],
    *, depth: int, max_depth: int, limit: int,
) -> None:
    if depth >= max_depth or len(out) >= limit:
        return
    skip = {".git", ".codeagents", "__pycache__", "node_modules", ".venv", "target"}
    try:
        children = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return
    for child in children:
        if child.name in skip:
            continue
        if len(out) >= limit:
            out.append("... (truncated)")
            return
        rel = child.relative_to(root)
        if child.is_dir():
            out.append(f"  {'  ' * depth}📁 {rel}/")
            _walk_dir(child, root, out, depth=depth + 1, max_depth=max_depth, limit=limit)
        else:
            size = child.stat().st_size
            out.append(f"  {'  ' * depth}📄 {rel}  ({_human(size)})")


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n //= 1024
    return f"{n:.1f}TB"


def _ls_entry(path: Path, root: Path, *, long: bool) -> str:
    rel = path.relative_to(root)
    marker = "/" if path.is_dir() else ""
    if not long:
        return f"{rel}{marker}"
    stat = path.stat()
    kind = "dir" if path.is_dir() else "file"
    return f"{kind}\t{_human(stat.st_size)}\t{rel}{marker}"


def glob_files(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    pattern = _require_str(args, "pattern")
    max_results = int(args.get("max_results", 100))
    skip = {".git", ".codeagents", "__pycache__", "node_modules", ".venv", "target"}
    matches: list[str] = []
    for p in sorted(workspace.root.glob(pattern)):
        if any(part in skip for part in p.parts):
            continue
        if p.is_file():
            matches.append(str(p.relative_to(workspace.root)))
        if len(matches) >= max_results:
            break
    return {"pattern": pattern, "matches": matches, "count": len(matches)}


def search(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    query = _require_str(args, "query")
    max_count = int(args.get("max_count", 50))
    if shutil.which("rg") is None:
        return _python_search(workspace, query=query, max_count=max_count)
    return _run(
        ["rg", "--line-number", "--max-count", str(max_count), query, str(workspace.root)],
        cwd=workspace.root,
    )


def propose_patch(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    relative_path = _require_str(args, "path")
    old_text = args.get("old_text", "")
    new_text = _require_str(args, "new_text")
    path = workspace.resolve_inside(relative_path)
    before = path.read_text(encoding="utf-8") if path.exists() else old_text
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"a/{relative_path}",
        tofile=f"b/{relative_path}",
    )
    return {"path": relative_path, "diff": "".join(diff)}


def safe_shell(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    command = args.get("command")
    argv = args.get("argv")
    if command and isinstance(command, str):
        argv = shlex.split(command)
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        raise ValueError("Provide 'command' (string) or 'argv' (list of strings)")
    if Path(argv[0]).name not in SAFE_COMMANDS:
        raise ValueError(f"Command not allowlisted: {argv[0]}. Allowed: {', '.join(sorted(SAFE_COMMANDS))}")
    return _run(argv, cwd=workspace.root)


def run_python(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    relative = _require_str(args, "path")
    target = workspace.resolve_inside(relative)
    if not target.exists():
        return {"error": f"File not found: {relative}"}
    if not target.is_file():
        return {"error": f"Not a file: {relative}"}
    extra_args = _string_list(args.get("args", []), key="args")
    timeout = int(args.get("timeout", 60))
    if bool(args.get("module", False)):
        module = str(Path(relative).with_suffix("")).replace("/", ".")
        argv = _python_argv(workspace, ["-m", module, *extra_args])
    else:
        argv = _python_argv(workspace, [relative, *extra_args])
    return _run(argv, cwd=workspace.root, timeout=timeout)


def python_module(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    module = _require_str(args, "module")
    extra_args = _string_list(args.get("args", []), key="args")
    timeout = int(args.get("timeout", 60))
    argv = _python_argv(workspace, ["-m", module, *extra_args])
    return _run(argv, cwd=workspace.root, timeout=timeout)


def flake8(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    relative = str(args.get("path", "."))
    target = workspace.resolve_inside(relative)
    if not target.exists():
        return {"error": f"Path not found: {relative}"}
    extra_args = _string_list(args.get("args", []), key="args")
    timeout = int(args.get("timeout", 60))
    argv = _python_argv(workspace, ["-m", "flake8", relative, *extra_args])
    result = _run(argv, cwd=workspace.root, timeout=timeout)
    if result["exit_code"] == 1 and "No module named flake8" in result["stderr"]:
        result["hint"] = "Install flake8 with pip_install or conda_create/conda_run before linting."
    return result


def pip_install(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    packages = _string_list(args.get("packages", []), key="packages")
    requirements = args.get("requirements")
    if requirements:
        req_path = workspace.resolve_inside(str(requirements))
        if not req_path.exists():
            return {"error": f"Requirements file not found: {requirements}"}
    if not packages and not requirements:
        raise ValueError("Provide packages or requirements")
    command = ["-m", "pip", "install"]
    if bool(args.get("upgrade", False)):
        command.append("--upgrade")
    if requirements:
        command.extend(["-r", str(requirements)])
    command.extend(packages)
    return _run(_python_argv(workspace, command), cwd=workspace.root, timeout=int(args.get("timeout", 600)))


def dangerous_shell(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    """Run an arbitrary shell command. Confirmation is gated by PermissionPolicy at the agent level."""
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("Provide 'command' as a non-empty string")
    return _run(["/bin/sh", "-c", command], cwd=workspace.root, timeout=int(args.get("timeout", 60)))


def conda_env_list(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    conda = _conda_executable()
    if not conda:
        return _missing_conda()
    return _run([conda, "env", "list", "--json"], cwd=workspace.root)


def conda_create(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    conda = _conda_executable()
    if not conda:
        return _missing_conda()
    name = _require_str(args, "name")
    packages = _string_list(args.get("packages", []), key="packages")
    python_version = str(args.get("python", "")).strip()
    argv = [conda, "create", "-y", "-n", name]
    if python_version:
        argv.append(f"python={python_version}")
    argv.extend(packages)
    return _run(argv, cwd=workspace.root, timeout=int(args.get("timeout", 1200)))


def conda_activate(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    name = _require_str(args, "name")
    _active_env_path(workspace).parent.mkdir(parents=True, exist_ok=True)
    _active_env_path(workspace).write_text(
        json.dumps({"name": name}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"status": "activated", "name": name, "note": "Future Python tools use `conda run -n <name>`."}


def conda_deactivate(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    path = _active_env_path(workspace)
    if path.exists():
        path.unlink()
    return {"status": "deactivated"}


def conda_run(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    conda = _conda_executable()
    if not conda:
        return _missing_conda()
    name = str(args.get("name") or _active_conda_env(workspace) or "").strip()
    if not name:
        raise ValueError("Provide conda env name or activate one first")
    command = args.get("command")
    argv = args.get("argv")
    if command and isinstance(command, str):
        argv = shlex.split(command)
    argv = _string_list(argv, key="command")
    if not argv:
        raise ValueError("Provide command")
    if Path(argv[0]).name not in SAFE_COMMANDS:
        raise ValueError(f"Command not allowlisted: {argv[0]}. Allowed: {', '.join(sorted(SAFE_COMMANDS))}")
    return _run([conda, "run", "-n", name, *argv], cwd=workspace.root, timeout=int(args.get("timeout", 60)))


def git_diff(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    staged = bool(args.get("staged", False))
    argv = ["git", "diff", "--staged"] if staged else ["git", "diff"]
    return _run(argv, cwd=workspace.root)


def git_status(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    return _run(["git", "status", "--short", "--branch"], cwd=workspace.root)


def run_tests(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    suite = str(args.get("suite", "python"))
    if suite == "python":
        return _run(_python_argv(workspace, ["-m", "compileall", "src"]), cwd=workspace.root)
    if suite == "pytest":
        return _run(_python_argv(workspace, ["-m", "pytest"]), cwd=workspace.root)
    if suite == "flake8":
        return flake8(workspace, {"path": ".", "timeout": args.get("timeout", 60)})
    if suite == "rust":
        return _run(["cargo", "test"], cwd=workspace.root)
    if suite == "cargo-check":
        return _run(["cargo", "check"], cwd=workspace.root)
    raise ValueError(f"Unknown test suite: {suite}")


# ── Helpers ───────────────────────────────────────────────────────────


def _run(argv: list[str], *, cwd: Path, timeout: int = 60) -> dict[str, Any]:
    try:
        completed = subprocess.run(argv, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        return {
            "argv": argv,
            "exit_code": 127,
            "stdout": "",
            "stderr": f"Executable not found: {argv[0]}",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": argv,
            "exit_code": 124,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or f"Command timed out after {timeout}s",
        }
    return {
        "argv": argv,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _require_url(args: dict[str, Any], key: str) -> str:
    url = _require_str(args, key).strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{key} must be an http(s) URL")
    return url


def _http_get_text(url: str, *, headers: dict[str, str] | None = None, timeout: int = 30) -> tuple[int, str]:
    status, response_headers, data = _http_request_bytes(url, headers=headers, timeout=timeout)
    return status, _decode_http_body(data, headers=response_headers)


def _http_request_bytes(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 30,
    verify_ssl_certs: bool = True,
) -> tuple[int, Any, bytes]:
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": "CodeAgents/0.1 (+local-agent)",
            "Accept-Encoding": "gzip, deflate",
            **(headers or {}),
        },
        method=method,
    )
    context = None if verify_ssl_certs else ssl._create_unverified_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        data = response.read()
        return response.status, response.headers, data


def _http_post_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: int = 30,
    verify_ssl_certs: bool = True,
) -> tuple[int, str]:
    status, response_headers, payload = _http_request_bytes(
        url,
        method="POST",
        data=data,
        headers=headers,
        timeout=timeout,
        verify_ssl_certs=verify_ssl_certs,
    )
    return status, _decode_http_body(payload, headers=response_headers)


def _decode_http_body(data: bytes, *, headers: Any) -> str:
    data = _decode_http_bytes(data, headers=headers)
    charset = headers.get_content_charset() if hasattr(headers, "get_content_charset") else None
    return data.decode(charset or "utf-8", errors="replace")


def _decode_http_bytes(data: bytes, *, headers: Any) -> bytes:
    encoding = str(headers.get("Content-Encoding", "")).lower()
    if "gzip" in encoding:
        return gzip.decompress(data)
    elif "deflate" in encoding:
        try:
            return zlib.decompress(data)
        except zlib.error:
            return zlib.decompress(data, -zlib.MAX_WBITS)
    return data


def _curl_body(args: dict[str, Any]) -> bytes | None:
    has_data = args.get("data") is not None
    has_json = args.get("json") is not None
    if has_data and has_json:
        raise ValueError("Pass either data or json, not both")
    if has_json:
        return json.dumps(args["json"], ensure_ascii=False).encode("utf-8")
    if has_data:
        value = args["data"]
        if isinstance(value, str):
            return value.encode("utf-8")
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False).encode("utf-8")
        raise ValueError("data must be a string, object, or array")
    return None


def _string_dict(value: Any, name: str) -> dict[str, str]:
    if value is None or value == "":
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return {str(k): str(v) for k, v in value.items()}


def _curl_output_path(workspace: Workspace, output_path: str) -> Path:
    try:
        path = workspace.resolve_inside(output_path)
    except WorkspaceError as exc:
        raise ValueError(str(exc)) from exc
    try:
        rel_path = path.relative_to(workspace.root)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace: {output_path}") from exc
    if path == workspace.root:
        raise ValueError("output_path must be a file path, not the workspace root")
    if rel_path.parts and rel_path.parts[0] == ".codeagents":
        raise ValueError("Refusing to write curl output into CodeAgents internal state")
    return path


def _workspace_relative_or_error(workspace: Workspace, path: Path, original: str) -> Path | dict[str, str]:
    try:
        return path.relative_to(workspace.root)
    except ValueError:
        return {"error": f"Path escapes workspace: {original}"}


def _is_internal_codeagents_path(rel_path: Path) -> bool:
    return bool(rel_path.parts and rel_path.parts[0] == ".codeagents")


def _public_response_headers(headers: Any) -> dict[str, str]:
    keep = {
        "content-type",
        "content-length",
        "content-encoding",
        "etag",
        "last-modified",
        "location",
    }
    result: dict[str, str] = {}
    for key in headers.keys():
        if str(key).lower() in keep:
            result[str(key)] = str(headers.get(key, ""))
    return result


def _local_config() -> dict[str, Any]:
    global _LOCAL_CONFIG_CACHE
    if _LOCAL_CONFIG_CACHE is not None:
        return _LOCAL_CONFIG_CACHE

    merged: dict[str, Any] = {}
    for path in (
        PROJECT_ROOT / "config" / "local.toml",
        PROJECT_ROOT / ".codeagents" / "secrets.toml",
    ):
        if not path.exists():
            continue
        try:
            with path.open("rb") as handle:
                raw = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        _deep_merge(merged, raw)
    _LOCAL_CONFIG_CACHE = merged
    return merged


def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


def _config_value(section: str, key: str, *env_names: str) -> str | None:
    value = _local_config().get(section, {}).get(key)
    if value is not None and value != "":
        return str(value)
    for env_name in env_names:
        env_value = os.getenv(env_name)
        if env_value:
            return env_value
    return None


def _retry_attempts(args: dict[str, Any]) -> int:
    return max(1, min(int(args.get("retry_attempts", 5)), 15))


def _retry_delay_seconds(args: dict[str, Any]) -> float:
    return max(0.0, min(float(args.get("retry_delay_seconds", 0.25)), 5.0))


def _call_with_retries(
    func: Any,
    *,
    attempts: int,
    delay_seconds: float,
) -> Any:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except Exception as exc:
            if not _is_retryable_web_error(exc) or attempt >= attempts:
                raise
            last_exc = exc
            if delay_seconds > 0:
                time.sleep(delay_seconds)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("retry loop exited without result")


def _is_retryable_web_error(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {408, 409, 425, 429} or exc.code >= 500
    if isinstance(exc, urllib.error.URLError):
        return True
    return isinstance(exc, (TimeoutError, OSError))


def _jina_headers() -> dict[str, str]:
    api_key = _config_value("web", "jina_api_key", "JINA_API_KEY", "CODEAGENTS_JINA_API_KEY")
    headers = {"Accept": "text/plain"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _jina_reader_fetch(url: str, *, timeout: int) -> tuple[int, str]:
    reader_url = f"https://r.jina.ai/{url}"
    return _jina_get_text_with_auth_retry(reader_url, timeout=timeout)


def _jina_get_text_with_auth_retry(url: str, *, timeout: int) -> tuple[int, str]:
    headers = _jina_headers()
    try:
        return _http_get_text(url, headers=headers, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code != 401 or "Authorization" not in headers:
            raise
        # A stale/invalid local Jina key should not break the free no-key path.
        return _http_get_text(url, headers={"Accept": "text/plain"}, timeout=timeout)


def _searxng_search(args: dict[str, Any], *, query: str, limit: int) -> dict[str, Any]:
    base_url = str(
        args.get("searxng_url")
        or _config_value("web", "searxng_url", "CODEAGENTS_SEARXNG_URL")
        or "http://127.0.0.1:8080"
    ).rstrip("/")
    params: dict[str, Any] = {
        "q": query,
        "format": "json",
        "language": args.get("language", "en"),
    }
    if args.get("time_range"):
        params["time_range"] = args["time_range"]
    if args.get("categories"):
        categories = args["categories"]
        params["categories"] = ",".join(categories) if isinstance(categories, list) else str(categories)
    url = f"{base_url}/search?{urllib.parse.urlencode(params)}"
    status, text = _http_get_text(url, timeout=int(args.get("timeout", 10)))
    raw = json.loads(text)
    results = []
    for item in raw.get("results", [])[:limit]:
        if not isinstance(item, dict):
            continue
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("content", ""),
            "engine": item.get("engine", ""),
        })
    return {
        "query": query,
        "provider": "searxng",
        "status": status,
        "searxng_url": base_url,
        "results": results,
    }


def _jina_search(*, query: str, limit: int, timeout: int) -> dict[str, Any]:
    encoded = urllib.parse.quote(query)
    url = f"https://s.jina.ai/{encoded}"
    status, text = _jina_get_text_with_auth_retry(url, timeout=timeout)
    return {
        "query": query,
        "provider": "jina",
        "status": status,
        "results": [{
            "title": f"Jina Search: {query}",
            "url": url,
            "snippet": text[:2000],
            "content": text,
        }][:limit],
    }


def _brave_search(args: dict[str, Any], *, query: str, limit: int) -> dict[str, Any]:
    api_key = _brave_api_key()
    if not api_key:
        raise ValueError("BRAVE_API_KEY or CODEAGENTS_BRAVE_API_KEY is not set")
    params = {
        "q": query,
        "count": str(limit),
    }
    if args.get("language"):
        params["search_lang"] = str(args["language"])
    if args.get("country"):
        params["country"] = str(args["country"])
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode(params)
    status, text = _http_get_text(
        url,
        headers={
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        },
        timeout=int(args.get("timeout", 30)),
    )
    raw = json.loads(text)
    results = []
    for item in raw.get("web", {}).get("results", [])[:limit]:
        if not isinstance(item, dict):
            continue
        results.append({
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "snippet": item.get("description", ""),
            "engine": "brave",
        })
    return {"query": query, "provider": "brave", "status": status, "results": results}


def _brave_api_key() -> str | None:
    return _config_value("web", "brave_api_key", "BRAVE_API_KEY", "CODEAGENTS_BRAVE_API_KEY")


def _gigachat_configured() -> bool:
    return bool(
        _config_value("gigachat", "authorization_key", "GIGACHAT_CREDENTIALS")
        or (
            _config_value("gigachat", "client_id", "GIGACHAT_CLIENT_ID")
            and _config_value("gigachat", "client_secret", "GIGACHAT_CLIENT_SECRET")
        )
        or _config_value("gigachat", "access_token", "GIGACHAT_ACCESS_TOKEN")
    )


def _rambler_proxy_configured() -> bool:
    return bool(_rambler_proxy_url({}))


def _rambler_proxy_search(args: dict[str, Any], *, query: str, limit: int) -> dict[str, Any]:
    endpoint = _rambler_proxy_url(args)
    if not endpoint:
        raise ValueError(
            "Rambler proxy URL is not configured. Set rambler_proxy.url in config/local.toml "
            "or RAMBLER_PROXY_URL/CODEAGENTS_RAMBLER_PROXY_URL."
        )

    timeout = int(args.get("timeout", 30))
    method = str(
        args.get("rambler_proxy_method")
        or _config_value("rambler_proxy", "method", "RAMBLER_PROXY_METHOD")
        or "GET"
    ).upper()
    query_param = str(
        args.get("rambler_proxy_query_param")
        or _config_value("rambler_proxy", "query_param", "RAMBLER_PROXY_QUERY_PARAM")
        or "query"
    )
    headers = _rambler_proxy_headers(timeout=timeout)
    if method == "GET":
        params = {query_param: query}
        limit_param = _config_value("rambler_proxy", "limit_param", "RAMBLER_PROXY_LIMIT_PARAM")
        if limit_param:
            params[limit_param] = str(limit)
        separator = "&" if urllib.parse.urlparse(endpoint).query else "?"
        url = endpoint + separator + urllib.parse.urlencode(params)
        status, text = _http_get_text(url, headers=headers, timeout=timeout)
    elif method == "POST":
        body_field = str(
            args.get("rambler_proxy_body_field")
            or _config_value("rambler_proxy", "body_field", "RAMBLER_PROXY_BODY_FIELD")
            or "query"
        )
        payload = {body_field: query, "limit": limit}
        status, text = _http_post_text(
            endpoint,
            headers={**headers, "Content-Type": "application/json"},
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=timeout,
            verify_ssl_certs=_rambler_proxy_verify_ssl_certs(),
        )
    else:
        raise ValueError("rambler_proxy method must be GET or POST")

    return _rambler_proxy_result(query=query, status=status, text=text, limit=limit)


def _rambler_proxy_url(args: dict[str, Any]) -> str | None:
    value = (
        args.get("rambler_proxy_url")
        or _config_value("rambler_proxy", "url", "RAMBLER_PROXY_URL", "CODEAGENTS_RAMBLER_PROXY_URL")
    )
    return str(value).rstrip("/") if value else None


def _rambler_proxy_headers(*, timeout: int) -> dict[str, str]:
    headers = {"Accept": "application/json, text/plain, */*"}
    auth = str(_config_value("rambler_proxy", "auth", "RAMBLER_PROXY_AUTH") or "gigachat_bearer").lower()
    if auth in {"none", "no", "off", "false"}:
        return headers
    if auth == "gigachat_bearer":
        headers["Authorization"] = f"Bearer {_gigachat_access_token(timeout=timeout)}"
        return headers
    if auth == "bearer":
        token = _config_value("rambler_proxy", "bearer_token", "RAMBLER_PROXY_BEARER_TOKEN")
        if not token:
            raise ValueError("rambler_proxy.auth='bearer' requires rambler_proxy.bearer_token")
        headers["Authorization"] = f"Bearer {token}"
        return headers
    raise ValueError("rambler_proxy.auth must be gigachat_bearer, bearer, or none")


def _rambler_proxy_verify_ssl_certs() -> bool:
    value = _config_value("rambler_proxy", "verify_ssl_certs", "RAMBLER_PROXY_VERIFY_SSL_CERTS")
    if value is None:
        return _gigachat_verify_ssl_certs()
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _rambler_proxy_result(*, query: str, status: int, text: str, limit: int) -> dict[str, Any]:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return {
            "query": query,
            "provider": "rambler_proxy",
            "status": status,
            "results": [{
                "title": f"Rambler proxy: {query}",
                "url": "",
                "snippet": text[:2000],
                "content": text,
                "engine": "rambler_proxy",
            }][:limit],
        }
    results = _extract_search_results(raw, limit=limit, engine="rambler_proxy")
    if not results:
        results = [{
            "title": f"Rambler proxy: {query}",
            "url": "",
            "snippet": json.dumps(raw, ensure_ascii=False)[:2000],
            "content": json.dumps(raw, ensure_ascii=False),
            "engine": "rambler_proxy",
        }]
    return {"query": query, "provider": "rambler_proxy", "status": status, "results": results[:limit]}


def _yandex_search_configured() -> bool:
    api_key = _config_value("yandex_search", "api_key", "YANDEX_SEARCH_API_KEY", "YANDEX_API_KEY")
    folder_id = _config_value("yandex_search", "folder_id", "YANDEX_SEARCH_FOLDER_ID", "YANDEX_FOLDER_ID")
    return bool(api_key and folder_id and folder_id != "...")


def _yandex_search(args: dict[str, Any], *, query: str, limit: int) -> dict[str, Any]:
    api_key = _config_value("yandex_search", "api_key", "YANDEX_SEARCH_API_KEY", "YANDEX_API_KEY")
    folder_id = _config_value("yandex_search", "folder_id", "YANDEX_SEARCH_FOLDER_ID", "YANDEX_FOLDER_ID")
    if not api_key:
        raise ValueError("Yandex Search API key is not configured")
    if not folder_id or folder_id == "...":
        raise ValueError("Yandex Search folder_id is not configured")

    endpoint = (
        args.get("yandex_search_url")
        or _config_value("yandex_search", "url", "YANDEX_SEARCH_URL")
        or "https://searchapi.api.cloud.yandex.net/v2/web/search"
    )
    search_type = str(
        args.get("yandex_search_type")
        or _config_value("yandex_search", "search_type", "YANDEX_SEARCH_TYPE")
        or "SEARCH_TYPE_RU"
    )
    response_format = str(
        args.get("yandex_response_format")
        or _config_value("yandex_search", "response_format", "YANDEX_SEARCH_RESPONSE_FORMAT")
        or "FORMAT_HTML"
    )
    payload = {
        "query": {
            "searchType": search_type,
            "queryText": query,
        },
        "folderId": folder_id,
        "responseFormat": response_format,
    }
    status, text = _http_post_text(
        str(endpoint),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Api-Key {api_key}",
        },
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=int(args.get("timeout", 30)),
    )
    raw = json.loads(text)
    html_content = _decode_yandex_raw_data(raw)
    cleaned = _clean_html_content(html_content)
    content = cleaned["text"] if cleaned["is_html"] else html_content
    results = _extract_search_results(raw, limit=limit, engine="yandex")
    if html_content:
        html_results = _extract_html_search_results(html_content, limit=limit)
        if html_results:
            results = html_results
    if not results:
        results = [{
            "title": f"Yandex Search: {query}",
            "url": "",
            "snippet": content[:2000] if content else json.dumps(raw, ensure_ascii=False)[:2000],
            "content": content or json.dumps(raw, ensure_ascii=False),
            "engine": "yandex",
        }]
    return {
        "query": query,
        "provider": "yandex",
        "status": status,
        "results": results[:limit],
        "content": content[:12000],
        "cleaned": cleaned["is_html"],
    }


def _decode_yandex_raw_data(raw: dict[str, Any]) -> str:
    raw_data = raw.get("rawData")
    if not isinstance(raw_data, str) or not raw_data:
        return ""
    try:
        return base64.b64decode(raw_data).decode("utf-8", errors="replace")
    except Exception:
        return raw_data


def _extract_html_search_results(html: str, *, limit: int) -> list[dict[str, Any]]:
    if not _looks_like_html(html):
        return []
    soup = BeautifulSoup(html, "html.parser")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "")
        if not href.startswith(("http://", "https://")):
            continue
        if href in seen or _is_low_value_search_url(href):
            continue
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if not title:
            continue
        seen.add(href)
        results.append({"title": title, "url": href, "snippet": title, "engine": "yandex"})
        if len(results) >= limit:
            break
    return results


def _clean_html_content(content: str) -> dict[str, Any]:
    if not _looks_like_html(content):
        return {"is_html": False, "text": content, "links": []}

    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(
        [
            "script",
            "style",
            "noscript",
            "template",
            "svg",
            "canvas",
            "form",
            "input",
            "button",
            "header",
            "footer",
            "nav",
            "aside",
        ]
    ):
        tag.decompose()

    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        if not href.startswith(("http://", "https://")):
            continue
        if href in seen or _is_low_value_search_url(href):
            continue
        text = _normalize_text(anchor.get_text(" ", strip=True))
        links.append({"text": text, "url": href})
        seen.add(href)
        if len(links) >= 50:
            break

    title = _normalize_text(soup.title.get_text(" ", strip=True)) if soup.title else ""
    text = _normalize_text(soup.get_text("\n", strip=True))
    if title and not text.startswith(title):
        text = f"{title}\n\n{text}" if text else title
    if links:
        link_lines = [
            f"- {item['text'] or item['url']}: {item['url']}"
            for item in links[:25]
        ]
        text = f"{text}\n\nLinks:\n" + "\n".join(link_lines)

    return {"is_html": True, "text": text, "links": links}


def _looks_like_html(content: str) -> bool:
    sample = content[:1000].lower()
    return any(marker in sample for marker in ("<html", "<body", "<!doctype html", "<head", "<script", "<div", "<p", "<a "))


def _normalize_text(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.replace("\r", "\n").split("\n")]
    compact: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if not previous_blank:
                compact.append("")
            previous_blank = True
            continue
        compact.append(line)
        previous_blank = False
    return "\n".join(compact).strip()


def _extract_search_results(raw: Any, *, limit: int, engine: str) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        candidates = raw
    elif isinstance(raw, dict):
        candidates = []
        for key in ("results", "items", "documents", "data", "organic", "web"):
            value = raw.get(key)
            if isinstance(value, list):
                candidates = value
                break
            if isinstance(value, dict):
                nested = _extract_search_results(value, limit=limit, engine=engine)
                if nested:
                    return nested
        if not candidates and all(isinstance(raw.get(key), str) for key in ("title", "url")):
            candidates = [raw]
    else:
        return []

    results: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or item.get("heading") or "")
        url = str(item.get("url") or item.get("link") or item.get("href") or "")
        snippet = str(item.get("snippet") or item.get("content") or item.get("description") or item.get("text") or "")
        if not title and not url and not snippet:
            continue
        results.append({"title": title, "url": url, "snippet": snippet, "engine": engine})
        if len(results) >= limit:
            break
    return results


def _is_low_value_search_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    return (
        host == "passport.yandex.ru"
        or host.endswith(".passport.yandex.ru")
        or url.startswith("https://yandex.ru/alice/")
        or url.startswith("https://ya.ru/alice/")
    )


def _gigachat_search(args: dict[str, Any], *, query: str, limit: int) -> dict[str, Any]:
    timeout = int(args.get("timeout", 30))
    token = _gigachat_access_token(timeout=timeout)
    base_url = (
        _config_value("gigachat", "base_url", "GIGACHAT_BASE_URL")
        or "https://gigachat.devices.sberbank.ru/api/v1"
    ).rstrip("/")
    model = _config_value("gigachat", "model", "GIGACHAT_MODEL") or "GigaChat"
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": int(args.get("max_tokens", 1200)),
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты поисковый помощник для кодового агента. "
                    "Дай краткий ответ на запрос и, если знаешь, перечисли релевантные URL. "
                    "Не выдумывай ссылки, если не уверен."
                ),
            },
            {"role": "user", "content": query},
        ],
    }
    try:
        status, text = _http_post_text(
            f"{base_url}/chat/completions",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=timeout,
            verify_ssl_certs=_gigachat_verify_ssl_certs(),
        )
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GigaChat chat failed: {_http_error_text(exc)}") from exc
    raw = json.loads(text)
    content = ""
    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = str(message.get("content", ""))
    return {
        "query": query,
        "provider": "gigachat",
        "status": status,
        "results": [
            {
                "title": f"GigaChat answer: {query}",
                "url": "",
                "snippet": content[:2000],
                "content": content,
                "engine": "gigachat",
            }
        ][:limit],
    }


def _gigachat_access_token(*, timeout: int) -> str:
    configured_token = _config_value("gigachat", "access_token", "GIGACHAT_ACCESS_TOKEN")
    if configured_token:
        return configured_token

    now = time.time()
    cached_token = _GIGACHAT_TOKEN_CACHE.get("access_token")
    expires_at = float(_GIGACHAT_TOKEN_CACHE.get("expires_at", 0))
    if cached_token and expires_at - 60 > now:
        return str(cached_token)

    authorization_key = _gigachat_authorization_key()
    if not authorization_key:
        raise ValueError(
            "GigaChat credentials are not configured. Set gigachat.authorization_key "
            "or gigachat.client_id/client_secret in config/local.toml or .codeagents/secrets.toml."
        )
    auth_url = (
        _config_value("gigachat", "auth_url", "GIGACHAT_AUTH_URL")
        or "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    )
    scope = _config_value("gigachat", "scope", "GIGACHAT_SCOPE") or "GIGACHAT_API_PERS"
    try:
        status, text = _http_post_text(
            auth_url,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "RqUID": str(uuid.uuid4()),
                "Authorization": f"Basic {authorization_key}",
            },
            data=urllib.parse.urlencode({"scope": scope}).encode("utf-8"),
            timeout=timeout,
            verify_ssl_certs=_gigachat_verify_ssl_certs(),
        )
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GigaChat OAuth failed: {_http_error_text(exc)}") from exc
    raw = json.loads(text)
    token = raw.get("access_token")
    if not token:
        raise ValueError(f"GigaChat OAuth response did not contain access_token (status={status})")
    expires_at_raw = raw.get("expires_at")
    if isinstance(expires_at_raw, (int, float)):
        expires_at = float(expires_at_raw)
        if expires_at > 10_000_000_000:
            expires_at = expires_at / 1000
    else:
        expires_at = now + 30 * 60
    _GIGACHAT_TOKEN_CACHE["access_token"] = token
    _GIGACHAT_TOKEN_CACHE["expires_at"] = expires_at
    return str(token)


def _gigachat_authorization_key() -> str | None:
    authorization_key = (
        _config_value("gigachat", "authorization_key", "GIGACHAT_CREDENTIALS")
        or _config_value("gigachat", "auth_key", "GIGACHAT_AUTH_KEY")
    )
    if authorization_key:
        return authorization_key
    client_id = _config_value("gigachat", "client_id", "GIGACHAT_CLIENT_ID")
    client_secret = _config_value("gigachat", "client_secret", "GIGACHAT_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    return base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")


def _gigachat_verify_ssl_certs() -> bool:
    value = _config_value("gigachat", "verify_ssl_certs", "GIGACHAT_VERIFY_SSL_CERTS")
    if value is None:
        return True
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _http_error_text(exc: urllib.error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""
    return f"HTTP {exc.code} {exc.reason}: {body[:1000]}"


def _web_cache_path(workspace: Workspace) -> Path:
    return workspace.root / ".codeagents" / "web_cache.sqlite3"


def _web_cache_get(workspace: Workspace, key: str, *, ttl_seconds: int) -> dict[str, Any] | None:
    path = _web_cache_path(workspace)
    if not path.exists():
        return None
    with sqlite3.connect(path) as conn:
        _init_web_cache(conn)
        row = conn.execute("select payload, created_at from web_cache where key = ?", (key,)).fetchone()
    if row is None:
        return None
    if ttl_seconds >= 0 and time.time() - float(row[1]) > ttl_seconds:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def _web_cache_put(workspace: Workspace, key: str, payload: dict[str, Any]) -> None:
    path = _web_cache_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        _init_web_cache(conn)
        conn.execute(
            """
            insert into web_cache(key, payload, created_at)
            values (?, ?, ?)
            on conflict(key) do update set
              payload = excluded.payload,
              created_at = excluded.created_at
            """,
            (key, json.dumps(payload, ensure_ascii=False), time.time()),
        )


def _init_web_cache(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        create table if not exists web_cache (
          key text primary key,
          payload text not null,
          created_at real not null
        )
        """
    )


def _python_argv(workspace: Workspace, args: list[str]) -> list[str]:
    active_env = _active_conda_env(workspace)
    if active_env:
        conda = _conda_executable()
        if conda:
            return [conda, "run", "-n", active_env, "python", *args]
    return ["python3", *args]


def _active_env_path(workspace: Workspace) -> Path:
    return workspace.root / ".codeagents" / "active_conda_env.json"


def _active_conda_env(workspace: Workspace) -> str | None:
    path = _active_env_path(workspace)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    name = raw.get("name")
    return name if isinstance(name, str) and name else None


def _conda_executable() -> str | None:
    return shutil.which("conda") or shutil.which("micromamba") or shutil.which("mamba")


def _missing_conda() -> dict[str, Any]:
    return {
        "exit_code": 127,
        "stdout": "",
        "stderr": "Conda executable not found. Install conda/mamba or use the default Python tools.",
    }


def _string_list(value: Any, *, key: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return shlex.split(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise ValueError(f"{key} must be a string or array of strings")


def _python_search(
    workspace: Workspace,
    *,
    query: str,
    max_count: int,
    root: Path | None = None,
    ignore_case: bool = False,
) -> dict[str, Any]:
    matches: list[str] = []
    skip = {".git", ".codeagents", "__pycache__", "node_modules", ".venv", "target"}
    paths = [root] if root and root.is_file() else sorted((root or workspace.root).rglob("*"))
    needle = query.lower() if ignore_case else query
    for path in paths:
        if not path.is_file() or any(part in skip for part in path.parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(lines, start=1):
            haystack = line.lower() if ignore_case else line
            if needle in haystack:
                relative = path.relative_to(workspace.root)
                matches.append(f"{relative}:{line_number}:{line}")
                if len(matches) >= max_count:
                    return {
                        "argv": ["python-search", query],
                        "exit_code": 0,
                        "stdout": "\n".join(matches) + "\n",
                        "stderr": "",
                    }
    return {
        "argv": ["python-search", query],
        "exit_code": 0 if matches else 1,
        "stdout": "\n".join(matches) + ("\n" if matches else ""),
        "stderr": "",
    }


def _require_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing required string argument: {key}")
    return value
