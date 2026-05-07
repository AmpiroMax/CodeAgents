"""HTTP surface: NDJSON streaming server consumed by the GUI and CLI.

* :mod:`codeagents.surfaces.http.server` — :class:`AgentRequestHandler`,
  :class:`ReusableThreadingHTTPServer`, :func:`serve` entry point.
* :mod:`codeagents.surfaces.http.router` — :class:`Route` dataclass and
  :func:`dispatch` helper used by the handler's GET routing table.
"""

from codeagents.surfaces.http.router import Route, dispatch
from codeagents.surfaces.http.server import (
    AgentRequestHandler,
    ReusableThreadingHTTPServer,
    serve,
)

__all__ = [
    "AgentRequestHandler",
    "ReusableThreadingHTTPServer",
    "Route",
    "dispatch",
    "serve",
]
