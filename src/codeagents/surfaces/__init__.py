"""User-facing surfaces: HTTP API, TUI, MCP server, SDK helpers.

Stage 1 re-exports the existing entry points. Stage 4 distributes the
HTTP server's monolithic handler across ``surfaces/http/``.
"""
