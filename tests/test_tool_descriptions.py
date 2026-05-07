from __future__ import annotations

import json
import re

from codeagents.tools._native_specs import NATIVE_TOOL_SPECS

EXAMPLE_RE = re.compile(r"Example:\s*([a-zA-Z0-9_\.]+)\s*(\{[^\n]*\})")


# Pack 7: some tools intentionally keep only a handful of params in their
# minimal description but still accept many advanced options at runtime
# (e.g. retry/cache knobs on web_search). For these we only require examples
# to be valid; we don't require every param to appear in the description.
_TOOLS_WITH_HIDDEN_PARAMS = {"web_search", "docs_search"}


def test_tool_descriptions_match_declared_arguments() -> None:
    """Every tool's ``Example:`` lines must use only declared params, and
    every declared param must appear in the description."""
    for spec in NATIVE_TOOL_SPECS:
        params = {p.name for p in spec.params}
        description = spec.description
        examples = EXAMPLE_RE.findall(description)
        used_args: set[str] = set()

        for example_tool, raw_args in examples:
            assert example_tool == spec.name, (
                f"Example for {spec.name!r} references {example_tool!r}"
            )
            args = json.loads(raw_args)
            assert isinstance(args, dict)
            assert not (set(args) - params), (
                f"{spec.name}: example uses undeclared params {set(args) - params}"
            )
            required = {p.name for p in spec.params if p.required}
            assert not (required - set(args)), (
                f"{spec.name}: example missing required params {required - set(args)}"
            )
            used_args.update(args)

        if spec.name in _TOOLS_WITH_HIDDEN_PARAMS:
            continue
        assert not (params - used_args), (
            f"{spec.name}: declared params {params - used_args} never appear in any Example"
        )
        for param_name in params:
            assert param_name in description, (
                f"{spec.name}: param {param_name!r} not mentioned in description"
            )
