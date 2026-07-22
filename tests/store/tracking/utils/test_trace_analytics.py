import pytest

from mlflow.store.tracking.utils.trace_analytics import get_assessment_analytics_fields


@pytest.mark.parametrize(
    ("value_json", "aggregate_value", "is_numeric_value"),
    [
        ("true", 1.0, False),
        ("false", 0.0, False),
        ('"yes"', 1.0, False),
        ('"no"', 0.0, False),
        ("0.8", 0.8, True),
        ('"0.8"', None, False),
        ('"score"', None, False),
        ("null", None, False),
    ],
)
def test_get_assessment_analytics_fields(value_json, aggregate_value, is_numeric_value):
    assert get_assessment_analytics_fields(value_json) == {
        "aggregate_value": aggregate_value,
        "is_numeric_value": is_numeric_value,
    }
