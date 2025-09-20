from __future__ import annotations

from flask import request, jsonify, g
import os
import re

from mlflow_multitenancy_plugin.server.ssar import (
    authorize_request,
    resolve_request_user,
)


def _validate_namespace(namespace: str) -> bool:
    """Validate namespace follows Kubernetes naming conventions."""
    if not namespace or len(namespace) > 253:
        return False
    # Kubernetes namespace pattern: lowercase alphanumeric with hyphens
    pattern = r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"
    return bool(re.match(pattern, namespace))


class RequestAnalyzer:
    """Analyzes Flask requests to determine MLflow resource type and RBAC verb."""

    def __init__(self, path: str, method: str):
        self.path = path.lower()
        self.method = method.upper()

    def get_resource_type(self) -> str:
        """Determine the MLflow resource type from request path."""
        if any(x in self.path for x in ["/registered-models", "/model-versions"]):
            return "models"
        elif any(x in self.path for x in ["/prompts", "/prompt-versions"]):
            return "prompts"
        else:
            return "experiments"

    def get_verb(self) -> str:
        """Determine the RBAC verb from request method and path."""
        if self.method == "GET":
            return "list" if "search" in self.path else "get"
        elif self.method in ("POST", "PUT", "PATCH", "DELETE"):
            if "delete" in self.path or self.method == "DELETE":
                return "delete"
            elif "create" in self.path:
                return "create"
            elif "update" in self.path or self.method in ("PUT", "PATCH"):
                return "update"
            else:
                return "create"
        else:
            return "get"


def install(app):
    @app.before_request
    def _authorize_and_scope_namespace():
        path = request.path or ""
        # Exempt non-API/static routes
        if (
            path == "/"
            or path.startswith("/static/")
            or path.startswith("/static-files/")
            or path.startswith("/js/")
            or path == "/favicon.ico"
            or path.startswith("/.well-known/")
            or path == "/ajax-api/2.0/mlflow/namespaces"
            or path in ("/health", "/version")
        ):
            return None

        lower = path.lower()
        method = request.method.upper()
        namespace = request.headers.get("X-MLflow-Namespace")

        if not namespace:
            return (
                jsonify(
                    {
                        "error_code": "INVALID_PARAMETER_VALUE",
                        "message": "Missing X-MLflow-Namespace header while multitenancy is enabled.",
                    }
                ),
                400,
            )

        if not _validate_namespace(namespace):
            return (
                jsonify(
                    {
                        "error_code": "INVALID_PARAMETER_VALUE",
                        "message": "Invalid namespace format. Must follow Kubernetes naming conventions.",
                    }
                ),
                400,
            )

        g.mlflow_namespace = namespace
        # Resolve user only for endpoints that require it (create run, log-batch)
        if method == "POST" and ("/runs/create" in lower or "/runs/log-batch" in lower):
            try:
                g.mlflow_user = resolve_request_user(request)
            except Exception:
                g.mlflow_user = None
        else:
            # Skip setting since we don't need it for other endpoints
            g.mlflow_user = None

        # Analyze request to determine resource type and verb
        analyzer = RequestAnalyzer(path, method)
        resource = analyzer.get_resource_type()
        verb = analyzer.get_verb()

        try:
            authorize_request(request, namespace, resource, verb)
        except Exception as e:
            msg = str(e)
            if "Missing bearer token" in msg:
                return jsonify({"error_code": "UNAUTHENTICATED", "message": msg}), 401
            return jsonify({"error_code": "PERMISSION_DENIED", "message": msg}), 403

    # Namespaces API route implemented in plugin
    from mlflow_multitenancy_plugin.server.ssar import (
        list_all_namespaces_with_service_account,
        filter_accessible_namespaces,
    )

    @app.route("/ajax-api/2.0/mlflow/namespaces", methods=["GET"])
    def serve_list_namespaces():
        # Enumerate all namespaces using SA token, then filter via user token SSAR
        candidates = list_all_namespaces_with_service_account()
        header_ns = request.headers.get("X-MLflow-Namespace")
        if header_ns:
            candidates = list(set((candidates or []) + [header_ns]))
        # Environment-provided candidate list (comma-separated) for clusters where SA cannot list
        if not candidates:
            extra_env = (os.getenv("MLFLOW_K8S_NAMESPACE_CANDIDATES") or "").strip()
            if extra_env:
                env_candidates = [c.strip() for c in extra_env.split(",") if c.strip()]
                candidates = list(set((candidates or []) + env_candidates))
        names = filter_accessible_namespaces(request, candidates)
        if not names:
            return (
                jsonify(
                    {
                        "error_code": "PERMISSION_DENIED",
                        "message": "No accessible namespaces for the provided token.",
                    }
                ),
                403,
            )
        return {"namespaces": names}
