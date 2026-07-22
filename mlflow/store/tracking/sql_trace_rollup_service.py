from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from huey import crontab

from mlflow.environment_variables import (
    MLFLOW_SQL_TRACE_ROLLUPS_ENABLED,
    MLFLOW_SQL_TRACE_ROLLUPS_MAX_PARTITIONS_PER_PASS,
    MLFLOW_TRACE_ROLLUPS_SCHEDULE,
)
from mlflow.exceptions import MlflowException

_logger = logging.getLogger(__name__)

_SQL_TRACE_ROLLUP_SCHEDULER_STATE_LOCK = threading.Lock()
_SQL_TRACE_ROLLUP_SCHEDULER_LAST_RUN_UTC_MINUTE: datetime | None = None


def run_sql_trace_rollup_scheduler() -> int:
    if not MLFLOW_SQL_TRACE_ROLLUPS_ENABLED.get():
        return 0

    if not _should_run_sql_trace_rollup_scheduler(MLFLOW_TRACE_ROLLUPS_SCHEDULE.get()):
        return 0

    from mlflow.server.handlers import _get_tracking_store

    tracking_store = _get_tracking_store()
    max_partitions = max(1, MLFLOW_SQL_TRACE_ROLLUPS_MAX_PARTITIONS_PER_PASS.get())
    build_rollups = getattr(tracking_store, "build_sql_trace_rollups", None)
    if build_rollups is None:
        _logger.info(
            "SQL trace rollup scheduler skipped because the tracking store does not support "
            "SQL trace rollups."
        )
        return 0

    start_time = time.monotonic()
    built_partitions = build_rollups(max_partitions=max_partitions)
    elapsed_seconds = time.monotonic() - start_time
    _logger.info(
        "SQL trace rollup scheduler built %s partition(s) in %.2f second(s).",
        built_partitions,
        elapsed_seconds,
    )
    return built_partitions


def _should_run_sql_trace_rollup_scheduler(schedule: str) -> bool:
    global _SQL_TRACE_ROLLUP_SCHEDULER_LAST_RUN_UTC_MINUTE

    fields = schedule.split()
    if len(fields) != 5:
        raise MlflowException.invalid_parameter_value(
            "MLFLOW_TRACE_ROLLUPS_SCHEDULE must be a five-field cron expression"
        )
    minute, hour, day, month, day_of_week = fields
    try:
        matches_schedule = crontab(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
        )
    except (TypeError, ValueError) as e:
        raise MlflowException.invalid_parameter_value(
            f"Invalid MLFLOW_TRACE_ROLLUPS_SCHEDULE: {schedule!r}"
        ) from e

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    if not matches_schedule(now):
        return False
    with _SQL_TRACE_ROLLUP_SCHEDULER_STATE_LOCK:
        if now == _SQL_TRACE_ROLLUP_SCHEDULER_LAST_RUN_UTC_MINUTE:
            return False
        _SQL_TRACE_ROLLUP_SCHEDULER_LAST_RUN_UTC_MINUTE = now
        return True
