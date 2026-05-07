"""HTTP surface package.

The route table primitives live in :mod:`codeagents.surfaces.http.router`.
The actual server (handler + ``serve``) still lives in
:mod:`codeagents.server`; importing it here would create a circular load
because ``server`` itself populates a route table built from
:class:`Route`. Stage 4 keeps the canonical entry points where they are
and only adds a routing helper alongside them.
"""

# Submodules are reached via explicit imports, e.g.:
#   from codeagents.surfaces.http.router import Route, dispatch
#   from codeagents.server import serve, AgentRequestHandler
