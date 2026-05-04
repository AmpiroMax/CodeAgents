from __future__ import annotations

import sys
from pathlib import Path

from codeagents.agent import AgentCore
from codeagents.tools_native.code import (
    cat,
    conda_activate,
    conda_deactivate,
    flake8,
    grep,
    head,
    ls,
    mkdir,
    mv,
    pwd,
    python_module,
    rm,
    run_python,
    tail,
    wc,
)
from codeagents.workspace import Workspace


def _workspace(path: Path) -> Workspace:
    return Workspace.from_path(path)


def test_console_read_tools(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("one two\nneedle line\nlast line\n", encoding="utf-8")
    workspace = _workspace(tmp_path)

    assert pwd(workspace, {})["cwd"] == str(tmp_path)
    assert "notes.txt" in ls(workspace, {})["entries"]
    assert cat(workspace, {"path": "notes.txt", "offset": 2, "limit": 1})["content"] == "needle line"
    assert head(workspace, {"path": "notes.txt", "lines": 1})["content"] == "one two"
    assert tail(workspace, {"path": "notes.txt", "lines": 1})["content"] == "last line"
    assert wc(workspace, {"path": "notes.txt"}) == {
        "path": "notes.txt",
        "lines": 3,
        "words": 6,
        "bytes": len("one two\nneedle line\nlast line\n".encode("utf-8")),
    }


def test_grep_searches_inside_workspace(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def main():\n    return 'Needle'\n", encoding="utf-8")
    workspace = _workspace(tmp_path)

    result = grep(workspace, {"query": "needle", "path": "src", "ignore_case": True})

    assert result["exit_code"] == 0
    assert "src/app.py" in result["stdout"]
    assert "Needle" in result["stdout"]


def test_agent_exposes_console_tools(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("hello\n", encoding="utf-8")
    agent = AgentCore.from_workspace(tmp_path)

    names = {tool.name for tool in agent.tools.list()}
    assert {"pwd", "ls", "cat", "grep", "head", "tail", "wc"}.issubset(names)
    assert "web_fetch" not in names
    assert {
        "rm",
        "mkdir",
        "mv",
        "run_python",
        "python_module",
        "flake8",
        "pip_install",
        "conda_env_list",
        "conda_create",
        "conda_activate",
        "conda_deactivate",
        "conda_run",
    }.issubset(names)

    result = agent.call_tool("cat", {"path": "file.txt"})
    assert result.result["content"] == "hello"
    assert result.confirmation_required is False
    rm_spec = agent.tools.get("rm")
    pwd_spec = agent.tools.get("pwd")
    assert "Example:" in rm_spec.description
    assert "Remove (delete)" in rm_spec.description
    assert "Example:" in pwd_spec.description
    assert "Example:" in agent.tools.get("mkdir").description
    assert "Example:" in agent.tools.get("mv").description

    rm_result = agent.call_tool("rm", {"path": "file.txt"})
    assert rm_result.confirmation_required is True
    assert rm_result.result["status"] == "confirmation_required"
    assert (tmp_path / "file.txt").exists()

    agent.approvals.approve_tool("rm", agent.tools.get("rm").permission)
    remembered_rm = agent.call_tool("rm", {"path": "file.txt"})
    assert remembered_rm.confirmation_required is False
    assert remembered_rm.result["status"] == "removed"
    assert not (tmp_path / "file.txt").exists()

    mkdir_result = agent.call_tool("mkdir", {"path": "created"})
    assert mkdir_result.confirmation_required is True
    assert mkdir_result.result["status"] == "confirmation_required"
    assert not (tmp_path / "created").exists()

    mv_result = agent.call_tool("mv", {"source": "missing.txt", "destination": "moved.txt"})
    assert mv_result.confirmation_required is True
    assert mv_result.result["status"] == "confirmation_required"


def test_write_tools_require_confirmation_from_config(tmp_path: Path) -> None:
    agent = AgentCore.from_workspace(tmp_path)

    result = agent.call_tool("write_file", {"path": "new.txt", "content": "hello\n"})

    assert result.confirmation_required is True
    assert result.result["status"] == "confirmation_required"
    assert not (tmp_path / "new.txt").exists()


def test_tool_call_rejects_extra_arguments_before_confirmation(tmp_path: Path) -> None:
    agent = AgentCore.from_workspace(tmp_path)

    result = agent.call_tool(
        "mkdir",
        {"path": "new-dir", "parents": True, "unexpected": "nope"},
    )

    assert result.confirmation_required is False
    assert result.result["status"] == "rejected_invalid_arguments"
    assert result.result["extra_arguments"] == ["unexpected"]
    assert "path" in result.result["allowed_arguments"]
    assert not (tmp_path / "new-dir").exists()


def test_tool_schema_disallows_additional_properties(tmp_path: Path) -> None:
    agent = AgentCore.from_workspace(tmp_path)

    specs = {spec.name: spec.to_json_schema() for spec in agent._agent_tools_as_specs()}

    assert specs["mkdir"]["function"]["parameters"]["additionalProperties"] is False
    assert "web_fetch" not in specs


def test_shell_approval_is_scoped_to_executable(tmp_path: Path) -> None:
    agent = AgentCore.from_workspace(tmp_path)
    command_name = Path(sys.executable).name
    command = f"{sys.executable} -c 'print(123)'"

    first = agent.call_tool("shell", {"command": command})
    assert first.confirmation_required is True
    assert first.result["status"] == "confirmation_required"

    agent.approvals.approve_shell_command(command_name, agent.tools.get("shell").permission)
    remembered = agent.call_tool("shell", {"command": command})
    assert remembered.confirmation_required is False
    assert remembered.result["exit_code"] == 0
    assert remembered.result["stdout"].strip() == "123"

    compound = agent.call_tool("shell", {"command": f"{command} && echo unsafe"})
    assert compound.confirmation_required is True
    assert compound.result["status"] == "confirmation_required"


def test_mkdir_creates_only_workspace_directories(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    created = mkdir(workspace, {"path": "nested/dir"})

    assert created["status"] == "created"
    assert created["path"] == "nested/dir"
    assert (tmp_path / "nested" / "dir").is_dir()
    assert "escapes workspace" in mkdir(workspace, {"path": "../outside"})["error"]
    assert "internal state" in mkdir(workspace, {"path": ".codeagents/cache"})["error"]


def test_mkdir_respects_parents_and_exist_ok(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    missing_parent = mkdir(workspace, {"path": "a/b", "parents": False})
    assert "Parent directory does not exist" in missing_parent["error"]

    (tmp_path / "already").mkdir()
    exists = mkdir(workspace, {"path": "already", "exist_ok": True})
    assert exists["status"] == "exists"

    exists_error = mkdir(workspace, {"path": "already", "exist_ok": False})
    assert "already exists" in exists_error["error"]


def test_mv_moves_files_and_directories_inside_workspace(tmp_path: Path) -> None:
    (tmp_path / "old.txt").write_text("hello\n", encoding="utf-8")
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "nested.txt").write_text("nested\n", encoding="utf-8")
    workspace = _workspace(tmp_path)

    moved_file = mv(workspace, {"source": "old.txt", "destination": "new/name.txt"})
    moved_dir = mv(workspace, {"source": "folder", "destination": "renamed"})

    assert moved_file["status"] == "moved"
    assert moved_file["kind"] == "file"
    assert (tmp_path / "new" / "name.txt").read_text(encoding="utf-8") == "hello\n"
    assert not (tmp_path / "old.txt").exists()
    assert moved_dir["kind"] == "directory"
    assert (tmp_path / "renamed" / "nested.txt").read_text(encoding="utf-8") == "nested\n"


def test_mv_refuses_escape_internal_and_overwrite_by_default(tmp_path: Path) -> None:
    (tmp_path / "source.txt").write_text("source\n", encoding="utf-8")
    (tmp_path / "dest.txt").write_text("dest\n", encoding="utf-8")
    workspace = _workspace(tmp_path)

    overwrite_error = mv(workspace, {"source": "source.txt", "destination": "dest.txt"})
    assert "already exists" in overwrite_error["error"]
    assert (tmp_path / "source.txt").exists()
    assert (tmp_path / "dest.txt").read_text(encoding="utf-8") == "dest\n"

    overwritten = mv(workspace, {"source": "source.txt", "destination": "dest.txt", "overwrite": True})
    assert overwritten["status"] == "moved"
    assert (tmp_path / "dest.txt").read_text(encoding="utf-8") == "source\n"

    (tmp_path / "x.txt").write_text("x\n", encoding="utf-8")
    assert "escapes workspace" in mv(workspace, {"source": "x.txt", "destination": "../x.txt"})["error"]
    assert "internal state" in mv(workspace, {"source": "x.txt", "destination": ".codeagents/x.txt"})["error"]


def test_python_execution_tools(tmp_path: Path) -> None:
    (tmp_path / "hello.py").write_text(
        "import sys\nprint('hello ' + ' '.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    workspace = _workspace(tmp_path)

    result = run_python(workspace, {"path": "hello.py", "args": ["agent"]})

    assert result["exit_code"] == 0
    assert result["stdout"].strip() == "hello agent"


def test_python_module_tool(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    result = python_module(workspace, {"module": "compileall", "args": ["."]})

    assert result["exit_code"] == 0


def test_flake8_reports_missing_module_with_hint(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text("x=1\n", encoding="utf-8")
    workspace = _workspace(tmp_path)

    result = flake8(workspace, {"path": "bad.py"})

    assert "argv" in result
    if result["exit_code"] == 1 and "No module named flake8" in result["stderr"]:
        assert "hint" in result


def test_conda_activation_state(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)

    activated = conda_activate(workspace, {"name": "agent-test"})
    assert activated["status"] == "activated"

    marker = tmp_path / ".codeagents" / "active_conda_env.json"
    assert marker.exists()

    deactivated = conda_deactivate(workspace, {})
    assert deactivated["status"] == "deactivated"
    assert not marker.exists()


def test_rm_removes_only_workspace_paths(tmp_path: Path) -> None:
    (tmp_path / "delete-me.txt").write_text("bye\n", encoding="utf-8")
    workspace = _workspace(tmp_path)

    removed = rm(workspace, {"path": "delete-me.txt"})

    assert removed["status"] == "removed"
    assert removed["kind"] == "file"
    assert not (tmp_path / "delete-me.txt").exists()
    assert "escapes workspace" in rm(workspace, {"path": "../outside.txt"})["error"]
    assert "workspace root" in rm(workspace, {"path": "."})["error"]


def test_rm_directory_requires_recursive(tmp_path: Path) -> None:
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "nested.txt").write_text("bye\n", encoding="utf-8")
    workspace = _workspace(tmp_path)

    rejected = rm(workspace, {"path": "old"})
    removed = rm(workspace, {"path": "old", "recursive": True})

    assert "recursive=true" in rejected["error"]
    assert removed["status"] == "removed"
    assert not (tmp_path / "old").exists()
