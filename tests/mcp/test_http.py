from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from fastmcp import Client

from mlflow.exceptions import MlflowException
from mlflow.mcp.http_server import create_http_mcp
from mlflow.mcp.http_tools import HTTP_MCP_TOOLS, create_experiment, get_experiment, list_runs
from mlflow.protos.databricks_pb2 import PERMISSION_DENIED, ErrorCode
from mlflow.store.tracking.sqlalchemy_store import SqlAlchemyStore


@pytest.fixture
def tracking_store(tmp_path, monkeypatch):
    store = SqlAlchemyStore(
        f"sqlite:///{tmp_path}/mlflow.db",
        (tmp_path / "artifacts").as_uri(),
    )
    monkeypatch.setattr("mlflow.server.handlers._get_tracking_store", lambda: store)
    return store


@pytest_asyncio.fixture
async def mcp_client():
    mcp = create_http_mcp()
    async with Client(mcp) as client:
        yield client


@pytest.mark.asyncio
async def test_http_tool_list_is_genai_only(mcp_client, monkeypatch):
    monkeypatch.setenv("MLFLOW_MCP_TOOLS", "all")
    names = {tool.name for tool in await mcp_client.list_tools()}
    assert "search_experiments" in names
    assert "list_runs" in names
    assert "search_traces" in names
    assert "list_scorers" in names
    assert "evaluate_traces" not in names
    assert "serve_model" not in names
    assert "create_deployment" not in names
    assert {fn.__name__ for fn in HTTP_MCP_TOOLS} <= names


def test_create_and_get_experiment(tracking_store):
    created = create_experiment(experiment_name="exp-a")
    fetched = get_experiment(experiment_id=created["experiment_id"])
    assert fetched["name"] == "exp-a"
    listed = list_runs(experiment_id=created["experiment_id"])
    assert listed["runs"] == []


def test_list_scorers_registered_only(tracking_store):
    from mlflow.mcp.http_tools import list_scorers

    experiment_id = tracking_store.create_experiment("scorer-exp")
    result = list_scorers(experiment_id=experiment_id)
    assert result == {"scorers": []}
    assert "builtin" not in result


def test_permission_denied_when_auth_on(tracking_store, monkeypatch):
    monkeypatch.setattr("mlflow.mcp.http_auth._auth_enabled", lambda: True)
    monkeypatch.setattr("mlflow.mcp.http_auth.current_username", lambda: "alice")
    monkeypatch.setattr("mlflow.mcp.http_auth._is_admin", lambda username: False)
    perm = MagicMock(can_read=False, can_update=False, can_delete=False)
    monkeypatch.setattr("mlflow.mcp.http_auth._experiment_permission", lambda eid, user: perm)

    experiment_id = tracking_store.create_experiment("denied")
    with pytest.raises(MlflowException, match="Permission denied") as exc:
        get_experiment(experiment_id=experiment_id)
    assert exc.value.error_code == ErrorCode.Name(PERMISSION_DENIED)


def test_auth_disabled_skips_permission_checks(tracking_store, monkeypatch):
    monkeypatch.setattr("mlflow.mcp.http_auth._auth_enabled", lambda: False)
    experiment_id = tracking_store.create_experiment("open")
    fetched = get_experiment(experiment_id=experiment_id)
    assert fetched["experiment_id"] == experiment_id
