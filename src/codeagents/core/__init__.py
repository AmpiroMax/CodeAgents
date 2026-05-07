"""Core agent abstractions.

This package's ``__init__`` is intentionally tiny: importing it must not
pull in heavy modules (``agent.py``, ``runtime.py``, etc.) because those
modules in turn import ``core.modes``, which would create a circular
load. Subpackages (``core.modes``, ``core.modes.prompts``) and re-exports
are reachable via explicit imports — for example::

    from codeagents.core.modes import resolve_prompt
    from codeagents.core.modes import filter_for_mode

Stage-1 cosmetic re-exports of ``AgentCore`` etc. were removed for the
same reason; if you need them, import from their canonical location:

    from codeagents.agent import AgentCore
    from codeagents.permissions import Permission
    from codeagents.workspace import Workspace
"""

# Intentionally empty. See module docstring.
