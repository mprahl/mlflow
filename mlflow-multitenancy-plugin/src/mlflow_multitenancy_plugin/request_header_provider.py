import os
import json
from typing import Dict

from mlflow.tracking.request_header.abstract_request_header_provider import RequestHeaderProvider


class MultitenancyRequestHeaderProvider(RequestHeaderProvider):
    """
    Injects headers needed for multitenancy/k8s auth into all MLflow client requests.

    - Authorization: Bearer <MLFLOW_TRACKING_TOKEN> (if present)
    - X-MLflow-Namespace: <MLFLOW_NAMESPACE> (if present)
    - Merges additional headers from MLFLOW_TRACKING_DEFAULT_HEADERS (JSON dict)
    """

    def in_context(self) -> bool:
        return True

    def request_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}

        # Merge optional default headers (JSON)
        try:
            raw = os.environ.get("MLFLOW_TRACKING_DEFAULT_HEADERS")
            if raw:
                extra = json.loads(raw)
                if isinstance(extra, dict):
                    headers.update({str(k): str(v) for k, v in extra.items()})
        except Exception:
            pass

        # Namespace
        ns = os.environ.get("MLFLOW_NAMESPACE")
        if ns and "X-MLflow-Namespace" not in headers:
            headers["X-MLflow-Namespace"] = ns

        # Authorization via MLFLOW_TRACKING_TOKEN unless already supplied
        if "Authorization" not in headers:
            token = os.environ.get("MLFLOW_TRACKING_TOKEN")
            if token and not token.startswith("Bearer "):
                token = f"Bearer {token}"
            if token:
                headers["Authorization"] = token

        return headers
