"""Route table primitives for the HTTP surface.

Stage-4 of the refactor introduces a router-table pattern: each route is
declared once as a :class:`Route` and the request handler dispatches by
walking the table instead of a ~280-line ``if/elif`` chain in ``do_GET``.

A route matches when:

* its ``method`` equals the request's HTTP method, AND
* either its ``path`` equals ``self.path`` exactly, or
* its ``prefix`` is a prefix of ``self.path`` (matches paths like
  ``/chats/<id>`` or ``/tools?query=...``).

The matched route's ``handler`` attribute name is looked up on the
request-handler instance and called with no arguments. The handler is
responsible for sending its own response; :func:`dispatch` returns
``True`` to signal that a route handled the request.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Route:
    method: str
    handler: str  # name of the bound method on the handler instance
    path: str | None = None  # exact match
    prefix: str | None = None  # prefix match (used with /chats/<id> etc.)

    def matches(self, method: str, path: str) -> bool:
        if method != self.method:
            return False
        if self.path is not None:
            # Allow exact match with optional ``?query`` suffix.
            return path == self.path or path.startswith(self.path + "?")
        if self.prefix is not None:
            return path.startswith(self.prefix)
        return False


def dispatch(handler, routes: Iterable[Route], method: str, path: str) -> bool:
    """Run the first matching route; return True iff a route handled it."""
    for route in routes:
        if route.matches(method, path):
            getattr(handler, route.handler)()
            return True
    return False


__all__ = ["Route", "dispatch"]
