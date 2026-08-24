"""Per-tool authorization for tracking-server HTTP MCP tools.

Path-level auth is handled by FastAPI permission middleware. Tools still check
the same resource permissions REST would use, using username + ids from tool
arguments (not Flask ``request``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mlflow.exceptions import MlflowException
from mlflow.protos.databricks_pb2 import PERMISSION_DENIED, RESOURCE_DOES_NOT_EXIST, ErrorCode


def _auth_enabled() -> bool:
    try:
        from mlflow.server.auth import is_auth_enabled

        return is_auth_enabled()
    except Exception:
        return False


def current_username() -> str | None:
    try:
        from fastmcp.server.dependencies import get_http_request

        request = get_http_request()
    except Exception:
        return None
    return getattr(getattr(request, "state", None), "username", None)


def _is_admin(username: str) -> bool:
    from mlflow.server.auth import store

    return bool(store.get_user(username).is_admin)


def _deny(message: str = "Permission denied") -> None:
    raise MlflowException(message, PERMISSION_DENIED)


def _require_auth_user() -> str | None:
    if not _auth_enabled():
        return None
    username = current_username()
    if not username:
        _deny("You are not authenticated.")
    if _is_admin(username):
        return None
    return username


def _experiment_permission(experiment_id: str, username: str):
    from mlflow.server.auth import _get_experiment_permission

    return _get_experiment_permission(experiment_id, username)


def _run_experiment_id(run_id: str) -> str:
    from mlflow.server.handlers import _get_tracking_store

    return _get_tracking_store().get_run(run_id).info.experiment_id


def _trace_permission(trace_id: str, username: str):
    from mlflow.server.auth import _get_permission_from_trace

    return _get_permission_from_trace(trace_id, username)


def require_create_experiment() -> None:
    username = _require_auth_user()
    if username is None:
        return
    from mlflow.server.auth import _can_create_in_workspace

    if not _can_create_in_workspace(username):
        _deny()


def require_experiment_read(experiment_id: str) -> None:
    username = _require_auth_user()
    if username is None:
        return
    if not _experiment_permission(experiment_id, username).can_read:
        _deny()


def require_experiment_update(experiment_id: str) -> None:
    username = _require_auth_user()
    if username is None:
        return
    if not _experiment_permission(experiment_id, username).can_update:
        _deny()


def require_experiment_delete(experiment_id: str) -> None:
    username = _require_auth_user()
    if username is None:
        return
    if not _experiment_permission(experiment_id, username).can_delete:
        _deny()


def require_experiment_read_by_name(experiment_name: str) -> str:
    from mlflow.server.handlers import _get_tracking_store

    experiment = _get_tracking_store().get_experiment_by_name(experiment_name)
    if experiment is None:
        raise MlflowException(
            f"Could not find experiment with name {experiment_name}",
            RESOURCE_DOES_NOT_EXIST,
        )
    require_experiment_read(experiment.experiment_id)
    return experiment.experiment_id


def require_run_read(run_id: str) -> None:
    username = _require_auth_user()
    if username is None:
        return
    if not _experiment_permission(_run_experiment_id(run_id), username).can_read:
        _deny()


def require_run_update(run_id: str) -> None:
    username = _require_auth_user()
    if username is None:
        return
    if not _experiment_permission(_run_experiment_id(run_id), username).can_update:
        _deny()


def require_run_delete(run_id: str) -> None:
    username = _require_auth_user()
    if username is None:
        return
    if not _experiment_permission(_run_experiment_id(run_id), username).can_delete:
        _deny()


def require_trace_read(trace_id: str) -> None:
    username = _require_auth_user()
    if username is None:
        return
    if not _trace_permission(trace_id, username).can_read:
        _deny()


def require_trace_update(trace_id: str) -> None:
    username = _require_auth_user()
    if username is None:
        return
    if not _trace_permission(trace_id, username).can_update:
        _deny()


def require_experiments_read(experiment_ids: list[str]) -> None:
    if not experiment_ids:
        raise MlflowException.invalid_parameter_value("At least one experiment_id is required.")
    username = _require_auth_user()
    if username is None:
        return
    if not all(_experiment_permission(eid, username).can_read for eid in experiment_ids):
        _deny()


def require_link_traces_to_run(run_id: str, trace_ids: list[str]) -> None:
    username = _require_auth_user()
    if username is None:
        return
    from mlflow.server.auth import _get_experiment_permission
    from mlflow.server.handlers import _get_tracking_store

    store = _get_tracking_store()
    run = store.get_run(run_id)
    if not _get_experiment_permission(run.info.experiment_id, username).can_update:
        _deny()
    if not trace_ids:
        raise MlflowException.invalid_parameter_value("At least one trace_id is required.")
    try:
        trace_experiment_ids = {store.get_trace_info(tid).experiment_id for tid in trace_ids}
    except MlflowException as e:
        if e.error_code == ErrorCode.Name(RESOURCE_DOES_NOT_EXIST):
            _deny()
        raise
    if not all(_get_experiment_permission(eid, username).can_read for eid in trace_experiment_ids):
        _deny()


def filter_readable_experiments(experiments: list[Any]) -> list[Any]:
    username = _require_auth_user()
    if username is None:
        return experiments
    from mlflow.server.auth import _role_based_read_predicate

    can_read: Callable[[str], bool] = _role_based_read_predicate(username, "experiment")
    return [e for e in experiments if can_read(e.experiment_id)]


def grant_experiment_manage(experiment_id: str) -> None:
    username = current_username() if _auth_enabled() else None
    if not username:
        return
    from mlflow.server.auth import store
    from mlflow.server.auth.permissions import MANAGE

    store.grant_user_permission(username, "experiment", experiment_id, MANAGE.name)


def grant_scorer_manage(experiment_id: str, name: str) -> None:
    username = current_username() if _auth_enabled() else None
    if not username:
        return
    from mlflow.server.auth import store
    from mlflow.server.auth.permissions import MANAGE

    pattern = store._scorer_pattern(experiment_id, name)
    store.grant_user_permission(username, "scorer", pattern, MANAGE.name)
