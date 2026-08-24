"""FastMCP Streamable HTTP app for the tracking server.

This module is named ``http_server`` rather than ``http`` so running
``python mlflow/mcp/server.py`` does not shadow the stdlib ``http`` package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mlflow.exceptions import MlflowException

if TYPE_CHECKING:
    from fastmcp import FastMCP


MCP_MISSING_EXTRA_MESSAGE = (
    "MCP HTTP is enabled (MLFLOW_SERVER_ENABLE_MCP / --enable-mcp) but the "
    "'mcp' extra is not installed. Install it with: pip install 'mlflow[mcp]'"
)


def require_fastmcp() -> None:
    try:
        import fastmcp  # noqa: F401
    except ImportError as e:
        raise MlflowException(MCP_MISSING_EXTRA_MESSAGE) from e


def create_http_mcp() -> FastMCP:
    """Build the tracking-server MCP tool set (genai allowlist only)."""
    require_fastmcp()
    from fastmcp import FastMCP

    from mlflow.mcp.http_tools import HTTP_MCP_TOOLS

    return FastMCP(
        name="MLflow Tracking MCP",
        tools=HTTP_MCP_TOOLS,
    )


def create_mcp_http_asgi_app() -> Any:
    """Starlette Streamable HTTP app to mount at ``/mcp`` (inner path is ``/``)."""
    mcp = create_http_mcp()
    mcp_asgi = mcp.http_app(
        path="/",
        transport="http",
        stateless_http=True,
        host_origin_protection=False,
    )
    return _EmptyPathToRoot(mcp_asgi)


class _EmptyPathToRoot:
    """Starlette ``Mount("/mcp")`` leaves path ``""`` for ``POST /mcp``; FastMCP routes ``/``."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.lifespan = app.lifespan

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path = scope.get("path") or ""
            if path in ("", "/mcp"):
                scope = {**scope, "path": "/"}
        await self.app(scope, receive, send)
