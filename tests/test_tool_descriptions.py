from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path


TOOLS_TOML = Path(__file__).resolve().parents[1] / "config" / "tools.toml"
EXAMPLE_RE = re.compile(r"Example:\s*([a-zA-Z0-9_\.]+)\s*(\{[^\n]*\})")


def test_tool_descriptions_match_declared_arguments() -> None:
    raw = tomllib.loads(TOOLS_TOML.read_text(encoding="utf-8"))

    for name, tool in raw.get("tools", {}).items():
        params = set((tool.get("params") or {}).keys())
        description = str(tool.get("description", ""))
        examples = EXAMPLE_RE.findall(description)
        used_args: set[str] = set()

        for example_tool, raw_args in examples:
            assert example_tool == name
            args = json.loads(raw_args)
            assert isinstance(args, dict)
            assert not (set(args) - params)
            required = {
                param_name
                for param_name, param in (tool.get("params") or {}).items()
                if param.get("required", True)
            }
            assert not (required - set(args))
            used_args.update(args)

        assert not (params - used_args)
        for param_name in params:
            assert param_name in description
