from __future__ import annotations

from unittest import mock

import mlflow.store.tracking.sql_trace_rollup_service as rollup_service
from mlflow.environment_variables import (
    MLFLOW_SQL_TRACE_ROLLUPS_ENABLED,
    MLFLOW_SQL_TRACE_ROLLUPS_MAX_PARTITIONS_PER_PASS,
    MLFLOW_TRACE_ROLLUPS_SCHEDULE,
)
from mlflow.store.tracking.sql_trace_rollup_service import run_sql_trace_rollup_scheduler


def test_scheduler_runs_when_enabled(monkeypatch):
    from mlflow.server import handlers

    tracking_store = mock.Mock()
    tracking_store.build_sql_trace_rollups.return_value = 3
    monkeypatch.setattr(handlers, "_get_tracking_store", lambda: tracking_store)
    monkeypatch.setattr(MLFLOW_SQL_TRACE_ROLLUPS_ENABLED, "get", lambda: True)
    monkeypatch.setattr(MLFLOW_TRACE_ROLLUPS_SCHEDULE, "get", lambda: "* * * * *")
    monkeypatch.setattr(
        MLFLOW_SQL_TRACE_ROLLUPS_MAX_PARTITIONS_PER_PASS,
        "get",
        lambda: 7,
    )
    monkeypatch.setattr(
        rollup_service,
        "_SQL_TRACE_ROLLUP_SCHEDULER_LAST_RUN_UTC_MINUTE",
        None,
    )

    assert run_sql_trace_rollup_scheduler() == 3
    tracking_store.build_sql_trace_rollups.assert_called_once_with(max_partitions=7)
