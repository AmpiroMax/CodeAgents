"""Observability surface: audit / inference / runtime / service request
logs and resource metrics.

The legacy modules still live at the package root (``codeagents.audit``,
``codeagents.inference_log``, ``codeagents.runtime_log``,
``codeagents.request_log``, ``codeagents.resource_metrics``,
``codeagents.metrics_sampler``) and all four loggers funnel their JSONL
appends through :func:`._jsonl.append_line` for consistency.

This ``__init__`` is intentionally minimal — re-exporting the legacy
modules from here would create a circular load (the loggers themselves
import :mod:`._jsonl`). Reach the helper directly::

    from codeagents.observability._jsonl import append_line

The legacy modules continue to be the canonical import paths; future
work can graduate the implementations into this package without
breaking either path.
"""

# Intentionally empty. See module docstring.
