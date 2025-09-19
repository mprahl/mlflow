from __future__ import annotations

from typing import Iterable, Optional, List, Dict, Any

from flask import g

from mlflow.entities import Experiment, ViewType
from mlflow.store.tracking import (
    SEARCH_MAX_RESULTS_DEFAULT,
    SEARCH_TRACES_DEFAULT_MAX_RESULTS,
)
from mlflow.store.tracking.abstract_store import AbstractStore as AbstractTrackingStore
from mlflow.store.entities.paged_list import PagedList
from mlflow.entities.model_registry import RegisteredModel, ModelVersion
from mlflow.store.model_registry.abstract_store import (
    AbstractStore as AbstractModelRegistryStore,
)


NAMESPACE_TAG_KEY = "mlflow.namespace"


def _require_namespace() -> str:
    ns = getattr(g, "mlflow_namespace", None)
    if not ns:
        # Should not occur when multitenancy is enabled and middleware is active
        raise RuntimeError("Namespace not found in request context")
    return ns


class NamespaceNameTransformer:
    """Handles transformation between user-visible and internal namespaced names."""

    def __init__(self, namespace: str):
        self.namespace = namespace
        self._prefix = f"{namespace}::"

    def to_internal(self, user_name: str) -> str:
        """Convert user-visible name to internal namespaced name."""
        if user_name.startswith(self._prefix):
            return user_name
        return self._prefix + user_name

    def from_internal(self, internal_name: str) -> str:
        """Convert internal namespaced name to user-visible name."""
        if internal_name.startswith(self._prefix):
            return internal_name[len(self._prefix) :]
        return internal_name

    def is_in_namespace(self, internal_name: str) -> bool:
        """Check if internal name belongs to this namespace."""
        return internal_name.startswith(self._prefix)


def _get_name_transformer() -> NamespaceNameTransformer:
    """Get a name transformer for the current namespace."""
    return NamespaceNameTransformer(_require_namespace())


def _ns_prefix() -> str:
    return f"{_require_namespace()}::"


def _to_internal_name(user_visible_name: str) -> str:
    return _get_name_transformer().to_internal(user_visible_name)


def _from_internal_name(internal_name: str) -> str:
    return _get_name_transformer().from_internal(internal_name)


def _ensure_top_level_has_namespace(tags: List[Any]) -> List[Any]:
    """
    Ensure a namespace tag is present on top-level resources.

    Args:
        tags: List of tag objects (ExperimentTag, RegisteredModelTag, etc.)

    Returns:
        List of tags with namespace tag added if not present
    """
    ns = _require_namespace()
    has_key = any(getattr(t, "key", None) == NAMESPACE_TAG_KEY for t in (tags or []))
    if not has_key:
        from mlflow.entities import ExperimentTag

        # Tags can be ExperimentTag or RegisteredModelTag depending on entity; both have same shape
        TagClass = (
            ExperimentTag
            if any(isinstance(t, ExperimentTag) for t in (tags or []))
            else None
        )
        # heuristic: if not ExperimentTag detected, just create a generic tag and rely on store to convert
        if TagClass is None:
            try:
                from mlflow.entities.model_registry import RegisteredModelTag as RMTag

                TagClass = RMTag
            except Exception:
                TagClass = ExperimentTag
        tags = list(tags or []) + [TagClass(NAMESPACE_TAG_KEY, ns)]
    return tags


def _tags_to_dict(tags: Any) -> Dict[str, Any]:
    """
    Convert various tag formats to a dictionary.

    Args:
        tags: Tags in various formats (list, dict, objects with key/value attrs)

    Returns:
        Dictionary mapping tag keys to values
    """
    if isinstance(tags, dict):
        return tags
    result = {}
    try:
        for t in tags or []:
            key = getattr(t, "key", None)
            val = getattr(t, "value", None)
            if key is None and isinstance(t, (list, tuple)) and len(t) == 2:
                key, val = t
            if key is not None:
                result[key] = val
    except Exception:
        pass
    return result


def _coalesce(value: Any, fallback: Any) -> Any:
    """Return value if not None, otherwise return fallback."""
    return value if value is not None else fallback


def _build_namespace_filter(ns: str) -> str:
    """Build a safe namespace filter string, escaping single quotes."""
    # Escape single quotes in namespace to prevent SQL injection
    escaped_ns = ns.replace("'", "''")
    return f"tags.`{NAMESPACE_TAG_KEY}` = '{escaped_ns}'"


class NamespaceEnforcingTrackingStore(AbstractTrackingStore):
    def __init__(self, base: AbstractTrackingStore):
        super().__init__()
        self._base = base

    # Experiments
    def create_experiment(self, name, artifact_location, tags):
        tags = _ensure_top_level_has_namespace(tags)
        return self._base.create_experiment(name, artifact_location, tags)

    def get_experiment(self, experiment_id):
        exp: Experiment = self._base.get_experiment(experiment_id)
        ns = _require_namespace()
        tag_map = _tags_to_dict(getattr(exp, "tags", None)) if exp else {}
        if exp and tag_map.get(NAMESPACE_TAG_KEY) != ns:
            from mlflow.exceptions import MlflowException
            from mlflow.protos.databricks_pb2 import PERMISSION_DENIED

            raise MlflowException(
                "Experiment not in namespace", error_code=PERMISSION_DENIED
            )
        return exp

    def get_experiment_by_name(self, name):
        exp: Experiment = getattr(self._base, "get_experiment_by_name")(name)
        if not exp:
            return exp
        ns = _require_namespace()
        tag_map = _tags_to_dict(getattr(exp, "tags", None)) if exp else {}
        if tag_map.get(NAMESPACE_TAG_KEY) != ns:
            # Hide experiments from other namespaces
            return None
        return exp

    def create_run(self, experiment_id, user_id, start_time, tags, run_name):
        # Ensure the parent experiment belongs to the current namespace before creating the run
        self.get_experiment(experiment_id)
        return self._base.create_run(
            experiment_id=experiment_id,
            user_id=user_id,
            start_time=start_time,
            tags=tags,
            run_name=run_name,
        )

    def delete_experiment(self, experiment_id):
        # Ensure experiment belongs to the current namespace
        self.get_experiment(experiment_id)
        return self._base.delete_experiment(experiment_id)

    def restore_experiment(self, experiment_id):
        self.get_experiment(experiment_id)
        return self._base.restore_experiment(experiment_id)

    def rename_experiment(self, experiment_id, new_name):
        self.get_experiment(experiment_id)
        return self._base.rename_experiment(experiment_id, new_name)

    def _assert_run_in_namespace(self, run_id):
        run = self._base.get_run(run_id)
        # Will raise if experiment is not in active namespace
        self.get_experiment(run.info.experiment_id)
        return run

    def get_run(self, run_id):
        run = self._base.get_run(run_id)
        self.get_experiment(run.info.experiment_id)
        return run

    def delete_run(self, run_id):
        self._assert_run_in_namespace(run_id)
        return self._base.delete_run(run_id)

    def restore_run(self, run_id):
        self._assert_run_in_namespace(run_id)
        return self._base.restore_run(run_id)

    def update_run_info(self, run_id, status, end_time, run_name=None):
        self._assert_run_in_namespace(run_id)
        return self._base.update_run_info(run_id, status, end_time, run_name)

    def set_tag(self, run_id, tag):
        self._assert_run_in_namespace(run_id)
        return self._base.set_tag(run_id, tag)

    def delete_tag(self, run_id, key):
        self._assert_run_in_namespace(run_id)
        return self._base.delete_tag(run_id, key)

    def log_batch(self, run_id, metrics, params, tags):
        self._assert_run_in_namespace(run_id)
        return self._base.log_batch(run_id, metrics, params, tags)

    def log_param(self, run_id, param):
        self._assert_run_in_namespace(run_id)
        return self._base.log_param(run_id, param)

    def log_metric(self, run_id, metric):
        self._assert_run_in_namespace(run_id)
        return self._base.log_metric(run_id, metric)

    def log_inputs(self, run_id: str, datasets=None, models=None):
        # Ensure run is in namespace before logging inputs
        self._assert_run_in_namespace(run_id)
        base_method = getattr(self._base, "log_inputs", None)
        if callable(base_method):
            return base_method(run_id=run_id, datasets=datasets, models=models)
        return None

    def get_metric_history(self, run_id, metric_key, max_results=None, page_token=None):
        # Ensure run is in namespace, then delegate to the base store
        self._assert_run_in_namespace(run_id)
        return self._base.get_metric_history(
            run_id, metric_key, max_results=max_results, page_token=page_token
        )

    def search_experiments(
        self,
        view_type: ViewType = ViewType.ACTIVE_ONLY,
        max_results: int = SEARCH_MAX_RESULTS_DEFAULT,
        filter_string: str | None = None,
        order_by: list[str] | None = None,
        page_token: str | None = None,
    ):
        ns = _require_namespace()
        # Coalesce potential None/empty values passed through
        view_type = _coalesce(view_type, ViewType.ACTIVE_ONLY)
        max_results = _coalesce(max_results, SEARCH_MAX_RESULTS_DEFAULT)
        filter_ns = _build_namespace_filter(ns)
        filter_string = (
            f"{filter_string} AND {filter_ns}" if filter_string else filter_ns
        )
        return self._base.search_experiments(
            view_type, max_results, filter_string, order_by, page_token
        )

    # Runs: rely on parent experiment scoping; no tag injection
    def _search_runs(
        self,
        experiment_ids,
        filter_string,
        run_view_type,
        max_results,
        order_by,
        page_token,
    ):
        # Filter experiments to only those in current namespace
        _require_namespace()
        allowed = [e.experiment_id for e in self.search_experiments()]
        filtered_ids = [eid for eid in (experiment_ids or []) if eid in allowed]
        # Coalesce defaults expected by base when handler passes None/empty
        max_results = _coalesce(max_results, SEARCH_MAX_RESULTS_DEFAULT)
        if not filtered_ids:
            # Gracefully return empty when no experiments are visible in this namespace
            return [], None
        return self._base._search_runs(
            filtered_ids,
            filter_string,
            run_view_type,
            max_results,
            order_by,
            page_token,
        )

    # Logged models helpers and API
    def _assert_logged_model_in_namespace(self, model_id: str):
        lm = self._base.get_logged_model(model_id)
        # Ensure the owning experiment belongs to the active namespace
        self.get_experiment(lm.experiment_id)
        return lm

    def create_logged_model(
        self,
        experiment_id: str,
        name: str | None = None,
        source_run_id: str | None = None,
        tags: list | None = None,
        params: list | None = None,
        model_type: str | None = None,
    ):
        # Ensure experiment is in namespace before creation
        self.get_experiment(experiment_id)
        return self._base.create_logged_model(
            experiment_id=experiment_id,
            name=name,
            source_run_id=source_run_id,
            tags=tags,
            params=params,
            model_type=model_type,
        )

    def get_logged_model(self, model_id: str):
        lm = self._base.get_logged_model(model_id)
        # Validate namespace via the owning experiment
        self.get_experiment(lm.experiment_id)
        return lm

    def delete_logged_model(self, model_id: str) -> None:
        self._assert_logged_model_in_namespace(model_id)
        return self._base.delete_logged_model(model_id)

    def set_logged_model_tags(self, model_id: str, tags: list) -> None:
        self._assert_logged_model_in_namespace(model_id)
        return self._base.set_logged_model_tags(model_id, tags)

    def delete_logged_model_tag(self, model_id: str, key: str) -> None:
        self._assert_logged_model_in_namespace(model_id)
        return self._base.delete_logged_model_tag(model_id, key)

    def finalize_logged_model(self, model_id: str, status):
        self._assert_logged_model_in_namespace(model_id)
        return self._base.finalize_logged_model(model_id, status)

    def record_logged_model(self, run_id, mlflow_model):
        # Ensure run is in namespace
        self._assert_run_in_namespace(run_id)
        return self._base.record_logged_model(run_id, mlflow_model)

    def log_outputs(self, run_id: str, models: list):
        # Ensure run is in namespace
        self._assert_run_in_namespace(run_id)
        # Validate each referenced logged model belongs to the namespace
        for m in models or []:
            try:
                model_id = getattr(m, "model_id", None)
                if model_id:
                    self._assert_logged_model_in_namespace(model_id)
            except Exception:
                # Best effort validation; delegate to base for strict checks
                pass
        return self._base.log_outputs(run_id=run_id, models=models)

    # Delegate other methods to base
    def __getattr__(self, item):
        return getattr(self._base, item)

    def set_experiment_tag(self, experiment_id, tag):
        # Ensure the experiment belongs to the current namespace before mutating
        exp = self.get_experiment(experiment_id)
        if not exp:
            return self._base.set_experiment_tag(experiment_id, tag)
        return self._base.set_experiment_tag(experiment_id, tag)

    def delete_experiment_tag(self, experiment_id, key):
        exp = self.get_experiment(experiment_id)
        if not exp:
            return self._base.delete_experiment_tag(experiment_id, key)
        return self._base.delete_experiment_tag(experiment_id, key)

    # Traces API: provide a safe namespaced implementation if base supports it, otherwise
    # return an empty result to avoid 500s where traces are not implemented.
    def search_traces(
        self,
        experiment_ids: list[str],
        filter_string: str | None = None,
        max_results: int = SEARCH_TRACES_DEFAULT_MAX_RESULTS,
        order_by: list[str] | None = None,
        page_token: str | None = None,
        model_id: str | None = None,
        sql_warehouse_id: str | None = None,
    ):
        # Scope experiments to current namespace
        _require_namespace()
        allowed = [e.experiment_id for e in self.search_experiments()]
        filtered_ids = [eid for eid in (experiment_ids or []) if eid in allowed]
        # Coalesce defaults
        max_results = _coalesce(max_results, SEARCH_TRACES_DEFAULT_MAX_RESULTS)
        # If base has an implementation, delegate to it
        base_method = getattr(self._base, "search_traces", None)
        if callable(base_method):
            try:
                return base_method(
                    filtered_ids,
                    filter_string,
                    max_results,
                    order_by,
                    page_token,
                    model_id,
                    sql_warehouse_id,
                )
            except NotImplementedError:
                return [], None
        return [], None

    def set_trace_tag(self, trace_id: str, key: str, value: str):
        base_method = getattr(self._base, "set_trace_tag", None)
        if callable(base_method):
            try:
                return base_method(trace_id, key, value)
            except NotImplementedError:
                return None
        return None

    def delete_trace_tag(self, trace_id: str, key: str):
        base_method = getattr(self._base, "delete_trace_tag", None)
        if callable(base_method):
            try:
                return base_method(trace_id, key)
            except NotImplementedError:
                return None
        return None

    # Additional traces/observability APIs
    def start_trace(self, trace_info):
        base_method = getattr(self._base, "start_trace", None)
        if callable(base_method):
            try:
                return base_method(trace_info)
            except NotImplementedError:
                return None
        return None

    def log_spans(self, experiment_id: str, spans):
        # Ensure experiment is in namespace
        self.get_experiment(experiment_id)
        base_method = getattr(self._base, "log_spans", None)
        if callable(base_method):
            try:
                return base_method(experiment_id, spans)
            except NotImplementedError:
                return []
        return []

    def log_spans_async(self, experiment_id: str, spans):
        # Ensure experiment is in namespace
        self.get_experiment(experiment_id)
        base_method = getattr(self._base, "log_spans_async", None)
        if callable(base_method):
            try:
                return base_method(experiment_id, spans)
            except NotImplementedError:
                return []
        return []

    def link_traces_to_run(self, trace_ids: list[str], run_id: str) -> None:
        self._assert_run_in_namespace(run_id)
        base_method = getattr(self._base, "link_traces_to_run", None)
        if callable(base_method):
            try:
                return base_method(trace_ids, run_id)
            except NotImplementedError:
                return None
        return None

    def get_trace_info(self, trace_id: str):
        base_method = getattr(self._base, "get_trace_info", None)
        if callable(base_method):
            try:
                return base_method(trace_id)
            except NotImplementedError:
                return None
        return None

    def _delete_traces(
        self,
        experiment_id: str,
        max_timestamp_millis: int | None = None,
        max_traces: int | None = None,
        trace_ids: list[str] | None = None,
    ) -> int:
        self.get_experiment(experiment_id)
        base_method = getattr(self._base, "_delete_traces", None)
        if callable(base_method):
            try:
                return base_method(
                    experiment_id,
                    max_timestamp_millis=max_timestamp_millis,
                    max_traces=max_traces,
                    trace_ids=trace_ids,
                )
            except NotImplementedError:
                return 0
        return 0

    def get_online_trace_details(
        self,
        trace_id: str,
        sql_warehouse_id: str,
        source_inference_table: str,
        source_databricks_request_id: str,
    ) -> str:
        base_method = getattr(self._base, "get_online_trace_details", None)
        if callable(base_method):
            try:
                return base_method(
                    trace_id,
                    sql_warehouse_id,
                    source_inference_table,
                    source_databricks_request_id,
                )
            except NotImplementedError:
                return ""
        return ""

    def calculate_trace_filter_correlation(
        self,
        experiment_ids: list[str],
        filter_string1: str,
        filter_string2: str,
        base_filter: str | None = None,
    ):
        _require_namespace()
        allowed = [e.experiment_id for e in self.search_experiments()]
        filtered_ids = [eid for eid in (experiment_ids or []) if eid in allowed]
        base_method = getattr(self._base, "calculate_trace_filter_correlation", None)
        if callable(base_method):
            try:
                return base_method(
                    filtered_ids, filter_string1, filter_string2, base_filter
                )
            except NotImplementedError:
                return None
        return None

    # Logged models search wrapper (namespaced)
    def search_logged_models(
        self,
        experiment_ids: list[str],
        filter_string: str | None = None,
        datasets: list[dict] | None = None,
        max_results: int | None = None,
        order_by: list[dict] | None = None,
        page_token: str | None = None,
    ):
        base_method = getattr(self._base, "search_logged_models", None)
        if callable(base_method):
            # Filter experiments to only those in current namespace
            _require_namespace()
            allowed = [e.experiment_id for e in self.search_experiments()]
            filtered_ids = [eid for eid in (experiment_ids or []) if eid in allowed]
            if not filtered_ids:
                return PagedList([], None)
            return base_method(
                experiment_ids=filtered_ids,
                filter_string=_coalesce(filter_string, None),
                datasets=_coalesce(datasets, None),
                max_results=_coalesce(max_results, None),
                order_by=_coalesce(order_by, None),
                page_token=_coalesce(page_token, None),
            )
        return PagedList([], None)

    # Datasets API
    def create_dataset(
        self,
        name: str,
        tags: dict[str, str] | None = None,
        experiment_ids: list[str] | None = None,
    ):
        # Inject namespace tag for dataset if tags is provided
        ns = _require_namespace()
        tags = dict(tags or {})
        if NAMESPACE_TAG_KEY not in tags:
            tags[NAMESPACE_TAG_KEY] = ns
        if experiment_ids is not None:
            allowed = [e.experiment_id for e in self.search_experiments()]
            experiment_ids = [eid for eid in experiment_ids if eid in allowed]
        base_method = getattr(self._base, "create_dataset", None)
        if callable(base_method):
            return base_method(name=name, tags=tags, experiment_ids=experiment_ids)
        return None

    def delete_dataset(self, dataset_id: str) -> None:
        base_method = getattr(self._base, "delete_dataset", None)
        if callable(base_method):
            return base_method(dataset_id)
        return None

    def get_dataset(self, dataset_id: str):
        base_method = getattr(self._base, "get_dataset", None)
        if callable(base_method):
            return base_method(dataset_id)
        return None

    def search_datasets(
        self,
        experiment_ids: list[str] | None = None,
        filter_string: str | None = None,
        max_results: int = 1000,
        order_by: list[str] | None = None,
        page_token: str | None = None,
    ):
        _require_namespace()
        if experiment_ids is not None:
            allowed = [e.experiment_id for e in self.search_experiments()]
            experiment_ids = [eid for eid in experiment_ids if eid in allowed]
            if not experiment_ids:
                return PagedList([], None)
        base_method = getattr(self._base, "search_datasets", None)
        if callable(base_method):
            try:
                return base_method(
                    experiment_ids=experiment_ids,
                    filter_string=filter_string,
                    max_results=max_results,
                    order_by=order_by,
                    page_token=page_token,
                )
            except NotImplementedError:
                return PagedList([], None)
        return PagedList([], None)

    def set_dataset_tags(self, dataset_id: str, tags: dict) -> None:
        base_method = getattr(self._base, "set_dataset_tags", None)
        if callable(base_method):
            try:
                return base_method(dataset_id, tags)
            except NotImplementedError:
                return None
        return None

    def delete_dataset_tag(self, dataset_id: str, key: str) -> None:
        base_method = getattr(self._base, "delete_dataset_tag", None)
        if callable(base_method):
            try:
                return base_method(dataset_id, key)
            except NotImplementedError:
                return None
        return None

    def add_dataset_to_experiments(self, dataset_id: str, experiment_ids: list[str]):
        _require_namespace()
        allowed = [e.experiment_id for e in self.search_experiments()]
        experiment_ids = [eid for eid in (experiment_ids or []) if eid in allowed]
        base_method = getattr(self._base, "add_dataset_to_experiments", None)
        if callable(base_method):
            return base_method(dataset_id, experiment_ids)
        return None

    def remove_dataset_from_experiments(
        self, dataset_id: str, experiment_ids: list[str]
    ):
        _require_namespace()
        allowed = [e.experiment_id for e in self.search_experiments()]
        experiment_ids = [eid for eid in (experiment_ids or []) if eid in allowed]
        base_method = getattr(self._base, "remove_dataset_from_experiments", None)
        if callable(base_method):
            return base_method(dataset_id, experiment_ids)
        return None

    def get_dataset_experiment_ids(self, dataset_id: str) -> list[str]:
        base_method = getattr(self._base, "get_dataset_experiment_ids", None)
        if callable(base_method):
            try:
                result = base_method(dataset_id)
                # Filter to namespace-visible experiments
                allowed = {e.experiment_id for e in self.search_experiments()}
                return [eid for eid in (result or []) if eid in allowed]
            except NotImplementedError:
                return []
        return []

    def upsert_dataset_records(
        self, dataset_id: str, records: list[dict]
    ) -> dict[str, int]:
        base_method = getattr(self._base, "upsert_dataset_records", None)
        if callable(base_method):
            try:
                return base_method(dataset_id, records)
            except NotImplementedError:
                return {}
        return {}

    def _load_dataset_records(
        self,
        dataset_id: str,
        max_results: int | None = None,
        page_token: str | None = None,
    ):
        base_method = getattr(self._base, "_load_dataset_records", None)
        if callable(base_method):
            try:
                return base_method(
                    dataset_id, max_results=max_results, page_token=page_token
                )
            except NotImplementedError:
                return ([], None)
        return ([], None)

    # Assessments & scorers APIs
    def create_assessment(self, assessment):
        base_method = getattr(self._base, "create_assessment", None)
        if callable(base_method):
            try:
                return base_method(assessment)
            except NotImplementedError:
                return None
        return None

    def update_assessment(
        self,
        trace_id: str,
        assessment_id: str,
        name: str | None = None,
        expectation: str | None = None,
        feedback: str | None = None,
        rationale: str | None = None,
        metadata: dict[str, str] | None = None,
    ):
        base_method = getattr(self._base, "update_assessment", None)
        if callable(base_method):
            try:
                return base_method(
                    trace_id,
                    assessment_id,
                    name=name,
                    expectation=expectation,
                    feedback=feedback,
                    rationale=rationale,
                    metadata=metadata,
                )
            except NotImplementedError:
                return None
        return None

    def delete_assessment(self, trace_id: str, assessment_id):
        base_method = getattr(self._base, "delete_assessment", None)
        if callable(base_method):
            try:
                return base_method(trace_id, assessment_id)
            except NotImplementedError:
                return None
        return None

    def get_assessment(self, trace_id: str, assessment_id: str):
        base_method = getattr(self._base, "get_assessment", None)
        if callable(base_method):
            try:
                return base_method(trace_id, assessment_id)
            except NotImplementedError:
                return None
        return None

    def register_scorer(
        self, experiment_id: str, name: str, serialized_scorer: str
    ) -> int:
        self.get_experiment(experiment_id)
        base_method = getattr(self._base, "register_scorer", None)
        if callable(base_method):
            try:
                return base_method(experiment_id, name, serialized_scorer)
            except NotImplementedError:
                return 0
        return 0

    def delete_scorer(self, experiment_id, name, version=None):
        self.get_experiment(experiment_id)
        base_method = getattr(self._base, "delete_scorer", None)
        if callable(base_method):
            try:
                return base_method(experiment_id, name, version)
            except NotImplementedError:
                return None
        return None

    def get_scorer(self, experiment_id, name, version=None):
        self.get_experiment(experiment_id)
        base_method = getattr(self._base, "get_scorer", None)
        if callable(base_method):
            try:
                return base_method(experiment_id, name, version)
            except NotImplementedError:
                return None
        return None

    def list_scorers(self, experiment_id):
        self.get_experiment(experiment_id)
        base_method = getattr(self._base, "list_scorers", None)
        if callable(base_method):
            try:
                return base_method(experiment_id)
            except NotImplementedError:
                return []
        return []

    def list_scorer_versions(self, experiment_id, name):
        self.get_experiment(experiment_id)
        base_method = getattr(self._base, "list_scorer_versions", None)
        if callable(base_method):
            try:
                return base_method(experiment_id, name)
            except NotImplementedError:
                return []
        return []


class NamespaceEnforcingModelRegistryStore(AbstractModelRegistryStore):
    def __init__(self, base: AbstractModelRegistryStore):
        super().__init__(store_uri=None, tracking_uri=None)
        self._base = base

    def create_registered_model(
        self, name, tags=None, description=None, deployment_job_id=None
    ):
        tags = _ensure_top_level_has_namespace(tags)
        return self._base.create_registered_model(
            _to_internal_name(name), tags, description, deployment_job_id
        )

    def update_registered_model(self, name, description, deployment_job_id=None):
        self.get_registered_model(name)
        return self._base.update_registered_model(
            _to_internal_name(name), description, deployment_job_id
        )

    def rename_registered_model(self, name, new_name):
        self.get_registered_model(name)
        return self._base.rename_registered_model(
            _to_internal_name(name), _to_internal_name(new_name)
        )

    def delete_registered_model(self, name):
        self.get_registered_model(name)
        return self._base.delete_registered_model(_to_internal_name(name))

    def get_registered_model(self, name):
        rm: RegisteredModel = self._base.get_registered_model(_to_internal_name(name))
        ns = _require_namespace()
        tag_map = _tags_to_dict(rm.tags)
        if tag_map.get(NAMESPACE_TAG_KEY) != ns:
            from mlflow.exceptions import MlflowException
            from mlflow.protos.databricks_pb2 import PERMISSION_DENIED

            raise MlflowException(
                "Registered model not in namespace", error_code=PERMISSION_DENIED
            )
        try:
            rm.name = _from_internal_name(rm.name)
        except Exception:
            pass
        return rm

    def get_latest_versions(self, name, stages=None):
        self.get_registered_model(name)
        res = self._base.get_latest_versions(_to_internal_name(name), stages)
        for mv in res:
            try:
                mv.name = _from_internal_name(mv.name)
            except Exception:
                pass
        return res

    def search_registered_models(
        self, filter_string=None, max_results=None, order_by=None, page_token=None
    ):
        ns = _require_namespace()
        filter_ns = _build_namespace_filter(ns)
        filter_string = (
            f"{filter_string} AND {filter_ns}" if filter_string else filter_ns
        )
        res = self._base.search_registered_models(
            filter_string, max_results, order_by, page_token
        )
        items = []
        for e in res:
            try:
                e.name = _from_internal_name(e.name)
            except Exception:
                pass
            items.append(e)
        return PagedList(items, res.token)

    def create_model_version(
        self,
        name,
        source,
        run_id=None,
        tags=None,
        run_link=None,
        description=None,
        local_model_path=None,
        model_id: str | None = None,
    ):
        # Ensure RM is in namespace
        self.get_registered_model(name)
        result = self._base.create_model_version(
            _to_internal_name(name),
            source,
            run_id,
            tags,
            run_link,
            description,
            local_model_path,
            model_id,
        )
        if result is None:
            try:
                latest = self._base.get_latest_versions(_to_internal_name(name))
                if latest:
                    # pick the highest numeric version
                    best = max(
                        latest, key=lambda mv: int(getattr(mv, "version", 0) or 0)
                    )
                    try:
                        best.name = _from_internal_name(best.name)
                    except Exception:
                        pass
                    return best
            except Exception:
                pass
        try:
            result.name = _from_internal_name(result.name)
        except Exception:
            pass
        return result

    def update_model_version(self, name, version, description):
        self.get_registered_model(name)
        return self._base.update_model_version(
            _to_internal_name(name), version, description
        )

    def transition_model_version_stage(
        self, name, version, stage, archive_existing_versions
    ):
        self.get_registered_model(name)
        return self._base.transition_model_version_stage(
            _to_internal_name(name), version, stage, archive_existing_versions
        )

    def delete_model_version(self, name, version):
        self.get_registered_model(name)
        return self._base.delete_model_version(_to_internal_name(name), version)

    def get_model_version(self, name, version):
        self.get_registered_model(name)
        mv = self._base.get_model_version(_to_internal_name(name), version)
        try:
            mv.name = _from_internal_name(mv.name)
        except Exception:
            pass
        return mv

    def get_model_version_download_uri(self, name, version):
        self.get_registered_model(name)
        return self._base.get_model_version_download_uri(
            _to_internal_name(name), version
        )

    def search_model_versions(
        self, filter_string=None, max_results=None, order_by=None, page_token=None
    ):
        base_method = getattr(self._base, "search_model_versions", None)
        if callable(base_method):
            import re

            rewritten = filter_string
            if isinstance(filter_string, str):

                def repl(m):
                    return f"name = '{_to_internal_name(m.group(1))}'"

                rewritten = re.sub(r"name\s*=\s*'([^']+)'", repl, filter_string)
            res = base_method(rewritten, max_results, order_by, page_token)
            items = []
            for mv in res:
                try:
                    mv.name = _from_internal_name(mv.name)
                except Exception:
                    pass
                items.append(mv)
            return PagedList(items, res.token)
        return PagedList([], None)

    def set_model_version_tag(self, name, version, tag):
        self.get_registered_model(name)
        return self._base.set_model_version_tag(_to_internal_name(name), version, tag)

    def delete_model_version_tag(self, name, version, key):
        self.get_registered_model(name)
        return self._base.delete_model_version_tag(
            _to_internal_name(name), version, key
        )

    def set_registered_model_tag(self, name, tag):
        self.get_registered_model(name)
        return self._base.set_registered_model_tag(_to_internal_name(name), tag)

    def delete_registered_model_tag(self, name, key):
        self.get_registered_model(name)
        return self._base.delete_registered_model_tag(_to_internal_name(name), key)

    def set_registered_model_alias(self, name, alias, version):
        self.get_registered_model(name)
        return self._base.set_registered_model_alias(
            _to_internal_name(name), alias, version
        )

    def delete_registered_model_alias(self, name, alias):
        self.get_registered_model(name)
        return self._base.delete_registered_model_alias(_to_internal_name(name), alias)

    def get_model_version_by_alias(self, name, alias):
        self.get_registered_model(name)
        mv = self._base.get_model_version_by_alias(_to_internal_name(name), alias)
        try:
            mv.name = _from_internal_name(mv.name)
        except Exception:
            pass
        return mv

    # Delegate the rest
    def __getattr__(self, item):
        return getattr(self._base, item)

    # Prompts handling: enforce namespace on create/get/search
    def create_prompt(
        self,
        name: str,
        description: str | None = None,
        tags: dict[str, str] | None = None,
    ):
        # Base expects tags as a dict for prompts
        ns = _require_namespace()
        tags = dict(tags or {})
        if NAMESPACE_TAG_KEY not in tags:
            tags[NAMESPACE_TAG_KEY] = ns
        return self._base.create_prompt(name, description=description, tags=tags)

    def get_prompt(self, name: str):
        prompt = self._base.get_prompt(name)
        ns = _require_namespace()
        if prompt:
            tag_map = _tags_to_dict(prompt.tags)
            if tag_map.get(NAMESPACE_TAG_KEY) != ns:
                from mlflow.exceptions import MlflowException
                from mlflow.protos.databricks_pb2 import PERMISSION_DENIED

                raise MlflowException(
                    "Prompt not in namespace", error_code=PERMISSION_DENIED
                )
        return prompt

    def search_prompts(
        self,
        filter_string: str | None = None,
        max_results: int | None = None,
        order_by: list[str] | None = None,
        page_token: str | None = None,
    ):
        ns = _require_namespace()
        filter_ns = _build_namespace_filter(ns)
        filter_string = (
            f"{filter_string} AND {filter_ns}" if filter_string else filter_ns
        )
        return self._base.search_prompts(
            filter_string, max_results, order_by, page_token
        )

    # Webhooks pass-throughs: return empty if not supported by the base store
    def list_webhooks_by_event(
        self, event, max_results: int | None = None, page_token: str | None = None
    ):
        base_method = getattr(self._base, "list_webhooks_by_event", None)
        if callable(base_method):
            try:
                return base_method(
                    event, max_results=max_results, page_token=page_token
                )
            except NotImplementedError:
                return PagedList([], None)
        return PagedList([], None)

    # Webhooks generic APIs: provide pass-throughs with safe fallbacks
    def create_webhook(
        self,
        name: str,
        url: str,
        events,
        description: str | None = None,
        secret: str | None = None,
        status: None | object = None,
    ):
        base_method = getattr(self._base, "create_webhook", None)
        if callable(base_method):
            try:
                return base_method(
                    name=name,
                    url=url,
                    events=events,
                    description=description,
                    secret=secret,
                    status=status,
                )
            except NotImplementedError:
                return None
        return None

    def get_webhook(self, webhook_id: str):
        base_method = getattr(self._base, "get_webhook", None)
        if callable(base_method):
            try:
                return base_method(webhook_id)
            except NotImplementedError:
                return None
        return None

    def list_webhooks(
        self, max_results: int | None = None, page_token: str | None = None
    ):
        base_method = getattr(self._base, "list_webhooks", None)
        if callable(base_method):
            try:
                return base_method(max_results=max_results, page_token=page_token)
            except NotImplementedError:
                return PagedList([], None)
        return PagedList([], None)

    def update_webhook(
        self,
        webhook_id: str,
        name: str | None = None,
        description: str | None = None,
        url: str | None = None,
        events=None,
        secret: str | None = None,
        status: None | object = None,
    ):
        base_method = getattr(self._base, "update_webhook", None)
        if callable(base_method):
            try:
                return base_method(
                    webhook_id,
                    name=name,
                    description=description,
                    url=url,
                    events=events,
                    secret=secret,
                    status=status,
                )
            except NotImplementedError:
                return None
        return None

    def delete_webhook(self, webhook_id: str) -> None:
        base_method = getattr(self._base, "delete_webhook", None)
        if callable(base_method):
            try:
                return base_method(webhook_id)
            except NotImplementedError:
                return None
        return None

    def test_webhook(self, webhook_id: str, event=None):
        base_method = getattr(self._base, "test_webhook", None)
        if callable(base_method):
            try:
                return base_method(webhook_id, event=event)
            except NotImplementedError:
                return None
        return None
