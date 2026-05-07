"""Audit / inference / runtime / request logs + resource metrics.

All loggers funnel writes through :func:`._jsonl.append_line` for
consistent NDJSON output. Modules:

* :mod:`codeagents.observability.audit` — agent-decision audit log.
* :mod:`codeagents.observability.inference_log` — model inference traces.
* :mod:`codeagents.observability.runtime_log` — runtime client telemetry.
* :mod:`codeagents.observability.request_log` — service-request log.
* :mod:`codeagents.observability.metrics_sampler` — periodic resource sampler.
* :mod:`codeagents.observability.resource_metrics` — resource snapshots.
"""

from codeagents.observability._jsonl import append_line
from codeagents.observability.audit import AuditEvent, AuditLog
from codeagents.observability.inference_log import InferenceLogEntry, InferenceLogger
from codeagents.observability.metrics_sampler import MetricsSampler, get_global_sampler
from codeagents.observability.request_log import (
    ServiceRequestLogEntry,
    ServiceRequestLogger,
)
from codeagents.observability.resource_metrics import collect_resource_snapshot
from codeagents.observability.runtime_log import (
    RuntimeRequestLogEntry,
    RuntimeRequestLogger,
)

__all__ = [
    "AuditEvent",
    "AuditLog",
    "InferenceLogEntry",
    "InferenceLogger",
    "MetricsSampler",
    "RuntimeRequestLogEntry",
    "RuntimeRequestLogger",
    "ServiceRequestLogEntry",
    "ServiceRequestLogger",
    "append_line",
    "collect_resource_snapshot",
    "get_global_sampler",
]
