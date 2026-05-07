"""Filesystem-flavoured native tools.

Read/write/edit files, directory listing/walking, glob, grep (with rg
fallback to a pure-python search), and the LSP-diagnostics post-processor
that ``write_file``/``edit_file`` use.
"""

from __future__ import annotations

import difflib
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from codeagents.core.workspace import Workspace, WorkspaceError
from codeagents.tools.shell import _python_search, _run


def _require_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing required string argument: {key}")
    return value


def _workspace_relative_or_error(
    workspace: Workspace, path: Path, original: str
) -> Path | dict[str, str]:
    try:
        return path.relative_to(workspace.root)
    except ValueError:
        return {"error": f"Path escapes workspace: {original}"}


def _is_internal_codeagents_path(rel_path: Path) -> bool:
    return bool(rel_path.parts and rel_path.parts[0] == ".codeagents")


# ── Read / list ───────────────────────────────────────────────────────


def read_file(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    path = workspace.resolve_for_read(_require_str(args, "path"))
    if not path.exists():
        return {"error": f"File not found: {workspace.display_path(path)}"}
    if not path.is_file():
        return {"error": f"Not a file: {workspace.display_path(path)}"}
    offset = int(args.get("offset", 1))
    limit = int(args.get("limit", 200))
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    total = len(lines)
    selected = lines[max(offset - 1, 0) : max(offset - 1, 0) + limit]
    numbered = "\n".join(f"{index + offset}|{line}" for index, line in enumerate(selected))
    return {
        "path": workspace.display_path(path),
        "total_lines": total,
        "content": numbered,
    }


def pwd(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "cwd": str(workspace.cwd),
        "workspace_root": str(workspace.root),
        "read_only": workspace.read_only,
    }


def ls(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    relative = str(args.get("path", "."))
    show_all = bool(args.get("all", False))
    long = bool(args.get("long", False))
    max_results = int(args.get("max_results", 200))
    target = workspace.resolve_for_read(relative)
    if not target.exists():
        return {"error": f"Path not found: {relative}"}
    base = target if target.is_dir() else target.parent
    if target.is_file():
        return {"path": relative, "entries": [_ls_entry(target, base, long=long)], "count": 1}
    entries: list[str] = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if not show_all and child.name.startswith("."):
            continue
        entries.append(_ls_entry(child, base, long=long))
        if len(entries) >= max_results:
            entries.append("... (truncated)")
            break
    return {"path": relative, "entries": entries, "count": len(entries)}


def cat(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    path = workspace.resolve_for_read(_require_str(args, "path"))
    if not path.exists():
        return {"error": f"File not found: {workspace.display_path(path)}"}
    if not path.is_file():
        return {"error": f"Not a file: {workspace.display_path(path)}"}
    offset = int(args.get("offset", 1))
    limit = int(args.get("limit", 400))
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    selected = lines[max(offset - 1, 0) : max(offset - 1, 0) + limit]
    return {
        "path": workspace.display_path(path),
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
    target = workspace.resolve_for_read(relative)
    if not target.exists():
        return {"error": f"Path not found: {relative}"}
    if shutil.which("rg") is not None:
        argv = ["rg", "--line-number", "--max-count", str(max_count)]
        if ignore_case:
            argv.append("--ignore-case")
        argv.extend([query, str(target)])
        return _run(argv, cwd=workspace.cwd)
    return _python_search(workspace, query=query, max_count=max_count, root=target, ignore_case=ignore_case)


def head(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    limit = int(args.get("lines", 20))
    return cat(workspace, {"path": _require_str(args, "path"), "offset": 1, "limit": limit})


def tail(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    path = workspace.resolve_for_read(_require_str(args, "path"))
    if not path.exists():
        return {"error": f"File not found: {workspace.display_path(path)}"}
    if not path.is_file():
        return {"error": f"Not a file: {workspace.display_path(path)}"}
    limit = int(args.get("lines", 20))
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    selected = lines[-limit:] if limit > 0 else []
    start = max(len(lines) - len(selected) + 1, 1)
    return {
        "path": workspace.display_path(path),
        "offset": start,
        "limit": limit,
        "total_lines": len(lines),
        "content": "\n".join(selected),
    }


def wc(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    path = workspace.resolve_for_read(_require_str(args, "path"))
    if not path.exists():
        return {"error": f"File not found: {workspace.display_path(path)}"}
    if not path.is_file():
        return {"error": f"Not a file: {workspace.display_path(path)}"}
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    return {
        "path": workspace.display_path(path),
        "lines": len(text.splitlines()),
        "words": len(text.split()),
        "bytes": len(raw),
    }


# ── Write / edit / move ───────────────────────────────────────────────


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

    pending_meta = _publish_pending_edit(
        workspace=workspace,
        relative=relative,
        original_text=original_text,
        new_text=new_text_full,
        diff=diff,
        edits_applied=applied,
    )

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


# ── Directory walking / search ────────────────────────────────────────


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


# ── LSP diagnostics post-processor ────────────────────────────────────


def with_diagnostics(
    workspace: Workspace,
    lsp: Any | None,
    args: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    """Attach LSP ``diagnostics`` to a write-tool result, when possible.

    Degradation-friendly: any failure (no manager, no server for this
    extension, timeout, server crash) silently leaves the result alone.
    """
    if lsp is None or not isinstance(result, dict):
        return result
    if "error" in result:
        return result
    raw_path = args.get("path") if isinstance(args, dict) else None
    if not isinstance(raw_path, str) or not raw_path.strip():
        return result
    try:
        path = workspace.resolve_inside(raw_path.strip())
    except Exception:
        return result
    try:
        diags = lsp.diagnostics(path)
    except Exception:
        return result
    if diags:
        result["diagnostics"] = diags
    return result
