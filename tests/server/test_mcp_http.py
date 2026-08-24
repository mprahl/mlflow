import pytest
from starlette.testclient import TestClient

from mlflow.exceptions import MlflowException
from mlflow.server.fastapi_app import create_fastapi_app
from mlflow.server.handlers import STATIC_PREFIX_ENV_VAR


def _mount_paths(app):
    return [getattr(route, "path", None) for route in app.routes]


def test_mcp_http_not_mounted_by_default():
    app = create_fastapi_app()
    assert "/mcp" not in _mount_paths(app)


def test_mcp_http_mounted_when_enabled(monkeypatch):
    monkeypatch.setenv("MLFLOW_SERVER_ENABLE_MCP", "true")
    monkeypatch.setenv("MLFLOW_SERVER_DISABLE_SECURITY_MIDDLEWARE", "true")
    app = create_fastapi_app()
    assert "/mcp" in _mount_paths(app)
    assert app.router.lifespan_context is not None

    with TestClient(app) as client:
        response = client.post("/mcp", headers={"Content-Type": "application/json"})
        assert response.status_code != 404


def test_mcp_http_missing_extra_fails_startup(monkeypatch):
    monkeypatch.setenv("MLFLOW_SERVER_ENABLE_MCP", "true")

    def _missing():
        raise MlflowException("the 'mcp' extra is not installed")

    monkeypatch.setattr("mlflow.mcp.http_server.require_fastmcp", _missing)
    with pytest.raises(MlflowException, match="mcp' extra is not installed"):
        create_fastapi_app()


def test_mcp_http_mount_not_under_static_prefix(monkeypatch):
    monkeypatch.setenv("MLFLOW_SERVER_ENABLE_MCP", "true")
    monkeypatch.setenv(STATIC_PREFIX_ENV_VAR, "/myprefix")
    monkeypatch.setenv("MLFLOW_SERVER_DISABLE_SECURITY_MIDDLEWARE", "true")
    app = create_fastapi_app()
    assert "/mcp" in _mount_paths(app)
    with TestClient(app) as client:
        assert client.post("/mcp", headers={"Content-Type": "application/json"}).status_code != 404
        assert client.post("/myprefix/mcp").status_code == 404
