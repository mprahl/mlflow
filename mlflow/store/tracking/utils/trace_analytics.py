import json
import math
from typing import Any

from mlflow.tracing.constant import CostKey, TokenUsageKey, TraceMetadataKey, TraceTagKey

DAY_BUCKET_SIZE_MS = 24 * 60 * 60 * 1000


def get_trace_analytics_fields(tags: dict[str, str], metadata: dict[str, str]) -> dict[str, Any]:
    return {
        "trace_name": tags.get(TraceTagKey.TRACE_NAME),
        "session_id": metadata.get(TraceMetadataKey.TRACE_SESSION),
        **extract_token_usage_metrics(metadata),
        **extract_cost_metrics(metadata),
    }


def extract_token_usage_metrics(metadata: dict[str, str]) -> dict[str, float | None]:
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
        "input_tokens": _float_or_none(token_usage.get(TokenUsageKey.INPUT_TOKENS)),
        "output_tokens": _float_or_none(token_usage.get(TokenUsageKey.OUTPUT_TOKENS)),
        "total_tokens": _float_or_none(token_usage.get(TokenUsageKey.TOTAL_TOKENS)),
        "cache_read_input_tokens": _float_or_none(
            token_usage.get(TokenUsageKey.CACHE_READ_INPUT_TOKENS)
        ),
        "cache_creation_input_tokens": _float_or_none(
            token_usage.get(TokenUsageKey.CACHE_CREATION_INPUT_TOKENS)
        ),
    }


def extract_cost_metrics(metadata: dict[str, str]) -> dict[str, float | None]:
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
        "input_cost": _float_or_none(cost.get(CostKey.INPUT_COST)),
        "output_cost": _float_or_none(cost.get(CostKey.OUTPUT_COST)),
        "total_cost": _float_or_none(cost.get(CostKey.TOTAL_COST)),
    }


def get_assessment_analytics_fields(value_json: str | None) -> dict[str, Any]:
    if value_json is None:
        return {"aggregate_value": None, "is_numeric_value": False}

    try:
        value = json.loads(value_json)
    except (TypeError, ValueError):
        value = value_json

    aggregate_value = None
    is_numeric_value = False

    if isinstance(value, bool):
        aggregate_value = 1.0 if value else 0.0
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric_value = float(value)
        if math.isfinite(numeric_value):
            aggregate_value = numeric_value
            is_numeric_value = True
    elif isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"yes", "no"}:
            aggregate_value = 1.0 if lowered == "yes" else 0.0

    return {"aggregate_value": aggregate_value, "is_numeric_value": is_numeric_value}


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    return numeric_value if math.isfinite(numeric_value) else None
