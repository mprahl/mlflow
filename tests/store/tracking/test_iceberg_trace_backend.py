from __future__ import annotations

import concurrent.futures
import json
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType

import pytest
import sqlalchemy as sa
from opentelemetry import trace as trace_api
from pyiceberg.exceptions import CommitFailedException
from pyiceberg.expressions.visitors import bind

import mlflow.store.tracking.iceberg_trace_backend as iceberg_trace_backend_module
import mlflow.store.tracking.sqlalchemy_store as sqlalchemy_store_module
import mlflow.tracing.trace_archival_config as trace_archival_config_module
from mlflow.entities import (
    AssessmentSource,
    AssessmentSourceType,
    Expectation,
    ExperimentTag,
    Feedback,
    trace_location,
)
from mlflow.entities.span import Span
from mlflow.entities.trace_info import TraceInfo
from mlflow.entities.trace_metrics import (
    AggregationType,
    MetricAggregation,
    MetricDataPoint,
    MetricViewType,
)
from mlflow.entities.trace_state import TraceState
from mlflow.environment_variables import (
    MLFLOW_ENABLE_WORKSPACES,
    MLFLOW_ICEBERG_TRACE_ARCHIVE_EXPERIMENT_MAX_WORKERS,
    MLFLOW_ICEBERG_TRACE_ARCHIVE_MAX_WORKERS,
    MLFLOW_ICEBERG_TRACE_ARCHIVE_PROJECT_BATCH_SIZE,
    MLFLOW_ICEBERG_TRACE_ARCHIVE_ROLLUP_REFRESH_CHUNKS,
    MLFLOW_ICEBERG_WAREHOUSE_URI,
    MLFLOW_SQL_TRACE_ROLLUPS_ENABLED,
    MLFLOW_TRACE_ARCHIVAL_CONFIG,
)
from mlflow.exceptions import MlflowException
from mlflow.protos.databricks_pb2 import RESOURCE_EXHAUSTED, ErrorCode
from mlflow.store.artifact.artifact_repository_registry import get_artifact_repository
from mlflow.store.entities.paged_list import PagedList
from mlflow.store.tracking.dbmodels.models import (
    SqlArchivedTraceLocator,
    SqlAssessmentDailyRollup,
    SqlSpanCostDailyRollup,
    SqlTraceMetricDailyRollup,
    SqlTraceRollupRebuild,
)
from mlflow.store.tracking.iceberg_trace_backend import (
    IcebergSqlAlchemyStore,
    _DuckDBTraceSearchCompiler,
    _TraceSearchSpec,
    run_iceberg_trace_compaction,
)
from mlflow.tracing.constant import (
    AssessmentMetricDimensionKey,
    AssessmentMetricKey,
    SpanAttributeKey,
    SpanMetricDimensionKey,
    SpanMetricKey,
    SpansLocation,
    TraceExperimentTagKey,
    TraceMetadataKey,
    TraceMetricDimensionKey,
    TraceMetricKey,
    TraceTagKey,
)
from mlflow.tracing.otel.otel_archival import TRACE_ARCHIVAL_FILENAME
from mlflow.utils.workspace_context import WorkspaceContext
from mlflow.utils.workspace_utils import DEFAULT_WORKSPACE_NAME

from tests.store.tracking.sqlalchemy_store.conftest import create_test_span

pytestmark = pytest.mark.notrackingurimock


def _replace_trace_rows_for_tests(
    store: IcebergSqlAlchemyStore, trace_ids: list[str], rows: list[dict]
) -> None:
    store._append_rows_with_conflict_retry(
        rows=rows,
        schema=iceberg_trace_backend_module._TRACE_INDEX_SCHEMA,
        load_table=store._trace_table,
        table_name=iceberg_trace_backend_module._TRACE_INDEX_TABLE,
        delete_expr=store._workspace_trace_ids_expr(trace_ids),
    )


def _replace_trace_tag_rows_for_tests(
    store: IcebergSqlAlchemyStore, trace_ids: list[str], rows: list[dict]
) -> None:
    store._append_rows_with_conflict_retry(
        rows=rows,
        schema=iceberg_trace_backend_module._TRACE_TAG_INDEX_SCHEMA,
        load_table=store._trace_tag_table,
        table_name=iceberg_trace_backend_module._TRACE_TAG_INDEX_TABLE,
        delete_expr=store._workspace_trace_ids_expr(trace_ids),
    )


def _replace_assessment_rows_for_tests(
    store: IcebergSqlAlchemyStore, assessment_ids: list[str], rows: list[dict]
) -> None:
    store._append_rows_with_conflict_retry(
        rows=rows,
        schema=iceberg_trace_backend_module._ASSESSMENT_INDEX_SCHEMA,
        load_table=store._assessment_table,
        table_name=iceberg_trace_backend_module._ASSESSMENT_INDEX_TABLE,
        delete_expr=store._workspace_assessment_ids_expr(assessment_ids),
    )


def _read_iceberg_table_rows(store: IcebergSqlAlchemyStore, table_name: str) -> list[dict]:
    return store._run_duckdb_query(
        trace_rows=store._iceberg_scan(table_name, workspace=store._get_active_workspace()),
        sql="SELECT * FROM trace_rows",
    )


class ColdOnlyIcebergSqlAlchemyStore(IcebergSqlAlchemyStore):
    @property
    def _hybrid_enabled(self) -> bool:
        return False

    def _require_assessment_row(self, trace_id: str, assessment_id: str) -> dict:
        self._require_trace_row(trace_id)
        rows = self._latest_assessment_rows(trace_id=trace_id, assessment_id=assessment_id)
        if not rows:
            raise MlflowException(
                f"Assessment with ID '{assessment_id}' not found for trace '{trace_id}'.",
                error_code=iceberg_trace_backend_module.RESOURCE_DOES_NOT_EXIST,
            )
        return rows[0]

    def _create_assessment_locked_for_tests(self, assessment, trace_row: dict | None = None):
        if assessment.trace_id is None:
            raise MlflowException.invalid_parameter_value("Assessment trace_id must be specified.")
        trace_row = trace_row or self._require_trace_row(assessment.trace_id)
        assessment.assessment_id = (
            assessment.assessment_id or iceberg_trace_backend_module.generate_assessment_id()
        )
        assessment.valid = assessment.valid if assessment.valid is not None else True
        assessment.create_time_ms = assessment.create_time_ms or int(
            iceberg_trace_backend_module.time.time() * 1000
        )
        assessment.last_update_time_ms = assessment.last_update_time_ms or assessment.create_time_ms

        rows = []
        replaced_assessment_ids = [assessment.assessment_id]
        if assessment.overrides:
            overridden_row = self._require_assessment_row(assessment.trace_id, assessment.overrides)
            overridden = self._assessment_row_to_entity(overridden_row)
            overridden.valid = False
            overridden.last_update_time_ms = int(iceberg_trace_backend_module.time.time() * 1000)
            replaced_assessment_ids.append(overridden.assessment_id)
            rows.append(
                self._assessment_row(
                    overridden,
                    experiment_id=trace_row["experiment_id"],
                    trace_row=trace_row,
                )
            )
        rows.append(
            self._assessment_row(
                assessment,
                experiment_id=trace_row["experiment_id"],
                trace_row=trace_row,
            )
        )
        _replace_assessment_rows_for_tests(self, replaced_assessment_ids, rows)
        return assessment

    def start_trace(self, trace_info):
        self.tracking_store._validate_trace_ingest_timestamp_ms(
            trace_info.request_time,
            parameter_name="trace_info.request_time",
        )
        with self._resources.write_lock:
            existing_row = next(iter(self._latest_trace_rows(trace_id=trace_info.trace_id)), None)
            row = self._build_trace_row_from_entity(trace_info, existing_row=existing_row)
            trace_tag_rows = self._trace_tag_rows(
                trace_id=row["trace_id"],
                experiment_id=row["experiment_id"],
                request_time_ms=row["request_time_ms"],
                current_tags=json.loads(row["tags_json"]),
            )
            _replace_trace_rows_for_tests(self, [row["trace_id"]], [row])
            _replace_trace_tag_rows_for_tests(self, [row["trace_id"]], trace_tag_rows)
            if trace_info.assessments:
                created_assessments = []
                for assessment in trace_info.assessments:
                    assessment.trace_id = assessment.trace_id or trace_info.trace_id
                    created_assessments.append(
                        self._create_assessment_locked_for_tests(assessment, trace_row=row)
                    )
                return self._trace_row_to_entity(row, assessments=created_assessments)
        return self._trace_row_to_entity(row, assessments=trace_info.assessments)

    def create_assessment(self, assessment):
        with self._resources.write_lock:
            if assessment.trace_id is None:
                raise MlflowException.invalid_parameter_value(
                    "Assessment trace_id must be specified."
                )
            trace_row = self._require_trace_row(assessment.trace_id)
            return self._create_assessment_locked_for_tests(assessment, trace_row=trace_row)

    def update_assessment(
        self,
        trace_id: str,
        assessment_id: str,
        name=None,
        expectation=None,
        feedback=None,
        rationale=None,
        metadata=None,
    ):
        with self._resources.write_lock:
            row = self._require_assessment_row(trace_id, assessment_id)
            trace_row = self._require_trace_row(trace_id)
            assessment = self._assessment_row_to_entity(row)
            if expectation is not None:
                if not isinstance(assessment, Expectation):
                    raise MlflowException.invalid_parameter_value(
                        "Cannot update expectation value on a Feedback assessment."
                    )
                assessment.value = expectation.value
            if feedback is not None:
                if not isinstance(assessment, Feedback):
                    raise MlflowException.invalid_parameter_value(
                        "Cannot update feedback value on an Expectation assessment."
                    )
                assessment.value = feedback.value
                assessment.error = feedback.error
                assessment.feedback.error = feedback.error
            if name is not None:
                assessment.name = name
            if rationale is not None:
                assessment.rationale = rationale
            if metadata is not None:
                assessment.metadata = {**(assessment.metadata or {}), **metadata}
            assessment.last_update_time_ms = int(iceberg_trace_backend_module.time.time() * 1000)
            _replace_assessment_rows_for_tests(
                self,
                [assessment_id],
                [
                    self._assessment_row(
                        assessment,
                        experiment_id=row["experiment_id"],
                        trace_row=trace_row,
                    )
                ],
            )
            return assessment

    def delete_assessment(self, trace_id: str, assessment_id: str):
        with self._resources.write_lock:
            rows = self._latest_assessment_rows(trace_id=trace_id, assessment_id=assessment_id)
            if not rows:
                return
            row = rows[0]
            trace_row = self._require_trace_row(trace_id)
            assessment = self._assessment_row_to_entity(row)
            rows_to_append = []
            if assessment.overrides:
                overridden_row = self._require_assessment_row(trace_id, assessment.overrides)
                overridden = self._assessment_row_to_entity(overridden_row)
                overridden.valid = True
                overridden.last_update_time_ms = int(
                    iceberg_trace_backend_module.time.time() * 1000
                )
                rows_to_append.append(
                    self._assessment_row(
                        overridden,
                        experiment_id=row["experiment_id"],
                        trace_row=trace_row,
                    )
                )
            replaced_assessment_ids = [assessment_id]
            if assessment.overrides:
                replaced_assessment_ids.append(assessment.overrides)
            _replace_assessment_rows_for_tests(self, replaced_assessment_ids, rows_to_append)

    def get_assessment(self, trace_id: str, assessment_id: str):
        return self._assessment_row_to_entity(self._require_assessment_row(trace_id, assessment_id))

    def set_trace_tag(self, trace_id: str, key: str, value: str):
        if key in self._reserved_hybrid_trace_tag_keys():
            raise MlflowException.invalid_parameter_value(
                f"Trace tag '{key}' is managed by the hybrid trace backend."
            )
        with self._resources.write_lock:
            row = self._require_trace_row(trace_id)
            tags = json.loads(row["tags_json"]) if row["tags_json"] else {}
            tags[key] = value
            row["trace_name"] = tags.get(TraceTagKey.TRACE_NAME)
            row["tags_json"] = json.dumps(tags, sort_keys=True)
            trace_tag_rows = self._trace_tag_rows(
                trace_id=row["trace_id"],
                experiment_id=row["experiment_id"],
                request_time_ms=row["request_time_ms"],
                current_tags=tags,
            )
            _replace_trace_rows_for_tests(self, [trace_id], [row])
            _replace_trace_tag_rows_for_tests(self, [trace_id], trace_tag_rows)

    def delete_trace_tag(self, trace_id: str, key: str):
        if key in self._reserved_hybrid_trace_tag_keys():
            raise MlflowException.invalid_parameter_value(
                f"Trace tag '{key}' is managed by the hybrid trace backend."
            )
        with self._resources.write_lock:
            row = self._require_trace_row(trace_id)
            tags = json.loads(row["tags_json"]) if row["tags_json"] else {}
            if key not in tags:
                raise MlflowException(
                    f"No trace tag with key '{key}' for trace with ID '{trace_id}'",
                    error_code=iceberg_trace_backend_module.RESOURCE_DOES_NOT_EXIST,
                )
            del tags[key]
            row["trace_name"] = tags.get(TraceTagKey.TRACE_NAME)
            row["tags_json"] = json.dumps(tags, sort_keys=True)
            trace_tag_rows = self._trace_tag_rows(
                trace_id=row["trace_id"],
                experiment_id=row["experiment_id"],
                request_time_ms=row["request_time_ms"],
                current_tags=tags,
            )
            _replace_trace_rows_for_tests(self, [trace_id], [row])
            _replace_trace_tag_rows_for_tests(self, [trace_id], trace_tag_rows)

    def delete_traces(
        self,
        experiment_id: str,
        max_timestamp_millis: int | None = None,
        max_traces: int | None = None,
        trace_ids=None,
    ):
        selected_trace_ids = self._select_cold_trace_ids_for_delete(
            experiment_id=experiment_id,
            max_timestamp_millis=max_timestamp_millis,
            max_traces=max_traces,
            trace_ids=trace_ids,
        )
        if not selected_trace_ids:
            return 0
        self._delete_iceberg_rows_for_trace_ids(selected_trace_ids)
        return len(selected_trace_ids)

    def log_spans(self, location: str, spans, tracking_uri=None):
        del tracking_uri
        if not spans:
            return spans

        grouped_spans: dict[str, list[Span]] = {}
        for span in spans:
            grouped_spans.setdefault(span.trace_id, []).append(span)
        for trace_id, trace_spans in grouped_spans.items():
            min_start_ms = min(span.start_time_ns for span in trace_spans) // 1_000_000
            self.tracking_store._validate_trace_ingest_timestamp_ms(
                min_start_ms,
                parameter_name=f"log_spans[{trace_id}].min_start_time_ms",
            )

        with self._resources.write_lock:
            existing_rows_by_trace_id = {
                trace_id: next(iter(self._latest_trace_rows(trace_id=trace_id)), None)
                for trace_id in grouped_spans
            }
            for trace_id, new_spans in grouped_spans.items():
                existing_row = existing_rows_by_trace_id[trace_id]
                latest_span_rows = self._latest_span_rows(trace_id=trace_id)
                latest_span_map = {
                    row["span_id"]: Span.from_dict(json.loads(row["span_json"]))
                    for row in latest_span_rows
                }
                for span in new_spans:
                    latest_span_map[span.span_id] = span
                merged_spans = sorted(
                    latest_span_map.values(),
                    key=lambda current_span: (current_span.start_time_ns, current_span.span_id),
                )
                trace_row = self._merge_trace_row_from_spans(
                    trace_id=trace_id,
                    experiment_id=location,
                    existing_row=existing_row,
                    current_spans=merged_spans,
                )
                current_span_rows = [
                    self._span_row(
                        span,
                        experiment_id=location,
                    )
                    for span in merged_spans
                ]
                trace_tag_rows = self._trace_tag_rows(
                    trace_id=trace_row["trace_id"],
                    experiment_id=trace_row["experiment_id"],
                    request_time_ms=trace_row["request_time_ms"],
                    current_tags=json.loads(trace_row["tags_json"]),
                )
                self._replace_span_rows_for_trace_ids([trace_id], current_span_rows)
                _replace_trace_rows_for_tests(self, [trace_id], [trace_row])
                _replace_trace_tag_rows_for_tests(self, [trace_id], trace_tag_rows)
        return spans

    async def log_spans_async(self, location: str, spans):
        return self.log_spans(location=location, spans=spans)


def _archive_root_uri(tmp_path: Path) -> str:
    archive_root = tmp_path / "archive"
    archive_root.mkdir(exist_ok=True)
    return archive_root.as_uri()


def _write_trace_archival_config(tmp_path: Path, *, max_trace_age: str = "24h") -> Path:
    archive_root = tmp_path / "archive-config"
    archive_root.mkdir(exist_ok=True)
    config_path = tmp_path / "trace-archival.yaml"
    config_path.write_text(
        "\n".join([
            "trace_archival:",
            "  enabled: true",
            f"  location: {archive_root.as_uri()}",
            "  retention: 30d",
            f"  max_trace_age: {max_trace_age}",
        ])
        + "\n",
        encoding="utf-8",
    )
    return config_path


def _tracking_trace_row_exists(store, trace_id: str) -> bool:
    with store.engine.connect() as connection:
        result = connection.execute(
            sa.text("SELECT 1 FROM trace_info WHERE request_id = :trace_id"),
            {"trace_id": trace_id},
        ).scalar_one_or_none()
    return result is not None


def _sql_rollup_counts(store) -> tuple[int, int, int]:
    with store.ManagedSessionMaker() as session:
        return (
            session.query(SqlTraceMetricDailyRollup).count(),
            session.query(SqlSpanCostDailyRollup).count(),
            session.query(SqlAssessmentDailyRollup).count(),
        )


@pytest.fixture
def routed_store(tmp_path: Path):
    artifact_uri = tmp_path / "artifacts"
    artifact_uri.mkdir()
    backend_store_uri = f"sqlite:///{tmp_path / 'tracking.db'}"
    store = ColdOnlyIcebergSqlAlchemyStore(backend_store_uri, artifact_uri.as_uri())
    try:
        yield store, tmp_path / "iceberg"
    finally:
        store._dispose_engine()
        store._clear_process_resources()


@pytest.fixture
def hybrid_routed_store(tmp_path: Path):
    artifact_uri = tmp_path / "artifacts"
    artifact_uri.mkdir()
    backend_store_uri = f"sqlite:///{tmp_path / 'tracking.db'}"
    store = IcebergSqlAlchemyStore(backend_store_uri, artifact_uri.as_uri())
    try:
        yield store, tmp_path / "iceberg"
    finally:
        store._dispose_engine()
        store._clear_process_resources()


def test_start_trace_persists_to_filesystem_backend(routed_store):
    store, iceberg_root = routed_store
    experiment_id = store.create_experiment("iceberg-start-trace")

    created = store.start_trace(
        TraceInfo(
            trace_id="tr-iceberg-start",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1234,
            execution_duration=56,
            state=TraceState.OK,
            tags={TraceTagKey.TRACE_NAME: "workflow_a", "custom_tag": "custom_value"},
            trace_metadata={TraceMetadataKey.SOURCE_RUN: "run-1"},
        )
    )

    fetched = store.get_trace_info(created.trace_id)
    assert fetched.trace_id == "tr-iceberg-start"
    assert fetched.request_time == 1234
    assert fetched.execution_duration == 56
    assert fetched.state == TraceState.OK
    assert fetched.tags[TraceTagKey.TRACE_NAME] == "workflow_a"
    assert fetched.tags[TraceTagKey.SPANS_LOCATION] == SpansLocation.TRACKING_STORE.value
    assert fetched.trace_metadata[TraceMetadataKey.SOURCE_RUN] == "run-1"
    assert fetched.trace_metadata[TraceMetadataKey.TRACE_INFO_FINALIZED] == "true"

    assert {"iceberg_tables", "iceberg_namespace_properties"} <= set(
        sa.inspect(store.engine).get_table_names()
    )
    assert any(path.is_file() for path in (iceberg_root / "warehouse").rglob("*"))


def test_hybrid_store_initializes_published_iceberg_cut(hybrid_routed_store):
    store, _ = hybrid_routed_store

    publication_state = store.get_iceberg_trace_publication_state()

    assert publication_state is not None
    assert publication_state["published_at_ms"] is not None
    assert publication_state["trace_index_metadata_location"] is not None
    assert publication_state["span_index_metadata_location"] is not None


def test_sqlalchemy_store_is_agnostic_of_iceberg():
    iceberg_methods = {
        "acquire_iceberg_trace_write_lock",
        "get_archived_trace_locator",
        "get_archived_trace_locators",
        "get_iceberg_trace_publication_state",
        "publish_archived_trace_batch",
        "_select_publishable_archived_trace_ids",
        "set_iceberg_trace_publication_state",
    }

    assert iceberg_methods.isdisjoint(vars(sqlalchemy_store_module.SqlAlchemyStore))


def test_routed_store_archives_traces_to_iceberg(monkeypatch, hybrid_routed_store, tmp_path: Path):
    store, _ = hybrid_routed_store
    backend = store
    experiment_id = store.create_experiment("iceberg-archive-trace")
    trace_id = "tr-archive-iceberg"
    archive_root_uri = _archive_root_uri(tmp_path)

    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_000,
            execution_duration=10,
            state=TraceState.OK,
            tags={TraceTagKey.TRACE_NAME: "workflow_archive"},
        )
    )
    store.log_spans(
        experiment_id,
        [create_test_span(trace_id, "root_span", span_id=111, trace_num=12345)],
    )
    store.create_assessment(
        Feedback(
            trace_id=trace_id,
            name="quality",
            value="yes",
            source=AssessmentSource(
                source_type=AssessmentSourceType.HUMAN,
                source_id="reviewer",
            ),
        )
    )
    assert [assessment.name for assessment in store.get_trace_info(trace_id).assessments] == [
        "quality"
    ]
    archival_data = store._load_trace_archival_data_batch([trace_id])
    assert [assessment.name for assessment in archival_data[trace_id][0].assessments] == ["quality"]
    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 120_000)
    archived = store.archive_traces(
        resolved_trace_archival_location=archive_root_uri,
        broader_retention="1m",
    )

    assert archived == 1

    assert not _tracking_trace_row_exists(store, trace_id)

    trace_info = store.get_trace_info(trace_id)
    assert trace_info.tags[TraceTagKey.SPANS_LOCATION] == SpansLocation.ARCHIVE_REPO.value
    assert trace_info.tags[TraceTagKey.ARCHIVE_LOCATION].startswith(f"{archive_root_uri}/")
    archive_uri_parts = trace_info.tags[TraceTagKey.ARCHIVE_LOCATION].rsplit("/", 2)
    assert archive_uri_parts[-1] == "artifacts"
    generation, attempt_id = archive_uri_parts[-2].split("-", 1)
    assert generation.isdigit()
    assert len(attempt_id) == 32

    trace = store.get_trace(trace_id)
    assert [span.name for span in trace.data.spans] == ["root_span"]
    assert [assessment.name for assessment in trace.info.assessments] == ["quality"]

    get_artifact_repository(trace_info.tags[TraceTagKey.ARCHIVE_LOCATION]).delete_artifacts(
        TRACE_ARCHIVAL_FILENAME
    )
    fallback_trace = store.get_trace(trace_id)
    assert fallback_trace.data.spans == []
    assert [assessment.name for assessment in fallback_trace.info.assessments] == ["quality"]

    cold_trace_rows = backend._latest_trace_rows(trace_id=trace_id)
    cold_span_rows = backend._latest_span_rows(trace_id=trace_id)
    assert len(cold_trace_rows) == 1
    assert len(cold_span_rows) == 1


def test_hybrid_archive_replaces_stale_locator_uri(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-stale-archive-locator")
    trace_id = "tr-stale-archive-locator"
    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [create_test_span(trace_id, "root", span_id=1, trace_num=1)],
    )
    with store.ManagedSessionMaker(read_only=False) as session:
        session.add(
            SqlArchivedTraceLocator(
                workspace=DEFAULT_WORKSPACE_NAME,
                experiment_id=int(experiment_id),
                trace_id=trace_id,
                request_time_ms=1_000,
                request_day=datetime.fromtimestamp(1, tz=timezone.utc).date(),
                archive_uri="file:///stale/archive",
            )
        )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 120_000)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=_archive_root_uri(tmp_path),
            broader_retention="1m",
        )
        == 1
    )

    locator = store.get_archived_trace_locator(trace_id)
    assert locator["archive_uri"] != "file:///stale/archive"
    assert not _tracking_trace_row_exists(store, trace_id)


def test_hybrid_archive_appends_distinct_experiments_in_parallel(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_ids = [
        store.create_experiment(f"iceberg-parallel-archive-{index}") for index in range(2)
    ]
    trace_ids = []
    for index, experiment_id in enumerate(experiment_ids):
        trace_id = f"tr-parallel-archive-{index}"
        trace_ids.append(trace_id)
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=1_000 + index,
                execution_duration=10,
                state=TraceState.OK,
            )
        )
        store.log_spans(
            experiment_id,
            [create_test_span(trace_id, "root", span_id=index + 1, trace_num=index + 1)],
        )

    append_barrier = threading.Barrier(2)
    appended_experiment_ids = []
    original_append = store._append_archived_trace_projection_rows

    def append_in_parallel(projections):
        appended_experiment_ids.append(projections[0].trace_row["experiment_id"])
        append_barrier.wait(timeout=10)
        return original_append(projections)

    monkeypatch.setenv(MLFLOW_ICEBERG_TRACE_ARCHIVE_EXPERIMENT_MAX_WORKERS.name, "2")
    monkeypatch.setenv(MLFLOW_ICEBERG_TRACE_ARCHIVE_MAX_WORKERS.name, "1")
    monkeypatch.setenv(MLFLOW_ICEBERG_TRACE_ARCHIVE_PROJECT_BATCH_SIZE.name, "1")
    monkeypatch.setattr(store, "_append_archived_trace_projection_rows", append_in_parallel)
    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 120_000)

    assert (
        store.archive_traces(
            resolved_trace_archival_location=_archive_root_uri(tmp_path),
            broader_retention="1m",
            max_traces_per_pass=2,
        )
        == 2
    )
    assert set(appended_experiment_ids) == set(experiment_ids)
    assert all(not _tracking_trace_row_exists(store, trace_id) for trace_id in trace_ids)
    assert {trace_info.trace_id for trace_info in store.batch_get_trace_infos(trace_ids)} == set(
        trace_ids
    )


def test_hybrid_archive_coalesces_rollup_refresh_across_projection_chunks(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-coalesced-rollup-refresh")
    trace_ids = []
    for index in range(3):
        trace_id = f"tr-coalesced-rollup-{index}"
        trace_ids.append(trace_id)
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=1_000 + index,
                execution_duration=10,
                state=TraceState.OK,
            )
        )
        store.log_spans(
            experiment_id,
            [create_test_span(trace_id, "root", span_id=index + 1, trace_num=index + 1)],
        )

    append_calls = []
    refresh_calls = []
    publication_calls = []
    original_append = store._append_archived_trace_projection_rows
    original_refresh = store._refresh_rollup_tables
    original_publish = store.publish_archived_trace_batch

    def record_append(projections):
        append_calls.append(projections)
        return original_append(projections)

    def record_refresh(**kwargs):
        refresh_calls.append(kwargs)
        return original_refresh(**kwargs)

    def record_publish(**kwargs):
        publication_calls.append(kwargs)
        return original_publish(**kwargs)

    monkeypatch.setenv(MLFLOW_ICEBERG_TRACE_ARCHIVE_MAX_WORKERS.name, "1")
    monkeypatch.setenv(MLFLOW_ICEBERG_TRACE_ARCHIVE_PROJECT_BATCH_SIZE.name, "1")
    monkeypatch.setenv(MLFLOW_ICEBERG_TRACE_ARCHIVE_ROLLUP_REFRESH_CHUNKS.name, "10")
    monkeypatch.setattr(store, "_append_archived_trace_projection_rows", record_append)
    monkeypatch.setattr(store, "_refresh_rollup_tables", record_refresh)
    monkeypatch.setattr(store, "publish_archived_trace_batch", record_publish)
    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 120_000)

    assert (
        store.archive_traces(
            resolved_trace_archival_location=_archive_root_uri(tmp_path),
            broader_retention="1m",
            max_traces_per_pass=3,
        )
        == 3
    )
    assert len(append_calls) == 1
    assert len(append_calls[0]) == 3
    assert len(refresh_calls) == 1
    assert len(publication_calls) == 1
    assert all(not _tracking_trace_row_exists(store, trace_id) for trace_id in trace_ids)
    assert {trace_info.trace_id for trace_info in store.batch_get_trace_infos(trace_ids)} == set(
        trace_ids
    )


def test_hybrid_archive_shared_rollup_refresh_failure_rolls_back_group(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-shared-rollup-refresh-failure")
    trace_ids = []
    for index in range(2):
        trace_id = f"tr-shared-rollup-failure-{index}"
        trace_ids.append(trace_id)
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=1_000 + index,
                execution_duration=10,
                state=TraceState.OK,
            )
        )
        store.log_spans(
            experiment_id,
            [create_test_span(trace_id, "root", span_id=index + 1, trace_num=index + 1)],
        )

    refresh_calls = 0
    publication_calls = []
    original_refresh = store._refresh_rollup_tables

    def fail_first_refresh(**kwargs):
        nonlocal refresh_calls
        refresh_calls += 1
        if refresh_calls == 1:
            raise RuntimeError("shared rollup refresh failed")
        return original_refresh(**kwargs)

    monkeypatch.setenv(MLFLOW_ICEBERG_TRACE_ARCHIVE_MAX_WORKERS.name, "1")
    monkeypatch.setenv(MLFLOW_ICEBERG_TRACE_ARCHIVE_PROJECT_BATCH_SIZE.name, "1")
    monkeypatch.setenv(MLFLOW_ICEBERG_TRACE_ARCHIVE_ROLLUP_REFRESH_CHUNKS.name, "10")
    monkeypatch.setattr(store, "_refresh_rollup_tables", fail_first_refresh)
    monkeypatch.setattr(
        store,
        "publish_archived_trace_batch",
        lambda **kwargs: publication_calls.append(kwargs),
    )
    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 120_000)

    assert (
        store.archive_traces(
            resolved_trace_archival_location=_archive_root_uri(tmp_path),
            broader_retention="1m",
            max_traces_per_pass=2,
        )
        == 0
    )
    assert refresh_calls == 2
    assert publication_calls == []
    assert all(_tracking_trace_row_exists(store, trace_id) for trace_id in trace_ids)
    assert [store.get_trace_info(trace_id).trace_id for trace_id in trace_ids] == trace_ids


def test_hybrid_archive_isolates_experiment_stage_failures(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_ids = [
        store.create_experiment(f"iceberg-stage-failure-{index}") for index in range(2)
    ]
    trace_ids = [f"tr-stage-failure-{index}" for index in range(2)]
    for index, (experiment_id, trace_id) in enumerate(zip(experiment_ids, trace_ids)):
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=1_000 + index,
                execution_duration=10,
                state=TraceState.OK,
            )
        )
        store.log_spans(
            experiment_id,
            [create_test_span(trace_id, "root", span_id=index + 1, trace_num=index + 1)],
        )

    original_load = store._load_trace_archival_data_batch

    def load_with_one_failure(batch_trace_ids):
        if trace_ids[0] in batch_trace_ids:
            raise sa.exc.SQLAlchemyError("load failed")
        return original_load(batch_trace_ids)

    monkeypatch.setenv(MLFLOW_ICEBERG_TRACE_ARCHIVE_EXPERIMENT_MAX_WORKERS.name, "2")
    monkeypatch.setenv(MLFLOW_ICEBERG_TRACE_ARCHIVE_PROJECT_BATCH_SIZE.name, "1")
    monkeypatch.setattr(store, "_load_trace_archival_data_batch", load_with_one_failure)
    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 120_000)

    assert (
        store.archive_traces(
            resolved_trace_archival_location=_archive_root_uri(tmp_path),
            broader_retention="1m",
            max_traces_per_pass=2,
        )
        == 1
    )
    assert _tracking_trace_row_exists(store, trace_ids[0])
    assert not _tracking_trace_row_exists(store, trace_ids[1])
    assert store.get_trace_info(trace_ids[1]).trace_id == trace_ids[1]


def test_archive_trace_workers_preserve_workspace_context(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    observed_workspaces = []
    candidates = [
        iceberg_trace_backend_module._TraceArchiveCandidate(
            trace_id=f"tr-workspace-context-{index}",
            experiment_id="1",
            timestamp_ms=index,
        )
        for index in range(2)
    ]

    def record_workspace(_trace_id, **_kwargs):
        observed_workspaces.append(store._get_active_workspace())
        return None

    monkeypatch.setenv(MLFLOW_ENABLE_WORKSPACES.name, "true")
    monkeypatch.setattr(store, "_stage_archived_trace_projection", record_workspace)
    with WorkspaceContext("team-a"):
        store._stage_archived_trace_projection_chunk(
            chunk=candidates,
            archival_data_by_trace_id={},
            resolved_trace_archival_location=_archive_root_uri(tmp_path),
            archive_now_experiment_ids=set(),
            retryable_failure_experiment_ids=set(),
            max_workers=2,
        )

    assert observed_workspaces == ["team-a", "team-a"]


def test_archive_trace_workers_isolate_unexpected_projection_errors(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    candidates = [
        iceberg_trace_backend_module._TraceArchiveCandidate(
            trace_id=f"tr-projection-error-{index}",
            experiment_id="1",
            timestamp_ms=index,
        )
        for index in range(2)
    ]
    successful_projection = object()

    def stage_projection(trace_id, **_kwargs):
        if trace_id == candidates[1].trace_id:
            raise ValueError("invalid projection")
        return successful_projection

    monkeypatch.setattr(store, "_stage_archived_trace_projection", stage_projection)
    projections = store._stage_archived_trace_projection_chunk(
        chunk=candidates,
        archival_data_by_trace_id={},
        resolved_trace_archival_location=_archive_root_uri(tmp_path),
        archive_now_experiment_ids=set(),
        retryable_failure_experiment_ids=set(),
        max_workers=2,
    )

    assert projections == [successful_projection]


def test_hybrid_archive_respects_broader_retention(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-archive-retention")
    archive_root_uri = _archive_root_uri(tmp_path)
    day_ms = 24 * 60 * 60 * 1000
    now_ms = 20 * day_ms
    old_trace_id = "tr-retention-old"
    recent_trace_id = "tr-retention-recent"

    for trace_id, request_time_ms in [
        (old_trace_id, now_ms - 11 * day_ms),
        (recent_trace_id, now_ms - 9 * day_ms),
    ]:
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=request_time_ms,
                execution_duration=10,
                state=TraceState.OK,
            )
        )
        store.log_spans(
            experiment_id,
            [
                create_test_span(
                    trace_id,
                    f"{trace_id}_span",
                    span_id=request_time_ms,
                    trace_num=request_time_ms,
                    start_ns=request_time_ms * 1_000_000,
                    end_ns=(request_time_ms + 10) * 1_000_000,
                )
            ],
        )

    def fail_if_span_to_dict_is_called(_span):
        raise AssertionError("archival should reuse loaded span dictionaries")

    def fail_if_replace_is_called(*_args, **_kwargs):
        raise AssertionError("archival should append Iceberg rows without delete+append replace")

    monkeypatch.setattr(Span, "to_dict", fail_if_span_to_dict_is_called)
    monkeypatch.setattr(store, "_replace_span_rows_for_trace_ids", fail_if_replace_is_called)
    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: now_ms)
    archived = store.archive_traces(
        resolved_trace_archival_location=archive_root_uri,
        broader_retention="10d",
    )

    assert archived == 1
    assert not _tracking_trace_row_exists(store, old_trace_id)
    assert _tracking_trace_row_exists(store, recent_trace_id)
    assert (
        store.get_trace_info(old_trace_id).tags[TraceTagKey.SPANS_LOCATION]
        == SpansLocation.ARCHIVE_REPO.value
    )


def test_hybrid_archive_now_overrides_broader_retention(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-archive-now")
    trace_id = "tr-iceberg-archive-now"
    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=100_000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [create_test_span(trace_id, "root", span_id=1, trace_num=1)],
    )
    store.set_experiment_tag(
        experiment_id,
        ExperimentTag(TraceExperimentTagKey.ARCHIVE_NOW, json.dumps({})),
    )
    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 120_000)

    assert (
        store.archive_traces(
            resolved_trace_archival_location=_archive_root_uri(tmp_path),
            broader_retention="10d",
        )
        == 1
    )
    assert not _tracking_trace_row_exists(store, trace_id)
    assert TraceExperimentTagKey.ARCHIVE_NOW not in store.get_experiment(experiment_id).tags


def test_s3_trace_archive_payload_uploader_reuses_client(monkeypatch):
    from mlflow.store.artifact.s3_artifact_repo import S3ArtifactRepository

    uploads = []
    client_calls = {"count": 0}

    class FakeS3Client:
        def upload_fileobj(self, *, Fileobj, Bucket, Key, ExtraArgs):
            uploads.append({
                "bucket": Bucket,
                "key": Key,
                "extra_args": ExtraArgs,
                "data": Fileobj.read(),
            })

    fake_client = FakeS3Client()

    def get_s3_client(_repo):
        client_calls["count"] += 1
        return fake_client

    monkeypatch.setattr(S3ArtifactRepository, "_get_s3_client", get_s3_client)

    uploader = iceberg_trace_backend_module._S3TraceArchivePayloadUploader.from_archive_root(
        "s3://archive-bucket/archive-root"
    )
    assert uploader is not None
    assert client_calls["count"] == 1

    uploader.upload_archived_trace_data_bytes(
        "s3://archive-bucket/archive-root/1/traces/tr-one/artifacts", b"one"
    )
    uploader.upload_archived_trace_data_bytes(
        "s3://archive-bucket/archive-root/1/traces/tr-two/artifacts", b"two"
    )

    assert uploads == [
        {
            "bucket": "archive-bucket",
            "key": "archive-root/1/traces/tr-one/artifacts/traces.pb",
            "extra_args": {"ContentType": "application/octet-stream"},
            "data": b"one",
        },
        {
            "bucket": "archive-bucket",
            "key": "archive-root/1/traces/tr-two/artifacts/traces.pb",
            "extra_args": {"ContentType": "application/octet-stream"},
            "data": b"two",
        },
    ]


def test_hybrid_archive_updates_publication_state_and_locator(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-publication-state")
    archive_root_uri = _archive_root_uri(tmp_path)
    trace_id = "tr-publication-state"

    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [create_test_span(trace_id, "root_span", span_id=111, trace_num=12345)],
    )

    original_publish = store.publish_archived_trace_batch
    original_bulk_upsert = iceberg_trace_backend_module._bulk_upsert
    publish_attempts = 0
    published_batches = []
    locator_batches = []

    def publish_with_one_conflict(**kwargs):
        nonlocal publish_attempts
        publish_attempts += 1
        published_batches.append([trace["trace_id"] for trace in kwargs["traces"]])
        if publish_attempts == 1:
            return None
        return original_publish(**kwargs)

    def record_bulk_upsert(session, model_class, rows):
        if model_class is SqlArchivedTraceLocator:
            locator_batches.append(rows)
        return original_bulk_upsert(session, model_class, rows)

    monkeypatch.setattr(store, "publish_archived_trace_batch", publish_with_one_conflict)
    monkeypatch.setattr(iceberg_trace_backend_module, "_bulk_upsert", record_bulk_upsert)
    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 120_000)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=archive_root_uri,
            broader_retention="1m",
        )
        >= 1
    )
    assert publish_attempts >= 2
    assert published_batches[0] == published_batches[1]

    assert not _tracking_trace_row_exists(store, trace_id)

    publication_state = store.get_iceberg_trace_publication_state()
    assert publication_state is not None
    assert publication_state["trace_index_metadata_location"] is not None
    assert publication_state["trace_tag_index_metadata_location"] is not None
    assert publication_state["span_index_metadata_location"] is not None
    assert publication_state["assessment_index_metadata_location"] is not None
    assert publication_state["trace_metric_daily_rollups_metadata_location"] is not None
    assert publication_state["span_cost_daily_rollups_metadata_location"] is not None
    assert publication_state["assessment_daily_rollups_metadata_location"] is not None
    assert publication_state["published_at_ms"] is not None

    locator = store.get_archived_trace_locator(trace_id)
    assert locator is not None
    assert locator["trace_id"] == trace_id
    assert locator["experiment_id"] == int(experiment_id)
    assert locator["archive_uri"] is not None
    assert len(locator_batches) == 1
    assert [row["trace_id"] for row in locator_batches[0]] == [trace_id]
    assert locator_batches[0][0]["published_at_ms"] == publication_state["published_at_ms"]

    locators = store.get_archived_trace_locators([trace_id, "missing"])
    assert set(locators) == {trace_id}
    assert locators[trace_id]["trace_id"] == trace_id

    publication_result = store.publish_archived_trace_batch(
        traces=[{"trace_id": trace_id}],
        metadata_locations={},
        expected_published_at_ms=publication_state["published_at_ms"],
    )
    assert publication_result == ([], [trace_id])

    class ArtifactRepoThatMustNotDelete:
        def delete_artifacts(self, _artifact_path):
            raise AssertionError("a published archive payload must not be deleted")

    store._delete_unreferenced_archived_trace_payload(
        trace_id=trace_id,
        artifact_uri=locator["archive_uri"],
        artifact_repo=ArtifactRepoThatMustNotDelete(),
    )


def test_batch_get_trace_infos_uses_archived_locator_batch_lookup(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-batch-locator")
    archive_root_uri = _archive_root_uri(tmp_path)
    trace_id = "tr-batch-locator"

    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [create_test_span(trace_id, "root_span", span_id=211, trace_num=22345)],
    )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 120_000)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=archive_root_uri,
            broader_retention="1m",
        )
        >= 1
    )

    original = store.get_archived_trace_locators
    calls: list[list[str]] = []

    def wrapped(trace_ids: list[str]):
        calls.append(list(trace_ids))
        return original(trace_ids)

    monkeypatch.setattr(store, "get_archived_trace_locators", wrapped)

    trace_infos = store.batch_get_trace_infos([trace_id])
    assert [trace_info.trace_id for trace_info in trace_infos] == [trace_id]
    assert calls == [[trace_id]]


def test_archived_trace_publication_rejects_stale_cut(hybrid_routed_store):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-stale-publication-cut")
    trace_id = "tr-stale-publication-cut"
    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [create_test_span(trace_id, "root", span_id=1, trace_num=1)],
    )
    generation = store._load_trace_archival_data_batch([trace_id])[trace_id][1]
    publication_state = store.get_iceberg_trace_publication_state()
    stale_published_at_ms = (
        publication_state["published_at_ms"] + 1 if publication_state is not None else 1
    )

    result = store.publish_archived_trace_batch(
        traces=[{"trace_id": trace_id, "db_payload_generation": generation}],
        metadata_locations={},
        expected_published_at_ms=stale_published_at_ms,
    )

    assert result is None
    assert _tracking_trace_row_exists(store, trace_id)
    assert store.get_archived_trace_locator(trace_id) is None


def test_archived_trace_publication_rejects_partially_stale_batch(hybrid_routed_store):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-partially-stale-publication")
    trace_ids = ["tr-current-generation", "tr-stale-generation"]
    for index, trace_id in enumerate(trace_ids):
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=1_000 + index,
                execution_duration=10,
                state=TraceState.OK,
            )
        )
        store.log_spans(
            experiment_id,
            [create_test_span(trace_id, "root", span_id=index + 1, trace_num=index + 1)],
        )
    archival_data = store._load_trace_archival_data_batch(trace_ids)
    traces = [
        {"trace_id": trace_id, "db_payload_generation": archival_data[trace_id][1]}
        for trace_id in trace_ids
    ]
    store.log_spans(
        experiment_id,
        [create_test_span(trace_ids[1], "late", span_id=3, trace_num=3)],
    )
    publication_state = store.get_iceberg_trace_publication_state()
    metadata_locations, snapshot_ids = store._current_iceberg_state()

    result = store.publish_archived_trace_batch(
        traces=traces,
        metadata_locations=metadata_locations,
        snapshot_ids=snapshot_ids,
        expected_published_at_ms=publication_state["published_at_ms"],
    )

    assert result is None
    assert all(_tracking_trace_row_exists(store, trace_id) for trace_id in trace_ids)
    assert store.get_archived_trace_locators(trace_ids) == {}
    assert (
        store.get_iceberg_trace_publication_state()["published_at_ms"]
        == publication_state["published_at_ms"]
    )


@pytest.mark.parametrize("mutation", ["tag", "assessment"])
def test_archived_trace_publication_rejects_stale_metadata_generation(
    hybrid_routed_store, mutation
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment(f"iceberg-stale-{mutation}-publication")
    trace_id = f"tr-stale-{mutation}-publication"
    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [create_test_span(trace_id, "root", span_id=1, trace_num=1)],
    )
    generation = store._load_trace_archival_data_batch([trace_id])[trace_id][1]
    if mutation == "tag":
        store.set_trace_tag(trace_id, "environment", "production")
    else:
        store.create_assessment(
            Feedback(
                trace_id=trace_id,
                name="quality",
                value=True,
                source=AssessmentSource(
                    source_type=AssessmentSourceType.HUMAN,
                    source_id="judge",
                ),
            )
        )
    publication_state = store.get_iceberg_trace_publication_state()

    result = store.publish_archived_trace_batch(
        traces=[
            {
                "trace_id": trace_id,
                "experiment_id": experiment_id,
                "db_payload_generation": generation,
            }
        ],
        metadata_locations={},
        expected_published_at_ms=publication_state["published_at_ms"],
    )

    assert result is None
    assert _tracking_trace_row_exists(store, trace_id)


def test_archive_cleans_appended_rows_when_publication_retries_fail(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-publication-retry-cleanup")
    trace_id = "tr-publication-retry-cleanup"
    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [create_test_span(trace_id, "root", span_id=1, trace_num=1)],
    )
    cleanup_calls = []
    original_cleanup = store._cleanup_unpublished_archived_projections

    def record_cleanup(projections):
        cleanup_calls.append([projection.trace_id for projection in projections])
        return original_cleanup(projections)

    monkeypatch.setattr(store, "_cleanup_unpublished_archived_projections", record_cleanup)
    monkeypatch.setattr(store, "publish_archived_trace_batch", lambda **_kwargs: None)
    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 120_000)

    archived = store.archive_traces(
        resolved_trace_archival_location=_archive_root_uri(tmp_path),
        broader_retention="1m",
    )

    assert archived == 0
    assert cleanup_calls == [[trace_id]]
    assert _tracking_trace_row_exists(store, trace_id)
    assert store._latest_trace_rows(trace_id=trace_id) == []
    for table_name in (
        iceberg_trace_backend_module._TRACE_METRIC_DAILY_ROLLUPS_TABLE,
        iceberg_trace_backend_module._SPAN_COST_DAILY_ROLLUPS_TABLE,
        iceberg_trace_backend_module._ASSESSMENT_DAILY_ROLLUPS_TABLE,
    ):
        assert {row["metric_name"] for row in _read_iceberg_table_rows(store, table_name)} <= {
            iceberg_trace_backend_module._ROLLUP_COVERAGE_METRIC
        }


def test_publication_fencing_token_is_monotonic(monkeypatch, hybrid_routed_store):
    store, _ = hybrid_routed_store
    monkeypatch.setattr(iceberg_trace_backend_module, "get_current_time_millis", lambda: 100)
    initial_state = store.get_iceberg_trace_publication_state()
    initial_published_at_ms = initial_state["published_at_ms"] if initial_state else None

    assert store.set_iceberg_trace_publication_state(
        metadata_locations={}, expected_published_at_ms=initial_published_at_ms
    )
    first_state = store.get_iceberg_trace_publication_state()
    assert first_state["published_at_ms"] == max(100, (initial_published_at_ms or 0) + 1)

    assert store.set_iceberg_trace_publication_state(
        metadata_locations={}, expected_published_at_ms=first_state["published_at_ms"]
    )
    second_state = store.get_iceberg_trace_publication_state()
    assert second_state["published_at_ms"] == first_state["published_at_ms"] + 1

    assert not store.set_iceberg_trace_publication_state(
        metadata_locations={}, expected_published_at_ms=initial_published_at_ms
    )
    assert (
        store.get_iceberg_trace_publication_state()["published_at_ms"]
        == second_state["published_at_ms"]
    )


def test_archival_preflight_batches_span_presence_query(hybrid_routed_store):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-batched-span-preflight")
    trace_ids = ["tr-preflight-one", "tr-preflight-two"]
    for index, trace_id in enumerate(trace_ids):
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=1_000 + index,
                execution_duration=10,
                state=TraceState.OK,
            )
        )
        store.log_spans(
            experiment_id,
            [create_test_span(trace_id, f"span-{index}", span_id=index + 1, trace_num=index + 1)],
        )
    archival_data = store._load_trace_archival_data_batch(trace_ids)
    traces = [
        {"trace_id": trace_id, "db_payload_generation": archival_data[trace_id][1]}
        for trace_id in trace_ids
    ]
    span_selects = []

    def record_span_select(_conn, _cursor, statement, _parameters, _context, _executemany):
        normalized_statement = " ".join(statement.lower().split())
        if " from spans " in f" {normalized_statement} ":
            span_selects.append(normalized_statement)

    sa.event.listen(store.engine, "before_cursor_execute", record_span_select)
    try:
        publishable_trace_ids = store._select_publishable_archived_trace_ids(traces=traces)
    finally:
        sa.event.remove(store.engine, "before_cursor_execute", record_span_select)

    assert publishable_trace_ids == trace_ids
    assert len(span_selects) == 1


def test_store_rejects_stale_trace_writes(monkeypatch, routed_store, tmp_path: Path):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-stale-trace-age")
    config_path = _write_trace_archival_config(tmp_path, max_trace_age="1h")
    monkeypatch.setenv(MLFLOW_TRACE_ARCHIVAL_CONFIG.name, str(config_path))
    monkeypatch.setattr(trace_archival_config_module, "_TRACE_ARCHIVAL_SERVER_CONFIG_CACHE", None)

    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    stale_ms = now_ms - 2 * 60 * 60 * 1000

    with pytest.raises(MlflowException, match="must not be older"):
        store.start_trace(
            TraceInfo(
                trace_id="tr-stale-start",
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=stale_ms,
                execution_duration=10,
                state=TraceState.OK,
            )
        )

    with pytest.raises(MlflowException, match="must not be older"):
        store.log_spans(
            experiment_id,
            [
                create_test_span(
                    "tr-stale-span",
                    "old_span",
                    span_id=311,
                    trace_num=33333,
                    start_ns=stale_ms * 1_000_000,
                    end_ns=(stale_ms + 10) * 1_000_000,
                )
            ],
        )


def test_hybrid_search_traces_merges_hot_and_cold_span_attribute_filters(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-hybrid-search")
    archive_root_uri = _archive_root_uri(tmp_path)

    store.start_trace(
        TraceInfo(
            trace_id="tr-archived-match",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "tr-archived-match",
                "archived_span",
                span_id=111,
                trace_num=12345,
                start_ns=1_000 * 1_000_000,
                end_ns=1_010 * 1_000_000,
                attributes={"model": "gpt-4"},
            )
        ],
    )

    store.start_trace(
        TraceInfo(
            trace_id="tr-hot-match",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=19_000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "tr-hot-match",
                "hot_span",
                span_id=222,
                trace_num=23456,
                start_ns=19_000 * 1_000_000,
                end_ns=19_010 * 1_000_000,
                attributes={"model": "gpt-4o"},
            )
        ],
    )

    store.start_trace(
        TraceInfo(
            trace_id="tr-hot-miss",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=19_500,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "tr-hot-miss",
                "miss_span",
                span_id=333,
                trace_num=34567,
                start_ns=19_500 * 1_000_000,
                end_ns=19_510 * 1_000_000,
                attributes={"model": "claude-3"},
            )
        ],
    )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 122_000)
    archived = store.archive_traces(
        resolved_trace_archival_location=archive_root_uri,
        broader_retention="2m",
    )
    assert archived >= 1

    store.start_trace(
        TraceInfo(
            trace_id="tr-hot-post-archive-match",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=122_000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "tr-hot-post-archive-match",
                "post_archive_hot_span",
                span_id=444,
                trace_num=45678,
                start_ns=122_000 * 1_000_000,
                end_ns=122_010 * 1_000_000,
                attributes={"model": "gpt-4.1"},
            )
        ],
    )

    trace_infos, next_token = store.search_traces(
        locations=[experiment_id],
        filter_string='span.attributes.model LIKE "%gpt-4%"',
        order_by=["timestamp DESC"],
        max_results=10,
    )

    assert [trace_info.trace_id for trace_info in trace_infos] == [
        "tr-hot-post-archive-match",
        "tr-hot-match",
        "tr-archived-match",
    ]
    assert next_token is None


def test_hybrid_find_completed_sessions_merges_archived_first_trace_filters(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-hybrid-sessions")
    archive_root_uri = _archive_root_uri(tmp_path)

    store.start_trace(
        TraceInfo(
            trace_id="tr-archived-session-first",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_000,
            execution_duration=10,
            state=TraceState.OK,
            trace_metadata={TraceMetadataKey.TRACE_SESSION: "session-archived"},
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "tr-archived-session-first",
                "archived_session_first",
                span_id=551,
                trace_num=551,
                start_ns=1_000 * 1_000_000,
                end_ns=1_010 * 1_000_000,
                attributes={"model": "gpt-4"},
            )
        ],
    )
    store.start_trace(
        TraceInfo(
            trace_id="tr-archived-session-second",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=2_000,
            execution_duration=10,
            state=TraceState.OK,
            trace_metadata={TraceMetadataKey.TRACE_SESSION: "session-archived"},
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "tr-archived-session-second",
                "archived_session_second",
                span_id=552,
                trace_num=552,
                start_ns=2_000 * 1_000_000,
                end_ns=2_010 * 1_000_000,
                attributes={"model": "other"},
            )
        ],
    )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 122_000)
    archived = store.archive_traces(
        resolved_trace_archival_location=archive_root_uri,
        broader_retention="2m",
    )
    assert archived >= 2

    store.start_trace(
        TraceInfo(
            trace_id="tr-hot-session-first",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=122_000,
            execution_duration=10,
            state=TraceState.OK,
            trace_metadata={TraceMetadataKey.TRACE_SESSION: "session-hot"},
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "tr-hot-session-first",
                "hot_session_first",
                span_id=661,
                trace_num=661,
                start_ns=122_000 * 1_000_000,
                end_ns=122_010 * 1_000_000,
                attributes={"model": "gpt-4.1"},
            )
        ],
    )

    monkeypatch.setattr(
        store,
        "_collect_trace_infos_from_search",
        lambda *args, **kwargs: pytest.fail("session query must aggregate in DuckDB"),
    )
    sessions = store.find_completed_sessions(
        experiment_id=experiment_id,
        min_last_trace_timestamp_ms=0,
        max_last_trace_timestamp_ms=200_000,
        filter_string='span.attributes.model LIKE "%gpt-4%"',
    )

    assert [session.session_id for session in sessions] == ["session-archived", "session-hot"]
    assert sessions[0].first_trace_timestamp_ms == 1_000
    assert sessions[0].last_trace_timestamp_ms == 2_000


def test_hybrid_delete_traces_handles_iceberg_archived_traces(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    backend = store
    experiment_id = store.create_experiment("iceberg-hybrid-delete")
    archive_root_uri = _archive_root_uri(tmp_path)

    store.start_trace(
        TraceInfo(
            trace_id="tr-archived-delete",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "tr-archived-delete",
                "archived_delete_span",
                span_id=771,
                trace_num=771,
                start_ns=1_000 * 1_000_000,
                end_ns=1_010 * 1_000_000,
                attributes={"model": "gpt-delete"},
            )
        ],
    )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 121_000)
    archived = store.archive_traces(
        resolved_trace_archival_location=archive_root_uri,
        broader_retention="2m",
    )
    assert archived >= 1

    trace_info = store.get_trace_info("tr-archived-delete")
    assert trace_info.tags[TraceTagKey.ARCHIVE_LOCATION].startswith(f"{archive_root_uri}/")
    traces_before_delete, _ = store.search_traces(
        locations=[experiment_id],
        filter_string='span.attributes.model LIKE "%gpt%"',
        max_results=10,
    )
    assert [trace.trace_id for trace in traces_before_delete] == ["tr-archived-delete"]

    assert store.delete_traces(experiment_id=experiment_id, trace_ids=["tr-archived-delete"]) == 1
    with pytest.raises(MlflowException, match="Trace with ID tr-archived-delete is not found."):
        store.get_trace_info("tr-archived-delete")
    assert backend._latest_trace_rows(trace_id="tr-archived-delete") == []
    traces_after_delete, _ = store.search_traces(
        locations=[experiment_id],
        filter_string='span.attributes.model LIKE "%gpt%"',
        max_results=10,
    )
    assert traces_after_delete == []


def test_hybrid_trace_filter_correlation_merges_archived_span_payload_filters(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-hybrid-correlation")
    archive_root_uri = _archive_root_uri(tmp_path)

    store.start_trace(
        TraceInfo(
            trace_id="tr-archived-joint",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_000,
            execution_duration=10,
            state=TraceState.OK,
            tags={"group": "match"},
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "tr-archived-joint",
                "archived_joint_span",
                span_id=881,
                trace_num=881,
                start_ns=1_000 * 1_000_000,
                end_ns=1_010 * 1_000_000,
                attributes={"model": "gpt-4"},
            )
        ],
    )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 121_000)
    archived = store.archive_traces(
        resolved_trace_archival_location=archive_root_uri,
        broader_retention="2m",
    )
    assert archived >= 1

    for trace_id, request_time, group, model in [
        ("tr-hot-joint", 122_000, "match", "gpt-4.1"),
        ("tr-hot-filter1-only", 123_000, "other", "gpt-4o"),
        ("tr-hot-filter2-only", 124_000, "match", "claude-3"),
    ]:
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=request_time,
                execution_duration=10,
                state=TraceState.OK,
                tags={"group": group},
            )
        )
        store.log_spans(
            experiment_id,
            [
                create_test_span(
                    trace_id,
                    f"{trace_id}_span",
                    span_id=request_time // 1000,
                    trace_num=request_time // 1000,
                    start_ns=request_time * 1_000_000,
                    end_ns=(request_time + 10) * 1_000_000,
                    attributes={"model": model},
                )
            ],
        )

    result = store.calculate_trace_filter_correlation(
        experiment_ids=[experiment_id],
        filter_string1='span.attributes.model LIKE "%gpt-4%"',
        filter_string2='tag.group = "match"',
    )

    assert result.total_count == 4
    assert result.filter1_count == 3
    assert result.filter2_count == 3
    assert result.joint_count == 2


def test_hybrid_reused_trace_id_does_not_pull_old_iceberg_spans_or_assessments(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-hybrid-trace-id-reuse")
    trace_id = "shared-trace-id"
    source = AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="judge")
    archive_root_uri = _archive_root_uri(tmp_path)

    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                trace_id,
                "old-span",
                span_id=991,
                trace_num=991,
                start_ns=1_000 * 1_000_000,
                end_ns=1_010 * 1_000_000,
                attributes={"model": "old-model"},
            )
        ],
    )
    store.create_assessment(
        Feedback(trace_id=trace_id, name="old_quality", value="old", source=source)
    )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 121_000)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=archive_root_uri,
            broader_retention="2m",
        )
        >= 1
    )
    assert store.delete_traces(experiment_id=experiment_id, trace_ids=[trace_id]) == 1

    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=2_000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                trace_id,
                "new-span",
                span_id=992,
                trace_num=992,
                start_ns=2_000 * 1_000_000,
                end_ns=2_010 * 1_000_000,
                attributes={"model": "new-model"},
            )
        ],
    )
    store.create_assessment(
        Feedback(trace_id=trace_id, name="new_quality", value="new", source=source)
    )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 122_000)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=archive_root_uri,
            broader_retention="2m",
        )
        >= 1
    )

    trace = store.get_trace(trace_id)
    assert [span.name for span in trace.data.spans] == ["new-span"]
    assert [assessment.name for assessment in trace.info.assessments] == ["new_quality"]

    old_matches, _ = store.search_traces(
        locations=[experiment_id],
        filter_string='span.attributes.model LIKE "%old-model%"',
        max_results=10,
    )
    new_matches, _ = store.search_traces(
        locations=[experiment_id],
        filter_string='span.attributes.model LIKE "%new-model%"',
        max_results=10,
    )
    assert old_matches == []
    assert [trace_info.trace_id for trace_info in new_matches] == [trace_id]


def test_hybrid_query_trace_metrics_merges_hot_and_cold_avg_and_percentile(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-hybrid-metric-merge")
    archive_root_uri = _archive_root_uri(tmp_path)

    store.start_trace(
        TraceInfo(
            trace_id="tr-cold-metric",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_000,
            execution_duration=10,
            state=TraceState.OK,
            tags={TraceTagKey.TRACE_NAME: "workflow"},
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "tr-cold-metric",
                "archived_span",
                span_id=301,
                trace_num=301,
                start_ns=1_000 * 1_000_000,
                end_ns=1_010 * 1_000_000,
            )
        ],
    )

    store.start_trace(
        TraceInfo(
            trace_id="tr-hot-metric",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=119_000,
            execution_duration=30,
            state=TraceState.OK,
            tags={TraceTagKey.TRACE_NAME: "workflow"},
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "tr-hot-metric",
                "hot_span",
                span_id=302,
                trace_num=302,
                start_ns=119_000 * 1_000_000,
                end_ns=119_030 * 1_000_000,
            )
        ],
    )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 121_000)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=archive_root_uri,
            broader_retention="2m",
        )
        >= 1
    )

    points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.LATENCY,
        aggregations=[
            MetricAggregation(aggregation_type=AggregationType.AVG),
            MetricAggregation(aggregation_type=AggregationType.PERCENTILE, percentile_value=50),
        ],
        dimensions=[TraceMetricDimensionKey.TRACE_NAME],
    )

    assert [asdict(point) for point in points] == [
        {
            "metric_name": TraceMetricKey.LATENCY,
            "dimensions": {TraceMetricDimensionKey.TRACE_NAME: "workflow"},
            "values": {"AVG": 20.0, "P50": 20.0},
        }
    ]


def test_hybrid_query_trace_metrics_merges_avg_without_sample_merge(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-hybrid-avg-merge")
    archive_root_uri = _archive_root_uri(tmp_path)

    for trace_id, request_time, duration, total_tokens in [
        ("tr-cold-avg-1", 1_000, 10, 100),
        ("tr-cold-avg-2", 2_000, 20, 300),
        ("tr-hot-avg", 250_000, 100, 500),
    ]:
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=request_time,
                execution_duration=duration,
                state=TraceState.OK,
                trace_metadata={
                    TraceMetadataKey.TOKEN_USAGE: json.dumps({"total_tokens": total_tokens})
                },
            )
        )
        store.log_spans(
            experiment_id,
            [
                create_test_span(
                    trace_id,
                    f"span-{trace_id}",
                    span_id=1,
                    trace_num=1,
                    start_ns=request_time * 1_000_000,
                    end_ns=(request_time + duration) * 1_000_000,
                )
            ],
        )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 300_000)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=archive_root_uri,
            broader_retention="2m",
        )
        >= 2
    )

    def fail_sample_merge(*args, **kwargs):
        raise AssertionError("AVG should be merged from SUM and COUNT without raw samples")

    monkeypatch.setattr(store, "_query_trace_metric_samples_hot", fail_sample_merge)

    latency_points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.LATENCY,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.AVG)],
        start_time_ms=0,
        end_time_ms=300_000,
    )
    assert [asdict(point) for point in latency_points] == [
        {
            "metric_name": TraceMetricKey.LATENCY,
            "dimensions": {},
            "values": {"AVG": pytest.approx(130 / 3)},
        }
    ]

    token_points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.TOTAL_TOKENS,
        aggregations=[
            MetricAggregation(aggregation_type=AggregationType.SUM),
            MetricAggregation(aggregation_type=AggregationType.AVG),
        ],
        start_time_ms=0,
        end_time_ms=300_000,
    )
    assert [asdict(point) for point in token_points] == [
        {
            "metric_name": TraceMetricKey.TOTAL_TOKENS,
            "dimensions": {},
            "values": {"SUM": 900.0, "AVG": 300.0},
        }
    ]


def test_hybrid_unbucketed_latency_and_tokens_use_hot_sql_rollups(monkeypatch, hybrid_routed_store):
    store, _ = hybrid_routed_store
    monkeypatch.setenv(MLFLOW_SQL_TRACE_ROLLUPS_ENABLED.name, "true")
    experiment_id = store.create_experiment("iceberg-hot-unbucketed-avg-rollups")
    day_ms = 24 * 60 * 60 * 1000
    base_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    for index, (day_offset, duration_ms, total_tokens) in enumerate([
        (0, 10, 100),
        (1, 20, 200),
        (1, 40, 400),
    ]):
        request_time_ms = base_ms + day_offset * day_ms + index
        store.start_trace(
            TraceInfo(
                trace_id=f"tr-hot-unbucketed-{index}",
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=request_time_ms,
                execution_duration=duration_ms,
                state=TraceState.OK,
                trace_metadata={
                    TraceMetadataKey.TOKEN_USAGE: json.dumps({"total_tokens": total_tokens})
                },
            )
        )

    assert store.build_sql_trace_rollups(max_partitions=10) == 2

    def fail_raw_query(*args, **kwargs):
        raise AssertionError("unbucketed latency/token summaries should use SQL daily rollups")

    monkeypatch.setattr(store.tracking_store, "_query_trace_metrics_raw", fail_raw_query)

    latency_points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.LATENCY,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.AVG)],
        start_time_ms=base_ms,
        end_time_ms=base_ms + 2 * day_ms - 1,
    )
    assert [asdict(point) for point in latency_points] == [
        {
            "metric_name": TraceMetricKey.LATENCY,
            "dimensions": {},
            "values": {"AVG": pytest.approx(70 / 3)},
        }
    ]

    token_points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.TOTAL_TOKENS,
        aggregations=[
            MetricAggregation(aggregation_type=AggregationType.SUM),
            MetricAggregation(aggregation_type=AggregationType.AVG),
        ],
        start_time_ms=base_ms,
        end_time_ms=base_ms + 2 * day_ms - 1,
    )
    assert [asdict(point) for point in token_points] == [
        {
            "metric_name": TraceMetricKey.TOTAL_TOKENS,
            "dimensions": {},
            "values": {"SUM": 700.0, "AVG": pytest.approx(700 / 3)},
        }
    ]


def test_hybrid_query_trace_metrics_clips_empty_time_range_edges(monkeypatch, hybrid_routed_store):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-hybrid-empty-range-clip")
    day_ms = 24 * 60 * 60 * 1000
    request_time = 10 * day_ms

    store.start_trace(
        TraceInfo(
            trace_id="tr-range-clip",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=request_time,
            execution_duration=10,
            state=TraceState.OK,
        )
    )

    hot_bounds = []
    cold_bounds = []

    def record_hot_query(**kwargs):
        hot_bounds.append((kwargs["start_time_ms"], kwargs["end_time_ms"]))
        return []

    def record_cold_query(**kwargs):
        cold_bounds.append((kwargs["start_time_ms"], kwargs["end_time_ms"]))
        return []

    monkeypatch.setattr(store, "_query_trace_metrics_hot_unvalidated", record_hot_query)
    monkeypatch.setattr(store, "_query_trace_metrics_cold", record_cold_query)

    store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.TRACE_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        time_interval_seconds=day_ms // 1000,
        start_time_ms=0,
        end_time_ms=20 * day_ms,
    )

    assert hot_bounds == [(request_time, 11 * day_ms - 1)]
    assert cold_bounds == []

    hot_bounds.clear()
    cold_bounds.clear()
    store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.TRACE_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
    )

    assert hot_bounds == [(request_time, 11 * day_ms - 1)]
    assert cold_bounds == []


def test_hybrid_query_trace_metrics_skips_sample_merge_for_disjoint_percentile_buckets(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-hybrid-disjoint-percentile")
    archive_root_uri = _archive_root_uri(tmp_path)
    day_ms = 24 * 60 * 60 * 1000

    for trace_id, request_time, duration in [
        ("tr-cold-bucket", 1_000, 10),
        ("tr-hot-bucket", day_ms + 1_000, 30),
    ]:
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=request_time,
                execution_duration=duration,
                state=TraceState.OK,
            )
        )
        store.log_spans(
            experiment_id,
            [
                create_test_span(
                    trace_id,
                    f"span-{trace_id}",
                    span_id=1,
                    trace_num=1,
                    start_ns=request_time * 1_000_000,
                    end_ns=(request_time + duration) * 1_000_000,
                )
            ],
        )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: day_ms + 121_000)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=archive_root_uri,
            broader_retention="2m",
        )
        >= 1
    )

    def fail_sample_merge(*args, **kwargs):
        raise AssertionError("raw sample merge should be skipped for disjoint buckets")

    monkeypatch.setattr(store, "_query_trace_metric_samples_hot", fail_sample_merge)

    points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.LATENCY,
        aggregations=[
            MetricAggregation(aggregation_type=AggregationType.PERCENTILE, percentile_value=50),
        ],
        time_interval_seconds=24 * 60 * 60,
        start_time_ms=0,
        end_time_ms=2 * day_ms,
    )

    sorted_points = sorted(points, key=lambda point: point.dimensions["time_bucket"])
    assert [point.dimensions for point in sorted_points] == [
        {"time_bucket": "1970-01-01T00:00:00+00:00"},
        {"time_bucket": "1970-01-02T00:00:00+00:00"},
    ]
    assert [next(iter(point.values.values())) for point in sorted_points] == [10.0, 30.0]


def test_hybrid_query_trace_metrics_merges_only_overlapping_percentile_bucket(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-hybrid-overlap-percentile")
    archive_root_uri = _archive_root_uri(tmp_path)
    day_ms = 24 * 60 * 60 * 1000
    assessment_source = AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="judge")

    for trace_id, request_time, duration in [
        ("tr-cold-only-bucket", 1_000, 10),
        ("tr-cold-overlap-bucket", day_ms + 500, 10),
        ("tr-hot-overlap-bucket", day_ms + 2_000, 30),
        ("tr-hot-only-bucket", 2 * day_ms + 1_000, 50),
    ]:
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=request_time,
                execution_duration=duration,
                state=TraceState.OK,
                tags={TraceTagKey.TRACE_NAME: "workflow"},
            )
        )
        store.log_spans(
            experiment_id,
            [
                create_test_span(
                    trace_id,
                    "operation",
                    span_id=1,
                    trace_num=1,
                    start_ns=request_time * 1_000_000,
                    end_ns=(request_time + duration) * 1_000_000,
                )
            ],
        )
        store.create_assessment(
            Feedback(
                trace_id=trace_id,
                name="score",
                value=duration,
                source=assessment_source,
            )
        )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: day_ms + 121_000)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=archive_root_uri,
            broader_retention="2m",
        )
        >= 1
    )

    original_hot_samples = store._query_trace_metric_samples_hot
    hot_sample_bounds = []

    def record_hot_samples(**kwargs):
        hot_sample_bounds.append((kwargs["start_time_ms"], kwargs["end_time_ms"]))
        return original_hot_samples(**kwargs)

    monkeypatch.setattr(store, "_query_trace_metric_samples_hot", record_hot_samples)

    points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.LATENCY,
        aggregations=[
            MetricAggregation(aggregation_type=AggregationType.AVG),
            MetricAggregation(aggregation_type=AggregationType.PERCENTILE, percentile_value=50),
        ],
        dimensions=[TraceMetricDimensionKey.TRACE_NAME],
        time_interval_seconds=24 * 60 * 60,
        start_time_ms=0,
        end_time_ms=3 * day_ms,
    )

    assert hot_sample_bounds == [(day_ms, 2 * day_ms - 1)]
    sorted_points = sorted(points, key=lambda point: point.dimensions["time_bucket"])
    assert [point.dimensions for point in sorted_points] == [
        {
            TraceMetricDimensionKey.TRACE_NAME: "workflow",
            "time_bucket": "1970-01-01T00:00:00+00:00",
        },
        {
            TraceMetricDimensionKey.TRACE_NAME: "workflow",
            "time_bucket": "1970-01-02T00:00:00+00:00",
        },
        {
            TraceMetricDimensionKey.TRACE_NAME: "workflow",
            "time_bucket": "1970-01-03T00:00:00+00:00",
        },
    ]
    assert [point.values for point in sorted_points] == [
        {"AVG": 10, "P50": 10},
        {"AVG": 20, "P50": 20},
        {"AVG": 50, "P50": 50},
    ]

    for view_type, metric_name, dimension, dimension_value in [
        (
            MetricViewType.SPANS,
            SpanMetricKey.LATENCY,
            SpanMetricDimensionKey.SPAN_NAME,
            "operation",
        ),
        (
            MetricViewType.ASSESSMENTS,
            AssessmentMetricKey.ASSESSMENT_VALUE,
            AssessmentMetricDimensionKey.ASSESSMENT_NAME,
            "score",
        ),
    ]:
        points = store.query_trace_metrics(
            experiment_ids=[experiment_id],
            view_type=view_type,
            metric_name=metric_name,
            aggregations=[
                MetricAggregation(aggregation_type=AggregationType.AVG),
                MetricAggregation(aggregation_type=AggregationType.PERCENTILE, percentile_value=50),
            ],
            dimensions=[dimension],
            time_interval_seconds=24 * 60 * 60,
            start_time_ms=0,
            end_time_ms=3 * day_ms,
        )
        actual_points = [
            asdict(point) for point in sorted(points, key=lambda p: p.dimensions["time_bucket"])
        ]
        assert actual_points == [
            {
                "metric_name": metric_name,
                "dimensions": {
                    dimension: dimension_value,
                    "time_bucket": "1970-01-01T00:00:00+00:00",
                },
                "values": {"AVG": 10, "P50": 10},
            },
            {
                "metric_name": metric_name,
                "dimensions": {
                    dimension: dimension_value,
                    "time_bucket": "1970-01-02T00:00:00+00:00",
                },
                "values": {"AVG": 20, "P50": 20},
            },
            {
                "metric_name": metric_name,
                "dimensions": {
                    dimension: dimension_value,
                    "time_bucket": "1970-01-03T00:00:00+00:00",
                },
                "values": {"AVG": 50, "P50": 50},
            },
        ]

    assert hot_sample_bounds == [(day_ms, 2 * day_ms - 1)] * 3


def test_hybrid_search_skips_cold_when_hot_store_covers_time_range(
    monkeypatch, hybrid_routed_store
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-hybrid-hot-search")
    start_time_ms = 200_000
    end_time_ms = start_time_ms + 2 * 24 * 60 * 60 * 1000
    for trace_id, request_time in [
        ("tr-hot-coverage-old", start_time_ms - 1),
        ("tr-hot-coverage-in-range", start_time_ms + 1_000),
    ]:
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=request_time,
                execution_duration=10,
                state=TraceState.OK,
            )
        )

    def fail_cold_search(*args, **kwargs):
        raise AssertionError("cold search should be skipped when hot store covers the time range")

    monkeypatch.setattr(store, "_search_traces_cold", fail_cold_search)

    traces, token = store.search_traces(
        locations=[experiment_id],
        filter_string=(
            f"attributes.timestamp_ms > {start_time_ms} AND attributes.timestamp_ms < {end_time_ms}"
        ),
        max_results=10,
        order_by=["timestamp DESC"],
    )

    assert token is None
    assert [trace_info.trace_id for trace_info in traces] == ["tr-hot-coverage-in-range"]


def test_hybrid_search_skips_older_cold_store_for_full_hot_page(monkeypatch, hybrid_routed_store):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-hybrid-hot-first-page")
    for trace_id, request_time in [
        ("tr-hot-oldest", 200_000),
        ("tr-hot-middle", 300_000),
        ("tr-hot-newest", 400_000),
    ]:
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=request_time,
                execution_duration=10,
                state=TraceState.OK,
            )
        )

    monkeypatch.setattr(
        store,
        "_trace_metric_storage_bounds",
        lambda _: ((200_000, 400_000), (0, 100_000)),
    )

    def fail_cold_search(*args, **kwargs):
        raise AssertionError("older cold traces cannot contribute to a full newer hot page")

    monkeypatch.setattr(store, "_search_traces_cold", fail_cold_search)
    hot_search_tokens = []
    original_call_hot_store = store._call_hot_store

    def record_hot_search(method_name, *args, **kwargs):
        if method_name == "search_traces":
            hot_search_tokens.append(kwargs.get("page_token"))
        return original_call_hot_store(method_name, *args, **kwargs)

    monkeypatch.setattr(store, "_call_hot_store", record_hot_search)

    first_page, token = store.search_traces(
        locations=[experiment_id],
        max_results=1,
        order_by=["timestamp DESC"],
    )
    second_page, second_token = store.search_traces(
        locations=[experiment_id],
        max_results=1,
        order_by=["timestamp DESC"],
        page_token=token,
    )

    assert token is not None
    assert second_token is not None
    assert [trace_info.trace_id for trace_info in first_page] == ["tr-hot-newest"]
    assert [trace_info.trace_id for trace_info in second_page] == ["tr-hot-middle"]
    assert hot_search_tokens == [None, token]


def test_hybrid_query_metrics_skips_cold_when_hot_store_covers_time_range(
    monkeypatch, hybrid_routed_store
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-hybrid-hot-metrics")
    start_time_ms = 200_000
    end_time_ms = start_time_ms + 2 * 24 * 60 * 60 * 1000
    for trace_id, request_time in [
        ("tr-hot-metric-coverage-old", start_time_ms - 1),
        ("tr-hot-metric-coverage-in-range", start_time_ms + 1_000),
    ]:
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=request_time,
                execution_duration=10,
                state=TraceState.OK,
            )
        )

    def fail_cold_metrics(*args, **kwargs):
        raise AssertionError("cold metrics should be skipped when hot store covers the time range")

    monkeypatch.setattr(store, "_query_trace_metrics_cold", fail_cold_metrics)

    points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.TRACE_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
    )

    assert [asdict(point) for point in points] == [
        {
            "metric_name": TraceMetricKey.TRACE_COUNT,
            "dimensions": {},
            "values": {"COUNT": 1},
        }
    ]


def test_hybrid_evicts_old_archived_payloads_but_keeps_recent_ones(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-hybrid-eviction")
    archive_root_uri = _archive_root_uri(tmp_path)

    store.start_trace(
        TraceInfo(
            trace_id="tr-cold-old",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_000,
            execution_duration=10,
            state=TraceState.OK,
            tags={TraceTagKey.TRACE_NAME: "old-trace"},
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "tr-cold-old",
                "old_span",
                span_id=411,
                trace_num=411,
                start_ns=1_000 * 1_000_000,
                end_ns=1_010 * 1_000_000,
                attributes={"model": "gpt-old"},
            )
        ],
    )

    store.start_trace(
        TraceInfo(
            trace_id="tr-hot-recent",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=119_000,
            execution_duration=10,
            state=TraceState.OK,
            tags={TraceTagKey.TRACE_NAME: "recent-trace"},
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "tr-hot-recent",
                "recent_span",
                span_id=412,
                trace_num=412,
                start_ns=119_000 * 1_000_000,
                end_ns=119_010 * 1_000_000,
                attributes={"model": "gpt-recent"},
            )
        ],
    )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 121_000)
    archived = store.archive_traces(
        resolved_trace_archival_location=archive_root_uri,
        broader_retention="1m",
        delete_payload_after_retention=True,
    )
    assert archived >= 1

    assert not _tracking_trace_row_exists(store, "tr-cold-old")
    assert _tracking_trace_row_exists(store, "tr-hot-recent")

    old_trace_info = store.get_trace_info("tr-cold-old")
    recent_trace_info = store.get_trace_info("tr-hot-recent")
    assert old_trace_info.tags[TraceTagKey.TRACE_NAME] == "old-trace"
    assert recent_trace_info.tags[TraceTagKey.TRACE_NAME] == "recent-trace"

    traces, _ = store.search_traces(
        locations=[experiment_id],
        filter_string="name = 'old-trace'",
        max_results=10,
    )
    assert [trace_info.trace_id for trace_info in traces] == ["tr-cold-old"]

    old_trace = store.get_trace("tr-cold-old")
    recent_trace = store.get_trace("tr-hot-recent")
    assert old_trace.data.spans == []
    assert [span.name for span in recent_trace.data.spans] == ["recent_span"]

    cold_metric_points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.TRACE_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        start_time_ms=0,
        end_time_ms=60_000,
    )
    assert [asdict(point) for point in cold_metric_points] == [
        {
            "metric_name": TraceMetricKey.TRACE_COUNT,
            "dimensions": {},
            "values": {"COUNT": 1},
        }
    ]


def test_hybrid_rejects_reserved_archive_tags(hybrid_routed_store):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-hybrid-reserved-tags")
    trace_id = "tr-reserved-tags"

    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_000,
            execution_duration=10,
            state=TraceState.OK,
            tags={
                TraceTagKey.SPANS_LOCATION: SpansLocation.ARCHIVE_REPO.value,
                TraceTagKey.ARCHIVE_LOCATION: "file:///should-not-stick",
                TraceTagKey.ARCHIVAL_FAILURE: "should-not-stick",
            },
        )
    )
    trace_info = store.get_trace_info(trace_id)
    assert TraceTagKey.SPANS_LOCATION not in trace_info.tags
    assert TraceTagKey.ARCHIVE_LOCATION not in trace_info.tags
    assert TraceTagKey.ARCHIVAL_FAILURE not in trace_info.tags

    for tag_key in (
        TraceTagKey.SPANS_LOCATION,
        TraceTagKey.ARCHIVE_LOCATION,
        TraceTagKey.ARCHIVAL_FAILURE,
    ):
        with pytest.raises(MlflowException, match="managed by the hybrid trace backend"):
            store.set_trace_tag(trace_id, tag_key, "forbidden")


def test_hybrid_archived_traces_reject_hot_tag_updates(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-hybrid-stale-filter")
    archive_root_uri = _archive_root_uri(tmp_path)

    store.start_trace(
        TraceInfo(
            trace_id="tr-stale-filter",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_000,
            execution_duration=10,
            state=TraceState.OK,
            tags={"env": "prod"},
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "tr-stale-filter",
                "stale_filter_span",
                span_id=211,
                trace_num=211,
                start_ns=1_000 * 1_000_000,
                end_ns=1_010 * 1_000_000,
                attributes={"model": "gpt-4"},
            )
        ],
    )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 121_000)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=archive_root_uri,
            broader_retention="2m",
        )
        >= 1
    )

    with pytest.raises(MlflowException, match="tr-stale-filter"):
        store.set_trace_tag("tr-stale-filter", "env", "dev")

    traces, _ = store.search_traces(
        locations=[experiment_id],
        filter_string='span.attributes.model LIKE "%gpt-4%" AND tag.env = "prod"',
        max_results=10,
    )
    assert [trace_info.trace_id for trace_info in traces] == ["tr-stale-filter"]


def test_hybrid_payload_search_order_by_handles_nulls_and_aliases(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-hybrid-ordering")
    archive_root_uri = _archive_root_uri(tmp_path)

    store.start_trace(
        TraceInfo(
            trace_id="tr-archived-order",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "tr-archived-order",
                "archived_order_span",
                span_id=301,
                trace_num=301,
                start_ns=1_000 * 1_000_000,
                end_ns=1_010 * 1_000_000,
                attributes={"model": "gpt-4"},
            )
        ],
    )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: 121_000)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=archive_root_uri,
            broader_retention="2m",
        )
        >= 1
    )

    for trace_id, request_time, duration, name in [
        ("tr-hot-name-a", 122_000, 50, "workflow-a"),
        ("tr-hot-name-b", 123_000, 5, "workflow-b"),
    ]:
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=request_time,
                execution_duration=duration,
                state=TraceState.OK,
                tags={TraceTagKey.TRACE_NAME: name},
            )
        )
        store.log_spans(
            experiment_id,
            [
                create_test_span(
                    trace_id,
                    f"{trace_id}_span",
                    span_id=request_time // 1000,
                    trace_num=request_time // 1000,
                    start_ns=request_time * 1_000_000,
                    end_ns=(request_time + duration) * 1_000_000,
                    attributes={"model": "gpt-4"},
                )
            ],
        )

    ordered_by_name, _ = store.search_traces(
        locations=[experiment_id],
        filter_string='span.attributes.model LIKE "%gpt-4%"',
        order_by=["name ASC"],
        max_results=10,
    )
    assert [trace_info.trace_id for trace_info in ordered_by_name] == [
        "tr-hot-name-a",
        "tr-hot-name-b",
        "tr-archived-order",
    ]

    unordered_trace_infos, _ = store.search_traces(
        locations=[experiment_id],
        filter_string='span.attributes.model LIKE "%gpt-4%"',
        order_by=["timestamp DESC"],
        max_results=10,
    )
    ordered_by_end_time = sorted(
        unordered_trace_infos,
        key=store._trace_info_sort_key(["end_time DESC"]),
    )
    assert [trace_info.trace_id for trace_info in ordered_by_end_time] == [
        "tr-hot-name-b",
        "tr-hot-name-a",
        "tr-archived-order",
    ]


def test_start_trace_uses_local_warehouse_env_override(tmp_path: Path, monkeypatch):
    artifact_uri = tmp_path / "artifacts"
    artifact_uri.mkdir()
    backend_store_uri = f"sqlite:///{tmp_path / 'tracking.db'}"
    configured_root = tmp_path / "configured-warehouse-root"

    monkeypatch.setenv(MLFLOW_ICEBERG_WAREHOUSE_URI.name, str(configured_root))
    store = IcebergSqlAlchemyStore(
        backend_store_uri,
        artifact_uri.as_uri(),
    )
    try:
        experiment_id = store.create_experiment("iceberg-env-warehouse")
        trace_info = store.start_trace(
            TraceInfo(
                trace_id="tr-iceberg-env-start",
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=1234,
                execution_duration=56,
                state=TraceState.OK,
                tags={TraceTagKey.TRACE_NAME: "workflow_env"},
            )
        )
        resolved = iceberg_trace_backend_module._resolve_warehouse_location(
            tracking_db_uri=backend_store_uri
        )

        assert trace_info.trace_id == "tr-iceberg-env-start"
        assert resolved.warehouse_uri == (configured_root / "warehouse").as_uri()
        assert (
            iceberg_trace_backend_module._resolve_tracking_db_uri(
                tracking_db_uri=backend_store_uri,
            )
            == backend_store_uri
        )
        store._trace_table()
        assert any(path.is_file() for path in (configured_root / "warehouse").rglob("*"))
    finally:
        store._dispose_engine()
        store._clear_process_resources()


def test_catalog_accepts_s3_warehouse_uri_when_env_override_is_configured(monkeypatch):
    calls = {}

    class FakeSqlCatalog:
        def __init__(self, name, **kwargs):
            calls["name"] = name
            calls.update(kwargs)

    monkeypatch.setenv(MLFLOW_ICEBERG_WAREHOUSE_URI.name, "s3://bucket/warehouse")
    monkeypatch.setattr(iceberg_trace_backend_module, "SqlCatalog", FakeSqlCatalog)

    iceberg_trace_backend_module._create_sql_catalog(
        tracking_db_uri="sqlite:////tmp/test-tracking.db",
    )

    assert calls["name"] == "mlflow"
    assert calls["warehouse"] == "s3://bucket/warehouse"
    assert calls["uri"] == "sqlite:////tmp/test-tracking.db"
    assert calls["init_catalog_tables"] == "false"


def test_iceberg_tables_are_partitioned_and_sorted_for_production_schema(routed_store):
    store, _ = routed_store
    backend = store

    tables = {
        "trace_index": backend._trace_table(),
        "trace_tag_index": backend._trace_tag_table(),
        "span_index": backend._span_table(),
        "assessment_index": backend._assessment_table(),
        "trace_metric_daily_rollups": backend._trace_metric_rollup_table(),
        "span_cost_daily_rollups": backend._span_cost_rollup_table(),
        "assessment_daily_rollups": backend._assessment_rollup_table(),
    }

    assert {
        table_name: [(field.name, str(field.transform)) for field in table.spec().fields]
        for table_name, table in tables.items()
    } == {
        "trace_index": [("experiment_id", "identity"), ("request_day", "identity")],
        "trace_tag_index": [("experiment_id", "identity"), ("request_day", "identity")],
        "span_index": [("experiment_id", "identity"), ("span_start_day", "identity")],
        "assessment_index": [("experiment_id", "identity"), ("trace_request_day", "identity")],
        "trace_metric_daily_rollups": [("workspace", "identity"), ("rollup_day", "identity")],
        "span_cost_daily_rollups": [("workspace", "identity"), ("rollup_day", "identity")],
        "assessment_daily_rollups": [("workspace", "identity"), ("rollup_day", "identity")],
    }

    assert {
        table_name: [
            (table.schema().find_column_name(field.source_id), field.direction.value)
            for field in table.sort_order().fields
        ]
        for table_name, table in tables.items()
    } == {
        "trace_index": [
            ("experiment_id", "asc"),
            ("request_time_ms", "desc"),
            ("trace_id", "asc"),
        ],
        "trace_tag_index": [
            ("experiment_id", "asc"),
            ("tag_key", "asc"),
            ("tag_value", "asc"),
            ("trace_id", "asc"),
        ],
        "span_index": [
            ("experiment_id", "asc"),
            ("trace_id", "asc"),
            ("start_time_ns", "asc"),
            ("span_id", "asc"),
        ],
        "assessment_index": [
            ("experiment_id", "asc"),
            ("trace_id", "asc"),
            ("assessment_name", "asc"),
            ("assessment_id", "asc"),
        ],
        "trace_metric_daily_rollups": [
            ("experiment_id", "asc"),
            ("metric_name", "asc"),
            ("grouping_set", "asc"),
            ("trace_status", "asc"),
        ],
        "span_cost_daily_rollups": [
            ("experiment_id", "asc"),
            ("metric_name", "asc"),
            ("grouping_set", "asc"),
            ("model_provider", "asc"),
            ("model_name", "asc"),
        ],
        "assessment_daily_rollups": [
            ("experiment_id", "asc"),
            ("metric_name", "asc"),
            ("grouping_set", "asc"),
        ],
    }


def test_compaction_partition_write_order_matches_table_sort_orders():
    assert iceberg_trace_backend_module._sort_order_sql(
        iceberg_trace_backend_module._TRACE_INDEX_TABLE
    ) == (
        '"experiment_id" ASC NULLS LAST, "request_time_ms" DESC NULLS LAST, '
        '"trace_id" ASC NULLS LAST'
    )
    assert iceberg_trace_backend_module._sort_order_sql(
        iceberg_trace_backend_module._SPAN_INDEX_TABLE
    ) == (
        '"experiment_id" ASC NULLS LAST, "trace_id" ASC NULLS LAST, '
        '"start_time_ns" ASC NULLS LAST, "span_id" ASC NULLS LAST'
    )


def test_iceberg_backend_resolves_workspace_without_tracking_store_dependency(
    tmp_path, monkeypatch
):
    artifact_uri = tmp_path / "artifacts"
    artifact_uri.mkdir()
    backend = IcebergSqlAlchemyStore(
        f"sqlite:///{tmp_path / 'workspace-resolution.db'}",
        artifact_uri.as_uri(),
    )
    try:
        monkeypatch.delenv(MLFLOW_ENABLE_WORKSPACES.name, raising=False)
        assert backend._get_active_workspace() == DEFAULT_WORKSPACE_NAME

        monkeypatch.setenv(MLFLOW_ENABLE_WORKSPACES.name, "true")
        with WorkspaceContext("team-a"):
            assert backend._get_active_workspace() == "team-a"

        with pytest.raises(MlflowException, match="Active workspace is required"):
            backend._get_active_workspace()
    finally:
        backend._dispose_engine()


def test_duckdb_trace_search_compiler_parameterizes_filters_and_ordering():
    compiled = _DuckDBTraceSearchCompiler(
        _TraceSearchSpec(
            experiment_ids=["exp-1", "exp-2"],
            trace_filters=[
                {
                    "type": "tag",
                    "key": "model'key",
                    "comparator": "=",
                    "value": "gpt-4'; DROP TABLE traces; --",
                },
                {
                    "type": "request_metadata",
                    "key": TraceMetadataKey.TRACE_USER,
                    "comparator": "=",
                    "value": "alice",
                },
            ],
            span_filters=[],
            assessment_filters=[],
            order_by=["tag.model DESC"],
            sql_limit=25,
            workspace="workspace-a",
        )
    ).compile()

    assert "FROM trace_rows" in compiled.sql
    assert "FROM trace_tag_rows" in compiled.sql
    assert "experiment_id IN (?,?)" in compiled.sql
    assert "workspace = ?" in compiled.sql
    assert "LIMIT 25" in compiled.sql
    assert "DROP TABLE" not in compiled.sql
    assert "tag_key = 'model''key'" in compiled.sql
    assert "ORDER BY (SELECT tt.tag_value" in compiled.sql
    assert compiled.params == [
        "exp-1",
        "exp-2",
        "workspace-a",
        "gpt-4'; DROP TABLE traces; --",
        "alice",
    ]


def test_duckdb_trace_search_compiler_projects_only_candidate_trace_ids():
    compiled = _DuckDBTraceSearchCompiler(
        _TraceSearchSpec(
            experiment_ids=["exp-1"],
            trace_filters=[],
            span_filters=[],
            assessment_filters=[],
            order_by=[],
            sql_limit=25,
            workspace="workspace-a",
        )
    ).compile()

    assert "SELECT t.*" not in compiled.sql
    assert "SELECT t.trace_id" in compiled.sql
    for column in ["trace_id", "experiment_id", "request_time_ms", "workspace"]:
        assert f'"{column}"' in compiled.sql
    for column in [
        "execution_duration_ms",
        "state",
        "client_request_id",
        "request_preview",
        "response_preview",
        "tags_json",
        "metadata_json",
        "source_run_id",
        "trace_user",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    ]:
        assert f'"{column}"' not in compiled.sql


def test_duckdb_trace_search_compiler_projects_filter_and_session_columns():
    compiled = _DuckDBTraceSearchCompiler(
        _TraceSearchSpec(
            experiment_ids=["exp-1"],
            trace_filters=[
                {
                    "type": "request_metadata",
                    "key": TraceMetadataKey.SOURCE_RUN,
                    "comparator": "=",
                    "value": "run-1",
                },
                {
                    "type": "tag",
                    "key": TraceTagKey.TRACE_NAME,
                    "comparator": "=",
                    "value": "workflow-a",
                },
            ],
            span_filters=[],
            assessment_filters=[],
            order_by=["timestamp DESC"],
            sql_limit=25,
            workspace="workspace-a",
            include_session_id=True,
        )
    ).compile()

    for column in ["source_run_id", "trace_name", "session_id"]:
        assert f'"{column}"' in compiled.sql
    assert 't."session_id"' not in compiled.sql
    assert "SELECT t.*" not in compiled.sql


def test_duckdb_trace_search_compiler_projects_auxiliary_filter_columns():
    compiled = _DuckDBTraceSearchCompiler(
        _TraceSearchSpec(
            experiment_ids=["exp-1"],
            trace_filters=[],
            span_filters=[
                {
                    "type": "span",
                    "key": "attributes.model",
                    "comparator": "=",
                    "value": "model-a",
                }
            ],
            assessment_filters=[
                {
                    "type": "feedback",
                    "key": "score",
                    "comparator": ">",
                    "value": 0.5,
                }
            ],
            order_by=[],
            sql_limit=25,
            workspace="workspace-a",
            include_session_id=True,
        )
    ).compile()

    for column in ["attributes_json", "aggregate_value", "valid", "metadata_json"]:
        assert f'"{column}"' in compiled.sql
    for column in ["span_json", "assessment_json", "assessment_value_text"]:
        assert f'"{column}"' not in compiled.sql
    assert compiled.requires_trace_tags
    assert compiled.requires_spans
    assert compiled.requires_assessments


def test_recent_partition_trace_search_fast_path_eligibility():
    timestamp_filter = [
        {"type": "attribute", "key": "timestamp_ms", "comparator": ">", "value": 1000},
        {"type": "attribute", "key": "timestamp_ms", "comparator": "<", "value": 2000},
    ]

    assert iceberg_trace_backend_module._can_search_traces_by_recent_partitions(
        trace_filters=timestamp_filter,
        assessment_filters=[],
        span_filters=[],
        order_by=["timestamp DESC"],
    )
    assert not iceberg_trace_backend_module._can_search_traces_by_recent_partitions(
        trace_filters=timestamp_filter,
        assessment_filters=[],
        span_filters=[],
        order_by=["timestamp ASC"],
    )
    assert not iceberg_trace_backend_module._can_search_traces_by_recent_partitions(
        trace_filters=[timestamp_filter[0]],
        assessment_filters=[],
        span_filters=[],
        order_by=["timestamp DESC"],
    )
    assert not iceberg_trace_backend_module._can_search_traces_by_recent_partitions(
        trace_filters=timestamp_filter,
        assessment_filters=[{"type": "feedback", "key": "quality"}],
        span_filters=[],
        order_by=["timestamp DESC"],
    )
    assert not iceberg_trace_backend_module._can_search_traces_by_recent_partitions(
        trace_filters=[
            *timestamp_filter,
            {"type": "tag", "key": "color", "comparator": "=", "value": "green"},
        ],
        assessment_filters=[],
        span_filters=[],
        order_by=["timestamp DESC"],
    )


class _FakeDuckDBConnection:
    def __init__(self):
        self.close_count = 0

    def close(self):
        self.close_count += 1


def test_duckdb_connection_caches_immutable_iceberg_metadata(monkeypatch):
    class RecordingConnection:
        def __init__(self):
            self.statements = []

        def execute(self, statement):
            self.statements.append(statement)
            return self

    connection = RecordingConnection()
    monkeypatch.setattr(iceberg_trace_backend_module.duckdb, "connect", lambda _: connection)

    assert iceberg_trace_backend_module._connect_duckdb() is connection
    assert "SET threads = 2" in connection.statements
    assert "SET enable_http_metadata_cache = true" in connection.statements
    assert "SET parquet_metadata_cache = true" in connection.statements
    assert "SET validate_external_file_cache = 'NO_VALIDATION'" in connection.statements


def test_duckdb_connection_pool_initializes_and_closes_each_connection(monkeypatch):
    connections = [_FakeDuckDBConnection(), _FakeDuckDBConnection()]
    connection_iter = iter(connections)
    monkeypatch.setattr(
        iceberg_trace_backend_module,
        "_connect_duckdb",
        lambda: next(connection_iter),
    )

    pool = iceberg_trace_backend_module._DuckDBConnectionPool(2)
    pool.close()
    pool.close()

    assert [connection.close_count for connection in connections] == [1, 1]


def test_duckdb_connection_pool_closes_partial_initialization(monkeypatch):
    connection = _FakeDuckDBConnection()
    calls = iter([connection, RuntimeError("connection failed")])

    def connect():
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(iceberg_trace_backend_module, "_connect_duckdb", connect)

    with pytest.raises(RuntimeError, match="connection failed"):
        iceberg_trace_backend_module._DuckDBConnectionPool(2)

    assert connection.close_count == 1


def test_duckdb_connection_pool_supports_bounded_concurrency(monkeypatch):
    connections = [_FakeDuckDBConnection(), _FakeDuckDBConnection()]
    connection_iter = iter(connections)
    monkeypatch.setattr(
        iceberg_trace_backend_module,
        "_connect_duckdb",
        lambda: next(connection_iter),
    )
    pool = iceberg_trace_backend_module._DuckDBConnectionPool(2)
    barrier = threading.Barrier(2)
    active = 0
    max_active = 0
    active_lock = threading.Lock()

    def borrow_connection():
        nonlocal active, max_active
        with pool.acquire() as lease:
            with active_lock:
                active += 1
                max_active = max(max_active, active)
            barrier.wait(timeout=5)
            with active_lock:
                active -= 1
            return lease.connection

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(borrow_connection) for _ in range(2)]
        borrowed_connections = {future.result(timeout=5) for future in futures}

    pool.close()

    assert max_active == 2
    assert borrowed_connections == set(connections)


def test_duckdb_connection_pool_prioritizes_lightweight_waiters(monkeypatch):
    connection = _FakeDuckDBConnection()
    monkeypatch.setattr(iceberg_trace_backend_module, "_connect_duckdb", lambda: connection)
    pool = iceberg_trace_backend_module._DuckDBConnectionPool(1)
    order = []

    def borrow(name, high_priority=False):
        with pool.acquire(high_priority=high_priority):
            order.append(name)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        with pool.acquire():
            lightweight = executor.submit(borrow, "lightweight", True)
            with pool._condition:
                assert pool._condition.wait_for(lambda: pool._high_priority_waiters == 1, timeout=5)
            heavy = executor.submit(borrow, "heavy")
        lightweight.result(timeout=5)
        heavy.result(timeout=5)
    pool.close()

    assert order == ["lightweight", "heavy"]


def test_duckdb_connection_pool_rejects_invalid_size():
    with pytest.raises(MlflowException, match="must be at least 1"):
        iceberg_trace_backend_module._DuckDBConnectionPool(0)


def test_hybrid_search_pins_published_cut_across_workers(monkeypatch, hybrid_routed_store):
    store, _ = hybrid_routed_store
    published_cut = iceberg_trace_backend_module._PublishedIcebergCut(
        metadata_locations=MappingProxyType({
            iceberg_trace_backend_module._TRACE_INDEX_TABLE: "trace-metadata.json",
            iceberg_trace_backend_module._SPAN_INDEX_TABLE: "span-metadata.json",
        }),
        snapshot_ids=MappingProxyType({
            iceberg_trace_backend_module._TRACE_INDEX_TABLE: 1,
            iceberg_trace_backend_module._SPAN_INDEX_TABLE: 2,
        }),
        published_at_ms=123,
    )
    load_count = 0
    observed_scans = []

    def load_cut():
        nonlocal load_count
        load_count += 1
        return published_cut

    def collect_from_search(*args, **kwargs):
        observed_scans.append((
            store._iceberg_scan(iceberg_trace_backend_module._TRACE_INDEX_TABLE),
            store._iceberg_scan(iceberg_trace_backend_module._SPAN_INDEX_TABLE),
        ))
        return []

    monkeypatch.setattr(store, "_load_published_iceberg_cut", load_cut)
    monkeypatch.setattr(store, "_collect_trace_infos_from_search", collect_from_search)
    monkeypatch.setattr(store, "_call_hot_store", lambda *args, **kwargs: [])

    traces, token = store.search_traces(experiment_ids=["1"], max_results=10)

    assert traces == []
    assert token is None
    assert load_count == 1
    assert len(observed_scans) == 2
    assert {
        (trace_scan.metadata_location, span_scan.metadata_location)
        for trace_scan, span_scan in observed_scans
    } == {("trace-metadata.json", "span-metadata.json")}
    assert {
        (trace_scan.published_at_ms, span_scan.published_at_ms)
        for trace_scan, span_scan in observed_scans
    } == {(123, 123)}


def test_pinned_operation_loads_live_table_state_once(monkeypatch, routed_store):
    store, _ = routed_store
    table = store._trace_table()
    load_count = 0

    def load_table(identifier):
        nonlocal load_count
        load_count += 1
        assert identifier == (
            *iceberg_trace_backend_module._ICEBERG_NAMESPACE,
            iceberg_trace_backend_module._TRACE_INDEX_TABLE,
        )
        return table

    monkeypatch.setattr(store._resources.catalog, "load_table", load_table)

    @iceberg_trace_backend_module._with_published_iceberg_cut
    def scan_twice(store):
        return [
            store._iceberg_scan(iceberg_trace_backend_module._TRACE_INDEX_TABLE) for _ in range(2)
        ]

    @iceberg_trace_backend_module._with_published_iceberg_cut
    def scan_unpublished_twice(store):
        return [
            store._iceberg_scan(
                iceberg_trace_backend_module._TRACE_INDEX_TABLE,
                published_metadata=False,
            )
            for _ in range(2)
        ]

    first_scans = scan_twice(store)
    second_scans = scan_twice(store)

    assert load_count == 2
    assert {scan.metadata_location for scan in first_scans} == {table.metadata_location}
    assert {scan.metadata_location for scan in second_scans} == {table.metadata_location}

    scan_unpublished_twice(store)
    assert load_count == 4


def test_iceberg_scan_cte_projects_columns_and_pushes_experiment_ids():
    scan = iceberg_trace_backend_module._DuckDBIcebergScan(
        table_name=iceberg_trace_backend_module._TRACE_INDEX_TABLE,
        metadata_location="trace-metadata.json",
        projected_columns=("workspace", "experiment_id", "request_time_ms"),
        workspace="workspace-a",
        experiment_ids=("1", "2"),
    )

    cte, params = iceberg_trace_backend_module._iceberg_scan_cte("trace_rows", scan)

    assert cte == (
        'trace_rows AS (SELECT "workspace", "experiment_id", "request_time_ms" '
        "FROM iceberg_scan(?) WHERE workspace = ? AND experiment_id IN (?,?))"
    )
    assert params == ["trace-metadata.json", "workspace-a", "1", "2"]


def test_iceberg_scan_cte_can_force_an_empty_published_table():
    scan = iceberg_trace_backend_module._DuckDBIcebergScan(
        table_name=iceberg_trace_backend_module._TRACE_INDEX_TABLE,
        metadata_location="trace-metadata.json",
        projected_columns=("trace_id",),
        workspace="workspace-a",
        empty=True,
    )

    cte, params = iceberg_trace_backend_module._iceberg_scan_cte("trace_rows", scan)

    assert cte == (
        'trace_rows AS (SELECT "trace_id" FROM iceberg_scan(?) WHERE workspace = ? AND FALSE)'
    )
    assert params == ["trace-metadata.json", "workspace-a"]


def test_span_filter_search_pushes_experiment_ids(monkeypatch, routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-span-filter-pushdown")
    queries = []

    def capture_query(**kwargs):
        queries.append(kwargs)
        return []

    monkeypatch.setattr(store, "_run_duckdb_query", capture_query)

    traces, page_token = store._search_traces_cold(
        experiment_ids=[experiment_id],
        filter_string='span.name = "root"',
    )

    assert traces == []
    assert page_token is None
    assert len(queries) == 1
    assert queries[0]["span_rows"].experiment_ids == (experiment_id,)


def test_append_retries_conflicting_commits(monkeypatch, routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-append-retry")
    backend = store

    trace_info = TraceInfo(
        trace_id="tr-retry",
        trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
        request_time=1234,
        execution_duration=56,
        state=TraceState.OK,
        tags={TraceTagKey.TRACE_NAME: "workflow_retry"},
    )
    trace_row = backend._build_trace_row_from_entity(trace_info)
    trace_tag_rows = backend._trace_tag_rows(
        trace_id=trace_row["trace_id"],
        experiment_id=trace_row["experiment_id"],
        request_time_ms=trace_row["request_time_ms"],
        current_tags=json.loads(trace_row["tags_json"]),
        previous_tags=None,
    )
    span_rows = [
        backend._span_row(
            create_test_span(
                trace_id="tr-retry",
                name="root",
                span_id=1,
                trace_num=1,
                start_ns=1_000_000_000,
                end_ns=2_000_000_000,
            ),
            experiment_id=experiment_id,
        )
    ]
    assessment_rows = [
        backend._assessment_row(
            Feedback(
                trace_id="tr-retry",
                name="quality",
                value="good",
                source=AssessmentSource(
                    source_type=AssessmentSourceType.HUMAN,
                    source_id="judge",
                ),
            ),
            experiment_id=experiment_id,
        )
    ]

    monkeypatch.setattr(iceberg_trace_backend_module.time, "sleep", lambda _: None)

    def assert_retries(load_method_name, append_fn, rows):
        class FlakyTable:
            def __init__(self):
                self.append_attempts = 0
                self.metadata_location = "metadata.json"

            def append(self, table):
                self.append_attempts += 1
                if self.append_attempts == 1:
                    raise CommitFailedException("simulated commit conflict")

            def current_snapshot(self):
                return type("Snapshot", (), {"snapshot_id": 1})()

        flaky_table = FlakyTable()
        load_attempts = {"count": 0}

        def load():
            load_attempts["count"] += 1
            return flaky_table

        monkeypatch.setattr(backend, load_method_name, load)
        append_fn(rows)
        assert flaky_table.append_attempts == 2
        assert load_attempts["count"] == 2

    assert_retries("_trace_table", backend._append_trace_rows, [trace_row])
    assert_retries("_trace_tag_table", backend._append_trace_tag_rows, trace_tag_rows)
    assert_retries("_span_table", backend._append_span_rows, span_rows)
    assert_retries("_assessment_table", backend._append_assessment_rows, assessment_rows)


def test_failed_rollup_refresh_cleans_precomputed_partitions(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-rollup-refresh-failure")
    trace_info = store.start_trace(
        TraceInfo(
            trace_id="tr-rollup-refresh-failure",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_577_836_800_000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    trace_row = store._build_trace_row_from_entity(trace_info)

    class RecordingArtifactRepo:
        def __init__(self):
            self.deleted_paths = []

        def delete_artifacts(self, artifact_path):
            self.deleted_paths.append(artifact_path)

    artifact_repo = RecordingArtifactRepo()
    projection = iceberg_trace_backend_module._StagedArchivedTraceProjection(
        trace_id=trace_info.trace_id,
        artifact_uri=(tmp_path / "archive-payload").as_uri(),
        artifact_repo=artifact_repo,
        db_payload_generation=0,
        trace_row=trace_row,
        trace_tag_rows=[],
        span_rows=[],
        assessment_rows=[],
        started_at=0.0,
        stage_timings_ms={},
    )
    cleaned_partitions = []

    def fail_rollup_refresh(**_kwargs):
        raise RuntimeError("rollup refresh failed")

    def record_cleanup(_trace_ids, **kwargs):
        cleaned_partitions.append(kwargs)

    monkeypatch.setattr(store, "_refresh_rollup_tables", fail_rollup_refresh)
    monkeypatch.setattr(store, "_delete_iceberg_rows_for_trace_ids", record_cleanup)

    with pytest.raises(MlflowException, match="Trace archival projection failed"):
        store._append_archived_trace_projections([projection])

    assert artifact_repo.deleted_paths == [TRACE_ARCHIVAL_FILENAME]
    assert cleaned_partitions == [
        {
            "trace_partition_keys": store._trace_rollup_partition_keys([trace_row]),
            "span_partition_keys": set(),
            "assessment_partition_keys": store._assessment_rollup_partition_keys([trace_row]),
        }
    ]


def test_failed_projection_cleanup_is_fatal(monkeypatch, hybrid_routed_store, tmp_path: Path):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-projection-cleanup-failure")
    trace_info = store.start_trace(
        TraceInfo(
            trace_id="tr-projection-cleanup-failure",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_577_836_800_000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    projection = iceberg_trace_backend_module._StagedArchivedTraceProjection(
        trace_id=trace_info.trace_id,
        artifact_uri=(tmp_path / "archive-payload").as_uri(),
        artifact_repo=None,
        db_payload_generation=0,
        trace_row=store._build_trace_row_from_entity(trace_info),
        trace_tag_rows=[],
        span_rows=[],
        assessment_rows=[],
        started_at=0.0,
        stage_timings_ms={},
    )

    def fail_refresh(**_kwargs):
        raise RuntimeError("refresh failed")

    def fail_cleanup(*_args, **_kwargs):
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(store, "_refresh_rollup_tables", fail_refresh)
    monkeypatch.setattr(store, "_delete_iceberg_rows_for_trace_ids", fail_cleanup)
    monkeypatch.setattr(
        store, "_delete_unreferenced_archived_trace_payload", lambda **_kwargs: None
    )

    with pytest.raises(
        iceberg_trace_backend_module._IcebergProjectionCleanupError,
        match="archival cannot safely continue",
    ):
        store._append_archived_trace_projections([projection])


def test_failed_payload_cleanup_does_not_skip_iceberg_rollback(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-payload-cleanup-failure")
    trace_info = store.start_trace(
        TraceInfo(
            trace_id="tr-payload-cleanup-failure",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_577_836_800_000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    projection = iceberg_trace_backend_module._StagedArchivedTraceProjection(
        trace_id=trace_info.trace_id,
        artifact_uri=(tmp_path / "archive-payload").as_uri(),
        artifact_repo=None,
        db_payload_generation=0,
        trace_row=store._build_trace_row_from_entity(trace_info),
        trace_tag_rows=[],
        span_rows=[],
        assessment_rows=[],
        started_at=0.0,
        stage_timings_ms={},
    )
    cleaned_trace_ids = []

    def fail_refresh(**_kwargs):
        raise RuntimeError("refresh failed")

    def fail_payload_cleanup(**_kwargs):
        raise RuntimeError("payload cleanup failed")

    def record_iceberg_cleanup(trace_ids, **_kwargs):
        cleaned_trace_ids.extend(trace_ids)

    monkeypatch.setattr(store, "_refresh_rollup_tables", fail_refresh)
    monkeypatch.setattr(store, "_delete_unreferenced_archived_trace_payload", fail_payload_cleanup)
    monkeypatch.setattr(store, "_delete_iceberg_rows_for_trace_ids", record_iceberg_cleanup)

    with pytest.raises(MlflowException, match="Trace archival projection failed"):
        store._append_archived_trace_projections([projection])

    assert cleaned_trace_ids == [trace_info.trace_id]


def test_parallel_archive_does_not_publish_after_uncertain_cleanup(
    monkeypatch, hybrid_routed_store
):
    store, _ = hybrid_routed_store
    cleanup_error = iceberg_trace_backend_module._IcebergProjectionCleanupError("cleanup failed")
    chunks = [
        iceberg_trace_backend_module._StagedArchiveExperimentChunk(
            experiment_id="1",
            candidates=[],
            processed_count=1,
            total_count=1,
            started_at=0.0,
            error=cleanup_error,
        ),
        iceberg_trace_backend_module._StagedArchiveExperimentChunk(
            experiment_id="2",
            candidates=[],
            processed_count=1,
            total_count=1,
            started_at=0.0,
        ),
    ]
    monkeypatch.setattr(
        store,
        "publish_archived_trace_batch",
        lambda **_kwargs: pytest.fail("an uncertain Iceberg state must not be published"),
    )

    with pytest.raises(
        iceberg_trace_backend_module._IcebergProjectionCleanupError,
        match="cleanup failed",
    ):
        store._publish_archived_experiment_chunks(chunks)


def test_iceberg_publication_barrier_blocks_later_live_head_publication(hybrid_routed_store):
    store, _ = hybrid_routed_store
    initial_state = store.get_iceberg_trace_publication_state()
    metadata_locations, snapshot_ids = store._current_iceberg_state()

    store._begin_iceberg_trace_publication_barrier()

    blocked_state = store.get_iceberg_trace_publication_state()
    assert blocked_state["publication_blocked"] is True
    assert not store.set_iceberg_trace_publication_state(
        metadata_locations=metadata_locations,
        snapshot_ids=snapshot_ids,
        expected_published_at_ms=initial_state["published_at_ms"],
    )
    with pytest.raises(MlflowException, match="incomplete prior write"):
        store._begin_iceberg_trace_publication_barrier()

    store._clear_iceberg_trace_publication_barrier()
    assert store.get_iceberg_trace_publication_state()["publication_blocked"] is False


def test_empty_archive_round_does_not_clear_prior_publication_barrier(hybrid_routed_store):
    store, _ = hybrid_routed_store
    chunk = iceberg_trace_backend_module._StagedArchiveExperimentChunk(
        experiment_id="1",
        candidates=[],
        processed_count=0,
        total_count=0,
        started_at=0.0,
    )
    store._begin_iceberg_trace_publication_barrier()
    try:
        store._publish_archived_experiment_chunks([chunk])
        assert store.get_iceberg_trace_publication_state()["publication_blocked"] is True
    finally:
        store._clear_iceberg_trace_publication_barrier()


def test_archive_cleans_staged_payloads_when_publication_barrier_is_blocked(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    projection = iceberg_trace_backend_module._StagedArchivedTraceProjection(
        trace_id="tr-blocked-archive-payload",
        artifact_uri=(tmp_path / "archive-payload").as_uri(),
        artifact_repo=None,
        db_payload_generation=0,
        trace_row={"experiment_id": "1"},
        trace_tag_rows=[],
        span_rows=[],
        assessment_rows=[],
        started_at=0.0,
        stage_timings_ms={},
    )
    chunk = iceberg_trace_backend_module._StagedArchiveExperimentChunk(
        experiment_id="1",
        candidates=[],
        processed_count=1,
        total_count=1,
        started_at=0.0,
        projections=[projection],
    )
    cleaned_projections = []

    monkeypatch.setattr(store, "_ensure_iceberg_trace_publication_unblocked", lambda: None)
    monkeypatch.setattr(
        store,
        "_run_archive_experiment_stage_round",
        lambda **_kwargs: [chunk],
    )
    monkeypatch.setattr(
        store,
        "_delete_staged_archive_payloads",
        lambda projections: cleaned_projections.extend(projections),
    )
    store._begin_iceberg_trace_publication_barrier()
    try:
        with pytest.raises(MlflowException, match="incomplete prior write"):
            store._archive_trace_candidates_by_experiment(
                candidates_by_experiment={"1": [object()]},
                resolved_trace_archival_location=(tmp_path / "archive").as_uri(),
                archive_now_experiment_ids=set(),
                archive_project_batch_size=1,
                archive_max_workers=1,
                archive_experiment_max_workers=1,
            )
    finally:
        store._clear_iceberg_trace_publication_barrier()

    assert cleaned_projections == [projection]


def test_iceberg_publication_barrier_blocks_compaction_before_rewrite(
    monkeypatch, hybrid_routed_store
):
    store, _ = hybrid_routed_store
    compaction_calls = []

    def record_compaction(*_args, **_kwargs):
        compaction_calls.append(True)

    monkeypatch.setattr(
        iceberg_trace_backend_module,
        "compact_iceberg_trace_tables",
        record_compaction,
    )
    store._begin_iceberg_trace_publication_barrier()
    try:
        with pytest.raises(MlflowException, match="incomplete prior write"):
            store.compact_iceberg_trace_tables()
    finally:
        store._clear_iceberg_trace_publication_barrier()

    assert compaction_calls == []


def test_iceberg_publication_barrier_blocks_payload_deletion(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    trace_id = "tr-blocked-payload-deletion"
    cold_trace_info = TraceInfo(
        trace_id=trace_id,
        trace_location=trace_location.TraceLocation.from_experiment_id("0"),
        request_time=1_000,
        execution_duration=10,
        state=TraceState.OK,
        tags={
            TraceTagKey.SPANS_LOCATION: SpansLocation.ARCHIVE_REPO.value,
            TraceTagKey.ARCHIVE_LOCATION: (tmp_path / "archive-payload").as_uri(),
        },
    )
    payload_deletion_calls = []

    monkeypatch.setattr(store, "_select_cold_trace_ids_for_delete", lambda **_kwargs: [trace_id])
    monkeypatch.setattr(store, "_batch_get_trace_infos_cold", lambda _trace_ids: [cold_trace_info])
    monkeypatch.setattr(
        store.tracking_store,
        "_delete_archived_trace_payloads",
        lambda _traces: payload_deletion_calls.append(True) or [trace_id],
    )
    store._begin_iceberg_trace_publication_barrier()
    try:
        with pytest.raises(MlflowException, match="incomplete prior write"):
            store.delete_traces(experiment_id="0", trace_ids=[trace_id])
    finally:
        store._clear_iceberg_trace_publication_barrier()

    assert payload_deletion_calls == []


def test_append_retries_are_bounded(monkeypatch, routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-append-retry-bounded")
    backend = store
    trace_info = TraceInfo(
        trace_id="tr-retry-bounded",
        trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
        request_time=1234,
        execution_duration=56,
        state=TraceState.OK,
        tags={TraceTagKey.TRACE_NAME: "workflow_retry_bounded"},
    )
    trace_row = backend._build_trace_row_from_entity(trace_info)

    class AlwaysConflictTable:
        def __init__(self):
            self.append_attempts = 0

        def append(self, table):
            self.append_attempts += 1
            raise CommitFailedException("persistent conflict")

    always_conflict = AlwaysConflictTable()
    load_attempts = {"count": 0}

    monkeypatch.setattr(iceberg_trace_backend_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(iceberg_trace_backend_module, "_ICEBERG_APPEND_MAX_RETRIES", 3)

    def load():
        load_attempts["count"] += 1
        return always_conflict

    monkeypatch.setattr(backend, "_trace_table", load)
    with pytest.raises(CommitFailedException, match="persistent conflict"):
        backend._append_trace_rows([trace_row])
    assert always_conflict.append_attempts == 3
    assert load_attempts["count"] == 3


def test_log_spans_and_get_trace_from_iceberg_backend(routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-log-spans")
    trace_id = "tr-iceberg-spans"

    spans = [
        create_test_span(
            trace_id=trace_id,
            name="root_span",
            span_id=111,
            status=trace_api.StatusCode.OK,
            start_ns=1_000_000_000,
            end_ns=2_000_000_000,
            trace_num=12345,
        ),
        create_test_span(
            trace_id=trace_id,
            name="child_span",
            span_id=222,
            parent_id=111,
            status=trace_api.StatusCode.UNSET,
            start_ns=1_500_000_000,
            end_ns=1_800_000_000,
            trace_num=12345,
        ),
    ]

    store.log_spans(experiment_id, spans)

    trace = store.get_trace(trace_id)
    trace_info = store.get_trace_info(trace_id)
    span_names = [span.name for span in trace.data.spans]

    assert span_names == ["root_span", "child_span"]
    assert trace_info.tags[TraceTagKey.SPANS_LOCATION] == SpansLocation.TRACKING_STORE.value
    assert trace.info.trace_id == trace_id
    assert trace.data.spans[0].span_id == "000000000000006f"
    assert trace.data.spans[1].parent_id == "000000000000006f"


def test_search_traces_and_query_metrics_with_duckdb(routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-search")

    store.start_trace(
        TraceInfo(
            trace_id="tr-search-1",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1000,
            execution_duration=10,
            state=TraceState.OK,
            tags={TraceTagKey.TRACE_NAME: "workflow_a"},
        )
    )
    store.start_trace(
        TraceInfo(
            trace_id="tr-search-2",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=2000,
            execution_duration=20,
            state=TraceState.ERROR,
            tags={TraceTagKey.TRACE_NAME: "workflow_b"},
        )
    )
    store.start_trace(
        TraceInfo(
            trace_id="tr-search-3",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=3000,
            execution_duration=30,
            state=TraceState.OK,
            tags={TraceTagKey.TRACE_NAME: "workflow_a"},
        )
    )

    trace_infos, next_token = store.search_traces(
        locations=[experiment_id],
        filter_string="name = 'workflow_a'",
        order_by=["timestamp DESC"],
        max_results=10,
    )

    assert [trace_info.trace_id for trace_info in trace_infos] == ["tr-search-3", "tr-search-1"]
    assert next_token is None

    metric_points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.TRACE_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        dimensions=[TraceMetricDimensionKey.TRACE_STATUS],
    )

    assert [asdict(point) for point in metric_points] == [
        {
            "metric_name": TraceMetricKey.TRACE_COUNT,
            "dimensions": {TraceMetricDimensionKey.TRACE_STATUS: "ERROR"},
            "values": {"COUNT": 1},
        },
        {
            "metric_name": TraceMetricKey.TRACE_COUNT,
            "dimensions": {TraceMetricDimensionKey.TRACE_STATUS: "OK"},
            "values": {"COUNT": 2},
        },
    ]


def test_search_traces_hydrates_only_selected_page(monkeypatch, routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-search-page-hydration")
    for index in range(3):
        trace_id = f"tr-page-{index}"
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=index + 1,
                execution_duration=10,
                state=TraceState.OK,
            )
        )

    calls = []
    original = store._run_duckdb_query

    def recording_run_duckdb_query(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(store, "_run_duckdb_query", recording_run_duckdb_query)

    traces, next_token = store.search_traces([experiment_id], max_results=1)

    assert [trace.trace_id for trace in traces] == ["tr-page-2"]
    assert next_token is not None
    assert len(calls) == 3
    candidate_scan = calls[0]["trace_rows"]
    assert set(candidate_scan.projected_columns) == {
        "trace_id",
        "experiment_id",
        "request_time_ms",
        "workspace",
    }
    assert {"metadata_json", "tags_json"}.isdisjoint(candidate_scan.projected_columns)
    hydration_scan = calls[1]["trace_rows"]
    assert hydration_scan.trace_ids == ("tr-page-2",)
    assert set(hydration_scan.projected_columns) == {
        "trace_id",
        "experiment_id",
        "request_time_ms",
        "request_day",
        "execution_duration_ms",
        "state",
        "client_request_id",
        "request_preview",
        "response_preview",
        "session_id",
        "tags_json",
        "metadata_json",
        "workspace",
    }
    assessment_scan = calls[2]["assessment_rows"]
    assert assessment_scan.trace_ids == ("tr-page-2",)
    assert assessment_scan.day_column == "trace_request_day"
    assert set(assessment_scan.projected_columns) == {
        "assessment_id",
        "trace_id",
        "experiment_id",
        "assessment_json",
        "workspace",
        "trace_request_time_ms",
        "trace_request_day",
    }


def test_search_traces_projects_assessment_filter_scan(monkeypatch, routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-assessment-filter-projection")
    trace_id = "tr-assessment-filter-projection"
    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.create_assessment(
        Feedback(
            trace_id=trace_id,
            name="score",
            value=0.9,
            source=AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="judge"),
        )
    )

    calls = []
    original = store._run_duckdb_query

    def recording_run_duckdb_query(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(store, "_run_duckdb_query", recording_run_duckdb_query)

    traces, _ = store.search_traces([experiment_id], filter_string="feedback.score > 0.5")

    assert [trace.trace_id for trace in traces] == [trace_id]
    assessment_scan = next(call["assessment_rows"] for call in calls if call.get("assessment_rows"))
    assert {
        "assessment_name",
        "assessment_type",
        "aggregate_value",
        "metadata_json",
        "valid",
    } <= set(assessment_scan.projected_columns)
    assert {"assessment_json", "assessment_value_text", "rationale"}.isdisjoint(
        assessment_scan.projected_columns
    )


def test_get_trace_legacy_row_loads_assessments_separately(monkeypatch, routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-get-trace-single-query")
    trace_id = "tr-single-query"
    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [create_test_span(trace_id, "root_span", span_id=111, trace_num=12345)],
    )
    source = AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="judge")
    store.create_assessment(
        Feedback(trace_id=trace_id, name="quality", value="good", source=source)
    )

    backend = store
    original = backend._run_duckdb_query
    calls = []

    def counting_run_duckdb_query(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(backend, "_run_duckdb_query", counting_run_duckdb_query)

    trace = store.get_trace(trace_id)

    assert [span.name for span in trace.data.spans] == ["root_span"]
    assert [assessment.name for assessment in trace.info.assessments] == ["quality"]
    assert len(calls) == 2
    assert calls[0]["trace_rows"].table_name == "trace_index"
    assert calls[0]["span_rows"].table_name == "span_index"
    assert "assessment_rows" not in calls[0]
    assert calls[1]["assessment_rows"].table_name == "assessment_index"


def test_hybrid_get_trace_reuses_cold_trace_info(monkeypatch, hybrid_routed_store):
    store, _ = hybrid_routed_store
    trace_id = "tr-cold-fallback"
    trace_info = TraceInfo(
        trace_id=trace_id,
        trace_location=trace_location.TraceLocation.from_experiment_id("1"),
        request_time=1000,
        execution_duration=10,
        state=TraceState.OK,
    )
    trace_row = {"trace_id": trace_id}
    expected_trace = iceberg_trace_backend_module.Trace(
        info=trace_info,
        data=iceberg_trace_backend_module.TraceData(spans=[]),
    )
    info_calls = []
    detail_calls = []

    def call_hot_store(method, *args, **kwargs):
        assert method == "get_trace_info"
        raise MlflowException(
            f"Trace with ID {trace_id} is not found.",
            error_code=iceberg_trace_backend_module.RESOURCE_DOES_NOT_EXIST,
        )

    def get_info_and_row(requested_trace_id):
        info_calls.append(requested_trace_id)
        return trace_info, trace_row

    def get_trace_from_info(row, info, *, allow_partial):
        detail_calls.append((row, info, allow_partial))
        return expected_trace

    monkeypatch.setattr(store, "_call_hot_store", call_hot_store)
    monkeypatch.setattr(store, "_get_trace_info_and_row_cold", get_info_and_row)
    monkeypatch.setattr(store, "_get_trace_from_cold_info", get_trace_from_info)

    assert store.get_trace(trace_id) is expected_trace
    assert info_calls == [trace_id]
    assert detail_calls == [(trace_row, trace_info, False)]


def test_trace_tag_index_updates_search_and_order(routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-tag-index")

    for trace_id, color in [("trace1", "blue"), ("trace2", "green"), ("trace3", "red")]:
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=1000,
                execution_duration=10,
                state=TraceState.OK,
                tags={"color": color},
            )
        )

    traces, _ = store.search_traces(
        [experiment_id],
        filter_string='tag.color = "green"',
        max_results=10,
    )
    assert [trace.trace_id for trace in traces] == ["trace2"]

    ordered, _ = store.search_traces(
        [experiment_id],
        order_by=["tag.color"],
        max_results=10,
    )
    assert [trace.trace_id for trace in ordered] == ["trace1", "trace2", "trace3"]

    store.set_trace_tag("trace2", "color", "amber")
    updated, _ = store.search_traces(
        [experiment_id],
        filter_string='tag.color = "amber"',
        max_results=10,
    )
    assert [trace.trace_id for trace in updated] == ["trace2"]

    store.delete_trace_tag("trace2", "color")
    deleted, _ = store.search_traces(
        [experiment_id],
        filter_string="tag.color IS NULL",
        max_results=10,
    )
    assert {trace.trace_id for trace in deleted} == {"trace2"}


def test_delete_traces_uses_tombstones(routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-delete")

    for idx in range(5):
        store.start_trace(
            TraceInfo(
                trace_id=f"tr-delete-{idx}",
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=idx,
                execution_duration=idx,
                state=TraceState.OK,
                tags={TraceTagKey.TRACE_NAME: f"workflow_{idx}"},
            )
        )

    deleted = store.delete_traces(
        experiment_id=experiment_id,
        max_timestamp_millis=3,
        max_traces=2,
    )
    assert deleted == 2

    traces, _ = store.search_traces(locations=[experiment_id], max_results=10)
    assert [trace.trace_id for trace in traces] == ["tr-delete-4", "tr-delete-3", "tr-delete-2"]

    deleted = store.delete_traces(
        experiment_id=experiment_id,
        trace_ids=["tr-delete-4"],
    )
    assert deleted == 1

    traces, _ = store.search_traces(locations=[experiment_id], max_results=10)
    assert [trace.trace_id for trace in traces] == ["tr-delete-3", "tr-delete-2"]

    with pytest.raises(MlflowException, match="Trace with ID tr-delete-4 is not found."):
        store.get_trace_info("tr-delete-4")


def test_find_completed_sessions_with_filters(routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-sessions")

    for timestamp, env in [(1000, "prod"), (2000, "dev")]:
        store.start_trace(
            TraceInfo(
                trace_id=f"trace-a-{timestamp}",
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=timestamp,
                execution_duration=1,
                state=TraceState.OK,
                tags={"env": env},
                trace_metadata={TraceMetadataKey.TRACE_SESSION: "session-a"},
            )
        )

    for timestamp, env in [(3000, "dev"), (4000, "prod")]:
        store.start_trace(
            TraceInfo(
                trace_id=f"trace-b-{timestamp}",
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=timestamp,
                execution_duration=1,
                state=TraceState.OK,
                tags={"env": env},
                trace_metadata={TraceMetadataKey.TRACE_SESSION: "session-b"},
            )
        )

    for timestamp, user in [(5000, "alice"), (6000, "bob")]:
        store.start_trace(
            TraceInfo(
                trace_id=f"trace-c-{timestamp}",
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=timestamp,
                execution_duration=1,
                state=TraceState.OK,
                trace_metadata={
                    TraceMetadataKey.TRACE_SESSION: "session-c",
                    "user_id": user,
                },
            )
        )

    completed = store.find_completed_sessions(
        experiment_id=experiment_id,
        min_last_trace_timestamp_ms=0,
        max_last_trace_timestamp_ms=5000,
        filter_string="tag.env = 'prod'",
    )
    assert len(completed) == 1
    assert completed[0].session_id == "session-a"
    assert completed[0].first_trace_timestamp_ms == 1000
    assert completed[0].last_trace_timestamp_ms == 2000

    completed = store.find_completed_sessions(
        experiment_id=experiment_id,
        min_last_trace_timestamp_ms=0,
        max_last_trace_timestamp_ms=10000,
        filter_string="metadata.user_id = 'alice'",
    )
    assert len(completed) == 1
    assert completed[0].session_id == "session-c"
    assert completed[0].first_trace_timestamp_ms == 5000
    assert completed[0].last_trace_timestamp_ms == 6000

    store._upsert_session_summaries(store._latest_trace_rows())
    scans = []
    original = store._run_duckdb_query

    def record_scans(**kwargs):
        scans.extend(
            scan.table_name
            for scan in kwargs.values()
            if hasattr(scan, "table_name") and scan is not None
        )
        return original(**kwargs)

    store._run_duckdb_query = record_scans
    completed = store.find_completed_sessions(
        experiment_id=experiment_id,
        min_last_trace_timestamp_ms=0,
        max_last_trace_timestamp_ms=10_000,
    )

    assert [session.session_id for session in completed] == ["session-a", "session-b", "session-c"]
    assert scans == [iceberg_trace_backend_module._SESSION_SUMMARY_TABLE]


def test_session_summary_delete_expression_handles_large_archive_group(routed_store):
    store, _ = routed_store
    keys = tuple(("1", f"session-{index}") for index in range(10_000))

    expression = store._session_summary_keys_expr(keys)

    bind(
        iceberg_trace_backend_module._SESSION_SUMMARY_SCHEMA,
        expression,
        case_sensitive=True,
    )


def test_assessment_crud_and_override_behavior(routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-assessment-crud")
    trace_info = store.start_trace(
        TraceInfo(
            trace_id="tr-assessment-crud",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )

    original = store.create_assessment(
        Feedback(
            trace_id=trace_info.trace_id,
            name="correctness",
            value="no",
            rationale="original",
            source=AssessmentSource(
                source_type=AssessmentSourceType.HUMAN,
                source_id="alice@example.com",
            ),
            metadata={"origin": "human"},
        )
    )
    assert original.assessment_id.startswith("a-")
    assert original.valid is True

    updated = store.update_assessment(
        trace_id=trace_info.trace_id,
        assessment_id=original.assessment_id,
        rationale="updated",
        metadata={"version": "2"},
    )
    assert updated.rationale == "updated"
    assert updated.metadata == {"origin": "human", "version": "2"}

    override = store.create_assessment(
        Feedback(
            trace_id=trace_info.trace_id,
            name="correctness",
            value="yes",
            overrides=original.assessment_id,
            source=AssessmentSource(
                source_type=AssessmentSourceType.HUMAN,
                source_id="bob@example.com",
            ),
        )
    )
    assert override.valid is True
    assert store.get_assessment(trace_info.trace_id, original.assessment_id).valid is False

    trace = store.get_trace(trace_info.trace_id)
    assessments = {assessment.assessment_id: assessment for assessment in trace.info.assessments}
    assert set(assessments) == {original.assessment_id, override.assessment_id}

    store.delete_assessment(trace_info.trace_id, override.assessment_id)
    restored = store.get_assessment(trace_info.trace_id, original.assessment_id)
    assert restored.valid is True

    with pytest.raises(MlflowException, match="not found"):
        store.get_assessment(trace_info.trace_id, override.assessment_id)


def test_search_traces_with_assessment_filters(routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-assessment-search")

    for trace_id in ["trace1", "trace2", "trace3", "trace4"]:
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=1000,
                execution_duration=10,
                state=TraceState.OK,
            )
        )

    source = AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="judge")
    original = store.create_assessment(
        Feedback(trace_id="trace1", name="correctness", value="no", source=source)
    )
    store.create_assessment(
        Feedback(
            trace_id="trace1",
            name="correctness",
            value="yes",
            source=source,
            overrides=original.assessment_id,
        )
    )
    store.create_assessment(
        Feedback(trace_id="trace2", name="correctness", value="no", source=source)
    )
    store.create_assessment(Feedback(trace_id="trace2", name="score", value=5, source=source))
    store.create_assessment(
        Feedback(
            trace_id="trace3",
            name="comment",
            value="Great response! Very helpful.",
            source=source,
        )
    )
    store.create_assessment(
        Expectation(trace_id="trace4", name="priority", value="urgent", source=source)
    )

    def search(filter_string: str):
        traces, _ = store.search_traces(
            [experiment_id], filter_string=filter_string, max_results=20
        )
        return {trace.trace_id for trace in traces}

    assert search('feedback.correctness = "yes"') == {"trace1"}
    assert search('feedback.correctness = "no"') == {"trace2"}
    assert search("feedback.score > 3") == {"trace2"}
    assert search('feedback.comment LIKE "%helpful%"') == {"trace3"}
    assert search("feedback.correctness IS NOT NULL") == {"trace1", "trace2"}
    assert search("feedback.correctness IS NULL") == {"trace3", "trace4"}
    assert search('expectation.priority = "urgent"') == {"trace4"}


def test_query_assessment_metrics_from_iceberg_backend(monkeypatch, routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-assessment-metrics")
    trace_id = "tr-assessment-metrics"
    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1000,
            execution_duration=10,
            state=TraceState.OK,
            tags={TraceTagKey.TRACE_NAME: "workflow"},
        )
    )

    source = AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="judge")
    for name, value in [
        ("correctness", True),
        ("correctness", False),
        ("quality", "high"),
        ("quality", "high"),
        ("score", 0.5),
        ("score", 0.9),
    ]:
        store.create_assessment(Feedback(trace_id=trace_id, name=name, value=value, source=source))

    calls = []
    original = store._run_duckdb_query

    def recording_run_duckdb_query(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(store, "_run_duckdb_query", recording_run_duckdb_query)

    count_points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.ASSESSMENTS,
        metric_name=AssessmentMetricKey.ASSESSMENT_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        dimensions=[
            AssessmentMetricDimensionKey.ASSESSMENT_NAME,
            AssessmentMetricDimensionKey.ASSESSMENT_VALUE,
        ],
    )
    assert [asdict(point) for point in count_points] == [
        {
            "metric_name": AssessmentMetricKey.ASSESSMENT_COUNT,
            "dimensions": {
                AssessmentMetricDimensionKey.ASSESSMENT_NAME: "correctness",
                AssessmentMetricDimensionKey.ASSESSMENT_VALUE: "false",
            },
            "values": {"COUNT": 1},
        },
        {
            "metric_name": AssessmentMetricKey.ASSESSMENT_COUNT,
            "dimensions": {
                AssessmentMetricDimensionKey.ASSESSMENT_NAME: "correctness",
                AssessmentMetricDimensionKey.ASSESSMENT_VALUE: "true",
            },
            "values": {"COUNT": 1},
        },
        {
            "metric_name": AssessmentMetricKey.ASSESSMENT_COUNT,
            "dimensions": {
                AssessmentMetricDimensionKey.ASSESSMENT_NAME: "quality",
                AssessmentMetricDimensionKey.ASSESSMENT_VALUE: '"high"',
            },
            "values": {"COUNT": 2},
        },
        {
            "metric_name": AssessmentMetricKey.ASSESSMENT_COUNT,
            "dimensions": {
                AssessmentMetricDimensionKey.ASSESSMENT_NAME: "score",
                AssessmentMetricDimensionKey.ASSESSMENT_VALUE: "0.5",
            },
            "values": {"COUNT": 1},
        },
        {
            "metric_name": AssessmentMetricKey.ASSESSMENT_COUNT,
            "dimensions": {
                AssessmentMetricDimensionKey.ASSESSMENT_NAME: "score",
                AssessmentMetricDimensionKey.ASSESSMENT_VALUE: "0.9",
            },
            "values": {"COUNT": 1},
        },
    ]
    count_query = calls[-1]
    assessment_scan = count_query["assessment_rows"]
    assert count_query["trace_rows"] is None
    assert {
        "assessment_name",
        "assessment_value_json",
        "experiment_id",
        "trace_request_time_ms",
        "workspace",
    } == set(assessment_scan.projected_columns)
    assert assessment_scan.experiment_ids == (experiment_id,)
    assert assessment_scan.day_column == "trace_request_day"

    avg_points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.ASSESSMENTS,
        metric_name=AssessmentMetricKey.ASSESSMENT_VALUE,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.AVG)],
        dimensions=[AssessmentMetricDimensionKey.ASSESSMENT_NAME],
        filters=["assessment.name = 'score'"],
    )
    assert [asdict(point) for point in avg_points] == [
        {
            "metric_name": AssessmentMetricKey.ASSESSMENT_VALUE,
            "dimensions": {AssessmentMetricDimensionKey.ASSESSMENT_NAME: "score"},
            "values": {"AVG": 0.7},
        }
    ]

    store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.ASSESSMENTS,
        metric_name=AssessmentMetricKey.ASSESSMENT_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        filters=["trace.status = 'OK'"],
    )
    assert calls[-1]["trace_rows"].table_name == iceberg_trace_backend_module._TRACE_INDEX_TABLE


def test_hybrid_assessment_distribution_respects_cold_trace_cap(monkeypatch, hybrid_routed_store):
    store, _ = hybrid_routed_store
    monkeypatch.setattr(
        iceberg_trace_backend_module.MLFLOW_ICEBERG_TRACE_ASSESSMENT_DISTRIBUTION_MAX_TRACES,
        "get",
        lambda: 10,
    )
    monkeypatch.setattr(store, "_count_published_archived_traces", lambda **_kwargs: 11)

    with pytest.raises(MlflowException, match="more than 10 traces") as exc_info:
        store._query_assessment_metrics(
            experiment_ids=["1"],
            metric_name=AssessmentMetricKey.ASSESSMENT_COUNT,
            aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
            dimensions=[
                AssessmentMetricDimensionKey.ASSESSMENT_NAME,
                AssessmentMetricDimensionKey.ASSESSMENT_VALUE,
            ],
        )

    assert exc_info.value.error_code == ErrorCode.Name(RESOURCE_EXHAUSTED)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, 1.0), (False, 0.0), ("yes", 1.0), ("no", 0.0), ("0.25", None), ("true", None)],
)
def test_assessment_aggregate_value_matches_sql_semantics(routed_store, value, expected):
    store, _ = routed_store
    assessment = Feedback(
        trace_id="tr-aggregate-value",
        name="score",
        value=value,
        source=AssessmentSource(
            source_type=AssessmentSourceType.HUMAN,
            source_id="reviewer",
        ),
    )

    row = store._assessment_row(assessment, experiment_id="1")

    assert row["aggregate_value"] == expected
    assert {
        "assessment_value_text_norm",
        "assessment_value_hash",
        "assessment_value_kind",
        "assessment_numeric_value",
    }.isdisjoint(row)


def test_iceberg_assessment_schema(routed_store):
    store, _ = routed_store
    trace_fields = {field.name for field in store._trace_table().schema().fields}
    assessment_fields = {field.name for field in store._assessment_table().schema().fields}

    assert "assessments_json" not in trace_fields
    assert "aggregate_value" in assessment_fields
    assert {
        "assessment_numeric_value",
        "assessment_value_text_norm",
        "assessment_value_hash",
        "assessment_value_kind",
    }.isdisjoint(assessment_fields)


@pytest.mark.parametrize(
    "table_name",
    [
        iceberg_trace_backend_module._TRACE_METRIC_DAILY_ROLLUPS_TABLE,
        iceberg_trace_backend_module._SPAN_COST_DAILY_ROLLUPS_TABLE,
        iceberg_trace_backend_module._ASSESSMENT_DAILY_ROLLUPS_TABLE,
    ],
)
def test_empty_rollup_table_is_not_available(routed_store, table_name):
    store, _ = routed_store

    assert not store._iceberg_table_has_snapshot(table_name)


def test_rollup_omitted_from_published_cut_does_not_use_catalog(monkeypatch, routed_store):
    store, _ = routed_store
    published_cut = iceberg_trace_backend_module._PublishedIcebergCut(
        metadata_locations=MappingProxyType({}),
        snapshot_ids=MappingProxyType({}),
        published_at_ms=123,
    )
    load_calls = []

    monkeypatch.setattr(store, "_published_iceberg_cut", lambda: published_cut)
    monkeypatch.setattr(store._resources.catalog, "load_table", load_calls.append)

    assert not store._iceberg_table_has_snapshot(
        iceberg_trace_backend_module._TRACE_METRIC_DAILY_ROLLUPS_TABLE
    )
    assert load_calls == []


def test_hybrid_assessment_dimensions_fall_back_to_raw_table(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-assessment-rollups")
    archive_root_uri = _archive_root_uri(tmp_path)
    base_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    store.start_trace(
        TraceInfo(
            trace_id="tr-assessment-rollup",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=base_ms,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "tr-assessment-rollup",
                "span",
                span_id=1,
                trace_num=1,
                start_ns=base_ms * 1_000_000,
                end_ns=(base_ms + 10) * 1_000_000,
            )
        ],
    )
    source = AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="judge")
    store.create_assessment(
        Feedback(trace_id="tr-assessment-rollup", name="correctness", value=True, source=source)
    )
    store.create_assessment(
        Feedback(trace_id="tr-assessment-rollup", name="score", value=0.9, source=source)
    )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: base_ms + 5 * 60 * 1000)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=archive_root_uri,
            broader_retention="1m",
        )
        >= 1
    )

    scanned_tables = []
    original = store._run_duckdb_query

    def record_scans(**kwargs):
        scanned_tables.append({
            scan.table_name
            for scan in kwargs.values()
            if hasattr(scan, "table_name") and scan is not None
        })
        return original(**kwargs)

    monkeypatch.setattr(store, "_run_duckdb_query", record_scans)
    points = store._query_trace_metrics_cold(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.ASSESSMENTS,
        metric_name=AssessmentMetricKey.ASSESSMENT_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        dimensions=[
            AssessmentMetricDimensionKey.ASSESSMENT_NAME,
            AssessmentMetricDimensionKey.ASSESSMENT_VALUE,
        ],
        start_time_ms=base_ms,
        end_time_ms=base_ms + 24 * 60 * 60 * 1000 - 1,
        skip_validation=True,
    )

    assert any(
        iceberg_trace_backend_module._ASSESSMENT_INDEX_TABLE in scan_set
        for scan_set in scanned_tables
    )
    assert all(
        iceberg_trace_backend_module._ASSESSMENT_DAILY_ROLLUPS_TABLE not in scan_set
        for scan_set in scanned_tables
    )
    assert [asdict(point) for point in points] == [
        {
            "metric_name": AssessmentMetricKey.ASSESSMENT_COUNT,
            "dimensions": {
                AssessmentMetricDimensionKey.ASSESSMENT_NAME: "correctness",
                AssessmentMetricDimensionKey.ASSESSMENT_VALUE: "true",
            },
            "values": {"COUNT": 1},
        },
        {
            "metric_name": AssessmentMetricKey.ASSESSMENT_COUNT,
            "dimensions": {
                AssessmentMetricDimensionKey.ASSESSMENT_NAME: "score",
                AssessmentMetricDimensionKey.ASSESSMENT_VALUE: "0.9",
            },
            "values": {"COUNT": 1},
        },
    ]

    scanned_tables.clear()
    daily_points = store._query_trace_metrics_cold(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.ASSESSMENTS,
        metric_name=AssessmentMetricKey.ASSESSMENT_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        dimensions=[AssessmentMetricDimensionKey.ASSESSMENT_NAME],
        time_interval_seconds=24 * 60 * 60,
        start_time_ms=base_ms,
        end_time_ms=base_ms + 24 * 60 * 60 * 1000 - 1,
        skip_validation=True,
    )

    assert any(
        iceberg_trace_backend_module._ASSESSMENT_INDEX_TABLE in scan_set
        for scan_set in scanned_tables
    )
    assert all(
        iceberg_trace_backend_module._ASSESSMENT_DAILY_ROLLUPS_TABLE not in scan_set
        for scan_set in scanned_tables
    )
    assert [point.values for point in daily_points] == [{"COUNT": 1}, {"COUNT": 1}]
    assert all("time_bucket" in point.dimensions for point in daily_points)

    scanned_tables.clear()
    global_points = store._query_trace_metrics_cold(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.ASSESSMENTS,
        metric_name=AssessmentMetricKey.ASSESSMENT_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        start_time_ms=base_ms,
        end_time_ms=base_ms + 24 * 60 * 60 * 1000 - 1,
        skip_validation=True,
    )

    assert any(
        iceberg_trace_backend_module._ASSESSMENT_DAILY_ROLLUPS_TABLE in scan_set
        for scan_set in scanned_tables
    )
    assert [point.values for point in global_points] == [{"COUNT": 2}]


def test_hybrid_assessment_rollups_preserve_delayed_assessments(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-assessment-delayed-rollups")
    archive_root_uri = _archive_root_uri(tmp_path)
    day_ms = 24 * 60 * 60 * 1000
    base_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    store.start_trace(
        TraceInfo(
            trace_id="tr-assessment-delayed",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=base_ms,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "tr-assessment-delayed",
                "span",
                span_id=1,
                trace_num=1,
                start_ns=base_ms * 1_000_000,
                end_ns=(base_ms + 10) * 1_000_000,
            )
        ],
    )
    source = AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="judge")
    store.create_assessment(
        Feedback(
            trace_id="tr-assessment-delayed",
            name="score",
            value=0.9,
            source=source,
            create_time_ms=base_ms + 2 * day_ms,
        )
    )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: base_ms + 3 * day_ms)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=archive_root_uri,
            broader_retention="1m",
        )
        >= 1
    )

    scanned_tables = []
    original_run_duckdb_query = store._run_duckdb_query

    def record_scans(**kwargs):
        scanned_tables.extend(
            scan.table_name
            for scan in kwargs.values()
            if hasattr(scan, "table_name") and scan is not None
        )
        return original_run_duckdb_query(**kwargs)

    monkeypatch.setattr(store, "_run_duckdb_query", record_scans)
    points = store._query_trace_metrics_cold(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.ASSESSMENTS,
        metric_name=AssessmentMetricKey.ASSESSMENT_VALUE,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.AVG)],
        dimensions=[AssessmentMetricDimensionKey.ASSESSMENT_NAME],
        filters=["assessment.name = 'score'"],
        time_interval_seconds=24 * 60 * 60,
        start_time_ms=base_ms,
        end_time_ms=base_ms + day_ms - 1,
        skip_validation=True,
    )
    assert [asdict(point) for point in points] == [
        {
            "metric_name": AssessmentMetricKey.ASSESSMENT_VALUE,
            "dimensions": {
                "time_bucket": "2020-01-01T00:00:00+00:00",
                AssessmentMetricDimensionKey.ASSESSMENT_NAME: "score",
            },
            "values": {"AVG": 0.9},
        }
    ]
    assert iceberg_trace_backend_module._ASSESSMENT_INDEX_TABLE in scanned_tables
    assert iceberg_trace_backend_module._ASSESSMENT_DAILY_ROLLUPS_TABLE not in scanned_tables

    store.start_trace(
        TraceInfo(
            trace_id="tr-assessment-delayed-hot",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=base_ms,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.create_assessment(
        Feedback(
            trace_id="tr-assessment-delayed-hot",
            name="score",
            value=0.7,
            source=source,
            create_time_ms=base_ms + 2 * day_ms,
        )
    )
    hybrid_points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.ASSESSMENTS,
        metric_name=AssessmentMetricKey.ASSESSMENT_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        dimensions=[AssessmentMetricDimensionKey.ASSESSMENT_NAME],
        filters=["assessment.name = 'score'"],
        time_interval_seconds=24 * 60 * 60,
        start_time_ms=base_ms,
        end_time_ms=base_ms + day_ms - 1,
    )
    assert [asdict(point) for point in hybrid_points] == [
        {
            "metric_name": AssessmentMetricKey.ASSESSMENT_COUNT,
            "dimensions": {
                "time_bucket": "2020-01-01T00:00:00+00:00",
                AssessmentMetricDimensionKey.ASSESSMENT_NAME: "score",
            },
            "values": {"COUNT": 2.0},
        }
    ]


def test_query_trace_token_metrics_from_iceberg_backend(routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-trace-token-metrics")

    traces = [
        ("trace1", "workflow_a", 150),
        ("trace2", "workflow_a", 300),
        ("trace3", "workflow_b", 450),
    ]
    for trace_id, name, total_tokens in traces:
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=1000,
                execution_duration=10,
                state=TraceState.OK,
                tags={TraceTagKey.TRACE_NAME: name},
                trace_metadata={
                    TraceMetadataKey.TOKEN_USAGE: json.dumps({"total_tokens": total_tokens})
                },
            )
        )

    result = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.TOTAL_TOKENS,
        aggregations=[
            MetricAggregation(aggregation_type=AggregationType.SUM),
            MetricAggregation(aggregation_type=AggregationType.AVG),
        ],
        dimensions=[TraceMetricDimensionKey.TRACE_NAME],
    )

    assert [asdict(point) for point in result] == [
        {
            "metric_name": TraceMetricKey.TOTAL_TOKENS,
            "dimensions": {TraceMetricDimensionKey.TRACE_NAME: "workflow_a"},
            "values": {"SUM": 450.0, "AVG": 225.0},
        },
        {
            "metric_name": TraceMetricKey.TOTAL_TOKENS,
            "dimensions": {TraceMetricDimensionKey.TRACE_NAME: "workflow_b"},
            "values": {"SUM": 450.0, "AVG": 450.0},
        },
    ]


def test_query_trace_metrics_rejects_non_integer_limit(routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-metric-limit-validation")

    with pytest.raises(MlflowException, match="max_results must be a non-negative integer"):
        store.query_trace_metrics(
            experiment_ids=[experiment_id],
            view_type=MetricViewType.TRACES,
            metric_name=TraceMetricKey.TRACE_COUNT,
            aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
            max_results="1; SELECT 1",
        )


def test_query_trace_and_assessment_metrics_with_time_buckets(routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-time-buckets")
    base_time = 1_577_836_800_000
    hour_ms = 60 * 60 * 1000

    for trace_id, timestamp in [
        ("trace1", base_time),
        ("trace2", base_time + 10 * 60 * 1000),
        ("trace3", base_time + hour_ms),
    ]:
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=timestamp,
                execution_duration=10,
                state=TraceState.OK,
            )
        )

    trace_points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.TRACE_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        time_interval_seconds=3600,
        start_time_ms=base_time,
        end_time_ms=base_time + 2 * hour_ms,
    )
    assert [asdict(point) for point in trace_points] == [
        {
            "metric_name": TraceMetricKey.TRACE_COUNT,
            "dimensions": {
                "time_bucket": datetime.fromtimestamp(base_time / 1000, tz=timezone.utc).isoformat()
            },
            "values": {"COUNT": 2},
        },
        {
            "metric_name": TraceMetricKey.TRACE_COUNT,
            "dimensions": {
                "time_bucket": datetime.fromtimestamp(
                    (base_time + hour_ms) / 1000, tz=timezone.utc
                ).isoformat()
            },
            "values": {"COUNT": 1},
        },
    ]

    source = AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="judge")
    for trace_id, timestamp in [
        ("trace1", base_time),
        ("trace1", base_time + 10 * 60 * 1000),
        ("trace2", base_time + hour_ms),
    ]:
        store.create_assessment(
            Feedback(
                trace_id=trace_id,
                name="quality",
                value=True,
                source=source,
                create_time_ms=timestamp,
            )
        )

    assessment_points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.ASSESSMENTS,
        metric_name=AssessmentMetricKey.ASSESSMENT_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        time_interval_seconds=3600,
        start_time_ms=base_time,
        end_time_ms=base_time + 2 * hour_ms,
    )
    assert [asdict(point) for point in assessment_points] == [
        {
            "metric_name": AssessmentMetricKey.ASSESSMENT_COUNT,
            "dimensions": {
                "time_bucket": datetime.fromtimestamp(base_time / 1000, tz=timezone.utc).isoformat()
            },
            "values": {"COUNT": 3},
        },
    ]


def test_hybrid_archive_writes_daily_rollup_rows(monkeypatch, hybrid_routed_store, tmp_path: Path):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-rollup-write")
    archive_root_uri = _archive_root_uri(tmp_path)
    base_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    store.start_trace(
        TraceInfo(
            trace_id="tr-rollup-write",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=base_ms,
            execution_duration=25,
            state=TraceState.OK,
            tags={TraceTagKey.TRACE_NAME: "workflow"},
            trace_metadata={TraceMetadataKey.TOKEN_USAGE: json.dumps({"total_tokens": 123})},
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "tr-rollup-write",
                "llm_call",
                span_id=1,
                trace_num=1,
                start_ns=base_ms * 1_000_000,
                end_ns=(base_ms + 25) * 1_000_000,
                attributes={
                    "mlflow.llm.cost": {
                        "input_cost": 0.01,
                        "output_cost": 0.02,
                        "total_cost": 0.03,
                    },
                    "mlflow.llm.model": "gpt-4",
                    "mlflow.llm.provider": "openai",
                },
            )
        ],
    )
    source = AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="judge")
    store.create_assessment(
        Feedback(trace_id="tr-rollup-write", name="quality", value=True, source=source)
    )
    store.create_assessment(
        Feedback(trace_id="tr-rollup-write", name="score", value=0.8, source=source)
    )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: base_ms + 5 * 60 * 1000)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=archive_root_uri,
            broader_retention="1m",
        )
        >= 1
    )

    trace_rollups = _read_iceberg_table_rows(
        store, iceberg_trace_backend_module._TRACE_METRIC_DAILY_ROLLUPS_TABLE
    )
    assert any(
        row["metric_name"] == TraceMetricKey.TRACE_COUNT
        and row["experiment_id"] == str(experiment_id)
        and row["workspace"] == DEFAULT_WORKSPACE_NAME
        and row["rollup_day"] == datetime(2020, 1, 1, tzinfo=timezone.utc).date()
        and row["grouping_set"] == iceberg_trace_backend_module._ROLLUP_GROUP_GLOBAL
        for row in trace_rollups
    )
    assert any(
        row["metric_name"] == TraceMetricKey.LATENCY and row["p50_value"] == 25.0
        for row in trace_rollups
    )

    span_rollups = _read_iceberg_table_rows(
        store, iceberg_trace_backend_module._SPAN_COST_DAILY_ROLLUPS_TABLE
    )
    assert any(
        row["metric_name"] == SpanMetricKey.TOTAL_COST
        and row["model_provider"] == "openai"
        and row["grouping_set"] == iceberg_trace_backend_module._ROLLUP_GROUP_MODEL_PROVIDER
        and row["sum_value"] == 0.03
        for row in span_rollups
    )

    assessment_rollups = _read_iceberg_table_rows(
        store, iceberg_trace_backend_module._ASSESSMENT_DAILY_ROLLUPS_TABLE
    )
    assert any(
        row["metric_name"] == AssessmentMetricKey.ASSESSMENT_COUNT
        and row["grouping_set"] == iceberg_trace_backend_module._ROLLUP_GROUP_GLOBAL
        and row["sample_count"] == 2
        for row in assessment_rollups
    )
    assert any(
        row["metric_name"] == AssessmentMetricKey.ASSESSMENT_VALUE
        and row["grouping_set"] == iceberg_trace_backend_module._ROLLUP_GROUP_GLOBAL
        and row["sample_count"] == 2
        and row["sum_value"] == 1.8
        and row["min_value"] == 0.8
        and row["max_value"] == 1.0
        for row in assessment_rollups
    )
    assert all(
        {"assessment_name", "assessment_value_json", "assessment_value_text"}.isdisjoint(row)
        for row in assessment_rollups
    )


def test_hybrid_archive_hands_sql_rollups_off_to_iceberg(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    monkeypatch.setenv(MLFLOW_SQL_TRACE_ROLLUPS_ENABLED.name, "true")
    experiment_id = store.create_experiment("iceberg-sql-rollup-handoff")
    archive_root_uri = _archive_root_uri(tmp_path)
    day_ms = 24 * 60 * 60 * 1000
    base_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    source = AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="judge")

    for offset in range(2):
        trace_id = f"tr-sql-rollup-handoff-{offset}"
        timestamp_ms = base_ms + offset * day_ms
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=timestamp_ms,
                execution_duration=10,
                state=TraceState.OK,
            )
        )
        store.log_spans(
            experiment_id,
            [
                create_test_span(
                    trace_id,
                    f"span-{offset}",
                    span_id=offset + 1,
                    trace_num=offset + 1,
                    start_ns=timestamp_ms * 1_000_000,
                    end_ns=(timestamp_ms + 10) * 1_000_000,
                    attributes={"mlflow.llm.cost": {"total_cost": 0.1}},
                )
            ],
        )
        store.create_assessment(
            Feedback(trace_id=trace_id, name="quality", value=True, source=source)
        )

    assert store.build_sql_trace_rollups(max_partitions=10) == 6
    assert all(count > 0 for count in _sql_rollup_counts(store))

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: base_ms + 3 * day_ms)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=archive_root_uri,
            broader_retention="1m",
            max_traces_per_pass=1,
        )
        == 1
    )

    assert not _tracking_trace_row_exists(store, "tr-sql-rollup-handoff-0")
    assert _tracking_trace_row_exists(store, "tr-sql-rollup-handoff-1")
    assert _sql_rollup_counts(store) == (0, 0, 0)
    assert any(
        row["experiment_id"] == experiment_id
        for row in _read_iceberg_table_rows(
            store, iceberg_trace_backend_module._TRACE_METRIC_DAILY_ROLLUPS_TABLE
        )
    )

    points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.TRACE_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        start_time_ms=base_ms,
        end_time_ms=base_ms + 2 * day_ms - 1,
    )
    assert [point.values for point in points] == [{"COUNT": 2.0}]

    assert store.build_sql_trace_rollups(max_partitions=10) == 3
    assert all(count > 0 for count in _sql_rollup_counts(store))


def test_hybrid_sql_rollup_handoff_preserves_cross_midnight_hot_spans(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    monkeypatch.setenv(MLFLOW_SQL_TRACE_ROLLUPS_ENABLED.name, "true")
    experiment_id = store.create_experiment("iceberg-cross-midnight-sql-rollup-handoff")
    day_ms = 24 * 60 * 60 * 1000
    base_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    request_time_ms = base_ms + day_ms
    trace_costs = {
        "tr-a-archive-cross-midnight": 0.1,
        "tr-z-hot-cross-midnight": 0.2,
    }
    for index, (trace_id, total_cost) in enumerate(trace_costs.items()):
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=request_time_ms,
                execution_duration=10,
                state=TraceState.OK,
            )
        )
        store.log_spans(
            experiment_id,
            [
                create_test_span(
                    trace_id,
                    "cross-midnight-span",
                    span_id=index + 1,
                    trace_num=index + 1,
                    start_ns=(base_ms + 1_000 + index) * 1_000_000,
                    end_ns=(base_ms + 1_010 + index) * 1_000_000,
                    attributes={"mlflow.llm.cost": {"total_cost": total_cost}},
                )
            ],
        )

    assert store.build_sql_trace_rollups(max_partitions=10) == 2
    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: base_ms + 3 * day_ms)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=_archive_root_uri(tmp_path),
            broader_retention="1m",
            max_traces_per_pass=1,
        )
        == 1
    )

    def query_hot_span_cost():
        return sqlalchemy_store_module.SqlAlchemyStore.query_trace_metrics(
            store,
            experiment_ids=[experiment_id],
            view_type=MetricViewType.SPANS,
            metric_name=SpanMetricKey.TOTAL_COST,
            aggregations=[MetricAggregation(aggregation_type=AggregationType.SUM)],
            start_time_ms=base_ms,
            end_time_ms=base_ms + day_ms - 1,
        )

    assert [point.values for point in query_hot_span_cost()] == [{"SUM": 0.2}]
    assert store.build_sql_trace_rollups(max_partitions=10) == 2
    assert [point.values for point in query_hot_span_cost()] == [{"SUM": 0.2}]

    hybrid_points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.SPANS,
        metric_name=SpanMetricKey.TOTAL_COST,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.SUM)],
        start_time_ms=base_ms,
        end_time_ms=base_ms + day_ms - 1,
    )
    assert [point.values for point in hybrid_points] == [{"SUM": pytest.approx(0.3)}]


def test_sql_rollups_rebuild_after_late_assessment(monkeypatch, hybrid_routed_store):
    store, _ = hybrid_routed_store
    monkeypatch.setenv(MLFLOW_SQL_TRACE_ROLLUPS_ENABLED.name, "true")
    experiment_id = store.create_experiment("sql-rollup-late-assessment")
    base_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    trace_id = "tr-sql-rollup-late-assessment"
    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=base_ms,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    source = AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="judge")
    store.create_assessment(Feedback(trace_id=trace_id, name="first", value=True, source=source))
    assert store.build_sql_trace_rollups(max_partitions=10) == 2
    with store.ManagedSessionMaker() as session:
        assert (
            session
            .query(SqlAssessmentDailyRollup)
            .filter(
                SqlAssessmentDailyRollup.experiment_id == int(experiment_id),
                SqlAssessmentDailyRollup.metric_name == AssessmentMetricKey.ASSESSMENT_COUNT,
                SqlAssessmentDailyRollup.grouping_set
                == sqlalchemy_store_module._SQL_ROLLUP_GROUP_GLOBAL,
            )
            .count()
            == 1
        )
        assert session.query(SqlTraceRollupRebuild).count() == 0

    store.create_assessment(Feedback(trace_id=trace_id, name="late", value=True, source=source))
    with store.ManagedSessionMaker() as session:
        assert (
            session
            .query(SqlTraceRollupRebuild)
            .filter(
                SqlTraceRollupRebuild.experiment_id == int(experiment_id),
                SqlTraceRollupRebuild.rollup_family
                == sqlalchemy_store_module._SQL_ASSESSMENT_ROLLUP_FAMILY,
            )
            .count()
            == 1
        )
    points = sqlalchemy_store_module.SqlAlchemyStore.query_trace_metrics(
        store,
        experiment_ids=[experiment_id],
        view_type=MetricViewType.ASSESSMENTS,
        metric_name=AssessmentMetricKey.ASSESSMENT_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        start_time_ms=base_ms,
        end_time_ms=base_ms + 24 * 60 * 60 * 1000 - 1,
    )
    assert [point.values for point in points] == [{"COUNT": 2}]
    assert store.build_sql_trace_rollups(max_partitions=10) == 1


@pytest.mark.parametrize(
    "model",
    [SqlTraceMetricDailyRollup, SqlSpanCostDailyRollup, SqlAssessmentDailyRollup],
)
def test_sql_rollups_use_identity_keys_and_explicit_grouping_sets(model):
    assert "id" in model.__table__.primary_key.columns
    assert "rollup_key" not in model.__table__.columns
    assert model.__table__.columns.grouping_set.nullable is False


def test_sql_trace_rollups_rebuild_after_late_span(monkeypatch, hybrid_routed_store):
    store, _ = hybrid_routed_store
    monkeypatch.setenv(MLFLOW_SQL_TRACE_ROLLUPS_ENABLED.name, "true")
    experiment_id = store.create_experiment("sql-rollup-late-span")
    base_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    trace_id = "tr-sql-rollup-late-span"

    def log_span(span_id: int, input_tokens: int):
        store.log_spans(
            experiment_id,
            [
                create_test_span(
                    trace_id,
                    f"span-{span_id}",
                    span_id=span_id,
                    trace_num=1,
                    start_ns=(base_ms + span_id) * 1_000_000,
                    end_ns=(base_ms + span_id + 1) * 1_000_000,
                    attributes={
                        SpanAttributeKey.CHAT_USAGE: json.dumps({
                            "input_tokens": input_tokens,
                            "total_tokens": input_tokens,
                        })
                    },
                )
            ],
        )

    log_span(1, 5)
    assert store.build_sql_trace_rollups(max_partitions=10) == 2
    log_span(2, 7)

    def query_input_tokens():
        return sqlalchemy_store_module.SqlAlchemyStore.query_trace_metrics(
            store,
            experiment_ids=[experiment_id],
            view_type=MetricViewType.TRACES,
            metric_name=TraceMetricKey.INPUT_TOKENS,
            aggregations=[MetricAggregation(aggregation_type=AggregationType.SUM)],
            start_time_ms=base_ms,
            end_time_ms=base_ms + 24 * 60 * 60 * 1000 - 1,
        )

    assert [point.values for point in query_input_tokens()] == [{"SUM": 12}]
    assert store.build_sql_trace_rollups(max_partitions=10) == 2
    assert [point.values for point in query_input_tokens()] == [{"SUM": 12}]


def test_sql_rollups_require_coverage_for_every_experiment(monkeypatch, hybrid_routed_store):
    store, _ = hybrid_routed_store
    monkeypatch.setenv(MLFLOW_SQL_TRACE_ROLLUPS_ENABLED.name, "true")
    base_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    experiment_ids = [store.create_experiment(f"sql-rollup-coverage-{index}") for index in range(2)]
    for index, experiment_id in enumerate(experiment_ids):
        store.start_trace(
            TraceInfo(
                trace_id=f"tr-sql-rollup-coverage-{index}",
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=base_ms,
                execution_duration=10,
                state=TraceState.OK,
            )
        )
    assert store.build_sql_trace_rollups(max_partitions=10) == 2
    with store.ManagedSessionMaker(read_only=False) as session:
        session.query(SqlTraceMetricDailyRollup).filter(
            SqlTraceMetricDailyRollup.experiment_id == int(experiment_ids[1]),
            SqlTraceMetricDailyRollup.metric_name == TraceMetricKey.TRACE_COUNT,
            SqlTraceMetricDailyRollup.grouping_set
            == sqlalchemy_store_module._SQL_ROLLUP_GROUP_GLOBAL,
        ).delete(synchronize_session=False)

    points = sqlalchemy_store_module.SqlAlchemyStore.query_trace_metrics(
        store,
        experiment_ids=experiment_ids,
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.TRACE_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        start_time_ms=base_ms,
        end_time_ms=base_ms + 24 * 60 * 60 * 1000 - 1,
    )
    assert [point.values for point in points] == [{"COUNT": 2}]


def test_sql_trace_rollups_invalidate_on_tag_mutation_and_delete(monkeypatch, hybrid_routed_store):
    store, _ = hybrid_routed_store
    monkeypatch.setenv(MLFLOW_SQL_TRACE_ROLLUPS_ENABLED.name, "true")
    experiment_id = store.create_experiment("sql-rollup-trace-mutations")
    base_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    trace_id = "tr-sql-rollup-trace-mutations"
    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=base_ms,
            execution_duration=10,
            state=TraceState.OK,
            tags={TraceTagKey.TRACE_NAME: "before"},
        )
    )
    assert store.build_sql_trace_rollups(max_partitions=10) == 1

    store.set_trace_tag(trace_id, TraceTagKey.TRACE_NAME, "after")
    assert store.build_sql_trace_rollups(max_partitions=10) == 1
    points = sqlalchemy_store_module.SqlAlchemyStore.query_trace_metrics(
        store,
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.TRACE_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        dimensions=[TraceMetricDimensionKey.TRACE_NAME],
        start_time_ms=base_ms,
        end_time_ms=base_ms + 24 * 60 * 60 * 1000 - 1,
    )
    assert [point.dimensions for point in points] == [{TraceMetricDimensionKey.TRACE_NAME: "after"}]

    assert store.delete_traces(experiment_id, trace_ids=[trace_id]) == 1
    deleted_points = sqlalchemy_store_module.SqlAlchemyStore.query_trace_metrics(
        store,
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.TRACE_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        start_time_ms=base_ms,
        end_time_ms=base_ms + 24 * 60 * 60 * 1000 - 1,
    )
    assert [point.values for point in deleted_points] == [{"COUNT": 0}]


def test_hybrid_archive_rolls_back_sql_rollup_deletion_on_publication_failure(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    monkeypatch.setenv(MLFLOW_SQL_TRACE_ROLLUPS_ENABLED.name, "true")
    experiment_id = store.create_experiment("iceberg-sql-rollup-rollback")
    trace_id = "tr-sql-rollup-rollback"
    base_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=base_ms,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                trace_id,
                "span",
                span_id=1,
                trace_num=1,
                start_ns=base_ms * 1_000_000,
                end_ns=(base_ms + 10) * 1_000_000,
                attributes={"mlflow.llm.cost": {"total_cost": 0.1}},
            )
        ],
    )
    store.create_assessment(
        Feedback(
            trace_id=trace_id,
            name="quality",
            value=True,
            source=AssessmentSource(
                source_type=AssessmentSourceType.HUMAN,
                source_id="judge",
            ),
        )
    )
    assert store.build_sql_trace_rollups(max_partitions=10) == 3
    rollup_counts = _sql_rollup_counts(store)
    assert all(count > 0 for count in rollup_counts)

    def fail_publication(*_args, **_kwargs):
        raise sa.exc.SQLAlchemyError("publish failed")

    monkeypatch.setattr(store, "_delete_review_queue_items_for_traces", fail_publication)
    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: base_ms + 2 * 60 * 1000)

    assert (
        store.archive_traces(
            resolved_trace_archival_location=_archive_root_uri(tmp_path),
            broader_retention="1m",
        )
        == 0
    )
    assert _tracking_trace_row_exists(store, trace_id)
    assert _sql_rollup_counts(store) == rollup_counts


def test_hybrid_trace_metric_queries_use_rollup_table(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-trace-rollups")
    archive_root_uri = _archive_root_uri(tmp_path)
    day_ms = 24 * 60 * 60 * 1000
    base_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    for idx, (timestamp_ms, latency_ms) in enumerate([
        (base_ms, 10),
        (base_ms + day_ms, 20),
        (base_ms + day_ms, 40),
    ]):
        store.start_trace(
            TraceInfo(
                trace_id=f"tr-rollup-{idx}",
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=timestamp_ms,
                execution_duration=latency_ms,
                state=TraceState.OK,
            )
        )
        store.log_spans(
            experiment_id,
            [
                create_test_span(
                    f"tr-rollup-{idx}",
                    f"span-{idx}",
                    span_id=idx + 1,
                    trace_num=idx + 1,
                    start_ns=timestamp_ms * 1_000_000,
                    end_ns=(timestamp_ms + latency_ms) * 1_000_000,
                )
            ],
        )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: base_ms + 3 * day_ms)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=archive_root_uri,
            broader_retention="1m",
        )
        >= 3
    )

    scanned_tables = []
    original = store._run_duckdb_query

    def record_scans(**kwargs):
        scanned_tables.append({
            scan.table_name
            for scan in kwargs.values()
            if hasattr(scan, "table_name") and scan is not None
        })
        return original(**kwargs)

    monkeypatch.setattr(store, "_run_duckdb_query", record_scans)

    points = store._query_trace_metrics_cold(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.LATENCY,
        aggregations=[
            MetricAggregation(aggregation_type=AggregationType.AVG),
            MetricAggregation(aggregation_type=AggregationType.PERCENTILE, percentile_value=50),
        ],
        time_interval_seconds=24 * 60 * 60,
        start_time_ms=base_ms,
        end_time_ms=base_ms + 2 * day_ms - 1,
        skip_validation=True,
    )

    assert any(
        iceberg_trace_backend_module._TRACE_METRIC_DAILY_ROLLUPS_TABLE in scan_set
        for scan_set in scanned_tables
    )
    assert [asdict(point) for point in points] == [
        {
            "metric_name": TraceMetricKey.LATENCY,
            "dimensions": {"time_bucket": "2020-01-01T00:00:00+00:00"},
            "values": {"AVG": 10.0, "P50": 10.0},
        },
        {
            "metric_name": TraceMetricKey.LATENCY,
            "dimensions": {"time_bucket": "2020-01-02T00:00:00+00:00"},
            "values": {"AVG": 30.0, "P50": 30.0},
        },
    ]

    scanned_tables.clear()
    count_points = store._query_trace_metrics_cold(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.TRACE_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        start_time_ms=base_ms,
        end_time_ms=base_ms + 2 * day_ms - 1,
        skip_validation=True,
    )
    assert [asdict(point) for point in count_points] == [
        {"metric_name": TraceMetricKey.TRACE_COUNT, "dimensions": {}, "values": {"COUNT": 3.0}}
    ]
    assert scanned_tables == [{iceberg_trace_backend_module._TRACE_METRIC_DAILY_ROLLUPS_TABLE}]

    scanned_tables.clear()
    fallback_points = store._query_trace_metrics_cold(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.LATENCY,
        aggregations=[
            MetricAggregation(aggregation_type=AggregationType.PERCENTILE, percentile_value=50)
        ],
        time_interval_seconds=2 * 24 * 60 * 60,
        start_time_ms=base_ms,
        end_time_ms=base_ms + 2 * day_ms - 1,
        skip_validation=True,
    )
    assert any(
        iceberg_trace_backend_module._TRACE_INDEX_TABLE in scan_set for scan_set in scanned_tables
    )
    assert all(
        iceberg_trace_backend_module._TRACE_METRIC_DAILY_ROLLUPS_TABLE not in scan_set
        for scan_set in scanned_tables
    )
    assert [asdict(point) for point in fallback_points] == [
        {
            "metric_name": TraceMetricKey.LATENCY,
            "dimensions": {"time_bucket": "2020-01-01T00:00:00+00:00"},
            "values": {"P50": 20.0},
        }
    ]

    scanned_tables.clear()
    store._query_trace_metrics_cold(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.TRACE_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        dimensions=[TraceMetricDimensionKey.TRACE_NAME],
        start_time_ms=base_ms,
        end_time_ms=base_ms + 2 * day_ms - 1,
        skip_validation=True,
    )
    assert iceberg_trace_backend_module._TRACE_INDEX_TABLE in {
        table_name for scan_set in scanned_tables for table_name in scan_set
    }
    assert all(
        iceberg_trace_backend_module._TRACE_METRIC_DAILY_ROLLUPS_TABLE not in scan_set
        for scan_set in scanned_tables
    )


def test_query_span_metrics_from_iceberg_backend(routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-span-metrics")
    trace_id = "tr-span-metrics"
    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )

    spans = [
        create_test_span(
            trace_id,
            "generate_response",
            span_id=1,
            span_type="LLM",
            status=trace_api.StatusCode.OK,
            start_ns=1_000_000_000,
            end_ns=1_100_000_000,
        ),
        create_test_span(
            trace_id,
            "generate_response",
            span_id=2,
            span_type="LLM",
            status=trace_api.StatusCode.OK,
            start_ns=2_000_000_000,
            end_ns=2_200_000_000,
        ),
        create_test_span(
            trace_id,
            "tool_call",
            span_id=3,
            span_type="TOOL",
            status=trace_api.StatusCode.ERROR,
            start_ns=3_000_000_000,
            end_ns=3_300_000_000,
        ),
    ]
    store.log_spans(experiment_id, spans)

    count_points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.SPANS,
        metric_name=SpanMetricKey.SPAN_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        dimensions=[SpanMetricDimensionKey.SPAN_TYPE],
    )
    assert [asdict(point) for point in count_points] == [
        {
            "metric_name": SpanMetricKey.SPAN_COUNT,
            "dimensions": {SpanMetricDimensionKey.SPAN_TYPE: "LLM"},
            "values": {"COUNT": 2},
        },
        {
            "metric_name": SpanMetricKey.SPAN_COUNT,
            "dimensions": {SpanMetricDimensionKey.SPAN_TYPE: "TOOL"},
            "values": {"COUNT": 1},
        },
    ]

    latency_points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.SPANS,
        metric_name=SpanMetricKey.LATENCY,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.AVG)],
        dimensions=[SpanMetricDimensionKey.SPAN_NAME],
        filters=["span.type = 'LLM'"],
    )
    assert [asdict(point) for point in latency_points] == [
        {
            "metric_name": SpanMetricKey.LATENCY,
            "dimensions": {SpanMetricDimensionKey.SPAN_NAME: "generate_response"},
            "values": {"AVG": 150.0},
        }
    ]


def test_search_traces_with_span_filters_from_iceberg_backend(routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-span-search")
    for trace_id in ["trace1", "trace2", "trace3"]:
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=1000,
                execution_duration=10,
                state=TraceState.OK,
            )
        )

    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "trace1",
                "search_web",
                span_id=111,
                span_type="TOOL",
                status=trace_api.StatusCode.ERROR,
                attributes={"model": "gpt-4"},
            ),
            create_test_span(
                "trace1",
                "other_tool",
                span_id=112,
                span_type="TOOL",
                status=trace_api.StatusCode.OK,
                attributes={"provider": "anthropic"},
            ),
        ],
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "trace2",
                "search_web",
                span_id=222,
                span_type="TOOL",
                status=trace_api.StatusCode.OK,
                attributes={"model": "claude-3"},
            )
        ],
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "trace3",
                "retriever",
                span_id=333,
                span_type="RETRIEVER",
                status=trace_api.StatusCode.OK,
                attributes={"database": "pinecone"},
            )
        ],
    )

    def search(filter_string: str):
        traces, _ = store.search_traces(
            [experiment_id], filter_string=filter_string, max_results=20
        )
        return {trace.trace_id for trace in traces}

    assert search('span.name = "search_web"') == {"trace1", "trace2"}
    assert search('span.type = "RETRIEVER"') == {"trace3"}
    assert search('span.status = "ERROR"') == {"trace1"}
    assert search('span.content LIKE "%pinecone%"') == {"trace3"}
    assert search('span.attributes.model LIKE "%gpt-4%"') == {"trace1"}
    assert search('span.name = "search_web" AND span.status = "OK"') == {"trace2"}


def test_search_traces_does_not_apply_trace_day_bounds_to_spans(routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-cross-day-span-search")
    request_time_ms = int(datetime(2024, 1, 1, 23, 59, 59, tzinfo=timezone.utc).timestamp() * 1000)
    trace_id = "cross-day-trace"
    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=request_time_ms,
            execution_duration=3000,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                trace_id,
                "after_midnight",
                start_ns=(request_time_ms + 2000) * 1_000_000,
                end_ns=(request_time_ms + 2500) * 1_000_000,
            )
        ],
    )

    traces, _ = store.search_traces(
        [experiment_id],
        filter_string=(
            f"attributes.timestamp_ms >= {request_time_ms} AND "
            f"attributes.timestamp_ms <= {request_time_ms} AND "
            'span.name = "after_midnight"'
        ),
        max_results=10,
    )

    assert [trace.trace_id for trace in traces] == [trace_id]


def test_metric_points_with_zero_values_have_data():
    points = [
        MetricDataPoint(
            metric_name=TraceMetricKey.LATENCY,
            dimensions={},
            values={"MIN": 0.0},
        )
    ]

    assert IcebergSqlAlchemyStore._metric_points_have_data(points)


def test_span_metrics_with_time_buckets_from_iceberg_backend(routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-span-time-buckets")
    trace_id = "tr-span-time-buckets"
    base_time_ns = 1_577_836_800_000_000_000
    hour_ns = 60 * 60 * 1_000_000_000
    base_time_ms = base_time_ns // 1_000_000
    hour_ms = 60 * 60 * 1000

    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=base_time_ms,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(trace_id, "span1", span_id=1, span_type="LLM", start_ns=base_time_ns),
            create_test_span(
                trace_id,
                "span2",
                span_id=2,
                span_type="LLM",
                start_ns=base_time_ns + 10 * 60 * 1_000_000_000,
            ),
            create_test_span(
                trace_id,
                "span3",
                span_id=3,
                span_type="TOOL",
                start_ns=base_time_ns + hour_ns,
            ),
        ],
    )

    result = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.SPANS,
        metric_name=SpanMetricKey.SPAN_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        dimensions=[SpanMetricDimensionKey.SPAN_TYPE],
        time_interval_seconds=3600,
        start_time_ms=base_time_ms,
        end_time_ms=base_time_ms + 2 * hour_ms,
    )
    assert [asdict(point) for point in result] == [
        {
            "metric_name": SpanMetricKey.SPAN_COUNT,
            "dimensions": {
                "time_bucket": datetime.fromtimestamp(
                    base_time_ms / 1000, tz=timezone.utc
                ).isoformat(),
                SpanMetricDimensionKey.SPAN_TYPE: "LLM",
            },
            "values": {"COUNT": 2},
        },
        {
            "metric_name": SpanMetricKey.SPAN_COUNT,
            "dimensions": {
                "time_bucket": datetime.fromtimestamp(
                    (base_time_ms + hour_ms) / 1000, tz=timezone.utc
                ).isoformat(),
                SpanMetricDimensionKey.SPAN_TYPE: "TOOL",
            },
            "values": {"COUNT": 1},
        },
    ]


def test_rollup_rebuilds_scan_each_fact_table_once(monkeypatch, routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-single-scan-rollups")
    base_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    trace_id = "tr-single-scan-rollups"
    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=base_ms,
            execution_duration=25,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                trace_id,
                "llm_call",
                start_ns=base_ms * 1_000_000,
                end_ns=(base_ms + 25) * 1_000_000,
                attributes={"mlflow.llm.cost": {"total_cost": 0.03}},
            )
        ],
    )
    store.create_assessment(
        Feedback(
            trace_id=trace_id,
            name="quality",
            value=0.9,
            source=AssessmentSource(
                source_type=AssessmentSourceType.HUMAN,
                source_id="judge",
            ),
        )
    )

    scans = []
    original = store._run_duckdb_query

    def record_scan(**kwargs):
        scans.extend(
            scan for scan in kwargs.values() if hasattr(scan, "table_name") and scan is not None
        )
        return original(**kwargs)

    monkeypatch.setattr(store, "_run_duckdb_query", record_scan)
    partition_keys = {
        (experiment_id, datetime.fromtimestamp(base_ms / 1000, tz=timezone.utc).date())
    }
    store._rebuild_trace_metric_rollups(partition_keys)
    store._rebuild_span_cost_rollups(partition_keys)
    store._rebuild_assessment_rollups(partition_keys)

    assert [scan.table_name for scan in scans] == [
        iceberg_trace_backend_module._TRACE_INDEX_TABLE,
        iceberg_trace_backend_module._SPAN_INDEX_TABLE,
        iceberg_trace_backend_module._ASSESSMENT_INDEX_TABLE,
    ]
    assessment_scan = scans[-1]
    assert assessment_scan.day_column == "trace_request_day"
    assert assessment_scan.start_time_ms == base_ms
    assert assessment_scan.end_time_ms == base_ms + 24 * 60 * 60 * 1000 - 1


def test_partial_day_metric_range_merges_raw_boundaries_with_rollups(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-partial-day-rollups")
    archive_root_uri = _archive_root_uri(tmp_path)
    day_ms = 24 * 60 * 60 * 1000
    base_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    for offset in range(3):
        trace_id = f"tr-partial-rollup-{offset}"
        timestamp_ms = base_ms + offset * day_ms
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=timestamp_ms,
                execution_duration=10,
                state=TraceState.OK,
            )
        )
        store.log_spans(
            experiment_id,
            [
                create_test_span(
                    trace_id,
                    f"span-{offset}",
                    start_ns=timestamp_ms * 1_000_000,
                    end_ns=(timestamp_ms + 10) * 1_000_000,
                )
            ],
        )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: base_ms + 4 * day_ms)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=archive_root_uri,
            broader_retention="1m",
        )
        == 3
    )

    scanned_tables = []
    original = store._run_duckdb_query

    def record_scans(**kwargs):
        scanned_tables.extend(
            scan.table_name
            for scan in kwargs.values()
            if hasattr(scan, "table_name") and scan is not None
        )
        return original(**kwargs)

    monkeypatch.setattr(store, "_run_duckdb_query", record_scans)
    points = store._query_trace_metrics_cold(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.TRACE_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        start_time_ms=base_ms + day_ms // 2,
        end_time_ms=base_ms + 2 * day_ms + day_ms // 2,
        skip_validation=True,
    )

    assert [asdict(point) for point in points] == [
        {"metric_name": TraceMetricKey.TRACE_COUNT, "dimensions": {}, "values": {"COUNT": 2.0}}
    ]
    assert iceberg_trace_backend_module._TRACE_METRIC_DAILY_ROLLUPS_TABLE in scanned_tables
    assert iceberg_trace_backend_module._TRACE_INDEX_TABLE not in scanned_tables


def test_sparse_rollup_days_do_not_force_raw_fallback(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-missing-rollup-day")
    archive_root_uri = _archive_root_uri(tmp_path)
    day_ms = 24 * 60 * 60 * 1000
    base_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    for offset in (0, 2):
        trace_id = f"tr-missing-rollup-{offset}"
        timestamp_ms = base_ms + offset * day_ms
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=timestamp_ms,
                execution_duration=10,
                state=TraceState.OK,
            )
        )
        store.log_spans(
            experiment_id,
            [
                create_test_span(
                    trace_id,
                    f"span-{offset}",
                    start_ns=timestamp_ms * 1_000_000,
                    end_ns=(timestamp_ms + 10) * 1_000_000,
                )
            ],
        )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: base_ms + 4 * day_ms)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=archive_root_uri,
            broader_retention="1m",
        )
        == 2
    )

    scanned_tables = []
    original = store._run_duckdb_query

    def record_scans(**kwargs):
        scanned_tables.extend(
            scan.table_name
            for scan in kwargs.values()
            if hasattr(scan, "table_name") and scan is not None
        )
        return original(**kwargs)

    monkeypatch.setattr(store, "_run_duckdb_query", record_scans)
    points = store._query_trace_metrics_cold(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.TRACE_COUNT,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        start_time_ms=base_ms,
        end_time_ms=base_ms + 3 * day_ms - 1,
        skip_validation=True,
    )

    assert [asdict(point) for point in points] == [
        {"metric_name": TraceMetricKey.TRACE_COUNT, "dimensions": {}, "values": {"COUNT": 2}}
    ]
    assert iceberg_trace_backend_module._TRACE_INDEX_TABLE not in scanned_tables
    assert iceberg_trace_backend_module._TRACE_METRIC_DAILY_ROLLUPS_TABLE in scanned_tables


def test_partial_day_rollup_drops_helper_only_avg_points():
    store = object.__new__(IcebergSqlAlchemyStore)
    aggregation = MetricAggregation(aggregation_type=AggregationType.AVG)
    empty_avg = PagedList(
        [
            MetricDataPoint(
                metric_name=TraceMetricKey.TOTAL_TOKENS,
                dimensions={},
                values={"AVG": None, "COUNT": 1.0, "SUM": 0.0},
            )
        ],
        None,
    )

    result = store._query_partial_daily_rollups(
        rollup_query=lambda **kwargs: empty_avg,
        raw_query=lambda **kwargs: PagedList([], None),
        query_kwargs={},
        aggregations=[aggregation],
        time_interval_seconds=None,
        start_time_ms=1,
        end_time_ms=2 * 24 * 60 * 60 * 1000,
        max_results=100,
    )

    assert result == []


def test_partial_day_rollup_rejects_non_daily_bucket_width():
    store = object.__new__(IcebergSqlAlchemyStore)

    def unexpected_query(**_kwargs):
        raise AssertionError("36-hour buckets must use raw facts")

    result = store._query_partial_daily_rollups(
        rollup_query=unexpected_query,
        raw_query=unexpected_query,
        query_kwargs={},
        aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
        time_interval_seconds=36 * 60 * 60,
        start_time_ms=1,
        end_time_ms=3 * 24 * 60 * 60 * 1000,
        max_results=100,
    )

    assert result is None


def test_calculate_trace_filter_correlation_from_iceberg_backend(monkeypatch, routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-correlation")

    for idx in range(10):
        store.start_trace(
            TraceInfo(
                trace_id=f"tool-{idx}",
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=1000 + idx,
                execution_duration=10,
                state=TraceState.OK,
                tags={"primary_span_type": "TOOL", "has_error": "true"},
            )
        )
    for idx in range(5):
        store.start_trace(
            TraceInfo(
                trace_id=f"llm-{idx}",
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=2000 + idx,
                execution_duration=10,
                state=TraceState.OK,
                tags={"primary_span_type": "LLM", "has_error": "false"},
            )
        )

    query_count = 0
    original = store._run_duckdb_query

    def count_query(**kwargs):
        nonlocal query_count
        query_count += 1
        return original(**kwargs)

    monkeypatch.setattr(store, "_run_duckdb_query", count_query)
    result = store.calculate_trace_filter_correlation(
        experiment_ids=[experiment_id],
        filter_string1='tags.primary_span_type = "TOOL"',
        filter_string2='tags.has_error = "true"',
    )
    assert result.filter1_count == 10
    assert result.filter2_count == 10
    assert result.joint_count == 10
    assert result.total_count == 15
    assert result.npmi == pytest.approx(1.0)
    assert query_count == 1

    base_filter_result = store.calculate_trace_filter_correlation(
        experiment_ids=[experiment_id],
        filter_string1='tags.primary_span_type = "TOOL"',
        filter_string2='tags.has_error = "true"',
        base_filter="timestamp_ms >= 1000 AND timestamp_ms < 1010",
    )
    assert base_filter_result.total_count == 10
    assert base_filter_result.filter1_count == 10
    assert base_filter_result.filter2_count == 10
    assert base_filter_result.joint_count == 10
    assert query_count == 2


def test_search_traces_session_scoped_assessment_expansion_from_iceberg_backend(routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-session-assessment-expansion")

    for trace_id in ["sa-t1", "sa-t2", "sa-t3"]:
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=1000,
                execution_duration=10,
                state=TraceState.OK,
                trace_metadata={TraceMetadataKey.TRACE_SESSION: "session-a"},
            )
        )
    for trace_id in ["sb-t1", "sb-t2", "sb-t3"]:
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=2000,
                execution_duration=10,
                state=TraceState.OK,
                trace_metadata={TraceMetadataKey.TRACE_SESSION: "session-b"},
            )
        )

    source = AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="judge")
    store.create_assessment(
        Feedback(
            trace_id="sa-t1",
            name="session_quality",
            value="good",
            source=source,
            metadata={TraceMetadataKey.TRACE_SESSION: "session-a"},
        )
    )
    store.create_assessment(
        Feedback(trace_id="sb-t1", name="trace_quality", value="bad", source=source)
    )

    def search(filter_string: str):
        traces, _ = store.search_traces(
            [experiment_id], filter_string=filter_string, max_results=20
        )
        return {trace.trace_id for trace in traces}

    assert search('feedback.session_quality = "good"') == {"sa-t1", "sa-t2", "sa-t3"}
    assert search("feedback.session_quality IS NOT NULL") == {"sa-t1", "sa-t2", "sa-t3"}
    assert search("feedback.session_quality IS NULL") == {"sb-t1", "sb-t2", "sb-t3"}
    assert search('feedback.trace_quality = "bad"') == {"sb-t1"}


def test_search_traces_with_regex_variants_from_iceberg_backend(routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-regex-search")

    store.start_trace(
        TraceInfo(
            trace_id="trace1",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1000,
            execution_duration=10,
            state=TraceState.OK,
            tags={"environment": "production-us-east-1"},
            trace_metadata={"version": "v1.2.3"},
            client_request_id="req-prod-us-east-123",
        )
    )
    store.start_trace(
        TraceInfo(
            trace_id="trace2",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=2000,
            execution_duration=10,
            state=TraceState.OK,
            tags={"environment": "staging-us-west-2"},
            trace_metadata={"version": "v2.0.0-beta"},
            client_request_id="req-staging-eu-789",
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "trace1",
                "llm.generate_response",
                span_id=1,
                span_type="LLM",
                attributes={"model": "gpt-4-turbo-preview"},
            )
        ],
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "trace2",
                "database.query_users",
                span_id=2,
                span_type="TOOL_PARENT",
                attributes={"model": "claude-3-sonnet-20240229"},
            )
        ],
    )
    source = AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="judge")
    store.create_assessment(
        Feedback(
            trace_id="trace1",
            name="comment",
            value="Great response! Very helpful.",
            source=source,
        )
    )

    def search(filter_string: str):
        traces, _ = store.search_traces(
            [experiment_id], filter_string=filter_string, max_results=20
        )
        return {trace.trace_id for trace in traces}

    assert search('tag.environment RLIKE "^production"') == {"trace1"}
    assert search('metadata.version RLIKE "beta"') == {"trace2"}
    assert search('trace.client_request_id RLIKE "^req-prod"') == {"trace1"}
    assert search('span.name RLIKE "^llm\\."') == {"trace1"}
    assert search('span.type RLIKE "PARENT$"') == {"trace2"}
    assert search('span.attributes.model RLIKE "preview"') == {"trace1"}
    assert search('feedback.comment RLIKE "Great.*helpful"') == {"trace1"}


def test_query_span_cost_metrics_from_iceberg_backend(routed_store):
    store, _ = routed_store
    experiment_id = store.create_experiment("iceberg-span-cost-metrics")
    trace_id = "tr-span-cost"
    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                trace_id,
                "openai_call",
                span_id=1,
                span_type="LLM",
                attributes={
                    "mlflow.llm.cost": {
                        "input_cost": 0.01,
                        "output_cost": 0.02,
                        "total_cost": 0.03,
                    },
                    "mlflow.llm.model": "gpt-4",
                    "mlflow.llm.provider": "openai",
                },
            ),
            create_test_span(
                trace_id,
                "anthropic_call",
                span_id=2,
                span_type="LLM",
                attributes={
                    "mlflow.llm.cost": {
                        "input_cost": 0.005,
                        "output_cost": 0.015,
                        "total_cost": 0.02,
                    },
                    "mlflow.llm.model": "claude-3-5-sonnet",
                    "mlflow.llm.provider": "anthropic",
                },
            ),
            create_test_span(
                trace_id,
                "no_provider_call",
                span_id=3,
                span_type="LLM",
                attributes={
                    "mlflow.llm.cost": {
                        "input_cost": 0.1,
                        "output_cost": 0.2,
                        "total_cost": 0.3,
                    }
                },
            ),
        ],
    )

    total_cost = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.SPANS,
        metric_name=SpanMetricKey.TOTAL_COST,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.SUM)],
        dimensions=[SpanMetricDimensionKey.SPAN_MODEL_PROVIDER],
    )
    assert [asdict(point) for point in total_cost] == [
        {
            "metric_name": SpanMetricKey.TOTAL_COST,
            "dimensions": {SpanMetricDimensionKey.SPAN_MODEL_PROVIDER: "anthropic"},
            "values": {"SUM": 0.02},
        },
        {
            "metric_name": SpanMetricKey.TOTAL_COST,
            "dimensions": {SpanMetricDimensionKey.SPAN_MODEL_PROVIDER: "openai"},
            "values": {"SUM": 0.03},
        },
    ]

    input_cost = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.SPANS,
        metric_name=SpanMetricKey.INPUT_COST,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.SUM)],
        dimensions=[SpanMetricDimensionKey.SPAN_MODEL_NAME],
    )
    assert [asdict(point) for point in input_cost] == [
        {
            "metric_name": SpanMetricKey.INPUT_COST,
            "dimensions": {SpanMetricDimensionKey.SPAN_MODEL_NAME: "claude-3-5-sonnet"},
            "values": {"SUM": 0.005},
        },
        {
            "metric_name": SpanMetricKey.INPUT_COST,
            "dimensions": {SpanMetricDimensionKey.SPAN_MODEL_NAME: "gpt-4"},
            "values": {"SUM": 0.01},
        },
    ]


def test_hybrid_span_cost_queries_use_rollup_table(
    monkeypatch, hybrid_routed_store, tmp_path: Path
):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-span-cost-rollups")
    archive_root_uri = _archive_root_uri(tmp_path)
    base_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    store.start_trace(
        TraceInfo(
            trace_id="tr-span-rollup",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=base_ms,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                "tr-span-rollup",
                "gpt_call",
                span_id=1,
                trace_num=1,
                start_ns=base_ms * 1_000_000,
                end_ns=(base_ms + 10) * 1_000_000,
                attributes={
                    "mlflow.llm.cost": {
                        "input_cost": 0.01,
                        "output_cost": 0.02,
                        "total_cost": 0.03,
                    },
                    "mlflow.llm.model": "gpt-4",
                    "mlflow.llm.provider": "openai",
                },
            ),
            create_test_span(
                "tr-span-rollup",
                "claude_call",
                span_id=2,
                trace_num=1,
                start_ns=(base_ms + 20) * 1_000_000,
                end_ns=(base_ms + 30) * 1_000_000,
                attributes={
                    "mlflow.llm.cost": {
                        "input_cost": 0.005,
                        "output_cost": 0.015,
                        "total_cost": 0.02,
                    },
                    "mlflow.llm.model": "claude-3-5-sonnet",
                    "mlflow.llm.provider": "anthropic",
                },
            ),
        ],
    )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: base_ms + 5 * 60 * 1000)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=archive_root_uri,
            broader_retention="1m",
        )
        >= 1
    )

    scanned_tables = []
    original = store._run_duckdb_query

    def record_scans(**kwargs):
        scanned_tables.append({
            scan.table_name
            for scan in kwargs.values()
            if hasattr(scan, "table_name") and scan is not None
        })
        return original(**kwargs)

    monkeypatch.setattr(store, "_run_duckdb_query", record_scans)
    points = store._query_trace_metrics_cold(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.SPANS,
        metric_name=SpanMetricKey.TOTAL_COST,
        aggregations=[MetricAggregation(aggregation_type=AggregationType.SUM)],
        dimensions=[SpanMetricDimensionKey.SPAN_MODEL_PROVIDER],
        start_time_ms=base_ms,
        end_time_ms=base_ms + 24 * 60 * 60 * 1000 - 1,
        skip_validation=True,
    )

    assert any(
        iceberg_trace_backend_module._SPAN_COST_DAILY_ROLLUPS_TABLE in scan_set
        for scan_set in scanned_tables
    )
    assert [asdict(point) for point in points] == [
        {
            "metric_name": SpanMetricKey.TOTAL_COST,
            "dimensions": {SpanMetricDimensionKey.SPAN_MODEL_PROVIDER: "anthropic"},
            "values": {"SUM": 0.02},
        },
        {
            "metric_name": SpanMetricKey.TOTAL_COST,
            "dimensions": {SpanMetricDimensionKey.SPAN_MODEL_PROVIDER: "openai"},
            "values": {"SUM": 0.03},
        },
    ]


def test_hybrid_delete_refreshes_rollup_tables(monkeypatch, hybrid_routed_store, tmp_path: Path):
    store, _ = hybrid_routed_store
    experiment_id = store.create_experiment("iceberg-delete-refreshes-rollups")
    archive_root_uri = _archive_root_uri(tmp_path)
    base_ms = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    source = AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="judge")

    for idx, trace_id in enumerate(["tr-rollup-delete-1", "tr-rollup-delete-2"], start=1):
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=base_ms + idx * 1000,
                execution_duration=10 * idx,
                state=TraceState.OK,
            )
        )
        store.log_spans(
            experiment_id,
            [
                create_test_span(
                    trace_id,
                    f"span-{idx}",
                    span_id=idx,
                    trace_num=idx,
                    start_ns=(base_ms + idx * 1000) * 1_000_000,
                    end_ns=(base_ms + idx * 1000 + 10 * idx) * 1_000_000,
                    attributes={
                        "mlflow.llm.cost": {
                            "input_cost": 0.01,
                            "output_cost": 0.02,
                            "total_cost": 0.03,
                        },
                        "mlflow.llm.model": "gpt-4",
                        "mlflow.llm.provider": "openai",
                    },
                )
            ],
        )
        store.create_assessment(
            Feedback(trace_id=trace_id, name="quality", value=True, source=source)
        )

    monkeypatch.setattr(store, "_get_archive_traces_now_millis", lambda: base_ms + 5 * 60 * 1000)
    assert (
        store.archive_traces(
            resolved_trace_archival_location=archive_root_uri,
            broader_retention="1m",
        )
        >= 2
    )

    def query_all_rollups():
        trace_points = store._query_trace_metrics_cold(
            experiment_ids=[experiment_id],
            view_type=MetricViewType.TRACES,
            metric_name=TraceMetricKey.TRACE_COUNT,
            aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
            start_time_ms=base_ms,
            end_time_ms=base_ms + 24 * 60 * 60 * 1000 - 1,
            skip_validation=True,
        )
        span_points = store._query_trace_metrics_cold(
            experiment_ids=[experiment_id],
            view_type=MetricViewType.SPANS,
            metric_name=SpanMetricKey.TOTAL_COST,
            aggregations=[MetricAggregation(aggregation_type=AggregationType.SUM)],
            start_time_ms=base_ms,
            end_time_ms=base_ms + 24 * 60 * 60 * 1000 - 1,
            skip_validation=True,
        )
        assessment_points = store._query_trace_metrics_cold(
            experiment_ids=[experiment_id],
            view_type=MetricViewType.ASSESSMENTS,
            metric_name=AssessmentMetricKey.ASSESSMENT_COUNT,
            aggregations=[MetricAggregation(aggregation_type=AggregationType.COUNT)],
            start_time_ms=base_ms,
            end_time_ms=base_ms + 24 * 60 * 60 * 1000 - 1,
            skip_validation=True,
        )
        return (
            [asdict(point) for point in trace_points],
            [asdict(point) for point in span_points],
            [asdict(point) for point in assessment_points],
        )

    before_delete = query_all_rollups()
    assert before_delete == (
        [{"metric_name": TraceMetricKey.TRACE_COUNT, "dimensions": {}, "values": {"COUNT": 2}}],
        [{"metric_name": SpanMetricKey.TOTAL_COST, "dimensions": {}, "values": {"SUM": 0.06}}],
        [
            {
                "metric_name": AssessmentMetricKey.ASSESSMENT_COUNT,
                "dimensions": {},
                "values": {"COUNT": 2},
            }
        ],
    )

    assert store.delete_traces(experiment_id=experiment_id, trace_ids=["tr-rollup-delete-1"]) == 1

    after_delete = query_all_rollups()
    assert after_delete == (
        [{"metric_name": TraceMetricKey.TRACE_COUNT, "dimensions": {}, "values": {"COUNT": 1}}],
        [{"metric_name": SpanMetricKey.TOTAL_COST, "dimensions": {}, "values": {"SUM": 0.03}}],
        [
            {
                "metric_name": AssessmentMetricKey.ASSESSMENT_COUNT,
                "dimensions": {},
                "values": {"COUNT": 1},
            }
        ],
    )


def _count_data_files(backend) -> int:
    tables = [
        backend._trace_table(),
        backend._trace_tag_table(),
        backend._span_table(),
        backend._assessment_table(),
        backend._trace_metric_rollup_table(),
        backend._span_cost_rollup_table(),
        backend._assessment_rollup_table(),
    ]
    return sum(len(list(table.scan().plan_files())) for table in tables)


def _compact_results(iceberg_root: Path):
    return {
        result.table_name: result
        for result in run_iceberg_trace_compaction(warehouse_location=str(iceberg_root))
    }


def _search_trace_ids(store, experiment_id: str, *, filter_string: str | None = None) -> set[str]:
    traces, _ = store.search_traces([experiment_id], filter_string=filter_string, max_results=20)
    return {trace.trace_id for trace in traces}


def test_compact_reduces_files_and_preserves_reads(routed_store):
    store, iceberg_root = routed_store
    experiment_id = store.create_experiment("iceberg-compaction")
    backend = store
    source = AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="judge")

    # Build append-only version history across all four tables.
    store.start_trace(
        TraceInfo(
            trace_id="tr-compact-1",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1_700_000_000_000,
            execution_duration=10,
            state=TraceState.OK,
            tags={TraceTagKey.TRACE_NAME: "workflow"},
        )
    )
    store.set_trace_tag("tr-compact-1", "stage", "draft")
    store.set_trace_tag("tr-compact-1", "stage", "final")
    store.create_assessment(
        Feedback(trace_id="tr-compact-1", name="quality", value="good", source=source)
    )
    store.create_assessment(Feedback(trace_id="tr-compact-1", name="score", value=4, source=source))

    store.log_spans(
        experiment_id,
        [
            create_test_span(
                trace_id="tr-compact-2",
                name="root_span",
                span_id=111,
                status=trace_api.StatusCode.OK,
                start_ns=1_700_000_000_000_000_000,
                end_ns=1_700_000_001_000_000_000,
                trace_num=222,
            ),
            create_test_span(
                trace_id="tr-compact-2",
                name="child_span",
                span_id=222,
                parent_id=111,
                status=trace_api.StatusCode.OK,
                start_ns=1_700_000_000_500_000_000,
                end_ns=1_700_000_000_800_000_000,
                trace_num=222,
            ),
        ],
    )

    files_before = _count_data_files(backend)

    results = _compact_results(iceberg_root)

    # Compaction preserves the current live snapshot while reducing file count.
    assert results["trace_index"].rows_after == 2
    assert results["span_index"].rows_after == 2
    assert results["assessment_index"].rows_after == 2
    assert results["trace_index"].rows_before == results["trace_index"].rows_after

    files_after = _count_data_files(backend)
    assert files_after < files_before
    # Every table should expire down to a single live snapshot after compaction.
    for result in results.values():
        assert result.snapshots_after <= 1
        assert result.data_files_after <= result.data_files_before

    # Reads still return the latest state after compaction.
    trace_info = store.get_trace_info("tr-compact-1")
    assert trace_info.tags[TraceTagKey.TRACE_NAME] == "workflow"
    assert trace_info.tags["stage"] == "final"

    trace = store.get_trace("tr-compact-2")
    assert [span.name for span in trace.data.spans] == ["root_span", "child_span"]

    found_trace_ids = {
        info.trace_id
        for info in store.search_traces(experiment_ids=[experiment_id], max_results=100)[0]
    }
    assert {"tr-compact-1", "tr-compact-2"} <= found_trace_ids


def test_compact_is_idempotent_on_empty_tables(routed_store):
    store, iceberg_root = routed_store
    store.create_experiment("iceberg-compaction-empty")
    backend = store
    # Touch the tables so they are lazily created before the offline catalog loads them.
    _count_data_files(backend)

    results = _compact_results(iceberg_root)

    assert set(results) == {
        "trace_index",
        "trace_tag_index",
        "span_index",
        "assessment_index",
        "trace_metric_daily_rollups",
        "span_cost_daily_rollups",
        "assessment_daily_rollups",
        "session_summary",
    }
    for result in results.values():
        assert result.rows_after == 0


def test_compacted_trace_reads_stay_fresh_after_assessment_update(routed_store):
    store, iceberg_root = routed_store
    experiment_id = store.create_experiment("iceberg-trace-detail-current")
    trace_id = "tr-detail-current"
    source = AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="judge")

    store.start_trace(
        TraceInfo(
            trace_id=trace_id,
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1000,
            execution_duration=10,
            state=TraceState.OK,
        )
    )
    store.log_spans(
        experiment_id,
        [create_test_span(trace_id, "root_span", span_id=111, trace_num=12345)],
    )
    store.create_assessment(
        Feedback(trace_id=trace_id, name="quality", value="good", source=source)
    )

    _compact_results(iceberg_root)

    trace = store.get_trace(trace_id)
    assert [span.name for span in trace.data.spans] == ["root_span"]
    assert [assessment.name for assessment in trace.info.assessments] == ["quality"]

    store.create_assessment(Feedback(trace_id=trace_id, name="fresh", value="yes", source=source))

    trace = store.get_trace(trace_id)
    assert {assessment.name for assessment in trace.info.assessments} == {"quality", "fresh"}


def test_compacted_search_reads_current_snapshot_after_followup_mutation(routed_store):
    store, iceberg_root = routed_store
    experiment_id = store.create_experiment("iceberg-post-compaction-search")

    store.start_trace(
        TraceInfo(
            trace_id="tr-search",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1000,
            execution_duration=5,
            state=TraceState.OK,
            tags={TraceTagKey.TRACE_NAME: "workflow"},
        )
    )
    _compact_results(iceberg_root)
    store.set_trace_tag("tr-search", "stage", "final")

    assert _search_trace_ids(store, experiment_id, filter_string="tag.stage = 'final'") == {
        "tr-search"
    }


def test_compaction_preserves_search_after_followup_tag_update(routed_store):
    store, iceberg_root = routed_store
    experiment_id = store.create_experiment("iceberg-post-compaction-tag-update")

    store.start_trace(
        TraceInfo(
            trace_id="tr-tag-update",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1000,
            execution_duration=5,
            state=TraceState.OK,
            tags={TraceTagKey.TRACE_NAME: "workflow"},
        )
    )
    _compact_results(iceberg_root)
    store.set_trace_tag("tr-tag-update", "stage", "final")

    assert _search_trace_ids(store, experiment_id, filter_string="tag.stage = 'final'") == {
        "tr-tag-update"
    }


def test_post_compaction_reads_cover_search_metrics_sessions_and_batch_get(routed_store):
    store, iceberg_root = routed_store
    experiment_id = store.create_experiment("iceberg-post-compaction-reads")
    source = AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="judge")

    store.start_trace(
        TraceInfo(
            trace_id="tr-session",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1000,
            execution_duration=5,
            state=TraceState.OK,
            tags={TraceTagKey.TRACE_NAME: "workflow"},
            trace_metadata={TraceMetadataKey.TRACE_SESSION: "session-a"},
        )
    )
    store.start_trace(
        TraceInfo(
            trace_id="tr-span",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=2000,
            execution_duration=7,
            state=TraceState.OK,
            tags={TraceTagKey.TRACE_NAME: "workflow"},
        )
    )
    store.create_assessment(Feedback(trace_id="tr-session", name="score", value=1, source=source))
    store.log_spans(
        experiment_id,
        [
            create_test_span(
                trace_id="tr-span",
                name="root_span",
                span_id=111,
                status=trace_api.StatusCode.OK,
                start_ns=2_000_000_000,
                end_ns=2_200_000_000,
                trace_num=333,
            )
        ],
    )

    _compact_results(iceberg_root)

    store.set_trace_tag("tr-session", "stage", "final")
    store.create_assessment(Feedback(trace_id="tr-session", name="score", value=5, source=source))

    assert _search_trace_ids(store, experiment_id, filter_string="tag.stage = 'final'") == {
        "tr-session"
    }

    trace = store.get_trace("tr-span")
    assert [span.name for span in trace.data.spans] == ["root_span"]

    traces = store.batch_get_traces(["tr-session", "tr-span"])
    assert [trace.info.trace_id for trace in traces] == ["tr-session", "tr-span"]
    assert traces[0].info.tags["stage"] == "final"

    infos = store.batch_get_trace_infos(["tr-session", "tr-span"])
    assert [info.trace_id for info in infos] == ["tr-session", "tr-span"]

    assessment_points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.ASSESSMENTS,
        metric_name=AssessmentMetricKey.ASSESSMENT_COUNT,
        aggregations=[MetricAggregation(AggregationType.COUNT)],
        filters=["assessment.name = 'score'"],
    )
    assert len(assessment_points) == 1
    assert assessment_points[0].values["COUNT"] == 2

    completed = store.find_completed_sessions(
        experiment_id=experiment_id,
        min_last_trace_timestamp_ms=0,
        max_last_trace_timestamp_ms=5000,
        filter_string="tag.stage = 'final'",
    )
    assert len(completed) == 1
    assert completed[0].session_id == "session-a"


def test_post_compaction_assessment_override_survives_repeated_compaction(routed_store):
    store, iceberg_root = routed_store
    experiment_id = store.create_experiment("iceberg-post-compaction-override")
    source = AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="judge")
    store.start_trace(
        TraceInfo(
            trace_id="tr-override",
            trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
            request_time=1000,
            execution_duration=5,
            state=TraceState.OK,
        )
    )
    original = store.create_assessment(
        Feedback(trace_id="tr-override", name="correctness", value="no", source=source)
    )

    _compact_results(iceberg_root)

    override = store.create_assessment(
        Feedback(
            trace_id="tr-override",
            name="correctness",
            value="yes",
            source=source,
            overrides=original.assessment_id,
        )
    )

    trace = store.get_trace("tr-override")
    assessments = {assessment.assessment_id: assessment for assessment in trace.info.assessments}
    assert assessments[original.assessment_id].valid is False
    assert assessments[override.assessment_id].valid is True
    assert _search_trace_ids(
        store, experiment_id, filter_string='feedback.correctness = "yes"'
    ) == {"tr-override"}

    points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.ASSESSMENTS,
        metric_name=AssessmentMetricKey.ASSESSMENT_COUNT,
        aggregations=[MetricAggregation(AggregationType.COUNT)],
        filters=["assessment.name = 'correctness'"],
    )
    assert len(points) == 1
    assert points[0].values["COUNT"] == 2

    _compact_results(iceberg_root)

    trace = store.get_trace("tr-override")
    assessments = {assessment.assessment_id: assessment for assessment in trace.info.assessments}
    assert assessments[original.assessment_id].valid is False
    assert assessments[override.assessment_id].valid is True
    assert _search_trace_ids(
        store, experiment_id, filter_string='feedback.correctness = "yes"'
    ) == {"tr-override"}


def test_post_compaction_delete_preserves_results(routed_store):
    store, iceberg_root = routed_store
    experiment_id = store.create_experiment("iceberg-post-compaction-delete")

    for trace_id in ["tr-delete", "tr-keep"]:
        store.start_trace(
            TraceInfo(
                trace_id=trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=1000,
                execution_duration=5,
                state=TraceState.OK,
            )
        )

    _compact_results(iceberg_root)

    assert store.delete_traces(experiment_id=experiment_id, trace_ids=["tr-delete"]) == 1
    assert _search_trace_ids(store, experiment_id) == {"tr-keep"}
    with pytest.raises(MlflowException, match="Trace with ID tr-delete"):
        store.get_trace_info("tr-delete")

    trace_points = store.query_trace_metrics(
        experiment_ids=[experiment_id],
        view_type=MetricViewType.TRACES,
        metric_name=TraceMetricKey.TRACE_COUNT,
        aggregations=[MetricAggregation(AggregationType.COUNT)],
    )
    assert len(trace_points) == 1
    assert trace_points[0].values["COUNT"] == 1


def test_workspace_scoped_post_compaction_mutations_remain_isolated(monkeypatch, routed_store):
    store, iceberg_root = routed_store
    monkeypatch.setenv(MLFLOW_ENABLE_WORKSPACES.name, "true")
    experiment_id = store.create_experiment("iceberg-post-compaction-workspaces")

    with WorkspaceContext("team-a"):
        store.start_trace(
            TraceInfo(
                trace_id="tr-team-a",
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=1000,
                execution_duration=5,
                state=TraceState.OK,
                tags={TraceTagKey.TRACE_NAME: "workflow"},
            )
        )
    with WorkspaceContext("team-b"):
        store.start_trace(
            TraceInfo(
                trace_id="tr-team-b",
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=2000,
                execution_duration=5,
                state=TraceState.OK,
                tags={TraceTagKey.TRACE_NAME: "workflow"},
            )
        )

    _compact_results(iceberg_root)

    with WorkspaceContext("team-a"):
        store.set_trace_tag("tr-team-a", "stage", "final")
        assert _search_trace_ids(store, experiment_id, filter_string="tag.stage = 'final'") == {
            "tr-team-a"
        }
    with WorkspaceContext("team-b"):
        assert _search_trace_ids(store, experiment_id, filter_string="tag.stage = 'final'") == set()

    with WorkspaceContext("team-a"):
        assert store.delete_traces(experiment_id=experiment_id, trace_ids=["tr-team-a"]) == 1
        assert _search_trace_ids(store, experiment_id) == set()
    with WorkspaceContext("team-b"):
        assert _search_trace_ids(store, experiment_id) == {"tr-team-b"}


def test_compaction_preserves_same_trace_id_across_workspaces(monkeypatch, routed_store):
    store, iceberg_root = routed_store
    monkeypatch.setenv(MLFLOW_ENABLE_WORKSPACES.name, "true")
    experiment_id = store.create_experiment("iceberg-shared-trace-id-workspaces")
    shared_trace_id = "shared-trace"

    with WorkspaceContext("team-a"):
        store.start_trace(
            TraceInfo(
                trace_id=shared_trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=1000,
                execution_duration=5,
                state=TraceState.OK,
                tags={TraceTagKey.TRACE_NAME: "workflow-a"},
            )
        )
    with WorkspaceContext("team-b"):
        store.start_trace(
            TraceInfo(
                trace_id=shared_trace_id,
                trace_location=trace_location.TraceLocation.from_experiment_id(experiment_id),
                request_time=2000,
                execution_duration=7,
                state=TraceState.ERROR,
                tags={TraceTagKey.TRACE_NAME: "workflow-b"},
            )
        )

    _compact_results(iceberg_root)

    with WorkspaceContext("team-a"):
        trace_info = store.get_trace_info(shared_trace_id)
        assert trace_info.request_time == 1000
        assert trace_info.state == TraceState.OK
        assert trace_info.tags[TraceTagKey.TRACE_NAME] == "workflow-a"

    with WorkspaceContext("team-b"):
        trace_info = store.get_trace_info(shared_trace_id)
        assert trace_info.request_time == 2000
        assert trace_info.state == TraceState.ERROR
        assert trace_info.tags[TraceTagKey.TRACE_NAME] == "workflow-b"
