"""Back-compat shim. Native tools moved to :mod:`codeagents.tools` in Stage 2.

External callers (tests, scripts, integrations) that imported
``codeagents.tools_native.code`` or ``codeagents.tools_native.research``
keep working through ``sys.modules`` aliases below. New code should
import from ``codeagents.tools`` directly.
"""

from __future__ import annotations

import sys

from codeagents.tools import kg, native_code, pdf, rag, research

# ``from codeagents.tools_native.code import register_code_tools`` keeps
# working: alias the legacy submodule names to the new locations.
sys.modules[__name__ + ".code"] = native_code
sys.modules[__name__ + ".research"] = research
sys.modules[__name__ + ".kg"] = kg
sys.modules[__name__ + ".pdf"] = pdf
sys.modules[__name__ + ".rag"] = rag

from codeagents.tools.native_code import register_code_tools as _register_code_tools
from codeagents.tools.pdf import register_pdf_tools
from codeagents.tools.research import register_research_tools


def register_code_tools(registry, workspace):
    """Register code tools + PDF + research/KG tools.

    Same wiring as before: registers PDF/research unconditionally and KG
    only if its native deps import cleanly.
    """
    _register_code_tools(registry, workspace)
    try:
        register_pdf_tools(registry, workspace)
    except Exception:
        pass
    register_research_tools(registry, workspace)
    try:
        from codeagents.tools.kg import register_kg_tools

        register_kg_tools(registry, workspace)
    except Exception:
        pass


__all__ = [
    "register_code_tools",
    "register_pdf_tools",
    "register_research_tools",
    "code",
    "kg",
    "pdf",
    "rag",
    "research",
]


# Re-export module names so attribute access (``tools_native.code``) works
# alongside the ``sys.modules`` alias (which makes ``import x.code`` work).
code = native_code
