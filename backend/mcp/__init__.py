"""MCP server — an external-surface adapter exposing Open Net Worth to MCP clients.

Like ``backend/connectors/``, this package lives OUTSIDE the layered spine: it
marshals an external protocol (MCP / JSON-RPC 2.0) into ``execute_tool`` calls
and marshals the results back. It imports ``backend.ai.tools`` + ``backend.core``
(downward/sideways at the api/ai external tier) and nothing in the spine imports
it (so ``core`` stays a leaf — see import-linter Contract 4).

A hand-rolled JSON-RPC-2.0-over-HTTP ``POST /mcp`` endpoint (NO new dependency),
Bearer-gated by the same instance API token that gates ``/api/*``, exposing the
registered tool surface plus a curated read-only resource catalog. Holding the
Bearer token IS the authorization — there is no tier/grant layer behind it; a
mutating call is checkpointed (undoable) and audited by ``adapter.call_tool``.
"""
