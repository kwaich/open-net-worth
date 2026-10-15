"""``GET /api/mcp/connect-info`` — the launch command Settings → MCP puts in a stdio config.

Authenticated by the app-wide gate like every route; it carries no secret (the token is read
separately from ``/api/auth/api-token``).
"""

from fastapi import APIRouter

from backend.services.mcp_connect import connect_info

router = APIRouter()


@router.get("/api/mcp/connect-info")
def get_connect_info():
    return connect_info()
