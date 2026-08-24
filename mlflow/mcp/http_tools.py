"""In-process genai MCP tools for the tracking-server HTTP mount.

These call ``_get_tracking_store()`` directly. They must not wrap Click
commands, Flask views, or HTTP-loopback to the same process.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from mlflow.entities.assessment import AssessmentSource, AssessmentSourceType, Expectation, Feedback
from mlflow.entities.experiment_tag import ExperimentTag
from mlflow.entities.run_status import RunStatus
from mlflow.entities.run_tag import RunTag
from mlflow.entities.view_type import ViewType
from mlflow.exceptions import MlflowException
from mlflow.mcp import http_auth
from mlflow.tracing.constant import TraceExperimentTagKey
from mlflow.utils.mlflow_tags import MLFLOW_PARENT_RUN_ID, MLFLOW_RUN_NOTE
from mlflow.utils.time import get_current_time_millis
from mlflow.utils.validation import _validate_trace_archival_retention_string

HTTP_MCP_MAX_RESULTS = 500


def _store():
    from mlflow.server.handlers import _get_tracking_store

    return _get_tracking_store()


def _cap_max_results(max_results: int | None, default: int = 100) -> int:
    value = default if max_results is None else max_results
    if value < 1:
        raise MlflowException.invalid_parameter_value("max_results must be at least 1")
    return min(value, HTTP_MCP_MAX_RESULTS)


def _experiment_to_dict(experiment) -> dict[str, Any]:
    return {
        "experiment_id": experiment.experiment_id,
        "name": experiment.name,
        "artifact_location": experiment.artifact_location,
        "lifecycle_stage": experiment.lifecycle_stage,
        "tags": dict(experiment.tags) if experiment.tags else {},
        "creation_time": experiment.creation_time,
        "last_update_time": experiment.last_update_time,
    }


def _scorer_version_to_dict(scorer_version) -> dict[str, Any]:
    return {
        "experiment_id": scorer_version.experiment_id,
        "name": scorer_version.scorer_name,
        "version": scorer_version.scorer_version,
        "scorer_id": scorer_version.scorer_id,
        "creation_time": scorer_version.creation_time,
    }


def _encode_trace_archival_retention_tag(retention: str) -> str:
    return json.dumps({"type": "duration", "value": retention})


def _encode_trace_archive_now_tag(older_than: str | None = None) -> str:
    payload = {} if older_than is None else {"older_than": older_than}
    return json.dumps(payload)


def _parse_json_value(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _assessment_source(source_type: str | None, source_id: str | None) -> AssessmentSource | None:
    if not source_type or not source_id:
        return None
    return AssessmentSource(
        source_type=getattr(AssessmentSourceType, source_type),
        source_id=source_id,
    )


def search_experiments(
    view: str = "active_only",
    max_results: int | None = None,
) -> dict[str, Any]:
    """Search experiments in the tracking store."""
    view_type = ViewType.from_string(view) if view else ViewType.ACTIVE_ONLY
    capped = _cap_max_results(max_results)
    page = _store().search_experiments(view_type=view_type, max_results=capped)
    experiments = http_auth.filter_readable_experiments(list(page))
    return {
        "experiments": [_experiment_to_dict(e) for e in experiments],
        "next_page_token": page.token,
    }


def get_experiment(
    experiment_id: str | None = None,
    experiment_name: str | None = None,
) -> dict[str, Any]:
    """Get an experiment by ID or name."""
    if (experiment_id is None) == (experiment_name is None):
        raise MlflowException.invalid_parameter_value(
            "Must specify exactly one of experiment_id or experiment_name."
        )
    if experiment_name is not None:
        experiment_id = http_auth.require_experiment_read_by_name(experiment_name)
        experiment = _store().get_experiment(experiment_id)
    else:
        http_auth.require_experiment_read(experiment_id)
        experiment = _store().get_experiment(experiment_id)
    return _experiment_to_dict(experiment)


def create_experiment(
    experiment_name: str,
    artifact_location: str | None = None,
    trace_archival_retention: str | None = None,
) -> dict[str, Any]:
    """Create an experiment."""
    http_auth.require_create_experiment()
    tags = None
    if trace_archival_retention is not None:
        retention = _validate_trace_archival_retention_string(trace_archival_retention)
        tags = [
            ExperimentTag(
                TraceExperimentTagKey.ARCHIVAL_RETENTION,
                _encode_trace_archival_retention_tag(retention),
            )
        ]
    experiment_id = _store().create_experiment(experiment_name, artifact_location, tags=tags)
    http_auth.grant_experiment_manage(experiment_id)
    return {"experiment_id": experiment_id, "name": experiment_name}


def rename_experiment(experiment_id: str, new_name: str) -> dict[str, Any]:
    """Rename an active experiment."""
    http_auth.require_experiment_update(experiment_id)
    _store().rename_experiment(experiment_id, new_name)
    return {"experiment_id": experiment_id, "name": new_name}


def update_experiment(
    experiment_id: str,
    trace_archival_retention: str | None = None,
    clear_trace_archival_retention: bool = False,
    trace_archive_now: bool = False,
    trace_archive_now_older_than: str | None = None,
    clear_trace_archive_now: bool = False,
) -> dict[str, Any]:
    """Update experiment-level trace archival policy controls."""
    http_auth.require_experiment_update(experiment_id)
    if trace_archival_retention is not None:
        trace_archival_retention = _validate_trace_archival_retention_string(
            trace_archival_retention
        )
    if trace_archive_now_older_than is not None:
        trace_archive_now_older_than = _validate_trace_archival_retention_string(
            trace_archive_now_older_than
        )
    if trace_archival_retention is not None and clear_trace_archival_retention:
        raise MlflowException.invalid_parameter_value(
            "Cannot specify both trace_archival_retention and clear_trace_archival_retention."
        )
    if trace_archive_now and trace_archive_now_older_than is not None:
        raise MlflowException.invalid_parameter_value(
            "Cannot specify both trace_archive_now and trace_archive_now_older_than."
        )
    if clear_trace_archive_now and (trace_archive_now or trace_archive_now_older_than is not None):
        raise MlflowException.invalid_parameter_value(
            "Cannot specify clear_trace_archive_now together with archive-now request flags."
        )
    if not any([
        trace_archival_retention is not None,
        clear_trace_archival_retention,
        trace_archive_now,
        trace_archive_now_older_than is not None,
        clear_trace_archive_now,
    ]):
        raise MlflowException.invalid_parameter_value("Must specify at least one update option.")

    store = _store()
    experiment = store.get_experiment(experiment_id)
    existing_tags = experiment.tags
    changes: list[str] = []

    if trace_archival_retention is not None:
        store.set_experiment_tag(
            experiment_id,
            ExperimentTag(
                TraceExperimentTagKey.ARCHIVAL_RETENTION,
                _encode_trace_archival_retention_tag(trace_archival_retention),
            ),
        )
        changes.append(f"set trace archival retention to {trace_archival_retention}")
    elif clear_trace_archival_retention:
        if TraceExperimentTagKey.ARCHIVAL_RETENTION in existing_tags:
            store.delete_experiment_tag(experiment_id, TraceExperimentTagKey.ARCHIVAL_RETENTION)
            changes.append("cleared trace archival retention override")
        else:
            changes.append("trace archival retention override was already unset")

    if trace_archive_now:
        store.set_experiment_tag(
            experiment_id,
            ExperimentTag(TraceExperimentTagKey.ARCHIVE_NOW, _encode_trace_archive_now_tag()),
        )
        changes.append("requested archive-now on the next scheduler pass")
    elif trace_archive_now_older_than is not None:
        store.set_experiment_tag(
            experiment_id,
            ExperimentTag(
                TraceExperimentTagKey.ARCHIVE_NOW,
                _encode_trace_archive_now_tag(trace_archive_now_older_than),
            ),
        )
        changes.append(
            "requested archive-now for traces older than "
            f"{trace_archive_now_older_than} on the next scheduler pass"
        )
    elif clear_trace_archive_now:
        if TraceExperimentTagKey.ARCHIVE_NOW in existing_tags:
            store.delete_experiment_tag(experiment_id, TraceExperimentTagKey.ARCHIVE_NOW)
            changes.append("cleared pending archive-now request")
        else:
            changes.append("archive-now request was already unset")

    return {"experiment_id": experiment_id, "changes": changes}


def delete_experiment(experiment_id: str) -> dict[str, Any]:
    """Mark an experiment for deletion."""
    http_auth.require_experiment_delete(experiment_id)
    _store().delete_experiment(experiment_id)
    return {"experiment_id": experiment_id, "deleted": True}


def restore_experiment(experiment_id: str) -> dict[str, Any]:
    """Restore a deleted experiment."""
    http_auth.require_experiment_delete(experiment_id)
    _store().restore_experiment(experiment_id)
    return {"experiment_id": experiment_id, "restored": True}


def list_runs(
    experiment_id: str,
    view: str = "active_only",
    max_results: int | None = None,
) -> dict[str, Any]:
    """List runs in an experiment."""
    http_auth.require_experiment_read(experiment_id)
    view_type = ViewType.from_string(view) if view else ViewType.ACTIVE_ONLY
    runs = _store().search_runs(
        [experiment_id], None, view_type, max_results=_cap_max_results(max_results)
    )
    return {
        "runs": [run.to_dictionary() for run in runs],
        "next_page_token": runs.token,
    }


def describe_run(run_id: str) -> dict[str, Any]:
    """Get run details."""
    http_auth.require_run_read(run_id)
    return _store().get_run(run_id).to_dictionary()


def create_run(
    experiment_id: str | None = None,
    experiment_name: str | None = None,
    run_name: str | None = None,
    description: str | None = None,
    tags: dict[str, str] | None = None,
    status: Literal["FINISHED", "FAILED", "KILLED"] = "FINISHED",
    parent_run_id: str | None = None,
) -> dict[str, Any]:
    """Create a run and immediately end it with the given status."""
    if (experiment_id is None) == (experiment_name is None):
        raise MlflowException.invalid_parameter_value(
            "Must specify exactly one of experiment_id or experiment_name."
        )
    if experiment_name is not None:
        experiment_id = http_auth.require_experiment_read_by_name(experiment_name)
    http_auth.require_experiment_update(experiment_id)
    if parent_run_id:
        http_auth.require_run_update(parent_run_id)

    run_tags = [RunTag(k, v) for k, v in (tags or {}).items()]
    if description:
        run_tags.append(RunTag(MLFLOW_RUN_NOTE, description))
    if parent_run_id:
        run_tags.append(RunTag(MLFLOW_PARENT_RUN_ID, parent_run_id))

    user_id = http_auth.current_username() or ""
    store = _store()
    run = store.create_run(
        experiment_id=experiment_id,
        user_id=user_id,
        start_time=get_current_time_millis(),
        tags=run_tags,
        run_name=run_name,
    )
    end_time = get_current_time_millis()
    store.update_run_info(run.info.run_id, RunStatus.from_string(status.upper()), end_time, None)
    return store.get_run(run.info.run_id).to_dictionary()


def delete_run(run_id: str) -> dict[str, Any]:
    """Mark a run for deletion."""
    http_auth.require_run_delete(run_id)
    _store().delete_run(run_id)
    return {"run_id": run_id, "deleted": True}


def restore_run(run_id: str) -> dict[str, Any]:
    """Restore a deleted run."""
    http_auth.require_run_delete(run_id)
    _store().restore_run(run_id)
    return {"run_id": run_id, "restored": True}


def link_traces_to_run(run_id: str, trace_ids: list[str]) -> dict[str, Any]:
    """Link traces to a run."""
    http_auth.require_link_traces_to_run(run_id, trace_ids)
    _store().link_traces_to_run(trace_ids, run_id)
    return {"run_id": run_id, "trace_ids": trace_ids}


def search_traces(
    experiment_id: str,
    filter_string: str | None = None,
    max_results: int | None = 100,
    order_by: list[str] | None = None,
    page_token: str | None = None,
) -> dict[str, Any]:
    """Search traces in an experiment (v3 API)."""
    http_auth.require_experiments_read([experiment_id])
    traces, token = _store().search_traces(
        locations=[experiment_id],
        filter_string=filter_string,
        max_results=_cap_max_results(max_results),
        order_by=order_by,
        page_token=page_token,
    )
    return {
        "traces": [t.to_dict() for t in traces],
        "next_page_token": token,
    }


def get_trace(trace_id: str, allow_partial: bool = True) -> dict[str, Any]:
    """Get a trace including spans."""
    http_auth.require_trace_read(trace_id)
    trace = _store().get_trace(trace_id, allow_partial=allow_partial)
    return trace.to_dict()


def delete_traces(
    experiment_id: str,
    trace_ids: list[str] | None = None,
    max_timestamp_millis: int | None = None,
    max_traces: int | None = None,
) -> dict[str, Any]:
    """Delete traces by ID or timestamp criteria."""
    http_auth.require_experiment_delete(experiment_id)
    deleted = _store().delete_traces(
        experiment_id=experiment_id,
        max_timestamp_millis=max_timestamp_millis,
        max_traces=max_traces,
        trace_ids=trace_ids,
    )
    return {"experiment_id": experiment_id, "traces_deleted": deleted}


def set_trace_tag(trace_id: str, key: str, value: str) -> dict[str, Any]:
    """Set a tag on a trace."""
    http_auth.require_trace_update(trace_id)
    _store().set_trace_tag(trace_id, key, value)
    return {"trace_id": trace_id, "key": key, "value": value}


def delete_trace_tag(trace_id: str, key: str) -> dict[str, Any]:
    """Delete a tag from a trace."""
    http_auth.require_trace_update(trace_id)
    _store().delete_trace_tag(trace_id, key)
    return {"trace_id": trace_id, "key": key, "deleted": True}


def log_trace_feedback(
    trace_id: str,
    name: str,
    value: str | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    rationale: str | None = None,
    metadata: dict[str, str] | None = None,
    span_id: str | None = None,
) -> dict[str, Any]:
    """Log feedback (evaluation score) to a trace."""
    http_auth.require_trace_update(trace_id)
    parsed_value = _parse_json_value(value) if isinstance(value, str) else value
    assessment = Feedback(
        name=name,
        value=parsed_value,
        source=_assessment_source(source_type, source_id),
        trace_id=trace_id,
        rationale=rationale,
        metadata=metadata,
        span_id=span_id,
    )
    created = _store().create_assessment(assessment)
    return created.to_dictionary()


def log_trace_expectation(
    trace_id: str,
    name: str,
    value: str,
    source_type: str | None = None,
    source_id: str | None = None,
    metadata: dict[str, str] | None = None,
    span_id: str | None = None,
) -> dict[str, Any]:
    """Log an expectation (ground truth) to a trace."""
    http_auth.require_trace_update(trace_id)
    assessment = Expectation(
        name=name,
        value=_parse_json_value(value),
        source=_assessment_source(source_type, source_id),
        trace_id=trace_id,
        metadata=metadata,
        span_id=span_id,
    )
    created = _store().create_assessment(assessment)
    return created.to_dictionary()


def get_trace_assessment(trace_id: str, assessment_id: str) -> dict[str, Any]:
    """Get assessment details."""
    http_auth.require_trace_read(trace_id)
    return _store().get_assessment(trace_id, assessment_id).to_dictionary()


def update_trace_assessment(
    trace_id: str,
    assessment_id: str,
    value: str | None = None,
    rationale: str | None = None,
    metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Update an existing assessment's value, rationale, or metadata."""
    http_auth.require_trace_update(trace_id)
    store = _store()
    existing = store.get_assessment(trace_id, assessment_id)
    parsed_value = _parse_json_value(value) if value is not None else None
    kwargs: dict[str, Any] = {}
    if rationale is not None:
        kwargs["rationale"] = rationale
    if metadata is not None:
        kwargs["metadata"] = metadata
    if value is not None:
        if getattr(existing, "feedback", None) is not None:
            kwargs["feedback"] = Feedback(name=existing.name, value=parsed_value)
        else:
            kwargs["expectation"] = Expectation(name=existing.name, value=parsed_value)
    updated = store.update_assessment(trace_id=trace_id, assessment_id=assessment_id, **kwargs)
    return updated.to_dictionary()


def delete_trace_assessment(trace_id: str, assessment_id: str) -> dict[str, Any]:
    """Delete an assessment from a trace."""
    http_auth.require_trace_update(trace_id)
    _store().delete_assessment(trace_id, assessment_id)
    return {"trace_id": trace_id, "assessment_id": assessment_id, "deleted": True}


def list_scorers(experiment_id: str) -> dict[str, Any]:
    """List registered scorers for an experiment."""
    http_auth.require_experiment_read(experiment_id)
    scorers = _store().list_scorers(experiment_id)
    return {"scorers": [_scorer_version_to_dict(s) for s in scorers]}


def register_llm_judge_scorer(
    name: str,
    instructions: str,
    experiment_id: str,
    model: str | None = None,
    description: str | None = None,
    base_url: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create an LLM judge scorer and register it in the tracking store."""
    http_auth.require_experiment_update(experiment_id)
    from mlflow.genai.judges import make_judge

    judge = make_judge(
        name=name,
        instructions=instructions,
        model=model,
        description=description,
        feedback_value_type=str,
        base_url=base_url,
        extra_headers=extra_headers,
    )
    serialized_scorer = json.dumps(judge.model_dump())
    serialized_data = json.loads(serialized_scorer)
    if serialized_data.get("call_source") is not None:
        raise MlflowException.invalid_parameter_value(
            "Decorator scorers cannot be registered through the tracking server MCP."
        )
    scorer_version = _store().register_scorer(experiment_id, judge.name, serialized_scorer)
    http_auth.grant_scorer_manage(experiment_id, judge.name)
    return _scorer_version_to_dict(scorer_version)


HTTP_MCP_TOOLS = [
    search_experiments,
    get_experiment,
    create_experiment,
    rename_experiment,
    update_experiment,
    delete_experiment,
    restore_experiment,
    list_runs,
    describe_run,
    create_run,
    delete_run,
    restore_run,
    link_traces_to_run,
    search_traces,
    get_trace,
    delete_traces,
    set_trace_tag,
    delete_trace_tag,
    log_trace_feedback,
    log_trace_expectation,
    get_trace_assessment,
    update_trace_assessment,
    delete_trace_assessment,
    list_scorers,
    register_llm_judge_scorer,
]
