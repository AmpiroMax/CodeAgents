"""Native tool specifications: descriptions and parameter schemas.

Single source of truth for what the model sees. Lives in code so the
description that appears in the OpenAI ``tools`` payload is exactly
what the file says — no TOML side-loading.

Generated once during the Stage-2 refactor from the legacy
``config/tools.toml``. Edit this file directly going forward.
"""

from __future__ import annotations

from codeagents.core.permissions import Permission
from codeagents.tools._registry import ParamSpec, ToolSpec


NATIVE_TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name='read_file',
        kind='native',
        permission=Permission('read_only'),
        description='Read a UTF-8 text file from the current workspace and return numbered lines. Use this before editing so you can quote exact context and line numbers.\n\nExample: read_file {"path":"src/codeagents/agent.py","offset":1,"limit":80}\nExample: read_file {"path":"README.md"}',
        params=(
            ParamSpec(
                name='path',
                type='string',
                description='Relative path to the file in the workspace',
                required=True,
            ),
            ParamSpec(
                name='offset',
                type='integer',
                description='Starting line number (1-based). Default: 1',
                required=False,
            ),
            ParamSpec(
                name='limit',
                type='integer',
                description='Maximum number of lines to return. Default: 200',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='pwd',
        kind='native',
        permission=Permission('read_only'),
        description="Print the agent's current working directory and the workspace trust boundary.\n\nReturns: cwd (where relative paths resolve), workspace_root (write boundary), and read_only (true when cwd has cd-ed outside workspace_root). Use this to confirm context before running file or shell tools.\n\nExample: pwd {}",
    ),
    ToolSpec(
        name='cd',
        kind='native',
        permission=Permission('read_only'),
        description='Change cwd. May leave workspace_root (then agent goes read-only). Trust boundary unchanged — use change_workspace to switch projects.\n\nExample: cd {"path":"src/codeagents"}',
        params=(
            ParamSpec(
                name='path',
                type='string',
                description='Target directory. Relative paths resolve against the current cwd; ~ and absolute paths are also accepted.',
                required=True,
            ),
        ),
    ),
    ToolSpec(
        name='change_workspace',
        kind='native',
        permission=Permission('workspace_write'),
        description='Switch the workspace trust boundary to a new directory (resets approvals). Requires user confirmation. For temporary navigation prefer cd.\n\nExample: change_workspace {"path":"~/code/new-project"}',
        params=(
            ParamSpec(
                name='path',
                type='string',
                description='Absolute or ~-relative path to the new workspace root. Created if missing.',
                required=True,
            ),
        ),
    ),
    ToolSpec(
        name='ls',
        kind='native',
        permission=Permission('read_only'),
        description='List files and directories like the shell command `ls`, but safely scoped to the workspace. Use it to inspect a directory without running arbitrary shell.\n\nExample: ls {"path":"src","long":true}\nExample: ls {"path":".","all":true,"max_results":50}',
        params=(
            ParamSpec(
                name='path',
                type='string',
                description='Relative path to list. Default: current directory',
                required=False,
            ),
            ParamSpec(
                name='all',
                type='boolean',
                description='Show hidden files. Default: false',
                required=False,
            ),
            ParamSpec(
                name='long',
                type='boolean',
                description='Show type and size. Default: false',
                required=False,
            ),
            ParamSpec(
                name='max_results',
                type='integer',
                description='Maximum entries to return. Default: 200',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='cat',
        kind='native',
        permission=Permission('read_only'),
        description='Read a UTF-8 text file like `cat`, safely scoped to the workspace. Prefer `read_file` when numbered lines are useful for editing; use `cat` when raw text is easier.\n\nExample: cat {"path":"pyproject.toml"}\nExample: cat {"path":"src/codeagents/runtime.py","offset":40,"limit":60}',
        params=(
            ParamSpec(
                name='path',
                type='string',
                description='Relative path to the file',
                required=True,
            ),
            ParamSpec(
                name='offset',
                type='integer',
                description='Starting line number (1-based). Default: 1',
                required=False,
            ),
            ParamSpec(
                name='limit',
                type='integer',
                description='Maximum lines to return. Default: 400',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='grep',
        kind='native',
        permission=Permission('read_only'),
        description='Search text like `grep`/`rg` inside the workspace. Use this for exact names, errors, imports, config keys, and symbols.\n\nExample: grep {"query":"PermissionPolicy","path":"src/codeagents"}\nExample: grep {"query":"flake8","path":".","ignore_case":true,"max_count":20}',
        params=(
            ParamSpec(
                name='query',
                type='string',
                description='Search string or regex pattern',
                required=True,
            ),
            ParamSpec(
                name='path',
                type='string',
                description='Relative file or directory to search. Default: current directory',
                required=False,
            ),
            ParamSpec(
                name='ignore_case',
                type='boolean',
                description='Case-insensitive search. Default: false',
                required=False,
            ),
            ParamSpec(
                name='max_count',
                type='integer',
                description='Maximum matches per searched file. Default: 100',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='head',
        kind='native',
        permission=Permission('read_only'),
        description='Read the first lines of a UTF-8 text file. Use it for quick file previews.\n\nExample: head {"path":"README.md","lines":30}',
        params=(
            ParamSpec(
                name='path',
                type='string',
                description='Relative path to the file',
                required=True,
            ),
            ParamSpec(
                name='lines',
                type='integer',
                description='Number of lines. Default: 20',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='tail',
        kind='native',
        permission=Permission('read_only'),
        description='Read the last lines of a UTF-8 text file. Use it for logs, generated output, and recent append-only files.\n\nExample: tail {"path":".codeagents/audit.jsonl","lines":20}',
        params=(
            ParamSpec(
                name='path',
                type='string',
                description='Relative path to the file',
                required=True,
            ),
            ParamSpec(
                name='lines',
                type='integer',
                description='Number of lines. Default: 20',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='wc',
        kind='native',
        permission=Permission('read_only'),
        description='Count lines, words, and bytes for a UTF-8 text file, like `wc`.\n\nExample: wc {"path":"src/codeagents/tools_native/code.py"}',
        params=(
            ParamSpec(
                name='path',
                type='string',
                description='Relative path to the file',
                required=True,
            ),
        ),
    ),
    ToolSpec(
        name='curl',
        kind='native',
        permission=Permission('network'),
        description='Make a raw HTTP request similar to curl. Use this when the agent needs direct internet access, custom headers, POST/PUT/PATCH/DELETE requests, API calls, or downloading a response to a workspace file. Network permission is required; output files are restricted to the current workspace and cannot be written into `.codeagents`.\n\nParams: `url` (required), `method`, `headers`, `data`, `json`, `output_path`, `timeout`, and `max_chars`. Do not pass both `data` and `json`.\n\nIf `output_path` is omitted, the response body is returned as text and HTML is cleaned into readable text. If `output_path` is provided, the response bytes are saved to that path inside the workspace. `max_chars` only applies when returning text.\n\nExample: curl {"url":"https://api.github.com/repos/python/cpython","headers":{"Accept":"application/json"},"max_chars":12000}\nExample: curl {"url":"https://example.com/archive.zip","output_path":"downloads/archive.zip","timeout":60}\nExample: curl {"url":"https://httpbin.org/post","method":"POST","json":{"hello":"world"}}\nExample: curl {"url":"https://httpbin.org/post","method":"POST","data":"name=agent","headers":{"Content-Type":"application/x-www-form-urlencoded"}}',
        params=(
            ParamSpec(
                name='url',
                type='string',
                description='HTTP or HTTPS URL to request. Only http(s) URLs are accepted.',
                required=True,
            ),
            ParamSpec(
                name='method',
                type='string',
                description='HTTP method. Default: GET',
                required=False,
                enum=('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD'),
            ),
            ParamSpec(
                name='headers',
                type='object',
                description='Optional request headers as a JSON object of string keys and values',
                required=False,
            ),
            ParamSpec(
                name='data',
                type='string',
                description='Optional request body. Use for raw text/form payloads. Do not combine with json.',
                required=False,
            ),
            ParamSpec(
                name='json',
                type='object',
                description='Optional JSON request body. Automatically sets Content-Type: application/json unless provided in headers. Do not combine with data.',
                required=False,
            ),
            ParamSpec(
                name='output_path',
                type='string',
                description='Optional workspace-relative file path where response bytes should be saved',
                required=False,
            ),
            ParamSpec(
                name='timeout',
                type='integer',
                description='Request timeout in seconds. Default: 30',
                required=False,
            ),
            ParamSpec(
                name='max_chars',
                type='integer',
                description='Maximum returned text characters when output_path is omitted. Default: 12000',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='web_search',
        kind='native',
        permission=Permission('network'),
        description='Web search. Provider auto tries Ollama/Yandex/SearXNG/Jina/Brave with round-robin and retries; results cached in .codeagents/web_cache.sqlite3.\n\nExample: web_search {"query":"FastAPI dependency injection docs","limit":5}',
        params=(
            ParamSpec(
                name='query',
                type='string',
                description='Search query. Can include operators such as `site:docs.python.org`.',
                required=True,
            ),
            ParamSpec(
                name='limit',
                type='integer',
                description='Maximum number of results. Default: 5, max: 20',
                required=False,
            ),
            ParamSpec(
                name='provider',
                type='string',
                description='Search provider: auto, searxng, jina, brave, yandex, yandex_search, rambler_proxy, rambler, or gigachat. Default: auto',
                required=False,
                enum=('auto', 'searxng', 'jina', 'brave', 'yandex', 'yandex_search', 'rambler_proxy', 'rambler', 'gigachat'),
            ),
            ParamSpec(
                name='language',
                type='string',
                description='Language code passed to providers when supported, e.g. en or ru',
                required=False,
            ),
            ParamSpec(
                name='time_range',
                type='string',
                description='SearXNG time filter when supported: day, month, or year',
                required=False,
            ),
            ParamSpec(
                name='searxng_url',
                type='string',
                description='Override SearXNG base URL. Default: CODEAGENTS_SEARXNG_URL or http://127.0.0.1:8080',
                required=False,
            ),
            ParamSpec(
                name='rambler_proxy_url',
                type='string',
                description='Override Sber/Rambler proxy endpoint URL. Default: rambler_proxy.url from local config or RAMBLER_PROXY_URL',
                required=False,
            ),
            ParamSpec(
                name='no_cache',
                type='boolean',
                description='Bypass `.codeagents/web_cache.sqlite3`. Default: false',
                required=False,
            ),
            ParamSpec(
                name='ttl_seconds',
                type='integer',
                description='Cache TTL in seconds. Default: 3600',
                required=False,
            ),
            ParamSpec(
                name='retry_attempts',
                type='integer',
                description='Retry attempts per provider for transient network failures. Default: 5, max: 15',
                required=False,
            ),
            ParamSpec(
                name='retry_delay_seconds',
                type='number',
                description='Delay between retry attempts in seconds. Default: 0.25, max: 5.0',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='docs_search',
        kind='native',
        permission=Permission('network'),
        description='Search documentation-oriented web results. If `domain` is set, the tool searches with `site:<domain>`. If `fetch_results=true`, it internally fetches the top result pages and returns readable snippets. Results use the same cheap/free provider stack and cache as `web_search`.\n\nUse this for agent coding tasks like "read the latest docs for FastAPI dependency injection" or "find the official pytest tmp_path docs". Prefer `domain` when you know the official docs site.\n\nParams: `query` (required), `domain`, `limit`, `fetch_results`, `max_fetch`, `retry_attempts`, `retry_delay_seconds`, `max_chars`, and `provider`.\n\nExample: docs_search {"query":"venv create environment","domain":"docs.python.org","limit":5,"fetch_results":true,"max_fetch":2,"max_chars":6000,"retry_attempts":5,"retry_delay_seconds":0.25}\nExample: docs_search {"query":"pytest tmp_path fixture","domain":"docs.pytest.org","limit":3}\nExample: docs_search {"query":"ruff configuration pyproject","provider":"brave","limit":5}',
        params=(
            ParamSpec(
                name='query',
                type='string',
                description='Documentation search query',
                required=True,
            ),
            ParamSpec(
                name='domain',
                type='string',
                description='Optional domain to restrict with site:, e.g. docs.python.org',
                required=False,
            ),
            ParamSpec(
                name='limit',
                type='integer',
                description='Maximum search results. Default: 5, max: 10',
                required=False,
            ),
            ParamSpec(
                name='fetch_results',
                type='boolean',
                description='Fetch top result pages internally and include readable snippets. Default: false',
                required=False,
            ),
            ParamSpec(
                name='max_fetch',
                type='integer',
                description='How many result URLs to fetch when fetch_results=true. Default: 2, max: 5',
                required=False,
            ),
            ParamSpec(
                name='retry_attempts',
                type='integer',
                description='Retry attempts per provider for transient network failures in search and fetch phases. Default: 5, max: 15',
                required=False,
            ),
            ParamSpec(
                name='retry_delay_seconds',
                type='number',
                description='Delay between retry attempts in seconds. Default: 0.25, max: 5.0',
                required=False,
            ),
            ParamSpec(
                name='max_chars',
                type='integer',
                description='Max chars per fetched page. Default: 6000',
                required=False,
            ),
            ParamSpec(
                name='provider',
                type='string',
                description='Search provider: auto, searxng, jina, or brave. Default: auto',
                required=False,
                enum=('auto', 'searxng', 'jina', 'brave'),
            ),
        ),
    ),
    ToolSpec(
        name='rm',
        kind='native',
        permission=Permission('shell_dangerous'),
        description='Delete a file or directory inside the current workspace. The bash tool refuses any command containing rm — this is the only path to delete. Always requires user confirmation. Use recursive=true for dirs.\n\nExample: rm {"path":"old_file.py"}\nExample: rm {"path":"build_output","recursive":true}\nExample: rm {"path":"maybe_missing.tmp","force":true}',
        params=(
            ParamSpec(
                name='path',
                type='string',
                description='Relative path inside the current workspace to delete. Absolute paths and paths escaping the workspace are rejected.',
                required=True,
            ),
            ParamSpec(
                name='recursive',
                type='boolean',
                description='Required to remove directories. Default: false. Keep false when deleting a normal file.',
                required=False,
            ),
            ParamSpec(
                name='force',
                type='boolean',
                description='If true, missing paths return status=missing instead of an error. Default: false.',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='write_file',
        kind='native',
        permission=Permission('workspace_write'),
        description='Write full content to a file in the workspace. Creates parent directories and overwrites existing files. Use for new files or complete rewrites; for targeted changes prefer `edit_file`.\n\nParams: `path` (required) and `content` (required).\n\nReturns also `diagnostics: [...]` if a configured LSP server covers this file, so you can immediately spot type/import errors introduced by the write.\n\nExample: write_file {"path":"notes.md","content":"# Notes\\n"}\nExample: write_file {"path":"src/package/__init__.py","content":"# Package marker\\n"}',
        params=(
            ParamSpec(
                name='path',
                type='string',
                description='Relative path to write',
                required=True,
            ),
            ParamSpec(
                name='content',
                type='string',
                description='Complete file content to write',
                required=True,
            ),
        ),
    ),
    ToolSpec(
        name='create_file',
        kind='native',
        permission=Permission('workspace_write'),
        description='Create a new file in the workspace. Fails if the file already exists. Use `write_file` only when overwriting is intentional.\n\nParams: `path` (required) and `content` (required).\n\nExample: create_file {"path":"tests/test_new_feature.py","content":"def test_smoke():\\n    assert True\\n"}',
        params=(
            ParamSpec(
                name='path',
                type='string',
                description='Relative path for the new file',
                required=True,
            ),
            ParamSpec(
                name='content',
                type='string',
                description='File content',
                required=True,
            ),
        ),
    ),
    ToolSpec(
        name='edit_file',
        kind='native',
        permission=Permission('workspace_write'),
        description='Line-based edits to a file. Preferred: edits=[{line,old_lines,new_lines}]; line is 1-based in the original file, old_lines must match exactly, edits applied bottom-up. Legacy: old_text/new_text for a unique substring. Returns a unified diff.\n\nReturns also `diagnostics: [...]` if a configured LSP server covers this file, so you can verify the edit didn\'t break types/imports without an extra tool call.\n\nExample: edit_file {"path":"src/app.py","edits":[{"line":10,"old_lines":["def hello():"],"new_lines":["def hello(name: str):"]}]}\nExample: edit_file {"path":"README.md","old_text":"old title","new_text":"new title"}',
        params=(
            ParamSpec(
                name='path',
                type='string',
                description='Relative path to the file',
                required=True,
            ),
            ParamSpec(
                name='edits',
                type='array',
                description='Preferred. Array of edit objects. Each item: {line: int (1-based, in original file), old_lines: [string,...] (exact lines without newline), new_lines: [string,...] (replacement lines without newline)}. Example:\n[{"line": 10, "old_lines": ["def hello():", "    return \'world\'"], "new_lines": ["def hello(name: str):", "    return f\'hello {name}\'"]}]',
                required=False,
            ),
            ParamSpec(
                name='old_text',
                type='string',
                description='Legacy: exact text to find and replace (must be unique in the file). Prefer `edits` for new calls.',
                required=False,
            ),
            ParamSpec(
                name='new_text',
                type='string',
                description='Legacy: replacement text. Required when old_text is used.',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='list_directory',
        kind='native',
        permission=Permission('read_only'),
        description='List files and directories with type, size, and name. Use this for project exploration and broad structure; use `ls` for a shell-like listing.\n\nExample: list_directory {"path":".","recursive":false,"max_depth":2}\nExample: list_directory {"path":"src","recursive":true,"max_depth":3}',
        params=(
            ParamSpec(
                name='path',
                type='string',
                description='Relative directory path. Default: current directory',
                required=False,
            ),
            ParamSpec(
                name='recursive',
                type='boolean',
                description='List subdirectories recursively. Default: false',
                required=False,
            ),
            ParamSpec(
                name='max_depth',
                type='integer',
                description='Maximum depth for recursive listing. Default: 2',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='glob_files',
        kind='native',
        permission=Permission('read_only'),
        description='Find files matching a glob pattern. Use this to locate files by name, extension, or directory pattern.\n\nExample: glob_files {"pattern":"**/*.py","max_results":100}\nExample: glob_files {"pattern":"tests/test_*.py"}',
        params=(
            ParamSpec(
                name='pattern',
                type='string',
                description="Glob pattern, e.g. '**/*.py', 'src/**/*.rs', '*.toml'",
                required=True,
            ),
            ParamSpec(
                name='max_results',
                type='integer',
                description='Maximum number of results. Default: 100',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='search',
        kind='native',
        permission=Permission('read_only'),
        description='Search for text or regex in the workspace using ripgrep when available. Returns file:line:match. Use for definitions, usages, error strings, imports, and config keys.\n\nExample: search {"query":"def run_python","max_count":20}\nExample: search {"query":"SHELL_DANGEROUS","max_count":50}',
        params=(
            ParamSpec(
                name='query',
                type='string',
                description='Search string or regex pattern',
                required=True,
            ),
            ParamSpec(
                name='max_count',
                type='integer',
                description='Maximum number of matches. Default: 50',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='propose_patch',
        kind='native',
        permission=Permission('propose'),
        description='Generate a unified diff showing proposed changes without applying them. Use when you want to show a full-file proposal before writing.\n\nParams: `path` (required) and `new_text` (required).\n\nExample: propose_patch {"path":"README.md","new_text":"# New README\\n"}',
        params=(
            ParamSpec(
                name='path',
                type='string',
                description='Relative path to the file',
                required=True,
            ),
            ParamSpec(
                name='new_text',
                type='string',
                description='Full new file content',
                required=True,
            ),
        ),
    ),
    ToolSpec(
        name='mkdir',
        kind='native',
        permission=Permission('workspace_write'),
        description='Create a directory inside the current workspace. Use this before writing files into a new folder, creating project structure, or preparing a download directory. This tool never creates directories outside the workspace and refuses to write inside `.codeagents`.\n\nExample: mkdir {"path":"src/components"}\nExample: mkdir {"path":"downloads/assets","parents":true,"exist_ok":true}',
        params=(
            ParamSpec(
                name='path',
                type='string',
                description='Workspace-relative directory path to create',
                required=True,
            ),
            ParamSpec(
                name='parents',
                type='boolean',
                description='Create missing parent directories. Default: true',
                required=False,
            ),
            ParamSpec(
                name='exist_ok',
                type='boolean',
                description='Return success if the directory already exists. Default: true',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='mv',
        kind='native',
        permission=Permission('workspace_write'),
        description='Move or rename a file or directory inside the current workspace. Use this for renaming files, moving generated assets into place, reorganizing folders, or moving downloaded files. This tool never moves paths outside the workspace and refuses to move anything into or out of `.codeagents`.\n\nBy default, it refuses to overwrite an existing destination. Pass `overwrite=true` only when replacing the destination is intended.\n\nExample: mv {"source":"old_name.py","destination":"src/new_name.py"}\nExample: mv {"source":"downloads/archive.zip","destination":"assets/archive.zip","overwrite":true}\nExample: mv {"source":"tmp/generated","destination":"src/generated"}',
        params=(
            ParamSpec(
                name='source',
                type='string',
                description='Workspace-relative source file or directory path',
                required=True,
            ),
            ParamSpec(
                name='destination',
                type='string',
                description='Workspace-relative destination file or directory path',
                required=True,
            ),
            ParamSpec(
                name='overwrite',
                type='boolean',
                description='Replace existing destination file or directory. Default: false',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='safe_shell',
        kind='native',
        permission=Permission('shell_safe'),
        description='Run an allowlisted shell command in the workspace. Prefer dedicated tools (`run_python`, `python_module`, `flake8`, `grep`, `git_status`, `git_diff`) when possible. This is for safe checks only, not destructive operations.\n\nParams: `command`, `argv`, `cwd`, and `timeout`. Pass either `command` (string) or `argv` (array of strings). `cwd` is an optional workspace-relative directory. `timeout` defaults to 60 seconds.\n\nThe command executable must be allowlisted. Limited shell syntax is supported for allowlisted commands: `cd <workspace path> && ...`, `&&`, `||`, pipes, and `2>&1`, so checks like `cargo check 2>&1 | head -50` work. Unsafe shell features such as variables, command substitution, semicolons, and multiline commands are rejected. Use confirmation-gated `shell` for arbitrary commands.\n\nExample: safe_shell {"command":"cargo check"}\nExample: safe_shell {"command":"cargo check 2>&1 | head -50","cwd":"crates/terminal-agent","timeout":120}\nExample: safe_shell {"argv":["python","-m","compileall","src"],"timeout":120}',
        params=(
            ParamSpec(
                name='command',
                type='string',
                description='Command string to run. The executable must be allowlisted. May use limited shell syntax for allowlisted commands.',
                required=False,
            ),
            ParamSpec(
                name='argv',
                type='array',
                description='Command argv array. Use this when no shell syntax is needed, e.g. ["python", "-m", "compileall", "src"].',
                required=False,
            ),
            ParamSpec(
                name='cwd',
                type='string',
                description='Workspace-relative directory to run from. Default: workspace root.',
                required=False,
            ),
            ParamSpec(
                name='timeout',
                type='integer',
                description='Timeout in seconds. Default: 60',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='run_python',
        kind='native',
        permission=Permission('shell_safe'),
        description='Run a Python file inside the workspace. If `conda_activate` selected an environment, this uses `conda run -n <env> python ...`; otherwise it uses `python3`. Use this to execute code the agent wrote.\n\nParams: `path` (required), `args`, `module`, and `timeout`. If `module=true`, the path is converted to a module name and run with `python -m`.\n\nExample: run_python {"path":"tetris.py"}\nExample: run_python {"path":"scripts/check.py","args":["--verbose"],"timeout":120}\nExample: run_python {"path":"src/package/cli.py","module":true,"args":["--help"]}',
        params=(
            ParamSpec(
                name='path',
                type='string',
                description='Relative path to the Python file',
                required=True,
            ),
            ParamSpec(
                name='args',
                type='array',
                description='Arguments passed to the script',
                required=False,
            ),
            ParamSpec(
                name='module',
                type='boolean',
                description='Run the path as a module name derived from the path. Default: false',
                required=False,
            ),
            ParamSpec(
                name='timeout',
                type='integer',
                description='Timeout in seconds. Default: 60',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='python_module',
        kind='native',
        permission=Permission('shell_safe'),
        description='Run `python -m <module>` inside the workspace. If a conda env is active for this workspace, it runs inside that env. Use for pytest, compileall, package entrypoints, and modules like `tetris`.\n\nExample: python_module {"module":"pytest","args":["-q"],"timeout":120}\nExample: python_module {"module":"compileall","args":["src"]}',
        params=(
            ParamSpec(
                name='module',
                type='string',
                description='Python module name, e.g. pytest or tetris',
                required=True,
            ),
            ParamSpec(
                name='args',
                type='array',
                description='Arguments passed after the module name',
                required=False,
            ),
            ParamSpec(
                name='timeout',
                type='integer',
                description='Timeout in seconds. Default: 60',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='flake8',
        kind='native',
        permission=Permission('shell_safe'),
        description='Run `python -m flake8` on a workspace file or directory. If flake8 is missing, the result includes a hint to install it with `pip_install` or conda. Use after edits to catch style and syntax issues.\n\nExample: flake8 {"path":"src","args":["--max-line-length=100"],"timeout":120}\nExample: flake8 {"path":"tests"}',
        params=(
            ParamSpec(
                name='path',
                type='string',
                description='Relative file or directory to lint. Default: current directory',
                required=False,
            ),
            ParamSpec(
                name='args',
                type='array',
                description='Extra flake8 arguments',
                required=False,
            ),
            ParamSpec(
                name='timeout',
                type='integer',
                description='Timeout in seconds. Default: 60',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='pip_install',
        kind='native',
        permission=Permission('network'),
        description='Install Python dependencies with `python -m pip install` in the active interpreter or selected conda env. This may access the network and therefore can require user approval depending on policy.\n\nExample: pip_install {"packages":["pytest","flake8"]}\nExample: pip_install {"requirements":"requirements.txt","upgrade":true,"timeout":600}',
        params=(
            ParamSpec(
                name='packages',
                type='array',
                description='Packages to install',
                required=False,
            ),
            ParamSpec(
                name='requirements',
                type='string',
                description='Relative requirements file path',
                required=False,
            ),
            ParamSpec(
                name='upgrade',
                type='boolean',
                description='Pass --upgrade. Default: false',
                required=False,
            ),
            ParamSpec(
                name='timeout',
                type='integer',
                description='Timeout in seconds. Default: 600',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='shell',
        kind='native',
        permission=Permission('shell_dangerous'),
        description='Run an arbitrary installed shell command in the workspace, including commands that do not have a dedicated tool such as `ollama list`. This is dangerous and requires explicit user confirmation in the TUI. Prefer dedicated tools when they exist.\n\nParams: `command` (required), `cwd`, and `timeout`. `cwd` is an optional workspace-relative directory. `timeout` defaults to 60 seconds.\n\nPersistent approval is scoped to the first executable name for simple commands. For example, approving `ollama list` with "always" allows future `ollama ...` commands in this workspace, but does not approve all shell commands. Compound shell syntax such as `;`, `&&`, pipes, redirects, variables, and subcommands can only be approved once and will not be remembered.\n\nDo not use this for file deletion; use `rm`, which is workspace-scoped and has extra safety checks.\n\nExample: shell {"command":"ollama list"}\nExample: shell {"command":"brew --version"}\nExample: shell {"command":"python scripts/local_tool.py --check","cwd":"tools","timeout":120}',
        params=(
            ParamSpec(
                name='command',
                type='string',
                description='Shell command to run from the workspace root. Simple commands can be remembered per executable name; compound shell syntax requires one-time approval.',
                required=True,
            ),
            ParamSpec(
                name='timeout',
                type='integer',
                description='Timeout in seconds. Default: 60',
                required=False,
            ),
            ParamSpec(
                name='cwd',
                type='string',
                description='Workspace-relative directory to run from. Default: workspace root.',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='conda_env_list',
        kind='native',
        permission=Permission('read_only'),
        description='List conda environments using `conda env list --json`. Use before selecting or creating an environment.\n\nExample: conda_env_list {}',
    ),
    ToolSpec(
        name='conda_create',
        kind='native',
        permission=Permission('network'),
        description='Create a conda environment with `conda create -y -n <name> ...`. This may download packages and can require user approval depending on network policy.\n\nExample: conda_create {"name":"codeagents-test","python":"3.11","packages":["pytest","flake8"],"timeout":1200}',
        params=(
            ParamSpec(
                name='name',
                type='string',
                description='Conda environment name',
                required=True,
            ),
            ParamSpec(
                name='python',
                type='string',
                description='Python version, e.g. 3.11. Default: conda default',
                required=False,
            ),
            ParamSpec(
                name='packages',
                type='array',
                description='Packages to install while creating the environment',
                required=False,
            ),
            ParamSpec(
                name='timeout',
                type='integer',
                description='Timeout in seconds. Default: 1200',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='conda_activate',
        kind='native',
        permission=Permission('shell_safe'),
        description='Select a conda environment for future Python tools in this workspace. This is not shell `source activate`; it stores the selected env in `.codeagents/active_conda_env.json`, and `run_python`, `python_module`, `flake8`, and `pip_install` then use `conda run -n <name>`.\n\nExample: conda_activate {"name":"agents"}',
        params=(
            ParamSpec(
                name='name',
                type='string',
                description='Conda environment name',
                required=True,
            ),
        ),
    ),
    ToolSpec(
        name='conda_deactivate',
        kind='native',
        permission=Permission('shell_safe'),
        description='Clear the active conda environment for this workspace. Future Python tools go back to default `python3`.\n\nExample: conda_deactivate {}',
    ),
    ToolSpec(
        name='conda_run',
        kind='native',
        permission=Permission('shell_safe'),
        description='Run an allowlisted command inside a conda environment using `conda run -n <name> ...`. Use this when a dedicated tool is not enough. The command must be allowlisted.\n\nExample: conda_run {"name":"agents","command":"python -m pytest -q","timeout":120}\nExample: conda_run {"command":"python -m flake8 src","timeout":120}',
        params=(
            ParamSpec(
                name='command',
                type='string',
                description="Command to run, e.g. 'python -m pytest'",
                required=True,
            ),
            ParamSpec(
                name='name',
                type='string',
                description='Conda environment name. Defaults to active env',
                required=False,
            ),
            ParamSpec(
                name='timeout',
                type='integer',
                description='Timeout in seconds. Default: 60',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='git_diff',
        kind='native',
        permission=Permission('read_only'),
        description='Show git diff for the workspace. Use to review pending changes before summarizing or committing.\n\nExample: git_diff {}\nExample: git_diff {"staged":true}',
        params=(
            ParamSpec(
                name='staged',
                type='boolean',
                description='Show staged changes instead of unstaged. Default: false',
                required=False,
            ),
        ),
    ),
    ToolSpec(
        name='git_status',
        kind='native',
        permission=Permission('read_only'),
        description='Show git status: modified, added, deleted, and untracked files.\n\nExample: git_status {}',
    ),
    ToolSpec(
        name='run_tests',
        kind='native',
        permission=Permission('shell_safe'),
        description='Run a common test or check suite. `python` runs compileall, `pytest` runs pytest through Python, `flake8` runs the flake8 tool, `rust` runs cargo test, and `cargo-check` runs cargo check.\n\nExample: run_tests {"suite":"python"}\nExample: run_tests {"suite":"pytest"}\nExample: run_tests {"suite":"flake8"}',
        params=(
            ParamSpec(
                name='suite',
                type='string',
                description='Test suite to run',
                required=False,
                enum=('python', 'pytest', 'flake8', 'rust', 'cargo-check'),
            ),
        ),
    ),
    ToolSpec(
        name='lsp_definition',
        kind='native',
        permission=Permission('read_only'),
        description='Jump to the definition of a symbol via LSP. Pass the file and a 1-based position (line, character). Returns up to a few `locations`, each with a 5-line snippet around the definition so you can see the signature without an extra `read_file`.\n\nExample: lsp_definition {"path":"src/codeagents/agent.py","line":135,"character":9}',
        params=(
            ParamSpec(name='path', type='string', description='Workspace-relative file path', required=True),
            ParamSpec(name='line', type='integer', description='1-based line of the symbol', required=True),
            ParamSpec(name='character', type='integer', description='1-based column (default 1)', required=False),
        ),
    ),
    ToolSpec(
        name='lsp_references',
        kind='native',
        permission=Permission('read_only'),
        description='Find all references to a symbol via LSP. Returns up to `limit` references, each with a 1-line snippet. Set `include_declaration=true` to also include the definition site.\n\nExample: lsp_references {"path":"src/codeagents/agent.py","line":135,"character":9,"include_declaration":false,"limit":20}',
        params=(
            ParamSpec(name='path', type='string', description='Workspace-relative file path', required=True),
            ParamSpec(name='line', type='integer', description='1-based line', required=True),
            ParamSpec(name='character', type='integer', description='1-based column (default 1)', required=False),
            ParamSpec(name='include_declaration', type='boolean', description='Include the declaration site (default false)', required=False),
            ParamSpec(name='limit', type='integer', description='Cap on returned references (default 50)', required=False),
        ),
    ),
    ToolSpec(
        name='lsp_hover',
        kind='native',
        permission=Permission('read_only'),
        description='Get type information and docstring for a symbol via LSP hover. Returns a markdown string. Cheap way to check signatures before editing.\n\nExample: lsp_hover {"path":"src/codeagents/agent.py","line":135,"character":9}',
        params=(
            ParamSpec(name='path', type='string', description='Workspace-relative file path', required=True),
            ParamSpec(name='line', type='integer', description='1-based line', required=True),
            ParamSpec(name='character', type='integer', description='1-based column (default 1)', required=False),
        ),
    ),
    ToolSpec(
        name='lsp_workspace_symbol',
        kind='native',
        permission=Permission('read_only'),
        description='Search for symbols across the workspace via LSP `workspace/symbol`. Pass a substring; returns up to `limit` matches with file/line. Use this when you know a name but not where it lives.\n\nExample: lsp_workspace_symbol {"query":"AgentCore","limit":20}',
        params=(
            ParamSpec(name='query', type='string', description='Substring of the symbol name', required=True),
            ParamSpec(name='limit', type='integer', description='Cap on returned symbols (default 50)', required=False),
        ),
    ),
    ToolSpec(
        name='lsp_diagnostics',
        kind='native',
        permission=Permission('read_only'),
        description='Run the LSP server over a file and return current diagnostics (errors/warnings/hints) without modifying it. Useful for a one-off lint check.\n\nExample: lsp_diagnostics {"path":"src/codeagents/agent.py"}',
        params=(
            ParamSpec(name='path', type='string', description='Workspace-relative file path', required=True),
        ),
    ),
    ToolSpec(
        name='code_context',
        kind='native',
        permission=Permission('read_only'),
        description='One-shot context bundle for a code target. Combines LSP precision (definition + references + hover + diagnostics) with semantic neighbors from the embedding index, plus the nearest tests touching the same file. Use this when you want \"everything I need to safely change X\" in a single call.\n\n`target` accepts: `path:line:col` for a point in code, `path` for a whole-file overview, or a bare symbol name (used for `workspace/symbol`).\n\nExample: code_context {"target":"src/codeagents/agent.py:135:9"}\nExample: code_context {"target":"AgentCore"}\nExample: code_context {"target":"src/codeagents/agent.py","include_tests":false,"include_rag":true,"k":3}',
        params=(
            ParamSpec(name='target', type='string', description='`path`, `path:line:col`, or a symbol name', required=True),
            ParamSpec(name='include_rag', type='boolean', description='Include semantic neighbors from the embedding index (default true)', required=False),
            ParamSpec(name='include_tests', type='boolean', description='Include nearest tests touching the same file (default true)', required=False),
            ParamSpec(name='k', type='integer', description='Cap on rag_neighbors / nearest_tests (default 5)', required=False),
        ),
    ),
)

__all__ = ['NATIVE_TOOL_SPECS']
