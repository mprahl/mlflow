from __future__ import annotations

from urllib.parse import urlparse

from mlflow.store.tracking.abstract_store import AbstractStore as AbstractTrackingStore
from mlflow.store.model_registry.abstract_store import AbstractStore as AbstractModelRegistryStore

from mlflow_multitenancy_plugin.server.store_wrappers import (
    NamespaceEnforcingTrackingStore,
    NamespaceEnforcingModelRegistryStore,
)


SQL_SCHEMES = {"sqlite", "postgresql", "mysql", "mssql"}


def _strip_ns_scheme(uri: str) -> str:
    if uri.startswith("ns+"):
        return uri[3:]
    if uri.startswith("ns:"):
        return uri[3:]
    return uri


def _build_tracking_base_store(inner_uri: str, artifact_uri: str | None) -> AbstractTrackingStore:
    parsed = urlparse(inner_uri)
    scheme = parsed.scheme or "file"
    if scheme in SQL_SCHEMES:
        from mlflow.store.tracking.sqlalchemy_store import SqlAlchemyStore

        return SqlAlchemyStore(inner_uri, artifact_uri)
    # Fallback to file store
    from mlflow.store.tracking.file_store import FileStore

    # For file:// URIs, artifact_uri is used as provided by MLflow
    return FileStore(inner_uri, artifact_uri)


def _build_registry_base_store(inner_uri: str) -> AbstractModelRegistryStore:
    parsed = urlparse(inner_uri)
    scheme = parsed.scheme or "file"
    if scheme in SQL_SCHEMES:
        from mlflow.store.model_registry.sqlalchemy_store import SqlAlchemyStore as MRSqlAlchemyStore

        return MRSqlAlchemyStore(inner_uri)
    # Registry over file store is not supported; default to SQLAlchemy error
    from mlflow.tracking.registry import UnsupportedModelRegistryStoreURIException

    raise UnsupportedModelRegistryStoreURIException(
        f"Unsupported model registry store URI scheme: '{scheme}'. Use a SQL database URI."
    )


def ns_tracking_store_factory(store_uri: str, artifact_uri: str | None = None) -> AbstractTrackingStore:
    inner_uri = _strip_ns_scheme(store_uri)
    base = _build_tracking_base_store(inner_uri, artifact_uri)
    return NamespaceEnforcingTrackingStore(base)


def ns_model_registry_store_factory(store_uri: str) -> AbstractModelRegistryStore:
    inner_uri = _strip_ns_scheme(store_uri)
    base = _build_registry_base_store(inner_uri)
    return NamespaceEnforcingModelRegistryStore(base)
