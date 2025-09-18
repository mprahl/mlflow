from __future__ import annotations

import json
import os
from typing import Optional, List

try:
    from kubernetes import client as k8s_client, config as k8s_config  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    k8s_client = None  # type: ignore
    k8s_config = None  # type: ignore
from flask import Request

# These env lookups mirror server behavior
MLFLOW_K8S_API = os.environ.get("MLFLOW_K8S_API")
MLFLOW_K8S_CA_FILE = os.environ.get("MLFLOW_K8S_CA_FILE")
MLFLOW_K8S_INSECURE_SKIP_TLS_VERIFY = os.environ.get(
    "MLFLOW_K8S_INSECURE_SKIP_TLS_VERIFY"
) in ("1", "true", "True")


def _raise_perm_denied(message: str):
    from mlflow.exceptions import MlflowException
    from mlflow.protos.databricks_pb2 import PERMISSION_DENIED

    raise MlflowException(message, error_code=PERMISSION_DENIED)


def _get_k8s_api_server() -> str:
    if MLFLOW_K8S_API:
        return MLFLOW_K8S_API.rstrip("/")
    if os.getenv("KUBERNETES_SERVICE_HOST"):
        return "https://kubernetes.default.svc"
    # Kubeconfig fallback
    for path in [
        *(
            os.getenv("KUBECONFIG", "").split(os.pathsep)
            if os.getenv("KUBECONFIG")
            else []
        ),
        os.path.join(os.path.expanduser("~"), ".kube", "config"),
    ]:
        try:
            import yaml  # type: ignore

            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                ctx = cfg.get("current-context")
                if not ctx:
                    continue
                contexts = {
                    c.get("name"): c.get("context", {})
                    for c in (cfg.get("contexts") or [])
                }
                cluster_name = (contexts.get(ctx) or {}).get("cluster")
                clusters = {
                    c.get("name"): c.get("cluster", {})
                    for c in (cfg.get("clusters") or [])
                }
                server = (clusters.get(cluster_name) or {}).get("server")
                if isinstance(server, str) and server:
                    return server.rstrip("/")
        except Exception:
            continue
    return "https://kubernetes.default.svc"


def _get_verify() -> bool | str:
    if MLFLOW_K8S_INSECURE_SKIP_TLS_VERIFY:
        return False
    if MLFLOW_K8S_CA_FILE:
        return MLFLOW_K8S_CA_FILE
    in_cluster_ca = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    if os.path.isfile(in_cluster_ca):
        return in_cluster_ca
    return True


def _extract_bearer_token(flask_request: Request) -> Optional[str]:
    auth = flask_request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth.split(" ", 1)[1].strip()
    # Accept token forwarded by oauth-proxy via X-Forwarded-Access-Token or user token variant
    xf_token = flask_request.headers.get(
        "X-Forwarded-Access-Token"
    ) or flask_request.headers.get("X-Forwarded-Access-Token".lower())
    if isinstance(xf_token, str) and xf_token.strip():
        return xf_token.strip()

    try:
        cookie_token = flask_request.cookies.get("mlflow.k8s.bearerToken")
        if cookie_token:
            return cookie_token.strip()
    except Exception:
        pass
    return None


# Export the same API functions used by server


def authorize_request(
    flask_request: Request, namespace: str, resource: str, verb: str
) -> None:
    """
    Authorize a request using Kubernetes SSAR (SelfSubjectAccessReview).

    Args:
        flask_request: Flask request object containing auth headers
        namespace: Target Kubernetes namespace
        resource: Resource type (experiments, models, prompts)
        verb: Action verb (get, list, create, update, delete)

    Raises:
        MlflowException: If authorization fails or token is invalid
    """
    token = _extract_bearer_token(flask_request)
    if not token:
        _raise_perm_denied("Missing bearer token for authorization.")

    if not (k8s_client and k8s_config):
        _raise_perm_denied("Kubernetes client is not available on the server.")

    api_client = _build_api_client_with_token(token)
    if not api_client:
        _raise_perm_denied(
            "Failed to construct Kubernetes API client with provided token."
        )

    auth_api = k8s_client.AuthorizationV1Api(api_client)
    review = k8s_client.V1SelfSubjectAccessReview(
        spec=k8s_client.V1SelfSubjectAccessReviewSpec(
            resource_attributes=k8s_client.V1ResourceAttributes(
                group="community.mlflow.org",
                resource=resource,
                verb=verb,
                namespace=namespace,
            )
        )
    )
    try:
        resp = auth_api.create_self_subject_access_review(review, _request_timeout=10)
        if not bool(getattr(resp.status, "allowed", False)):
            reason = getattr(resp.status, "reason", "Access denied")
            _raise_perm_denied(f"Access denied by SSAR: {reason}")
        return
    except Exception as e:
        # Handle Kubernetes API exceptions specifically
        if hasattr(e, "status"):
            if e.status == 401:
                _raise_perm_denied("Invalid or expired token")
            elif e.status == 403:
                _raise_perm_denied("Insufficient permissions for authorization check")
            else:
                _raise_perm_denied(f"Authorization service error: {e.status}")
        else:
            _raise_perm_denied(f"Authorization check failed: {type(e).__name__}")


def _build_api_client_with_token(token: str):
    if not k8s_client:
        return None
    try:
        cfg = k8s_client.Configuration()
        cfg.host = _get_k8s_api_server()
        verify = _get_verify()
        if verify is False:
            cfg.verify_ssl = False
        elif isinstance(verify, str):
            cfg.ssl_ca_cert = verify
        cfg.api_key = {"authorization": token}
        cfg.api_key_prefix = {"authorization": "Bearer"}
        return k8s_client.ApiClient(cfg)
    except Exception:
        return None


def _get_service_account_api_client() -> Optional[object]:
    if not (k8s_config and k8s_client):
        return None
    # Prefer in-cluster, then fall back to kubeconfig
    try:
        k8s_config.load_incluster_config()
        return k8s_client.ApiClient()
    except Exception:
        try:
            k8s_config.load_kube_config()
            return k8s_client.ApiClient()
        except Exception:
            return None


def list_all_namespaces_with_service_account() -> List[str]:
    """
    List all Kubernetes namespaces using service account credentials.

    Returns:
        List of namespace names, empty if unable to connect or list
    """
    if not (k8s_client and k8s_config):
        return []
    api_client = _get_service_account_api_client()
    if not api_client:
        return []
    try:
        v1 = k8s_client.CoreV1Api(api_client)
        items = v1.list_namespace(_preload_content=False, _request_timeout=5)
        data = json.loads(items.data) if hasattr(items, "data") else items.to_dict()
        out = []
        for item in data.get("items") or []:
            md = item.get("metadata") or {}
            name = md.get("name")
            if isinstance(name, str):
                out.append(name)
        return sorted(set(out), key=lambda n: n.lower())
    except Exception:
        return []


def _ssar_allows_mlflow_api(token: str, namespace: str) -> bool:
    if not (k8s_client and k8s_config):
        return False
    api_client = _build_api_client_with_token(token)
    if not api_client:
        return False
    try:
        auth_api = k8s_client.AuthorizationV1Api(api_client)
        for resource in ("experiments", "models", "prompts"):
            review = k8s_client.V1SelfSubjectAccessReview(
                spec=k8s_client.V1SelfSubjectAccessReviewSpec(
                    resource_attributes=k8s_client.V1ResourceAttributes(
                        group="community.mlflow.org",
                        resource=resource,
                        verb="list",
                        namespace=namespace,
                    )
                )
            )
            resp = auth_api.create_self_subject_access_review(
                review, _request_timeout=5
            )
            if bool(getattr(resp.status, "allowed", False)):
                return True
    except Exception:
        return False
    return False


def filter_accessible_namespaces(
    flask_request: Request, candidates: List[str]
) -> List[str]:
    """
    Filter namespace candidates to only those accessible by the user's token.

    Args:
        flask_request: Flask request containing user's bearer token
        candidates: List of namespace names to check

    Returns:
        List of accessible namespace names
    """
    token = _extract_bearer_token(flask_request)
    if not token:
        return []
    allowed = [ns for ns in set(candidates or []) if _ssar_allows_mlflow_api(token, ns)]
    return sorted(allowed, key=lambda n: n.lower())
