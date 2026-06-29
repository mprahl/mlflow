from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mlflow.environment_variables import MLFLOW_USE_ICEBERG_ARCHIVAL
from mlflow.exceptions import MlflowException
from mlflow.server import handlers
from mlflow.server.fastapi_app import (
    add_iceberg_query_warmup,
    add_mcp_exception_handlers,
    create_fastapi_app,
)


@pytest.fixture
def client():
    return TestClient(create_fastapi_app())


def test_websocket_to_wsgi_mount_does_not_crash(client):
    # A WebSocket handshake routed to the catch-all WSGI mount must be rejected
    # cleanly (close code 1000) instead of raising AssertionError.
    with pytest.raises(WebSocketDisconnect) as excinfo:  # noqa: PT011
        with client.websocket_connect("/gateway/proxy/codex/v1/models"):
            pass
    assert excinfo.value.code == 1000


def test_http_to_wsgi_mount_still_served(client):
    resp = client.get("/health")
    assert resp.status_code == 200


def test_mcp_exception_handler_delegates_for_non_mcp_routes():
    app = FastAPI()

    @app.exception_handler(MlflowException)
    async def existing_mlflow_exception_handler(request, exc):
        return JSONResponse(status_code=418, content={"detail": "delegated"})

    add_mcp_exception_handlers(app)

    @app.get("/non-mcp")
    async def non_mcp():
        raise MlflowException.invalid_parameter_value("boom")

    client = TestClient(app)
    response = client.get("/non-mcp")

    assert response.status_code == 418
    assert response.json() == {"detail": "delegated"}


def test_iceberg_query_resources_are_warmed_on_startup(monkeypatch):
    warmup = mock.Mock()
    store = mock.Mock(warm_iceberg_trace_query_resources=warmup)
    monkeypatch.setattr(MLFLOW_USE_ICEBERG_ARCHIVAL, "get", lambda: True)
    monkeypatch.setattr(handlers, "_get_tracking_store", lambda: store)
    app = FastAPI()

    add_iceberg_query_warmup(app)
    app.router.on_startup[0]()

    warmup.assert_called_once_with()
