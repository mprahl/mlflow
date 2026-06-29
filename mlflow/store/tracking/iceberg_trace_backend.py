from __future__ import annotations

import atexit
import concurrent.futures
import hashlib
import json
import logging
import math
import os
import posixpath
import random
import re
import tempfile
import threading
import time
import uuid
import warnings
from collections.abc import Mapping
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar, copy_context
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from functools import wraps
from io import BytesIO
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import quote, unquote, urlparse

import duckdb
import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.exceptions import CommitFailedException, NamespaceAlreadyExistsError
from pyiceberg.expressions import AlwaysTrue, And, EqualTo, In, Or
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table.sorting import NullOrder, SortDirection, SortField, SortOrder
from pyiceberg.transforms import IdentityTransform
from pyiceberg.types import BooleanType, DateType, DoubleType, LongType, NestedField, StringType
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from mlflow.entities import Assessment, Expectation, Feedback, trace_location
from mlflow.entities.span import Span
from mlflow.entities.trace import Trace
from mlflow.entities.trace_data import TraceData
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
    MLFLOW_ICEBERG_TRACE_ASSESSMENT_DISTRIBUTION_MAX_TRACES,
    MLFLOW_ICEBERG_TRACE_DUCKDB_POOL_SIZE,
    MLFLOW_ICEBERG_TRACE_DUCKDB_THREADS,
    MLFLOW_ICEBERG_TRACE_QUERY_PROFILE,
    MLFLOW_ICEBERG_WAREHOUSE_URI,
)
from mlflow.exceptions import MlflowException, MlflowTraceArchivalMalformedTrace
from mlflow.genai.scorers.online.entities import CompletedSession
from mlflow.protos.databricks_pb2 import (
    INTERNAL_ERROR,
    INVALID_PARAMETER_VALUE,
    INVALID_STATE,
    RESOURCE_DOES_NOT_EXIST,
    RESOURCE_EXHAUSTED,
    ErrorCode,
)
from mlflow.server.constants import BACKEND_STORE_URI_ENV_VAR
from mlflow.store.analytics.trace_correlation import calculate_npmi_from_counts
from mlflow.store.artifact.artifact_repository_registry import get_artifact_repository
from mlflow.store.entities.paged_list import PagedList
from mlflow.store.tracking import (
    MAX_RESULTS_QUERY_TRACE_METRICS,
    SEARCH_MAX_RESULTS_THRESHOLD,
    SEARCH_TRACES_DEFAULT_MAX_RESULTS,
)
from mlflow.store.tracking.dbmodels.models import (
    SqlArchivedTraceLocator,
    SqlAssessmentDailyRollup,
    SqlIcebergTracePublicationState,
    SqlSpan,
    SqlSpanCostDailyRollup,
    SqlTraceInfo,
    SqlTraceMetricDailyRollup,
)
from mlflow.store.tracking.sqlalchemy_store import SqlAlchemyStore, _bulk_upsert
from mlflow.store.tracking.sqlalchemy_workspace_store import WorkspaceAwareSqlAlchemyStore
from mlflow.store.tracking.utils.sql_trace_metrics_utils import (
    MetricDataSample,
    validate_query_trace_metrics_params,
)
from mlflow.store.tracking.utils.trace_analytics import get_assessment_analytics_fields
from mlflow.store.tracking.utils.trace_archival import (
    _parse_trace_archival_duration_millis,
    _TraceArchiveCandidate,
    _TraceDeleteSelection,
)
from mlflow.tracing.analysis import TraceFilterCorrelationResult
from mlflow.tracing.constant import (
    AssessmentMetricDimensionKey,
    AssessmentMetricKey,
    CostKey,
    SpanAttributeKey,
    SpanMetricDimensionKey,
    SpanMetricKey,
    SpansLocation,
    TokenUsageKey,
    TraceArchivalFailureReason,
    TraceMetadataKey,
    TraceMetricDimensionKey,
    TraceMetricKey,
    TraceSizeStatsKey,
    TraceTagKey,
)
from mlflow.tracing.otel.otel_archival import TRACE_ARCHIVAL_FILENAME, spans_to_traces_data_pb
from mlflow.tracing.otel.translation import translate_loaded_span
from mlflow.tracing.trace_archival_config import get_trace_archival_server_config
from mlflow.tracing.utils import generate_assessment_id  # noqa: F401
from mlflow.tracing.utils.artifact_utils import get_archive_uri_for_trace
from mlflow.utils.file_utils import ExclusiveFileLock
from mlflow.utils.search_utils import (
    SearchTraceMetricsUtils,
    SearchTraceUtils,
    SearchUtils,
    _convert_like_pattern_to_regex,
)
from mlflow.utils.time import get_current_time_millis
from mlflow.utils.uri import append_to_uri_path
from mlflow.utils.validation import (
    _validate_trace_archival_location,
    _validate_trace_archival_retention_string,
)
from mlflow.utils.workspace_context import get_request_workspace
from mlflow.utils.workspace_utils import DEFAULT_WORKSPACE_NAME

_logger = logging.getLogger(__name__)

_Row = dict[str, Any]
_Filter = dict[str, Any]
_QueryParams = list[Any]

_ICEBERG_NAMESPACE = ("default",)
_ICEBERG_TRACE_WRITE_LOCK_ID = int.from_bytes(
    hashlib.sha256(b"mlflow-iceberg-trace-write").digest()[:8],
    byteorder="big",
    signed=True,
)
_TRACE_INDEX_TABLE = "trace_index"
_TRACE_TAG_INDEX_TABLE = "trace_tag_index"
_SPAN_INDEX_TABLE = "span_index"
_ASSESSMENT_INDEX_TABLE = "assessment_index"
_TRACE_METRIC_DAILY_ROLLUPS_TABLE = "trace_metric_daily_rollups"
_SPAN_COST_DAILY_ROLLUPS_TABLE = "span_cost_daily_rollups"
_ASSESSMENT_DAILY_ROLLUPS_TABLE = "assessment_daily_rollups"
_SESSION_SUMMARY_TABLE = "session_summary"
_ROLLUP_COVERAGE_METRIC = "__mlflow_rollup_coverage__"
_ROLLUP_GROUP_GLOBAL = "global"
_ROLLUP_GROUP_STATUS = "status"
_ROLLUP_GROUP_MODEL = "model"
_ROLLUP_GROUP_PROVIDER = "provider"
_ROLLUP_GROUP_MODEL_PROVIDER = "model_provider"
_ICEBERG_APPEND_MAX_RETRIES = 8
_ICEBERG_APPEND_INITIAL_BACKOFF_SECONDS = 0.05
_ICEBERG_APPEND_MAX_BACKOFF_SECONDS = 1.0
_MAX_QUERY_TRACE_METRIC_BUCKETS = 1000
_MAX_CORRELATION_FALLBACK_TRACE_IDS = SEARCH_MAX_RESULTS_THRESHOLD
_MAX_HYBRID_TRACE_INFO_COLLECTION = SEARCH_MAX_RESULTS_THRESHOLD
_HOT_STORE_COVERAGE_MIN_RANGE_MS = 24 * 60 * 60 * 1000
_LOCAL_WAREHOUSE_DIRNAME = "warehouse"
_HYBRID_RESERVED_TRACE_TAG_KEYS = frozenset({
    TraceTagKey.SPANS_LOCATION,
    TraceTagKey.ARCHIVE_LOCATION,
    TraceTagKey.ARCHIVAL_FAILURE,
})


def _schema(*fields: tuple[int, str, Any]) -> Schema:
    return Schema(
        *(NestedField(field_id, name, field_type) for field_id, name, field_type in fields)
    )


_TRACE_INDEX_SCHEMA = _schema(
    (1, "trace_id", StringType()),
    (2, "experiment_id", StringType()),
    (3, "request_time_ms", LongType()),
    (4, "request_day", DateType()),
    (5, "execution_duration_ms", LongType()),
    (6, "state", StringType()),
    (7, "trace_name", StringType()),
    (8, "client_request_id", StringType()),
    (9, "request_preview", StringType()),
    (10, "response_preview", StringType()),
    (11, "session_id", StringType()),
    (12, "source_run_id", StringType()),
    (13, "trace_user", StringType()),
    (14, "input_tokens", DoubleType()),
    (15, "output_tokens", DoubleType()),
    (16, "total_tokens", DoubleType()),
    (17, "cache_read_input_tokens", DoubleType()),
    (18, "cache_creation_input_tokens", DoubleType()),
    (19, "tags_json", StringType()),
    (20, "metadata_json", StringType()),
    (21, "workspace", StringType()),
    (22, "input_cost", DoubleType()),
    (23, "output_cost", DoubleType()),
    (24, "total_cost", DoubleType()),
)

_TRACE_TAG_INDEX_SCHEMA = _schema(
    (1, "trace_id", StringType()),
    (2, "experiment_id", StringType()),
    (3, "request_day", DateType()),
    (4, "tag_key", StringType()),
    (5, "tag_value", StringType()),
    (6, "workspace", StringType()),
)

_SPAN_INDEX_SCHEMA = _schema(
    (1, "trace_id", StringType()),
    (2, "experiment_id", StringType()),
    (3, "span_id", StringType()),
    (4, "parent_span_id", StringType()),
    (5, "name", StringType()),
    (6, "span_type", StringType()),
    (7, "start_time_ns", LongType()),
    (8, "span_start_day", DateType()),
    (9, "end_time_ns", LongType()),
    (10, "status", StringType()),
    (11, "attributes_json", StringType()),
    (12, "span_json", StringType()),
    (13, "workspace", StringType()),
    (14, "model_name", StringType()),
    (15, "model_provider", StringType()),
    (16, "input_cost", DoubleType()),
    (17, "output_cost", DoubleType()),
    (18, "total_cost", DoubleType()),
    (19, "latency_ms", DoubleType()),
)

_ASSESSMENT_INDEX_SCHEMA = _schema(
    (1, "assessment_id", StringType()),
    (2, "trace_id", StringType()),
    (3, "experiment_id", StringType()),
    (4, "assessment_name", StringType()),
    (5, "assessment_type", StringType()),
    (6, "assessment_value_json", StringType()),
    (7, "assessment_value_text", StringType()),
    (8, "aggregate_value", DoubleType()),
    (9, "create_time_ms", LongType()),
    (10, "assessment_create_day", DateType()),
    (11, "last_update_time_ms", LongType()),
    (12, "rationale", StringType()),
    (13, "run_id", StringType()),
    (14, "span_id", StringType()),
    (15, "source_type", StringType()),
    (16, "source_id", StringType()),
    (17, "metadata_json", StringType()),
    (18, "overrides", StringType()),
    (19, "valid", BooleanType()),
    (20, "assessment_json", StringType()),
    (21, "workspace", StringType()),
    (22, "trace_request_time_ms", LongType()),
    (23, "trace_request_day", DateType()),
)


_TRACE_METRIC_DAILY_ROLLUPS_SCHEMA = _schema(
    (1, "workspace", StringType()),
    (2, "experiment_id", StringType()),
    (3, "rollup_day", DateType()),
    (4, "metric_name", StringType()),
    (5, "grouping_set", StringType()),
    (6, "trace_status", StringType()),
    (7, "sample_count", LongType()),
    (8, "sum_value", DoubleType()),
    (9, "min_value", DoubleType()),
    (10, "max_value", DoubleType()),
    (11, "p50_value", DoubleType()),
    (12, "p90_value", DoubleType()),
    (13, "p99_value", DoubleType()),
)

_SPAN_COST_DAILY_ROLLUPS_SCHEMA = _schema(
    (1, "workspace", StringType()),
    (2, "experiment_id", StringType()),
    (3, "rollup_day", DateType()),
    (4, "metric_name", StringType()),
    (5, "grouping_set", StringType()),
    (6, "model_name", StringType()),
    (7, "model_provider", StringType()),
    (8, "sample_count", LongType()),
    (9, "sum_value", DoubleType()),
    (10, "min_value", DoubleType()),
    (11, "max_value", DoubleType()),
)

_ASSESSMENT_DAILY_ROLLUPS_SCHEMA = _schema(
    (1, "workspace", StringType()),
    (2, "experiment_id", StringType()),
    (3, "rollup_day", DateType()),
    (4, "metric_name", StringType()),
    (5, "grouping_set", StringType()),
    (6, "sample_count", LongType()),
    (7, "sum_value", DoubleType()),
    (8, "min_value", DoubleType()),
    (9, "max_value", DoubleType()),
)

_SESSION_SUMMARY_SCHEMA = _schema(
    (1, "workspace", StringType()),
    (2, "experiment_id", StringType()),
    (3, "session_id", StringType()),
    (4, "first_trace_timestamp_ms", LongType()),
    (5, "last_trace_timestamp_ms", LongType()),
)


def _experiment_day_partition_spec(schema: Schema, day_column: str) -> PartitionSpec:
    experiment_field = schema.find_field("experiment_id")
    day_field = schema.find_field(day_column)
    return PartitionSpec(
        PartitionField(
            source_id=experiment_field.field_id,
            field_id=1000,
            transform=IdentityTransform(),
            name="experiment_id",
        ),
        PartitionField(
            source_id=day_field.field_id,
            field_id=1001,
            transform=IdentityTransform(),
            name=day_column,
        ),
    )


def _workspace_day_partition_spec(schema: Schema, day_column: str) -> PartitionSpec:
    workspace_field = schema.find_field("workspace")
    day_field = schema.find_field(day_column)
    return PartitionSpec(
        PartitionField(
            source_id=workspace_field.field_id,
            field_id=1000,
            transform=IdentityTransform(),
            name="workspace",
        ),
        PartitionField(
            source_id=day_field.field_id,
            field_id=1001,
            transform=IdentityTransform(),
            name=day_column,
        ),
    )


def _experiment_partition_spec(schema: Schema) -> PartitionSpec:
    experiment_field = schema.find_field("experiment_id")
    return PartitionSpec(
        PartitionField(
            source_id=experiment_field.field_id,
            field_id=1000,
            transform=IdentityTransform(),
            name="experiment_id",
        )
    )


def _sort_order(schema: Schema, *fields: tuple[str, SortDirection]) -> SortOrder:
    return SortOrder(
        *(
            SortField(
                source_id=schema.find_field(name).field_id,
                transform=IdentityTransform(),
                direction=direction,
                null_order=NullOrder.NULLS_LAST,
            )
            for name, direction in fields
        )
    )


_TABLE_SCHEMAS = {
    _TRACE_INDEX_TABLE: _TRACE_INDEX_SCHEMA,
    _TRACE_TAG_INDEX_TABLE: _TRACE_TAG_INDEX_SCHEMA,
    _SPAN_INDEX_TABLE: _SPAN_INDEX_SCHEMA,
    _ASSESSMENT_INDEX_TABLE: _ASSESSMENT_INDEX_SCHEMA,
    _TRACE_METRIC_DAILY_ROLLUPS_TABLE: _TRACE_METRIC_DAILY_ROLLUPS_SCHEMA,
    _SPAN_COST_DAILY_ROLLUPS_TABLE: _SPAN_COST_DAILY_ROLLUPS_SCHEMA,
    _ASSESSMENT_DAILY_ROLLUPS_TABLE: _ASSESSMENT_DAILY_ROLLUPS_SCHEMA,
    _SESSION_SUMMARY_TABLE: _SESSION_SUMMARY_SCHEMA,
}

_TABLE_PARTITION_SPECS = {
    _TRACE_INDEX_TABLE: _experiment_day_partition_spec(_TRACE_INDEX_SCHEMA, "request_day"),
    _TRACE_TAG_INDEX_TABLE: _experiment_day_partition_spec(_TRACE_TAG_INDEX_SCHEMA, "request_day"),
    _SPAN_INDEX_TABLE: _experiment_day_partition_spec(_SPAN_INDEX_SCHEMA, "span_start_day"),
    _ASSESSMENT_INDEX_TABLE: _experiment_day_partition_spec(
        _ASSESSMENT_INDEX_SCHEMA, "trace_request_day"
    ),
    _TRACE_METRIC_DAILY_ROLLUPS_TABLE: _workspace_day_partition_spec(
        _TRACE_METRIC_DAILY_ROLLUPS_SCHEMA, "rollup_day"
    ),
    _SPAN_COST_DAILY_ROLLUPS_TABLE: _workspace_day_partition_spec(
        _SPAN_COST_DAILY_ROLLUPS_SCHEMA, "rollup_day"
    ),
    _ASSESSMENT_DAILY_ROLLUPS_TABLE: _workspace_day_partition_spec(
        _ASSESSMENT_DAILY_ROLLUPS_SCHEMA, "rollup_day"
    ),
    _SESSION_SUMMARY_TABLE: _experiment_partition_spec(_SESSION_SUMMARY_SCHEMA),
}

_TABLE_SORT_ORDERS = {
    _TRACE_INDEX_TABLE: _sort_order(
        _TRACE_INDEX_SCHEMA,
        ("experiment_id", SortDirection.ASC),
        ("request_time_ms", SortDirection.DESC),
        ("trace_id", SortDirection.ASC),
    ),
    _TRACE_TAG_INDEX_TABLE: _sort_order(
        _TRACE_TAG_INDEX_SCHEMA,
        ("experiment_id", SortDirection.ASC),
        ("tag_key", SortDirection.ASC),
        ("tag_value", SortDirection.ASC),
        ("trace_id", SortDirection.ASC),
    ),
    _SPAN_INDEX_TABLE: _sort_order(
        _SPAN_INDEX_SCHEMA,
        ("experiment_id", SortDirection.ASC),
        ("trace_id", SortDirection.ASC),
        ("start_time_ns", SortDirection.ASC),
        ("span_id", SortDirection.ASC),
    ),
    _ASSESSMENT_INDEX_TABLE: _sort_order(
        _ASSESSMENT_INDEX_SCHEMA,
        ("experiment_id", SortDirection.ASC),
        ("trace_id", SortDirection.ASC),
        ("assessment_name", SortDirection.ASC),
        ("assessment_id", SortDirection.ASC),
    ),
    _TRACE_METRIC_DAILY_ROLLUPS_TABLE: _sort_order(
        _TRACE_METRIC_DAILY_ROLLUPS_SCHEMA,
        ("experiment_id", SortDirection.ASC),
        ("metric_name", SortDirection.ASC),
        ("grouping_set", SortDirection.ASC),
        ("trace_status", SortDirection.ASC),
    ),
    _SPAN_COST_DAILY_ROLLUPS_TABLE: _sort_order(
        _SPAN_COST_DAILY_ROLLUPS_SCHEMA,
        ("experiment_id", SortDirection.ASC),
        ("metric_name", SortDirection.ASC),
        ("grouping_set", SortDirection.ASC),
        ("model_provider", SortDirection.ASC),
        ("model_name", SortDirection.ASC),
    ),
    _ASSESSMENT_DAILY_ROLLUPS_TABLE: _sort_order(
        _ASSESSMENT_DAILY_ROLLUPS_SCHEMA,
        ("experiment_id", SortDirection.ASC),
        ("metric_name", SortDirection.ASC),
        ("grouping_set", SortDirection.ASC),
    ),
    _SESSION_SUMMARY_TABLE: _sort_order(
        _SESSION_SUMMARY_SCHEMA,
        ("experiment_id", SortDirection.ASC),
        ("last_trace_timestamp_ms", SortDirection.ASC),
        ("session_id", SortDirection.ASC),
    ),
}

_CANONICAL_TABLES = (
    _TRACE_INDEX_TABLE,
    _TRACE_TAG_INDEX_TABLE,
    _SPAN_INDEX_TABLE,
    _ASSESSMENT_INDEX_TABLE,
    _TRACE_METRIC_DAILY_ROLLUPS_TABLE,
    _SPAN_COST_DAILY_ROLLUPS_TABLE,
    _ASSESSMENT_DAILY_ROLLUPS_TABLE,
    _SESSION_SUMMARY_TABLE,
)

_TABLE_REQUIRED_COLUMNS = {
    _TRACE_INDEX_TABLE: {"trace_id", "experiment_id", "workspace"},
    _TRACE_TAG_INDEX_TABLE: {"trace_id", "experiment_id", "tag_key", "workspace"},
    _SPAN_INDEX_TABLE: {"trace_id", "experiment_id", "span_id", "workspace"},
    _ASSESSMENT_INDEX_TABLE: {"assessment_id", "trace_id", "experiment_id", "workspace"},
    _TRACE_METRIC_DAILY_ROLLUPS_TABLE: {
        "workspace",
        "experiment_id",
        "rollup_day",
        "metric_name",
        "grouping_set",
    },
    _SPAN_COST_DAILY_ROLLUPS_TABLE: {
        "workspace",
        "experiment_id",
        "rollup_day",
        "metric_name",
        "grouping_set",
    },
    _ASSESSMENT_DAILY_ROLLUPS_TABLE: {
        "workspace",
        "experiment_id",
        "rollup_day",
        "metric_name",
        "grouping_set",
    },
    _SESSION_SUMMARY_TABLE: {"workspace", "experiment_id", "session_id"},
}

_EPOCH_DATE = date(1970, 1, 1)
_TRACE_SEARCH_RECENT_PARTITION_BATCH_DAYS = 2
_SPAN_SEARCH_TRACE_START_TOLERANCE_MS = 10_000
_ICEBERG_TABLE_PROPERTIES = {"write.parquet.compression-codec": "zstd"}

_RESOURCE_CACHE_LOCK = threading.Lock()
_RESOURCE_CACHE: dict[str, "_IcebergProcessResources"] = {}


def _normalize_local_warehouse_path(raw_path: str) -> Path:
    path = Path(unquote(raw_path)).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    if path.name != _LOCAL_WAREHOUSE_DIRNAME:
        path = path / _LOCAL_WAREHOUSE_DIRNAME
    return path


@dataclass(frozen=True)
class _ResolvedWarehouseLocation:
    warehouse_root: Path | None
    warehouse_uri: str


def _default_warehouse_root_from_tracking_db(tracking_db_uri: str | None) -> Path | None:
    if tracking_db_uri is None:
        return None
    parsed = urlparse(tracking_db_uri)
    if parsed.scheme != "sqlite" or not parsed.path:
        return None
    tracking_db_path = Path(unquote(parsed.path)).expanduser().resolve()
    return tracking_db_path.parent / "iceberg"


def _resolve_warehouse_location(
    *, tracking_db_uri: str | None = None, warehouse_location: str | None = None
) -> _ResolvedWarehouseLocation:
    configured_warehouse = warehouse_location or MLFLOW_ICEBERG_WAREHOUSE_URI.get()
    if configured_warehouse:
        parsed = urlparse(configured_warehouse)
        if parsed.scheme in {"", "file"}:
            raw_path = parsed.path if parsed.scheme == "file" else configured_warehouse
            warehouse_path = _normalize_local_warehouse_path(raw_path)
            warehouse_uri = warehouse_path.as_uri()
            warehouse_path.mkdir(parents=True, exist_ok=True)
            return _ResolvedWarehouseLocation(
                warehouse_root=None,
                warehouse_uri=warehouse_uri,
            )
        if parsed.scheme == "s3":
            return _ResolvedWarehouseLocation(
                warehouse_root=None,
                warehouse_uri=configured_warehouse,
            )
        raise MlflowException(
            "MLFLOW_ICEBERG_WAREHOUSE_URI must be a local filesystem path, file:// URI, or "
            "s3:// URI.",
            error_code=INVALID_PARAMETER_VALUE,
        )

    if warehouse_root := _default_warehouse_root_from_tracking_db(tracking_db_uri):
        warehouse_root.mkdir(parents=True, exist_ok=True)
        data_dir = warehouse_root / _LOCAL_WAREHOUSE_DIRNAME
        data_dir.mkdir(exist_ok=True)
        return _ResolvedWarehouseLocation(
            warehouse_root=warehouse_root,
            warehouse_uri=data_dir.as_uri(),
        )

    raise MlflowException.invalid_parameter_value(
        "MLFLOW_ICEBERG_WAREHOUSE_URI must be set when MLFLOW_USE_ICEBERG_ARCHIVAL is enabled "
        "with a non-SQLite tracking URI."
    )


@dataclass
class _IcebergProcessResources:
    owner_pid: int
    warehouse_root: Path | None
    warehouse_uri: str
    catalog: Any
    duckdb_pool: _DuckDBConnectionPool
    write_lock: threading.Lock
    rollup_coverage_cache: dict[tuple[Any, ...], bool] = field(default_factory=dict)
    rollup_coverage_cache_lock: threading.Lock = field(default_factory=threading.Lock)

    def close(self) -> None:
        if self.owner_pid == os.getpid():
            self.duckdb_pool.close()


@dataclass(frozen=True)
class _DuckDBIcebergScan:
    table_name: str
    metadata_location: str
    published_at_ms: int | None = None
    projected_columns: tuple[str, ...] | None = None
    workspace: str | None = None
    experiment_ids: tuple[str, ...] | None = None
    day_column: str | None = None
    time_column: str | None = None
    time_multiplier: int = 1
    start_time_ms: int | None = None
    end_time_ms: int | None = None
    trace_ids: tuple[str, ...] | None = None
    empty: bool = False


@dataclass(frozen=True)
class _CompiledDuckDBQuery:
    sql: str
    params: _QueryParams
    requires_trace_tags: bool = False
    requires_spans: bool = False
    requires_assessments: bool = False


@dataclass(frozen=True)
class _CompiledMetricSampleQuery:
    trace_rows: _DuckDBIcebergScan | None
    trace_tag_rows: _DuckDBIcebergScan | None
    span_rows: _DuckDBIcebergScan | None
    assessment_rows: _DuckDBIcebergScan | None
    sql: str
    params: _QueryParams
    dimension_names: tuple[str, ...]
    has_time_bucket: bool


@dataclass
class _TraceSearchSpec:
    experiment_ids: list[str]
    trace_filters: list[_Filter]
    span_filters: list[_Filter]
    assessment_filters: list[_Filter]
    order_by: list[str]
    sql_limit: int
    workspace: str | None
    include_trace_tags: bool = True
    include_session_id: bool = False


@dataclass
class _StagedArchivedTraceProjection:
    trace_id: str
    artifact_uri: str
    artifact_repo: Any | None
    db_payload_generation: int
    trace_row: _Row
    trace_tag_rows: list[_Row]
    span_rows: list[_Row]
    assessment_rows: list[_Row]
    started_at: float
    stage_timings_ms: dict[str, float]


@dataclass
class _StagedArchiveExperimentChunk:
    experiment_id: str
    candidates: list[_TraceArchiveCandidate]
    processed_count: int
    total_count: int
    started_at: float
    projections: list[_StagedArchivedTraceProjection] = field(default_factory=list)
    load_ms: float | None = None
    stage_ms: float | None = None
    stage_timings_ms: dict[str, float] = field(default_factory=dict)
    append_ms: float | None = None
    append_table_timings_ms: dict[str, float] | None = None
    publish_ms: float | None = None
    unpublished_cleanup_ms: float | None = None
    archived_count: int = 0
    retryable_failure: bool = False
    error: Exception | None = None


class _IcebergProjectionCleanupError(MlflowException):
    pass


@dataclass
class _S3TraceArchivePayloadUploader:
    root_artifact_repo: Any
    s3_client: Any
    extra_args: dict[str, Any]

    @classmethod
    def from_archive_root(cls, archive_root_uri: str) -> _S3TraceArchivePayloadUploader | None:
        if urlparse(archive_root_uri).scheme != "s3":
            return None

        from mlflow.store.artifact.s3_artifact_repo import S3ArtifactRepository

        root_artifact_repo = get_artifact_repository(archive_root_uri)
        if not isinstance(root_artifact_repo, S3ArtifactRepository):
            return None

        extra_args = {"ContentType": "application/octet-stream"}
        extra_args.update(root_artifact_repo._bucket_owner_params)
        environ_extra_args = root_artifact_repo.get_s3_file_upload_extra_args()
        if environ_extra_args is not None:
            extra_args.update(environ_extra_args)
        return cls(
            root_artifact_repo=root_artifact_repo,
            s3_client=root_artifact_repo._get_s3_client(),
            extra_args=extra_args,
        )

    def upload_archived_trace_data_bytes(self, artifact_uri: str, data: bytes) -> None:
        bucket, dest_path = self.root_artifact_repo.parse_s3_compliant_uri(artifact_uri)
        self.s3_client.upload_fileobj(
            Fileobj=BytesIO(data),
            Bucket=bucket,
            Key=posixpath.join(dest_path, TRACE_ARCHIVAL_FILENAME),
            ExtraArgs=dict(self.extra_args),
        )


def _connect_duckdb() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    threads = MLFLOW_ICEBERG_TRACE_DUCKDB_THREADS.get()
    if threads < 1:
        connection.close()
        raise MlflowException.invalid_parameter_value(
            "MLFLOW_ICEBERG_TRACE_DUCKDB_THREADS must be at least 1."
        )
    connection.execute(f"SET threads = {threads}")
    try:
        connection.execute("LOAD httpfs")
    except duckdb.Error:
        try:
            connection.execute("INSTALL httpfs")
            connection.execute("LOAD httpfs")
        except duckdb.Error as exc:
            raise MlflowException(
                "Iceberg trace backend requires DuckDB's httpfs extension.",
                error_code=INVALID_PARAMETER_VALUE,
            ) from exc
    try:
        connection.execute("LOAD iceberg")
    except duckdb.Error:
        try:
            connection.execute("INSTALL iceberg")
            connection.execute("LOAD iceberg")
        except duckdb.Error as exc:
            raise MlflowException(
                "Iceberg trace backend requires DuckDB's iceberg extension.",
                error_code=INVALID_PARAMETER_VALUE,
            ) from exc
    # Let DuckDB parallelize Parquet scans/aggregations across cores. Ordering is enforced
    # explicitly via ORDER BY where it matters, so dropping the insertion-order guarantee lets the
    # scan operators run fully in parallel.
    try:
        connection.execute("SET preserve_insertion_order = false")
    except duckdb.Error:
        pass

    # Iceberg metadata and data files are immutable: a commit publishes new object paths rather
    # than modifying existing objects. Each query is also pinned to one metadata location. This
    # makes it safe for these dedicated connections to reuse remote and Parquet metadata without
    # issuing validation requests for objects they have already read.
    connection.execute("SET enable_http_metadata_cache = true")
    connection.execute("SET parquet_metadata_cache = true")
    connection.execute("SET validate_external_file_cache = 'NO_VALIDATION'")
    return connection


@dataclass(frozen=True)
class _DuckDBConnectionLease:
    connection: duckdb.DuckDBPyConnection
    pool_wait_ms: float


class _DuckDBConnectionPool:
    def __init__(self, size: int):
        if size < 1:
            raise MlflowException.invalid_parameter_value(
                "MLFLOW_ICEBERG_TRACE_DUCKDB_POOL_SIZE must be at least 1."
            )

        connections = []
        try:
            connections.extend(_connect_duckdb() for _ in range(size))
        except Exception:
            for connection in connections:
                connection.close()
            raise

        self._condition = threading.Condition()
        self._available = connections
        self._checked_out = 0
        self._high_priority_waiters = 0
        self._closed = False

    @contextmanager
    def acquire(self, *, high_priority: bool = False):
        start_time = time.perf_counter()
        with self._condition:
            if high_priority:
                self._high_priority_waiters += 1
            try:
                while not self._closed and (
                    not self._available or (not high_priority and self._high_priority_waiters > 0)
                ):
                    self._condition.wait()
            finally:
                if high_priority:
                    self._high_priority_waiters -= 1
            if self._closed:
                raise RuntimeError("DuckDB connection pool is closed")
            connection = self._available.pop()
            self._checked_out += 1

        lease = _DuckDBConnectionLease(
            connection=connection,
            pool_wait_ms=_elapsed_ms(start_time),
        )
        try:
            yield lease
        finally:
            with self._condition:
                self._checked_out -= 1
                if self._closed:
                    connection.close()
                else:
                    self._available.append(connection)
                self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            if self._closed:
                while self._checked_out:
                    self._condition.wait()
                return
            self._closed = True
            idle_connections = self._available
            self._available = []
            self._condition.notify_all()

        for connection in idle_connections:
            connection.close()

        with self._condition:
            while self._checked_out:
                self._condition.wait()


def _iceberg_trace_duckdb_pool_size() -> int:
    pool_size = MLFLOW_ICEBERG_TRACE_DUCKDB_POOL_SIZE.get()
    if pool_size < 1:
        raise MlflowException.invalid_parameter_value(
            "MLFLOW_ICEBERG_TRACE_DUCKDB_POOL_SIZE must be at least 1."
        )
    return pool_size


@dataclass(frozen=True)
class _PublishedIcebergCut:
    metadata_locations: Mapping[str, str | None]
    snapshot_ids: Mapping[str, int | None]
    published_at_ms: int | None


@dataclass(frozen=True)
class _LiveIcebergTableState:
    metadata_location: str
    snapshot_id: int | None


@dataclass(frozen=True)
class _PinnedIcebergCut:
    tracking_store_id: int
    cut: _PublishedIcebergCut | None
    live_table_states: dict[str, _LiveIcebergTableState] = field(default_factory=dict)
    live_table_states_lock: threading.Lock = field(default_factory=threading.Lock)


_NO_PINNED_ICEBERG_CUT = object()
_PINNED_ICEBERG_CUT: ContextVar[_PinnedIcebergCut | object] = ContextVar(
    "mlflow_pinned_iceberg_cut",
    default=_NO_PINNED_ICEBERG_CUT,
)


def _with_published_iceberg_cut(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        pinned_cut = _PINNED_ICEBERG_CUT.get()
        tracking_store_id = id(self.tracking_store)
        if (
            pinned_cut is not _NO_PINNED_ICEBERG_CUT
            and pinned_cut.tracking_store_id == tracking_store_id
        ):
            return method(self, *args, **kwargs)
        token = _PINNED_ICEBERG_CUT.set(
            _PinnedIcebergCut(
                tracking_store_id=tracking_store_id,
                cut=self._load_published_iceberg_cut(),
            )
        )
        try:
            return method(self, *args, **kwargs)
        finally:
            _PINNED_ICEBERG_CUT.reset(token)

    return wrapper


def _submit_with_current_context(executor, function, /, *args, **kwargs):
    context = copy_context()
    return executor.submit(context.run, function, *args, **kwargs)


def _ensure_iceberg_trace_tables(catalog) -> None:
    try:
        catalog.create_namespace(_ICEBERG_NAMESPACE)
    except NamespaceAlreadyExistsError:
        pass
    for table_name, schema in _TABLE_SCHEMAS.items():
        catalog.create_table_if_not_exists(
            (*_ICEBERG_NAMESPACE, table_name),
            schema,
            partition_spec=_TABLE_PARTITION_SPECS[table_name],
            sort_order=_TABLE_SORT_ORDERS[table_name],
            properties=_ICEBERG_TABLE_PROPERTIES,
        )
        table = catalog.load_table((*_ICEBERG_NAMESPACE, table_name))
        existing_fields = {field.name for field in table.schema().fields}
        missing_fields = [field for field in schema.fields if field.name not in existing_fields]
        if missing_fields:
            with table.update_schema() as update:
                for field in missing_fields:
                    update.add_column(field.name, field.field_type)
        if table_name == _ASSESSMENT_INDEX_TABLE:
            partition_columns = {
                table.schema().find_column_name(field.source_id) for field in table.spec().fields
            }
            if "trace_request_day" not in partition_columns:
                try:
                    with table.update_spec() as update:
                        if "assessment_create_day" in partition_columns:
                            update.remove_field("assessment_create_day")
                        update.add_identity("trace_request_day")
                except CommitFailedException:
                    table.refresh()
                    partition_columns = {
                        table.schema().find_column_name(field.source_id)
                        for field in table.spec().fields
                    }
                    if "trace_request_day" not in partition_columns:
                        raise
        if any(
            table.properties.get(key) != value for key, value in _ICEBERG_TABLE_PROPERTIES.items()
        ):
            with table.transaction() as transaction:
                transaction.set_properties(_ICEBERG_TABLE_PROPERTIES)


def _infer_tracking_db_uri_from_warehouse(warehouse_uri: str) -> str | None:
    parsed = urlparse(warehouse_uri)
    if parsed.scheme != "file":
        return None
    warehouse_path = Path(unquote(parsed.path)).resolve()
    candidate_paths = [
        warehouse_path.parent / "tracking.db",
        warehouse_path.parent.parent / "tracking.db",
    ]
    for tracking_db_path in candidate_paths:
        if tracking_db_path.exists():
            return f"sqlite:///{tracking_db_path}"
    return None


def _resolve_tracking_db_uri(
    *, tracking_db_uri: str | None = None, warehouse_location: str | None = None
) -> str:
    if tracking_db_uri:
        return tracking_db_uri
    if resolved := os.environ.get(BACKEND_STORE_URI_ENV_VAR):
        return resolved
    warehouse_uri = _resolve_warehouse_location(
        tracking_db_uri=tracking_db_uri, warehouse_location=warehouse_location
    ).warehouse_uri
    if inferred := _infer_tracking_db_uri_from_warehouse(warehouse_uri):
        # ponytail: infer the benchmark-layout tracking DB for offline compaction tools until
        # those callers pass the tracking DB URI explicitly.
        return inferred
    raise MlflowException.invalid_parameter_value(
        "Iceberg trace backend requires the tracking database URI to resolve its shared catalog."
    )


def _create_sql_catalog(
    *, tracking_db_uri: str | None = None, warehouse_location: str | None = None
) -> SqlCatalog:
    resolved_warehouse_location = _resolve_warehouse_location(
        tracking_db_uri=tracking_db_uri, warehouse_location=warehouse_location
    )
    return SqlCatalog(
        "mlflow",
        uri=_resolve_tracking_db_uri(
            tracking_db_uri=tracking_db_uri, warehouse_location=warehouse_location
        ),
        warehouse=resolved_warehouse_location.warehouse_uri,
        init_catalog_tables="false",
    )


def _create_process_resources(
    *, tracking_db_uri: str | None = None, warehouse_location: str | None = None
) -> _IcebergProcessResources:
    resolved_warehouse_location = _resolve_warehouse_location(
        tracking_db_uri=tracking_db_uri, warehouse_location=warehouse_location
    )
    catalog = _create_sql_catalog(
        tracking_db_uri=tracking_db_uri, warehouse_location=warehouse_location
    )
    _ensure_iceberg_trace_tables(catalog)

    resources = _IcebergProcessResources(
        owner_pid=os.getpid(),
        warehouse_root=resolved_warehouse_location.warehouse_root,
        warehouse_uri=resolved_warehouse_location.warehouse_uri,
        catalog=catalog,
        duckdb_pool=_DuckDBConnectionPool(_iceberg_trace_duckdb_pool_size()),
        write_lock=threading.RLock(),
    )
    atexit.register(resources.close)
    return resources


def _get_process_resources(
    *, tracking_db_uri: str | None = None, warehouse_location: str | None = None
) -> _IcebergProcessResources:
    warehouse_uri = _resolve_warehouse_location(
        tracking_db_uri=tracking_db_uri, warehouse_location=warehouse_location
    ).warehouse_uri
    resolved_tracking_db_uri = _resolve_tracking_db_uri(
        tracking_db_uri=tracking_db_uri, warehouse_location=warehouse_location
    )
    cache_key = f"pid:{os.getpid()}|warehouse:{warehouse_uri}|tracking:{resolved_tracking_db_uri}"
    with _RESOURCE_CACHE_LOCK:
        resources = _RESOURCE_CACHE.get(cache_key)
        if resources is None:
            resources = _create_process_resources(
                tracking_db_uri=resolved_tracking_db_uri,
                warehouse_location=warehouse_location,
            )
            _RESOURCE_CACHE[cache_key] = resources
        return resources


def _local_sql_catalog(
    *, tracking_db_uri: str | None = None, warehouse_location: str | None = None
) -> SqlCatalog:
    """Build a SqlCatalog bound directly to the tracking database and warehouse."""
    return _create_sql_catalog(
        tracking_db_uri=tracking_db_uri, warehouse_location=warehouse_location
    )


@dataclass
class _TableCompactionResult:
    table_name: str
    rows_before: int
    rows_after: int
    data_files_before: int
    data_files_after: int
    snapshots_before: int
    snapshots_after: int


def _table_data_file_count(table) -> int:
    snapshot = table.current_snapshot()
    if snapshot is None:
        return 0
    return sum(1 for _ in table.scan().plan_files())


def _date_to_iceberg_day(value: date | None) -> int | None:
    if value is None:
        return None
    return (value - _EPOCH_DATE).days


def _projected_rows_cte_sql(
    source_name: str,
    table_name: str,
    *,
    alias: str,
    columns: list[str] | tuple[str, ...] | None = None,
    include_required_columns: bool = True,
) -> str:
    projected_columns = _projected_columns(
        table_name, columns, include_required_columns=include_required_columns
    )
    select_columns = ", ".join(_sql_identifier(column) for column in projected_columns)
    return f"{alias} AS (SELECT {select_columns} FROM {source_name})"


def _projected_columns(
    table_name: str,
    columns: list[str] | tuple[str, ...] | None,
    *,
    include_required_columns: bool = True,
) -> list[str]:
    schema_columns = [field.name for field in _TABLE_SCHEMAS[table_name].fields]
    if columns is None:
        return schema_columns
    requested_columns = set(columns)
    if include_required_columns:
        requested_columns.update(_TABLE_REQUIRED_COLUMNS[table_name])
    return [column for column in schema_columns if column in requested_columns]


def _table_partition_columns(table_name: str) -> list[str]:
    schema = _TABLE_SCHEMAS[table_name]
    return [
        schema.find_column_name(partition_field.source_id)
        for partition_field in _TABLE_PARTITION_SPECS[table_name].fields
    ]


def _compacted_partition_sql(table_name: str) -> str:
    schema = _TABLE_SCHEMAS[table_name]
    select_columns = ", ".join(_sql_identifier(field.name) for field in schema.fields)
    # Rollup tables use workspace+day partitions and some historical partitions can be empty in
    # future schema evolutions, so compaction matches partition values null-safely.
    partition_predicates = " AND ".join(
        f"{_sql_identifier(column)} IS NOT DISTINCT FROM ?"
        for column in _table_partition_columns(table_name)
    )
    return f"""
        SELECT {select_columns}
        FROM compacted_rows
        WHERE {partition_predicates}
    """


def _sort_order_sql(table_name: str) -> str:
    schema = _TABLE_SCHEMAS[table_name]
    return ", ".join(
        f"{_sql_identifier(schema.find_column_name(field.source_id))} "
        f"{field.direction.value.upper()} NULLS LAST"
        for field in _TABLE_SORT_ORDERS[table_name].fields
    )


def _compact_table(
    catalog,
    table_name: str,
    *,
    duckdb_connection: duckdb.DuckDBPyConnection,
    expire_snapshots: bool = True,
) -> _TableCompactionResult:
    """Rewrite a single Iceberg trace table into bin-packed files from the current snapshot."""
    schema = _TABLE_SCHEMAS[table_name]
    table = catalog.load_table((*_ICEBERG_NAMESPACE, table_name))
    snapshots_before = len(table.metadata.snapshots)
    data_files_before = _table_data_file_count(table)
    target_schema = _arrow_schema(schema)

    duckdb_connection.execute("DROP TABLE IF EXISTS compacted_rows")
    duckdb_connection.execute(
        "CREATE TEMP TABLE compacted_rows AS SELECT * FROM iceberg_scan(?)",
        [table.metadata_location],
    )
    rows_before = duckdb_connection.execute("SELECT count(*) FROM compacted_rows").fetchone()[0]
    rows_after = rows_before

    partition_columns = _table_partition_columns(table_name)
    partitions = duckdb_connection.execute(
        "SELECT DISTINCT "
        + ", ".join(_sql_identifier(column) for column in partition_columns)
        + " FROM compacted_rows"
    ).fetchall()

    if partitions:
        order_by = _sort_order_sql(table_name)
        with table.transaction() as transaction:
            transaction.delete(AlwaysTrue())
            for partition_values in partitions:
                arrow_table = duckdb_connection.execute(
                    f"{_compacted_partition_sql(table_name)} ORDER BY {order_by}",
                    list(partition_values),
                ).to_arrow_table()
                transaction.append(arrow_table.cast(target_schema))
    elif rows_before > 0:
        with table.transaction() as transaction:
            transaction.delete(AlwaysTrue())

    duckdb_connection.execute("DROP TABLE IF EXISTS compacted_rows")

    if expire_snapshots:
        _expire_table_snapshots(catalog, table_name)

    table = catalog.load_table((*_ICEBERG_NAMESPACE, table_name))
    return _TableCompactionResult(
        table_name=table_name,
        rows_before=rows_before,
        rows_after=rows_after,
        data_files_before=data_files_before,
        data_files_after=_table_data_file_count(table),
        snapshots_before=snapshots_before,
        snapshots_after=len(table.metadata.snapshots),
    )


def _expire_table_snapshots(catalog, table_name: str) -> None:
    """Drop every snapshot except the current one to reclaim metadata and orphaned data files."""
    table = catalog.load_table((*_ICEBERG_NAMESPACE, table_name))
    current_snapshot = table.current_snapshot()
    if current_snapshot is None:
        return
    retained_snapshot_ids = {current_snapshot.snapshot_id}
    expirable_snapshot_ids = [
        snapshot.snapshot_id
        for snapshot in table.metadata.snapshots
        if snapshot.snapshot_id not in retained_snapshot_ids
    ]
    if not expirable_snapshot_ids:
        return
    try:
        table.maintenance.expire_snapshots().by_ids(expirable_snapshot_ids).commit()
    except Exception as exc:  # pragma: no cover - best-effort hygiene, never fail compaction
        _logger.warning("Failed to expire snapshots for table %s: %s", table_name, exc)


def compact_iceberg_trace_tables(
    catalog,
    *,
    expire_snapshots: bool = True,
    write_lock=None,
) -> list[_TableCompactionResult]:
    """Compact all Iceberg trace tables backed by ``catalog``.

    Shared by the standalone maintenance script and the periodic compaction job so the two paths
    stay in lockstep.
    """
    connection = _connect_duckdb()
    try:
        try:
            connection.execute("SET memory_limit = '8GB'")
            connection.execute("SET threads = 2")
            connection.execute(
                "SET temp_directory = ?", [str(Path(os.environ.get("TMPDIR", "/tmp")))]
            )
        except duckdb.Error:
            pass
        with write_lock or nullcontext():
            return [
                _compact_table(
                    catalog,
                    table_name,
                    duckdb_connection=connection,
                    expire_snapshots=expire_snapshots,
                )
                for table_name in _CANONICAL_TABLES
            ]
    finally:
        connection.close()


def run_iceberg_trace_compaction(
    warehouse_location: str | None = None,
    *,
    tracking_db_uri: str | None = None,
    expire_snapshots: bool = True,
) -> list[_TableCompactionResult]:
    """Compact a warehouse using a direct SqlCatalog."""
    catalog = _local_sql_catalog(
        tracking_db_uri=tracking_db_uri,
        warehouse_location=warehouse_location,
    )
    _ensure_iceberg_trace_tables(catalog)
    return compact_iceberg_trace_tables(catalog, expire_snapshots=expire_snapshots)


def _arrow_schema(schema: Schema) -> pa.Schema:
    return schema.as_arrow()


def _timestamp_ms_to_day(timestamp_ms: int | None) -> date | None:
    if timestamp_ms is None:
        return None
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).date()


def _timestamp_ns_to_day(timestamp_ns: int | None) -> date | None:
    if timestamp_ns is None:
        return None
    return datetime.fromtimestamp(timestamp_ns / 1_000_000_000, tz=timezone.utc).date()


def _sql_string_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _validated_limit(value: int, parameter_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MlflowException.invalid_parameter_value(
            f"{parameter_name} must be a non-negative integer"
        )
    return value


def _limit_clause(value: int | None, parameter_name: str) -> str:
    return f"LIMIT {_validated_limit(value, parameter_name)}" if value is not None else ""


def _trace_time_bounds_from_filters(
    trace_filters: list[_Filter],
) -> tuple[int | None, int | None]:
    start_time_ms = None
    end_time_ms = None
    for parsed_filter in trace_filters:
        if parsed_filter["type"] != "attribute" or parsed_filter["key"] != "timestamp_ms":
            continue
        comparator = parsed_filter["comparator"]
        value = parsed_filter.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        timestamp_ms = int(value)
        if comparator in {">", ">="}:
            start_time_ms = (
                max(start_time_ms, timestamp_ms) if start_time_ms is not None else timestamp_ms
            )
        elif comparator in {"<", "<="}:
            end_time_ms = (
                min(end_time_ms, timestamp_ms) if end_time_ms is not None else timestamp_ms
            )
        elif comparator == "=":
            start_time_ms = (
                max(start_time_ms, timestamp_ms) if start_time_ms is not None else timestamp_ms
            )
            end_time_ms = (
                min(end_time_ms, timestamp_ms) if end_time_ms is not None else timestamp_ms
            )
    return start_time_ms, end_time_ms


def _filters_reference_trace_tags(filters: list[str] | None) -> bool:
    for filter_clause in filters or []:
        parsed = SearchTraceMetricsUtils.parse_search_filter(filter_clause)
        if parsed.view_type == "trace" and parsed.entity == "tag":
            return True
    return False


def _filters_reference_traces(filters: list[str] | None) -> bool:
    return any(
        SearchTraceMetricsUtils.parse_search_filter(filter_clause).view_type == "trace"
        for filter_clause in filters or []
    )


def _trace_search_references_arbitrary_tags(
    trace_filters: list[_Filter], order_by: list[str]
) -> bool:
    if any(
        parsed_filter["type"] == "tag" and parsed_filter["key"] != TraceTagKey.TRACE_NAME
        for parsed_filter in trace_filters
    ):
        return True
    for clause in order_by:
        identifier_type, key, _ = SearchTraceUtils.parse_order_by_for_search_traces(clause)
        if identifier_type == "tag" and key != TraceTagKey.TRACE_NAME:
            return True
    return False


def _trace_search_uses_timestamp_desc_order(order_by: list[str]) -> bool:
    if not order_by:
        return True
    if len(order_by) != 1:
        return False
    identifier_type, key, is_ascending = SearchTraceUtils.parse_order_by_for_search_traces(
        order_by[0]
    )
    return identifier_type == "attribute" and key == "timestamp_ms" and not is_ascending


def _can_search_traces_by_recent_partitions(
    *,
    trace_filters: list[_Filter],
    assessment_filters: list[_Filter],
    span_filters: list[_Filter],
    order_by: list[str],
) -> bool:
    if assessment_filters:
        return False
    if any(
        parsed_filter["key"] not in {"content"}
        and not parsed_filter["key"].startswith("attributes.")
        for parsed_filter in span_filters
    ):
        return False
    if _trace_search_references_arbitrary_tags(trace_filters, order_by):
        return False
    if not _trace_search_uses_timestamp_desc_order(order_by):
        return False
    start_time_ms, end_time_ms = _trace_time_bounds_from_filters(trace_filters)
    return start_time_ms is not None and end_time_ms is not None


def _day_bounds_ms(value: date) -> tuple[int, int]:
    start = int(
        datetime(value.year, value.month, value.day, tzinfo=timezone.utc).timestamp() * 1000
    )
    return start, start + 24 * 60 * 60 * 1000 - 1


def _dimension_selects(
    time_bucket_selects: list[str], dimension_columns: list[tuple[str, str]]
) -> list[str]:
    return [
        *time_bucket_selects,
        *(f"{column} AS {_sql_identifier(dimension)}" for dimension, column in dimension_columns),
    ]


def _metric_group_terms(
    time_bucket_group: str | None, dimension_columns: list[tuple[str, str]]
) -> list[str]:
    return ([time_bucket_group] if time_bucket_group else []) + [
        column for _, column in dimension_columns
    ]


def _span_matches_trace_generation_sql(*, span_alias: str, trace_alias: str) -> str:
    archived_trace_guard = (
        f"coalesce({_json_string_expr('tags_json', TraceTagKey.SPANS_LOCATION, trace_alias)}, "
        f"{_sql_string_literal(SpansLocation.TRACKING_STORE.value)}) "
        f"!= {_sql_string_literal(SpansLocation.ARCHIVE_REPO.value)}"
    )
    return (
        f"{span_alias}.trace_id = {trace_alias}.trace_id "
        f"AND ({archived_trace_guard} OR ("
        f"CAST({span_alias}.start_time_ns / 1000000 AS BIGINT) >= {trace_alias}.request_time_ms "
        f"AND CAST({span_alias}.start_time_ns / 1000000 AS BIGINT) "
        f"<= ({trace_alias}.request_time_ms + COALESCE({trace_alias}.execution_duration_ms, 0))"
        f"))"
    )


def _assessment_matches_trace_generation_sql(*, assessment_alias: str, trace_alias: str) -> str:
    archived_trace_guard = (
        f"coalesce({_json_string_expr('tags_json', TraceTagKey.SPANS_LOCATION, trace_alias)}, "
        f"{_sql_string_literal(SpansLocation.TRACKING_STORE.value)}) "
        f"!= {_sql_string_literal(SpansLocation.ARCHIVE_REPO.value)}"
    )
    return (
        f"{assessment_alias}.trace_id = {trace_alias}.trace_id "
        f"AND ({archived_trace_guard} OR "
        f"{assessment_alias}.trace_request_time_ms = {trace_alias}.request_time_ms)"
    )


_TRACE_METADATA_METRIC_COLUMNS = {
    TraceMetadataKey.SOURCE_RUN: "source_run_id",
    TraceMetadataKey.TRACE_SESSION: "session_id",
    TraceMetadataKey.TRACE_USER: "trace_user",
}

_TRACE_DETAIL_TRACE_COLUMNS = [
    "trace_id",
    "experiment_id",
    "request_time_ms",
    "execution_duration_ms",
    "state",
    "client_request_id",
    "request_preview",
    "response_preview",
    "session_id",
    "tags_json",
    "metadata_json",
]


def _metric_trace_filter_columns(filters: list[str] | None) -> set[str]:
    """Trace_index columns referenced by trace-scoped span/assessment metric filters.

    Mirrors the expressions built in ``_compile_span_metric_filter`` /
    ``_compile_assessment_metric_filter`` so the ``latest_traces`` projection can drop every column
    a query never touches (notably the large ``metadata_json``). Tag filters correlate on
    ``trace_id``, which is always projected, so they add nothing here.
    """
    columns: set[str] = set()
    for filter_string in filters or []:
        parsed = SearchTraceMetricsUtils.parse_search_filter(filter_string)
        if parsed.view_type != "trace":
            continue
        if parsed.entity == "status":
            columns.add("state")
        elif parsed.entity == "metadata" and parsed.key:
            columns.add(_TRACE_METADATA_METRIC_COLUMNS.get(parsed.key, "metadata_json"))
    return columns


def _search_trace_filter_columns(parsed_filter: _Filter) -> set[str]:
    """Trace_index columns referenced by a parsed search-traces filter.

    Mirrors ``_compile_trace_filter`` so trace-metric/search projections stay minimal.
    """
    filter_type = parsed_filter["type"]
    key = parsed_filter["key"]
    if filter_type == "attribute":
        return {
            "status": {"state"},
            "timestamp_ms": {"request_time_ms"},
            "execution_time_ms": {"execution_duration_ms"},
            "client_request_id": {"client_request_id"},
            "end_time_ms": {"request_time_ms", "execution_duration_ms"},
        }.get(key, set())
    if filter_type == "tag":
        return {"trace_name"} if key == TraceTagKey.TRACE_NAME else set()
    if filter_type == "request_metadata":
        return {_TRACE_METADATA_METRIC_COLUMNS.get(key, "metadata_json")}
    return set()


def _search_trace_order_by_columns(order_by: list[str]) -> set[str]:
    if not order_by:
        return {"request_time_ms"}

    columns: set[str] = set()
    for clause in order_by:
        identifier_type, key, _ = SearchTraceUtils.parse_order_by_for_search_traces(clause)
        if identifier_type == "attribute":
            if column := {
                "timestamp_ms": "request_time_ms",
                "execution_time_ms": "execution_duration_ms",
                "status": "state",
                "experiment_id": "experiment_id",
            }.get(key):
                columns.add(column)
        elif identifier_type == "tag" and key == TraceTagKey.TRACE_NAME:
            columns.add("trace_name")
        elif identifier_type == "request_metadata":
            columns.add(_TRACE_METADATA_METRIC_COLUMNS.get(key, "metadata_json"))
    return columns


def _trace_search_projection_columns(spec: _TraceSearchSpec) -> set[str]:
    columns = {"trace_id", "experiment_id", "request_time_ms", "workspace"}
    if spec.include_session_id:
        columns.add("session_id")
    for parsed_filter in spec.trace_filters:
        columns.update(_search_trace_filter_columns(parsed_filter))
    columns.update(_search_trace_order_by_columns(spec.order_by))
    return columns


def _span_search_projection_columns(filters: list[_Filter]) -> set[str]:
    columns = {"trace_id"}
    for parsed_filter in filters:
        key = parsed_filter["key"]
        if key.startswith("attributes."):
            columns.add("attributes_json")
        elif column := {
            "name": "name",
            "type": "span_type",
            "status": "status",
            "content": "span_json",
        }.get(key):
            columns.add(column)
    return columns


def _assessment_search_projection_columns(filters: list[_Filter]) -> set[str]:
    columns = {
        "trace_id",
        "assessment_type",
        "assessment_name",
        "valid",
        "metadata_json",
    }
    for parsed_filter in filters:
        comparator = parsed_filter["comparator"]
        if comparator in {">", ">=", "<", "<="}:
            columns.add("aggregate_value")
        elif comparator not in {"IS NULL", "IS NOT NULL"}:
            columns.add("assessment_value_text")
    return columns


_SPAN_METRIC_DIMENSION_COLUMNS = {
    SpanMetricDimensionKey.SPAN_NAME: "name",
    SpanMetricDimensionKey.SPAN_TYPE: "span_type",
    SpanMetricDimensionKey.SPAN_STATUS: "status",
    SpanMetricDimensionKey.SPAN_MODEL_NAME: "model_name",
    SpanMetricDimensionKey.SPAN_MODEL_PROVIDER: "model_provider",
}
_SPAN_METRIC_VALUE_COLUMNS = {
    SpanMetricKey.LATENCY: "latency_ms",
    SpanMetricKey.INPUT_COST: "input_cost",
    SpanMetricKey.OUTPUT_COST: "output_cost",
    SpanMetricKey.TOTAL_COST: "total_cost",
}
_SPAN_METRIC_FILTER_COLUMNS = {
    "name": "name",
    "status": "status",
    "type": "span_type",
    "model": "model_name",
    "model_provider": "model_provider",
}
_ASSESSMENT_METRIC_DIMENSION_COLUMNS = {
    AssessmentMetricDimensionKey.ASSESSMENT_NAME: "assessment_name",
    AssessmentMetricDimensionKey.ASSESSMENT_VALUE: "assessment_value_json",
}
_ASSESSMENT_METRIC_FILTER_COLUMNS = {
    "name": "assessment_name",
    "type": "assessment_type",
    "value": "assessment_value_text",
}

_TRACE_ROLLUP_DIMENSION_COLUMNS = {
    TraceMetricDimensionKey.TRACE_STATUS: "trace_status",
}
_TRACE_ROLLUP_FILTER_DIMENSIONS = {
    ("trace", "status", None): TraceMetricDimensionKey.TRACE_STATUS,
}
_TRACE_ROLLUP_VALUE_COLUMNS = {
    TraceMetricKey.LATENCY: "execution_duration_ms",
    TraceMetricKey.INPUT_TOKENS: "input_tokens",
    TraceMetricKey.OUTPUT_TOKENS: "output_tokens",
    TraceMetricKey.TOTAL_TOKENS: "total_tokens",
    TraceMetricKey.CACHE_READ_INPUT_TOKENS: "cache_read_input_tokens",
    TraceMetricKey.CACHE_CREATION_INPUT_TOKENS: "cache_creation_input_tokens",
}

_SPAN_COST_ROLLUP_DIMENSION_COLUMNS = {
    SpanMetricDimensionKey.SPAN_MODEL_NAME: "model_name",
    SpanMetricDimensionKey.SPAN_MODEL_PROVIDER: "model_provider",
}
_SPAN_COST_ROLLUP_FILTER_DIMENSIONS = {
    ("span", "model", None): SpanMetricDimensionKey.SPAN_MODEL_NAME,
    ("span", "model_provider", None): SpanMetricDimensionKey.SPAN_MODEL_PROVIDER,
}
_SPAN_COST_ROLLUP_VALUE_COLUMNS = {
    SpanMetricKey.INPUT_COST: "input_cost",
    SpanMetricKey.OUTPUT_COST: "output_cost",
    SpanMetricKey.TOTAL_COST: "total_cost",
}

_ROLLUP_STORED_PERCENTILES = {
    50: "p50_value",
    90: "p90_value",
    99: "p99_value",
}


def _span_metric_filter_columns(filters: list[str] | None) -> set[str]:
    """span_index columns referenced by span-scoped span-metric filters."""
    columns: set[str] = set()
    for filter_string in filters or []:
        parsed = SearchTraceMetricsUtils.parse_search_filter(filter_string)
        if parsed.view_type == "span" and (
            column := _SPAN_METRIC_FILTER_COLUMNS.get(parsed.entity)
        ):
            columns.add(column)
    return columns


def _assessment_metric_filter_columns(filters: list[str] | None) -> set[str]:
    """assessment_index columns referenced by assessment-scoped assessment-metric filters."""
    columns: set[str] = set()
    for filter_string in filters or []:
        parsed = SearchTraceMetricsUtils.parse_search_filter(filter_string)
        if parsed.view_type == "assessment" and (
            column := _ASSESSMENT_METRIC_FILTER_COLUMNS.get(parsed.entity)
        ):
            columns.add(column)
    return columns


def _profile_iceberg_trace_queries() -> bool:
    return MLFLOW_ICEBERG_TRACE_QUERY_PROFILE.get()


def _iceberg_archive_project_batch_size() -> int:
    return max(1, MLFLOW_ICEBERG_TRACE_ARCHIVE_PROJECT_BATCH_SIZE.get())


def _iceberg_archive_rollup_refresh_chunks() -> int:
    return max(1, MLFLOW_ICEBERG_TRACE_ARCHIVE_ROLLUP_REFRESH_CHUNKS.get())


def _iceberg_archive_max_workers() -> int:
    return max(1, MLFLOW_ICEBERG_TRACE_ARCHIVE_MAX_WORKERS.get())


def _iceberg_archive_experiment_max_workers() -> int:
    return max(1, MLFLOW_ICEBERG_TRACE_ARCHIVE_EXPERIMENT_MAX_WORKERS.get())


def _elapsed_ms(start_time: float) -> float:
    return round((time.perf_counter() - start_time) * 1000, 2)


def _archive_stage_timings_ms(
    projections: list[_StagedArchivedTraceProjection],
) -> dict[str, float]:
    timing_keys = ("decode_spans", "serialize_pb", "upload_payload", "build_projection")
    return {
        key: round(sum(projection.stage_timings_ms.get(key, 0.0) for projection in projections), 2)
        for key in timing_keys
    }


def _partition_keys_time_bounds(
    partition_keys: set[tuple[str, date]],
) -> tuple[int | None, int | None]:
    if not partition_keys:
        return None, None
    days = [day for _, day in partition_keys]
    start_time_ms, _ = _day_bounds_ms(min(days))
    _, end_time_ms = _day_bounds_ms(max(days))
    return start_time_ms, end_time_ms


def _experiment_day_partition_where_sql(
    experiment_column: str,
    day_column: str,
    partition_keys: set[tuple[str, date]],
) -> tuple[str, _QueryParams]:
    clauses = []
    params: _QueryParams = []
    for experiment_id, day in sorted(partition_keys):
        clauses.append(f"({experiment_column} = ? AND {day_column} = ?)")
        params.extend([experiment_id, day])
    return "(" + " OR ".join(clauses) + ")", params


def _rollup_coverage_rows(
    partition_keys: set[tuple[str, date]],
    *,
    workspace: str,
    dimension_columns: tuple[str, ...],
    percentile_columns: bool = False,
) -> list[_Row]:
    return [
        {
            "workspace": workspace,
            "experiment_id": experiment_id,
            "rollup_day": rollup_day,
            "metric_name": _ROLLUP_COVERAGE_METRIC,
            "grouping_set": _ROLLUP_GROUP_GLOBAL,
            **dict.fromkeys(dimension_columns),
            "sample_count": 0,
            "sum_value": None,
            "min_value": None,
            "max_value": None,
            **(
                {
                    "p50_value": None,
                    "p90_value": None,
                    "p99_value": None,
                }
                if percentile_columns
                else {}
            ),
        }
        for experiment_id, rollup_day in sorted(partition_keys)
    ]


def _scan_profile(scan: _DuckDBIcebergScan) -> dict[str, Any]:
    return {
        "table": scan.table_name,
        "published_at_ms": scan.published_at_ms,
        "projected_columns": scan.projected_columns,
        "workspace": scan.workspace,
        "experiment_id_filter": len(scan.experiment_ids) if scan.experiment_ids else 0,
        "day_column": scan.day_column,
        "time_column": scan.time_column,
        "has_start_time_ms": scan.start_time_ms is not None,
        "has_end_time_ms": scan.end_time_ms is not None,
        "trace_id_filter": len(scan.trace_ids) if scan.trace_ids else 0,
    }


def _scan_where_sql(scan: _DuckDBIcebergScan) -> tuple[str, _QueryParams]:
    where_clauses: list[str] = []
    params: _QueryParams = []
    if scan.workspace is not None:
        where_clauses.append("workspace = ?")
        params.append(scan.workspace)
    if scan.experiment_ids:
        placeholders = ",".join("?" for _ in scan.experiment_ids)
        where_clauses.append(f"experiment_id IN ({placeholders})")
        params.extend(scan.experiment_ids)
    if scan.day_column is not None:
        if start_day := _timestamp_ms_to_day(scan.start_time_ms):
            where_clauses.append(f"{_sql_identifier(scan.day_column)} >= ?")
            params.append(start_day)
        if end_day := _timestamp_ms_to_day(scan.end_time_ms):
            where_clauses.append(f"{_sql_identifier(scan.day_column)} <= ?")
            params.append(end_day)
    if scan.time_column is not None:
        if scan.start_time_ms is not None:
            where_clauses.append(f"{_sql_identifier(scan.time_column)} >= ?")
            params.append(scan.start_time_ms * scan.time_multiplier)
        if scan.end_time_ms is not None:
            where_clauses.append(f"{_sql_identifier(scan.time_column)} <= ?")
            params.append(scan.end_time_ms * scan.time_multiplier)
    # Push trace_id equality/membership into the scan so DuckDB can prune Parquet row groups
    # (span_index/assessment_index are sorted by trace_id) and defer materializing heavy JSON
    # columns until after the filter. The dedup window still sees every version of the matched
    # trace_id, so latest-row semantics are unchanged.
    if scan.trace_ids is not None:
        placeholders = ",".join("?" for _ in scan.trace_ids)
        where_clauses.append(f"trace_id IN ({placeholders})")
        params.extend(scan.trace_ids)
    return (f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""), params


def _iceberg_scan_cte(name: str, scan: _DuckDBIcebergScan) -> tuple[str, _QueryParams]:
    where_sql, where_params = _scan_where_sql(scan)
    if scan.empty:
        where_sql = f"{where_sql}{' AND' if where_sql else ' WHERE'} FALSE"
    projection_sql = (
        ", ".join(_sql_identifier(column) for column in scan.projected_columns)
        if scan.projected_columns
        else "*"
    )
    return (
        f"{name} AS (SELECT {projection_sql} FROM iceberg_scan(?){where_sql})",
        [scan.metadata_location, *where_params],
    )


def _prepend_ctes(sql: str, ctes: list[str]) -> str:
    if not ctes:
        return sql
    stripped_sql = sql.lstrip()
    prefix = sql[: len(sql) - len(stripped_sql)]
    if stripped_sql.upper().startswith("WITH "):
        return f"{prefix}WITH {', '.join(ctes)}, {stripped_sql[5:]}"
    return f"{prefix}WITH {', '.join(ctes)} {stripped_sql}"


def _clear_process_resources() -> None:
    with _RESOURCE_CACHE_LOCK:
        resources = list(_RESOURCE_CACHE.values())
        _RESOURCE_CACHE.clear()
    for resource in resources:
        resource.close()


def _qualify(column: str, table_alias: str | None = None) -> str:
    return f"{table_alias}.{column}" if table_alias else column


def _json_string_expr(column: str, key: str, table_alias: str | None = None) -> str:
    escaped_key = key.replace("\\", "\\\\").replace('"', '\\"')
    path = f'$."{escaped_key}"'
    return f"json_extract_string({_qualify(column, table_alias)}, {_sql_string_literal(path)})"


def _json_nested_string_expr(column: str, keys: list[str], table_alias: str | None = None) -> str:
    path = "$"
    for key in keys:
        escaped_key = key.replace("\\", "\\\\").replace('"', '\\"')
        path += f'."{escaped_key}"'
    return f"json_extract_string({_qualify(column, table_alias)}, {_sql_string_literal(path)})"


def _tag_scalar_expr(
    key: str,
    *,
    trace_alias: str | None = None,
    tag_cte_name: str = "latest_trace_tags",
) -> str:
    trace_id_expr = _qualify("trace_id", trace_alias)
    return (
        "(SELECT tt.tag_value "
        f"FROM {tag_cte_name} AS tt "
        f"WHERE tt.trace_id = {trace_id_expr} AND tt.tag_key = {_sql_string_literal(key)} "
        "LIMIT 1)"
    )


def _compile_trace_filter(
    parsed_filter: _Filter, table_alias: str | None = None
) -> tuple[str, _QueryParams]:
    filter_type = parsed_filter["type"]
    key = parsed_filter["key"]
    comparator = parsed_filter["comparator"]
    value = parsed_filter.get("value")

    if filter_type == "attribute":
        expression = {
            "status": "state",
            "timestamp_ms": "request_time_ms",
            "execution_time_ms": "execution_duration_ms",
            "client_request_id": "client_request_id",
            "end_time_ms": "(request_time_ms + execution_duration_ms)",
        }.get(key)
        expression = _qualify(expression, table_alias) if expression else None
    elif filter_type == "tag":
        expression = (
            _qualify("trace_name", table_alias)
            if key == TraceTagKey.TRACE_NAME
            else _tag_scalar_expr(key, trace_alias=table_alias)
        )
    elif filter_type == "request_metadata":
        expression = {
            TraceMetadataKey.SOURCE_RUN: _qualify("source_run_id", table_alias),
            TraceMetadataKey.TRACE_SESSION: _qualify("session_id", table_alias),
            TraceMetadataKey.TRACE_USER: _qualify("trace_user", table_alias),
        }.get(key, _json_string_expr("metadata_json", key, table_alias))
    else:
        expression = None

    if expression is None:
        raise MlflowException(
            f"Iceberg trace backend does not support filter {parsed_filter!r} yet.",
            error_code=INVALID_PARAMETER_VALUE,
        )

    if comparator in {"IS NULL", "IS NOT NULL"}:
        return f"{expression} {comparator}", []
    if comparator in {"IN", "NOT IN"}:
        placeholders = ",".join("?" for _ in value)
        return f"{expression} {comparator} ({placeholders})", list(value)
    if comparator == "RLIKE":
        return f"regexp_matches(CAST({expression} AS VARCHAR), ?)", [value]
    return f"{expression} {comparator} ?", [value]


class _DuckDBTraceSearchCompiler:
    def __init__(self, spec: _TraceSearchSpec):
        self.spec = spec

    def compile(self) -> _CompiledDuckDBQuery:
        where_clauses = []
        params: _QueryParams = []
        trace_columns = _trace_search_projection_columns(self.spec)
        latest_traces_cte = _projected_rows_cte_sql(
            "trace_rows", _TRACE_INDEX_TABLE, alias="latest_traces", columns=trace_columns
        )
        if self.spec.experiment_ids:
            where_clauses.append(
                f"experiment_id IN ({','.join('?' for _ in self.spec.experiment_ids)})"
            )
            params.extend(self.spec.experiment_ids)
        if self.spec.workspace:
            where_clauses.append("workspace = ?")
            params.append(self.spec.workspace)
        for parsed_filter in self.spec.trace_filters:
            clause, clause_params = _compile_trace_filter(parsed_filter, table_alias="t")
            where_clauses.append(clause)
            params.extend(clause_params)
        start_time_ms, end_time_ms = _trace_time_bounds_from_filters(self.spec.trace_filters)
        if start_time_ms is not None:
            where_clauses.append("request_time_ms >= ?")
            params.append(start_time_ms)
        if end_time_ms is not None:
            where_clauses.append("request_time_ms <= ?")
            params.append(end_time_ms)

        latest_trace_tags_cte = (
            ", latest_trace_tags AS (SELECT * FROM trace_tag_rows)"
            if self.spec.include_trace_tags
            else ""
        )
        latest_spans_cte = ""
        if self.spec.span_filters:
            latest_spans_cte = ", " + _projected_rows_cte_sql(
                "span_rows",
                _SPAN_INDEX_TABLE,
                alias="latest_spans",
                columns=_span_search_projection_columns(self.spec.span_filters),
            )
        latest_assessments_cte = ""
        if self.spec.assessment_filters:
            latest_assessments_cte = ", " + _projected_rows_cte_sql(
                "assessment_rows",
                _ASSESSMENT_INDEX_TABLE,
                alias="latest_assessments",
                columns=_assessment_search_projection_columns(self.spec.assessment_filters),
            )
        if self.spec.span_filters:
            span_exists_clause, span_exists_params = self._compile_span_filter_exists(
                self.spec.span_filters, trace_alias="t"
            )
            where_clauses.append(span_exists_clause)
            params.extend(span_exists_params)
        if self.spec.assessment_filters:
            for parsed_filter in self.spec.assessment_filters:
                clause, clause_params = self._compile_assessment_filter_exists(
                    parsed_filter, trace_alias="t"
                )
                where_clauses.append(clause)
                params.extend(clause_params)
        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        return _CompiledDuckDBQuery(
            sql=f"""
                WITH {latest_traces_cte}
                {latest_trace_tags_cte}
                {latest_spans_cte}
                {latest_assessments_cte}
                SELECT t.trace_id, t.experiment_id, t.request_time_ms
                FROM latest_traces AS t
                {where_sql}
                ORDER BY {", ".join(self._compile_order_by(self.spec.order_by, table_alias="t"))}
                LIMIT {_validated_limit(self.spec.sql_limit, "max_results")}
            """,
            params=params,
            requires_trace_tags=self.spec.include_trace_tags,
            requires_spans=bool(self.spec.span_filters),
            requires_assessments=bool(self.spec.assessment_filters),
        )

    def _compile_span_filter(
        self, parsed_filter: _Filter, table_alias: str | None = None
    ) -> tuple[str, _QueryParams]:
        key = parsed_filter["key"]
        comparator = parsed_filter["comparator"]
        value = parsed_filter.get("value")
        expression = {
            "name": _qualify("name", table_alias),
            "type": _qualify("span_type", table_alias),
            "status": _qualify("status", table_alias),
            "content": _qualify("span_json", table_alias),
        }.get(key)
        if expression is None and key.startswith("attributes."):
            expression = _json_string_expr(
                "attributes_json", key[len("attributes.") :], table_alias
            )
        if expression is None:
            raise MlflowException.invalid_parameter_value(
                f"Unsupported span filter {parsed_filter!r}."
            )
        if comparator in {"IS NULL", "IS NOT NULL"}:
            return f"{expression} {comparator}", []
        if comparator in {"IN", "NOT IN"}:
            placeholders = ",".join("?" for _ in value)
            return f"{expression} {comparator} ({placeholders})", list(value)
        if comparator == "RLIKE":
            return f"regexp_matches(CAST({expression} AS VARCHAR), ?)", [value]
        return f"{expression} {comparator} ?", [value]

    def _compile_span_filter_exists(
        self, span_filters: list[_Filter], *, trace_alias: str
    ) -> tuple[str, _QueryParams]:
        where_clauses = [f"s.trace_id = {trace_alias}.trace_id"]
        params: _QueryParams = []
        for parsed_filter in span_filters:
            clause, clause_params = self._compile_span_filter(parsed_filter, table_alias="s")
            where_clauses.append(clause)
            params.extend(clause_params)
        return (
            f"EXISTS (SELECT 1 FROM latest_spans AS s WHERE {' AND '.join(where_clauses)})",
            params,
        )

    def _compile_assessment_value_clause(
        self, parsed_filter: _Filter, *, table_alias: str
    ) -> tuple[str, _QueryParams]:
        comparator = parsed_filter["comparator"]
        value = parsed_filter.get("value")
        if comparator in {">", ">=", "<", "<="}:
            return f"{_qualify('aggregate_value', table_alias)} {comparator} ?", [value]
        expression = _qualify("assessment_value_text", table_alias)
        if comparator in {"IN", "NOT IN"}:
            placeholders = ",".join("?" for _ in value)
            return f"{expression} {comparator} ({placeholders})", list(value)
        if comparator == "RLIKE":
            return f"regexp_matches(CAST({expression} AS VARCHAR), ?)", [value]
        return f"{expression} {comparator} ?", [value]

    def _compile_assessment_filter_exists(
        self, parsed_filter: _Filter, *, trace_alias: str
    ) -> tuple[str, _QueryParams]:
        base_where = [
            "a.assessment_type = ?",
            "a.assessment_name = ?",
            "coalesce(a.valid, true) = true",
        ]
        session_id_expr = _json_string_expr("metadata_json", TraceMetadataKey.TRACE_SESSION, "a")
        direct_where = [*base_where, f"a.trace_id = {trace_alias}.trace_id"]
        session_where = [
            *base_where,
            f"{_qualify('session_id', trace_alias)} IS NOT NULL",
            f"{session_id_expr} = {_qualify('session_id', trace_alias)}",
        ]
        base_params = [parsed_filter["type"], parsed_filter["key"]]
        comparator = parsed_filter["comparator"]
        direct_predicate = " AND ".join(direct_where)
        session_predicate = " AND ".join(session_where)
        if comparator == "IS NULL":
            clause = (
                f"NOT EXISTS (SELECT 1 FROM latest_assessments AS a WHERE {direct_predicate}) "
                f"AND NOT EXISTS (SELECT 1 FROM latest_assessments AS a "
                f"WHERE {session_predicate})"
            )
            return clause, [*base_params, *base_params]
        if comparator == "IS NOT NULL":
            clause = (
                f"EXISTS (SELECT 1 FROM latest_assessments AS a WHERE {direct_predicate}) "
                f"OR EXISTS (SELECT 1 FROM latest_assessments AS a WHERE {session_predicate})"
            )
            return clause, [*base_params, *base_params]
        value_clause, value_params = self._compile_assessment_value_clause(
            parsed_filter, table_alias="a"
        )
        direct_clause = (
            f"EXISTS (SELECT 1 FROM latest_assessments AS a "
            f"WHERE {' AND '.join([*direct_where, value_clause])})"
        )
        session_clause = (
            f"EXISTS (SELECT 1 FROM latest_assessments AS a "
            f"WHERE {' AND '.join([*session_where, value_clause])})"
        )
        return (
            f"({direct_clause} OR {session_clause})",
            [*base_params, *value_params, *base_params, *value_params],
        )

    def _compile_order_by(self, order_by: list[str], table_alias: str | None = None) -> list[str]:
        if not order_by:
            return [
                f"{_qualify('request_time_ms', table_alias)} DESC",
                f"{_qualify('trace_id', table_alias)} DESC",
            ]
        clauses = []
        for clause in order_by:
            identifier_type, key, is_ascending = SearchTraceUtils.parse_order_by_for_search_traces(
                clause
            )
            if identifier_type == "attribute":
                column = {
                    "timestamp_ms": "request_time_ms",
                    "execution_time_ms": "execution_duration_ms",
                    "status": "state",
                    "experiment_id": "experiment_id",
                }.get(key)
                column = _qualify(column, table_alias) if column else None
            elif identifier_type == "tag":
                column = (
                    _qualify("trace_name", table_alias)
                    if key == TraceTagKey.TRACE_NAME
                    else _tag_scalar_expr(key, trace_alias=table_alias)
                )
            elif identifier_type == "request_metadata":
                column = {
                    TraceMetadataKey.SOURCE_RUN: _qualify("source_run_id", table_alias),
                    TraceMetadataKey.TRACE_SESSION: _qualify("session_id", table_alias),
                    TraceMetadataKey.TRACE_USER: _qualify("trace_user", table_alias),
                }.get(key, _json_string_expr("metadata_json", key, table_alias))
            else:
                column = None
            if column is None:
                raise MlflowException(
                    f"Iceberg trace backend does not support order_by={clause!r} yet.",
                    error_code=INVALID_PARAMETER_VALUE,
                )
            clauses.append(f"{column} {'ASC' if is_ascending else 'DESC'} NULLS LAST")
        clauses.append(f"{_qualify('trace_id', table_alias)} DESC")
        return clauses


def _assessment_type(assessment: Assessment) -> str:
    if isinstance(assessment, Feedback):
        return "feedback"
    if isinstance(assessment, Expectation):
        return "expectation"
    return "issue"


def _extract_token_usage_metrics(metadata: dict[str, str]) -> dict[str, float | None]:
    token_usage_json = metadata.get(TraceMetadataKey.TOKEN_USAGE)
    if not token_usage_json:
        return {
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "cache_read_input_tokens": None,
            "cache_creation_input_tokens": None,
        }
    try:
        token_usage = json.loads(token_usage_json)
    except Exception:
        token_usage = {}
    return {
        "input_tokens": float(token_usage.get(TokenUsageKey.INPUT_TOKENS))
        if token_usage.get(TokenUsageKey.INPUT_TOKENS) is not None
        else None,
        "output_tokens": float(token_usage.get(TokenUsageKey.OUTPUT_TOKENS))
        if token_usage.get(TokenUsageKey.OUTPUT_TOKENS) is not None
        else None,
        "total_tokens": float(token_usage.get(TokenUsageKey.TOTAL_TOKENS))
        if token_usage.get(TokenUsageKey.TOTAL_TOKENS) is not None
        else None,
        "cache_read_input_tokens": float(token_usage.get(TokenUsageKey.CACHE_READ_INPUT_TOKENS))
        if token_usage.get(TokenUsageKey.CACHE_READ_INPUT_TOKENS) is not None
        else None,
        "cache_creation_input_tokens": float(
            token_usage.get(TokenUsageKey.CACHE_CREATION_INPUT_TOKENS)
        )
        if token_usage.get(TokenUsageKey.CACHE_CREATION_INPUT_TOKENS) is not None
        else None,
    }


def _extract_cost_metrics(metadata: dict[str, str]) -> dict[str, float | None]:
    cost_json = metadata.get(TraceMetadataKey.COST)
    if not cost_json:
        return {
            "input_cost": None,
            "output_cost": None,
            "total_cost": None,
        }
    try:
        cost = json.loads(cost_json)
    except Exception:
        cost = {}
    return {
        "input_cost": _finite_float(cost.get(CostKey.INPUT_COST)),
        "output_cost": _finite_float(cost.get(CostKey.OUTPUT_COST)),
        "total_cost": _finite_float(cost.get(CostKey.TOTAL_COST)),
    }


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    return numeric_value if math.isfinite(numeric_value) else None


def _decode_json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _span_metric_payload(attributes: dict[str, Any]) -> dict[str, float | str | None]:
    cost_payload = _decode_json_value(attributes.get(SpanAttributeKey.LLM_COST))
    if not isinstance(cost_payload, dict):
        cost_payload = {}

    return {
        "model_name": _decode_json_value(attributes.get(SpanAttributeKey.MODEL)),
        "model_provider": _decode_json_value(attributes.get(SpanAttributeKey.MODEL_PROVIDER)),
        "input_cost": _finite_float(cost_payload.get(CostKey.INPUT_COST)),
        "output_cost": _finite_float(cost_payload.get(CostKey.OUTPUT_COST)),
        "total_cost": _finite_float(cost_payload.get(CostKey.TOTAL_COST)),
    }


def _assessment_value_payload(
    assessment: Assessment,
) -> tuple[str | None, str | None]:
    value = getattr(assessment, "value", None)
    if value is None:
        return None, None

    value_json = json.dumps(value, sort_keys=True)
    value_text = value if isinstance(value, str) else value_json
    return value_json, value_text


def _assessment_value_matches_string(
    value_text: str | None, comparator: str, expected: str
) -> bool:
    if comparator == "IS NULL":
        return value_text is None
    if comparator == "IS NOT NULL":
        return value_text is not None
    if value_text is None:
        return False
    if comparator == "=":
        return value_text == expected
    if comparator == "!=":
        return value_text != expected
    if comparator == "LIKE":
        return _convert_like_pattern_to_regex(expected).match(value_text) is not None
    if comparator == "ILIKE":
        return (
            _convert_like_pattern_to_regex(expected, flags=re.IGNORECASE).match(value_text)
            is not None
        )
    if comparator == "RLIKE":
        return re.search(expected, value_text) is not None
    raise MlflowException.invalid_parameter_value(f"Unsupported comparator {comparator!r}")


def _assessment_value_matches_numeric(
    numeric_value: float | None, comparator: str, expected: int | float
) -> bool:
    if numeric_value is None:
        return False
    return SearchUtils.get_comparison_func(comparator)(numeric_value, expected)


class _IcebergTraceStoreBase:
    def __init__(self, *args, **kwargs):
        self.tracking_store = self
        super().__init__(*args, **kwargs)
        self.__resources: _IcebergProcessResources | None = None
        if self._hybrid_enabled:
            with self.acquire_iceberg_trace_write_lock():
                if self.get_iceberg_trace_publication_state() is None:
                    self._publish_current_iceberg_cut()

    @property
    def _resources(self) -> _IcebergProcessResources:
        if self.__resources is None or self.__resources.owner_pid != os.getpid():
            self.__resources = _get_process_resources(
                tracking_db_uri=getattr(self.tracking_store, "db_uri", None),
            )
        return self.__resources

    def warm_iceberg_trace_query_resources(self) -> None:
        _ = self._resources

    @staticmethod
    def _clear_process_resources() -> None:
        _clear_process_resources()

    @staticmethod
    def _workspaces_enabled() -> bool:
        return MLFLOW_ENABLE_WORKSPACES.get()

    def _get_active_workspace(self) -> str | None:
        inherited_get_active_workspace = getattr(super(), "_get_active_workspace", None)
        if inherited_get_active_workspace is not None and self.supports_workspaces:
            return inherited_get_active_workspace()
        if not self._workspaces_enabled():
            return DEFAULT_WORKSPACE_NAME

        if workspace := get_request_workspace():
            return workspace

        raise MlflowException.invalid_parameter_value(
            "Active workspace is required. Configure a default workspace or call "
            "mlflow.set_workspace() before interacting with the store."
        )

    def _trace_table(self):
        return self._resources.catalog.load_table((*_ICEBERG_NAMESPACE, _TRACE_INDEX_TABLE))

    def _trace_tag_table(self):
        return self._resources.catalog.load_table((
            *_ICEBERG_NAMESPACE,
            _TRACE_TAG_INDEX_TABLE,
        ))

    def _span_table(self):
        return self._resources.catalog.load_table((*_ICEBERG_NAMESPACE, _SPAN_INDEX_TABLE))

    def _assessment_table(self):
        return self._resources.catalog.load_table((
            *_ICEBERG_NAMESPACE,
            _ASSESSMENT_INDEX_TABLE,
        ))

    def _trace_metric_rollup_table(self):
        return self._resources.catalog.load_table((
            *_ICEBERG_NAMESPACE,
            _TRACE_METRIC_DAILY_ROLLUPS_TABLE,
        ))

    def _span_cost_rollup_table(self):
        return self._resources.catalog.load_table((
            *_ICEBERG_NAMESPACE,
            _SPAN_COST_DAILY_ROLLUPS_TABLE,
        ))

    def _assessment_rollup_table(self):
        return self._resources.catalog.load_table((
            *_ICEBERG_NAMESPACE,
            _ASSESSMENT_DAILY_ROLLUPS_TABLE,
        ))

    def _session_summary_table(self):
        return self._resources.catalog.load_table((
            *_ICEBERG_NAMESPACE,
            _SESSION_SUMMARY_TABLE,
        ))

    def _current_iceberg_state(self) -> tuple[dict[str, str], dict[str, int | None]]:
        tables = {
            table_name: self._resources.catalog.load_table((*_ICEBERG_NAMESPACE, table_name))
            for table_name in _TABLE_SCHEMAS
        }
        metadata_locations = {
            table_name: table.metadata_location for table_name, table in tables.items()
        }
        snapshot_ids = {
            table_name: snapshot.snapshot_id if (snapshot := table.current_snapshot()) else None
            for table_name, table in tables.items()
        }
        return metadata_locations, snapshot_ids

    @staticmethod
    def _iceberg_publication_state_dict(
        publication_state: SqlIcebergTracePublicationState,
    ) -> dict[str, Any]:
        return {
            "trace_index_metadata_location": publication_state.trace_index_metadata_location,
            "trace_tag_index_metadata_location": (
                publication_state.trace_tag_index_metadata_location
            ),
            "span_index_metadata_location": publication_state.span_index_metadata_location,
            "assessment_index_metadata_location": (
                publication_state.assessment_index_metadata_location
            ),
            "trace_metric_daily_rollups_metadata_location": (
                publication_state.trace_metric_daily_rollups_metadata_location
            ),
            "span_cost_daily_rollups_metadata_location": (
                publication_state.span_cost_daily_rollups_metadata_location
            ),
            "assessment_daily_rollups_metadata_location": (
                publication_state.assessment_daily_rollups_metadata_location
            ),
            "session_summary_metadata_location": (
                publication_state.session_summary_metadata_location
            ),
            "trace_index_snapshot_id": publication_state.trace_index_snapshot_id,
            "trace_tag_index_snapshot_id": publication_state.trace_tag_index_snapshot_id,
            "span_index_snapshot_id": publication_state.span_index_snapshot_id,
            "assessment_index_snapshot_id": publication_state.assessment_index_snapshot_id,
            "trace_metric_daily_rollups_snapshot_id": (
                publication_state.trace_metric_daily_rollups_snapshot_id
            ),
            "span_cost_daily_rollups_snapshot_id": (
                publication_state.span_cost_daily_rollups_snapshot_id
            ),
            "assessment_daily_rollups_snapshot_id": (
                publication_state.assessment_daily_rollups_snapshot_id
            ),
            "session_summary_snapshot_id": publication_state.session_summary_snapshot_id,
            "publication_blocked": publication_state.publication_blocked,
            "published_at_ms": publication_state.published_at_ms,
        }

    def get_iceberg_trace_publication_state(self) -> dict[str, Any] | None:
        with self.ManagedSessionMaker() as session:
            publication_state = session.get(SqlIcebergTracePublicationState, "default")
            return (
                self._iceberg_publication_state_dict(publication_state)
                if publication_state is not None
                else None
            )

    @staticmethod
    def _get_or_create_locked_iceberg_publication_state(session, expected_published_at_ms):
        query = session.query(SqlIcebergTracePublicationState).filter(
            SqlIcebergTracePublicationState.state_key == "default"
        )
        publication_state = query.with_for_update().one_or_none()
        if publication_state is not None:
            return publication_state, publication_state.published_at_ms
        if expected_published_at_ms is not None:
            return None, None

        try:
            with session.begin_nested():
                publication_state = SqlIcebergTracePublicationState(state_key="default")
                session.add(publication_state)
                session.flush()
            return publication_state, None
        except IntegrityError:
            publication_state = query.with_for_update().one()
            return publication_state, publication_state.published_at_ms

    @staticmethod
    def _set_locked_iceberg_publication_state(
        publication_state: SqlIcebergTracePublicationState,
        *,
        metadata_locations: dict[str, str | None],
        snapshot_ids: dict[str, int | None],
        observed_published_at_ms: int | None,
    ) -> None:
        publication_state.trace_index_metadata_location = metadata_locations.get("trace_index")
        publication_state.trace_tag_index_metadata_location = metadata_locations.get(
            "trace_tag_index"
        )
        publication_state.span_index_metadata_location = metadata_locations.get("span_index")
        publication_state.assessment_index_metadata_location = metadata_locations.get(
            "assessment_index"
        )
        publication_state.trace_metric_daily_rollups_metadata_location = metadata_locations.get(
            "trace_metric_daily_rollups"
        )
        publication_state.span_cost_daily_rollups_metadata_location = metadata_locations.get(
            "span_cost_daily_rollups"
        )
        publication_state.assessment_daily_rollups_metadata_location = metadata_locations.get(
            "assessment_daily_rollups"
        )
        publication_state.session_summary_metadata_location = metadata_locations.get(
            "session_summary"
        )
        publication_state.trace_index_snapshot_id = snapshot_ids.get("trace_index")
        publication_state.trace_tag_index_snapshot_id = snapshot_ids.get("trace_tag_index")
        publication_state.span_index_snapshot_id = snapshot_ids.get("span_index")
        publication_state.assessment_index_snapshot_id = snapshot_ids.get("assessment_index")
        publication_state.trace_metric_daily_rollups_snapshot_id = snapshot_ids.get(
            "trace_metric_daily_rollups"
        )
        publication_state.span_cost_daily_rollups_snapshot_id = snapshot_ids.get(
            "span_cost_daily_rollups"
        )
        publication_state.assessment_daily_rollups_snapshot_id = snapshot_ids.get(
            "assessment_daily_rollups"
        )
        publication_state.session_summary_snapshot_id = snapshot_ids.get("session_summary")
        publication_state.publication_blocked = False
        publication_state.published_at_ms = max(
            get_current_time_millis(),
            (observed_published_at_ms or 0) + 1,
        )

    @contextmanager
    def acquire_iceberg_trace_write_lock(self):
        if self.engine.dialect.name == "sqlite":
            lock_name = hashlib.sha256(self.db_uri.encode("utf-8")).hexdigest()
            with ExclusiveFileLock(f"{tempfile.gettempdir()}/iceberg-trace-write-{lock_name}"):
                yield
            return
        if self.engine.dialect.name != "postgresql":
            yield
            return

        with self.engine.connect() as connection:
            connection.execute(
                text("SELECT pg_advisory_lock(:lock_id)"),
                {"lock_id": _ICEBERG_TRACE_WRITE_LOCK_ID},
            )
            connection.commit()
            try:
                yield
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": _ICEBERG_TRACE_WRITE_LOCK_ID},
                )
                connection.commit()

    def set_iceberg_trace_publication_state(
        self,
        *,
        metadata_locations: dict[str, str | None],
        expected_published_at_ms: int | None,
        snapshot_ids: dict[str, int | None] | None = None,
        allow_blocked: bool = False,
    ) -> bool:
        with self.ManagedSessionMaker(read_only=False) as session:
            publication_state, observed_published_at_ms = (
                self._get_or_create_locked_iceberg_publication_state(
                    session, expected_published_at_ms
                )
            )
            if (
                publication_state is None
                or observed_published_at_ms != expected_published_at_ms
                or (publication_state.publication_blocked and not allow_blocked)
            ):
                return False
            self._set_locked_iceberg_publication_state(
                publication_state,
                metadata_locations=metadata_locations,
                snapshot_ids=snapshot_ids or {},
                observed_published_at_ms=observed_published_at_ms,
            )
            return True

    def _begin_iceberg_trace_publication_barrier(self) -> None:
        with self.ManagedSessionMaker(read_only=False) as session:
            publication_state = (
                session
                .query(SqlIcebergTracePublicationState)
                .filter(SqlIcebergTracePublicationState.state_key == "default")
                .with_for_update()
                .one()
            )
            if publication_state.publication_blocked:
                raise MlflowException(
                    "Iceberg trace publication is blocked by an incomplete prior write. "
                    "Repair or restore the Iceberg warehouse before publishing another cut.",
                    error_code=INVALID_STATE,
                )
            publication_state.publication_blocked = True

    def _clear_iceberg_trace_publication_barrier(self) -> None:
        with self.ManagedSessionMaker(read_only=False) as session:
            publication_state = (
                session
                .query(SqlIcebergTracePublicationState)
                .filter(SqlIcebergTracePublicationState.state_key == "default")
                .with_for_update()
                .one()
            )
            publication_state.publication_blocked = False

    def _ensure_iceberg_trace_publication_unblocked(self) -> None:
        with self.ManagedSessionMaker() as session:
            publication_blocked = (
                session
                .query(SqlIcebergTracePublicationState.publication_blocked)
                .filter(SqlIcebergTracePublicationState.state_key == "default")
                .scalar()
            )
        if publication_blocked:
            raise MlflowException(
                "Iceberg trace publication is blocked by an incomplete prior write. "
                "Repair or restore the Iceberg warehouse before modifying it.",
                error_code=INVALID_STATE,
            )

    @staticmethod
    def _archived_trace_locator_dict(locator: SqlArchivedTraceLocator) -> dict[str, Any]:
        return {
            "workspace": locator.workspace,
            "experiment_id": locator.experiment_id,
            "trace_id": locator.trace_id,
            "request_time_ms": locator.request_time_ms,
            "request_day": locator.request_day,
            "archive_uri": locator.archive_uri,
            "published_at_ms": locator.published_at_ms,
        }

    def get_archived_trace_locator(self, trace_id: str) -> dict[str, Any] | None:
        workspace = self._get_active_workspace()
        with self.ManagedSessionMaker() as session:
            locator = (
                session
                .query(SqlArchivedTraceLocator)
                .filter(
                    SqlArchivedTraceLocator.workspace == workspace,
                    SqlArchivedTraceLocator.trace_id == trace_id,
                )
                .one_or_none()
            )
            return self._archived_trace_locator_dict(locator) if locator is not None else None

    def get_archived_trace_locators(self, trace_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not trace_ids:
            return {}
        workspace = self._get_active_workspace()
        with self.ManagedSessionMaker() as session:
            rows = (
                session
                .query(SqlArchivedTraceLocator)
                .filter(
                    SqlArchivedTraceLocator.workspace == workspace,
                    SqlArchivedTraceLocator.trace_id.in_(trace_ids),
                )
                .all()
            )
            return {row.trace_id: self._archived_trace_locator_dict(row) for row in rows}

    def _select_expired_archived_trace_locators_for_delete(
        self,
        *,
        max_request_time_ms: int,
        trace_ids: list[str] | None = None,
    ) -> list[_TraceDeleteSelection]:
        workspace = self._get_active_workspace()
        with self.ManagedSessionMaker() as session:
            query = session.query(SqlArchivedTraceLocator).filter(
                SqlArchivedTraceLocator.workspace == workspace,
                SqlArchivedTraceLocator.request_time_ms <= max_request_time_ms,
            )
            if trace_ids is not None:
                query = query.filter(SqlArchivedTraceLocator.trace_id.in_(trace_ids))
            return [
                _TraceDeleteSelection(
                    trace_id=row.trace_id,
                    archived_artifact_uri=row.archive_uri,
                )
                for row in query.all()
            ]

    def _clear_archived_trace_locator_payload_uris(self, trace_ids: list[str]) -> None:
        if not trace_ids:
            return
        workspace = self._get_active_workspace()
        with self.ManagedSessionMaker(read_only=False) as session:
            (
                session
                .query(SqlArchivedTraceLocator)
                .filter(
                    SqlArchivedTraceLocator.workspace == workspace,
                    SqlArchivedTraceLocator.trace_id.in_(trace_ids),
                )
                .update({SqlArchivedTraceLocator.archive_uri: None}, synchronize_session=False)
            )

    def _publish_iceberg_trace_deletion(
        self,
        trace_ids: list[str],
        *,
        delete_hot_rows: bool,
        allow_blocked: bool = False,
    ) -> int:
        if not trace_ids:
            return 0
        metadata_locations, snapshot_ids = self._current_iceberg_state()
        workspace = self._get_active_workspace()
        with self.ManagedSessionMaker(read_only=False) as session:
            experiment_ids = {
                int(experiment_id)
                for (experiment_id,) in (
                    session
                    .query(SqlArchivedTraceLocator.experiment_id)
                    .filter(
                        SqlArchivedTraceLocator.workspace == workspace,
                        SqlArchivedTraceLocator.trace_id.in_(trace_ids),
                    )
                    .all()
                )
            }
            self._lock_sql_rollup_experiments(session, experiment_ids)
            if delete_hot_rows:
                self._invalidate_sql_rollups_for_trace_ids(session, trace_ids)
            publication_state = (
                session
                .query(SqlIcebergTracePublicationState)
                .filter(SqlIcebergTracePublicationState.state_key == "default")
                .with_for_update()
                .one_or_none()
            )
            if publication_state is None:
                raise MlflowException(
                    "Cannot publish an Iceberg deletion without publication state.",
                    error_code=INVALID_STATE,
                )
            if publication_state.publication_blocked and not allow_blocked:
                raise MlflowException(
                    "Cannot publish an Iceberg deletion while trace publication is blocked.",
                    error_code=INVALID_STATE,
                )
            self._set_locked_iceberg_publication_state(
                publication_state,
                metadata_locations=metadata_locations,
                snapshot_ids=snapshot_ids,
                observed_published_at_ms=publication_state.published_at_ms,
            )
            (
                session
                .query(SqlArchivedTraceLocator)
                .filter(
                    SqlArchivedTraceLocator.workspace == workspace,
                    SqlArchivedTraceLocator.trace_id.in_(trace_ids),
                )
                .delete(synchronize_session=False)
            )
            if not delete_hot_rows:
                return 0
            deleted_count = (
                session
                .query(SqlTraceInfo)
                .filter(SqlTraceInfo.request_id.in_(trace_ids))
                .delete(synchronize_session=False)
            )
            self._delete_review_queue_items_for_traces(session, trace_ids)
            return deleted_count

    def _delete_unreferenced_archived_trace_payload(
        self,
        *,
        trace_id: str,
        artifact_uri: str,
        artifact_repo,
    ) -> None:
        workspace = self._get_active_workspace()
        with self.ManagedSessionMaker() as session:
            locator_exists = (
                session
                .query(SqlArchivedTraceLocator.trace_id)
                .filter(
                    SqlArchivedTraceLocator.workspace == workspace,
                    SqlArchivedTraceLocator.trace_id == trace_id,
                    SqlArchivedTraceLocator.archive_uri == artifact_uri,
                )
                .first()
                is not None
            )
        if not locator_exists:
            super()._delete_unreferenced_archived_trace_payload(
                trace_id=trace_id,
                artifact_uri=artifact_uri,
                artifact_repo=artifact_repo,
            )

    def publish_archived_trace_batch(
        self,
        *,
        traces: list[dict[str, Any]],
        metadata_locations: dict[str, str | None],
        expected_published_at_ms: int | None,
        snapshot_ids: dict[str, int | None] | None = None,
    ) -> tuple[list[str], list[str]] | None:
        """
        Atomically publish an Iceberg cut, archived locators, and hot-row eviction.

        Returns newly published trace IDs and IDs already protected by locators. ``None``
        indicates that the expected publication cut changed and the caller must retry.
        """
        if not traces:
            return [], []

        trace_ids = [trace["trace_id"] for trace in traces]
        traces_by_id = {trace["trace_id"]: trace for trace in traces}
        workspace = self._get_active_workspace()

        with self.ManagedSessionMaker(read_only=False) as session:
            experiment_ids = {
                int(trace["experiment_id"])
                for trace in traces
                if trace.get("experiment_id") is not None
            }
            if len(experiment_ids) < len({trace["trace_id"] for trace in traces}):
                experiment_ids.update(
                    int(experiment_id)
                    for (experiment_id,) in (
                        self
                        ._trace_query(session, workspace=workspace)
                        .with_entities(SqlTraceInfo.experiment_id)
                        .filter(SqlTraceInfo.request_id.in_(trace_ids))
                        .all()
                    )
                )
            self._lock_sql_rollup_experiments(session, experiment_ids)
            locked_traces = (
                self
                ._trace_query(session, for_update_or_delete=True, workspace=workspace)
                .filter(SqlTraceInfo.request_id.in_(trace_ids))
                .all()
            )
            locked_by_id = {trace.request_id: trace for trace in locked_traces}
            existing_locator_uris = dict(
                session
                .query(SqlArchivedTraceLocator.trace_id, SqlArchivedTraceLocator.archive_uri)
                .filter(
                    SqlArchivedTraceLocator.workspace == workspace,
                    SqlArchivedTraceLocator.trace_id.in_(trace_ids),
                )
                .all()
            )
            already_published_trace_ids = {
                trace_id
                for trace_id, archive_uri in existing_locator_uris.items()
                if (attempted_archive_uri := traces_by_id[trace_id].get("archive_uri")) is None
                or archive_uri == attempted_archive_uri
            }
            publishable_trace_ids = [
                trace_id
                for trace_id in trace_ids
                if (sql_trace_info := locked_by_id.get(trace_id)) is not None
                and sql_trace_info.db_payload_generation
                == traces_by_id[trace_id]["db_payload_generation"]
                and sql_trace_info.status != TraceState.IN_PROGRESS.value
            ]
            if not publishable_trace_ids and already_published_trace_ids == set(trace_ids):
                return [], list(already_published_trace_ids)
            if set(publishable_trace_ids) != set(trace_ids):
                return None

            publication_state, observed_published_at_ms = (
                self._get_or_create_locked_iceberg_publication_state(
                    session, expected_published_at_ms
                )
            )
            if publication_state is None or observed_published_at_ms != expected_published_at_ms:
                return None
            self._set_locked_iceberg_publication_state(
                publication_state,
                metadata_locations=metadata_locations,
                snapshot_ids=snapshot_ids or {},
                observed_published_at_ms=observed_published_at_ms,
            )

            locator_rows = []
            for trace_id in publishable_trace_ids:
                trace = traces_by_id[trace_id]
                locator_rows.append({
                    "workspace": workspace,
                    "experiment_id": int(trace["experiment_id"]),
                    "trace_id": trace_id,
                    "request_time_ms": int(trace["request_time_ms"]),
                    "request_day": trace["request_day"],
                    "archive_uri": trace.get("archive_uri"),
                    "published_at_ms": publication_state.published_at_ms,
                })
            _bulk_upsert(session, SqlArchivedTraceLocator, locator_rows)

            self._delete_sql_rollups_for_published_traces(
                session=session,
                workspace=workspace,
                traces=[traces_by_id[trace_id] for trace_id in publishable_trace_ids],
            )
            (
                self
                ._trace_query(session, workspace=workspace)
                .filter(SqlTraceInfo.request_id.in_(publishable_trace_ids))
                .delete(synchronize_session=False)
            )
            self._delete_review_queue_items_for_traces(session, publishable_trace_ids)
            return publishable_trace_ids, list(already_published_trace_ids)

    @staticmethod
    def _delete_sql_rollups_for_published_traces(
        *,
        session,
        workspace: str,
        traces: list[dict[str, Any]],
    ) -> None:
        trace_invalidation_days: dict[int, date] = {}
        span_invalidation_days: dict[int, date] = {}
        for trace in traces:
            experiment_id = int(trace["experiment_id"])
            if request_day := trace.get("request_day"):
                trace_invalidation_days[experiment_id] = min(
                    request_day,
                    trace_invalidation_days.get(experiment_id, request_day),
                )
            if span_start_days := trace.get("span_start_days"):
                earliest_span_day = min(span_start_days)
                span_invalidation_days[experiment_id] = min(
                    earliest_span_day,
                    span_invalidation_days.get(experiment_id, earliest_span_day),
                )

        # SQL rollup reads infer contiguous coverage from the first and last rolled-up day.
        # Removing only a touched day could leave a gap that appears covered, so invalidate each
        # family's suffix from the earliest source day touched by the published traces. Trace and
        # assessment rollups are trace-day scoped; span-cost rollups are span-start-day scoped.
        for model, invalidation_days in (
            (SqlTraceMetricDailyRollup, trace_invalidation_days),
            (SqlAssessmentDailyRollup, trace_invalidation_days),
            (SqlSpanCostDailyRollup, span_invalidation_days),
        ):
            if not invalidation_days:
                continue
            partition_clauses = [
                and_(model.experiment_id == experiment_id, model.rollup_day >= rollup_day)
                for experiment_id, rollup_day in invalidation_days.items()
            ]
            (
                session
                .query(model)
                .filter(model.workspace == workspace, or_(*partition_clauses))
                .delete(synchronize_session=False)
            )

    def _select_publishable_archived_trace_ids(
        self,
        *,
        traces: list[dict[str, Any]],
    ) -> list[str]:
        if not traces:
            return []

        trace_ids = [trace["trace_id"] for trace in traces]
        traces_by_id = {trace["trace_id"]: trace for trace in traces}
        with self.ManagedSessionMaker(read_only=False) as session:
            locked_traces = (
                self
                ._trace_query(session, for_update_or_delete=True)
                .options(selectinload(SqlTraceInfo.tags))
                .filter(SqlTraceInfo.request_id.in_(trace_ids))
                .all()
            )
            locked_by_id = {trace.request_id: trace for trace in locked_traces}
            trace_ids_with_spans = {
                trace_id
                for (trace_id,) in (
                    session
                    .query(SqlSpan.trace_id)
                    .filter(
                        SqlSpan.trace_id.in_(trace_ids),
                        SqlSpan.content != "",
                    )
                    .distinct()
                    .all()
                )
            }
            return [
                trace_id
                for trace_id in trace_ids
                if (sql_trace_info := locked_by_id.get(trace_id)) is not None
                and sql_trace_info.db_payload_generation
                == traces_by_id[trace_id]["db_payload_generation"]
                and self._is_trace_metadata_actionable_for_archival(sql_trace_info)
                and trace_id in trace_ids_with_spans
            ]

    def _load_published_iceberg_cut(self) -> _PublishedIcebergCut | None:
        if not self._hybrid_enabled:
            return None
        publication_state = self.get_iceberg_trace_publication_state()
        if publication_state is None:
            return None
        warehouse_prefix = self._resources.warehouse_uri.rstrip("/") + "/"
        locations = {
            _TRACE_INDEX_TABLE: publication_state["trace_index_metadata_location"],
            _TRACE_TAG_INDEX_TABLE: publication_state["trace_tag_index_metadata_location"],
            _SPAN_INDEX_TABLE: publication_state["span_index_metadata_location"],
            _ASSESSMENT_INDEX_TABLE: publication_state["assessment_index_metadata_location"],
            _TRACE_METRIC_DAILY_ROLLUPS_TABLE: publication_state.get(
                "trace_metric_daily_rollups_metadata_location"
            ),
            _SPAN_COST_DAILY_ROLLUPS_TABLE: publication_state.get(
                "span_cost_daily_rollups_metadata_location"
            ),
            _ASSESSMENT_DAILY_ROLLUPS_TABLE: publication_state.get(
                "assessment_daily_rollups_metadata_location"
            ),
            _SESSION_SUMMARY_TABLE: publication_state.get("session_summary_metadata_location"),
        }
        for table_name, metadata_location in locations.items():
            if metadata_location is None:
                continue
            if not metadata_location.startswith(warehouse_prefix):
                raise MlflowException(
                    f"Published metadata location for table '{table_name}' is outside the "
                    "configured Iceberg warehouse.",
                    error_code=INVALID_STATE,
                )
        snapshot_ids = {
            table_name: publication_state.get(f"{table_name}_snapshot_id")
            for table_name in locations
        }
        return _PublishedIcebergCut(
            metadata_locations=MappingProxyType(locations),
            snapshot_ids=MappingProxyType(snapshot_ids),
            published_at_ms=publication_state["published_at_ms"],
        )

    def _published_iceberg_cut(self) -> _PublishedIcebergCut | None:
        pinned_cut = _PINNED_ICEBERG_CUT.get()
        if pinned_cut is not _NO_PINNED_ICEBERG_CUT and pinned_cut.tracking_store_id == id(
            self.tracking_store
        ):
            return pinned_cut.cut
        return self._load_published_iceberg_cut()

    def _live_iceberg_table_state(self, table_name: str) -> _LiveIcebergTableState:
        pinned_cut = _PINNED_ICEBERG_CUT.get()
        if pinned_cut is not _NO_PINNED_ICEBERG_CUT and pinned_cut.tracking_store_id == id(
            self.tracking_store
        ):
            with pinned_cut.live_table_states_lock:
                if table_state := pinned_cut.live_table_states.get(table_name):
                    return table_state
                table_state = self._load_live_iceberg_table_state(table_name)
                pinned_cut.live_table_states[table_name] = table_state
                return table_state
        return self._load_live_iceberg_table_state(table_name)

    def _load_live_iceberg_table_state(self, table_name: str) -> _LiveIcebergTableState:
        table = self._resources.catalog.load_table((*_ICEBERG_NAMESPACE, table_name))
        snapshot = table.current_snapshot()
        return _LiveIcebergTableState(
            metadata_location=table.metadata_location,
            snapshot_id=snapshot.snapshot_id if snapshot else None,
        )

    def _iceberg_table_has_snapshot(self, table_name: str) -> bool:
        published_cut = self._published_iceberg_cut()
        if self._hybrid_enabled or published_cut is not None:
            return (
                published_cut is not None
                and published_cut.metadata_locations.get(table_name) is not None
                and published_cut.snapshot_ids.get(table_name) is not None
            )
        return self._live_iceberg_table_state(table_name).snapshot_id is not None

    def _archived_trace_locator(self, trace_id: str):
        if not self._hybrid_enabled:
            return None
        return self.get_archived_trace_locator(trace_id)

    def _publish_current_iceberg_cut(self, *, allow_blocked: bool = False) -> None:
        if not self._hybrid_enabled:
            return
        for _ in range(3):
            publication_state = self.get_iceberg_trace_publication_state()
            metadata_locations, snapshot_ids = self._current_iceberg_state()
            if self.set_iceberg_trace_publication_state(
                metadata_locations=metadata_locations,
                snapshot_ids=snapshot_ids,
                expected_published_at_ms=(
                    publication_state["published_at_ms"] if publication_state else None
                ),
                allow_blocked=allow_blocked,
            ):
                return
        raise MlflowException(
            "Failed to publish the current Iceberg cut after concurrent updates.",
            error_code=INVALID_STATE,
        )

    def compact_iceberg_trace_tables(self, *, expire_snapshots: bool = False):
        with (
            self.acquire_iceberg_trace_write_lock(),
            self._resources.write_lock,
        ):
            self._begin_iceberg_trace_publication_barrier()
            results = compact_iceberg_trace_tables(
                self._resources.catalog,
                expire_snapshots=expire_snapshots,
            )
            self._publish_current_iceberg_cut(allow_blocked=True)
            return results

    def build_sql_trace_rollups(self, *, max_partitions: int) -> int:
        with self.acquire_iceberg_trace_write_lock():
            return super().build_sql_trace_rollups(max_partitions=max_partitions)

    def _validated_archive_uri_for_trace(self, trace_info: TraceInfo) -> str:
        archive_uri = _validate_trace_archival_location(
            get_archive_uri_for_trace(trace_info),
            parameter_name="archive_uri",
        )
        if trace_archival_config := get_trace_archival_server_config():
            if trace_archival_config.enabled:
                expected_prefix = trace_archival_config.location.rstrip("/") + "/"
                if not archive_uri.startswith(expected_prefix):
                    raise MlflowException(
                        "Archived trace payload URI is outside the configured archival location.",
                        error_code=INVALID_STATE,
                    )
        return archive_uri

    def _iceberg_scan(
        self,
        table_name: str,
        *,
        projected_columns: set[str] | tuple[str, ...] | None = None,
        workspace: str | None = None,
        experiment_ids: tuple[str, ...] | None = None,
        day_column: str | None = None,
        time_column: str | None = None,
        time_multiplier: int | None = None,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
        trace_ids: tuple[str, ...] | None = None,
        published_metadata: bool = True,
        include_required_columns: bool = True,
    ) -> _DuckDBIcebergScan:
        profile = _profile_iceberg_trace_queries()
        start_time = time.perf_counter() if profile else 0.0
        projected_columns = (
            tuple(
                _projected_columns(
                    table_name,
                    tuple(projected_columns),
                    include_required_columns=include_required_columns,
                )
            )
            if projected_columns is not None
            else None
        )
        if time_column is None:
            time_column = {
                (_TRACE_INDEX_TABLE, "request_day"): "request_time_ms",
                (_ASSESSMENT_INDEX_TABLE, "trace_request_day"): "trace_request_time_ms",
                (_ASSESSMENT_INDEX_TABLE, "assessment_create_day"): "create_time_ms",
                (_SPAN_INDEX_TABLE, "span_start_day"): "start_time_ns",
            }.get((table_name, day_column))
        if time_multiplier is None:
            time_multiplier = 1_000_000 if time_column == "start_time_ns" else 1
        published_cut = self._published_iceberg_cut() if published_metadata else None
        if published_metadata and published_cut is not None:
            if published_metadata_location := published_cut.metadata_locations.get(table_name):
                return _DuckDBIcebergScan(
                    table_name=table_name,
                    metadata_location=published_metadata_location,
                    published_at_ms=published_cut.published_at_ms,
                    projected_columns=projected_columns,
                    workspace=workspace,
                    experiment_ids=experiment_ids,
                    day_column=day_column,
                    time_column=time_column,
                    time_multiplier=time_multiplier,
                    start_time_ms=start_time_ms,
                    end_time_ms=end_time_ms,
                    trace_ids=trace_ids,
                )
            table_state = self._live_iceberg_table_state(table_name)
            return _DuckDBIcebergScan(
                table_name=table_name,
                metadata_location=table_state.metadata_location,
                published_at_ms=published_cut.published_at_ms,
                projected_columns=projected_columns,
                workspace=workspace,
                experiment_ids=experiment_ids,
                day_column=day_column,
                time_column=time_column,
                time_multiplier=time_multiplier,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                trace_ids=trace_ids,
                empty=True,
            )
        if published_metadata and self._hybrid_enabled:
            table_state = self._live_iceberg_table_state(table_name)
            return _DuckDBIcebergScan(
                table_name=table_name,
                metadata_location=table_state.metadata_location,
                projected_columns=projected_columns,
                workspace=workspace,
                experiment_ids=experiment_ids,
                day_column=day_column,
                time_column=time_column,
                time_multiplier=time_multiplier,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                trace_ids=trace_ids,
                empty=True,
            )
        table_state = (
            self._live_iceberg_table_state(table_name)
            if published_metadata
            else self._load_live_iceberg_table_state(table_name)
        )
        table_load_ms = _elapsed_ms(start_time) if profile else None
        plan_start_time = time.perf_counter() if profile else 0.0
        scan = _DuckDBIcebergScan(
            table_name=table_name,
            metadata_location=table_state.metadata_location,
            projected_columns=projected_columns,
            workspace=workspace,
            experiment_ids=experiment_ids,
            day_column=day_column,
            time_column=time_column,
            time_multiplier=time_multiplier,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            trace_ids=trace_ids,
        )
        if profile:
            _logger.info(
                "Iceberg trace scan planned: %s",
                {
                    **_scan_profile(scan),
                    "table_load_ms": table_load_ms,
                    "plan_ms": _elapsed_ms(plan_start_time),
                    "metadata_location": scan.metadata_location,
                },
            )
        return scan

    def _run_duckdb_query(
        self,
        *,
        trace_rows: _DuckDBIcebergScan | None = None,
        trace_tag_rows: _DuckDBIcebergScan | None = None,
        span_rows: _DuckDBIcebergScan | None = None,
        assessment_rows: _DuckDBIcebergScan | None = None,
        trace_detail_rows: _DuckDBIcebergScan | None = None,
        arrow_tables: Mapping[str, pa.Table] | None = None,
        sql: str,
        params: list[Any] | tuple[Any, ...] | None = None,
    ) -> list[_Row]:
        scans = {
            "trace_rows": trace_rows,
            "trace_tag_rows": trace_tag_rows,
            "span_rows": span_rows,
            "assessment_rows": assessment_rows,
            "trace_detail_rows": trace_detail_rows,
        }

        def _execute(active_scans: dict[str, _DuckDBIcebergScan | None]) -> list[_Row]:
            profile = _profile_iceberg_trace_queries()
            ctes = []
            scan_params = []
            for name, scan in active_scans.items():
                if scan is not None:
                    cte, cte_params = _iceberg_scan_cte(name, scan)
                    ctes.append(cte)
                    scan_params.extend(cte_params)
            final_sql = _prepend_ctes(sql, ctes)
            start_time = time.perf_counter() if profile else 0.0
            populated_scans = [scan for scan in active_scans.values() if scan is not None]
            heavy_fact_scan = any(
                scan.table_name in {_SPAN_INDEX_TABLE, _ASSESSMENT_INDEX_TABLE}
                and not scan.trace_ids
                for scan in populated_scans
            )
            high_priority = not heavy_fact_scan and (
                any(scan.trace_ids for scan in populated_scans)
                or all(
                    scan.table_name
                    in {
                        _TRACE_METRIC_DAILY_ROLLUPS_TABLE,
                        _SPAN_COST_DAILY_ROLLUPS_TABLE,
                        _ASSESSMENT_DAILY_ROLLUPS_TABLE,
                        _SESSION_SUMMARY_TABLE,
                    }
                    for scan in populated_scans
                )
                or " LIMIT " in final_sql.upper()
            )
            with self._resources.duckdb_pool.acquire(high_priority=high_priority) as lease:
                execution_start_time = time.perf_counter() if profile else 0.0
                registered_names = []
                try:
                    for name, table in (arrow_tables or {}).items():
                        lease.connection.register(name, table)
                        registered_names.append(name)
                    cursor = lease.connection.execute(final_sql, [*scan_params, *(params or [])])
                    columns = [column[0] for column in cursor.description]
                    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
                    execution_ms = _elapsed_ms(execution_start_time) if profile else None
                finally:
                    for name in registered_names:
                        lease.connection.unregister(name)
            if profile:
                active_scan_profiles = [
                    _scan_profile(scan) for scan in active_scans.values() if scan is not None
                ]
                _logger.info(
                    "Iceberg trace DuckDB query completed: %s",
                    {
                        "elapsed_ms": _elapsed_ms(start_time),
                        "pool_wait_ms": lease.pool_wait_ms,
                        "execution_ms": execution_ms,
                        "high_priority": high_priority,
                        "row_count": len(rows),
                        "scans": active_scan_profiles,
                        "sql_hash": hashlib.sha256(final_sql.encode("utf-8")).hexdigest()[:12],
                    },
                )
            return rows

        return _execute(scans)

    def _latest_trace_rows(self, *, trace_id: str | None = None) -> list[_Row]:
        where = []
        params: _QueryParams = []
        start_time_ms = None
        end_time_ms = None
        if trace_id is not None:
            where.append("trace_id = ?")
            params.append(trace_id)
            if locator := self._archived_trace_locator(trace_id):
                where.append("experiment_id = ?")
                params.append(str(locator["experiment_id"]))
                start_time_ms = locator["request_time_ms"]
                end_time_ms = locator["request_time_ms"]
        workspace = self._get_active_workspace()
        latest_traces_cte = _projected_rows_cte_sql(
            "trace_rows", _TRACE_INDEX_TABLE, alias="latest_traces"
        )
        return self._run_duckdb_query(
            trace_rows=self._iceberg_scan(
                _TRACE_INDEX_TABLE,
                workspace=workspace,
                day_column="request_day",
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                trace_ids=(trace_id,) if trace_id is not None else None,
            ),
            sql=f"""
                WITH {latest_traces_cte}
                SELECT *
                FROM latest_traces
                {"WHERE " + " AND ".join(where) if where else ""}
            """,
            params=params,
        )

    def _trace_ids_with_locator_clause(
        self,
        trace_ids: list[str],
        *,
        table_alias: str | None = None,
        day_column: str = "request_day",
        locators_by_trace_id: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[str, _QueryParams]:
        if not trace_ids:
            return "1 = 0", []
        if locators_by_trace_id is None:
            locators_by_trace_id = (
                self.get_archived_trace_locators(trace_ids) if self._hybrid_enabled else {}
            )
        clauses = []
        params: _QueryParams = []
        remaining_trace_ids = []
        trace_id_column = _qualify("trace_id", table_alias)
        experiment_column = _qualify("experiment_id", table_alias)
        request_day_column = _qualify(day_column, table_alias)
        for trace_id in trace_ids:
            if locator := locators_by_trace_id.get(trace_id):
                clauses.append(
                    f"({trace_id_column} = ? AND {experiment_column} = ? "
                    f"AND {request_day_column} = ?)"
                )
                params.extend([trace_id, str(locator["experiment_id"]), locator["request_day"]])
            else:
                remaining_trace_ids.append(trace_id)
        if remaining_trace_ids:
            clauses.append(f"{trace_id_column} IN ({','.join('?' for _ in remaining_trace_ids)})")
            params.extend(remaining_trace_ids)
        return "(" + " OR ".join(clauses) + ")", params

    def _latest_trace_rows_by_trace_id(self, trace_ids: list[str]) -> dict[str, _Row]:
        if not trace_ids:
            return {}
        workspace = self._get_active_workspace()
        locators_by_trace_id = (
            self.get_archived_trace_locators(trace_ids) if self._hybrid_enabled else {}
        )
        trace_filter_sql, trace_filter_params = self._trace_ids_with_locator_clause(
            trace_ids, locators_by_trace_id=locators_by_trace_id
        )
        locator_timestamps = [
            locator["request_time_ms"] for locator in locators_by_trace_id.values()
        ]
        experiment_ids = tuple(
            sorted({str(locator["experiment_id"]) for locator in locators_by_trace_id.values()})
        )
        hydration_columns = {*_TRACE_DETAIL_TRACE_COLUMNS, "request_day"}
        latest_traces_cte = _projected_rows_cte_sql(
            "trace_rows",
            _TRACE_INDEX_TABLE,
            alias="latest_traces",
            columns=hydration_columns,
        )
        rows = self._run_duckdb_query(
            trace_rows=self._iceberg_scan(
                _TRACE_INDEX_TABLE,
                projected_columns=hydration_columns,
                workspace=workspace,
                experiment_ids=experiment_ids or None,
                day_column="request_day" if locator_timestamps else None,
                start_time_ms=min(locator_timestamps) if locator_timestamps else None,
                end_time_ms=max(locator_timestamps) if locator_timestamps else None,
                trace_ids=tuple(trace_ids),
            ),
            sql=f"""
                WITH {latest_traces_cte}
                SELECT *
                FROM latest_traces
                WHERE {trace_filter_sql}
            """,
            params=trace_filter_params,
        )
        return {row["trace_id"]: row for row in rows}

    def _latest_span_rows(
        self,
        *,
        trace_id: str | None = None,
        start_time_ms: int | None = None,
        end_time_ms: int | None = None,
    ) -> list[_Row]:
        where = []
        params: _QueryParams = []
        if trace_id is not None:
            where.append("trace_id = ?")
            params.append(trace_id)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        workspace = self._get_active_workspace()
        span_rows_scan = self._iceberg_scan(
            _SPAN_INDEX_TABLE,
            workspace=workspace,
            day_column="span_start_day",
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            trace_ids=(trace_id,) if trace_id is not None else None,
        )
        return self._run_duckdb_query(
            span_rows=span_rows_scan,
            sql=f"""
                WITH {
                _projected_rows_cte_sql(
                    "span_rows",
                    _SPAN_INDEX_TABLE,
                    alias="latest_spans",
                )
            }
                SELECT *
                FROM latest_spans
                {where_sql}
            """,
            params=params,
        )

    def _latest_assessment_rows(
        self, *, trace_id: str | None = None, assessment_id: str | None = None
    ) -> list[_Row]:
        where = []
        params: _QueryParams = []
        if trace_id is not None:
            where.append("trace_id = ?")
            params.append(trace_id)
        if assessment_id is not None:
            where.append("assessment_id = ?")
            params.append(assessment_id)
        workspace = self._get_active_workspace()
        latest_assessments_cte = _projected_rows_cte_sql(
            "assessment_rows", _ASSESSMENT_INDEX_TABLE, alias="latest_assessments"
        )
        return self._run_duckdb_query(
            assessment_rows=self._iceberg_scan(
                _ASSESSMENT_INDEX_TABLE,
                workspace=workspace,
                trace_ids=(trace_id,) if trace_id is not None else None,
            ),
            sql=f"""
                WITH {latest_assessments_cte}
                SELECT *
                FROM latest_assessments
                {"WHERE " + " AND ".join(where) if where else ""}
            """,
            params=params,
        )

    def _latest_assessments_by_trace_id(
        self,
        trace_ids: list[str],
        *,
        valid_only: bool = False,
        trace_request_time_ms_by_trace_id: dict[str, int | None] | None = None,
        trace_rows_by_id: dict[str, _Row] | None = None,
    ) -> dict[str, list[Assessment]]:
        if not trace_ids:
            return {}
        workspace = self._get_active_workspace()
        trace_request_times = []
        if trace_request_time_ms_by_trace_id is not None:
            trace_request_times = [
                timestamp
                for trace_id in trace_ids
                if (timestamp := trace_request_time_ms_by_trace_id.get(trace_id)) is not None
            ]
            if len(trace_request_times) != len(trace_ids):
                trace_request_times = []
        trace_request_days = sorted({
            day
            for timestamp in trace_request_times
            if (day := _timestamp_ms_to_day(timestamp)) is not None
        })
        experiment_ids = (
            sorted({row["experiment_id"] for row in trace_rows_by_id.values() if row})
            if trace_rows_by_id
            else []
        )
        assessment_columns = {
            "trace_id",
            "trace_request_time_ms",
            "trace_request_day",
            "assessment_json",
        }
        if valid_only:
            assessment_columns.add("valid")
        latest_assessments_cte = _projected_rows_cte_sql(
            "assessment_rows",
            _ASSESSMENT_INDEX_TABLE,
            alias="latest_assessments",
            columns=assessment_columns,
        )
        trace_request_day_clause = (
            "AND trace_request_day IN (" + ",".join("?" for _ in trace_request_days) + ")"
            if trace_request_days
            else ""
        )
        experiment_clause = (
            "AND experiment_id IN (" + ",".join("?" for _ in experiment_ids) + ")"
            if experiment_ids
            else ""
        )
        valid_clause = "AND valid = true" if valid_only else ""
        rows = self._run_duckdb_query(
            assessment_rows=self._iceberg_scan(
                _ASSESSMENT_INDEX_TABLE,
                projected_columns=assessment_columns,
                workspace=workspace,
                experiment_ids=tuple(experiment_ids) or None,
                day_column="trace_request_day" if trace_request_times else None,
                start_time_ms=min(trace_request_times) if trace_request_times else None,
                end_time_ms=max(trace_request_times) if trace_request_times else None,
                trace_ids=tuple(trace_ids),
            ),
            sql=f"""
                WITH {latest_assessments_cte}
                SELECT *
                FROM latest_assessments
                WHERE trace_id IN ({",".join("?" for _ in trace_ids)})
                    {experiment_clause}
                    {trace_request_day_clause}
                    {valid_clause}
            """,
            params=[*trace_ids, *experiment_ids, *trace_request_days],
        )
        assessments_by_trace_id: dict[str, list[Assessment]] = {
            trace_id: [] for trace_id in trace_ids
        }
        for row in rows:
            trace_row = trace_rows_by_id and trace_rows_by_id.get(row["trace_id"])
            if trace_row is None and trace_request_time_ms_by_trace_id is not None:
                trace_row = {
                    "request_time_ms": trace_request_time_ms_by_trace_id.get(row["trace_id"])
                }
            if trace_row is not None and not self._assessment_row_matches_trace_row(row, trace_row):
                continue
            assessments_by_trace_id.setdefault(row["trace_id"], []).append(
                self._assessment_row_to_entity(row)
            )
        return assessments_by_trace_id

    def _assessments_for_trace_rows(
        self, trace_rows_by_id: dict[str, _Row]
    ) -> dict[str, list[Assessment]]:
        return self._latest_assessments_by_trace_id(
            list(trace_rows_by_id),
            trace_request_time_ms_by_trace_id={
                trace_id: trace_row["request_time_ms"]
                for trace_id, trace_row in trace_rows_by_id.items()
            },
            trace_rows_by_id=trace_rows_by_id,
        )

    def _latest_trace_tag_rows(self, *, trace_id: str | None = None) -> list[_Row]:
        where = []
        params: _QueryParams = []
        if trace_id is not None:
            where.append("trace_id = ?")
            params.append(trace_id)
        workspace = self._get_active_workspace()
        latest_trace_tags_cte = _projected_rows_cte_sql(
            "trace_tag_rows", _TRACE_TAG_INDEX_TABLE, alias="latest_trace_tags"
        )
        return self._run_duckdb_query(
            trace_tag_rows=self._iceberg_scan(
                _TRACE_TAG_INDEX_TABLE,
                workspace=workspace,
                trace_ids=(trace_id,) if trace_id is not None else None,
            ),
            sql=f"""
                WITH {latest_trace_tags_cte}
                SELECT *
                FROM latest_trace_tags
                {"WHERE " + " AND ".join(where) if where else ""}
            """,
            params=params,
        )

    @staticmethod
    def _trace_row_is_archive_backed(trace_row: _Row | None) -> bool:
        if trace_row is None:
            return False
        tags_json = trace_row.get("tags_json")
        if not tags_json:
            return False
        try:
            tags = json.loads(tags_json)
        except (TypeError, ValueError):
            return False
        return tags.get(TraceTagKey.SPANS_LOCATION) == SpansLocation.ARCHIVE_REPO.value

    @staticmethod
    def _assessment_row_matches_trace_row(assessment_row: _Row, trace_row: _Row | None) -> bool:
        if not _IcebergTraceStoreBase._trace_row_is_archive_backed(trace_row):
            return True
        return trace_row is not None and (
            assessment_row.get("trace_request_time_ms") == trace_row.get("request_time_ms")
        )

    @staticmethod
    def _span_row_matches_trace_row(span_row: _Row, trace_row: _Row | None) -> bool:
        if not _IcebergTraceStoreBase._trace_row_is_archive_backed(trace_row):
            return True
        if trace_row is None:
            return False
        start_time_ns = span_row.get("start_time_ns")
        request_time_ms = trace_row.get("request_time_ms")
        if start_time_ns is None or request_time_ms is None:
            return False
        execution_duration_ms = trace_row.get("execution_duration_ms") or 0
        start_time_ms = start_time_ns // 1_000_000
        return request_time_ms <= start_time_ms <= request_time_ms + execution_duration_ms

    def _latest_span_rows_by_trace_id(
        self, trace_ids: list[str], *, trace_rows_by_id: dict[str, _Row] | None = None
    ) -> dict[str, list[_Row]]:
        if not trace_ids:
            return {}
        workspace = self._get_active_workspace()
        where_clauses = [f"trace_id IN ({','.join('?' for _ in trace_ids)})"]
        params: _QueryParams = list(trace_ids)
        if trace_rows_by_id:
            experiment_ids = sorted({
                row["experiment_id"] for row in trace_rows_by_id.values() if row
            })
            if experiment_ids:
                where_clauses.append(f"experiment_id IN ({','.join('?' for _ in experiment_ids)})")
                params.extend(experiment_ids)
        projected_columns = {
            "trace_id",
            "experiment_id",
            "span_id",
            "start_time_ns",
            "span_start_day",
            "span_json",
        }
        latest_spans_cte = _projected_rows_cte_sql(
            "span_rows",
            _SPAN_INDEX_TABLE,
            alias="latest_spans",
            columns=projected_columns,
        )
        rows = self._run_duckdb_query(
            span_rows=self._iceberg_scan(
                _SPAN_INDEX_TABLE,
                projected_columns=projected_columns,
                workspace=workspace,
                trace_ids=tuple(trace_ids),
            ),
            sql=f"""
                WITH {latest_spans_cte}
                SELECT *
                FROM latest_spans
                WHERE {" AND ".join(where_clauses)}
            """,
            params=params,
        )
        spans_by_trace_id: dict[str, list[_Row]] = {trace_id: [] for trace_id in trace_ids}
        for row in rows:
            if trace_rows_by_id is not None and not self._span_row_matches_trace_row(
                row, trace_rows_by_id.get(row["trace_id"])
            ):
                continue
            spans_by_trace_id.setdefault(row["trace_id"], []).append(row)
        return spans_by_trace_id

    def _artifact_location(self, trace_id: str, experiment_id: str) -> str:
        experiment = self.tracking_store.get_experiment(experiment_id)
        return self.tracking_store._get_trace_artifact_location_tag(experiment, trace_id).value

    def _assessment_row_to_entity(self, row: _Row) -> Assessment:
        return Assessment.from_dictionary(json.loads(row["assessment_json"]))

    def _trace_row_to_entity(self, row: _Row, assessments: list[Assessment]) -> TraceInfo:
        return TraceInfo(
            trace_id=row["trace_id"],
            trace_location=trace_location.TraceLocation.from_experiment_id(row["experiment_id"]),
            request_time=row["request_time_ms"],
            execution_duration=row["execution_duration_ms"],
            state=TraceState(row["state"]),
            client_request_id=row["client_request_id"],
            request_preview=row["request_preview"],
            response_preview=row["response_preview"],
            trace_metadata=json.loads(row["metadata_json"]) if row["metadata_json"] else {},
            tags=json.loads(row["tags_json"]) if row["tags_json"] else {},
            assessments=assessments,
        )

    def _trace_detail_rows(self, trace_id: str) -> list[_Row]:
        workspace = self._get_active_workspace()
        latest_traces_cte = _projected_rows_cte_sql(
            "trace_rows",
            _TRACE_INDEX_TABLE,
            alias="latest_traces",
            columns=_TRACE_DETAIL_TRACE_COLUMNS,
        )
        latest_spans_cte = _projected_rows_cte_sql(
            "span_rows",
            _SPAN_INDEX_TABLE,
            alias="latest_spans",
            columns={"trace_id", "span_id", "start_time_ns", "span_json"},
        )
        trace_select_columns = ", ".join(
            f"t.{_sql_identifier(column)}" for column in _TRACE_DETAIL_TRACE_COLUMNS
        )
        null_trace_columns = ", ".join(
            f"CAST(NULL AS VARCHAR) AS {_sql_identifier(column)}"
            if column
            in {
                "trace_id",
                "experiment_id",
                "state",
                "client_request_id",
                "request_preview",
                "response_preview",
                "tags_json",
                "metadata_json",
            }
            else f"CAST(NULL AS BIGINT) AS {_sql_identifier(column)}"
            for column in _TRACE_DETAIL_TRACE_COLUMNS
        )
        return self._run_duckdb_query(
            trace_rows=self._iceberg_scan(
                _TRACE_INDEX_TABLE,
                workspace=workspace,
                trace_ids=(trace_id,),
            ),
            span_rows=self._iceberg_scan(
                _SPAN_INDEX_TABLE,
                workspace=workspace,
                trace_ids=(trace_id,),
            ),
            sql=f"""
                WITH {latest_traces_cte},
                {latest_spans_cte},
                selected_trace AS (
                    SELECT {trace_select_columns}
                    FROM latest_traces AS t
                    WHERE t.trace_id = ?
                )
                SELECT
                    'trace' AS row_type,
                    {trace_select_columns},
                    CAST(NULL AS BIGINT) AS span_start_time_ns,
                    CAST(NULL AS VARCHAR) AS span_id,
                    CAST(NULL AS VARCHAR) AS span_json
                FROM selected_trace AS t
                UNION ALL
                SELECT
                    'span' AS row_type,
                    {null_trace_columns},
                    s.start_time_ns AS span_start_time_ns,
                    s.span_id,
                    s.span_json
                FROM latest_spans AS s
                JOIN selected_trace AS t
                    ON {_span_matches_trace_generation_sql(span_alias="s", trace_alias="t")}
            """,
            params=[trace_id],
        )

    def _append_rows_with_conflict_retry(
        self,
        *,
        rows: list[_Row],
        schema: Schema,
        load_table,
        table_name: str,
        delete_expr=None,
    ) -> _LiveIcebergTableState | None:
        del table_name
        if not rows and delete_expr is None:
            return None

        arrow_table = pa.Table.from_pylist(rows, schema=_arrow_schema(schema)) if rows else None
        delay_seconds = _ICEBERG_APPEND_INITIAL_BACKOFF_SECONDS
        for attempt in range(1, _ICEBERG_APPEND_MAX_RETRIES + 1):
            try:
                table = load_table()
                if delete_expr is None:
                    table.append(arrow_table)
                else:
                    with table.transaction() as transaction:
                        with warnings.catch_warnings():
                            warnings.filterwarnings(
                                "ignore",
                                message="Delete operation did not match any records",
                            )
                            transaction.delete(delete_expr)
                        if arrow_table is not None and len(rows) > 0:
                            transaction.append(arrow_table)
                snapshot = table.current_snapshot()
                return _LiveIcebergTableState(
                    metadata_location=table.metadata_location,
                    snapshot_id=snapshot.snapshot_id if snapshot else None,
                )
            except CommitFailedException:
                if attempt == _ICEBERG_APPEND_MAX_RETRIES:
                    raise
                # Avoid thundering-herd retries when multiple replicas race the same commit.
                time.sleep(delay_seconds * random.uniform(0.5, 1.5))
                delay_seconds = min(delay_seconds * 2, _ICEBERG_APPEND_MAX_BACKOFF_SECONDS)

    @staticmethod
    def _and_expressions(*expressions):
        expressions = [expression for expression in expressions if expression is not None]
        while len(expressions) > 1:
            expressions = [
                And(expressions[index], expressions[index + 1])
                if index + 1 < len(expressions)
                else expressions[index]
                for index in range(0, len(expressions), 2)
            ]
        return expressions[0] if expressions else None

    @staticmethod
    def _or_expressions(*expressions):
        expressions = [expression for expression in expressions if expression is not None]
        while len(expressions) > 1:
            expressions = [
                Or(expressions[index], expressions[index + 1])
                if index + 1 < len(expressions)
                else expressions[index]
                for index in range(0, len(expressions), 2)
            ]
        return expressions[0] if expressions else None

    def _workspace_trace_ids_expr(self, trace_ids: list[str] | tuple[str, ...]):
        if not trace_ids:
            return None
        return self._and_expressions(
            EqualTo("workspace", self._get_active_workspace()),
            In("trace_id", trace_ids),
        )

    def _workspace_assessment_ids_expr(self, assessment_ids: list[str] | tuple[str, ...]):
        if not assessment_ids:
            return None
        return self._and_expressions(
            EqualTo("workspace", self._get_active_workspace()),
            In("assessment_id", assessment_ids),
        )

    def _session_summary_keys_expr(self, keys: list[tuple[str, str]] | tuple[tuple[str, str], ...]):
        if not keys:
            return None
        return self._and_expressions(
            EqualTo("workspace", self._get_active_workspace()),
            self._or_expressions(
                *(
                    self._and_expressions(
                        EqualTo("experiment_id", experiment_id),
                        EqualTo("session_id", session_id),
                    )
                    for experiment_id, session_id in keys
                )
            ),
        )

    def _rollup_partition_expr(self, partition_keys: set[tuple[str, date]]):
        if not partition_keys:
            return None
        return self._and_expressions(
            EqualTo("workspace", self._get_active_workspace()),
            self._or_expressions(
                *(
                    self._and_expressions(
                        EqualTo("experiment_id", experiment_id),
                        EqualTo("rollup_day", rollup_day),
                    )
                    for experiment_id, rollup_day in sorted(partition_keys)
                )
            ),
        )

    @staticmethod
    def _trace_rollup_partition_keys(trace_rows: list[_Row]) -> set[tuple[str, date]]:
        return {
            (row["experiment_id"], row["request_day"])
            for row in trace_rows
            if row.get("request_day") is not None
        }

    @staticmethod
    def _span_rollup_partition_keys(span_rows: list[_Row]) -> set[tuple[str, date]]:
        return {
            (row["experiment_id"], row["span_start_day"])
            for row in span_rows
            if row.get("span_start_day") is not None
        }

    @staticmethod
    def _assessment_rollup_partition_keys(trace_rows: list[_Row]) -> set[tuple[str, date]]:
        return {
            (row["experiment_id"], row["request_day"])
            for row in trace_rows
            if row.get("request_day") is not None
        }

    def _replace_trace_metric_rollup_rows(
        self, partition_keys: set[tuple[str, date]], rows: list[_Row]
    ) -> _LiveIcebergTableState | None:
        return self._append_rows_with_conflict_retry(
            rows=rows,
            schema=_TRACE_METRIC_DAILY_ROLLUPS_SCHEMA,
            load_table=self._trace_metric_rollup_table,
            table_name=_TRACE_METRIC_DAILY_ROLLUPS_TABLE,
            delete_expr=self._rollup_partition_expr(partition_keys),
        )

    def _replace_span_cost_rollup_rows(
        self, partition_keys: set[tuple[str, date]], rows: list[_Row]
    ) -> _LiveIcebergTableState | None:
        return self._append_rows_with_conflict_retry(
            rows=rows,
            schema=_SPAN_COST_DAILY_ROLLUPS_SCHEMA,
            load_table=self._span_cost_rollup_table,
            table_name=_SPAN_COST_DAILY_ROLLUPS_TABLE,
            delete_expr=self._rollup_partition_expr(partition_keys),
        )

    def _replace_assessment_rollup_rows(
        self, partition_keys: set[tuple[str, date]], rows: list[_Row]
    ) -> _LiveIcebergTableState | None:
        return self._append_rows_with_conflict_retry(
            rows=rows,
            schema=_ASSESSMENT_DAILY_ROLLUPS_SCHEMA,
            load_table=self._assessment_rollup_table,
            table_name=_ASSESSMENT_DAILY_ROLLUPS_TABLE,
            delete_expr=self._rollup_partition_expr(partition_keys),
        )

    def _clear_rollup_rows(
        self,
        *,
        trace_partition_keys: set[tuple[str, date]],
        span_partition_keys: set[tuple[str, date]],
        assessment_partition_keys: set[tuple[str, date]],
    ) -> None:
        self._replace_trace_metric_rollup_rows(trace_partition_keys, [])
        self._replace_span_cost_rollup_rows(span_partition_keys, [])
        self._replace_assessment_rollup_rows(assessment_partition_keys, [])

    def _replace_span_rows_for_trace_ids(self, trace_ids: list[str], rows: list[_Row]) -> None:
        self._append_rows_with_conflict_retry(
            rows=rows,
            schema=_SPAN_INDEX_SCHEMA,
            load_table=self._span_table,
            table_name=_SPAN_INDEX_TABLE,
            delete_expr=self._workspace_trace_ids_expr(trace_ids),
        )

    def _delete_iceberg_rows_for_trace_ids(
        self,
        trace_ids: list[str],
        *,
        trace_partition_keys: set[tuple[str, date]] | None = None,
        span_partition_keys: set[tuple[str, date]] | None = None,
        assessment_partition_keys: set[tuple[str, date]] | None = None,
    ) -> None:
        delete_expr = self._workspace_trace_ids_expr(trace_ids)
        if delete_expr is None:
            return
        trace_rows_by_id = self._latest_trace_rows_by_trace_id(trace_ids)
        affected_session_keys = {
            (row["experiment_id"], row["session_id"])
            for row in trace_rows_by_id.values()
            if row.get("session_id") is not None
        }
        span_rows_by_trace_id = self._latest_span_rows_by_trace_id(
            trace_ids, trace_rows_by_id=trace_rows_by_id
        )
        if trace_partition_keys is None:
            trace_partition_keys = self._trace_rollup_partition_keys(
                list(trace_rows_by_id.values())
            )
        if span_partition_keys is None:
            span_partition_keys = self._span_rollup_partition_keys([
                row for rows in span_rows_by_trace_id.values() for row in rows
            ])
        if assessment_partition_keys is None:
            assessment_partition_keys = self._assessment_rollup_partition_keys(
                list(trace_rows_by_id.values())
            )
        self._clear_rollup_rows(
            trace_partition_keys=trace_partition_keys,
            span_partition_keys=span_partition_keys,
            assessment_partition_keys=assessment_partition_keys,
        )
        self._append_rows_with_conflict_retry(
            rows=[],
            schema=_TRACE_INDEX_SCHEMA,
            load_table=self._trace_table,
            table_name=_TRACE_INDEX_TABLE,
            delete_expr=delete_expr,
        )
        self._append_rows_with_conflict_retry(
            rows=[],
            schema=_TRACE_TAG_INDEX_SCHEMA,
            load_table=self._trace_tag_table,
            table_name=_TRACE_TAG_INDEX_TABLE,
            delete_expr=delete_expr,
        )
        self._append_rows_with_conflict_retry(
            rows=[],
            schema=_SPAN_INDEX_SCHEMA,
            load_table=self._span_table,
            table_name=_SPAN_INDEX_TABLE,
            delete_expr=delete_expr,
        )
        self._append_rows_with_conflict_retry(
            rows=[],
            schema=_ASSESSMENT_INDEX_SCHEMA,
            load_table=self._assessment_table,
            table_name=_ASSESSMENT_INDEX_TABLE,
            delete_expr=delete_expr,
        )
        self._rebuild_session_summaries(affected_session_keys)
        self._refresh_rollup_tables(
            trace_partition_keys=trace_partition_keys,
            span_partition_keys=span_partition_keys,
            assessment_partition_keys=assessment_partition_keys,
        )

    def _append_trace_rows(self, rows: list[_Row]) -> _LiveIcebergTableState | None:
        return self._append_rows_with_conflict_retry(
            rows=rows,
            schema=_TRACE_INDEX_SCHEMA,
            load_table=self._trace_table,
            table_name=_TRACE_INDEX_TABLE,
        )

    def _append_trace_row(self, row: _Row) -> _LiveIcebergTableState | None:
        return self._append_trace_rows([row])

    def _append_trace_tag_rows(self, rows: list[_Row]) -> _LiveIcebergTableState | None:
        return self._append_rows_with_conflict_retry(
            rows=rows,
            schema=_TRACE_TAG_INDEX_SCHEMA,
            load_table=self._trace_tag_table,
            table_name=_TRACE_TAG_INDEX_TABLE,
        )

    def _append_span_rows(self, rows: list[_Row]) -> _LiveIcebergTableState | None:
        return self._append_rows_with_conflict_retry(
            rows=rows,
            schema=_SPAN_INDEX_SCHEMA,
            load_table=self._span_table,
            table_name=_SPAN_INDEX_TABLE,
        )

    def _append_assessment_rows(self, rows: list[_Row]) -> _LiveIcebergTableState | None:
        return self._append_rows_with_conflict_retry(
            rows=rows,
            schema=_ASSESSMENT_INDEX_SCHEMA,
            load_table=self._assessment_table,
            table_name=_ASSESSMENT_INDEX_TABLE,
        )

    def _upsert_session_summaries(self, trace_rows: list[_Row]) -> _LiveIcebergTableState | None:
        grouped: dict[tuple[str, str], _Row] = {}
        for row in trace_rows:
            if not (session_id := row.get("session_id")):
                continue
            key = (row["experiment_id"], session_id)
            request_time_ms = row["request_time_ms"]
            if key not in grouped:
                grouped[key] = {
                    "workspace": self._get_active_workspace(),
                    "experiment_id": row["experiment_id"],
                    "session_id": session_id,
                    "first_trace_timestamp_ms": request_time_ms,
                    "last_trace_timestamp_ms": request_time_ms,
                }
            else:
                grouped[key]["first_trace_timestamp_ms"] = min(
                    grouped[key]["first_trace_timestamp_ms"], request_time_ms
                )
                grouped[key]["last_trace_timestamp_ms"] = max(
                    grouped[key]["last_trace_timestamp_ms"], request_time_ms
                )
        if not grouped:
            return None

        session_ids = tuple(sorted({session_id for _, session_id in grouped}))
        experiment_ids = tuple(sorted({experiment_id for experiment_id, _ in grouped}))
        existing_rows = self._run_duckdb_query(
            trace_rows=self._iceberg_scan(
                _SESSION_SUMMARY_TABLE,
                workspace=self._get_active_workspace(),
                experiment_ids=experiment_ids,
                published_metadata=False,
            ),
            sql=f"""
                SELECT *
                FROM trace_rows
                WHERE workspace = ?
                    AND experiment_id IN ({",".join("?" for _ in experiment_ids)})
                    AND session_id IN ({",".join("?" for _ in session_ids)})
            """,
            params=[self._get_active_workspace(), *experiment_ids, *session_ids],
        )
        for row in existing_rows:
            key = (row["experiment_id"], row["session_id"])
            if key in grouped:
                grouped[key]["first_trace_timestamp_ms"] = min(
                    grouped[key]["first_trace_timestamp_ms"],
                    row["first_trace_timestamp_ms"],
                )
                grouped[key]["last_trace_timestamp_ms"] = max(
                    grouped[key]["last_trace_timestamp_ms"],
                    row["last_trace_timestamp_ms"],
                )

        return self._append_rows_with_conflict_retry(
            rows=list(grouped.values()),
            schema=_SESSION_SUMMARY_SCHEMA,
            load_table=self._session_summary_table,
            table_name=_SESSION_SUMMARY_TABLE,
            delete_expr=self._session_summary_keys_expr(tuple(sorted(grouped))),
        )

    def _rebuild_session_summaries(
        self, session_keys: set[tuple[str, str]]
    ) -> _LiveIcebergTableState | None:
        if not session_keys:
            return None
        ordered_session_keys = tuple(sorted(session_keys))
        key_clauses = " OR ".join(
            "(experiment_id = ? AND session_id = ?)" for _ in ordered_session_keys
        )
        rows = self._run_duckdb_query(
            trace_rows=self._iceberg_scan(
                _TRACE_INDEX_TABLE,
                projected_columns={
                    "workspace",
                    "experiment_id",
                    "session_id",
                    "request_time_ms",
                },
                workspace=self._get_active_workspace(),
                published_metadata=False,
            ),
            sql=f"""
                SELECT
                    workspace,
                    experiment_id,
                    session_id,
                    MIN(request_time_ms) AS first_trace_timestamp_ms,
                    MAX(request_time_ms) AS last_trace_timestamp_ms
                FROM trace_rows
                WHERE workspace = ?
                    AND ({key_clauses})
                GROUP BY workspace, experiment_id, session_id
            """,
            params=[
                self._get_active_workspace(),
                *(value for key in ordered_session_keys for value in key),
            ],
        )
        return self._append_rows_with_conflict_retry(
            rows=rows,
            schema=_SESSION_SUMMARY_SCHEMA,
            load_table=self._session_summary_table,
            table_name=_SESSION_SUMMARY_TABLE,
            delete_expr=self._session_summary_keys_expr(ordered_session_keys),
        )

    def _rebuild_trace_metric_rollups(
        self, partition_keys: set[tuple[str, date]]
    ) -> _LiveIcebergTableState | None:
        partition_keys = {
            (experiment_id, day) for experiment_id, day in partition_keys if day is not None
        }
        if not partition_keys:
            return None

        workspace = self._get_active_workspace()
        start_time_ms, end_time_ms = _partition_keys_time_bounds(partition_keys)
        partition_where, partition_params = _experiment_day_partition_where_sql(
            "experiment_id", "request_day", partition_keys
        )
        trace_rows_scan = self._iceberg_scan(
            _TRACE_INDEX_TABLE,
            workspace=workspace,
            day_column="request_day",
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            published_metadata=False,
        )
        metric_values = [
            f"({_sql_string_literal(TraceMetricKey.TRACE_COUNT)}, trace_count, "
            "NULL, NULL, NULL, NULL, NULL, NULL)"
        ]
        for metric_name, value_column in _TRACE_ROLLUP_VALUE_COLUMNS.items():
            metric_values.append(
                f"({_sql_string_literal(metric_name)}, {value_column}_count, "
                f"{value_column}_sum, {value_column}_min, {value_column}_max, "
                f"{value_column}_p50, {value_column}_p90, {value_column}_p99)"
            )
        value_aggregations = []
        for value_column in _TRACE_ROLLUP_VALUE_COLUMNS.values():
            value_aggregations.extend([
                f"CAST(COUNT({value_column}) AS BIGINT) AS {value_column}_count",
                f"SUM({value_column}) AS {value_column}_sum",
                f"MIN({value_column}) AS {value_column}_min",
                f"MAX({value_column}) AS {value_column}_max",
                f"quantile_cont({value_column}, 0.5) AS {value_column}_p50",
                f"quantile_cont({value_column}, 0.9) AS {value_column}_p90",
                f"quantile_cont({value_column}, 0.99) AS {value_column}_p99",
            ])
        rows = self._run_duckdb_query(
            trace_rows=trace_rows_scan,
            sql=f"""
                WITH grouped_rollups AS (
                    SELECT
                        workspace,
                        experiment_id,
                        request_day,
                        CASE
                            WHEN GROUPING(state) = 1
                            THEN {_sql_string_literal(_ROLLUP_GROUP_GLOBAL)}
                            ELSE {_sql_string_literal(_ROLLUP_GROUP_STATUS)}
                        END AS grouping_set,
                        CASE WHEN GROUPING(state) = 0 THEN state END AS trace_status,
                        CAST(COUNT(*) AS BIGINT) AS trace_count,
                        {", ".join(value_aggregations)}
                    FROM trace_rows
                    WHERE workspace = ?
                        AND {partition_where}
                    GROUP BY GROUPING SETS (
                        (workspace, experiment_id, request_day),
                        (workspace, experiment_id, request_day, state)
                    )
                    HAVING (GROUPING(state) = 1 OR state IS NOT NULL)
                )
                SELECT
                    g.workspace,
                    g.experiment_id,
                    g.request_day AS rollup_day,
                    m.metric_name,
                    g.grouping_set,
                    g.trace_status,
                    m.sample_count,
                    m.sum_value,
                    m.min_value,
                    m.max_value,
                    m.p50_value,
                    m.p90_value,
                    m.p99_value
                FROM grouped_rollups AS g
                CROSS JOIN LATERAL (
                    VALUES {", ".join(metric_values)}
                ) AS m(
                    metric_name,
                    sample_count,
                    sum_value,
                    min_value,
                    max_value,
                    p50_value,
                    p90_value,
                    p99_value
                )
                WHERE m.sample_count > 0
            """,
            params=[workspace, *partition_params],
        )
        rows.extend(
            _rollup_coverage_rows(
                partition_keys,
                workspace=workspace,
                dimension_columns=("trace_status",),
                percentile_columns=True,
            )
        )
        return self._replace_trace_metric_rollup_rows(partition_keys, rows)

    def _rebuild_span_cost_rollups(
        self, partition_keys: set[tuple[str, date]]
    ) -> _LiveIcebergTableState | None:
        partition_keys = {
            (experiment_id, day) for experiment_id, day in partition_keys if day is not None
        }
        if not partition_keys:
            return None

        workspace = self._get_active_workspace()
        start_time_ms, end_time_ms = _partition_keys_time_bounds(partition_keys)
        partition_where, partition_params = _experiment_day_partition_where_sql(
            "experiment_id", "span_start_day", partition_keys
        )
        span_rows_scan = self._iceberg_scan(
            _SPAN_INDEX_TABLE,
            workspace=workspace,
            day_column="span_start_day",
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            published_metadata=False,
        )
        metric_values = []
        value_aggregations = []
        for metric_name, value_column in _SPAN_COST_ROLLUP_VALUE_COLUMNS.items():
            metric_values.append(
                f"({_sql_string_literal(metric_name)}, {value_column}_count, "
                f"{value_column}_sum, {value_column}_min, {value_column}_max)"
            )
            value_aggregations.extend([
                f"CAST(COUNT({value_column}) AS BIGINT) AS {value_column}_count",
                f"SUM({value_column}) AS {value_column}_sum",
                f"MIN({value_column}) AS {value_column}_min",
                f"MAX({value_column}) AS {value_column}_max",
            ])
        rows = self._run_duckdb_query(
            span_rows=span_rows_scan,
            sql=f"""
                WITH grouped_rollups AS (
                    SELECT
                        workspace,
                        experiment_id,
                        span_start_day,
                        CASE
                            WHEN GROUPING(model_name) = 1
                                AND GROUPING(model_provider) = 1
                                THEN {_sql_string_literal(_ROLLUP_GROUP_GLOBAL)}
                            WHEN GROUPING(model_name) = 0
                                AND GROUPING(model_provider) = 1
                                THEN {_sql_string_literal(_ROLLUP_GROUP_MODEL)}
                            WHEN GROUPING(model_name) = 1
                                AND GROUPING(model_provider) = 0
                                THEN {_sql_string_literal(_ROLLUP_GROUP_PROVIDER)}
                            ELSE {_sql_string_literal(_ROLLUP_GROUP_MODEL_PROVIDER)}
                        END AS grouping_set,
                        CASE WHEN GROUPING(model_name) = 0 THEN model_name END AS model_name,
                        CASE
                            WHEN GROUPING(model_provider) = 0 THEN model_provider
                        END AS model_provider,
                        {", ".join(value_aggregations)}
                    FROM span_rows
                    WHERE workspace = ?
                        AND {partition_where}
                    GROUP BY GROUPING SETS (
                        (workspace, experiment_id, span_start_day),
                        (workspace, experiment_id, span_start_day, model_name),
                        (workspace, experiment_id, span_start_day, model_provider),
                        (workspace, experiment_id, span_start_day, model_name, model_provider)
                    )
                    HAVING (GROUPING(model_name) = 1 OR model_name IS NOT NULL)
                        AND (GROUPING(model_provider) = 1 OR model_provider IS NOT NULL)
                )
                SELECT
                    g.workspace,
                    g.experiment_id,
                    g.span_start_day AS rollup_day,
                    m.metric_name,
                    g.grouping_set,
                    g.model_name,
                    g.model_provider,
                    m.sample_count,
                    m.sum_value,
                    m.min_value,
                    m.max_value
                FROM grouped_rollups AS g
                CROSS JOIN LATERAL (
                    VALUES {", ".join(metric_values)}
                ) AS m(metric_name, sample_count, sum_value, min_value, max_value)
                WHERE m.sample_count > 0
            """,
            params=[workspace, *partition_params],
        )
        rows.extend(
            _rollup_coverage_rows(
                partition_keys,
                workspace=workspace,
                dimension_columns=("model_name", "model_provider"),
            )
        )
        return self._replace_span_cost_rollup_rows(partition_keys, rows)

    def _rebuild_assessment_rollups(
        self,
        partition_keys: set[tuple[str, date]],
    ) -> _LiveIcebergTableState | None:
        partition_keys = {
            (experiment_id, day) for experiment_id, day in partition_keys if day is not None
        }
        if not partition_keys:
            return None

        workspace = self._get_active_workspace()
        start_time_ms, end_time_ms = _partition_keys_time_bounds(partition_keys)
        partition_where, partition_params = _experiment_day_partition_where_sql(
            "experiment_id", "trace_request_day", partition_keys
        )
        assessment_rows_scan = self._iceberg_scan(
            _ASSESSMENT_INDEX_TABLE,
            workspace=workspace,
            day_column="trace_request_day",
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            published_metadata=False,
        )
        rows = self._run_duckdb_query(
            assessment_rows=assessment_rows_scan,
            sql=f"""
                WITH grouped_rollups AS (
                    SELECT
                        workspace,
                        experiment_id,
                        trace_request_day,
                        CAST(COUNT(*) AS BIGINT) AS assessment_count,
                        CAST(COUNT(aggregate_value) AS BIGINT) AS aggregate_count,
                        SUM(aggregate_value) AS aggregate_sum,
                        MIN(aggregate_value) AS aggregate_min,
                        MAX(aggregate_value) AS aggregate_max
                    FROM assessment_rows
                    WHERE workspace = ?
                        AND {partition_where}
                    GROUP BY workspace, experiment_id, trace_request_day
                )
                SELECT
                    g.workspace,
                    g.experiment_id,
                    g.trace_request_day AS rollup_day,
                    m.metric_name,
                    {_sql_string_literal(_ROLLUP_GROUP_GLOBAL)} AS grouping_set,
                    m.sample_count,
                    m.sum_value,
                    m.min_value,
                    m.max_value
                FROM grouped_rollups AS g
                CROSS JOIN LATERAL (
                    VALUES
                        (
                            {_sql_string_literal(AssessmentMetricKey.ASSESSMENT_COUNT)},
                            g.assessment_count,
                            CAST(NULL AS DOUBLE),
                            CAST(NULL AS DOUBLE),
                            CAST(NULL AS DOUBLE)
                        ),
                        (
                            {_sql_string_literal(AssessmentMetricKey.ASSESSMENT_VALUE)},
                            g.aggregate_count,
                            CAST(g.aggregate_sum AS DOUBLE),
                            CAST(g.aggregate_min AS DOUBLE),
                            CAST(g.aggregate_max AS DOUBLE)
                        )
                ) AS m(metric_name, sample_count, sum_value, min_value, max_value)
                WHERE m.sample_count > 0
            """,
            params=[workspace, *partition_params],
        )
        rows.extend(
            _rollup_coverage_rows(
                partition_keys,
                workspace=workspace,
                dimension_columns=(),
            )
        )
        return self._replace_assessment_rollup_rows(partition_keys, rows)

    def _refresh_rollup_tables(
        self,
        *,
        trace_partition_keys: set[tuple[str, date]],
        span_partition_keys: set[tuple[str, date]],
        assessment_partition_keys: set[tuple[str, date]],
    ) -> dict[str, _LiveIcebergTableState]:
        table_states = {}
        for table_name, table_state in [
            (
                _TRACE_METRIC_DAILY_ROLLUPS_TABLE,
                self._rebuild_trace_metric_rollups(trace_partition_keys),
            ),
            (
                _SPAN_COST_DAILY_ROLLUPS_TABLE,
                self._rebuild_span_cost_rollups(span_partition_keys),
            ),
            (
                _ASSESSMENT_DAILY_ROLLUPS_TABLE,
                self._rebuild_assessment_rollups(assessment_partition_keys),
            ),
        ]:
            if table_state is not None:
                table_states[table_name] = table_state
        return table_states

    def _trace_tag_rows(
        self,
        *,
        trace_id: str,
        experiment_id: str,
        request_time_ms: int | None,
        current_tags: dict[str, str],
        previous_tags: dict[str, str] | None = None,
    ) -> list[_Row]:
        del previous_tags
        workspace = self._get_active_workspace()
        return [
            {
                "trace_id": trace_id,
                "experiment_id": experiment_id,
                "request_day": _timestamp_ms_to_day(request_time_ms),
                "tag_key": key,
                "tag_value": value,
                "workspace": workspace,
            }
            for key, value in current_tags.items()
        ]

    def _build_trace_row_from_entity(
        self,
        trace_info: TraceInfo,
        *,
        existing_row: _Row | None = None,
    ) -> _Row:
        existing_tags = json.loads(existing_row["tags_json"]) if existing_row else {}
        existing_metadata = json.loads(existing_row["metadata_json"]) if existing_row else {}
        merged_tags = existing_tags | trace_info.tags
        # Mark traces as store-backed from start_trace() so the async exporter does not race
        # ahead and upload legacy raw trace JSON artifacts before log_spans() lands.
        merged_tags.setdefault(TraceTagKey.SPANS_LOCATION, SpansLocation.TRACKING_STORE.value)
        merged_tags.setdefault(
            "mlflow.artifactLocation",
            self._artifact_location(trace_info.trace_id, trace_info.experiment_id),
        )
        merged_metadata = existing_metadata | trace_info.trace_metadata
        merged_metadata[TraceMetadataKey.TRACE_INFO_FINALIZED] = "true"
        token_usage_metrics = _extract_token_usage_metrics(merged_metadata)
        cost_metrics = _extract_cost_metrics(merged_metadata)
        return {
            "trace_id": trace_info.trace_id,
            "experiment_id": trace_info.experiment_id,
            "request_time_ms": trace_info.request_time,
            "request_day": _timestamp_ms_to_day(trace_info.request_time),
            "execution_duration_ms": trace_info.execution_duration,
            "state": trace_info.state.value,
            "trace_name": merged_tags.get(TraceTagKey.TRACE_NAME),
            "client_request_id": trace_info.client_request_id
            or (existing_row and existing_row["client_request_id"]),
            "request_preview": trace_info.request_preview
            if trace_info.request_preview is not None
            else (existing_row and existing_row["request_preview"]),
            "response_preview": trace_info.response_preview
            if trace_info.response_preview is not None
            else (existing_row and existing_row["response_preview"]),
            "session_id": merged_metadata.get(TraceMetadataKey.TRACE_SESSION),
            "source_run_id": merged_metadata.get(TraceMetadataKey.SOURCE_RUN),
            "trace_user": merged_metadata.get(TraceMetadataKey.TRACE_USER),
            **token_usage_metrics,
            **cost_metrics,
            "tags_json": json.dumps(merged_tags, sort_keys=True),
            "metadata_json": json.dumps(merged_metadata, sort_keys=True),
            "workspace": self._get_active_workspace(),
        }

    def _span_row(
        self,
        span: Span,
        *,
        experiment_id: str,
        span_dict: dict[str, Any] | None = None,
    ) -> _Row:
        attributes = span.attributes
        metric_payload = _span_metric_payload(attributes)
        return {
            "trace_id": span.trace_id,
            "experiment_id": experiment_id,
            "span_id": span.span_id,
            "parent_span_id": span.parent_id,
            "name": span.name,
            "span_type": span.span_type,
            "start_time_ns": span.start_time_ns,
            "span_start_day": _timestamp_ns_to_day(span.start_time_ns),
            "end_time_ns": span.end_time_ns,
            "status": span.status.status_code.value,
            "attributes_json": json.dumps(attributes, sort_keys=True),
            "span_json": json.dumps(
                span_dict if span_dict is not None else span.to_dict(), sort_keys=True
            ),
            "workspace": self._get_active_workspace(),
            **metric_payload,
            "latency_ms": (span.end_time_ns - span.start_time_ns) / 1_000_000.0
            if span.end_time_ns is not None
            else None,
        }

    def _assessment_row(
        self,
        assessment: Assessment,
        *,
        experiment_id: str,
        trace_row: _Row | None = None,
    ) -> _Row:
        value_json, value_text = _assessment_value_payload(assessment)
        aggregate_value = get_assessment_analytics_fields(value_json)["aggregate_value"]
        valid = assessment.valid if assessment.valid is not None else True
        return {
            "assessment_id": assessment.assessment_id,
            "trace_id": assessment.trace_id,
            "experiment_id": experiment_id,
            "assessment_name": assessment.name,
            "assessment_type": _assessment_type(assessment),
            "assessment_value_json": value_json,
            "assessment_value_text": value_text,
            "aggregate_value": aggregate_value,
            "create_time_ms": assessment.create_time_ms,
            "assessment_create_day": _timestamp_ms_to_day(assessment.create_time_ms),
            "last_update_time_ms": assessment.last_update_time_ms,
            "rationale": assessment.rationale,
            "run_id": assessment.run_id,
            "span_id": assessment.span_id,
            "source_type": getattr(
                assessment.source.source_type, "value", assessment.source.source_type
            ),
            "source_id": assessment.source.source_id,
            "metadata_json": json.dumps(assessment.metadata or {}, sort_keys=True),
            "overrides": assessment.overrides,
            "valid": valid,
            "assessment_json": json.dumps(assessment.to_dictionary(), sort_keys=True),
            "workspace": self._get_active_workspace(),
            "trace_request_time_ms": trace_row and trace_row["request_time_ms"],
            "trace_request_day": _timestamp_ms_to_day(trace_row and trace_row["request_time_ms"]),
        }

    def _merge_trace_row_from_spans(
        self,
        *,
        trace_id: str,
        experiment_id: str,
        existing_row: _Row | None,
        current_spans: list[Span],
    ) -> _Row:
        existing_tags = json.loads(existing_row["tags_json"]) if existing_row else {}
        existing_metadata = json.loads(existing_row["metadata_json"]) if existing_row else {}
        finalized = existing_metadata.get(TraceMetadataKey.TRACE_INFO_FINALIZED) == "true"
        root_span = next(
            (span for span in current_spans if span.parent_id is None), current_spans[0]
        )

        merged_tags = dict(existing_tags)
        merged_tags.setdefault(TraceTagKey.TRACE_NAME, root_span.name)
        merged_tags[TraceTagKey.SPANS_LOCATION] = SpansLocation.TRACKING_STORE.value
        merged_tags.setdefault(
            "mlflow.artifactLocation", self._artifact_location(trace_id, experiment_id)
        )

        merged_metadata = dict(existing_metadata)
        merged_metadata[TraceMetadataKey.SIZE_STATS] = json.dumps(
            {TraceSizeStatsKey.NUM_SPANS: len(current_spans)}, sort_keys=True
        )

        token_usage_metrics = {
            "input_tokens": existing_row["input_tokens"] if existing_row else None,
            "output_tokens": existing_row["output_tokens"] if existing_row else None,
            "total_tokens": existing_row["total_tokens"] if existing_row else None,
            "cache_read_input_tokens": existing_row["cache_read_input_tokens"]
            if existing_row
            else None,
            "cache_creation_input_tokens": existing_row["cache_creation_input_tokens"]
            if existing_row
            else None,
        }
        cost_metrics = {
            "input_cost": existing_row["input_cost"] if existing_row else None,
            "output_cost": existing_row["output_cost"] if existing_row else None,
            "total_cost": existing_row["total_cost"] if existing_row else None,
        }

        request_time_ms = existing_row["request_time_ms"] if existing_row else None
        execution_duration_ms = existing_row["execution_duration_ms"] if existing_row else None
        state = existing_row["state"] if existing_row else TraceState.STATE_UNSPECIFIED.value
        if not finalized:
            min_start_ns = min(span.start_time_ns for span in current_spans)
            end_candidates = [
                span.end_time_ns if span.end_time_ns is not None else span.start_time_ns
                for span in current_spans
            ]
            request_time_ms = min_start_ns // 1_000_000
            execution_duration_ms = (max(end_candidates) - min_start_ns) // 1_000_000
            if any(span.end_time_ns is None for span in current_spans):
                state = TraceState.IN_PROGRESS.value
            elif root_span.status.status_code.value == "ERROR":
                state = TraceState.ERROR.value
            elif root_span.status.status_code.value == "OK":
                state = TraceState.OK.value
            else:
                state = TraceState.STATE_UNSPECIFIED.value

        return {
            "trace_id": trace_id,
            "experiment_id": experiment_id,
            "request_time_ms": request_time_ms,
            "request_day": _timestamp_ms_to_day(request_time_ms),
            "execution_duration_ms": execution_duration_ms,
            "state": state,
            "trace_name": merged_tags.get(TraceTagKey.TRACE_NAME),
            "client_request_id": existing_row["client_request_id"] if existing_row else None,
            "request_preview": existing_row["request_preview"] if existing_row else None,
            "response_preview": existing_row["response_preview"] if existing_row else None,
            "session_id": merged_metadata.get(TraceMetadataKey.TRACE_SESSION),
            "source_run_id": merged_metadata.get(TraceMetadataKey.SOURCE_RUN),
            "trace_user": merged_metadata.get(TraceMetadataKey.TRACE_USER),
            **token_usage_metrics,
            **cost_metrics,
            "tags_json": json.dumps(merged_tags, sort_keys=True),
            "metadata_json": json.dumps(merged_metadata, sort_keys=True),
            "workspace": self._get_active_workspace(),
        }

    def _require_trace_row(self, trace_id: str) -> _Row:
        rows = self._latest_trace_rows(trace_id=trace_id)
        if not rows:
            raise MlflowException(
                f"Trace with ID {trace_id} is not found.",
                error_code=RESOURCE_DOES_NOT_EXIST,
            )
        return rows[0]

    def _get_trace_info_cold(self, trace_id: str):
        trace_info, _ = self._get_trace_info_and_row_cold(trace_id)
        return trace_info

    def _get_trace_info_and_row_cold(self, trace_id: str) -> tuple[TraceInfo, _Row]:
        trace_row = self._require_trace_row(trace_id)
        assessments = self._assessments_for_trace_rows({trace_id: trace_row}).get(trace_id, [])
        return self._trace_row_to_entity(trace_row, assessments=assessments), trace_row

    def _matches_string_filter(
        self, actual_value: Any, comparator: str, expected_value: Any
    ) -> bool:
        actual_str = None if actual_value is None else str(actual_value)
        if comparator == "IS NULL":
            return actual_value is None
        if comparator == "IS NOT NULL":
            return actual_value is not None
        if actual_str is None:
            return False
        if comparator == "=":
            return actual_str == str(expected_value) or actual_str == json.dumps(expected_value)
        if comparator == "!=":
            return actual_str != str(expected_value) and actual_str != json.dumps(expected_value)
        if comparator in {"IN", "NOT IN"}:
            expected_values = {str(v) for v in expected_value} | {
                json.dumps(v) for v in expected_value
            }
            result = actual_str in expected_values
            return result if comparator == "IN" else not result
        if comparator == "LIKE":
            return _convert_like_pattern_to_regex(str(expected_value)).match(actual_str) is not None
        if comparator == "ILIKE":
            return (
                _convert_like_pattern_to_regex(str(expected_value), flags=re.IGNORECASE).match(
                    actual_str
                )
                is not None
            )
        if comparator == "RLIKE":
            return re.search(str(expected_value), actual_str) is not None
        raise MlflowException.invalid_parameter_value(f"Unsupported comparator {comparator!r}")

    def _span_field_value(self, span_row: _Row, parsed_filter: _Filter) -> Any:
        key = parsed_filter["key"]
        if key == "name":
            return span_row["name"]
        if key == "type":
            return span_row["span_type"]
        if key == "status":
            return span_row["status"]
        if key == "content":
            return span_row["span_json"]
        if key.startswith("attributes."):
            attributes = (
                json.loads(span_row["attributes_json"]) if span_row["attributes_json"] else {}
            )
            return attributes.get(key[len("attributes.") :])
        return None

    def _trace_matches_span_filters(
        self, span_rows: list[_Row], span_filters: list[_Filter]
    ) -> bool:
        for span_row in span_rows:
            if all(
                self._matches_string_filter(
                    self._span_field_value(span_row, parsed_filter),
                    parsed_filter["comparator"],
                    parsed_filter.get("value"),
                )
                for parsed_filter in span_filters
            ):
                return True
        return False

    def _trace_matches_assessment_filters(
        self,
        assessments: list[Assessment],
        session_assessments: list[Assessment],
        assessment_filters: list[_Filter],
    ) -> bool:
        for parsed_filter in assessment_filters:
            direct_scoped = [
                a
                for a in assessments
                if _assessment_type(a) == parsed_filter["type"]
                and a.name == parsed_filter["key"]
                and (a.valid if a.valid is not None else True)
            ]
            session_scoped = [
                a
                for a in session_assessments
                if _assessment_type(a) == parsed_filter["type"]
                and a.name == parsed_filter["key"]
                and (a.valid if a.valid is not None else True)
            ]
            scoped = [*direct_scoped, *session_scoped]
            comparator = parsed_filter["comparator"]
            value = parsed_filter.get("value")
            if comparator == "IS NULL":
                if scoped:
                    return False
                continue
            if comparator == "IS NOT NULL":
                if not scoped:
                    return False
                continue
            if not scoped:
                return False

            matched = False
            for assessment in scoped:
                value_json, value_text = _assessment_value_payload(assessment)
                if comparator in {"=", "!=", "LIKE", "ILIKE", "RLIKE"}:
                    if _assessment_value_matches_string(value_text, comparator, str(value)):
                        matched = True
                        break
                elif comparator in {">", ">=", "<", "<="}:
                    if not isinstance(value, (int, float)):
                        raise MlflowException.invalid_parameter_value(
                            "Expected a numeric value for "
                            f"{parsed_filter['type']}.{parsed_filter['key']}"
                        )
                    numeric_value = get_assessment_analytics_fields(value_json)["aggregate_value"]
                    if _assessment_value_matches_numeric(numeric_value, comparator, value):
                        matched = True
                        break
                else:
                    raise MlflowException.invalid_parameter_value(
                        f"Unsupported assessment comparator {comparator!r}"
                    )
            if not matched:
                return False
        return True

    def _search_trace_rows_by_recent_partitions(
        self,
        *,
        experiment_ids: list[str],
        trace_filters: list[_Filter],
        span_filters: list[_Filter],
        assessment_filters: list[_Filter],
        order_by: list[str],
        max_results: int,
        offset: int,
        workspace: str | None,
    ) -> list[_Row]:
        start_time_ms, end_time_ms = _trace_time_bounds_from_filters(trace_filters)
        if start_time_ms is None or end_time_ms is None:
            return []

        start_day = _timestamp_ms_to_day(start_time_ms)
        end_day = _timestamp_ms_to_day(end_time_ms)
        if start_day is None or end_day is None:
            return []

        target_row_count = offset + max_results + 1
        experiment_id_filter = tuple(experiment_ids) if experiment_ids else None
        rows: list[_Row] = []
        window_end_day = end_day
        while window_end_day >= start_day and len(rows) < target_row_count:
            window_start_day = max(
                start_day,
                window_end_day - timedelta(days=_TRACE_SEARCH_RECENT_PARTITION_BATCH_DAYS - 1),
            )
            window_start_time_ms, _ = _day_bounds_ms(window_start_day)
            _, window_end_time_ms = _day_bounds_ms(window_end_day)
            spec = _TraceSearchSpec(
                experiment_ids=experiment_ids,
                trace_filters=trace_filters,
                span_filters=span_filters,
                assessment_filters=assessment_filters,
                order_by=order_by,
                sql_limit=target_row_count - len(rows),
                workspace=workspace,
                include_trace_tags=False,
            )
            query = _DuckDBTraceSearchCompiler(spec).compile()
            rows.extend(
                self._run_duckdb_query(
                    trace_rows=self._iceberg_scan(
                        _TRACE_INDEX_TABLE,
                        projected_columns=_trace_search_projection_columns(spec),
                        workspace=workspace,
                        experiment_ids=experiment_id_filter,
                        day_column="request_day",
                        start_time_ms=max(start_time_ms, window_start_time_ms),
                        end_time_ms=min(end_time_ms, window_end_time_ms),
                    ),
                    span_rows=(
                        self._iceberg_scan(
                            _SPAN_INDEX_TABLE,
                            projected_columns=_span_search_projection_columns(span_filters),
                            workspace=workspace,
                            experiment_ids=experiment_id_filter,
                            day_column="span_start_day",
                            start_time_ms=max(
                                0,
                                max(start_time_ms, window_start_time_ms)
                                - _SPAN_SEARCH_TRACE_START_TOLERANCE_MS,
                            ),
                        )
                        if span_filters
                        else None
                    ),
                    sql=query.sql,
                    params=query.params,
                )
            )
            window_end_day = window_start_day - timedelta(days=1)
        return rows

    def _search_traces_cold(
        self,
        experiment_ids=None,
        filter_string=None,
        max_results=SEARCH_TRACES_DEFAULT_MAX_RESULTS,
        order_by=None,
        page_token=None,
        model_id=None,
        locations=None,
    ):
        del model_id
        self.tracking_store._validate_max_results_param(max_results)
        offset = SearchTraceUtils.parse_start_offset_from_page_token(page_token)
        experiment_ids = locations or experiment_ids or []
        workspace = self._get_active_workspace()
        experiment_id_filter = tuple(experiment_ids) if experiment_ids else None

        assessment_filters = []
        span_filters = []
        trace_filters = []
        if filter_string:
            for parsed_filter in SearchTraceUtils.parse_search_filter_for_search_traces(
                filter_string
            ):
                if parsed_filter["type"] in {"feedback", "expectation"}:
                    assessment_filters.append(parsed_filter)
                elif parsed_filter["type"] == "span":
                    span_filters.append(parsed_filter)
                else:
                    trace_filters.append(parsed_filter)

        sql_limit = offset + max_results + 1
        order_by = order_by or []
        if _can_search_traces_by_recent_partitions(
            trace_filters=trace_filters,
            assessment_filters=assessment_filters,
            span_filters=span_filters,
            order_by=order_by,
        ):
            rows = self._search_trace_rows_by_recent_partitions(
                experiment_ids=experiment_ids,
                trace_filters=trace_filters,
                span_filters=span_filters,
                assessment_filters=assessment_filters,
                order_by=order_by,
                max_results=max_results,
                offset=offset,
                workspace=workspace,
            )
        else:
            start_time_ms, end_time_ms = _trace_time_bounds_from_filters(trace_filters)
            spec = _TraceSearchSpec(
                experiment_ids=experiment_ids,
                trace_filters=trace_filters,
                span_filters=span_filters,
                assessment_filters=assessment_filters,
                order_by=order_by,
                sql_limit=sql_limit,
                workspace=workspace,
                include_trace_tags=_trace_search_references_arbitrary_tags(trace_filters, order_by),
                include_session_id=bool(assessment_filters),
            )
            trace_rows_scan = self._iceberg_scan(
                _TRACE_INDEX_TABLE,
                projected_columns=_trace_search_projection_columns(spec),
                workspace=workspace,
                experiment_ids=experiment_id_filter,
                day_column="request_day",
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
            query = _DuckDBTraceSearchCompiler(spec).compile()
            trace_tag_rows = (
                self._iceberg_scan(
                    _TRACE_TAG_INDEX_TABLE,
                    projected_columns={"trace_id", "tag_key", "tag_value"},
                    workspace=workspace,
                    experiment_ids=experiment_id_filter,
                    day_column="request_day",
                    start_time_ms=start_time_ms,
                    end_time_ms=end_time_ms,
                )
                if query.requires_trace_tags
                else None
            )
            span_rows_scan = (
                self._iceberg_scan(
                    _SPAN_INDEX_TABLE,
                    projected_columns=_span_search_projection_columns(span_filters),
                    workspace=workspace,
                    experiment_ids=experiment_id_filter,
                    day_column="span_start_day" if start_time_ms is not None else None,
                    start_time_ms=(
                        max(0, start_time_ms - _SPAN_SEARCH_TRACE_START_TOLERANCE_MS)
                        if start_time_ms is not None
                        else None
                    ),
                )
                if query.requires_spans
                else None
            )
            assessment_rows_scan = (
                self._iceberg_scan(
                    _ASSESSMENT_INDEX_TABLE,
                    projected_columns=_assessment_search_projection_columns(assessment_filters),
                    workspace=workspace,
                    experiment_ids=experiment_id_filter,
                    day_column="trace_request_day",
                    start_time_ms=start_time_ms,
                    end_time_ms=end_time_ms,
                )
                if query.requires_assessments
                else None
            )
            rows = self._run_duckdb_query(
                trace_rows=trace_rows_scan,
                trace_tag_rows=trace_tag_rows,
                span_rows=span_rows_scan,
                assessment_rows=assessment_rows_scan,
                sql=query.sql,
                params=query.params,
            )
        page_trace_ids = [row["trace_id"] for row in rows[offset : offset + max_results]]
        candidate_rows_by_id = {
            row["trace_id"]: {
                **row,
                "tags_json": json.dumps({
                    TraceTagKey.SPANS_LOCATION: SpansLocation.ARCHIVE_REPO.value
                }),
            }
            for row in rows[offset : offset + max_results]
        }
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            hydrated_rows_future = _submit_with_current_context(
                executor, self._latest_trace_rows_by_trace_id, page_trace_ids
            )
            assessments_future = _submit_with_current_context(
                executor, self._assessments_for_trace_rows, candidate_rows_by_id
            )
            hydrated_rows_by_id = hydrated_rows_future.result()
            assessments_by_trace_id = assessments_future.result()
        page_rows = [hydrated_rows_by_id[trace_id] for trace_id in page_trace_ids]
        trace_infos = [
            self._trace_row_to_entity(
                row, assessments=assessments_by_trace_id.get(row["trace_id"], [])
            )
            for row in page_rows
        ]
        next_token = (
            SearchTraceUtils.create_page_token(offset + max_results)
            if len(rows) > offset + max_results
            else None
        )
        return trace_infos, next_token

    def _compile_span_metric_filter(self, filter_string: str) -> tuple[str, _QueryParams]:
        parsed = SearchTraceMetricsUtils.parse_search_filter(filter_string)
        expression = None
        if parsed.view_type == "trace":
            if parsed.entity == "status":
                expression = "t.state"
            elif parsed.entity == "tag" and parsed.key:
                expression = _tag_scalar_expr(parsed.key, trace_alias="t")
            elif parsed.entity == "metadata" and parsed.key:
                expression = {
                    TraceMetadataKey.SOURCE_RUN: "t.source_run_id",
                    TraceMetadataKey.TRACE_SESSION: "t.session_id",
                    TraceMetadataKey.TRACE_USER: "t.trace_user",
                }.get(parsed.key, _json_string_expr("metadata_json", parsed.key, "t"))
        elif parsed.view_type == "span":
            expression = {
                "name": "s.name",
                "status": "s.status",
                "type": "s.span_type",
                "model": "s.model_name",
                "model_provider": "s.model_provider",
            }.get(parsed.entity)
        if expression is None:
            raise MlflowException.invalid_parameter_value(
                f"Unsupported span metric filter {filter_string!r}"
            )
        if parsed.comparator in {"IS NULL", "IS NOT NULL"}:
            return f"{expression} {parsed.comparator}", []
        return f"{expression} {parsed.comparator} ?", [parsed.value]

    def _compile_assessment_metric_filter(self, filter_string: str) -> tuple[str, _QueryParams]:
        parsed = SearchTraceMetricsUtils.parse_search_filter(filter_string)
        expression = None
        if parsed.view_type == "trace":
            if parsed.entity == "status":
                expression = "t.state"
            elif parsed.entity == "tag" and parsed.key:
                expression = _tag_scalar_expr(parsed.key, trace_alias="t")
            elif parsed.entity == "metadata" and parsed.key:
                expression = {
                    TraceMetadataKey.SOURCE_RUN: "t.source_run_id",
                    TraceMetadataKey.TRACE_SESSION: "t.session_id",
                    TraceMetadataKey.TRACE_USER: "t.trace_user",
                }.get(parsed.key, _json_string_expr("metadata_json", parsed.key, "t"))
        elif parsed.view_type == "assessment":
            expression = {
                "name": "a.assessment_name",
                "type": "a.assessment_type",
                "value": "a.assessment_value_text",
            }.get(parsed.entity)
        if expression is None:
            raise MlflowException.invalid_parameter_value(
                f"Unsupported assessment metric filter {filter_string!r}"
            )
        if parsed.comparator in {"IS NULL", "IS NOT NULL"}:
            return f"{expression} {parsed.comparator}", []
        return f"{expression} {parsed.comparator} ?", [parsed.value]

    def _time_bucket_clause(
        self, column: str, interval_seconds: int | None
    ) -> tuple[list[str], str | None]:
        if not interval_seconds:
            return [], None
        interval_ms = interval_seconds * 1000
        return [
            f"CAST(floor({column} / {interval_ms}) * {interval_ms} AS BIGINT) AS time_bucket_ms"
        ], "time_bucket_ms"

    def _validate_metric_query_shape(
        self,
        *,
        time_interval_seconds: int | None,
        start_time_ms: int | None,
        end_time_ms: int | None,
    ) -> None:
        if time_interval_seconds is None:
            return
        if time_interval_seconds <= 0:
            raise MlflowException.invalid_parameter_value("time_interval_seconds must be positive.")
        if start_time_ms is None or end_time_ms is None:
            raise MlflowException.invalid_parameter_value(
                "start_time_ms and end_time_ms are required if time_interval_seconds is set."
            )
        if start_time_ms >= end_time_ms:
            raise MlflowException.invalid_parameter_value(
                "start_time_ms must be less than end_time_ms."
            )
        bucket_count = math.ceil((end_time_ms - start_time_ms) / (time_interval_seconds * 1000))
        if bucket_count > _MAX_QUERY_TRACE_METRIC_BUCKETS:
            raise MlflowException.invalid_parameter_value(
                "Metric queries may produce at most "
                f"{_MAX_QUERY_TRACE_METRIC_BUCKETS} time buckets."
            )

    @staticmethod
    def _time_range_matches_daily_rollups(
        *, start_time_ms: int | None, end_time_ms: int | None
    ) -> bool:
        day_ms = 24 * 60 * 60 * 1000
        if start_time_ms is not None and start_time_ms % day_ms != 0:
            return False
        if end_time_ms is not None and (end_time_ms + 1) % day_ms != 0:
            return False
        return True

    @staticmethod
    def _time_interval_matches_daily_rollups(time_interval_seconds: int | None) -> bool:
        return time_interval_seconds is None or time_interval_seconds % (24 * 60 * 60) == 0

    def _parse_trace_rollup_filters(
        self, filters: list[str] | None
    ) -> tuple[set[str], list[str], _QueryParams] | None:
        filter_dimensions: set[str] = set()
        where_clauses = []
        params: _QueryParams = []
        for filter_string in filters or []:
            parsed = SearchTraceMetricsUtils.parse_search_filter(filter_string)
            dimension = _TRACE_ROLLUP_FILTER_DIMENSIONS.get((
                parsed.view_type,
                parsed.entity,
                parsed.key if parsed.entity == "tag" else None,
            ))
            if dimension is None or parsed.comparator != "=" or dimension in filter_dimensions:
                return None
            filter_dimensions.add(dimension)
            where_clauses.append(f"r.{_TRACE_ROLLUP_DIMENSION_COLUMNS[dimension]} = ?")
            params.append(parsed.value)
        return filter_dimensions, where_clauses, params

    def _parse_span_cost_rollup_filters(
        self, filters: list[str] | None
    ) -> tuple[set[str], list[str], _QueryParams] | None:
        filter_dimensions: set[str] = set()
        where_clauses = []
        params: _QueryParams = []
        for filter_string in filters or []:
            parsed = SearchTraceMetricsUtils.parse_search_filter(filter_string)
            dimension = _SPAN_COST_ROLLUP_FILTER_DIMENSIONS.get((
                parsed.view_type,
                parsed.entity,
                None,
            ))
            if dimension is None or parsed.comparator != "=" or dimension in filter_dimensions:
                return None
            filter_dimensions.add(dimension)
            where_clauses.append(f"r.{_SPAN_COST_ROLLUP_DIMENSION_COLUMNS[dimension]} = ?")
            params.append(parsed.value)
        return filter_dimensions, where_clauses, params

    def _compile_persisted_rollup_aggregations(
        self,
        metric_name: str,
        aggregations,
        *,
        allow_percentiles: bool,
    ) -> list[str]:
        count_like_metrics = {
            TraceMetricKey.TRACE_COUNT,
            AssessmentMetricKey.ASSESSMENT_COUNT,
        }
        expressions = []
        for aggregation in aggregations:
            alias = str(aggregation)
            if aggregation.aggregation_type == AggregationType.COUNT:
                expr = "SUM(r.sample_count)"
            elif aggregation.aggregation_type == AggregationType.SUM:
                expr = (
                    "SUM(r.sample_count)"
                    if metric_name in count_like_metrics
                    else "SUM(r.sum_value)"
                )
            elif aggregation.aggregation_type == AggregationType.AVG:
                expr = (
                    "AVG(r.sample_count)"
                    if metric_name in count_like_metrics
                    else "SUM(r.sum_value) / NULLIF(SUM(r.sample_count), 0)"
                )
            elif aggregation.aggregation_type == AggregationType.MIN:
                expr = (
                    "MIN(r.sample_count)"
                    if metric_name in count_like_metrics
                    else "MIN(r.min_value)"
                )
            elif aggregation.aggregation_type == AggregationType.MAX:
                expr = (
                    "MAX(r.sample_count)"
                    if metric_name in count_like_metrics
                    else "MAX(r.max_value)"
                )
            elif aggregation.aggregation_type == AggregationType.PERCENTILE and allow_percentiles:
                percentile_column = _ROLLUP_STORED_PERCENTILES.get(aggregation.percentile_value)
                if percentile_column is None:
                    raise MlflowException.invalid_parameter_value(
                        f"Unsupported percentile rollup {aggregation.percentile_value!r}."
                    )
                expr = f"MIN(r.{percentile_column})"
            else:
                raise MlflowException.invalid_parameter_value(
                    f"Unsupported rollup aggregation {aggregation} for {metric_name}."
                )
            expressions.append(f"{expr} AS {_sql_identifier(alias)}")
        return expressions

    def _compile_direct_rollup_aggregations(self, metric_name: str, aggregations) -> list[str]:
        count_like_metrics = {
            TraceMetricKey.TRACE_COUNT,
            AssessmentMetricKey.ASSESSMENT_COUNT,
        }
        expressions = []
        for aggregation in aggregations:
            alias = str(aggregation)
            if aggregation.aggregation_type == AggregationType.COUNT:
                expr = "CAST(r.sample_count AS DOUBLE)"
            elif aggregation.aggregation_type == AggregationType.SUM:
                expr = (
                    "CAST(r.sample_count AS DOUBLE)"
                    if metric_name in count_like_metrics
                    else "r.sum_value"
                )
            elif aggregation.aggregation_type == AggregationType.AVG:
                expr = (
                    "CAST(r.sample_count AS DOUBLE)"
                    if metric_name in count_like_metrics
                    else "r.sum_value / NULLIF(r.sample_count, 0)"
                )
            elif aggregation.aggregation_type == AggregationType.MIN:
                expr = (
                    "CAST(r.sample_count AS DOUBLE)"
                    if metric_name in count_like_metrics
                    else "r.min_value"
                )
            elif aggregation.aggregation_type == AggregationType.MAX:
                expr = (
                    "CAST(r.sample_count AS DOUBLE)"
                    if metric_name in count_like_metrics
                    else "r.max_value"
                )
            elif aggregation.aggregation_type == AggregationType.PERCENTILE:
                percentile_column = _ROLLUP_STORED_PERCENTILES.get(aggregation.percentile_value)
                if percentile_column is None:
                    raise MlflowException.invalid_parameter_value(
                        f"Unsupported percentile rollup {aggregation.percentile_value!r}."
                    )
                # Daily percentile rollups are exact only for the stored daily bucket itself; they
                # must be projected directly from a single persisted row, not re-aggregated.
                expr = f"r.{percentile_column}"
            else:
                raise MlflowException.invalid_parameter_value(
                    f"Unsupported rollup aggregation {aggregation} for {metric_name}."
                )
            expressions.append(f"{expr} AS {_sql_identifier(alias)}")
        return expressions

    def _rollup_rows_to_metric_points(
        self,
        *,
        rows: list[_Row],
        metric_name: str,
        aggregations,
        dimension_columns: list[tuple[str, str]],
        reject_duplicate_keys: bool = False,
    ) -> PagedList[MetricDataPoint] | None:
        data_points = []
        point_keys = set()
        for row in rows:
            if any(row.get(dimension) is None for dimension, _ in dimension_columns):
                continue
            dims = {
                dimension: row[dimension]
                for dimension, _ in dimension_columns
                if row.get(dimension) is not None
            }
            if row.get("time_bucket_ms") is not None:
                dims["time_bucket"] = datetime.fromtimestamp(
                    row["time_bucket_ms"] / 1000, tz=timezone.utc
                ).isoformat()
            values = {str(aggregation): row[str(aggregation)] for aggregation in aggregations}
            if all(value is None for value in values.values()):
                continue
            point_key = tuple(sorted(dims.items()))
            if reject_duplicate_keys and point_key in point_keys:
                return None
            point_keys.add(point_key)
            data_points.append(
                MetricDataPoint(metric_name=metric_name, dimensions=dims, values=values)
            )
        return PagedList(data_points, None)

    def _rollup_range_is_covered(
        self,
        *,
        table_name: str,
        experiment_ids: list[str] | tuple[str, ...] | None,
        start_time_ms: int | None,
        end_time_ms: int | None,
    ) -> bool:
        if not experiment_ids or start_time_ms is None or end_time_ms is None:
            return False
        start_day = _timestamp_ms_to_day(start_time_ms)
        end_day = _timestamp_ms_to_day(end_time_ms)
        if start_day is None or end_day is None or start_day > end_day:
            return False
        workspace = self._get_active_workspace()
        rollup_scan = self._iceberg_scan(
            table_name,
            projected_columns={"workspace", "experiment_id", "rollup_day", "metric_name"},
            workspace=workspace,
            experiment_ids=tuple(experiment_ids),
            day_column="rollup_day",
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )
        cache_key = (
            table_name,
            rollup_scan.metadata_location,
            workspace,
            tuple(experiment_ids),
            start_day,
            end_day,
        )
        with self._resources.rollup_coverage_cache_lock:
            if cache_key in self._resources.rollup_coverage_cache:
                return self._resources.rollup_coverage_cache[cache_key]

        rows = self._run_duckdb_query(
            trace_rows=rollup_scan,
            sql=f"""
                SELECT count(DISTINCT experiment_id) AS coverage_count
                FROM trace_rows
                WHERE workspace = ?
                    AND experiment_id IN ({",".join("?" for _ in experiment_ids)})
                    AND rollup_day >= ?
                    AND rollup_day <= ?
                    AND metric_name = ?
            """,
            params=[
                workspace,
                *experiment_ids,
                start_day,
                end_day,
                _ROLLUP_COVERAGE_METRIC,
            ],
        )
        # Coverage rows are published atomically with the corresponding raw source cut. A sparse
        # request may include calendar days with no source rows, so requiring one sentinel for
        # every requested day incorrectly disables rollups for otherwise fully covered archives.
        covered = rows[0]["coverage_count"] == len(set(experiment_ids))
        with self._resources.rollup_coverage_cache_lock:
            if len(self._resources.rollup_coverage_cache) >= 256:
                self._resources.rollup_coverage_cache.pop(
                    next(iter(self._resources.rollup_coverage_cache))
                )
            self._resources.rollup_coverage_cache[cache_key] = covered
        return covered

    @staticmethod
    def _partial_daily_rollup_ranges(
        start_time_ms: int | None,
        end_time_ms: int | None,
    ) -> tuple[tuple[int, int] | None, list[tuple[int, int]]]:
        if start_time_ms is None or end_time_ms is None or start_time_ms > end_time_ms:
            return None, []
        start_day = _timestamp_ms_to_day(start_time_ms)
        end_day = _timestamp_ms_to_day(end_time_ms)
        if start_day is None or end_day is None:
            return None, []
        start_day_start, start_day_end = _day_bounds_ms(start_day)
        end_day_start, end_day_end = _day_bounds_ms(end_day)
        if start_day == end_day:
            if start_time_ms == start_day_start and end_time_ms == end_day_end:
                return (start_time_ms, end_time_ms), []
            return None, [(start_time_ms, end_time_ms)]

        raw_ranges = []
        interior_start = start_time_ms
        if start_time_ms != start_day_start:
            raw_ranges.append((start_time_ms, start_day_end))
            interior_start = start_day_end + 1
        interior_end = end_time_ms
        if end_time_ms != end_day_end:
            raw_ranges.append((end_day_start, end_time_ms))
            interior_end = end_day_start - 1
        interior_range = (interior_start, interior_end) if interior_start <= interior_end else None
        return interior_range, raw_ranges

    def _query_partial_daily_rollups(
        self,
        *,
        rollup_query,
        raw_query,
        query_kwargs: dict[str, Any],
        aggregations: list[MetricAggregation],
        time_interval_seconds: int | None,
        start_time_ms: int | None,
        end_time_ms: int | None,
        max_results: int,
    ) -> PagedList[MetricDataPoint] | None:
        interior_range, raw_ranges = self._partial_daily_rollup_ranges(
            start_time_ms,
            end_time_ms,
        )
        if interior_range is None or not raw_ranges:
            return None
        if not self._time_interval_matches_daily_rollups(time_interval_seconds):
            return None
        if (
            any(
                aggregation.aggregation_type == AggregationType.PERCENTILE
                for aggregation in aggregations
            )
            and time_interval_seconds != 24 * 60 * 60
        ):
            return None

        internal_aggregations = self._avg_merge_aggregations(aggregations)
        internal_kwargs = {
            **query_kwargs,
            "aggregations": internal_aggregations,
            "max_results": MAX_RESULTS_QUERY_TRACE_METRICS,
        }
        interior_points = rollup_query(
            **internal_kwargs,
            start_time_ms=interior_range[0],
            end_time_ms=interior_range[1],
        )
        if interior_points is None:
            return None
        merged_points = list(interior_points)
        for raw_start_time_ms, raw_end_time_ms in raw_ranges:
            boundary_points = raw_query(
                **internal_kwargs,
                start_time_ms=raw_start_time_ms,
                end_time_ms=raw_end_time_ms,
            )
            merged_points = self._merge_metric_points(merged_points, list(boundary_points))
        merged_points = self._strip_helper_metric_values(merged_points, aggregations)
        merged_points = [
            point
            for point in merged_points
            if any(value is not None for value in point.values.values())
        ]
        return PagedList(self._sort_and_limit_metric_points(merged_points, max_results), None)

    def _query_trace_metric_rollups(
        self,
        *,
        experiment_ids,
        metric_name: str,
        aggregations,
        dimensions=None,
        filters=None,
        time_interval_seconds=None,
        start_time_ms=None,
        end_time_ms=None,
        max_results=MAX_RESULTS_QUERY_TRACE_METRICS,
    ) -> PagedList[MetricDataPoint] | None:
        if not self._hybrid_enabled or not self._iceberg_table_has_snapshot(
            _TRACE_METRIC_DAILY_ROLLUPS_TABLE
        ):
            return None
        if metric_name not in {TraceMetricKey.TRACE_COUNT, *_TRACE_ROLLUP_VALUE_COLUMNS}:
            return None
        if dimensions and any(
            dimension not in _TRACE_ROLLUP_DIMENSION_COLUMNS for dimension in dimensions
        ):
            return None
        if not self._time_interval_matches_daily_rollups(time_interval_seconds):
            return None
        if not self._time_range_matches_daily_rollups(
            start_time_ms=start_time_ms, end_time_ms=end_time_ms
        ):
            return None
        parsed_filters = self._parse_trace_rollup_filters(filters)
        if parsed_filters is None:
            return None
        filter_dimensions, filter_clauses, filter_params = parsed_filters
        grain_dimensions = set(dimensions or []) | filter_dimensions
        grouping_set = (
            _ROLLUP_GROUP_STATUS
            if TraceMetricDimensionKey.TRACE_STATUS in grain_dimensions
            else _ROLLUP_GROUP_GLOBAL
        )
        aggregation_types = {aggregation.aggregation_type for aggregation in aggregations}
        allow_percentiles = AggregationType.PERCENTILE in aggregation_types
        if allow_percentiles:
            if time_interval_seconds != 24 * 60 * 60 or len(experiment_ids or []) != 1:
                return None
            if any(
                aggregation.aggregation_type == AggregationType.PERCENTILE
                and aggregation.percentile_value not in _ROLLUP_STORED_PERCENTILES
                for aggregation in aggregations
            ):
                return None
        elif not aggregation_types <= {
            AggregationType.COUNT,
            AggregationType.SUM,
            AggregationType.AVG,
            AggregationType.MIN,
            AggregationType.MAX,
        }:
            return None
        if (
            metric_name == TraceMetricKey.TRACE_COUNT
            and aggregation_types
            & {
                AggregationType.AVG,
                AggregationType.MIN,
                AggregationType.MAX,
            }
            and len(experiment_ids or []) != 1
        ):
            return None
        if (
            metric_name == TraceMetricKey.TRACE_COUNT
            and grouping_set == _ROLLUP_GROUP_GLOBAL
            and not dimensions
            and not filters
            and time_interval_seconds is None
            and aggregation_types <= {AggregationType.COUNT, AggregationType.SUM}
        ):
            workspace = self._get_active_workspace()
            rows = self._run_duckdb_query(
                trace_rows=self._iceberg_scan(
                    _TRACE_METRIC_DAILY_ROLLUPS_TABLE,
                    projected_columns={
                        "workspace",
                        "experiment_id",
                        "rollup_day",
                        "metric_name",
                        "grouping_set",
                        "sample_count",
                    },
                    workspace=workspace,
                    experiment_ids=tuple(experiment_ids),
                    day_column="rollup_day",
                    start_time_ms=start_time_ms,
                    end_time_ms=end_time_ms,
                ),
                sql=f"""
                    SELECT
                        count(DISTINCT CASE
                            WHEN metric_name = ? THEN experiment_id
                        END) AS coverage_count,
                        SUM(CASE
                            WHEN metric_name = ? AND grouping_set = ? THEN sample_count
                            ELSE 0
                        END) AS trace_count
                    FROM trace_rows
                    WHERE workspace = ?
                        AND experiment_id IN ({",".join("?" for _ in experiment_ids)})
                        AND rollup_day >= ?
                        AND rollup_day <= ?
                """,
                params=[
                    _ROLLUP_COVERAGE_METRIC,
                    TraceMetricKey.TRACE_COUNT,
                    _ROLLUP_GROUP_GLOBAL,
                    workspace,
                    *experiment_ids,
                    _timestamp_ms_to_day(start_time_ms),
                    _timestamp_ms_to_day(end_time_ms),
                ],
            )
            if rows[0]["coverage_count"] != len(set(experiment_ids)):
                return None
            trace_count = float(rows[0]["trace_count"] or 0)
            return PagedList(
                [
                    MetricDataPoint(
                        metric_name=metric_name,
                        dimensions={},
                        values={str(aggregation): trace_count for aggregation in aggregations},
                    )
                ],
                None,
            )
        if not self._rollup_range_is_covered(
            table_name=_TRACE_METRIC_DAILY_ROLLUPS_TABLE,
            experiment_ids=experiment_ids,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        ):
            return None

        where_clauses = ["1 = 1"]
        params: _QueryParams = []
        if experiment_ids:
            where_clauses.append(f"r.experiment_id IN ({','.join('?' for _ in experiment_ids)})")
            params.extend(experiment_ids)
        workspace = self._get_active_workspace()
        if workspace:
            where_clauses.append("r.workspace = ?")
            params.append(workspace)
        if start_time_ms is not None:
            where_clauses.append("r.rollup_day >= ?")
            params.append(_timestamp_ms_to_day(start_time_ms))
        if end_time_ms is not None:
            where_clauses.append("r.rollup_day <= ?")
            params.append(_timestamp_ms_to_day(end_time_ms))
        where_clauses.append("r.metric_name = ?")
        params.append(metric_name)
        where_clauses.append("r.grouping_set = ?")
        params.append(grouping_set)
        where_clauses.extend(filter_clauses)
        params.extend(filter_params)

        dimension_columns = [
            (dimension, f"r.{_TRACE_ROLLUP_DIMENSION_COLUMNS[dimension]}")
            for dimension in dimensions or []
        ]
        time_bucket_selects, time_bucket_group = self._time_bucket_clause(
            "CAST(epoch(CAST(r.rollup_day AS TIMESTAMP)) * 1000 AS BIGINT)",
            time_interval_seconds,
        )
        select_dimensions = _dimension_selects(time_bucket_selects, dimension_columns)
        group_terms = _metric_group_terms(time_bucket_group, dimension_columns)
        group_by = ", ".join(group_terms)
        order_by = ", ".join(group_terms) or "1"
        aggregation_sql = (
            self._compile_direct_rollup_aggregations(metric_name, aggregations)
            if allow_percentiles
            else self._compile_persisted_rollup_aggregations(
                metric_name,
                aggregations,
                allow_percentiles=False,
            )
        )
        rows = self._run_duckdb_query(
            trace_rows=self._iceberg_scan(
                _TRACE_METRIC_DAILY_ROLLUPS_TABLE,
                workspace=workspace,
                day_column="rollup_day",
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            ),
            sql=f"""
                SELECT
                    {", ".join(select_dimensions + aggregation_sql)}
                FROM trace_rows AS r
                WHERE {" AND ".join(where_clauses)}
                {"GROUP BY " + group_by if (group_by and not allow_percentiles) else ""}
                ORDER BY {order_by}
                LIMIT {_validated_limit(max_results, "max_results")}
            """,
            params=params,
        )
        return self._rollup_rows_to_metric_points(
            rows=rows,
            metric_name=metric_name,
            aggregations=aggregations,
            dimension_columns=dimension_columns,
            reject_duplicate_keys=allow_percentiles,
        )

    def _query_span_cost_rollups(
        self,
        *,
        experiment_ids,
        metric_name: str,
        aggregations,
        dimensions=None,
        filters=None,
        time_interval_seconds=None,
        start_time_ms=None,
        end_time_ms=None,
        max_results=MAX_RESULTS_QUERY_TRACE_METRICS,
    ) -> PagedList[MetricDataPoint] | None:
        if (
            not self._hybrid_enabled
            or metric_name not in _SPAN_COST_ROLLUP_VALUE_COLUMNS
            or not self._iceberg_table_has_snapshot(_SPAN_COST_DAILY_ROLLUPS_TABLE)
        ):
            return None
        if dimensions and any(
            dimension not in _SPAN_COST_ROLLUP_DIMENSION_COLUMNS for dimension in dimensions
        ):
            return None
        if not self._time_interval_matches_daily_rollups(time_interval_seconds):
            return None
        if not self._time_range_matches_daily_rollups(
            start_time_ms=start_time_ms, end_time_ms=end_time_ms
        ):
            return None
        if any(
            aggregation.aggregation_type == AggregationType.PERCENTILE
            for aggregation in aggregations
        ):
            return None
        parsed_filters = self._parse_span_cost_rollup_filters(filters)
        if parsed_filters is None:
            return None
        filter_dimensions, filter_clauses, filter_params = parsed_filters
        grain_dimensions = set(dimensions or []) | filter_dimensions
        grouping_set = {
            frozenset(): _ROLLUP_GROUP_GLOBAL,
            frozenset({SpanMetricDimensionKey.SPAN_MODEL_NAME}): _ROLLUP_GROUP_MODEL,
            frozenset({SpanMetricDimensionKey.SPAN_MODEL_PROVIDER}): _ROLLUP_GROUP_PROVIDER,
            frozenset({
                SpanMetricDimensionKey.SPAN_MODEL_NAME,
                SpanMetricDimensionKey.SPAN_MODEL_PROVIDER,
            }): _ROLLUP_GROUP_MODEL_PROVIDER,
        }.get(frozenset(grain_dimensions))
        if grouping_set is None:
            return None
        if not self._rollup_range_is_covered(
            table_name=_SPAN_COST_DAILY_ROLLUPS_TABLE,
            experiment_ids=experiment_ids,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        ):
            return None

        where_clauses = ["1 = 1"]
        params: _QueryParams = []
        if experiment_ids:
            where_clauses.append(f"r.experiment_id IN ({','.join('?' for _ in experiment_ids)})")
            params.extend(experiment_ids)
        workspace = self._get_active_workspace()
        if workspace:
            where_clauses.append("r.workspace = ?")
            params.append(workspace)
        if start_time_ms is not None:
            where_clauses.append("r.rollup_day >= ?")
            params.append(_timestamp_ms_to_day(start_time_ms))
        if end_time_ms is not None:
            where_clauses.append("r.rollup_day <= ?")
            params.append(_timestamp_ms_to_day(end_time_ms))
        where_clauses.append("r.metric_name = ?")
        params.append(metric_name)
        where_clauses.append("r.grouping_set = ?")
        params.append(grouping_set)
        where_clauses.extend(filter_clauses)
        params.extend(filter_params)

        dimension_columns = [
            (dimension, f"r.{_SPAN_COST_ROLLUP_DIMENSION_COLUMNS[dimension]}")
            for dimension in dimensions or []
        ]
        time_bucket_selects, time_bucket_group = self._time_bucket_clause(
            "CAST(epoch(CAST(r.rollup_day AS TIMESTAMP)) * 1000 AS BIGINT)",
            time_interval_seconds,
        )
        select_dimensions = _dimension_selects(time_bucket_selects, dimension_columns)
        group_terms = _metric_group_terms(time_bucket_group, dimension_columns)
        group_by = ", ".join(group_terms)
        order_by = ", ".join(group_terms) or "1"
        rows = self._run_duckdb_query(
            span_rows=self._iceberg_scan(
                _SPAN_COST_DAILY_ROLLUPS_TABLE,
                workspace=workspace,
                day_column="rollup_day",
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            ),
            sql=f"""
                SELECT
                    {
                ", ".join(
                    select_dimensions
                    + self._compile_persisted_rollup_aggregations(
                        metric_name,
                        aggregations,
                        allow_percentiles=False,
                    )
                )
            }
                FROM span_rows AS r
                WHERE {" AND ".join(where_clauses)}
                {"GROUP BY " + group_by if group_by else ""}
                ORDER BY {order_by}
                LIMIT {_validated_limit(max_results, "max_results")}
            """,
            params=params,
        )
        return self._rollup_rows_to_metric_points(
            rows=rows,
            metric_name=metric_name,
            aggregations=aggregations,
            dimension_columns=dimension_columns,
        )

    def _query_assessment_rollups(
        self,
        *,
        experiment_ids,
        metric_name: str,
        aggregations,
        dimensions=None,
        filters=None,
        time_interval_seconds=None,
        start_time_ms=None,
        end_time_ms=None,
        max_results=MAX_RESULTS_QUERY_TRACE_METRICS,
    ) -> PagedList[MetricDataPoint] | None:
        if not self._hybrid_enabled or metric_name not in {
            AssessmentMetricKey.ASSESSMENT_COUNT,
            AssessmentMetricKey.ASSESSMENT_VALUE,
        }:
            return None
        if not self._iceberg_table_has_snapshot(_ASSESSMENT_DAILY_ROLLUPS_TABLE):
            return None
        if dimensions or filters:
            return None
        # Persisted assessment rows are daily, so they can answer unbucketed and
        # native daily-bucket queries without changing trace-time semantics.
        if time_interval_seconds not in {None, 24 * 60 * 60}:
            return None
        if not self._time_range_matches_daily_rollups(
            start_time_ms=start_time_ms, end_time_ms=end_time_ms
        ):
            return None
        if any(
            aggregation.aggregation_type == AggregationType.PERCENTILE
            for aggregation in aggregations
        ):
            return None
        aggregation_types = {aggregation.aggregation_type for aggregation in aggregations}
        if (
            metric_name == AssessmentMetricKey.ASSESSMENT_COUNT
            and aggregation_types
            & {
                AggregationType.AVG,
                AggregationType.MIN,
                AggregationType.MAX,
            }
            and len(experiment_ids or []) != 1
        ):
            return None
        if not self._rollup_range_is_covered(
            table_name=_ASSESSMENT_DAILY_ROLLUPS_TABLE,
            experiment_ids=experiment_ids,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        ):
            return None

        where_clauses = ["1 = 1"]
        params: _QueryParams = []
        if experiment_ids:
            where_clauses.append(f"r.experiment_id IN ({','.join('?' for _ in experiment_ids)})")
            params.extend(experiment_ids)
        workspace = self._get_active_workspace()
        if workspace:
            where_clauses.append("r.workspace = ?")
            params.append(workspace)
        if start_time_ms is not None:
            where_clauses.append("r.rollup_day >= ?")
            params.append(_timestamp_ms_to_day(start_time_ms))
        if end_time_ms is not None:
            where_clauses.append("r.rollup_day <= ?")
            params.append(_timestamp_ms_to_day(end_time_ms))
        where_clauses.append("r.metric_name = ?")
        params.append(metric_name)
        where_clauses.append("r.grouping_set = ?")
        params.append(_ROLLUP_GROUP_GLOBAL)

        dimension_columns = []
        time_bucket_selects, time_bucket_group = self._time_bucket_clause(
            "CAST(epoch(CAST(r.rollup_day AS TIMESTAMP)) * 1000 AS BIGINT)",
            time_interval_seconds,
        )
        select_dimensions = _dimension_selects(time_bucket_selects, dimension_columns)
        group_terms = _metric_group_terms(time_bucket_group, dimension_columns)
        group_by = ", ".join(group_terms)
        order_by = ", ".join(group_terms) or "1"
        rows = self._run_duckdb_query(
            assessment_rows=self._iceberg_scan(
                _ASSESSMENT_DAILY_ROLLUPS_TABLE,
                workspace=workspace,
                day_column="rollup_day",
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            ),
            sql=f"""
                SELECT
                    {
                ", ".join(
                    select_dimensions
                    + self._compile_persisted_rollup_aggregations(
                        metric_name,
                        aggregations,
                        allow_percentiles=False,
                    )
                )
            }
                FROM assessment_rows AS r
                WHERE {" AND ".join(where_clauses)}
                {"GROUP BY " + group_by if group_by else ""}
                ORDER BY {order_by}
                LIMIT {_validated_limit(max_results, "max_results")}
            """,
            params=params,
        )
        return self._rollup_rows_to_metric_points(
            rows=rows,
            metric_name=metric_name,
            aggregations=aggregations,
            dimension_columns=dimension_columns,
        )

    def _compile_metric_aggregations(
        self, aggregations, *, value_expr: str | None, distinct: bool = False
    ):
        expressions = []
        for aggregation in aggregations:
            alias = str(aggregation)
            if aggregation.aggregation_type == AggregationType.COUNT:
                if value_expr is None:
                    expr = "COUNT(*)"
                elif distinct:
                    expr = f"COUNT(DISTINCT {value_expr})"
                else:
                    expr = f"COUNT({value_expr})"
            else:
                if value_expr is None:
                    raise MlflowException(
                        f"Aggregation {aggregation} requires a numeric metric column.",
                        error_code=INVALID_PARAMETER_VALUE,
                    )
                if aggregation.aggregation_type == AggregationType.SUM:
                    expr = f"SUM({value_expr})"
                elif aggregation.aggregation_type == AggregationType.AVG:
                    expr = f"AVG({value_expr})"
                elif aggregation.aggregation_type == AggregationType.MIN:
                    expr = f"MIN({value_expr})"
                elif aggregation.aggregation_type == AggregationType.MAX:
                    expr = f"MAX({value_expr})"
                elif aggregation.aggregation_type == AggregationType.PERCENTILE:
                    expr = f"quantile_cont({value_expr}, {aggregation.percentile_value / 100.0})"
                else:
                    raise MlflowException(
                        f"Unsupported aggregation {aggregation}.",
                        error_code=INVALID_PARAMETER_VALUE,
                    )
            expressions.append(f"{expr} AS {_sql_identifier(alias)}")
        return expressions

    def _historical_trace_rollup_interval_seconds(
        self,
        *,
        metric_name: str,
        aggregations,
        time_interval_seconds: int | None,
        end_time_ms: int | None,
    ) -> int | None:
        if time_interval_seconds is None or end_time_ms is None:
            return None
        if metric_name == TraceMetricKey.SESSION_COUNT:
            return None
        if any(
            aggregation.aggregation_type == AggregationType.PERCENTILE
            for aggregation in aggregations
        ):
            return None
        current_day_start_ms = int(
            datetime
            .now(tz=timezone.utc)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .timestamp()
            * 1000
        )
        if end_time_ms >= current_day_start_ms:
            return None
        if time_interval_seconds >= 7 * 24 * 60 * 60:
            return 7 * 24 * 60 * 60
        if time_interval_seconds >= 24 * 60 * 60:
            return 24 * 60 * 60
        return None

    def _compile_trace_metric_rollup_aggregations(
        self, metric_name: str, aggregations
    ) -> list[str]:
        expressions = []
        for aggregation in aggregations:
            alias = str(aggregation)
            if metric_name == TraceMetricKey.TRACE_COUNT:
                if aggregation.aggregation_type in {AggregationType.COUNT, AggregationType.SUM}:
                    expr = "SUM(r.rollup_count)"
                elif aggregation.aggregation_type == AggregationType.AVG:
                    expr = "AVG(r.rollup_count)"
                elif aggregation.aggregation_type == AggregationType.MIN:
                    expr = "MIN(r.rollup_count)"
                elif aggregation.aggregation_type == AggregationType.MAX:
                    expr = "MAX(r.rollup_count)"
                else:
                    raise MlflowException.invalid_parameter_value(
                        f"Unsupported rollup aggregation {aggregation} for {metric_name}."
                    )
            else:
                if aggregation.aggregation_type == AggregationType.COUNT:
                    expr = "SUM(r.rollup_count)"
                elif aggregation.aggregation_type == AggregationType.SUM:
                    expr = "SUM(r.rollup_sum)"
                elif aggregation.aggregation_type == AggregationType.AVG:
                    expr = "SUM(r.rollup_sum) / NULLIF(SUM(r.rollup_count), 0)"
                elif aggregation.aggregation_type == AggregationType.MIN:
                    expr = "MIN(r.rollup_min)"
                elif aggregation.aggregation_type == AggregationType.MAX:
                    expr = "MAX(r.rollup_max)"
                else:
                    raise MlflowException.invalid_parameter_value(
                        f"Unsupported rollup aggregation {aggregation} for {metric_name}."
                    )
            expressions.append(f"{expr} AS {_sql_identifier(alias)}")
        return expressions

    def _query_archived_trace_locator_count(
        self,
        *,
        experiment_ids,
        aggregations,
        start_time_ms: int | None,
        end_time_ms: int | None,
    ) -> PagedList[MetricDataPoint]:
        trace_count = self._count_published_archived_traces(
            experiment_ids=experiment_ids,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )
        return PagedList(
            [
                MetricDataPoint(
                    metric_name=TraceMetricKey.TRACE_COUNT,
                    dimensions={},
                    values={str(aggregation): float(trace_count) for aggregation in aggregations},
                )
            ],
            None,
        )

    def _count_published_archived_traces(
        self,
        *,
        experiment_ids,
        start_time_ms: int | None,
        end_time_ms: int | None,
        stop_after: int | None = None,
    ) -> int:
        with self.tracking_store.ManagedSessionMaker() as session:
            query = session.query(SqlArchivedTraceLocator.trace_id).filter(
                SqlArchivedTraceLocator.experiment_id.in_([
                    int(experiment_id) for experiment_id in experiment_ids
                ])
            )
            if workspace := self._get_active_workspace():
                query = query.filter(SqlArchivedTraceLocator.workspace == workspace)
            if start_time_ms is not None:
                query = query.filter(SqlArchivedTraceLocator.request_time_ms >= start_time_ms)
            if end_time_ms is not None:
                query = query.filter(SqlArchivedTraceLocator.request_time_ms <= end_time_ms)
            if (published_cut := self._published_iceberg_cut()) and (
                published_at_ms := published_cut.published_at_ms
            ):
                query = query.filter(SqlArchivedTraceLocator.published_at_ms <= published_at_ms)
            if stop_after is not None:
                query = query.limit(stop_after + 1)
            return session.query(func.count()).select_from(query.subquery()).scalar() or 0

    def _query_trace_metrics_cold(
        self,
        experiment_ids,
        view_type,
        metric_name: str,
        aggregations,
        dimensions=None,
        filters=None,
        time_interval_seconds=None,
        start_time_ms=None,
        end_time_ms=None,
        max_results=MAX_RESULTS_QUERY_TRACE_METRICS,
        page_token=None,
        skip_validation: bool = False,
    ):
        del page_token
        max_results = max_results or MAX_RESULTS_QUERY_TRACE_METRICS
        if not skip_validation:
            validate_query_trace_metrics_params(view_type, metric_name, aggregations, dimensions)
        self._validate_metric_query_shape(
            time_interval_seconds=time_interval_seconds,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )
        if view_type == MetricViewType.ASSESSMENTS:
            rollup_points = self._query_assessment_rollups(
                experiment_ids=experiment_ids,
                metric_name=metric_name,
                aggregations=aggregations,
                dimensions=dimensions,
                filters=filters,
                time_interval_seconds=time_interval_seconds,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                max_results=max_results,
            )
            if rollup_points is not None:
                return rollup_points
            partial_rollup_points = self._query_partial_daily_rollups(
                rollup_query=self._query_assessment_rollups,
                raw_query=self._query_assessment_metrics,
                query_kwargs={
                    "experiment_ids": experiment_ids,
                    "metric_name": metric_name,
                    "dimensions": dimensions,
                    "filters": filters,
                    "time_interval_seconds": time_interval_seconds,
                },
                aggregations=aggregations,
                time_interval_seconds=time_interval_seconds,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                max_results=max_results,
            )
            if partial_rollup_points is not None:
                return partial_rollup_points
            return self._query_assessment_metrics(
                experiment_ids=experiment_ids,
                metric_name=metric_name,
                aggregations=aggregations,
                dimensions=dimensions,
                filters=filters,
                time_interval_seconds=time_interval_seconds,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                max_results=max_results,
            )
        if view_type == MetricViewType.SPANS:
            rollup_points = self._query_span_cost_rollups(
                experiment_ids=experiment_ids,
                metric_name=metric_name,
                aggregations=aggregations,
                dimensions=dimensions,
                filters=filters,
                time_interval_seconds=time_interval_seconds,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                max_results=max_results,
            )
            if rollup_points is not None:
                return rollup_points
            partial_rollup_points = self._query_partial_daily_rollups(
                rollup_query=self._query_span_cost_rollups,
                raw_query=self._query_span_metrics,
                query_kwargs={
                    "experiment_ids": experiment_ids,
                    "metric_name": metric_name,
                    "dimensions": dimensions,
                    "filters": filters,
                    "time_interval_seconds": time_interval_seconds,
                },
                aggregations=aggregations,
                time_interval_seconds=time_interval_seconds,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                max_results=max_results,
            )
            if partial_rollup_points is not None:
                return partial_rollup_points
            return self._query_span_metrics(
                experiment_ids=experiment_ids,
                metric_name=metric_name,
                aggregations=aggregations,
                dimensions=dimensions,
                filters=filters,
                time_interval_seconds=time_interval_seconds,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                max_results=max_results,
            )
        if view_type != MetricViewType.TRACES:
            raise MlflowException(
                "Iceberg trace backend only supports trace, span, and assessment "
                f"metrics, received {view_type}.",
                error_code=INVALID_PARAMETER_VALUE,
            )

        rollup_points = self._query_trace_metric_rollups(
            experiment_ids=experiment_ids,
            metric_name=metric_name,
            aggregations=aggregations,
            dimensions=dimensions,
            filters=filters,
            time_interval_seconds=time_interval_seconds,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            max_results=max_results,
        )
        if rollup_points is not None:
            return rollup_points
        partial_rollup_points = self._query_partial_daily_rollups(
            rollup_query=self._query_trace_metric_rollups,
            raw_query=lambda **kwargs: self._query_trace_metrics_cold(
                view_type=MetricViewType.TRACES,
                skip_validation=True,
                **kwargs,
            ),
            query_kwargs={
                "experiment_ids": experiment_ids,
                "metric_name": metric_name,
                "dimensions": dimensions,
                "filters": filters,
                "time_interval_seconds": time_interval_seconds,
            },
            aggregations=aggregations,
            time_interval_seconds=time_interval_seconds,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            max_results=max_results,
        )
        if partial_rollup_points is not None:
            return partial_rollup_points

        if (
            self._hybrid_enabled
            and metric_name == TraceMetricKey.TRACE_COUNT
            and not dimensions
            and not filters
            and time_interval_seconds is None
            and experiment_ids
            and all(
                aggregation.aggregation_type in {AggregationType.COUNT, AggregationType.SUM}
                for aggregation in aggregations
            )
        ):
            return self._query_archived_trace_locator_count(
                experiment_ids=experiment_ids,
                aggregations=aggregations,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )

        dimension_columns = []
        for dimension in dimensions or []:
            column = {
                TraceMetricDimensionKey.TRACE_STATUS: "state",
                TraceMetricDimensionKey.TRACE_NAME: "trace_name",
            }.get(dimension)
            if column is None:
                raise MlflowException(
                    f"Iceberg trace backend does not support dimension {dimension!r} yet.",
                    error_code=INVALID_PARAMETER_VALUE,
                )
            dimension_columns.append((dimension, column))

        where_clauses = ["1 = 1"]
        params: _QueryParams = []
        if experiment_ids:
            where_clauses.append(f"experiment_id IN ({','.join('?' for _ in experiment_ids)})")
            params.extend(experiment_ids)
        workspace = self._get_active_workspace()
        if workspace:
            where_clauses.append("workspace = ?")
            params.append(workspace)
        if start_time_ms is not None:
            where_clauses.append("request_time_ms >= ?")
            params.append(start_time_ms)
        if end_time_ms is not None:
            where_clauses.append("request_time_ms <= ?")
            params.append(end_time_ms)
        for filter_clause in filters or []:
            for parsed_filter in SearchTraceUtils.parse_search_filter_for_search_traces(
                filter_clause
            ):
                clause, clause_params = _compile_trace_filter(parsed_filter)
                where_clauses.append(clause)
                params.extend(clause_params)

        if metric_name == TraceMetricKey.TRACE_COUNT:
            aggregation_sql = self._compile_metric_aggregations(aggregations, value_expr=None)
        elif metric_name == TraceMetricKey.SESSION_COUNT:
            aggregation_sql = self._compile_metric_aggregations(
                aggregations, value_expr="session_id", distinct=True
            )
        elif metric_name == TraceMetricKey.LATENCY:
            aggregation_sql = self._compile_metric_aggregations(
                aggregations, value_expr="execution_duration_ms"
            )
        elif metric_name in {
            TraceMetricKey.INPUT_TOKENS,
            TraceMetricKey.OUTPUT_TOKENS,
            TraceMetricKey.TOTAL_TOKENS,
            TraceMetricKey.CACHE_READ_INPUT_TOKENS,
            TraceMetricKey.CACHE_CREATION_INPUT_TOKENS,
        }:
            value_expr = {
                TraceMetricKey.INPUT_TOKENS: "input_tokens",
                TraceMetricKey.OUTPUT_TOKENS: "output_tokens",
                TraceMetricKey.TOTAL_TOKENS: "total_tokens",
                TraceMetricKey.CACHE_READ_INPUT_TOKENS: "cache_read_input_tokens",
                TraceMetricKey.CACHE_CREATION_INPUT_TOKENS: "cache_creation_input_tokens",
            }[metric_name]
            aggregation_sql = self._compile_metric_aggregations(aggregations, value_expr=value_expr)
            where_clauses.append(f"{value_expr} IS NOT NULL")
        else:
            raise MlflowException(
                f"Iceberg trace backend does not support metric {metric_name!r} yet.",
                error_code=INVALID_PARAMETER_VALUE,
            )

        time_bucket_selects, time_bucket_group = self._time_bucket_clause(
            "request_time_ms", time_interval_seconds
        )
        select_dimensions = _dimension_selects(time_bucket_selects, dimension_columns)
        group_terms = _metric_group_terms(time_bucket_group, dimension_columns)
        group_by = ", ".join(group_terms)
        order_by = ", ".join(group_terms) or "1"
        include_trace_tags = _filters_reference_trace_tags(filters)
        latest_trace_tags_cte = (
            ", latest_trace_tags AS (SELECT * FROM trace_tag_rows)" if include_trace_tags else ""
        )
        trace_columns: set[str] = {"experiment_id", "request_time_ms", "request_day"}
        trace_columns.update(column for _, column in dimension_columns)
        if metric_value_column := {
            TraceMetricKey.SESSION_COUNT: "session_id",
            TraceMetricKey.LATENCY: "execution_duration_ms",
            TraceMetricKey.INPUT_TOKENS: "input_tokens",
            TraceMetricKey.OUTPUT_TOKENS: "output_tokens",
            TraceMetricKey.TOTAL_TOKENS: "total_tokens",
            TraceMetricKey.CACHE_READ_INPUT_TOKENS: "cache_read_input_tokens",
            TraceMetricKey.CACHE_CREATION_INPUT_TOKENS: "cache_creation_input_tokens",
        }.get(metric_name):
            trace_columns.add(metric_value_column)
        for filter_clause in filters or []:
            for parsed_filter in SearchTraceUtils.parse_search_filter_for_search_traces(
                filter_clause
            ):
                trace_columns.update(_search_trace_filter_columns(parsed_filter))
        trace_rows_scan = self._iceberg_scan(
            _TRACE_INDEX_TABLE,
            projected_columns=trace_columns,
            workspace=workspace,
            experiment_ids=tuple(experiment_ids) if experiment_ids else None,
            day_column="request_day",
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )
        latest_traces_cte = _projected_rows_cte_sql(
            "trace_rows",
            _TRACE_INDEX_TABLE,
            alias="latest_traces",
            columns=trace_columns,
        )
        trace_tag_rows_scan = (
            self._iceberg_scan(
                _TRACE_TAG_INDEX_TABLE,
                workspace=workspace,
                day_column="request_day",
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
            if include_trace_tags
            else None
        )
        if self._historical_trace_rollup_interval_seconds(
            metric_name=metric_name,
            aggregations=aggregations,
            time_interval_seconds=time_interval_seconds,
            end_time_ms=end_time_ms,
        ):
            rollup_cte_prefix = f"{latest_traces_cte}{latest_trace_tags_cte},"
            rollup_dimension_columns = [(name, f"r.{column}") for name, column in dimension_columns]
            rollup_time_bucket_selects, rollup_time_bucket_group = self._time_bucket_clause(
                "r.rollup_time_ms", time_interval_seconds
            )
            rollup_select_dimensions = _dimension_selects(
                rollup_time_bucket_selects, rollup_dimension_columns
            )
            rollup_group_terms = _metric_group_terms(
                rollup_time_bucket_group, rollup_dimension_columns
            )
            rollup_group_by = ", ".join(rollup_group_terms)
            rollup_order_by = ", ".join(rollup_group_terms) or "1"
            rollup_aggregation_sql = self._compile_trace_metric_rollup_aggregations(
                metric_name, aggregations
            )
            daily_group_terms = ["request_day", *(column for _, column in dimension_columns)]
            daily_rollup_exprs = [
                "CAST(epoch(CAST(request_day AS TIMESTAMP)) * 1000 AS BIGINT) AS rollup_time_ms",
                *(f"{column} AS {_sql_identifier(column)}" for _, column in dimension_columns),
            ]
            if metric_name == TraceMetricKey.TRACE_COUNT:
                daily_rollup_exprs.append("COUNT(*) AS rollup_count")
            else:
                daily_rollup_exprs.extend([
                    f"COUNT({metric_value_column}) AS rollup_count",
                    f"SUM({metric_value_column}) AS rollup_sum",
                    f"MIN({metric_value_column}) AS rollup_min",
                    f"MAX({metric_value_column}) AS rollup_max",
                ])
            rows = self._run_duckdb_query(
                trace_rows=trace_rows_scan,
                trace_tag_rows=trace_tag_rows_scan,
                sql=f"""
                    WITH {rollup_cte_prefix}
                    daily_rollups AS (
                        SELECT
                            {", ".join(daily_rollup_exprs)}
                        FROM latest_traces
                        WHERE {" AND ".join(where_clauses)}
                        GROUP BY {", ".join(daily_group_terms)}
                    )
                    SELECT
                        {", ".join(rollup_select_dimensions + rollup_aggregation_sql)}
                    FROM daily_rollups AS r
                    {"GROUP BY " + rollup_group_by if rollup_group_by else ""}
                    ORDER BY {rollup_order_by}
                    LIMIT {_validated_limit(max_results, "max_results")}
                """,
                params=params,
            )
        else:
            rows = self._run_duckdb_query(
                trace_rows=trace_rows_scan,
                trace_tag_rows=trace_tag_rows_scan,
                sql=f"""
                    WITH {latest_traces_cte}
                    {latest_trace_tags_cte}
                    SELECT
                        {", ".join(select_dimensions + aggregation_sql)}
                    FROM latest_traces
                    WHERE {" AND ".join(where_clauses)}
                    {"GROUP BY " + group_by if group_by else ""}
                    ORDER BY {order_by}
                    LIMIT {_validated_limit(max_results, "max_results")}
                """,
                params=params,
            )
        data_points = []
        for row in rows:
            dims = {
                dimension: row[dimension]
                for dimension, _ in dimension_columns
                if row.get(dimension) is not None
            }
            if row.get("time_bucket_ms") is not None:
                dims["time_bucket"] = datetime.fromtimestamp(
                    row["time_bucket_ms"] / 1000, tz=timezone.utc
                ).isoformat()
            values = {str(aggregation): row[str(aggregation)] for aggregation in aggregations}
            if all(value is None for value in values.values()):
                continue
            data_points.append(
                MetricDataPoint(metric_name=metric_name, dimensions=dims, values=values)
            )
        return PagedList(data_points, None)

    def _compile_trace_metric_sample_query(
        self,
        *,
        experiment_ids,
        metric_name: str,
        dimensions=None,
        filters=None,
        time_interval_seconds=None,
        start_time_ms=None,
        end_time_ms=None,
    ) -> _CompiledMetricSampleQuery:
        dimension_columns = []
        for dimension in dimensions or []:
            column = {
                TraceMetricDimensionKey.TRACE_STATUS: "state",
                TraceMetricDimensionKey.TRACE_NAME: "trace_name",
            }.get(dimension)
            if column is None:
                raise MlflowException(
                    f"Iceberg trace backend does not support dimension {dimension!r} yet.",
                    error_code=INVALID_PARAMETER_VALUE,
                )
            dimension_columns.append((dimension, column))

        where_clauses = ["1 = 1"]
        params: _QueryParams = []
        if experiment_ids:
            where_clauses.append(f"experiment_id IN ({','.join('?' for _ in experiment_ids)})")
            params.extend(experiment_ids)
        workspace = self._get_active_workspace()
        if workspace:
            where_clauses.append("workspace = ?")
            params.append(workspace)
        if start_time_ms is not None:
            where_clauses.append("request_time_ms >= ?")
            params.append(start_time_ms)
        if end_time_ms is not None:
            where_clauses.append("request_time_ms <= ?")
            params.append(end_time_ms)
        for filter_clause in filters or []:
            for parsed_filter in SearchTraceUtils.parse_search_filter_for_search_traces(
                filter_clause
            ):
                clause, clause_params = _compile_trace_filter(parsed_filter)
                where_clauses.append(clause)
                params.extend(clause_params)

        value_expr = {
            TraceMetricKey.LATENCY: "execution_duration_ms",
            TraceMetricKey.INPUT_TOKENS: "input_tokens",
            TraceMetricKey.OUTPUT_TOKENS: "output_tokens",
            TraceMetricKey.TOTAL_TOKENS: "total_tokens",
            TraceMetricKey.CACHE_READ_INPUT_TOKENS: "cache_read_input_tokens",
            TraceMetricKey.CACHE_CREATION_INPUT_TOKENS: "cache_creation_input_tokens",
        }.get(metric_name)
        if value_expr is None:
            raise MlflowException.invalid_parameter_value(
                f"Unsupported trace metric sample source for {metric_name!r}"
            )
        where_clauses.append(f"{value_expr} IS NOT NULL")

        time_bucket_selects, _ = self._time_bucket_clause("request_time_ms", time_interval_seconds)
        trace_rows_scan = self._iceberg_scan(
            _TRACE_INDEX_TABLE,
            workspace=workspace,
            day_column="request_day",
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )
        include_trace_tags = _filters_reference_trace_tags(filters)
        trace_columns: set[str] = {"experiment_id", "request_time_ms", value_expr}
        trace_columns.update(column for _, column in dimension_columns)
        for filter_clause in filters or []:
            for parsed_filter in SearchTraceUtils.parse_search_filter_for_search_traces(
                filter_clause
            ):
                trace_columns.update(_search_trace_filter_columns(parsed_filter))
        latest_traces_cte = _projected_rows_cte_sql(
            "trace_rows",
            _TRACE_INDEX_TABLE,
            alias="latest_traces",
            columns=trace_columns,
        )
        trace_tags_cte = (
            ", latest_trace_tags AS (SELECT * FROM trace_tag_rows)" if include_trace_tags else ""
        )
        dimension_selects = ", ".join(
            time_bucket_selects
            + [f"{column} AS {_sql_identifier(name)}" for name, column in dimension_columns]
        )
        trace_tag_rows_scan = (
            self._iceberg_scan(
                _TRACE_TAG_INDEX_TABLE,
                workspace=workspace,
                day_column="request_day",
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
            if include_trace_tags
            else None
        )
        return _CompiledMetricSampleQuery(
            trace_rows=trace_rows_scan,
            trace_tag_rows=trace_tag_rows_scan,
            span_rows=None,
            assessment_rows=None,
            sql=f"""
                WITH {latest_traces_cte}
                {trace_tags_cte}
                SELECT
                    {dimension_selects}
                    {"," if (time_bucket_selects or dimension_columns) else ""}
                    {value_expr} AS sample_value
                FROM latest_traces
                WHERE {" AND ".join(where_clauses)}
            """,
            params=params,
            dimension_names=tuple(name for name, _ in dimension_columns),
            has_time_bucket=bool(time_bucket_selects),
        )

    def _compile_metric_sample_query(
        self,
        *,
        view_type,
        experiment_ids,
        metric_name: str,
        dimensions=None,
        filters=None,
        time_interval_seconds=None,
        start_time_ms=None,
        end_time_ms=None,
    ) -> _CompiledMetricSampleQuery:
        compile_query = {
            MetricViewType.TRACES: self._compile_trace_metric_sample_query,
            MetricViewType.SPANS: self._compile_span_metric_sample_query,
            MetricViewType.ASSESSMENTS: self._compile_assessment_metric_sample_query,
        }.get(view_type)
        if compile_query is None:
            raise MlflowException.invalid_parameter_value(
                f"Unsupported metric sample view type {view_type!r}"
            )
        return compile_query(
            experiment_ids=experiment_ids,
            metric_name=metric_name,
            dimensions=dimensions,
            filters=filters,
            time_interval_seconds=time_interval_seconds,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

    def _query_span_metrics(
        self,
        *,
        experiment_ids,
        metric_name: str,
        aggregations,
        dimensions=None,
        filters=None,
        time_interval_seconds=None,
        start_time_ms=None,
        end_time_ms=None,
        max_results=MAX_RESULTS_QUERY_TRACE_METRICS,
    ):
        dimension_columns = []
        for dimension in dimensions or []:
            column = {
                SpanMetricDimensionKey.SPAN_NAME: "s.name",
                SpanMetricDimensionKey.SPAN_TYPE: "s.span_type",
                SpanMetricDimensionKey.SPAN_STATUS: "s.status",
                SpanMetricDimensionKey.SPAN_MODEL_NAME: "s.model_name",
                SpanMetricDimensionKey.SPAN_MODEL_PROVIDER: "s.model_provider",
            }.get(dimension)
            if column is None:
                raise MlflowException.invalid_parameter_value(
                    f"Unsupported span metric dimension {dimension!r}"
                )
            dimension_columns.append((dimension, column))

        where_clauses = ["1 = 1"]
        params: _QueryParams = []
        if experiment_ids:
            where_clauses.append(f"s.experiment_id IN ({','.join('?' for _ in experiment_ids)})")
            params.extend(experiment_ids)
        workspace = self._get_active_workspace()
        if workspace:
            where_clauses.append("s.workspace = ?")
            params.append(workspace)
        if start_time_ms is not None:
            where_clauses.append("CAST(s.start_time_ns / 1000000 AS BIGINT) >= ?")
            params.append(start_time_ms)
        if end_time_ms is not None:
            where_clauses.append("CAST(s.start_time_ns / 1000000 AS BIGINT) <= ?")
            params.append(end_time_ms)
        for filter_clause in filters or []:
            clause, clause_params = self._compile_span_metric_filter(filter_clause)
            where_clauses.append(clause)
            params.extend(clause_params)

        if metric_name == SpanMetricKey.SPAN_COUNT:
            aggregation_sql = self._compile_metric_aggregations(aggregations, value_expr=None)
        elif metric_name == SpanMetricKey.LATENCY:
            aggregation_sql = self._compile_metric_aggregations(
                aggregations, value_expr="s.latency_ms"
            )
        elif metric_name in {
            SpanMetricKey.INPUT_COST,
            SpanMetricKey.OUTPUT_COST,
            SpanMetricKey.TOTAL_COST,
        }:
            cost_column = {
                SpanMetricKey.INPUT_COST: CostKey.INPUT_COST,
                SpanMetricKey.OUTPUT_COST: CostKey.OUTPUT_COST,
                SpanMetricKey.TOTAL_COST: CostKey.TOTAL_COST,
            }[metric_name]
            value_expr = {
                CostKey.INPUT_COST: "s.input_cost",
                CostKey.OUTPUT_COST: "s.output_cost",
                CostKey.TOTAL_COST: "s.total_cost",
            }[cost_column]
            aggregation_sql = self._compile_metric_aggregations(aggregations, value_expr=value_expr)
            where_clauses.append(f"{value_expr} IS NOT NULL")
        else:
            raise MlflowException.invalid_parameter_value(
                f"Unsupported span metric {metric_name!r}"
            )

        time_bucket_selects, time_bucket_group = self._time_bucket_clause(
            "CAST(s.start_time_ns / 1000000 AS BIGINT)", time_interval_seconds
        )
        select_dimensions = _dimension_selects(time_bucket_selects, dimension_columns)
        group_terms = _metric_group_terms(time_bucket_group, dimension_columns)
        group_by = ", ".join(group_terms)
        order_by = ", ".join(group_terms) or "1"
        include_trace_rows = _filters_reference_traces(filters)
        include_trace_tags = include_trace_rows and _filters_reference_trace_tags(filters)
        latest_trace_tags_cte = (
            "latest_trace_tags AS (SELECT * FROM trace_tag_rows)" if include_trace_tags else None
        )
        trace_columns = _metric_trace_filter_columns(filters) | {
            "request_time_ms",
            "execution_duration_ms",
            "tags_json",
        }
        latest_traces_cte = (
            _projected_rows_cte_sql(
                "trace_rows",
                _TRACE_INDEX_TABLE,
                alias="latest_traces",
                columns=trace_columns,
            )
            if include_trace_rows
            else None
        )
        span_columns: set[str] = {
            "workspace",
            "trace_id",
            "span_id",
            "experiment_id",
            "start_time_ns",
        }
        span_columns.update(
            _SPAN_METRIC_DIMENSION_COLUMNS[dimension]
            for dimension in dimensions or []
            if dimension in _SPAN_METRIC_DIMENSION_COLUMNS
        )
        if span_value_column := _SPAN_METRIC_VALUE_COLUMNS.get(metric_name):
            span_columns.add(span_value_column)
        span_columns.update(_span_metric_filter_columns(filters))
        latest_spans_cte = _projected_rows_cte_sql(
            "span_rows",
            _SPAN_INDEX_TABLE,
            alias="latest_spans",
            columns=span_columns,
        )
        experiment_id_filter = tuple(experiment_ids) if experiment_ids else None
        trace_rows_scan = (
            self._iceberg_scan(
                _TRACE_INDEX_TABLE,
                projected_columns=trace_columns,
                workspace=workspace,
                experiment_ids=experiment_id_filter,
                day_column="request_day",
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
            if include_trace_rows
            else None
        )
        span_rows_scan = self._iceberg_scan(
            _SPAN_INDEX_TABLE,
            projected_columns=span_columns,
            workspace=workspace,
            experiment_ids=experiment_id_filter,
            day_column="span_start_day",
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )
        rows = self._run_duckdb_query(
            trace_rows=trace_rows_scan,
            trace_tag_rows=self._iceberg_scan(
                _TRACE_TAG_INDEX_TABLE,
                workspace=workspace,
                experiment_ids=experiment_id_filter,
                day_column="request_day",
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
            if include_trace_tags
            else None,
            span_rows=span_rows_scan,
            sql=f"""
                WITH {
                ", ".join(
                    cte
                    for cte in (latest_traces_cte, latest_trace_tags_cte, latest_spans_cte)
                    if cte is not None
                )
            }
                SELECT
                    {", ".join(select_dimensions + aggregation_sql)}
                FROM latest_spans AS s
                {
                "JOIN latest_traces AS t ON "
                + _span_matches_trace_generation_sql(span_alias="s", trace_alias="t")
                if include_trace_rows
                else ""
            }
                WHERE {" AND ".join(where_clauses)}
                {"GROUP BY " + group_by if group_by else ""}
                ORDER BY {order_by}
                LIMIT {_validated_limit(max_results, "max_results")}
            """,
            params=params,
        )
        data_points = []
        for row in rows:
            if any(row.get(dimension) is None for dimension, _ in dimension_columns):
                continue
            dims = {
                dimension: row[dimension]
                for dimension, _ in dimension_columns
                if row.get(dimension) is not None
            }
            if row.get("time_bucket_ms") is not None:
                dims["time_bucket"] = datetime.fromtimestamp(
                    row["time_bucket_ms"] / 1000, tz=timezone.utc
                ).isoformat()
            values = {str(aggregation): row[str(aggregation)] for aggregation in aggregations}
            if all(value is None for value in values.values()):
                continue
            data_points.append(
                MetricDataPoint(metric_name=metric_name, dimensions=dims, values=values)
            )
        return PagedList(data_points, None)

    def _compile_span_metric_sample_query(
        self,
        *,
        experiment_ids,
        metric_name: str,
        dimensions=None,
        filters=None,
        time_interval_seconds=None,
        start_time_ms=None,
        end_time_ms=None,
    ) -> _CompiledMetricSampleQuery:
        dimension_columns = []
        for dimension in dimensions or []:
            column = {
                SpanMetricDimensionKey.SPAN_NAME: "s.name",
                SpanMetricDimensionKey.SPAN_TYPE: "s.span_type",
                SpanMetricDimensionKey.SPAN_STATUS: "s.status",
                SpanMetricDimensionKey.SPAN_MODEL_NAME: "s.model_name",
                SpanMetricDimensionKey.SPAN_MODEL_PROVIDER: "s.model_provider",
            }.get(dimension)
            if column is None:
                raise MlflowException.invalid_parameter_value(
                    f"Unsupported span metric dimension {dimension!r}"
                )
            dimension_columns.append((dimension, column))

        where_clauses = ["1 = 1"]
        params: _QueryParams = []
        if experiment_ids:
            where_clauses.append(f"s.experiment_id IN ({','.join('?' for _ in experiment_ids)})")
            params.extend(experiment_ids)
        workspace = self._get_active_workspace()
        if workspace:
            where_clauses.append("s.workspace = ?")
            params.append(workspace)
        if start_time_ms is not None:
            where_clauses.append("CAST(s.start_time_ns / 1000000 AS BIGINT) >= ?")
            params.append(start_time_ms)
        if end_time_ms is not None:
            where_clauses.append("CAST(s.start_time_ns / 1000000 AS BIGINT) <= ?")
            params.append(end_time_ms)
        for filter_clause in filters or []:
            clause, clause_params = self._compile_span_metric_filter(filter_clause)
            where_clauses.append(clause)
            params.extend(clause_params)

        value_expr = {
            SpanMetricKey.LATENCY: "s.latency_ms",
            SpanMetricKey.INPUT_COST: "s.input_cost",
            SpanMetricKey.OUTPUT_COST: "s.output_cost",
            SpanMetricKey.TOTAL_COST: "s.total_cost",
        }.get(metric_name)
        if value_expr is None:
            raise MlflowException.invalid_parameter_value(
                f"Unsupported span metric sample source for {metric_name!r}"
            )
        where_clauses.append(f"{value_expr} IS NOT NULL")

        time_bucket_selects, _ = self._time_bucket_clause(
            "CAST(s.start_time_ns / 1000000 AS BIGINT)", time_interval_seconds
        )
        include_trace_tags = _filters_reference_trace_tags(filters)
        trace_columns = _metric_trace_filter_columns(filters) | {
            "request_time_ms",
            "execution_duration_ms",
            "tags_json",
        }
        latest_traces_cte = _projected_rows_cte_sql(
            "trace_rows",
            _TRACE_INDEX_TABLE,
            alias="latest_traces",
            columns=trace_columns,
        )
        span_columns: set[str] = {
            "workspace",
            "trace_id",
            "span_id",
            "experiment_id",
            "start_time_ns",
        }
        span_columns.update(
            _SPAN_METRIC_DIMENSION_COLUMNS[dimension]
            for dimension in dimensions or []
            if dimension in _SPAN_METRIC_DIMENSION_COLUMNS
        )
        span_columns.add(_SPAN_METRIC_VALUE_COLUMNS[metric_name])
        span_columns.update(_span_metric_filter_columns(filters))
        latest_spans_cte = _projected_rows_cte_sql(
            "span_rows",
            _SPAN_INDEX_TABLE,
            alias="latest_spans",
            columns=span_columns,
        )
        experiment_id_filter = tuple(experiment_ids) if experiment_ids else None
        trace_rows_scan = self._iceberg_scan(
            _TRACE_INDEX_TABLE,
            projected_columns=trace_columns,
            workspace=workspace,
            experiment_ids=experiment_id_filter,
            day_column="request_day",
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )
        span_rows_scan = self._iceberg_scan(
            _SPAN_INDEX_TABLE,
            projected_columns=span_columns,
            workspace=workspace,
            experiment_ids=experiment_id_filter,
            day_column="span_start_day",
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )
        trace_tags_cte = (
            "latest_trace_tags AS (SELECT * FROM trace_tag_rows)," if include_trace_tags else ""
        )
        dimension_selects = ", ".join(
            time_bucket_selects
            + [f"{column} AS {_sql_identifier(name)}" for name, column in dimension_columns]
        )
        trace_tag_rows_scan = (
            self._iceberg_scan(
                _TRACE_TAG_INDEX_TABLE,
                workspace=workspace,
                experiment_ids=experiment_id_filter,
                day_column="request_day",
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
            if include_trace_tags
            else None
        )
        return _CompiledMetricSampleQuery(
            trace_rows=trace_rows_scan,
            trace_tag_rows=trace_tag_rows_scan,
            span_rows=span_rows_scan,
            assessment_rows=None,
            sql=f"""
                WITH {latest_traces_cte},
                {trace_tags_cte}
                {latest_spans_cte}
                SELECT
                    {dimension_selects}
                    {"," if (time_bucket_selects or dimension_columns) else ""}
                    {value_expr} AS sample_value
                FROM latest_spans AS s
                JOIN latest_traces AS t
                    ON {_span_matches_trace_generation_sql(span_alias="s", trace_alias="t")}
                WHERE {" AND ".join(where_clauses)}
            """,
            params=params,
            dimension_names=tuple(name for name, _ in dimension_columns),
            has_time_bucket=bool(time_bucket_selects),
        )

    def _query_assessment_metrics(
        self,
        *,
        experiment_ids,
        metric_name: str,
        aggregations,
        dimensions=None,
        filters=None,
        time_interval_seconds=None,
        start_time_ms=None,
        end_time_ms=None,
        max_results=MAX_RESULTS_QUERY_TRACE_METRICS,
    ):
        is_value_distribution = (
            metric_name == AssessmentMetricKey.ASSESSMENT_COUNT
            and AssessmentMetricDimensionKey.ASSESSMENT_VALUE in (dimensions or [])
        )
        if self._hybrid_enabled and is_value_distribution:
            max_traces = MLFLOW_ICEBERG_TRACE_ASSESSMENT_DISTRIBUTION_MAX_TRACES.get()
            if max_traces < 0:
                raise MlflowException.invalid_parameter_value(
                    "MLFLOW_ICEBERG_TRACE_ASSESSMENT_DISTRIBUTION_MAX_TRACES must be non-negative."
                )
            if max_traces:
                archived_trace_count = self._count_published_archived_traces(
                    experiment_ids=experiment_ids,
                    start_time_ms=start_time_ms,
                    end_time_ms=end_time_ms,
                    stop_after=max_traces,
                )
                if archived_trace_count > max_traces:
                    raise MlflowException(
                        "Exact assessment value distribution is unavailable because the cold "
                        f"range contains more than {max_traces:,} traces. Narrow the time range "
                        "or increase "
                        "MLFLOW_ICEBERG_TRACE_ASSESSMENT_DISTRIBUTION_MAX_TRACES.",
                        error_code=RESOURCE_EXHAUSTED,
                    )

        dimension_columns = []
        for dimension in dimensions or []:
            column = {
                AssessmentMetricDimensionKey.ASSESSMENT_NAME: "a.assessment_name",
                AssessmentMetricDimensionKey.ASSESSMENT_VALUE: "a.assessment_value_json",
            }.get(dimension)
            if column is None:
                raise MlflowException.invalid_parameter_value(
                    f"Unsupported assessment metric dimension {dimension!r}"
                )
            dimension_columns.append((dimension, column))

        where_clauses = ["1 = 1"]
        params: _QueryParams = []
        if experiment_ids:
            where_clauses.append(f"a.experiment_id IN ({','.join('?' for _ in experiment_ids)})")
            params.extend(experiment_ids)
        workspace = self._get_active_workspace()
        if workspace:
            where_clauses.append("a.workspace = ?")
            params.append(workspace)
        if start_time_ms is not None:
            where_clauses.append("a.trace_request_time_ms >= ?")
            params.append(start_time_ms)
        if end_time_ms is not None:
            where_clauses.append("a.trace_request_time_ms <= ?")
            params.append(end_time_ms)
        for filter_clause in filters or []:
            clause, clause_params = self._compile_assessment_metric_filter(filter_clause)
            where_clauses.append(clause)
            params.extend(clause_params)

        if metric_name == AssessmentMetricKey.ASSESSMENT_COUNT:
            aggregation_sql = self._compile_metric_aggregations(aggregations, value_expr=None)
        elif metric_name == AssessmentMetricKey.ASSESSMENT_VALUE:
            aggregation_sql = self._compile_metric_aggregations(
                aggregations, value_expr="a.aggregate_value"
            )
            where_clauses.append("a.aggregate_value IS NOT NULL")
        else:
            raise MlflowException.invalid_parameter_value(
                f"Unsupported assessment metric {metric_name!r}"
            )

        time_bucket_selects, time_bucket_group = self._time_bucket_clause(
            "a.trace_request_time_ms", time_interval_seconds
        )
        select_dimensions = _dimension_selects(time_bucket_selects, dimension_columns)
        group_terms = _metric_group_terms(time_bucket_group, dimension_columns)
        group_by = ", ".join(group_terms)
        order_by = ", ".join(group_terms) or "1"
        include_trace_rows = _filters_reference_traces(filters)
        include_trace_tags = include_trace_rows and _filters_reference_trace_tags(filters)
        latest_trace_tags_cte = (
            "latest_trace_tags AS (SELECT * FROM trace_tag_rows)" if include_trace_tags else None
        )
        trace_columns = _metric_trace_filter_columns(filters) | {
            "request_time_ms",
            "tags_json",
        }
        latest_traces_cte = (
            _projected_rows_cte_sql(
                "trace_rows",
                _TRACE_INDEX_TABLE,
                alias="latest_traces",
                columns=trace_columns,
            )
            if include_trace_rows
            else None
        )
        assessment_columns: set[str] = {
            "workspace",
            "experiment_id",
            "trace_request_time_ms",
        }
        if include_trace_rows:
            assessment_columns.add("trace_id")
        assessment_columns.update(
            _ASSESSMENT_METRIC_DIMENSION_COLUMNS[dimension]
            for dimension in dimensions or []
            if dimension in _ASSESSMENT_METRIC_DIMENSION_COLUMNS
        )
        if metric_name == AssessmentMetricKey.ASSESSMENT_VALUE:
            assessment_columns.add("aggregate_value")
        assessment_columns.update(_assessment_metric_filter_columns(filters))
        latest_assessments_cte = _projected_rows_cte_sql(
            "assessment_rows",
            _ASSESSMENT_INDEX_TABLE,
            alias="latest_assessments",
            columns=assessment_columns,
            include_required_columns=False,
        )
        experiment_id_filter = tuple(experiment_ids) if experiment_ids else None
        trace_rows_scan = (
            self._iceberg_scan(
                _TRACE_INDEX_TABLE,
                projected_columns=trace_columns,
                workspace=workspace,
                experiment_ids=experiment_id_filter,
                day_column="request_day",
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
            if include_trace_rows
            else None
        )
        assessment_rows_scan = self._iceberg_scan(
            _ASSESSMENT_INDEX_TABLE,
            projected_columns=assessment_columns,
            workspace=workspace,
            experiment_ids=experiment_id_filter,
            day_column="trace_request_day",
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            include_required_columns=False,
        )
        rows = self._run_duckdb_query(
            trace_rows=trace_rows_scan,
            trace_tag_rows=self._iceberg_scan(
                _TRACE_TAG_INDEX_TABLE,
                projected_columns={"trace_id", "tag_key", "tag_value"},
                workspace=workspace,
                experiment_ids=experiment_id_filter,
                day_column="request_day",
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
            if include_trace_tags
            else None,
            assessment_rows=assessment_rows_scan,
            sql=f"""
                WITH {
                ", ".join(
                    cte
                    for cte in (
                        latest_traces_cte,
                        latest_trace_tags_cte,
                        latest_assessments_cte,
                    )
                    if cte is not None
                )
            }
                SELECT
                    {", ".join(select_dimensions + aggregation_sql)}
                FROM latest_assessments AS a
                {
                "JOIN latest_traces AS t ON "
                + _assessment_matches_trace_generation_sql(assessment_alias="a", trace_alias="t")
                if include_trace_rows
                else ""
            }
                WHERE {" AND ".join(where_clauses)}
                {"GROUP BY " + group_by if group_by else ""}
                ORDER BY {order_by}
                LIMIT {_validated_limit(max_results, "max_results")}
            """,
            params=params,
        )
        return PagedList(
            [
                MetricDataPoint(
                    metric_name=metric_name,
                    dimensions={
                        **(
                            {
                                "time_bucket": datetime.fromtimestamp(
                                    row["time_bucket_ms"] / 1000, tz=timezone.utc
                                ).isoformat()
                            }
                            if row.get("time_bucket_ms") is not None
                            else {}
                        ),
                        **{
                            dimension: row[dimension]
                            for dimension, _ in dimension_columns
                            if row.get(dimension) is not None
                        },
                    },
                    values={
                        str(aggregation): row[str(aggregation)] for aggregation in aggregations
                    },
                )
                for row in rows
                if not all(row[str(aggregation)] is None for aggregation in aggregations)
            ],
            None,
        )

    def _compile_assessment_metric_sample_query(
        self,
        *,
        experiment_ids,
        metric_name: str,
        dimensions=None,
        filters=None,
        time_interval_seconds=None,
        start_time_ms=None,
        end_time_ms=None,
    ) -> _CompiledMetricSampleQuery:
        dimension_columns = []
        for dimension in dimensions or []:
            column = {
                AssessmentMetricDimensionKey.ASSESSMENT_NAME: "a.assessment_name",
                AssessmentMetricDimensionKey.ASSESSMENT_VALUE: "a.assessment_value_json",
            }.get(dimension)
            if column is None:
                raise MlflowException.invalid_parameter_value(
                    f"Unsupported assessment metric dimension {dimension!r}"
                )
            dimension_columns.append((dimension, column))

        where_clauses = ["1 = 1"]
        params: _QueryParams = []
        if experiment_ids:
            where_clauses.append(f"a.experiment_id IN ({','.join('?' for _ in experiment_ids)})")
            params.extend(experiment_ids)
        workspace = self._get_active_workspace()
        if workspace:
            where_clauses.append("a.workspace = ?")
            params.append(workspace)
        if start_time_ms is not None:
            where_clauses.append("a.trace_request_time_ms >= ?")
            params.append(start_time_ms)
        if end_time_ms is not None:
            where_clauses.append("a.trace_request_time_ms <= ?")
            params.append(end_time_ms)
        for filter_clause in filters or []:
            clause, clause_params = self._compile_assessment_metric_filter(filter_clause)
            where_clauses.append(clause)
            params.extend(clause_params)

        if metric_name != AssessmentMetricKey.ASSESSMENT_VALUE:
            raise MlflowException.invalid_parameter_value(
                f"Unsupported assessment metric sample source for {metric_name!r}"
            )
        where_clauses.append("a.aggregate_value IS NOT NULL")
        time_bucket_selects, _ = self._time_bucket_clause(
            "a.trace_request_time_ms", time_interval_seconds
        )
        include_trace_tags = _filters_reference_trace_tags(filters)
        trace_columns = _metric_trace_filter_columns(filters) | {
            "request_time_ms",
            "tags_json",
        }
        latest_traces_cte = _projected_rows_cte_sql(
            "trace_rows",
            _TRACE_INDEX_TABLE,
            alias="latest_traces",
            columns=trace_columns,
        )
        assessment_columns: set[str] = {
            "workspace",
            "assessment_id",
            "trace_id",
            "experiment_id",
            "trace_request_time_ms",
            "create_time_ms",
            "aggregate_value",
        }
        assessment_columns.update(
            _ASSESSMENT_METRIC_DIMENSION_COLUMNS[dimension]
            for dimension in dimensions or []
            if dimension in _ASSESSMENT_METRIC_DIMENSION_COLUMNS
        )
        assessment_columns.update(_assessment_metric_filter_columns(filters))
        latest_assessments_cte = _projected_rows_cte_sql(
            "assessment_rows",
            _ASSESSMENT_INDEX_TABLE,
            alias="latest_assessments",
            columns=assessment_columns,
        )
        experiment_id_filter = tuple(experiment_ids) if experiment_ids else None
        trace_rows_scan = self._iceberg_scan(
            _TRACE_INDEX_TABLE,
            projected_columns=trace_columns,
            workspace=workspace,
            experiment_ids=experiment_id_filter,
            day_column="request_day",
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )
        assessment_rows_scan = self._iceberg_scan(
            _ASSESSMENT_INDEX_TABLE,
            projected_columns=assessment_columns,
            workspace=workspace,
            experiment_ids=experiment_id_filter,
            day_column="trace_request_day",
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )
        trace_tag_rows_scan = (
            self._iceberg_scan(
                _TRACE_TAG_INDEX_TABLE,
                projected_columns={"trace_id", "tag_key", "tag_value"},
                workspace=workspace,
                experiment_ids=experiment_id_filter,
                day_column="request_day",
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
            if include_trace_tags
            else None
        )
        return _CompiledMetricSampleQuery(
            trace_rows=trace_rows_scan,
            trace_tag_rows=trace_tag_rows_scan,
            span_rows=None,
            assessment_rows=assessment_rows_scan,
            sql=f"""
                WITH {latest_traces_cte},
                {
                "latest_trace_tags AS (SELECT * FROM trace_tag_rows)," if include_trace_tags else ""
            }
                {latest_assessments_cte}
                SELECT
                    {
                ", ".join(
                    time_bucket_selects
                    + [f"{column} AS {_sql_identifier(name)}" for name, column in dimension_columns]
                )
            }
                    {"," if (time_bucket_selects or dimension_columns) else ""}
                    a.aggregate_value AS sample_value
                FROM latest_assessments AS a
                JOIN latest_traces AS t
                    ON {
                _assessment_matches_trace_generation_sql(assessment_alias="a", trace_alias="t")
            }
                WHERE {" AND ".join(where_clauses)}
            """,
            params=params,
            dimension_names=tuple(name for name, _ in dimension_columns),
            has_time_bucket=bool(time_bucket_selects),
        )

    def _find_completed_sessions_cold(
        self,
        experiment_id: str,
        min_last_trace_timestamp_ms: int,
        max_last_trace_timestamp_ms: int,
        max_results=None,
        filter_string=None,
    ) -> list[CompletedSession]:
        workspace = self._get_active_workspace()
        if not filter_string and self._iceberg_table_has_snapshot(_SESSION_SUMMARY_TABLE):
            rows = self._run_duckdb_query(
                trace_rows=self._iceberg_scan(
                    _SESSION_SUMMARY_TABLE,
                    workspace=workspace,
                    experiment_ids=(experiment_id,),
                ),
                sql=f"""
                    SELECT session_id, first_trace_timestamp_ms, last_trace_timestamp_ms
                    FROM trace_rows
                    WHERE workspace = ?
                        AND experiment_id = ?
                        AND last_trace_timestamp_ms >= ?
                        AND last_trace_timestamp_ms <= ?
                    ORDER BY last_trace_timestamp_ms ASC, session_id ASC
                    {_limit_clause(max_results, "max_results")}
                """,
                params=[
                    workspace,
                    experiment_id,
                    min_last_trace_timestamp_ms,
                    max_last_trace_timestamp_ms,
                ],
            )
            return [
                CompletedSession(
                    session_id=row["session_id"],
                    first_trace_timestamp_ms=row["first_trace_timestamp_ms"],
                    last_trace_timestamp_ms=row["last_trace_timestamp_ms"],
                )
                for row in rows
            ]

        where_clauses = ["experiment_id = ?", "session_id IS NOT NULL"]
        base_params: _QueryParams = [experiment_id]
        if workspace:
            where_clauses.append("workspace = ?")
            base_params.append(workspace)

        trace_filters = []
        span_filters = []
        first_trace_filters = []
        filter_params: _QueryParams = []
        if filter_string:
            for parsed_filter in SearchTraceUtils.parse_search_filter_for_search_traces(
                filter_string
            ):
                if parsed_filter["type"] == "span":
                    span_filters.append(parsed_filter)
                    continue
                trace_filters.append(parsed_filter)
                clause, clause_params = _compile_trace_filter(parsed_filter, table_alias="ft")
                first_trace_filters.append(clause)
                filter_params.extend(clause_params)

        spec = _TraceSearchSpec(
            experiment_ids=[experiment_id],
            trace_filters=trace_filters,
            span_filters=span_filters,
            assessment_filters=[],
            order_by=[],
            sql_limit=1,
            workspace=workspace,
            include_session_id=True,
        )
        if span_filters:
            clause, clause_params = _DuckDBTraceSearchCompiler(spec)._compile_span_filter_exists(
                span_filters, trace_alias="ft"
            )
            first_trace_filters.append(clause)
            filter_params.extend(clause_params)
        filter_start_time_ms, _ = _trace_time_bounds_from_filters(trace_filters)

        limit_clause = _limit_clause(max_results, "max_results")
        latest_traces_cte = _projected_rows_cte_sql(
            "trace_rows",
            _TRACE_INDEX_TABLE,
            alias="latest_traces",
            columns=_trace_search_projection_columns(spec),
        )
        latest_spans_cte = (
            ", "
            + _projected_rows_cte_sql(
                "span_rows",
                _SPAN_INDEX_TABLE,
                alias="latest_spans",
                columns=_span_search_projection_columns(span_filters),
            )
            if span_filters
            else ""
        )
        rows = self._run_duckdb_query(
            trace_rows=self._iceberg_scan(
                _TRACE_INDEX_TABLE,
                projected_columns=_trace_search_projection_columns(spec),
                workspace=workspace,
                experiment_ids=(experiment_id,),
                day_column="request_day",
                start_time_ms=filter_start_time_ms,
                end_time_ms=max_last_trace_timestamp_ms,
            ),
            trace_tag_rows=self._iceberg_scan(
                _TRACE_TAG_INDEX_TABLE,
                workspace=workspace,
                experiment_ids=(experiment_id,),
            ),
            span_rows=(
                self._iceberg_scan(
                    _SPAN_INDEX_TABLE,
                    projected_columns=_span_search_projection_columns(span_filters),
                    workspace=workspace,
                    experiment_ids=(experiment_id,),
                    day_column="span_start_day" if filter_start_time_ms is not None else None,
                    start_time_ms=(
                        max(0, filter_start_time_ms - _SPAN_SEARCH_TRACE_START_TOLERANCE_MS)
                        if filter_start_time_ms is not None
                        else None
                    ),
                )
                if span_filters
                else None
            ),
            sql=f"""
                WITH {latest_traces_cte},
                latest_trace_tags AS (
                    SELECT *
                    FROM trace_tag_rows
                )
                {latest_spans_cte},
                session_traces AS (
                    SELECT *
                    FROM latest_traces
                    WHERE {" AND ".join(where_clauses)}
                ),
                session_stats AS (
                    SELECT
                        session_id,
                        min(request_time_ms) AS first_trace_timestamp_ms,
                        max(request_time_ms) AS last_trace_timestamp_ms
                    FROM session_traces
                    GROUP BY session_id
                ),
                first_traces AS (
                    SELECT *
                    FROM session_traces
                    QUALIFY row_number() OVER (
                        PARTITION BY session_id ORDER BY request_time_ms ASC, trace_id ASC
                    ) = 1
                )
                SELECT
                    stats.session_id,
                    stats.first_trace_timestamp_ms,
                    stats.last_trace_timestamp_ms
                FROM session_stats AS stats
                JOIN first_traces AS ft
                    ON stats.session_id = ft.session_id
                WHERE stats.last_trace_timestamp_ms >= ?
                    AND stats.last_trace_timestamp_ms <= ?
                    {"AND " + " AND ".join(first_trace_filters) if first_trace_filters else ""}
                ORDER BY stats.last_trace_timestamp_ms ASC, stats.session_id ASC
                {limit_clause}
            """,
            params=[
                *base_params,
                min_last_trace_timestamp_ms,
                max_last_trace_timestamp_ms,
                *filter_params,
            ],
        )
        return [
            CompletedSession(
                session_id=row["session_id"],
                first_trace_timestamp_ms=row["first_trace_timestamp_ms"],
                last_trace_timestamp_ms=row["last_trace_timestamp_ms"],
            )
            for row in rows
        ]

    def _calculate_trace_filter_correlation_cold(
        self,
        experiment_ids,
        filter_string1: str,
        filter_string2: str,
        base_filter: str | None = None,
    ):
        workspace = self._get_active_workspace()
        base_where = ["1 = 1"]
        base_params: _QueryParams = []
        if experiment_ids:
            base_where.append(f"experiment_id IN ({','.join('?' for _ in experiment_ids)})")
            base_params.extend(experiment_ids)
        if workspace:
            base_where.append("workspace = ?")
            base_params.append(workspace)
        parsed_base_filters = (
            SearchTraceUtils.parse_search_filter_for_search_traces(base_filter)
            if base_filter
            else []
        )
        parsed_filter1 = SearchTraceUtils.parse_search_filter_for_search_traces(filter_string1)
        parsed_filter2 = SearchTraceUtils.parse_search_filter_for_search_traces(filter_string2)

        def _compile_filters(parsed_filters):
            clauses = []
            params: _QueryParams = []
            for parsed_filter in parsed_filters:
                clause, clause_params = _compile_trace_filter(parsed_filter, table_alias="t")
                clauses.append(clause)
                params.extend(clause_params)
            return clauses, params

        base_filter_clauses, base_filter_params = _compile_filters(parsed_base_filters)
        base_where.extend(base_filter_clauses)
        base_params.extend(base_filter_params)
        filter1_clauses, filter1_params = _compile_filters(parsed_filter1)
        filter2_clauses, filter2_params = _compile_filters(parsed_filter2)
        filter1_sql = " AND ".join(filter1_clauses) or "TRUE"
        filter2_sql = " AND ".join(filter2_clauses) or "TRUE"

        all_filters = [*parsed_base_filters, *parsed_filter1, *parsed_filter2]
        trace_columns = {"workspace", "experiment_id"}
        for parsed_filter in all_filters:
            trace_columns.update(_search_trace_filter_columns(parsed_filter))
        include_trace_tags = _trace_search_references_arbitrary_tags(all_filters, [])
        latest_traces_cte = _projected_rows_cte_sql(
            "trace_rows",
            _TRACE_INDEX_TABLE,
            alias="latest_traces",
            columns=trace_columns,
        )
        trace_tags_cte = (
            ", latest_trace_tags AS (SELECT * FROM trace_tag_rows)" if include_trace_tags else ""
        )
        rows = self._run_duckdb_query(
            trace_rows=self._iceberg_scan(
                _TRACE_INDEX_TABLE,
                projected_columns=trace_columns,
                workspace=workspace,
                experiment_ids=tuple(experiment_ids) if experiment_ids else None,
            ),
            trace_tag_rows=(
                self._iceberg_scan(
                    _TRACE_TAG_INDEX_TABLE,
                    projected_columns={"trace_id", "tag_key", "tag_value"},
                    workspace=workspace,
                    experiment_ids=tuple(experiment_ids) if experiment_ids else None,
                )
                if include_trace_tags
                else None
            ),
            sql=f"""
                WITH {latest_traces_cte}
                {trace_tags_cte}
                SELECT
                    COUNT(*) AS total_count,
                    COUNT(*) FILTER (WHERE {filter1_sql}) AS filter1_count,
                    COUNT(*) FILTER (WHERE {filter2_sql}) AS filter2_count,
                    COUNT(*) FILTER (WHERE ({filter1_sql}) AND ({filter2_sql})) AS joint_count
                FROM latest_traces AS t
                WHERE {" AND ".join(base_where)}
            """,
            params=[
                *filter1_params,
                *filter2_params,
                *filter1_params,
                *filter2_params,
                *base_params,
            ],
        )
        total_count = rows[0]["total_count"]
        filter1_count = rows[0]["filter1_count"]
        filter2_count = rows[0]["filter2_count"]
        joint_count = rows[0]["joint_count"]
        npmi_result = calculate_npmi_from_counts(
            joint_count=joint_count,
            filter1_count=filter1_count,
            filter2_count=filter2_count,
            total_count=total_count,
        )
        return TraceFilterCorrelationResult(
            npmi=npmi_result.npmi,
            npmi_smoothed=npmi_result.npmi_smoothed,
            filter1_count=filter1_count,
            filter2_count=filter2_count,
            joint_count=joint_count,
            total_count=total_count,
        )

    def _select_cold_trace_ids_for_delete(
        self,
        experiment_id: str,
        max_timestamp_millis: int | None = None,
        max_traces: int | None = None,
        trace_ids=None,
        excluded_trace_ids: set[str] | None = None,
    ) -> list[str]:
        workspace = self._get_active_workspace()
        where_clauses = ["experiment_id = ?"]
        params: _QueryParams = [experiment_id]
        if workspace:
            where_clauses.append("workspace = ?")
            params.append(workspace)
        if max_timestamp_millis is not None:
            where_clauses.append("request_time_ms <= ?")
            params.append(max_timestamp_millis)
        if trace_ids:
            where_clauses.append(f"trace_id IN ({','.join('?' for _ in trace_ids)})")
            params.extend(trace_ids)
        if excluded_trace_ids:
            where_clauses.append(f"trace_id NOT IN ({','.join('?' for _ in excluded_trace_ids)})")
            params.extend(sorted(excluded_trace_ids))

        limit_clause = _limit_clause(max_traces, "max_traces")
        latest_traces_cte = _projected_rows_cte_sql(
            "trace_rows", _TRACE_INDEX_TABLE, alias="latest_traces"
        )
        rows = self._run_duckdb_query(
            trace_rows=self._iceberg_scan(
                _TRACE_INDEX_TABLE,
                workspace=workspace,
                day_column="request_day",
                end_time_ms=max_timestamp_millis,
            ),
            sql=f"""
                WITH {latest_traces_cte}
                SELECT trace_id
                FROM latest_traces
                WHERE {" AND ".join(where_clauses)}
                ORDER BY request_time_ms ASC, trace_id ASC
                {limit_clause}
            """,
            params=params,
        )
        return [row["trace_id"] for row in rows]

    def _get_trace_cold(self, trace_id: str, *, allow_partial: bool = False):
        rows = self._trace_detail_rows(trace_id)
        trace_row = next((row for row in rows if row["row_type"] == "trace"), None)
        if trace_row is None:
            raise MlflowException(
                f"Trace with ID {trace_id} is not found.",
                error_code=RESOURCE_DOES_NOT_EXIST,
            )
        span_rows = sorted(
            (row for row in rows if row["row_type"] == "span"),
            key=lambda row: (row["span_start_time_ns"], row["span_id"]),
        )
        spans = [Span.from_dict(json.loads(row["span_json"])) for row in span_rows]
        metadata = json.loads(trace_row["metadata_json"]) if trace_row["metadata_json"] else {}
        if size_stats_json := metadata.get(TraceMetadataKey.SIZE_STATS):
            expected_num_spans = json.loads(size_stats_json).get(TraceSizeStatsKey.NUM_SPANS)
            if not allow_partial and expected_num_spans and expected_num_spans > len(spans):
                raise MlflowException(
                    f"Trace with ID {trace_id} is not fully exported yet.",
                    error_code=RESOURCE_DOES_NOT_EXIST,
                )
        assessments = self._assessments_for_trace_rows({trace_id: trace_row}).get(trace_id, [])
        return Trace(
            info=self._trace_row_to_entity(trace_row, assessments=assessments),
            data=TraceData(spans=spans),
        )

    def _get_trace_from_cold_info(
        self,
        trace_row: _Row,
        trace_info: TraceInfo,
        *,
        allow_partial: bool,
    ) -> Trace:
        trace_id = trace_info.trace_id
        span_rows = sorted(
            self._latest_span_rows_by_trace_id(
                [trace_id], trace_rows_by_id={trace_id: trace_row}
            ).get(trace_id, []),
            key=lambda row: (row["start_time_ns"], row["span_id"]),
        )
        spans = [Span.from_dict(json.loads(row["span_json"])) for row in span_rows]
        if size_stats_json := trace_info.trace_metadata.get(TraceMetadataKey.SIZE_STATS):
            expected_num_spans = json.loads(size_stats_json).get(TraceSizeStatsKey.NUM_SPANS)
            if not allow_partial and expected_num_spans and expected_num_spans > len(spans):
                raise MlflowException(
                    f"Trace with ID {trace_id} is not fully exported yet.",
                    error_code=RESOURCE_DOES_NOT_EXIST,
                )
        return Trace(info=trace_info, data=TraceData(spans=spans))

    def _batch_get_traces_cold(self, trace_ids, location: str | None = None):
        del location
        trace_ids = list(trace_ids)
        traces = []
        trace_rows_by_id = self._latest_trace_rows_by_trace_id(trace_ids)
        span_rows_by_trace_id = self._latest_span_rows_by_trace_id(
            trace_ids, trace_rows_by_id=trace_rows_by_id
        )
        assessments_by_trace_id = self._assessments_for_trace_rows(trace_rows_by_id)
        for trace_id in trace_ids:
            trace_row = trace_rows_by_id.get(trace_id)
            if trace_row is None:
                continue
            span_rows = sorted(
                span_rows_by_trace_id.get(trace_id, []),
                key=lambda row: (row["start_time_ns"], row["span_id"]),
            )
            spans = [Span.from_dict(json.loads(row["span_json"])) for row in span_rows]
            metadata = json.loads(trace_row["metadata_json"]) if trace_row["metadata_json"] else {}
            if size_stats_json := metadata.get(TraceMetadataKey.SIZE_STATS):
                expected_num_spans = json.loads(size_stats_json).get(TraceSizeStatsKey.NUM_SPANS)
                if expected_num_spans and expected_num_spans > len(spans):
                    continue
            traces.append(
                Trace(
                    info=self._trace_row_to_entity(
                        trace_row, assessments=assessments_by_trace_id.get(trace_id, [])
                    ),
                    data=TraceData(spans=spans),
                )
            )
        return traces

    def _batch_get_trace_infos_cold(self, trace_ids, location: str | None = None):
        del location
        trace_ids = list(trace_ids)
        trace_rows_by_id = self._latest_trace_rows_by_trace_id(trace_ids)
        assessments_by_trace_id = self._assessments_for_trace_rows(trace_rows_by_id)
        return [
            self._trace_row_to_entity(
                trace_row, assessments=assessments_by_trace_id.get(trace_id, [])
            )
            for trace_id in trace_ids
            if (trace_row := trace_rows_by_id.get(trace_id))
        ]

    def _call_hot_store(self, method_name: str, *args, **kwargs):
        return getattr(super(), method_name)(*args, **kwargs)

    @property
    def _hybrid_enabled(self) -> bool:
        return True

    @staticmethod
    def _is_archive_backed_trace_info(trace_info: TraceInfo) -> bool:
        return trace_info.tags.get(TraceTagKey.SPANS_LOCATION) == SpansLocation.ARCHIVE_REPO.value

    @staticmethod
    def _trace_with_empty_spans(trace_info: TraceInfo) -> Trace:
        return Trace(info=trace_info, data=TraceData(spans=[]))

    @staticmethod
    def _is_not_found_exception(exc: MlflowException) -> bool:
        return exc.error_code == ErrorCode.Name(RESOURCE_DOES_NOT_EXIST)

    @staticmethod
    def _has_payload_dependent_span_filters(filter_string: str | None) -> bool:
        if not filter_string:
            return False

        return any(
            parsed_filter["type"] == "span"
            and (
                parsed_filter["key"] == "content" or parsed_filter["key"].startswith("attributes.")
            )
            for parsed_filter in SearchTraceUtils.parse_search_filter_for_search_traces(
                filter_string
            )
        )

    @staticmethod
    def _trace_info_order_value(
        trace_info: TraceInfo,
        *,
        identifier_type: str,
        key: str,
    ) -> Any:
        if identifier_type == "attribute":
            if key == "status":
                return trace_info.state.value if trace_info.state is not None else None
            if key == "name":
                return trace_info.tags.get(TraceTagKey.TRACE_NAME)
            if key in {"timestamp", "timestamp_ms"}:
                return trace_info.request_time
            if key in {"execution_time", "execution_time_ms"}:
                return trace_info.execution_duration
            if key in {"end_time", "end_time_ms"}:
                if trace_info.execution_duration is None:
                    return None
                return trace_info.request_time + trace_info.execution_duration
            if key == "request_id":
                return trace_info.trace_id
            if not hasattr(trace_info, key):
                raise MlflowException.invalid_parameter_value(
                    f"Invalid order_by entity `attribute` with key `{key}`"
                )
            return getattr(trace_info, key)
        if identifier_type == "tag":
            return trace_info.tags.get(key)
        if identifier_type == "request_metadata":
            return trace_info.request_metadata.get(key)
        raise MlflowException.invalid_parameter_value(
            f"Invalid order_by entity `{identifier_type}` with key `{key}`"
        )

    def _trace_info_sort_key(self, order_by: list[str] | None):
        class _OrderedValue:
            def __init__(self, value, *, ascending: bool):
                self.value = value
                self.ascending = ascending

            def __eq__(self, other):
                if not isinstance(other, _OrderedValue):
                    return NotImplemented
                return other.value == self.value

            def __lt__(self, other):
                if self.value is None:
                    return False
                if other.value is None:
                    return True
                if self.ascending:
                    return self.value < other.value
                return other.value < self.value

        parsed_order_by = [
            SearchTraceUtils.parse_order_by_for_search_traces(clause) for clause in (order_by or [])
        ]
        if not any(key == "timestamp_ms" for _, key, _ in parsed_order_by):
            parsed_order_by.append(("attribute", "timestamp_ms", False))
        if not any(key == "request_id" for _, key, _ in parsed_order_by):
            parsed_order_by.append(("attribute", "request_id", True))

        def _sort_value(trace_info: TraceInfo):
            values = []
            for identifier_type, key, is_ascending in parsed_order_by:
                value = self._trace_info_order_value(
                    trace_info,
                    identifier_type=identifier_type,
                    key=key,
                )
                values.append(_OrderedValue(value, ascending=is_ascending))
            return tuple(values)

        return _sort_value

    def _merge_hot_and_cold_trace_infos(
        self,
        *,
        hot_trace_infos: list[TraceInfo],
        cold_trace_infos: list[TraceInfo],
        order_by: list[str] | None,
        max_results: int,
        page_token: str | None,
        authoritative_trace_infos_by_id: dict[str, TraceInfo] | None = None,
    ) -> tuple[list[TraceInfo], str | None]:
        offset = SearchTraceUtils.parse_start_offset_from_page_token(page_token)
        merged_by_trace_id = {trace_info.trace_id: trace_info for trace_info in hot_trace_infos}
        cold_only_trace_ids = [
            trace_info.trace_id
            for trace_info in cold_trace_infos
            if trace_info.trace_id not in merged_by_trace_id
        ]
        if cold_only_trace_ids:
            authoritative_infos = authoritative_trace_infos_by_id
            if authoritative_infos is None:
                authoritative_infos = {
                    trace_info.trace_id: trace_info
                    for trace_info in self._call_hot_store(
                        "batch_get_trace_infos",
                        cold_only_trace_ids,
                    )
                }
            for cold_trace_info in cold_trace_infos:
                if cold_trace_info.trace_id in merged_by_trace_id:
                    continue
                authoritative_trace_info = authoritative_infos.get(cold_trace_info.trace_id)
                merged_by_trace_id[cold_trace_info.trace_id] = (
                    authoritative_trace_info or cold_trace_info
                )

        merged_trace_infos = sorted(
            merged_by_trace_id.values(),
            key=self._trace_info_sort_key(order_by),
        )
        page = merged_trace_infos[offset : offset + max_results]
        has_next_page = len(merged_trace_infos) > offset + max_results
        next_page_token = (
            SearchTraceUtils.create_page_token(offset + max_results) if has_next_page else None
        )
        return page, next_page_token

    def _collect_trace_infos_from_search(
        self,
        search_fn,
        *,
        fetch_limit: int | None,
        experiment_ids,
        filter_string,
        order_by,
        model_id,
        locations,
    ) -> list[TraceInfo]:
        collected: list[TraceInfo] = []
        page_token = None
        effective_fetch_limit = (
            fetch_limit if fetch_limit is not None else _MAX_HYBRID_TRACE_INFO_COLLECTION
        )
        while True:
            remaining = effective_fetch_limit - len(collected)
            if remaining <= 0:
                break

            page_size = min(SEARCH_MAX_RESULTS_THRESHOLD, remaining)
            if page_size <= 0:
                break

            trace_infos, next_token = search_fn(
                experiment_ids=experiment_ids,
                filter_string=filter_string,
                max_results=page_size,
                order_by=order_by,
                page_token=page_token,
                model_id=model_id,
                locations=locations,
            )
            collected.extend(trace_infos)
            if next_token is None or not trace_infos:
                break
            if fetch_limit is None and len(collected) >= _MAX_HYBRID_TRACE_INFO_COLLECTION:
                raise MlflowException.invalid_parameter_value(
                    "Hybrid payload-filter scans are limited to "
                    f"{_MAX_HYBRID_TRACE_INFO_COLLECTION} traces."
                )
            page_token = next_token
        return collected

    @staticmethod
    def _combine_filter_strings(*filter_strings: str | None) -> str | None:
        combined = [filter_string for filter_string in filter_strings if filter_string]
        return " AND ".join(combined) if combined else None

    @staticmethod
    def _metric_points_have_data(points: list[MetricDataPoint]) -> bool:
        return any(value is not None for point in points for value in point.values.values())

    @staticmethod
    def _can_merge_metric_points(aggregations: list[MetricAggregation]) -> bool:
        return all(
            aggregation.aggregation_type
            in {
                AggregationType.COUNT,
                AggregationType.SUM,
                AggregationType.MIN,
                AggregationType.MAX,
            }
            for aggregation in aggregations
        )

    @staticmethod
    def _can_merge_avg_metric_points(aggregations: list[MetricAggregation]) -> bool:
        aggregation_types = {aggregation.aggregation_type for aggregation in aggregations}
        return (
            AggregationType.AVG in aggregation_types
            and AggregationType.PERCENTILE not in aggregation_types
        )

    @staticmethod
    def _avg_merge_aggregations(aggregations: list[MetricAggregation]) -> list[MetricAggregation]:
        if not _IcebergTraceStoreBase._can_merge_avg_metric_points(aggregations):
            return aggregations
        augmented = list(aggregations)
        labels = {str(aggregation) for aggregation in augmented}
        for aggregation_type in (AggregationType.COUNT, AggregationType.SUM):
            aggregation = MetricAggregation(aggregation_type=aggregation_type)
            if str(aggregation) not in labels:
                augmented.append(aggregation)
                labels.add(str(aggregation))
        return augmented

    @staticmethod
    def _strip_helper_metric_values(
        points: list[MetricDataPoint],
        aggregations: list[MetricAggregation],
    ) -> list[MetricDataPoint]:
        requested_labels = {str(aggregation) for aggregation in aggregations}
        return [
            MetricDataPoint(
                metric_name=point.metric_name,
                dimensions=dict(point.dimensions),
                values={
                    label: value
                    for label, value in point.values.items()
                    if label in requested_labels
                },
            )
            for point in points
        ]

    @staticmethod
    def _merge_metric_values(
        left: dict[str, float],
        right: dict[str, float],
    ) -> dict[str, float]:
        merged: dict[str, float] = {}
        for key in set(left) | set(right):
            left_value = left.get(key)
            right_value = right.get(key)
            if key in {"COUNT", "SUM"}:
                merged[key] = float(left_value or 0) + float(right_value or 0)
            elif (
                key == "AVG"
                and ("COUNT" in left or "COUNT" in right)
                and ("SUM" in left or "SUM" in right)
            ):
                total_count = float(left.get("COUNT") or 0) + float(right.get("COUNT") or 0)
                total_sum = float(left.get("SUM") or 0) + float(right.get("SUM") or 0)
                merged[key] = total_sum / total_count if total_count else None
            elif key == "MIN":
                candidates = [value for value in (left_value, right_value) if value is not None]
                merged[key] = min(candidates) if candidates else None
            elif key == "MAX":
                candidates = [value for value in (left_value, right_value) if value is not None]
                merged[key] = max(candidates) if candidates else None
            else:
                merged[key] = right_value if right_value is not None else left_value
        return merged

    def _merge_metric_points(
        self,
        hot_points: list[MetricDataPoint],
        cold_points: list[MetricDataPoint],
    ) -> list[MetricDataPoint]:
        merged: dict[tuple[str, tuple[tuple[str, str], ...]], MetricDataPoint] = {}
        for point in [*cold_points, *hot_points]:
            key = self._metric_point_key(point)
            if key not in merged:
                merged[key] = MetricDataPoint(
                    metric_name=point.metric_name,
                    dimensions=dict(point.dimensions),
                    values=dict(point.values),
                )
                continue
            merged[key] = MetricDataPoint(
                metric_name=point.metric_name,
                dimensions=dict(point.dimensions),
                values=self._merge_metric_values(merged[key].values, point.values),
            )
        return list(merged.values())

    @staticmethod
    def _metric_point_key(point: MetricDataPoint) -> tuple[str, tuple[tuple[str, str], ...]]:
        return point.metric_name, tuple(sorted(point.dimensions.items()))

    @staticmethod
    def _metric_sample_key(
        metric_name: str,
        dimensions: dict[str, str],
    ) -> tuple[str, tuple[tuple[str, str], ...]]:
        return metric_name, tuple(sorted(dimensions.items()))

    def _metric_point_keys_overlap(
        self, hot_points: list[MetricDataPoint], cold_points: list[MetricDataPoint]
    ) -> bool:
        hot_keys = {self._metric_point_key(point) for point in hot_points}
        return any(self._metric_point_key(point) in hot_keys for point in cold_points)

    def _metric_point_key_intersection(
        self,
        hot_points: list[MetricDataPoint],
        cold_points: list[MetricDataPoint],
    ) -> set[tuple[str, tuple[tuple[str, str], ...]]]:
        return {self._metric_point_key(point) for point in hot_points} & {
            self._metric_point_key(point) for point in cold_points
        }

    def _metric_points_excluding_keys(
        self,
        points: list[MetricDataPoint],
        excluded_keys: set[tuple[str, tuple[tuple[str, str], ...]]],
    ) -> list[MetricDataPoint]:
        return [point for point in points if self._metric_point_key(point) not in excluded_keys]

    @staticmethod
    def _metric_overlap_sample_time_bounds(
        *,
        overlap_keys: set[tuple[str, tuple[tuple[str, str], ...]]],
        time_interval_seconds: int | None,
        start_time_ms: int | None,
        end_time_ms: int | None,
    ) -> tuple[int | None, int | None]:
        if time_interval_seconds is None:
            return start_time_ms, end_time_ms
        bucket_starts = []
        for _, dimensions in overlap_keys:
            time_bucket = dict(dimensions).get("time_bucket")
            if time_bucket is None:
                return start_time_ms, end_time_ms
            try:
                bucket_starts.append(int(datetime.fromisoformat(time_bucket).timestamp() * 1000))
            except ValueError:
                return start_time_ms, end_time_ms
        if not bucket_starts:
            return start_time_ms, end_time_ms
        interval_ms = time_interval_seconds * 1000
        overlap_start_time_ms = min(bucket_starts)
        overlap_end_time_ms = max(bucket_starts) + interval_ms - 1
        if start_time_ms is not None:
            overlap_start_time_ms = max(overlap_start_time_ms, start_time_ms)
        if end_time_ms is not None:
            overlap_end_time_ms = min(overlap_end_time_ms, end_time_ms)
        return overlap_start_time_ms, overlap_end_time_ms

    def _sort_and_limit_metric_points(
        self,
        points: list[MetricDataPoint],
        max_results: int | None,
    ) -> list[MetricDataPoint]:
        sorted_points = sorted(
            points,
            key=lambda point: tuple(value for _, value in self._metric_point_key(point)[1]),
        )
        return sorted_points[:max_results] if max_results is not None else sorted_points

    def _merge_metric_points_with_overlap_arrow(
        self,
        *,
        view_type,
        metric_name: str,
        aggregations: list[MetricAggregation],
        hot_points: list[MetricDataPoint],
        cold_points: list[MetricDataPoint],
        hot_samples: list[MetricDataSample],
        overlap_keys: set[tuple[str, tuple[tuple[str, str], ...]]],
        max_results: int | None,
        query_kwargs: dict[str, Any],
    ) -> PagedList[MetricDataPoint]:
        cold_query = self._compile_metric_sample_query(
            view_type=view_type,
            experiment_ids=query_kwargs["experiment_ids"],
            metric_name=metric_name,
            dimensions=query_kwargs.get("dimensions"),
            filters=query_kwargs.get("filters"),
            time_interval_seconds=query_kwargs.get("time_interval_seconds"),
            start_time_ms=query_kwargs.get("start_time_ms"),
            end_time_ms=query_kwargs.get("end_time_ms"),
        )
        dimension_columns = list(cold_query.dimension_names)
        if cold_query.has_time_bucket:
            dimension_columns.append("time_bucket_ms")

        arrow_fields = [pa.field(name, pa.string()) for name in cold_query.dimension_names]
        if cold_query.has_time_bucket:
            arrow_fields.append(pa.field("time_bucket_ms", pa.int64()))
        arrow_fields.append(pa.field("sample_value", pa.float64()))
        hot_rows = []
        for sample in hot_samples:
            if self._metric_sample_key(metric_name, sample.dimensions) not in overlap_keys:
                continue
            row = {name: sample.dimensions.get(name) for name in cold_query.dimension_names}
            if cold_query.has_time_bucket:
                time_bucket = sample.dimensions.get("time_bucket")
                row["time_bucket_ms"] = (
                    int(datetime.fromisoformat(time_bucket).timestamp() * 1000)
                    if time_bucket is not None
                    else None
                )
            row["sample_value"] = sample.value
            hot_rows.append(row)
        hot_table = pa.Table.from_pylist(hot_rows, schema=pa.schema(arrow_fields))

        selected_columns = [*dimension_columns, "sample_value"]
        selected_columns_sql = ", ".join(_sql_identifier(name) for name in selected_columns)
        group_by_sql = ", ".join(_sql_identifier(name) for name in dimension_columns)
        aggregation_sql = self._compile_metric_aggregations(aggregations, value_expr="sample_value")
        select_sql = ", ".join([
            *(f"{_sql_identifier(name)}" for name in dimension_columns),
            *aggregation_sql,
        ])
        rows = self._run_duckdb_query(
            trace_rows=cold_query.trace_rows,
            trace_tag_rows=cold_query.trace_tag_rows,
            span_rows=cold_query.span_rows,
            assessment_rows=cold_query.assessment_rows,
            arrow_tables={"hot_metric_samples": hot_table},
            sql=f"""
                WITH cold_metric_samples AS (
                    {cold_query.sql}
                ),
                combined_metric_samples AS (
                    SELECT {selected_columns_sql} FROM cold_metric_samples
                    UNION ALL
                    SELECT {selected_columns_sql} FROM hot_metric_samples
                )
                SELECT {select_sql}
                FROM combined_metric_samples
                {"GROUP BY " + group_by_sql if group_by_sql else ""}
                {"ORDER BY " + group_by_sql if group_by_sql else ""}
            """,
            params=cold_query.params,
        )

        overlap_points = []
        for row in rows:
            point_dimensions = {
                name: row[name] for name in cold_query.dimension_names if row.get(name) is not None
            }
            if cold_query.has_time_bucket and row.get("time_bucket_ms") is not None:
                point_dimensions["time_bucket"] = datetime.fromtimestamp(
                    row["time_bucket_ms"] / 1000, tz=timezone.utc
                ).isoformat()
            if self._metric_sample_key(metric_name, point_dimensions) not in overlap_keys:
                continue
            overlap_points.append(
                MetricDataPoint(
                    metric_name=metric_name,
                    dimensions=point_dimensions,
                    values={
                        str(aggregation): row[str(aggregation)]
                        for aggregation in aggregations
                        if row.get(str(aggregation)) is not None
                    },
                )
            )

        disjoint_points = self._merge_metric_points(
            self._metric_points_excluding_keys(hot_points, overlap_keys),
            self._metric_points_excluding_keys(cold_points, overlap_keys),
        )
        merged_points = self._merge_metric_points(overlap_points, disjoint_points)
        return PagedList(self._sort_and_limit_metric_points(merged_points, max_results), None)

    def _query_trace_metric_samples_hot(
        self,
        *,
        experiment_ids,
        view_type,
        metric_name: str,
        dimensions=None,
        filters=None,
        time_interval_seconds=None,
        start_time_ms=None,
        end_time_ms=None,
    ):
        return self._call_hot_store(
            "_query_trace_metric_samples",
            experiment_ids=experiment_ids,
            view_type=view_type,
            metric_name=metric_name,
            dimensions=dimensions,
            filters=filters,
            time_interval_seconds=time_interval_seconds,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )

    def _query_trace_metrics_hot_unvalidated(
        self,
        *,
        experiment_ids,
        view_type,
        metric_name: str,
        aggregations,
        dimensions=None,
        filters=None,
        time_interval_seconds=None,
        start_time_ms=None,
        end_time_ms=None,
        max_results=None,
        page_token=None,
    ):
        del page_token
        with self.tracking_store.ManagedSessionMaker() as session:
            rollup_points = self.tracking_store._query_sql_daily_rollup_metrics(
                session=session,
                experiment_ids=experiment_ids,
                view_type=view_type,
                metric_name=metric_name,
                aggregations=aggregations,
                dimensions=dimensions,
                filters=filters,
                time_interval_seconds=time_interval_seconds,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                max_results=max_results,
            )
            if rollup_points is not None:
                return PagedList(rollup_points, None)
            return PagedList(
                self.tracking_store._query_trace_metrics_raw(
                    session=session,
                    experiment_ids=experiment_ids,
                    view_type=view_type,
                    metric_name=metric_name,
                    aggregations=aggregations,
                    dimensions=dimensions,
                    filters=filters,
                    time_interval_seconds=time_interval_seconds,
                    start_time_ms=start_time_ms,
                    end_time_ms=end_time_ms,
                    max_results=max_results,
                ),
                None,
            )

    def _all_matching_trace_ids(
        self,
        *,
        experiment_ids: list[str] | None = None,
        filter_string: str | None = None,
        locations: list[str] | None = None,
        max_trace_ids: int = _MAX_CORRELATION_FALLBACK_TRACE_IDS,
    ) -> set[str]:
        trace_ids: set[str] = set()
        page_token = None
        while True:
            trace_infos, next_token = self.search_traces(
                experiment_ids=experiment_ids,
                filter_string=filter_string,
                max_results=SEARCH_MAX_RESULTS_THRESHOLD,
                order_by=["timestamp DESC"],
                page_token=page_token,
                locations=locations,
            )
            trace_ids.update(trace_info.trace_id for trace_info in trace_infos)
            if len(trace_ids) > max_trace_ids:
                raise MlflowException.invalid_parameter_value(
                    "Payload-filter correlation is limited to "
                    f"{max_trace_ids} traces in the hybrid backend."
                )
            if next_token is None or not trace_infos:
                break
            page_token = next_token
        return trace_ids

    def start_trace(self, trace_info):
        trace_info = deepcopy(trace_info)
        for key in self._reserved_hybrid_trace_tag_keys():
            trace_info.tags.pop(key, None)
        return self._call_hot_store("start_trace", trace_info)

    @_with_published_iceberg_cut
    def get_trace_info(self, trace_id: str):
        if not self._hybrid_enabled:
            return self._get_trace_info_cold(trace_id)
        try:
            return self._call_hot_store("get_trace_info", trace_id)
        except MlflowException as exc:
            if not self._is_not_found_exception(exc):
                raise
            return self._get_trace_info_cold(trace_id)

    @staticmethod
    def _reserved_hybrid_trace_tag_keys() -> set[str]:
        return set(_HYBRID_RESERVED_TRACE_TAG_KEYS)

    @staticmethod
    def _experiment_ids_for_hot_coverage(experiment_ids=None, locations=None) -> list[int]:
        resolved_experiment_ids = locations or experiment_ids or []
        try:
            return [int(experiment_id) for experiment_id in resolved_experiment_ids]
        except (TypeError, ValueError):
            return []

    def _hot_store_covers_time_range(
        self,
        *,
        experiment_ids=None,
        locations=None,
        start_time_ms: int | None,
        end_time_ms: int | None,
    ) -> bool:
        if start_time_ms is None or end_time_ms is None:
            return False
        if end_time_ms - start_time_ms <= _HOT_STORE_COVERAGE_MIN_RANGE_MS:
            return False
        experiment_ids_int = self._experiment_ids_for_hot_coverage(experiment_ids, locations)
        if not experiment_ids_int:
            return False
        with self.tracking_store.ManagedSessionMaker() as session:
            covered_experiment_ids = {
                experiment_id
                for experiment_id in experiment_ids_int
                if (
                    oldest_timestamp_ms := self.tracking_store
                    ._trace_query(session)
                    .with_entities(SqlTraceInfo.timestamp_ms)
                    .filter(SqlTraceInfo.experiment_id == experiment_id)
                    .order_by(SqlTraceInfo.timestamp_ms.asc())
                    .limit(1)
                    .scalar()
                )
                is not None
                and oldest_timestamp_ms <= start_time_ms
            }
        return set(experiment_ids_int) <= covered_experiment_ids

    def _search_time_bounds(self, filter_string: str | None) -> tuple[int | None, int | None]:
        trace_filters = []
        if filter_string:
            trace_filters = [
                parsed_filter
                for parsed_filter in SearchTraceUtils.parse_search_filter_for_search_traces(
                    filter_string
                )
                if parsed_filter["type"] == "attribute"
            ]
        return _trace_time_bounds_from_filters(trace_filters)

    def _clip_metric_time_range_to_trace_bounds(
        self,
        *,
        experiment_ids,
        start_time_ms: int | None,
        end_time_ms: int | None,
    ) -> tuple[int | None, int | None, bool]:
        if not experiment_ids:
            return start_time_ms, end_time_ms, False
        experiment_ids_int = self._experiment_ids_for_hot_coverage(experiment_ids)
        if not experiment_ids_int:
            return start_time_ms, end_time_ms, False
        hot_bounds, cold_bounds = self._trace_metric_storage_bounds(experiment_ids_int)
        bounds = [bounds for bounds in (hot_bounds, cold_bounds) if bounds[0] is not None]
        if not bounds:
            return start_time_ms, end_time_ms, True
        return self._clip_time_range_to_storage_bounds(
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
            oldest_timestamp_ms=min(bounds[0] for bounds in bounds),
            newest_timestamp_ms=max(bounds[1] for bounds in bounds),
        )

    def _trace_metric_storage_bounds(
        self, experiment_ids: list[int]
    ) -> tuple[tuple[int | None, int | None], tuple[int | None, int | None]]:
        with self.tracking_store.ManagedSessionMaker() as session:
            hot_bounds = [
                (
                    self.tracking_store
                    ._trace_query(session)
                    .with_entities(SqlTraceInfo.timestamp_ms)
                    .filter(SqlTraceInfo.experiment_id == experiment_id)
                    .order_by(SqlTraceInfo.timestamp_ms.asc())
                    .limit(1)
                    .scalar(),
                    self.tracking_store
                    ._trace_query(session)
                    .with_entities(SqlTraceInfo.timestamp_ms)
                    .filter(SqlTraceInfo.experiment_id == experiment_id)
                    .order_by(SqlTraceInfo.timestamp_ms.desc())
                    .limit(1)
                    .scalar(),
                )
                for experiment_id in experiment_ids
            ]
            archived_bounds = []
            for experiment_id in experiment_ids:
                base_query = session.query(SqlArchivedTraceLocator.request_time_ms).filter(
                    SqlArchivedTraceLocator.experiment_id == experiment_id
                )
                if workspace := self._get_active_workspace():
                    base_query = base_query.filter(SqlArchivedTraceLocator.workspace == workspace)
                archived_bounds.append((
                    base_query
                    .order_by(SqlArchivedTraceLocator.request_time_ms.asc())
                    .limit(1)
                    .scalar(),
                    base_query
                    .order_by(SqlArchivedTraceLocator.request_time_ms.desc())
                    .limit(1)
                    .scalar(),
                ))
        hot_oldest_timestamp_ms = min(
            (bounds[0] for bounds in hot_bounds if bounds[0] is not None), default=None
        )
        hot_newest_timestamp_ms = max(
            (bounds[1] for bounds in hot_bounds if bounds[1] is not None), default=None
        )
        archived_oldest_timestamp_ms = min(
            (bounds[0] for bounds in archived_bounds if bounds[0] is not None), default=None
        )
        archived_newest_timestamp_ms = max(
            (bounds[1] for bounds in archived_bounds if bounds[1] is not None), default=None
        )
        return (
            (hot_oldest_timestamp_ms, hot_newest_timestamp_ms),
            (archived_oldest_timestamp_ms, archived_newest_timestamp_ms),
        )

    @staticmethod
    def _clip_time_range_to_storage_bounds(
        *,
        start_time_ms: int | None,
        end_time_ms: int | None,
        oldest_timestamp_ms: int | None,
        newest_timestamp_ms: int | None,
    ) -> tuple[int | None, int | None, bool]:
        if oldest_timestamp_ms is None or newest_timestamp_ms is None:
            return start_time_ms, end_time_ms, True
        if start_time_ms is None or start_time_ms <= oldest_timestamp_ms:
            clipped_start_time_ms, _ = _day_bounds_ms(_timestamp_ms_to_day(oldest_timestamp_ms))
        else:
            clipped_start_time_ms = start_time_ms
        if end_time_ms is None or end_time_ms >= newest_timestamp_ms:
            _, clipped_end_time_ms = _day_bounds_ms(_timestamp_ms_to_day(newest_timestamp_ms))
        else:
            clipped_end_time_ms = end_time_ms
        return (
            clipped_start_time_ms,
            clipped_end_time_ms,
            clipped_start_time_ms > clipped_end_time_ms,
        )

    @_with_published_iceberg_cut
    def search_traces(
        self,
        experiment_ids=None,
        filter_string=None,
        max_results=SEARCH_TRACES_DEFAULT_MAX_RESULTS,
        order_by=None,
        page_token=None,
        model_id=None,
        locations=None,
    ):
        if not self._hybrid_enabled:
            return self._search_traces_cold(
                experiment_ids=experiment_ids,
                filter_string=filter_string,
                max_results=max_results,
                order_by=order_by,
                page_token=page_token,
                model_id=model_id,
                locations=locations,
            )
        start_time_ms, end_time_ms = self._search_time_bounds(filter_string)
        if self._hot_store_covers_time_range(
            experiment_ids=experiment_ids,
            locations=locations,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        ):
            return self._call_hot_store(
                "search_traces",
                experiment_ids=experiment_ids,
                filter_string=filter_string,
                max_results=max_results,
                order_by=order_by,
                page_token=page_token,
                model_id=model_id,
                locations=locations,
            )
        fetch_limit = (
            SearchTraceUtils.parse_start_offset_from_page_token(page_token) + max_results + 1
        )
        search_kwargs = {
            "fetch_limit": fetch_limit,
            "experiment_ids": experiment_ids,
            "filter_string": filter_string,
            "order_by": order_by,
            "model_id": model_id,
            "locations": locations,
        }
        hot_trace_infos = None
        cold_search_kwargs = search_kwargs
        experiment_ids_int = self._experiment_ids_for_hot_coverage(experiment_ids, locations)
        if _trace_search_uses_timestamp_desc_order(order_by or []) and experiment_ids_int:
            hot_bounds, cold_bounds = self._trace_metric_storage_bounds(experiment_ids_int)
            stores_are_time_ordered = hot_bounds[0] is not None and (
                cold_bounds[1] is None or hot_bounds[0] > cold_bounds[1]
            )
            if stores_are_time_ordered:
                if max_results < SEARCH_MAX_RESULTS_THRESHOLD:
                    hot_page, _ = self._call_hot_store(
                        "search_traces",
                        experiment_ids=experiment_ids,
                        filter_string=filter_string,
                        max_results=max_results + 1,
                        order_by=order_by,
                        page_token=page_token,
                        model_id=model_id,
                        locations=locations,
                    )
                    if len(hot_page) > max_results:
                        return hot_page[:max_results], SearchTraceUtils.create_page_token(
                            SearchTraceUtils.parse_start_offset_from_page_token(page_token)
                            + max_results
                        )
                    if cold_bounds[0] is None:
                        return hot_page, None
                hot_trace_infos = self._collect_trace_infos_from_search(
                    lambda **kwargs: self._call_hot_store("search_traces", **kwargs),
                    **search_kwargs,
                )
                if len(hot_trace_infos) >= fetch_limit:
                    return self._merge_hot_and_cold_trace_infos(
                        hot_trace_infos=hot_trace_infos,
                        cold_trace_infos=[],
                        order_by=order_by,
                        max_results=max_results,
                        page_token=page_token,
                    )
                if cold_bounds[0] is None:
                    return self._merge_hot_and_cold_trace_infos(
                        hot_trace_infos=hot_trace_infos,
                        cold_trace_infos=[],
                        order_by=order_by,
                        max_results=max_results,
                        page_token=page_token,
                    )
                cold_search_kwargs = {
                    **search_kwargs,
                    "filter_string": self._combine_filter_strings(
                        filter_string,
                        f"attributes.timestamp_ms >= {cold_bounds[0]}",
                        f"attributes.timestamp_ms <= {cold_bounds[1]}",
                    ),
                }

        if hot_trace_infos is None:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                hot_trace_infos_future = _submit_with_current_context(
                    executor,
                    self._collect_trace_infos_from_search,
                    lambda **kwargs: self._call_hot_store("search_traces", **kwargs),
                    **search_kwargs,
                )
                cold_trace_infos_future = _submit_with_current_context(
                    executor,
                    self._collect_trace_infos_from_search,
                    lambda **kwargs: self._search_traces_cold(**kwargs),
                    **search_kwargs,
                )
                hot_trace_infos = hot_trace_infos_future.result()
                cold_trace_infos = cold_trace_infos_future.result()
        else:
            cold_trace_infos = self._collect_trace_infos_from_search(
                lambda **kwargs: self._search_traces_cold(**kwargs),
                **cold_search_kwargs,
            )

        hot_trace_ids = {trace_info.trace_id for trace_info in hot_trace_infos}
        cold_candidate_ids = [
            trace_info.trace_id
            for trace_info in cold_trace_infos
            if trace_info.trace_id not in hot_trace_ids
        ]
        hot_candidate_trace_infos = {
            trace_info.trace_id: trace_info
            for trace_info in self._call_hot_store(
                "batch_get_trace_infos", cold_candidate_ids, location=None
            )
        }

        payload_dependent = self._has_payload_dependent_span_filters(filter_string)
        trace_filters = []
        assessment_filters = []
        if payload_dependent and filter_string:
            for parsed_filter in SearchTraceUtils.parse_search_filter_for_search_traces(
                filter_string
            ):
                if parsed_filter["type"] in {"feedback", "expectation"}:
                    assessment_filters.append(parsed_filter)
                elif parsed_filter["type"] == "span" and (
                    parsed_filter["key"] == "content"
                    or parsed_filter["key"].startswith("attributes.")
                ):
                    continue
                elif parsed_filter["type"] != "span":
                    trace_filters.append(parsed_filter)

        session_assessments_by_session_id: dict[str, list[Assessment]] = {}
        if payload_dependent and assessment_filters:
            for trace_info in hot_candidate_trace_infos.values():
                for assessment in trace_info.assessments:
                    if session_id := (assessment.metadata or {}).get(
                        TraceMetadataKey.TRACE_SESSION
                    ):
                        session_assessments_by_session_id.setdefault(session_id, []).append(
                            assessment
                        )

        def _keep_cold_candidate(trace_info: TraceInfo) -> bool:
            hot_trace_info = hot_candidate_trace_infos.get(trace_info.trace_id)
            if hot_trace_info is None:
                return True
            if not payload_dependent:
                return True
            if not all(
                SearchTraceUtils._does_trace_match_clause(hot_trace_info, parsed_filter)
                for parsed_filter in trace_filters
            ):
                return False
            if assessment_filters:
                return self._trace_matches_assessment_filters(
                    hot_trace_info.assessments,
                    session_assessments_by_session_id.get(
                        hot_trace_info.trace_metadata.get(TraceMetadataKey.TRACE_SESSION),
                        [],
                    ),
                    assessment_filters,
                )
            return True

        cold_trace_infos = [
            trace_info for trace_info in cold_trace_infos if _keep_cold_candidate(trace_info)
        ]
        return self._merge_hot_and_cold_trace_infos(
            hot_trace_infos=hot_trace_infos,
            cold_trace_infos=cold_trace_infos,
            order_by=order_by,
            max_results=max_results,
            page_token=page_token,
            authoritative_trace_infos_by_id=hot_candidate_trace_infos,
        )

    @_with_published_iceberg_cut
    def find_completed_sessions(
        self,
        experiment_id: str,
        min_last_trace_timestamp_ms: int,
        max_last_trace_timestamp_ms: int,
        max_results=None,
        filter_string=None,
    ):
        if not self._hybrid_enabled:
            return self._find_completed_sessions_cold(
                experiment_id=experiment_id,
                min_last_trace_timestamp_ms=min_last_trace_timestamp_ms,
                max_last_trace_timestamp_ms=max_last_trace_timestamp_ms,
                max_results=max_results,
                filter_string=filter_string,
            )
        session_query_kwargs = {
            "experiment_id": experiment_id,
            "min_last_trace_timestamp_ms": min_last_trace_timestamp_ms,
            "max_last_trace_timestamp_ms": max_last_trace_timestamp_ms,
            "max_results": None,
            "filter_string": filter_string,
        }
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            hot_sessions_future = _submit_with_current_context(
                executor, self._call_hot_store, "find_completed_sessions", **session_query_kwargs
            )
            cold_sessions_future = _submit_with_current_context(
                executor, self._find_completed_sessions_cold, **session_query_kwargs
            )
            hot_sessions = hot_sessions_future.result()
            cold_sessions = cold_sessions_future.result()
        sessions_by_id = {
            completed_session.session_id: completed_session for completed_session in hot_sessions
        }
        for completed_session in cold_sessions:
            if completed_session.session_id in sessions_by_id:
                existing = sessions_by_id[completed_session.session_id]
                sessions_by_id[completed_session.session_id] = CompletedSession(
                    session_id=completed_session.session_id,
                    first_trace_timestamp_ms=min(
                        existing.first_trace_timestamp_ms,
                        completed_session.first_trace_timestamp_ms,
                    ),
                    last_trace_timestamp_ms=max(
                        existing.last_trace_timestamp_ms,
                        completed_session.last_trace_timestamp_ms,
                    ),
                )
            else:
                sessions_by_id[completed_session.session_id] = completed_session
        merged_sessions = sorted(
            sessions_by_id.values(),
            key=lambda completed_session: (
                completed_session.last_trace_timestamp_ms,
                completed_session.session_id,
            ),
        )
        return merged_sessions if max_results is None else merged_sessions[:max_results]

    @_with_published_iceberg_cut
    def query_trace_metrics(
        self,
        experiment_ids,
        view_type,
        metric_name: str,
        aggregations,
        dimensions=None,
        filters=None,
        time_interval_seconds=None,
        start_time_ms=None,
        end_time_ms=None,
        max_results=None,
        page_token=None,
    ):
        if not self._hybrid_enabled:
            return self._query_trace_metrics_cold(
                experiment_ids=experiment_ids,
                view_type=view_type,
                metric_name=metric_name,
                aggregations=aggregations,
                dimensions=dimensions,
                filters=filters,
                time_interval_seconds=time_interval_seconds,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                max_results=max_results,
                page_token=page_token,
            )
        if view_type == MetricViewType.TRACES and self._hot_store_covers_time_range(
            experiment_ids=experiment_ids,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        ):
            return self._call_hot_store(
                "query_trace_metrics",
                experiment_ids=experiment_ids,
                view_type=view_type,
                metric_name=metric_name,
                aggregations=aggregations,
                dimensions=dimensions,
                filters=filters,
                time_interval_seconds=time_interval_seconds,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
                max_results=max_results,
                page_token=page_token,
            )
        validate_query_trace_metrics_params(view_type, metric_name, aggregations, dimensions)
        self._validate_metric_query_shape(
            time_interval_seconds=time_interval_seconds,
            start_time_ms=start_time_ms,
            end_time_ms=end_time_ms,
        )
        hot_time_range = (start_time_ms, end_time_ms, False)
        cold_time_range = (start_time_ms, end_time_ms, False)
        combined_time_range = (start_time_ms, end_time_ms, False)
        if view_type == MetricViewType.TRACES:
            experiment_ids_int = self._experiment_ids_for_hot_coverage(experiment_ids)
            if experiment_ids_int:
                hot_bounds, cold_bounds = self._trace_metric_storage_bounds(experiment_ids_int)
                hot_time_range = self._clip_time_range_to_storage_bounds(
                    start_time_ms=start_time_ms,
                    end_time_ms=end_time_ms,
                    oldest_timestamp_ms=hot_bounds[0],
                    newest_timestamp_ms=hot_bounds[1],
                )
                cold_time_range = self._clip_time_range_to_storage_bounds(
                    start_time_ms=start_time_ms,
                    end_time_ms=end_time_ms,
                    oldest_timestamp_ms=cold_bounds[0],
                    newest_timestamp_ms=cold_bounds[1],
                )
                populated_bounds = [
                    bounds for bounds in (hot_bounds, cold_bounds) if bounds[0] is not None
                ]
                if populated_bounds:
                    combined_time_range = self._clip_time_range_to_storage_bounds(
                        start_time_ms=start_time_ms,
                        end_time_ms=end_time_ms,
                        oldest_timestamp_ms=min(bounds[0] for bounds in populated_bounds),
                        newest_timestamp_ms=max(bounds[1] for bounds in populated_bounds),
                    )
                else:
                    combined_time_range = (start_time_ms, end_time_ms, True)
            else:
                combined_time_range = self._clip_metric_time_range_to_trace_bounds(
                    experiment_ids=experiment_ids,
                    start_time_ms=start_time_ms,
                    end_time_ms=end_time_ms,
                )
        start_time_ms, end_time_ms, no_traces_in_range = combined_time_range
        if no_traces_in_range:
            return PagedList([], None)
        aggregate_query_aggregations = self._avg_merge_aggregations(aggregations)
        query_kwargs = {
            "experiment_ids": experiment_ids,
            "view_type": view_type,
            "metric_name": metric_name,
            "aggregations": aggregate_query_aggregations,
            "dimensions": dimensions,
            "filters": filters,
            "time_interval_seconds": time_interval_seconds,
            "start_time_ms": start_time_ms,
            "end_time_ms": end_time_ms,
            "max_results": max_results,
            "page_token": page_token,
        }
        hot_query_kwargs = {
            **query_kwargs,
            "start_time_ms": hot_time_range[0],
            "end_time_ms": hot_time_range[1],
        }
        cold_query_kwargs = {
            **query_kwargs,
            "start_time_ms": cold_time_range[0],
            "end_time_ms": cold_time_range[1],
        }
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            hot_points_future = (
                _submit_with_current_context(
                    executor, self._query_trace_metrics_hot_unvalidated, **hot_query_kwargs
                )
                if not hot_time_range[2]
                else None
            )
            cold_points_future = (
                _submit_with_current_context(
                    executor,
                    self._query_trace_metrics_cold,
                    **cold_query_kwargs,
                    skip_validation=True,
                )
                if not cold_time_range[2]
                else None
            )
            hot_points = hot_points_future.result() if hot_points_future is not None else []
            try:
                cold_points = cold_points_future.result() if cold_points_future is not None else []
            except MlflowException as exc:
                if exc.error_code == ErrorCode.Name(INVALID_PARAMETER_VALUE):
                    return PagedList(
                        self._strip_helper_metric_values(hot_points, aggregations), None
                    )
                raise
        hot_has_data = self._metric_points_have_data(hot_points)
        cold_has_data = self._metric_points_have_data(cold_points)
        if hot_has_data and cold_has_data:
            if not self._metric_point_keys_overlap(hot_points, cold_points):
                merged_points = self._merge_metric_points(hot_points, cold_points)
                return PagedList(
                    self._strip_helper_metric_values(merged_points, aggregations), None
                )
            if self._can_merge_metric_points(aggregations) or self._can_merge_avg_metric_points(
                aggregations
            ):
                merged_points = self._merge_metric_points(hot_points, cold_points)
                return PagedList(
                    self._strip_helper_metric_values(merged_points, aggregations), None
                )
            overlap_keys = self._metric_point_key_intersection(hot_points, cold_points)
            sample_start_time_ms, sample_end_time_ms = self._metric_overlap_sample_time_bounds(
                overlap_keys=overlap_keys,
                time_interval_seconds=time_interval_seconds,
                start_time_ms=start_time_ms,
                end_time_ms=end_time_ms,
            )
            sample_query_kwargs = {
                key: value
                for key, value in query_kwargs.items()
                if key not in {"aggregations", "max_results", "page_token"}
            }
            sample_query_kwargs["start_time_ms"] = sample_start_time_ms
            sample_query_kwargs["end_time_ms"] = sample_end_time_ms
            hot_samples = self._query_trace_metric_samples_hot(**sample_query_kwargs)
            return self._merge_metric_points_with_overlap_arrow(
                view_type=view_type,
                metric_name=metric_name,
                aggregations=aggregations,
                hot_points=hot_points,
                cold_points=cold_points,
                hot_samples=hot_samples,
                overlap_keys=overlap_keys,
                max_results=max_results,
                query_kwargs=sample_query_kwargs,
            )
        if cold_has_data:
            return PagedList(self._strip_helper_metric_values(cold_points, aggregations), None)
        if hot_has_data:
            return PagedList(self._strip_helper_metric_values(hot_points, aggregations), None)
        return PagedList(
            self._strip_helper_metric_values(cold_points or hot_points, aggregations), None
        )

    def set_trace_tag(self, trace_id: str, key: str, value: str):
        if key in self._reserved_hybrid_trace_tag_keys():
            raise MlflowException.invalid_parameter_value(
                f"Trace tag '{key}' is managed by the hybrid trace backend."
            )
        return self._call_hot_store("set_trace_tag", trace_id, key, value)

    def delete_trace_tag(self, trace_id: str, key: str):
        if key in self._reserved_hybrid_trace_tag_keys():
            raise MlflowException.invalid_parameter_value(
                f"Trace tag '{key}' is managed by the hybrid trace backend."
            )
        return self._call_hot_store("delete_trace_tag", trace_id, key)

    def delete_traces(
        self,
        experiment_id: str,
        max_timestamp_millis: int | None = None,
        max_traces: int | None = None,
        trace_ids=None,
    ):
        deleted_db_backed_count = 0
        selected_archived_traces = []
        selected_trace_ids: list[str] = []
        with self.tracking_store.ManagedSessionMaker(read_only=False) as session:
            self._lock_sql_rollup_experiments(session, [int(experiment_id)])
            filters = [SqlTraceInfo.experiment_id == int(experiment_id)]
            if max_timestamp_millis is not None:
                filters.append(SqlTraceInfo.timestamp_ms <= max_timestamp_millis)
            if trace_ids:
                filters.append(SqlTraceInfo.request_id.in_(trace_ids))
            if max_traces is not None:
                limited_subquery = (
                    self.tracking_store
                    ._trace_query(session, for_update_or_delete=True)
                    .with_entities(SqlTraceInfo.request_id)
                    .filter(*filters)
                    .order_by(SqlTraceInfo.timestamp_ms, SqlTraceInfo.request_id)
                    .limit(max_traces)
                    .subquery()
                )
                filters.append(SqlTraceInfo.request_id.in_(select(limited_subquery.c.request_id)))

            selected_trace_ids = self.tracking_store._select_trace_ids_for_delete(
                session=session,
                filters=filters,
            )
            if selected_trace_ids:
                selected_archived_traces = self.tracking_store._select_archived_traces_for_delete(
                    session=session,
                    trace_ids=selected_trace_ids,
                )
                archived_trace_ids = {
                    selected_trace.trace_id for selected_trace in selected_archived_traces
                }
                db_backed_trace_ids = [
                    trace_id
                    for trace_id in selected_trace_ids
                    if trace_id not in archived_trace_ids
                ]
                self._invalidate_sql_rollups_for_trace_ids(session, selected_trace_ids)
                if db_backed_trace_ids:
                    deleted_db_backed_count = (
                        session
                        .query(SqlTraceInfo)
                        .filter(SqlTraceInfo.request_id.in_(db_backed_trace_ids))
                        .delete(synchronize_session=False)
                    )
                    self.tracking_store._delete_review_queue_items_for_traces(
                        session, db_backed_trace_ids
                    )

        if not selected_archived_traces:
            selected_trace_id_set = set(selected_trace_ids)
            remaining_limit = (
                None if max_traces is None else max(0, max_traces - deleted_db_backed_count)
            )
            cold_only_trace_ids = self._select_cold_trace_ids_for_delete(
                experiment_id=experiment_id,
                max_timestamp_millis=max_timestamp_millis,
                max_traces=remaining_limit,
                trace_ids=trace_ids,
                excluded_trace_ids=selected_trace_id_set,
            )
            if not cold_only_trace_ids:
                return deleted_db_backed_count
            cold_trace_infos = self._batch_get_trace_infos_cold(cold_only_trace_ids)
            archive_backed_infos = [
                trace_info
                for trace_info in cold_trace_infos
                if self._is_archive_backed_trace_info(trace_info)
            ]
            non_archive_trace_ids = {
                trace_info.trace_id
                for trace_info in cold_trace_infos
                if not self._is_archive_backed_trace_info(trace_info)
            }
            with (
                self.acquire_iceberg_trace_write_lock(),
                self._resources.write_lock,
            ):
                self._begin_iceberg_trace_publication_barrier()
                deleted_cold_trace_ids = set(non_archive_trace_ids)
                if archive_backed_infos:
                    deleted_cold_trace_ids.update(
                        self.tracking_store._delete_archived_trace_payloads([
                            _TraceDeleteSelection(
                                trace_id=trace_info.trace_id,
                                archived_artifact_uri=get_archive_uri_for_trace(trace_info),
                            )
                            for trace_info in archive_backed_infos
                        ])
                    )
                if deleted_cold_trace_ids:
                    self._delete_iceberg_rows_for_trace_ids(sorted(deleted_cold_trace_ids))
                    self._publish_iceberg_trace_deletion(
                        sorted(deleted_cold_trace_ids),
                        delete_hot_rows=False,
                        allow_blocked=True,
                    )
                else:
                    self._clear_iceberg_trace_publication_barrier()
            return deleted_db_backed_count + len(deleted_cold_trace_ids)

        with (
            self.acquire_iceberg_trace_write_lock(),
            self._resources.write_lock,
        ):
            self._begin_iceberg_trace_publication_barrier()
            deleted_archived_trace_ids = self.tracking_store._delete_archived_trace_payloads(
                selected_archived_traces
            )
            if not deleted_archived_trace_ids:
                self._clear_iceberg_trace_publication_barrier()
                return deleted_db_backed_count
            self._delete_iceberg_rows_for_trace_ids(deleted_archived_trace_ids)
            deleted_archived_count = self._publish_iceberg_trace_deletion(
                deleted_archived_trace_ids,
                delete_hot_rows=True,
                allow_blocked=True,
            )
        return deleted_db_backed_count + deleted_archived_count

    def create_assessment(self, assessment):
        return self._call_hot_store("create_assessment", assessment)

    def get_assessment(self, trace_id: str, assessment_id: str):
        return self._call_hot_store("get_assessment", trace_id, assessment_id)

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
        return self._call_hot_store(
            "update_assessment",
            trace_id=trace_id,
            assessment_id=assessment_id,
            name=name,
            expectation=expectation,
            feedback=feedback,
            rationale=rationale,
            metadata=metadata,
        )

    def delete_assessment(self, trace_id: str, assessment_id: str):
        return self._call_hot_store("delete_assessment", trace_id, assessment_id)

    @_with_published_iceberg_cut
    def calculate_trace_filter_correlation(
        self,
        experiment_ids,
        filter_string1: str,
        filter_string2: str,
        base_filter: str | None = None,
    ):
        if not self._hybrid_enabled:
            return self._calculate_trace_filter_correlation_cold(
                experiment_ids=experiment_ids,
                filter_string1=filter_string1,
                filter_string2=filter_string2,
                base_filter=base_filter,
            )
        if any(
            self._has_payload_dependent_span_filters(filter_string)
            for filter_string in (filter_string1, filter_string2, base_filter)
        ):
            total_trace_ids = self._all_matching_trace_ids(
                experiment_ids=experiment_ids,
                filter_string=base_filter,
            )
            filter1_trace_ids = self._all_matching_trace_ids(
                experiment_ids=experiment_ids,
                filter_string=self._combine_filter_strings(base_filter, filter_string1),
            )
            filter2_trace_ids = self._all_matching_trace_ids(
                experiment_ids=experiment_ids,
                filter_string=self._combine_filter_strings(base_filter, filter_string2),
            )
            joint_trace_ids = self._all_matching_trace_ids(
                experiment_ids=experiment_ids,
                filter_string=self._combine_filter_strings(
                    base_filter,
                    filter_string1,
                    filter_string2,
                ),
            )
            npmi_result = calculate_npmi_from_counts(
                joint_count=len(joint_trace_ids),
                filter1_count=len(filter1_trace_ids),
                filter2_count=len(filter2_trace_ids),
                total_count=len(total_trace_ids),
            )
            return TraceFilterCorrelationResult(
                npmi=npmi_result.npmi,
                npmi_smoothed=npmi_result.npmi_smoothed,
                filter1_count=len(filter1_trace_ids),
                filter2_count=len(filter2_trace_ids),
                joint_count=len(joint_trace_ids),
                total_count=len(total_trace_ids),
            )

        return self._call_hot_store(
            "calculate_trace_filter_correlation",
            experiment_ids=experiment_ids,
            filter_string1=filter_string1,
            filter_string2=filter_string2,
            base_filter=base_filter,
        )

    def log_spans(self, location: str, spans, tracking_uri=None):
        return self._call_hot_store("log_spans", location, spans, tracking_uri=tracking_uri)

    async def log_spans_async(self, location: str, spans):
        return await self._call_hot_store("log_spans_async", location, spans)

    @_with_published_iceberg_cut
    def get_trace(self, trace_id: str, *, allow_partial: bool = False):
        if not self._hybrid_enabled:
            return self._get_trace_cold(trace_id, allow_partial=allow_partial)
        try:
            trace_info = self._call_hot_store("get_trace_info", trace_id)
            hot_trace_exists = True
            cold_trace_row = None
        except MlflowException as exc:
            if not self._is_not_found_exception(exc):
                raise
            trace_info, cold_trace_row = self._get_trace_info_and_row_cold(trace_id)
            hot_trace_exists = False

        if not self._is_archive_backed_trace_info(trace_info):
            if hot_trace_exists:
                return self._call_hot_store("get_trace", trace_id, allow_partial=allow_partial)
            assert cold_trace_row is not None
            return self._get_trace_from_cold_info(
                cold_trace_row,
                trace_info,
                allow_partial=allow_partial,
            )
        try:
            spans = (
                get_artifact_repository(self._validated_archive_uri_for_trace(trace_info))
                .download_archived_trace_data()
                .spans
            )
            return Trace(info=trace_info, data=TraceData(spans=spans))
        except Exception as exc:
            if self.tracking_store._is_missing_archived_trace_payload_delete_error(exc):
                return self._trace_with_empty_spans(trace_info)
            _logger.warning(
                "Failed to load archived trace payload for %s.",
                trace_id,
                exc_info=True,
            )
            raise

    @_with_published_iceberg_cut
    def batch_get_traces(self, trace_ids, location: str | None = None):
        if not self._hybrid_enabled:
            return self._batch_get_traces_cold(trace_ids, location=location)
        trace_ids = list(trace_ids)
        if not trace_ids:
            return []

        trace_infos = self._call_hot_store("batch_get_trace_infos", trace_ids, location=location)
        trace_infos_by_id = {trace_info.trace_id: trace_info for trace_info in trace_infos}
        missing_trace_ids = [
            trace_id for trace_id in trace_ids if trace_id not in trace_infos_by_id
        ]
        if missing_trace_ids:
            for trace_info in self._batch_get_trace_infos_cold(
                missing_trace_ids, location=location
            ):
                trace_infos_by_id[trace_info.trace_id] = trace_info
        hot_trace_ids = [
            trace_id
            for trace_id in trace_ids
            if (trace_info := trace_infos_by_id.get(trace_id)) is not None
            and not self._is_archive_backed_trace_info(trace_info)
        ]
        archive_backed_trace_ids = [
            trace_id
            for trace_id in trace_ids
            if (trace_info := trace_infos_by_id.get(trace_id)) is not None
            and self._is_archive_backed_trace_info(trace_info)
        ]

        traces_by_id = {
            trace.info.trace_id: trace
            for trace in self._call_hot_store("batch_get_traces", hot_trace_ids, location=location)
        }
        for trace_id in archive_backed_trace_ids:
            if trace_id in traces_by_id:
                continue
            trace_info = trace_infos_by_id.get(trace_id)
            if trace_info is None:
                continue
            try:
                spans = (
                    get_artifact_repository(self._validated_archive_uri_for_trace(trace_info))
                    .download_archived_trace_data()
                    .spans
                )
                traces_by_id[trace_id] = Trace(info=trace_info, data=TraceData(spans=spans))
            except Exception as exc:
                if self.tracking_store._is_missing_archived_trace_payload_delete_error(exc):
                    traces_by_id[trace_id] = self._trace_with_empty_spans(trace_info)
                    continue
                _logger.warning(
                    "Failed to load archived trace payload for %s during batch_get_traces.",
                    trace_id,
                    exc_info=True,
                )
                raise

        return [traces_by_id[trace_id] for trace_id in trace_ids if trace_id in traces_by_id]

    @_with_published_iceberg_cut
    def batch_get_trace_infos(self, trace_ids, location: str | None = None):
        if not self._hybrid_enabled:
            return self._batch_get_trace_infos_cold(trace_ids, location=location)
        trace_ids = list(trace_ids)
        hot_trace_infos = self._call_hot_store(
            "batch_get_trace_infos", trace_ids, location=location
        )
        hot_trace_infos_by_id = {trace_info.trace_id: trace_info for trace_info in hot_trace_infos}
        missing_trace_ids = [
            trace_id for trace_id in trace_ids if trace_id not in hot_trace_infos_by_id
        ]
        cold_trace_infos_by_id = {
            trace_info.trace_id: trace_info
            for trace_info in self._batch_get_trace_infos_cold(missing_trace_ids, location=location)
        }
        return [
            hot_trace_infos_by_id.get(trace_id) or cold_trace_infos_by_id.get(trace_id)
            for trace_id in trace_ids
            if hot_trace_infos_by_id.get(trace_id) or cold_trace_infos_by_id.get(trace_id)
        ]

    def _select_publishable_archived_projections(
        self, projections: list[_StagedArchivedTraceProjection]
    ) -> list[_StagedArchivedTraceProjection]:
        publishable_trace_ids = set(
            self._select_publishable_archived_trace_ids(
                traces=[
                    {
                        "trace_id": projection.trace_id,
                        "db_payload_generation": projection.db_payload_generation,
                    }
                    for projection in projections
                ]
            )
        )
        for projection in projections:
            if projection.trace_id not in publishable_trace_ids:
                self._delete_unreferenced_archived_trace_payload(
                    trace_id=projection.trace_id,
                    artifact_uri=projection.artifact_uri,
                    artifact_repo=projection.artifact_repo
                    or get_artifact_repository(projection.artifact_uri),
                )
        return [
            projection for projection in projections if projection.trace_id in publishable_trace_ids
        ]

    def _cleanup_unpublished_archived_projections(
        self, projections: list[_StagedArchivedTraceProjection]
    ) -> None:
        if not projections:
            return
        locators = self.get_archived_trace_locators([
            projection.trace_id for projection in projections
        ])
        unpublished_projections = [
            projection
            for projection in projections
            if locators.get(projection.trace_id, {}).get("archive_uri") != projection.artifact_uri
        ]
        if not unpublished_projections:
            return
        self._delete_iceberg_rows_for_trace_ids(
            [projection.trace_id for projection in unpublished_projections],
            trace_partition_keys=self._trace_rollup_partition_keys([
                projection.trace_row for projection in unpublished_projections
            ]),
            span_partition_keys=self._span_rollup_partition_keys([
                row for projection in unpublished_projections for row in projection.span_rows
            ]),
            assessment_partition_keys=self._assessment_rollup_partition_keys([
                projection.trace_row for projection in unpublished_projections
            ]),
        )
        for projection in unpublished_projections:
            self._delete_unreferenced_archived_trace_payload(
                trace_id=projection.trace_id,
                artifact_uri=projection.artifact_uri,
                artifact_repo=projection.artifact_repo
                or get_artifact_repository(projection.artifact_uri),
            )
        self._publish_current_iceberg_cut(allow_blocked=True)

    def archive_traces(
        self,
        *,
        resolved_trace_archival_location: str,
        broader_retention: str,
        long_retention_allowlist: set[str] | list[str] | None = None,
        max_traces_per_pass: int | None = None,
        delete_payload_after_retention: bool = False,
    ) -> int:
        if max_traces_per_pass is not None and max_traces_per_pass <= 0:
            raise MlflowException.invalid_parameter_value(
                f"`max_traces_per_pass` must be a positive integer, received {max_traces_per_pass}."
            )
        if not resolved_trace_archival_location:
            raise MlflowException.invalid_parameter_value(
                "`resolved_trace_archival_location` must be provided."
            )
        # ponytail: keep the existing validation shape even though the hybrid Iceberg path
        # currently writes cold data to Iceberg directly instead of using this object-store root.
        _validate_trace_archival_location(
            resolved_trace_archival_location,
            parameter_name="resolved_trace_archival_location",
        )
        if not broader_retention:
            raise MlflowException.invalid_parameter_value("`broader_retention` must be provided.")
        broader_retention = _validate_trace_archival_retention_string(
            broader_retention, parameter_name="broader_retention"
        )
        now_millis = self.tracking_store._get_archive_traces_now_millis()
        long_retention_allowlist = {
            str(experiment_id) for experiment_id in long_retention_allowlist or []
        }
        if long_retention_allowlist:
            raise MlflowException.invalid_parameter_value(
                "The Iceberg trace backend does not support `long_retention_allowlist`."
            )
        broader_retention_millis = _parse_trace_archival_duration_millis(broader_retention)
        if broader_retention_millis is None:
            raise MlflowException(
                "Trace archival config resolution returned no archival retention.",
                error_code=INTERNAL_ERROR,
            )

        pass_start_time = time.perf_counter()
        candidate_selection_start_time = time.perf_counter()
        with self.tracking_store.ManagedSessionMaker() as session:
            archive_now_requests, candidates = self.tracking_store._plan_trace_archival(
                session=session,
                now_millis=now_millis,
                broader_retention_millis=broader_retention_millis,
                long_retention_allowlist=set(),
                max_traces_per_pass=max_traces_per_pass,
            )
        candidate_selection_ms = _elapsed_ms(candidate_selection_start_time)
        _logger.info(
            "Iceberg archive pass discovered %s candidate trace(s) in %s ms.",
            len(candidates),
            candidate_selection_ms,
        )

        archived_count = 0
        retryable_failure_experiment_ids: set[str] = set()
        archive_now_experiment_ids = {request.experiment_id for request in archive_now_requests}
        candidates_to_archive = (
            candidates if max_traces_per_pass is None else candidates[:max_traces_per_pass]
        )

        archive_project_batch_size = _iceberg_archive_project_batch_size()
        archive_rollup_refresh_chunks = _iceberg_archive_rollup_refresh_chunks()
        archive_max_workers = _iceberg_archive_max_workers()
        archive_experiment_max_workers = _iceberg_archive_experiment_max_workers()
        candidates_by_experiment: dict[str, list[_TraceArchiveCandidate]] = {}
        for candidate in candidates_to_archive:
            candidates_by_experiment.setdefault(candidate.experiment_id, []).append(candidate)

        try:
            archived_count, retryable_failure_experiment_ids = (
                self._archive_trace_candidates_by_experiment(
                    candidates_by_experiment=candidates_by_experiment,
                    resolved_trace_archival_location=resolved_trace_archival_location,
                    archive_now_experiment_ids=archive_now_experiment_ids,
                    archive_project_batch_size=archive_project_batch_size,
                    archive_max_workers=archive_max_workers,
                    archive_experiment_max_workers=archive_experiment_max_workers,
                    archive_rollup_refresh_chunks=archive_rollup_refresh_chunks,
                )
            )
        finally:
            self.tracking_store._clear_completed_archive_now_requests(
                archive_now_requests=archive_now_requests,
                now_millis=now_millis,
                retryable_failure_experiment_ids=retryable_failure_experiment_ids,
            )

        payload_expired_trace_ids: list[str] = []
        payload_cleanup_ms = 0.0
        if delete_payload_after_retention:
            payload_cleanup_start_time = time.perf_counter()
            payload_expired_trace_ids = self.tracking_store._delete_archived_trace_payloads(
                self._select_expired_archived_trace_locators_for_delete(
                    max_request_time_ms=now_millis - broader_retention_millis
                )
            )
            self._clear_archived_trace_locator_payload_uris(payload_expired_trace_ids)
            payload_cleanup_ms = _elapsed_ms(payload_cleanup_start_time)
        _logger.info(
            "Iceberg archive pass archived %s trace(s) and marked %s trace(s) as "
            "payload-expired; payload_cleanup_ms=%s; pass_ms=%s.",
            archived_count,
            len(payload_expired_trace_ids),
            payload_cleanup_ms,
            _elapsed_ms(pass_start_time),
        )

        return archived_count

    def _archive_trace_candidates_by_experiment(
        self,
        *,
        candidates_by_experiment: dict[str, list[_TraceArchiveCandidate]],
        resolved_trace_archival_location: str,
        archive_now_experiment_ids: set[str],
        archive_project_batch_size: int,
        archive_max_workers: int,
        archive_experiment_max_workers: int,
        archive_rollup_refresh_chunks: int = 10,
    ) -> tuple[int, set[str]]:
        archived_count = 0
        retryable_failure_experiment_ids: set[str] = set()
        chunks_by_experiment = {
            experiment_id: [
                (
                    min(start + archive_project_batch_size, len(experiment_candidates)),
                    experiment_candidates[start : start + archive_project_batch_size],
                )
                for start in range(0, len(experiment_candidates), archive_project_batch_size)
            ]
            for experiment_id, experiment_candidates in candidates_by_experiment.items()
        }
        max_rounds = max((len(chunks) for chunks in chunks_by_experiment.values()), default=0)
        for group_start in range(0, max_rounds, archive_rollup_refresh_chunks):
            self._ensure_iceberg_trace_publication_unblocked()
            staged_chunks = []
            for round_index in range(
                group_start, min(group_start + archive_rollup_refresh_chunks, max_rounds)
            ):
                round_chunks = [
                    (experiment_id, *chunks[round_index])
                    for experiment_id, chunks in chunks_by_experiment.items()
                    if round_index < len(chunks)
                ]
                staged_chunks.extend(
                    self._run_archive_experiment_stage_round(
                        round_chunks=round_chunks,
                        candidates_by_experiment=candidates_by_experiment,
                        resolved_trace_archival_location=resolved_trace_archival_location,
                        archive_now_experiment_ids=archive_now_experiment_ids,
                        archive_max_workers=archive_max_workers,
                        archive_experiment_max_workers=archive_experiment_max_workers,
                    )
                )
            with (
                self.acquire_iceberg_trace_write_lock(),
                self._resources.write_lock,
            ):
                publication_barrier_acquired = False
                staged_projections = [
                    projection
                    for chunk in staged_chunks
                    if chunk.error is None
                    for projection in chunk.projections
                ]
                if staged_projections:
                    try:
                        self._begin_iceberg_trace_publication_barrier()
                        publication_barrier_acquired = True
                    except Exception:
                        self._delete_staged_archive_payloads(staged_projections)
                        raise
                appended_chunks = self._run_archive_experiment_append_round(
                    staged_chunks=staged_chunks,
                    archive_experiment_max_workers=archive_experiment_max_workers,
                )
                self._publish_archived_experiment_chunks(
                    appended_chunks,
                    publication_barrier_acquired=publication_barrier_acquired,
                )

            for chunk in appended_chunks:
                archived_count += chunk.archived_count
                if chunk.retryable_failure:
                    retryable_failure_experiment_ids.add(chunk.experiment_id)
                self._log_archived_experiment_chunk(chunk)
        return archived_count, retryable_failure_experiment_ids

    def _run_archive_experiment_stage_round(
        self,
        *,
        round_chunks: list[tuple[str, int, list[_TraceArchiveCandidate]]],
        candidates_by_experiment: dict[str, list[_TraceArchiveCandidate]],
        resolved_trace_archival_location: str,
        archive_now_experiment_ids: set[str],
        archive_max_workers: int,
        archive_experiment_max_workers: int,
    ) -> list[_StagedArchiveExperimentChunk]:
        def stage(experiment_id, processed_count, candidates):
            result = _StagedArchiveExperimentChunk(
                experiment_id=experiment_id,
                candidates=candidates,
                processed_count=processed_count,
                total_count=len(candidates_by_experiment[experiment_id]),
                started_at=time.perf_counter(),
            )
            try:
                load_start_time = time.perf_counter()
                archival_data = self.tracking_store._load_trace_archival_data_batch([
                    candidate.trace_id for candidate in candidates
                ])
                result.load_ms = _elapsed_ms(load_start_time)
                stage_start_time = time.perf_counter()
                failure_ids: set[str] = set()
                result.projections = self._stage_archived_trace_projection_chunk(
                    chunk=candidates,
                    archival_data_by_trace_id=archival_data,
                    resolved_trace_archival_location=resolved_trace_archival_location,
                    archive_now_experiment_ids=archive_now_experiment_ids,
                    retryable_failure_experiment_ids=failure_ids,
                    max_workers=archive_max_workers,
                )
                result.retryable_failure = bool(failure_ids)
                result.stage_ms = _elapsed_ms(stage_start_time)
                result.stage_timings_ms = _archive_stage_timings_ms(result.projections)
            except Exception as e:
                result.error = e
                result.retryable_failure = True
            return result

        return self._run_archive_experiment_tasks(
            [
                (stage, experiment_id, processed_count, candidates)
                for experiment_id, processed_count, candidates in round_chunks
            ],
            max_workers=archive_experiment_max_workers,
            thread_name_prefix="iceberg-archive-experiment-stage",
        )

    def _run_archive_experiment_append_round(
        self,
        *,
        staged_chunks: list[_StagedArchiveExperimentChunk],
        archive_experiment_max_workers: int,
    ) -> list[_StagedArchiveExperimentChunk]:
        def prepare(chunk):
            if chunk.error is not None:
                return chunk
            try:
                chunk.projections = self._select_publishable_archived_projections(chunk.projections)
            except Exception as e:
                chunk.error = e
            return chunk

        chunks_by_experiment = {}
        for chunk in staged_chunks:
            chunks_by_experiment.setdefault(chunk.experiment_id, []).append(chunk)

        def append_experiment(experiment_chunks):
            experiment_chunks = [prepare(chunk) for chunk in experiment_chunks]
            appendable_chunks = [
                chunk for chunk in experiment_chunks if chunk.error is None and chunk.projections
            ]
            if not appendable_chunks:
                return experiment_chunks
            projections = [
                projection for chunk in appendable_chunks for projection in chunk.projections
            ]
            append_start_time = time.perf_counter()
            append_timings_ms = {}
            try:
                append_timings_ms = self._append_archived_trace_projection_rows(projections)
            except Exception as e:
                for chunk in appendable_chunks:
                    chunk.error = e
            finally:
                append_ms = _elapsed_ms(append_start_time)
                for chunk in appendable_chunks:
                    chunk.append_ms = append_ms
                    chunk.append_table_timings_ms = dict(append_timings_ms)
            return experiment_chunks

        appended_chunks = [
            chunk
            for experiment_chunks in self._run_archive_experiment_tasks(
                [
                    (append_experiment, experiment_chunks)
                    for experiment_chunks in chunks_by_experiment.values()
                ],
                max_workers=archive_experiment_max_workers,
                thread_name_prefix="iceberg-archive-experiment-append",
            )
            for chunk in experiment_chunks
        ]
        successful_chunks = [
            chunk for chunk in appended_chunks if chunk.error is None and chunk.projections
        ]
        if not successful_chunks:
            return appended_chunks

        successful_projections = [
            projection for chunk in successful_chunks for projection in chunk.projections
        ]
        partition_keys = self._archived_projection_rollup_partition_keys(successful_projections)
        refresh_start_time = time.perf_counter()
        try:
            self._refresh_rollup_tables(
                trace_partition_keys=partition_keys[0],
                span_partition_keys=partition_keys[1],
                assessment_partition_keys=partition_keys[2],
            )
        except Exception as e:
            try:
                self._rollback_archived_trace_projections(
                    successful_projections, partition_keys=partition_keys
                )
                failure = e
            except _IcebergProjectionCleanupError as cleanup_error:
                failure = cleanup_error
            for chunk in successful_chunks:
                chunk.error = failure
        finally:
            refresh_ms = _elapsed_ms(refresh_start_time)
            for chunk in successful_chunks:
                chunk.append_table_timings_ms["rollup_refresh"] = refresh_ms
        return appended_chunks

    @staticmethod
    def _run_archive_experiment_tasks(tasks, *, max_workers: int, thread_name_prefix: str):
        if max_workers <= 1 or len(tasks) <= 1:
            return [task[0](*task[1:]) for task in tasks]
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(max_workers, len(tasks)),
            thread_name_prefix=thread_name_prefix,
        ) as executor:
            futures = [executor.submit(copy_context().run, task[0], *task[1:]) for task in tasks]
            return [future.result() for future in futures]

    def _publish_archived_experiment_chunks(
        self,
        chunks: list[_StagedArchiveExperimentChunk],
        *,
        publication_barrier_acquired: bool = False,
    ) -> None:
        fatal_cleanup_errors = [
            chunk.error
            for chunk in chunks
            if isinstance(chunk.error, _IcebergProjectionCleanupError)
        ]
        if fatal_cleanup_errors:
            raise fatal_cleanup_errors[0]
        appended_chunks = [chunk for chunk in chunks if chunk.error is None]
        appended_projections = [
            projection for chunk in appended_chunks for projection in chunk.projections
        ]
        if not appended_projections:
            if publication_barrier_acquired:
                self._clear_iceberg_trace_publication_barrier()
            return
        trace_payloads = [
            {
                "trace_id": projection.trace_id,
                "experiment_id": projection.trace_row["experiment_id"],
                "request_time_ms": projection.trace_row["request_time_ms"],
                "request_day": projection.trace_row["request_day"],
                "span_start_days": sorted({
                    row["span_start_day"]
                    for row in projection.span_rows
                    if row.get("span_start_day") is not None
                }),
                "archive_uri": projection.artifact_uri,
                "db_payload_generation": projection.db_payload_generation,
            }
            for projection in appended_projections
        ]
        publish_start_time = time.perf_counter()
        try:
            publication_result = None
            for _ in range(3):
                publication_state = self.get_iceberg_trace_publication_state()
                metadata_locations, snapshot_ids = self._current_iceberg_state()
                publication_result = self.publish_archived_trace_batch(
                    traces=trace_payloads,
                    metadata_locations=metadata_locations,
                    snapshot_ids=snapshot_ids,
                    expected_published_at_ms=(
                        publication_state["published_at_ms"] if publication_state else None
                    ),
                )
                if publication_result is not None:
                    break
            if publication_result is None:
                raise MlflowException(
                    "Failed to publish archived experiment chunks after concurrent updates.",
                    error_code=INVALID_STATE,
                )
        except Exception as e:
            try:
                self._cleanup_unpublished_archived_projections(appended_projections)
            except Exception as cleanup_error:
                raise _IcebergProjectionCleanupError(
                    "Trace archival projection cleanup failed; archival cannot safely continue."
                ) from cleanup_error
            for chunk in appended_chunks:
                chunk.error = e
            return

        published_trace_ids, already_published_trace_ids = map(set, publication_result)
        for chunk in appended_chunks:
            chunk.publish_ms = _elapsed_ms(publish_start_time)
            chunk_trace_ids = {projection.trace_id for projection in chunk.projections}
            chunk.archived_count = len(chunk_trace_ids & published_trace_ids)
            unpublished_trace_ids = (
                chunk_trace_ids - published_trace_ids - already_published_trace_ids
            )
            cleanup_start_time = time.perf_counter()
            if unpublished_trace_ids:
                self._cleanup_unpublished_archived_projections([
                    projection
                    for projection in chunk.projections
                    if projection.trace_id in unpublished_trace_ids
                ])
            chunk.unpublished_cleanup_ms = _elapsed_ms(cleanup_start_time)

    def _delete_staged_archive_payloads(
        self, projections: list[_StagedArchivedTraceProjection]
    ) -> None:
        for projection in projections:
            try:
                self._delete_unreferenced_archived_trace_payload(
                    trace_id=projection.trace_id,
                    artifact_uri=projection.artifact_uri,
                    artifact_repo=projection.artifact_repo
                    or get_artifact_repository(projection.artifact_uri),
                )
            except Exception:
                _logger.warning(
                    "Failed to delete staged archived payload for trace %s after the Iceberg "
                    "publication barrier could not be acquired.",
                    projection.trace_id,
                    exc_info=True,
                )

    @staticmethod
    def _log_archived_experiment_chunk(chunk: _StagedArchiveExperimentChunk) -> None:
        if chunk.error is not None:
            if isinstance(chunk.error, _IcebergProjectionCleanupError):
                raise chunk.error
            _logger.warning(
                "Failed to archive Iceberg projection chunk of %s trace(s) for experiment %s; "
                "continuing.",
                len(chunk.projections),
                chunk.experiment_id,
                exc_info=(type(chunk.error), chunk.error, chunk.error.__traceback__),
            )
            chunk.retryable_failure = True
        _logger.info(
            "Iceberg archive pass processed %s/%s candidate trace(s) for experiment %s; "
            "archived=%s; chunk_timing_ms={load=%s, stage_upload=%s, stage_upload_work=%s, "
            "iceberg_append=%s, iceberg_append_tables=%s, sql_publish=%s, "
            "unpublished_cleanup=%s, total=%s}.",
            chunk.processed_count,
            chunk.total_count,
            chunk.experiment_id,
            chunk.archived_count,
            chunk.load_ms,
            chunk.stage_ms,
            chunk.stage_timings_ms,
            chunk.append_ms,
            chunk.append_table_timings_ms,
            chunk.publish_ms,
            chunk.unpublished_cleanup_ms,
            _elapsed_ms(chunk.started_at),
        )

    def _stage_archived_trace_projection_chunk(
        self,
        *,
        chunk: list,
        archival_data_by_trace_id: dict[str, tuple[TraceInfo, int, list[tuple[str, str]]]],
        resolved_trace_archival_location: str,
        archive_now_experiment_ids: set[str],
        retryable_failure_experiment_ids: set[str],
        max_workers: int,
    ) -> list[_StagedArchivedTraceProjection]:
        projections: list[_StagedArchivedTraceProjection] = []
        archive_payload_uploader = _S3TraceArchivePayloadUploader.from_archive_root(
            resolved_trace_archival_location
        )

        def _stage_candidate(candidate):
            projection = self._stage_archived_trace_projection(
                candidate.trace_id,
                resolved_trace_archival_location=resolved_trace_archival_location,
                archival_data=archival_data_by_trace_id.get(candidate.trace_id),
                archive_payload_uploader=archive_payload_uploader,
            )
            return candidate, projection

        if max_workers <= 1 or len(chunk) <= 1:
            for candidate in chunk:
                try:
                    _, projection = _stage_candidate(candidate)
                    if projection is not None:
                        projections.append(projection)
                except Exception:
                    _logger.warning(
                        "Failed to stage trace %s for Iceberg archival; leaving it eligible "
                        "for retry.",
                        candidate.trace_id,
                        exc_info=True,
                    )
                    if candidate.experiment_id in archive_now_experiment_ids:
                        retryable_failure_experiment_ids.add(candidate.experiment_id)
            return projections

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(max_workers, len(chunk)),
            thread_name_prefix="iceberg-archive",
        ) as executor:
            future_to_candidate = {
                executor.submit(copy_context().run, _stage_candidate, candidate): candidate
                for candidate in chunk
            }
            for future in concurrent.futures.as_completed(future_to_candidate):
                candidate = future_to_candidate[future]
                try:
                    _, projection = future.result()
                    if projection is not None:
                        projections.append(projection)
                except Exception:
                    _logger.warning(
                        "Failed to stage trace %s for Iceberg archival; leaving it eligible "
                        "for retry.",
                        candidate.trace_id,
                        exc_info=True,
                    )
                    if candidate.experiment_id in archive_now_experiment_ids:
                        retryable_failure_experiment_ids.add(candidate.experiment_id)

        return projections

    def _decode_archival_span_payloads(
        self, span_rows: list[tuple[str, str]]
    ) -> list[tuple[Span, dict[str, Any]]]:
        try:
            span_dicts = [translate_loaded_span(json.loads(content)) for _, content in span_rows]
            return [(Span.from_dict(span_dict), span_dict) for span_dict in span_dicts]
        except (MlflowException, TypeError, ValueError, AttributeError) as e:
            raise MlflowTraceArchivalMalformedTrace(str(e)) from e

    def _stage_archived_trace_projection(
        self,
        trace_id: str,
        *,
        resolved_trace_archival_location: str,
        archival_data: tuple[TraceInfo, int, list[tuple[str, str]]] | None = None,
        archive_payload_uploader: _S3TraceArchivePayloadUploader | None = None,
    ) -> _StagedArchivedTraceProjection | None:
        start_time = time.perf_counter()
        archival_data = archival_data or self.tracking_store._load_trace_archival_data(trace_id)
        if archival_data is None:
            return None

        trace_info, db_payload_generation, span_rows = archival_data
        stage_timings_ms = {}
        try:
            decode_start_time = time.perf_counter()
            span_payloads = self._decode_archival_span_payloads(span_rows)
            stage_timings_ms["decode_spans"] = _elapsed_ms(decode_start_time)
            serialize_start_time = time.perf_counter()
            spans = [span for span, _ in span_payloads]
            archived_pb = spans_to_traces_data_pb(spans)
            stage_timings_ms["serialize_pb"] = _elapsed_ms(serialize_start_time)
        except MlflowTraceArchivalMalformedTrace:
            _logger.warning(
                "Marking trace %s as MALFORMED_TRACE during Iceberg archival.", trace_id
            )
            self.tracking_store._mark_trace_archival_failure(
                trace_id=trace_id,
                failure_reason=TraceArchivalFailureReason.MALFORMED_TRACE.value,
                db_payload_generation=db_payload_generation,
            )
            return None

        artifact_uri = append_to_uri_path(
            resolved_trace_archival_location,
            str(trace_info.experiment_id),
            self.tracking_store.TRACE_FOLDER_NAME,
            quote(trace_info.trace_id, safe=""),
            f"{db_payload_generation}-{uuid.uuid4().hex}",
            self.tracking_store.ARTIFACTS_FOLDER_NAME,
        )
        artifact_repo = None
        try:
            upload_start_time = time.perf_counter()
            if archive_payload_uploader is not None:
                archive_payload_uploader.upload_archived_trace_data_bytes(artifact_uri, archived_pb)
            else:
                artifact_repo = get_artifact_repository(artifact_uri)
                artifact_repo.upload_archived_trace_data_bytes(archived_pb)
            stage_timings_ms["upload_payload"] = _elapsed_ms(upload_start_time)
        except Exception as e:
            self._delete_unreferenced_archived_trace_payload(
                trace_id=trace_id,
                artifact_uri=artifact_uri,
                artifact_repo=artifact_repo or get_artifact_repository(artifact_uri),
            )
            raise MlflowException("Trace archival upload failed.") from e

        try:
            projection_start_time = time.perf_counter()
            archived_trace_info = deepcopy(trace_info)
            archived_trace_info.tags = {
                **archived_trace_info.tags,
                TraceTagKey.SPANS_LOCATION: SpansLocation.ARCHIVE_REPO.value,
                TraceTagKey.ARCHIVE_LOCATION: artifact_uri,
            }
            trace_row = self._build_trace_row_from_entity(archived_trace_info)
            trace_tag_rows = self._trace_tag_rows(
                trace_id=trace_row["trace_id"],
                experiment_id=trace_row["experiment_id"],
                request_time_ms=trace_row["request_time_ms"],
                current_tags=json.loads(trace_row["tags_json"]),
                previous_tags=None,
            )
            span_projection_rows = [
                self._span_row(
                    span,
                    experiment_id=trace_row["experiment_id"],
                    span_dict=span_dict,
                )
                for span, span_dict in span_payloads
            ]
            assessment_rows = [
                self._assessment_row(
                    assessment,
                    experiment_id=trace_row["experiment_id"],
                    trace_row=trace_row,
                )
                for assessment in archived_trace_info.assessments
            ]
            stage_timings_ms["build_projection"] = _elapsed_ms(projection_start_time)
        except Exception:
            self._delete_unreferenced_archived_trace_payload(
                trace_id=trace_id,
                artifact_uri=artifact_uri,
                artifact_repo=artifact_repo or get_artifact_repository(artifact_uri),
            )
            raise
        return _StagedArchivedTraceProjection(
            trace_id=trace_id,
            artifact_uri=artifact_uri,
            artifact_repo=artifact_repo,
            db_payload_generation=db_payload_generation,
            trace_row=trace_row,
            trace_tag_rows=trace_tag_rows,
            span_rows=span_projection_rows,
            assessment_rows=assessment_rows,
            started_at=start_time,
            stage_timings_ms=stage_timings_ms,
        )

    def _archived_projection_rollup_partition_keys(
        self, projections: list[_StagedArchivedTraceProjection]
    ) -> tuple[set[tuple[str, date]], set[tuple[str, date]], set[tuple[str, date]]]:
        trace_partition_keys = self._trace_rollup_partition_keys([
            projection.trace_row for projection in projections
        ])
        span_partition_keys = self._span_rollup_partition_keys([
            row for projection in projections for row in projection.span_rows
        ])
        assessment_partition_keys = self._assessment_rollup_partition_keys([
            projection.trace_row for projection in projections
        ])
        return trace_partition_keys, span_partition_keys, assessment_partition_keys

    def _rollback_archived_trace_projections(
        self,
        projections: list[_StagedArchivedTraceProjection],
        *,
        partition_keys: tuple[set[tuple[str, date]], set[tuple[str, date]], set[tuple[str, date]]],
    ) -> None:
        for projection in projections:
            try:
                self._delete_unreferenced_archived_trace_payload(
                    trace_id=projection.trace_id,
                    artifact_uri=projection.artifact_uri,
                    artifact_repo=projection.artifact_repo
                    or get_artifact_repository(projection.artifact_uri),
                )
            except Exception:
                _logger.warning(
                    "Failed to delete unreferenced archived payload for trace %s while "
                    "rolling back an Iceberg projection.",
                    projection.trace_id,
                    exc_info=True,
                )
        try:
            self._delete_iceberg_rows_for_trace_ids(
                [projection.trace_id for projection in projections],
                trace_partition_keys=partition_keys[0],
                span_partition_keys=partition_keys[1],
                assessment_partition_keys=partition_keys[2],
            )
        except Exception as cleanup_error:
            _logger.error(
                "Failed to clean up Iceberg rows for %s archived trace projection(s).",
                len(projections),
                exc_info=True,
            )
            raise _IcebergProjectionCleanupError(
                "Trace archival projection cleanup failed; archival cannot safely continue."
            ) from cleanup_error

    def _append_archived_trace_projection_rows(
        self, projections: list[_StagedArchivedTraceProjection]
    ) -> dict[str, float]:
        if not projections:
            return {}
        append_timings_ms = {}
        partition_keys = self._archived_projection_rollup_partition_keys(projections)

        def _time_append(table_name: str, append_fn, rows: list[_Row]) -> None:
            start_time = time.perf_counter()
            try:
                append_fn(rows)
            finally:
                append_timings_ms[table_name] = _elapsed_ms(start_time)

        try:
            _time_append(
                _TRACE_INDEX_TABLE,
                self._append_trace_rows,
                [projection.trace_row for projection in projections],
            )
            _time_append(
                _TRACE_TAG_INDEX_TABLE,
                self._append_trace_tag_rows,
                [row for projection in projections for row in projection.trace_tag_rows],
            )
            _time_append(
                _SPAN_INDEX_TABLE,
                self._append_span_rows,
                [row for projection in projections for row in projection.span_rows],
            )
            _time_append(
                _ASSESSMENT_INDEX_TABLE,
                self._append_assessment_rows,
                [row for projection in projections for row in projection.assessment_rows],
            )
            session_start_time = time.perf_counter()
            try:
                self._upsert_session_summaries([projection.trace_row for projection in projections])
            finally:
                append_timings_ms[_SESSION_SUMMARY_TABLE] = _elapsed_ms(session_start_time)
            return append_timings_ms
        except Exception as e:
            self._rollback_archived_trace_projections(projections, partition_keys=partition_keys)
            if isinstance(e, MlflowException):
                raise
            raise MlflowException("Trace archival projection failed.") from e

    def _append_archived_trace_projections(
        self, projections: list[_StagedArchivedTraceProjection]
    ) -> dict[str, float]:
        append_timings_ms = self._append_archived_trace_projection_rows(projections)
        if not projections:
            return append_timings_ms
        partition_keys = self._archived_projection_rollup_partition_keys(projections)
        refresh_start_time = time.perf_counter()
        try:
            self._refresh_rollup_tables(
                trace_partition_keys=partition_keys[0],
                span_partition_keys=partition_keys[1],
                assessment_partition_keys=partition_keys[2],
            )
        except Exception as e:
            self._rollback_archived_trace_projections(projections, partition_keys=partition_keys)
            if isinstance(e, MlflowException):
                raise
            raise MlflowException("Trace archival projection failed.") from e
        finally:
            append_timings_ms["rollup_refresh"] = _elapsed_ms(refresh_start_time)
        return append_timings_ms


class IcebergSqlAlchemyStore(_IcebergTraceStoreBase, SqlAlchemyStore):
    pass


class WorkspaceAwareIcebergSqlAlchemyStore(_IcebergTraceStoreBase, WorkspaceAwareSqlAlchemyStore):
    pass
