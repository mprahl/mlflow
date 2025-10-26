from __future__ import annotations

import json
from urllib.parse import quote

from mlflow.entities import Workspace
from mlflow.store.workspace.abstract_store import AbstractStore
from mlflow.utils.rest_utils import http_request, verify_rest_response


class RestWorkspaceStore(AbstractStore):
    """REST-backed workspace store implementation."""

    def __init__(self, get_host_creds):
        self.get_host_creds = get_host_creds

    def list_workspaces(self, request) -> list[Workspace]:
        endpoint = "/api/2.0/mlflow/workspaces"
        response = http_request(
            host_creds=self.get_host_creds(),
            endpoint=endpoint,
            method="GET",
        )
        response = verify_rest_response(response, endpoint)
        payload = json.loads(response.text) if response.text else {}
        return [Workspace(**ws) for ws in payload.get("workspaces", [])]

    def get_workspace(self, workspace_name: str, request) -> Workspace:
        endpoint = f"/api/2.0/mlflow/workspaces/{quote(workspace_name, safe='')}"
        response = http_request(
            host_creds=self.get_host_creds(),
            endpoint=endpoint,
            method="GET",
        )
        response = verify_rest_response(response, endpoint)
        data = json.loads(response.text) if response.text else {}
        return Workspace(**data)

    def create_workspace(self, workspace: Workspace, request) -> Workspace:
        endpoint = "/api/2.0/mlflow/workspaces"
        payload = {"name": workspace.name}
        if workspace.description is not None:
            payload["description"] = workspace.description
        response = http_request(
            host_creds=self.get_host_creds(),
            endpoint=endpoint,
            method="POST",
            json=payload,
        )
        if response.status_code == 201:
            data = json.loads(response.text)
            return Workspace(**data)

        verify_rest_response(response, endpoint)

    def update_workspace(self, workspace: Workspace, request) -> Workspace:
        endpoint = f"/api/2.0/mlflow/workspaces/{quote(workspace.name, safe='')}"
        payload = {}
        if workspace.description is not None:
            payload["description"] = workspace.description
        response = http_request(
            host_creds=self.get_host_creds(),
            endpoint=endpoint,
            method="PATCH",
            json=payload,
        )
        response = verify_rest_response(response, endpoint)
        data = json.loads(response.text) if response.text else {}
        return Workspace(**data)

    def delete_workspace(self, workspace_name: str, request) -> None:
        endpoint = f"/api/2.0/mlflow/workspaces/{quote(workspace_name, safe='')}"
        response = http_request(
            host_creds=self.get_host_creds(),
            endpoint=endpoint,
            method="DELETE",
        )
        if response.status_code == 204:
            return
        verify_rest_response(response, endpoint)

    def get_default_workspace(self, request) -> Workspace:
        raise NotImplementedError("REST workspace provider does not expose a default workspace API")
