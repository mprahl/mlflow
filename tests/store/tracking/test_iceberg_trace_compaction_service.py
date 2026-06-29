from __future__ import annotations

import pytest

import mlflow.store.tracking.iceberg_trace_compaction_service as compaction_service
from mlflow.environment_variables import (
    MLFLOW_ICEBERG_TRACE_COMPACTION_ENABLED,
    MLFLOW_ICEBERG_TRACE_COMPACTION_INTERVAL_SECONDS,
    MLFLOW_USE_ICEBERG_ARCHIVAL,
)
from mlflow.store.tracking.iceberg_trace_compaction_service import (
    run_iceberg_trace_compaction_scheduler,
)


@pytest.fixture(autouse=True)
def _reset_scheduler_state(monkeypatch):
    monkeypatch.setattr(
        compaction_service, "_ICEBERG_TRACE_COMPACTION_SCHEDULER_LAST_RUN_MONOTONIC", 0.0
    )
    return


def _track_compaction(monkeypatch):
    calls = []

    class FakeTrackingStore:
        def compact_iceberg_trace_tables(self):
            calls.append("compact")
            return []

    from mlflow.server import handlers

    monkeypatch.setattr(handlers, "_get_tracking_store", lambda: FakeTrackingStore())
    return calls


def test_scheduler_noop_when_disabled(monkeypatch):
    calls = _track_compaction(monkeypatch)
    monkeypatch.setattr(MLFLOW_ICEBERG_TRACE_COMPACTION_ENABLED, "get", lambda: False)
    monkeypatch.setenv(MLFLOW_USE_ICEBERG_ARCHIVAL.name, "true")

    assert run_iceberg_trace_compaction_scheduler() == 0
    assert calls == []


def test_scheduler_noop_when_iceberg_archival_disabled(monkeypatch):
    calls = _track_compaction(monkeypatch)
    monkeypatch.setattr(MLFLOW_ICEBERG_TRACE_COMPACTION_ENABLED, "get", lambda: True)
    monkeypatch.setenv(MLFLOW_USE_ICEBERG_ARCHIVAL.name, "false")

    assert run_iceberg_trace_compaction_scheduler() == 0
    assert calls == []


def test_scheduler_noop_when_iceberg_archival_unset(monkeypatch):
    calls = _track_compaction(monkeypatch)
    monkeypatch.setattr(MLFLOW_ICEBERG_TRACE_COMPACTION_ENABLED, "get", lambda: True)
    monkeypatch.delenv(MLFLOW_USE_ICEBERG_ARCHIVAL.name, raising=False)

    assert run_iceberg_trace_compaction_scheduler() == 0
    assert calls == []


def test_scheduler_runs_then_respects_interval(monkeypatch):
    calls = _track_compaction(monkeypatch)
    monkeypatch.setattr(MLFLOW_ICEBERG_TRACE_COMPACTION_ENABLED, "get", lambda: True)
    monkeypatch.setattr(MLFLOW_ICEBERG_TRACE_COMPACTION_INTERVAL_SECONDS, "get", lambda: 86400)
    monkeypatch.setenv(MLFLOW_USE_ICEBERG_ARCHIVAL.name, "true")

    # Pin the monotonic clock so the second poll falls within the configured interval.
    monkeypatch.setattr(compaction_service.time, "monotonic", lambda: 1_000_000.0)

    # First poll runs; the second poll is skipped because the interval has not elapsed.
    assert run_iceberg_trace_compaction_scheduler() == 0  # len([]) from fake compaction
    assert calls == ["compact"]
    assert run_iceberg_trace_compaction_scheduler() == 0
    assert calls == ["compact"]


def test_scheduler_runs_again_after_interval_elapses(monkeypatch):
    calls = _track_compaction(monkeypatch)
    monkeypatch.setattr(MLFLOW_ICEBERG_TRACE_COMPACTION_ENABLED, "get", lambda: True)
    monkeypatch.setattr(MLFLOW_ICEBERG_TRACE_COMPACTION_INTERVAL_SECONDS, "get", lambda: 3600)
    monkeypatch.setenv(MLFLOW_USE_ICEBERG_ARCHIVAL.name, "true")

    # Drive a fake monotonic clock so the second poll lands after the interval has elapsed.
    clock = {"now": 10_000.0}
    monkeypatch.setattr(compaction_service.time, "monotonic", lambda: clock["now"])

    run_iceberg_trace_compaction_scheduler()
    clock["now"] += 4000.0
    run_iceberg_trace_compaction_scheduler()
    assert calls == ["compact", "compact"]
