"""Single source of truth for the CodeAgents build version.

Other places (``src/codeagents/__init__.py``, ``pyproject.toml``,
``gui/package.json``) re-export / mirror this value. Update them via
``./set_version <major> <minor> <patch>`` — never edit by hand.
"""

__version__ = "3.2.0"
