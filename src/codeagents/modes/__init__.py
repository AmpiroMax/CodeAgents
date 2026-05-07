"""Back-compat shim. The mode registry now lives in
:mod:`codeagents.core.modes`. New code should import from there.
"""

from codeagents.core.modes import (
    MODE_REGISTRY,
    ModeSpec,
    allowed_permissions_for,
    filter_for_mode,
    list_modes,
    resolve_prompt,
    whitelist_for,
)
from codeagents.core.modes.prompts import reload_prompts as reload_system_prompts
from codeagents.system_prompts import system_prompt_addendum

__all__ = [
    "MODE_REGISTRY",
    "ModeSpec",
    "allowed_permissions_for",
    "filter_for_mode",
    "list_modes",
    "reload_system_prompts",
    "resolve_prompt",
    "system_prompt_addendum",
    "whitelist_for",
]
