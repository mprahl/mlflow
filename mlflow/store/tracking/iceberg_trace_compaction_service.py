from __future__ import annotations

import logging
import threading
import time

from mlflow.environment_variables import (
    MLFLOW_ICEBERG_TRACE_COMPACTION_ENABLED,
    MLFLOW_ICEBERG_TRACE_COMPACTION_INTERVAL_SECONDS,
    MLFLOW_USE_ICEBERG_ARCHIVAL,
)

_logger = logging.getLogger(__name__)

_ICEBERG_TRACE_COMPACTION_SCHEDULER_STATE_LOCK = threading.Lock()
_ICEBERG_TRACE_COMPACTION_SCHEDULER_LAST_RUN_MONOTONIC = 0.0


def run_iceberg_trace_compaction_scheduler() -> int:
    """
    Run one scheduler poll for the Iceberg trace backend compaction job.

    This entrypoint is invoked by the periodic Huey task. It is a no-op unless compaction is
    enabled, an Iceberg trace backend is configured, and the configured polling interval has
    elapsed. When it runs, it bin-packs the small data files produced by streaming ingestion,
    collapses append-only version history to the latest live row per key, and expires stale
    snapshots.

    Returns:
        The number of Iceberg tables compacted in this poll. Returns ``0`` when the scheduler is
        disabled, no Iceberg trace backend is configured, or the configured interval has not
        elapsed yet.
    """
    if not MLFLOW_ICEBERG_TRACE_COMPACTION_ENABLED.get():
        return 0

    if not MLFLOW_USE_ICEBERG_ARCHIVAL.get():
        return 0

    interval_seconds = max(1, MLFLOW_ICEBERG_TRACE_COMPACTION_INTERVAL_SECONDS.get())
    if not _should_run_iceberg_trace_compaction_scheduler(interval_seconds):
        return 0

    # Imported lazily so the periodic task module stays importable without the `iceberg` extra.
    from mlflow.server.handlers import _get_tracking_store

    start_time = time.monotonic()
    tracking_store = _get_tracking_store()
    results = tracking_store.compact_iceberg_trace_tables()
    elapsed_seconds = time.monotonic() - start_time

    data_files_before = sum(result.data_files_before for result in results)
    data_files_after = sum(result.data_files_after for result in results)
    _logger.info(
        "Iceberg trace compaction pass rewrote %s table(s) (%s -> %s data files) in %.2f "
        "second(s).",
        len(results),
        data_files_before,
        data_files_after,
        elapsed_seconds,
    )
    return len(results)


def _should_run_iceberg_trace_compaction_scheduler(interval_seconds: int) -> bool:
    """
    Decide whether the current scheduler poll should run a compaction pass.

    Huey polls every minute, but the effective compaction cadence is typically much longer. This
    helper uses a process-local monotonic timestamp and lock to skip polls until the configured
    interval has elapsed, then records the current time when a pass is admitted.

    Args:
        interval_seconds: Minimum number of seconds between admitted compaction passes in the
            current process.

    Returns:
        ``True`` if this poll should proceed with compaction, otherwise ``False``.
    """
    global _ICEBERG_TRACE_COMPACTION_SCHEDULER_LAST_RUN_MONOTONIC

    now = time.monotonic()
    with _ICEBERG_TRACE_COMPACTION_SCHEDULER_STATE_LOCK:
        if now - _ICEBERG_TRACE_COMPACTION_SCHEDULER_LAST_RUN_MONOTONIC < interval_seconds:
            return False
        _ICEBERG_TRACE_COMPACTION_SCHEDULER_LAST_RUN_MONOTONIC = now
        return True
