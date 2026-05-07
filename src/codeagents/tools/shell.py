"""Shell-related native tools and the subprocess helpers they share.

Split out of ``native_code.py`` in the post-honest-refactor cleanup so the
file actually reads like a subsystem instead of a 3k-line monolith.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from codeagents.core.workspace import Workspace


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


_RM_PATTERN = re.compile(r"(?:^|[\s;&|`(])(?:sudo\s+)?rm(?:\s|$)")


# ── Subprocess core ───────────────────────────────────────────────────


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


def _command_cwd(workspace: Workspace, raw_cwd: Any) -> Path:
    if raw_cwd is None or raw_cwd == "":
        return workspace.cwd
    if not isinstance(raw_cwd, str):
        raise ValueError("cwd must be a workspace-relative string")
    cwd = workspace.resolve_inside(raw_cwd)
    if not cwd.exists():
        raise ValueError(f"cwd not found: {raw_cwd}")
    if not cwd.is_dir():
        raise ValueError(f"cwd is not a directory: {raw_cwd}")
    return cwd


def _uses_shell_syntax(command: str) -> bool:
    return any(token in command for token in ("&&", "||", "|", ">", "<", "2>&1", "2>"))


def _validate_safe_shell_command(workspace: Workspace, command: str, cwd: Path) -> str | None:
    if any(char in command for char in "\n\r;`$(){}"):
        return "safe_shell rejects command substitution, variables, semicolons, braces, and multiline commands"
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|&<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError as exc:
        return f"Invalid shell command: {exc}"
    if not tokens:
        return "Provide a non-empty command"

    operators = {"&&", "||", "|"}
    redirects = {">", ">>", "<", "2>", "2>>", "1>", "1>>"}
    inline_redirects = {"2>&1", "1>&2"}
    expect_command = True
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token in operators:
            if expect_command:
                return f"Unexpected shell operator: {token}"
            expect_command = True
            idx += 1
            continue
        if token in inline_redirects:
            idx += 1
            continue
        if token in redirects:
            if idx + 1 >= len(tokens):
                return f"Missing redirection target after {token}"
            idx += 2
            continue
        if token.startswith((">", ">>", "2>", "2>>", "1>", "1>>")) and token not in {">", ">>"}:
            idx += 1
            continue
        if expect_command:
            executable = Path(token).name
            if executable == "cd":
                if idx + 1 >= len(tokens):
                    return "cd requires a workspace-relative target"
                target_arg = tokens[idx + 1]
                try:
                    target = (cwd / target_arg).resolve()
                    target.relative_to(workspace.root)
                except Exception:
                    return f"cd target escapes workspace: {target_arg}"
                if not target.exists() or not target.is_dir():
                    return f"cd target is not a directory: {target_arg}"
            elif executable not in SAFE_COMMANDS:
                return (
                    f"Command not allowlisted: {token}. "
                    f"Allowed: {', '.join(sorted(SAFE_COMMANDS | {'cd'}))}"
                )
            expect_command = False
        idx += 1
    if expect_command:
        return "Command cannot end with a shell operator"
    return None


# ── Conda / python launching ──────────────────────────────────────────


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


# ── Tool handlers ─────────────────────────────────────────────────────


def safe_shell(workspace: Workspace, args: dict[str, Any]) -> dict[str, Any]:
    command = args.get("command")
    argv = args.get("argv")
    cwd = _command_cwd(workspace, args.get("cwd"))
    timeout = int(args.get("timeout", 60))
    if command and isinstance(command, str):
        if _uses_shell_syntax(command):
            validation_error = _validate_safe_shell_command(workspace, command, cwd)
            if validation_error:
                raise ValueError(validation_error)
            return _run(["/bin/sh", "-c", command], cwd=cwd, timeout=timeout)
        argv = shlex.split(command)
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        raise ValueError("Provide 'command' (string) or 'argv' (list of strings)")
    if Path(argv[0]).name not in SAFE_COMMANDS:
        raise ValueError(f"Command not allowlisted: {argv[0]}. Allowed: {', '.join(sorted(SAFE_COMMANDS))}")
    return _run(argv, cwd=cwd, timeout=timeout)


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
    """Run an arbitrary shell command. Confirmation is gated by PermissionPolicy at the agent level.

    The bash tool refuses any command that contains ``rm`` (including
    pipes, sub-shells and ``sudo rm``). Deletions must go through the
    dedicated ``rm`` tool so the user always sees a separate approval
    prompt for destructive ops.
    """

    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("Provide 'command' as a non-empty string")
    if _RM_PATTERN.search(command):
        return {
            "error": (
                "bash refuses commands containing 'rm'. Use the dedicated "
                "rm tool (it has its own approval prompt)."
            ),
            "command": command,
        }
    return _run(
        ["/bin/sh", "-c", command],
        cwd=_command_cwd(workspace, args.get("cwd")),
        timeout=int(args.get("timeout", 60)),
    )


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
