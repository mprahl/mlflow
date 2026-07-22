"""add trace analytics optimizations

Create Date: 2026-07-06 14:15:00.000000

"""

import json
from itertools import islice

import sqlalchemy as sa
from alembic import op

from mlflow.store.tracking.utils.trace_analytics import (
    get_assessment_analytics_fields,
)
from mlflow.tracing.constant import (
    CostKey,
    SpanAttributeKey,
    TokenUsageKey,
    TraceMetadataKey,
    TraceTagKey,
)

# revision identifiers, used by Alembic.
revision = "e5f6a7b8c9d0"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None

_TOKEN_COLUMNS = {
    TokenUsageKey.INPUT_TOKENS: "input_tokens",
    TokenUsageKey.OUTPUT_TOKENS: "output_tokens",
    TokenUsageKey.TOTAL_TOKENS: "total_tokens",
    TokenUsageKey.CACHE_READ_INPUT_TOKENS: "cache_read_input_tokens",
    TokenUsageKey.CACHE_CREATION_INPUT_TOKENS: "cache_creation_input_tokens",
}
_SPAN_COST_COLUMNS = {
    CostKey.INPUT_COST: "input_cost",
    CostKey.OUTPUT_COST: "output_cost",
    CostKey.TOTAL_COST: "total_cost",
}
_BATCH_SIZE = 1000


def upgrade():
    if op.get_bind().dialect.name == "sqlite":
        _add_analytics_columns_sqlite()
    else:
        _add_analytics_columns()

    _backfill_trace_info_analytics()
    _backfill_span_cost_analytics()
    _backfill_assessment_analytics()
    _backfill_trace_cost_analytics()
    _delete_legacy_analytics_rows()
    _delete_legacy_cost_rows()

    if op.get_bind().dialect.name == "sqlite":
        _finalize_analytics_schema_sqlite()
    else:
        _finalize_analytics_schema()

    _create_sql_trace_rollup_tables()


def downgrade():
    _drop_sql_trace_rollup_tables()
    if op.get_bind().dialect.name == "sqlite":
        _prepare_analytics_downgrade_sqlite()
    else:
        _prepare_analytics_downgrade()

    _restore_legacy_cost_rows()
    _restore_legacy_analytics_rows()
    _restore_span_dimension_attributes()

    if op.get_bind().dialect.name == "sqlite":
        _drop_analytics_schema_sqlite()
    else:
        _drop_analytics_schema()


def _add_analytics_columns_sqlite():
    with op.batch_alter_table("trace_info", schema=None) as batch_op:
        batch_op.add_column(sa.Column("trace_name", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("session_id", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("input_tokens", sa.Float(precision=53), nullable=True))
        batch_op.add_column(sa.Column("output_tokens", sa.Float(precision=53), nullable=True))
        batch_op.add_column(sa.Column("total_tokens", sa.Float(precision=53), nullable=True))
        batch_op.add_column(
            sa.Column("cache_read_input_tokens", sa.Float(precision=53), nullable=True)
        )
        batch_op.add_column(
            sa.Column("cache_creation_input_tokens", sa.Float(precision=53), nullable=True)
        )
        batch_op.add_column(sa.Column("input_cost", sa.Float(precision=53), nullable=True))
        batch_op.add_column(sa.Column("output_cost", sa.Float(precision=53), nullable=True))
        batch_op.add_column(sa.Column("total_cost", sa.Float(precision=53), nullable=True))

    with op.batch_alter_table("assessments", schema=None) as batch_op:
        batch_op.add_column(sa.Column("experiment_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("trace_timestamp_ms", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("aggregate_value", sa.Float(precision=53), nullable=True))
        batch_op.add_column(
            sa.Column(
                "is_numeric_value",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    with op.batch_alter_table("spans", schema=None) as batch_op:
        batch_op.add_column(sa.Column("input_cost", sa.Float(precision=53), nullable=True))
        batch_op.add_column(sa.Column("output_cost", sa.Float(precision=53), nullable=True))
        batch_op.add_column(sa.Column("total_cost", sa.Float(precision=53), nullable=True))
        batch_op.add_column(sa.Column("model_name", sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column("model_provider", sa.String(length=500), nullable=True))


def _add_analytics_columns():
    op.add_column("trace_info", sa.Column("trace_name", sa.String(length=500), nullable=True))
    op.add_column("trace_info", sa.Column("session_id", sa.String(length=500), nullable=True))
    op.add_column("trace_info", sa.Column("input_tokens", sa.Float(precision=53), nullable=True))
    op.add_column("trace_info", sa.Column("output_tokens", sa.Float(precision=53), nullable=True))
    op.add_column("trace_info", sa.Column("total_tokens", sa.Float(precision=53), nullable=True))
    op.add_column(
        "trace_info",
        sa.Column("cache_read_input_tokens", sa.Float(precision=53), nullable=True),
    )
    op.add_column(
        "trace_info",
        sa.Column("cache_creation_input_tokens", sa.Float(precision=53), nullable=True),
    )
    op.add_column("trace_info", sa.Column("input_cost", sa.Float(precision=53), nullable=True))
    op.add_column("trace_info", sa.Column("output_cost", sa.Float(precision=53), nullable=True))
    op.add_column("trace_info", sa.Column("total_cost", sa.Float(precision=53), nullable=True))

    op.add_column("assessments", sa.Column("experiment_id", sa.Integer(), nullable=True))
    op.add_column("assessments", sa.Column("trace_timestamp_ms", sa.BigInteger(), nullable=True))
    op.add_column(
        "assessments", sa.Column("aggregate_value", sa.Float(precision=53), nullable=True)
    )
    op.add_column(
        "assessments",
        sa.Column(
            "is_numeric_value",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.add_column("spans", sa.Column("input_cost", sa.Float(precision=53), nullable=True))
    op.add_column("spans", sa.Column("output_cost", sa.Float(precision=53), nullable=True))
    op.add_column("spans", sa.Column("total_cost", sa.Float(precision=53), nullable=True))
    op.add_column("spans", sa.Column("model_name", sa.String(length=500), nullable=True))
    op.add_column("spans", sa.Column("model_provider", sa.String(length=500), nullable=True))
    op.drop_constraint("fk_spans_experiment_id", "spans", type_="foreignkey")


def _prepare_spans_for_batch_recreation():
    with op.batch_alter_table("spans", schema=None) as batch_op:
        batch_op.execute(sa.text("DROP INDEX index_spans_experiment_id_duration"))
        batch_op.execute(sa.text("ALTER TABLE spans DROP COLUMN duration_ns"))


def _restore_span_duration_column(batch_op):
    batch_op.add_column(
        sa.Column(
            "duration_ns",
            sa.BigInteger(),
            sa.Computed("end_time_unix_nano - start_time_unix_nano", persisted=True),
            nullable=True,
        )
    )
    batch_op.create_index(
        "index_spans_experiment_id_duration",
        ["experiment_id", "duration_ns"],
        unique=False,
    )


def _finalize_analytics_schema_sqlite():
    _prepare_spans_for_batch_recreation()
    with op.batch_alter_table("spans", schema=None) as batch_op:
        batch_op.drop_column("dimension_attributes")
        _restore_span_duration_column(batch_op)

    with op.batch_alter_table("assessments", schema=None) as batch_op:
        batch_op.create_index(
            "idx_assessments_exp_trace_ts",
            ["experiment_id", "trace_timestamp_ms"],
            unique=False,
        )
        batch_op.create_index(
            "idx_assessments_exp_trace_ts_name",
            ["experiment_id", "trace_timestamp_ms", "name"],
            unique=False,
        )
        batch_op.create_index(
            "idx_assessments_exp_name_valid",
            ["experiment_id", "name", "valid"],
            unique=False,
        )


def _finalize_analytics_schema():
    if op.get_bind().dialect.name == "postgresql":
        op.create_index(
            "idx_spans_cost_trace_time_cover",
            "spans",
            ["trace_id", "start_time_unix_nano"],
            postgresql_include=[
                "input_cost",
                "output_cost",
                "total_cost",
                "model_name",
                "model_provider",
            ],
            postgresql_where=sa.text(
                "input_cost IS NOT NULL OR output_cost IS NOT NULL OR total_cost IS NOT NULL"
            ),
        )

    op.drop_column("spans", "dimension_attributes")
    op.create_index(
        "idx_assessments_exp_trace_ts",
        "assessments",
        ["experiment_id", "trace_timestamp_ms"],
    )
    op.create_index(
        "idx_assessments_exp_trace_ts_name",
        "assessments",
        ["experiment_id", "trace_timestamp_ms", "name"],
    )
    op.create_index(
        "idx_assessments_exp_name_valid",
        "assessments",
        ["experiment_id", "name", "valid"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.create_index(
            "idx_assessments_numeric_exp_trace_ts_type_name",
            "assessments",
            ["experiment_id", "trace_timestamp_ms", "assessment_type", "name"],
            postgresql_include=["aggregate_value"],
            postgresql_where=sa.text("valid AND aggregate_value IS NOT NULL"),
        )


def _drop_analytics_schema_sqlite():
    with op.batch_alter_table("assessments", schema=None) as batch_op:
        batch_op.drop_index("idx_assessments_exp_trace_ts_name")
        batch_op.drop_index("idx_assessments_exp_trace_ts")
        batch_op.drop_index("idx_assessments_exp_name_valid")
        batch_op.drop_column("is_numeric_value")
        batch_op.drop_column("aggregate_value")
        batch_op.drop_column("trace_timestamp_ms")
        batch_op.drop_column("experiment_id")

    with op.batch_alter_table("trace_info", schema=None) as batch_op:
        batch_op.drop_column("total_cost")
        batch_op.drop_column("output_cost")
        batch_op.drop_column("input_cost")
        batch_op.drop_column("cache_creation_input_tokens")
        batch_op.drop_column("cache_read_input_tokens")
        batch_op.drop_column("total_tokens")
        batch_op.drop_column("output_tokens")
        batch_op.drop_column("input_tokens")
        batch_op.drop_column("session_id")
        batch_op.drop_column("trace_name")

    _prepare_spans_for_batch_recreation()
    with op.batch_alter_table("spans", schema=None) as batch_op:
        batch_op.drop_column("model_provider")
        batch_op.drop_column("model_name")
        batch_op.drop_column("total_cost")
        batch_op.drop_column("output_cost")
        batch_op.drop_column("input_cost")
        _restore_span_duration_column(batch_op)


def _prepare_analytics_downgrade_sqlite():
    with op.batch_alter_table("spans", schema=None) as batch_op:
        batch_op.add_column(sa.Column("dimension_attributes", sa.JSON(), nullable=True))


def _prepare_analytics_downgrade():
    op.add_column("spans", sa.Column("dimension_attributes", sa.JSON(), nullable=True))


def _drop_analytics_schema():
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index("idx_spans_cost_trace_time_cover", table_name="spans")
        op.drop_index(
            "idx_assessments_numeric_exp_trace_ts_type_name",
            table_name="assessments",
        )
    op.create_foreign_key(
        "fk_spans_experiment_id",
        "spans",
        "experiments",
        ["experiment_id"],
        ["experiment_id"],
    )
    op.drop_index("idx_assessments_exp_trace_ts_name", table_name="assessments")
    op.drop_index("idx_assessments_exp_trace_ts", table_name="assessments")
    op.drop_index("idx_assessments_exp_name_valid", table_name="assessments")
    op.drop_column("assessments", "aggregate_value")
    op.drop_column("assessments", "is_numeric_value")
    op.drop_column("assessments", "trace_timestamp_ms")
    op.drop_column("assessments", "experiment_id")

    op.drop_column("trace_info", "cache_creation_input_tokens")
    op.drop_column("trace_info", "cache_read_input_tokens")
    op.drop_column("trace_info", "total_tokens")
    op.drop_column("trace_info", "output_tokens")
    op.drop_column("trace_info", "input_tokens")
    op.drop_column("trace_info", "total_cost")
    op.drop_column("trace_info", "output_cost")
    op.drop_column("trace_info", "input_cost")
    op.drop_column("trace_info", "session_id")
    op.drop_column("trace_info", "trace_name")

    op.drop_column("spans", "total_cost")
    op.drop_column("spans", "output_cost")
    op.drop_column("spans", "input_cost")
    op.drop_column("spans", "model_provider")
    op.drop_column("spans", "model_name")


def _create_sql_trace_rollup_tables():
    op.create_table(
        "sql_trace_metric_daily_rollups",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("workspace", sa.String(length=255), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=False),
        sa.Column("rollup_day", sa.Date(), nullable=False),
        sa.Column("metric_name", sa.String(length=250), nullable=False),
        sa.Column("grouping_set", sa.String(length=50), nullable=False),
        sa.Column("trace_status", sa.String(length=50), nullable=True),
        sa.Column("sample_count", sa.BigInteger(), nullable=False),
        sa.Column("sum_value", sa.Float(precision=53), nullable=True),
        sa.Column("min_value", sa.Float(precision=53), nullable=True),
        sa.Column("max_value", sa.Float(precision=53), nullable=True),
        sa.Column("p50_value", sa.Float(precision=53), nullable=True),
        sa.Column("p90_value", sa.Float(precision=53), nullable=True),
        sa.Column("p99_value", sa.Float(precision=53), nullable=True),
        sa.PrimaryKeyConstraint("id", name="sql_trace_metric_daily_rollups_pk"),
    )
    op.create_index(
        "idx_sql_trace_metric_rollups_lookup",
        "sql_trace_metric_daily_rollups",
        [
            "workspace",
            "experiment_id",
            "rollup_day",
            "metric_name",
            "grouping_set",
            "trace_status",
        ],
    )

    op.create_table(
        "sql_span_cost_daily_rollups",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("workspace", sa.String(length=255), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=False),
        sa.Column("rollup_day", sa.Date(), nullable=False),
        sa.Column("metric_name", sa.String(length=250), nullable=False),
        sa.Column("grouping_set", sa.String(length=50), nullable=False),
        sa.Column("model_name", sa.String(length=500), nullable=True),
        sa.Column("model_provider", sa.String(length=500), nullable=True),
        sa.Column("sample_count", sa.BigInteger(), nullable=False),
        sa.Column("sum_value", sa.Float(precision=53), nullable=True),
        sa.Column("min_value", sa.Float(precision=53), nullable=True),
        sa.Column("max_value", sa.Float(precision=53), nullable=True),
        sa.PrimaryKeyConstraint("id", name="sql_span_cost_daily_rollups_pk"),
    )
    op.create_index(
        "idx_sql_span_cost_rollups_lookup",
        "sql_span_cost_daily_rollups",
        [
            "workspace",
            "experiment_id",
            "rollup_day",
            "metric_name",
            "grouping_set",
            "model_name",
            "model_provider",
        ],
    )

    op.create_table(
        "sql_assessment_daily_rollups",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("workspace", sa.String(length=255), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=False),
        sa.Column("rollup_day", sa.Date(), nullable=False),
        sa.Column("metric_name", sa.String(length=250), nullable=False),
        sa.Column("grouping_set", sa.String(length=50), nullable=False),
        sa.Column("sample_count", sa.BigInteger(), nullable=False),
        sa.Column("sum_value", sa.Float(precision=53), nullable=True),
        sa.Column("min_value", sa.Float(precision=53), nullable=True),
        sa.Column("max_value", sa.Float(precision=53), nullable=True),
        sa.PrimaryKeyConstraint("id", name="sql_assessment_daily_rollups_pk"),
    )
    op.create_index(
        "idx_sql_assessment_rollups_lookup",
        "sql_assessment_daily_rollups",
        ["workspace", "experiment_id", "rollup_day", "metric_name", "grouping_set"],
    )

    op.create_table(
        "sql_trace_rollup_rebuild_queue",
        sa.Column("workspace", sa.String(length=255), nullable=False),
        sa.Column("experiment_id", sa.Integer(), nullable=False),
        sa.Column("rollup_day", sa.Date(), nullable=False),
        sa.Column("rollup_family", sa.String(length=50), nullable=False),
        sa.PrimaryKeyConstraint(
            "workspace",
            "experiment_id",
            "rollup_day",
            "rollup_family",
            name="sql_trace_rollup_rebuild_queue_pk",
        ),
    )

    if op.get_bind().dialect.name == "postgresql":
        op.create_index(
            "idx_spans_cost_exp_time_cover",
            "spans",
            ["experiment_id", "start_time_unix_nano"],
            postgresql_include=[
                "input_cost",
                "output_cost",
                "total_cost",
                "model_name",
                "model_provider",
            ],
            postgresql_where=sa.text(
                "input_cost IS NOT NULL OR output_cost IS NOT NULL OR total_cost IS NOT NULL"
            ),
        )


def _drop_sql_trace_rollup_tables():
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index("idx_spans_cost_exp_time_cover", table_name="spans")

    op.drop_table("sql_trace_rollup_rebuild_queue")
    op.drop_index("idx_sql_assessment_rollups_lookup", table_name="sql_assessment_daily_rollups")
    op.drop_table("sql_assessment_daily_rollups")
    op.drop_index("idx_sql_span_cost_rollups_lookup", table_name="sql_span_cost_daily_rollups")
    op.drop_table("sql_span_cost_daily_rollups")
    op.drop_index(
        "idx_sql_trace_metric_rollups_lookup", table_name="sql_trace_metric_daily_rollups"
    )
    op.drop_table("sql_trace_metric_daily_rollups")


def _backfill_trace_info_analytics():
    bind = op.get_bind()
    metadata = sa.MetaData()
    trace_info = sa.Table("trace_info", metadata, autoload_with=bind)
    trace_tags = sa.Table("trace_tags", metadata, autoload_with=bind)
    trace_metadata = sa.Table("trace_request_metadata", metadata, autoload_with=bind)
    trace_metrics = sa.Table("trace_metrics", metadata, autoload_with=bind)

    trace_id_rows = bind.execute(sa.select(trace_info.c.request_id))
    for batch in _batched(trace_id_rows, _BATCH_SIZE):
        batch_ids = [row.request_id for row in batch]
        trace_name_by_id = {
            row.request_id: row.value
            for row in bind.execute(
                sa.select(trace_tags.c.request_id, trace_tags.c.value).where(
                    trace_tags.c.request_id.in_(batch_ids),
                    trace_tags.c.key == TraceTagKey.TRACE_NAME,
                )
            )
        }
        session_by_id = {
            row.request_id: row.value
            for row in bind.execute(
                sa.select(trace_metadata.c.request_id, trace_metadata.c.value).where(
                    trace_metadata.c.request_id.in_(batch_ids),
                    trace_metadata.c.key == TraceMetadataKey.TRACE_SESSION,
                )
            )
        }
        metrics_by_id = {trace_id: {} for trace_id in batch_ids}
        metric_rows = bind.execute(
            sa.select(trace_metrics.c.request_id, trace_metrics.c.key, trace_metrics.c.value).where(
                trace_metrics.c.request_id.in_(batch_ids),
                trace_metrics.c.key.in_(list(_TOKEN_COLUMNS)),
            )
        )
        for row in metric_rows:
            metrics_by_id[row.request_id][_TOKEN_COLUMNS[row.key]] = row.value

        for trace_id in batch_ids:
            bind.execute(
                trace_info
                .update()
                .where(trace_info.c.request_id == trace_id)
                .values(
                    trace_name=trace_name_by_id.get(trace_id),
                    session_id=session_by_id.get(trace_id),
                    input_tokens=metrics_by_id[trace_id].get("input_tokens"),
                    output_tokens=metrics_by_id[trace_id].get("output_tokens"),
                    total_tokens=metrics_by_id[trace_id].get("total_tokens"),
                    cache_read_input_tokens=metrics_by_id[trace_id].get("cache_read_input_tokens"),
                    cache_creation_input_tokens=metrics_by_id[trace_id].get(
                        "cache_creation_input_tokens"
                    ),
                )
            )


def _backfill_assessment_analytics():
    bind = op.get_bind()
    metadata = sa.MetaData()
    trace_info = sa.Table("trace_info", metadata, autoload_with=bind)
    assessments = sa.Table("assessments", metadata, autoload_with=bind)

    rows = bind.execute(
        sa.select(
            assessments.c.assessment_id,
            assessments.c.value,
            trace_info.c.experiment_id,
            trace_info.c.timestamp_ms,
        ).select_from(
            assessments.join(trace_info, assessments.c.trace_id == trace_info.c.request_id)
        )
    )
    update_stmt = assessments.update().where(
        assessments.c.assessment_id == sa.bindparam("assessment_id_param")
    )
    for batch in _batched(rows, _BATCH_SIZE):
        batch_updates = []
        for row in batch:
            analytics = get_assessment_analytics_fields(row.value)
            batch_updates.append({
                "assessment_id_param": row.assessment_id,
                "experiment_id": row.experiment_id,
                "trace_timestamp_ms": row.timestamp_ms,
                "aggregate_value": analytics["aggregate_value"],
                "is_numeric_value": analytics["is_numeric_value"],
            })
        bind.execute(
            update_stmt.values(
                experiment_id=sa.bindparam("experiment_id"),
                trace_timestamp_ms=sa.bindparam("trace_timestamp_ms"),
                aggregate_value=sa.bindparam("aggregate_value"),
                is_numeric_value=sa.bindparam("is_numeric_value"),
            ),
            batch_updates,
        )


def _backfill_span_cost_analytics():
    bind = op.get_bind()
    metadata = sa.MetaData()
    spans = sa.Table("spans", metadata, autoload_with=bind)
    span_metrics = sa.Table("span_metrics", metadata, autoload_with=bind)

    span_rows = bind.execute(
        sa.select(spans.c.trace_id, spans.c.span_id, spans.c.dimension_attributes)
    )
    update_stmt = spans.update().where(
        spans.c.trace_id == sa.bindparam("trace_id_param"),
        spans.c.span_id == sa.bindparam("span_id_param"),
    )
    for batch in _batched(span_rows, _BATCH_SIZE):
        span_keys = [(row.trace_id, row.span_id) for row in batch]
        dimension_attributes_by_span = {
            (row.trace_id, row.span_id): _as_dimension_attributes(row.dimension_attributes)
            for row in batch
        }
        metrics_by_span = {span_key: {} for span_key in span_keys}
        metric_rows = bind.execute(
            sa.select(
                span_metrics.c.trace_id,
                span_metrics.c.span_id,
                span_metrics.c.key,
                span_metrics.c.value,
            ).where(
                sa.tuple_(span_metrics.c.trace_id, span_metrics.c.span_id).in_(span_keys),
                span_metrics.c.key.in_(list(_SPAN_COST_COLUMNS)),
            )
        )
        for row in metric_rows:
            metrics_by_span[(row.trace_id, row.span_id)][_SPAN_COST_COLUMNS[row.key]] = row.value

        batch_updates = [
            {
                "trace_id_param": trace_id,
                "span_id_param": span_id,
                "input_cost": metrics.get("input_cost"),
                "output_cost": metrics.get("output_cost"),
                "total_cost": metrics.get("total_cost"),
                "model_name": dimension_attributes_by_span[(trace_id, span_id)].get(
                    SpanAttributeKey.MODEL
                ),
                "model_provider": dimension_attributes_by_span[(trace_id, span_id)].get(
                    SpanAttributeKey.MODEL_PROVIDER
                ),
            }
            for (trace_id, span_id), metrics in metrics_by_span.items()
        ]
        if batch_updates:
            bind.execute(
                update_stmt.values(
                    input_cost=sa.bindparam("input_cost"),
                    output_cost=sa.bindparam("output_cost"),
                    total_cost=sa.bindparam("total_cost"),
                    model_name=sa.bindparam("model_name"),
                    model_provider=sa.bindparam("model_provider"),
                ),
                batch_updates,
            )


def _restore_span_dimension_attributes():
    bind = op.get_bind()
    metadata = sa.MetaData()
    spans = sa.Table("spans", metadata, autoload_with=bind)
    rows = bind.execute(
        sa.select(spans.c.trace_id, spans.c.span_id, spans.c.model_name, spans.c.model_provider)
    )
    update_stmt = spans.update().where(
        spans.c.trace_id == sa.bindparam("trace_id_param"),
        spans.c.span_id == sa.bindparam("span_id_param"),
    )
    for batch in _batched(rows, _BATCH_SIZE):
        updates = []
        for row in batch:
            values = {
                key: value
                for key, value in (
                    (SpanAttributeKey.MODEL, row.model_name),
                    (SpanAttributeKey.MODEL_PROVIDER, row.model_provider),
                )
                if value is not None
            }
            updates.append({
                "trace_id_param": row.trace_id,
                "span_id_param": row.span_id,
                "dimension_attributes": values or None,
            })
        bind.execute(
            update_stmt.values(dimension_attributes=sa.bindparam("dimension_attributes")), updates
        )


def _backfill_trace_cost_analytics():
    bind = op.get_bind()
    metadata = sa.MetaData()
    trace_info = sa.Table("trace_info", metadata, autoload_with=bind)
    trace_metadata = sa.Table("trace_request_metadata", metadata, autoload_with=bind)
    span_metrics = sa.Table("span_metrics", metadata, autoload_with=bind)

    updated_trace_ids = set()
    cost_rows = bind.execute(
        sa.select(trace_metadata.c.request_id, trace_metadata.c.value).where(
            trace_metadata.c.key == TraceMetadataKey.COST
        )
    )
    for batch in _batched(cost_rows, _BATCH_SIZE):
        updates = []
        for row in batch:
            try:
                cost = json.loads(row.value) if row.value else {}
            except (TypeError, ValueError):
                cost = {}
            updates.append({
                "request_id_param": row.request_id,
                **{
                    column_name: _float_or_none(cost.get(cost_key))
                    for cost_key, column_name in _SPAN_COST_COLUMNS.items()
                },
            })
            updated_trace_ids.add(row.request_id)
        if updates:
            bind.execute(
                trace_info
                .update()
                .where(trace_info.c.request_id == sa.bindparam("request_id_param"))
                .values(
                    input_cost=sa.bindparam("input_cost"),
                    output_cost=sa.bindparam("output_cost"),
                    total_cost=sa.bindparam("total_cost"),
                ),
                updates,
            )

    trace_id_rows = bind.execute(sa.select(trace_info.c.request_id))
    for batch in _batched(trace_id_rows, _BATCH_SIZE):
        batch_ids = [row.request_id for row in batch if row.request_id not in updated_trace_ids]
        if not batch_ids:
            continue
        cost_sums_by_trace_id = {trace_id: {} for trace_id in batch_ids}
        cost_rows = bind.execute(
            sa
            .select(
                span_metrics.c.trace_id,
                span_metrics.c.key,
                sa.func.sum(span_metrics.c.value).label("value"),
            )
            .where(
                span_metrics.c.trace_id.in_(batch_ids),
                span_metrics.c.key.in_(list(_SPAN_COST_COLUMNS)),
            )
            .group_by(span_metrics.c.trace_id, span_metrics.c.key)
        )
        for row in cost_rows:
            cost_sums_by_trace_id[row.trace_id][_SPAN_COST_COLUMNS[row.key]] = row.value

        updates = [
            {
                "request_id_param": trace_id,
                "input_cost": costs.get("input_cost"),
                "output_cost": costs.get("output_cost"),
                "total_cost": costs.get("total_cost"),
            }
            for trace_id, costs in cost_sums_by_trace_id.items()
            if costs
        ]
        if updates:
            bind.execute(
                trace_info
                .update()
                .where(trace_info.c.request_id == sa.bindparam("request_id_param"))
                .values(
                    input_cost=sa.bindparam("input_cost"),
                    output_cost=sa.bindparam("output_cost"),
                    total_cost=sa.bindparam("total_cost"),
                ),
                updates,
            )


def _delete_legacy_cost_rows():
    bind = op.get_bind()
    metadata = sa.MetaData()
    trace_metadata = sa.Table("trace_request_metadata", metadata, autoload_with=bind)
    span_metrics = sa.Table("span_metrics", metadata, autoload_with=bind)
    bind.execute(trace_metadata.delete().where(trace_metadata.c.key == TraceMetadataKey.COST))
    bind.execute(span_metrics.delete().where(span_metrics.c.key.in_(list(_SPAN_COST_COLUMNS))))


def _restore_legacy_cost_rows():
    bind = op.get_bind()
    metadata = sa.MetaData()
    trace_info = sa.Table("trace_info", metadata, autoload_with=bind)
    trace_metadata = sa.Table("trace_request_metadata", metadata, autoload_with=bind)
    spans = sa.Table("spans", metadata, autoload_with=bind)
    span_metrics = sa.Table("span_metrics", metadata, autoload_with=bind)

    trace_rows = bind.execute(
        sa.select(
            trace_info.c.request_id,
            *(getattr(trace_info.c, column) for column in _SPAN_COST_COLUMNS.values()),
        )
    )
    for batch in _batched(trace_rows, _BATCH_SIZE):
        metadata_rows = []
        for row in batch:
            cost = {
                key: getattr(row, column)
                for key, column in _SPAN_COST_COLUMNS.items()
                if getattr(row, column) is not None
            }
            if cost:
                metadata_rows.append({
                    "request_id": row.request_id,
                    "key": TraceMetadataKey.COST,
                    "value": json.dumps(cost),
                })
        if metadata_rows:
            bind.execute(trace_metadata.insert(), metadata_rows)

    span_rows = bind.execute(
        sa.select(
            spans.c.trace_id,
            spans.c.span_id,
            *(getattr(spans.c, column) for column in _SPAN_COST_COLUMNS.values()),
        )
    )
    for batch in _batched(span_rows, _BATCH_SIZE):
        metric_rows = [
            {
                "trace_id": row.trace_id,
                "span_id": row.span_id,
                "key": key,
                "value": getattr(row, column),
            }
            for row in batch
            for key, column in _SPAN_COST_COLUMNS.items()
            if getattr(row, column) is not None
        ]
        if metric_rows:
            bind.execute(span_metrics.insert(), metric_rows)


def _delete_legacy_analytics_rows():
    bind = op.get_bind()
    metadata = sa.MetaData()
    trace_tags = sa.Table("trace_tags", metadata, autoload_with=bind)
    trace_metadata = sa.Table("trace_request_metadata", metadata, autoload_with=bind)
    trace_metrics = sa.Table("trace_metrics", metadata, autoload_with=bind)

    bind.execute(trace_tags.delete().where(trace_tags.c.key == TraceTagKey.TRACE_NAME))
    bind.execute(
        trace_metadata.delete().where(
            trace_metadata.c.key.in_([
                TraceMetadataKey.TRACE_SESSION,
                TraceMetadataKey.TOKEN_USAGE,
            ])
        )
    )
    bind.execute(trace_metrics.delete().where(trace_metrics.c.key.in_(list(_TOKEN_COLUMNS))))


def _restore_legacy_analytics_rows():
    bind = op.get_bind()
    metadata = sa.MetaData()
    trace_info = sa.Table("trace_info", metadata, autoload_with=bind)
    trace_tags = sa.Table("trace_tags", metadata, autoload_with=bind)
    trace_metadata = sa.Table("trace_request_metadata", metadata, autoload_with=bind)
    trace_metrics = sa.Table("trace_metrics", metadata, autoload_with=bind)

    trace_rows = bind.execute(
        sa.select(
            trace_info.c.request_id,
            trace_info.c.trace_name,
            trace_info.c.session_id,
            *(getattr(trace_info.c, column) for column in _TOKEN_COLUMNS.values()),
        )
    )
    for batch in _batched(trace_rows, _BATCH_SIZE):
        tag_rows = []
        metadata_rows = []
        metric_rows = []
        for row in batch:
            if row.trace_name is not None:
                tag_rows.append({
                    "request_id": row.request_id,
                    "key": TraceTagKey.TRACE_NAME,
                    "value": row.trace_name,
                })
            if row.session_id is not None:
                metadata_rows.append({
                    "request_id": row.request_id,
                    "key": TraceMetadataKey.TRACE_SESSION,
                    "value": row.session_id,
                })
            token_usage = {
                key: getattr(row, column)
                for key, column in _TOKEN_COLUMNS.items()
                if getattr(row, column) is not None
            }
            if token_usage:
                metadata_rows.append({
                    "request_id": row.request_id,
                    "key": TraceMetadataKey.TOKEN_USAGE,
                    "value": json.dumps(token_usage),
                })
                metric_rows.extend(
                    {"request_id": row.request_id, "key": key, "value": value}
                    for key, value in token_usage.items()
                )
        if tag_rows:
            bind.execute(trace_tags.insert(), tag_rows)
        if metadata_rows:
            bind.execute(trace_metadata.insert(), metadata_rows)
        if metric_rows:
            bind.execute(trace_metrics.insert(), metric_rows)


def _as_dimension_attributes(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _float_or_none(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _batched(result, size):
    iterator = iter(result)
    while batch := list(islice(iterator, size)):
        yield batch
