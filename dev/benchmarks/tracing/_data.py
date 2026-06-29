import json
import random
import time
import uuid
from dataclasses import dataclass

from opentelemetry import trace as trace_api
from opentelemetry.sdk.resources import Resource as _OTelResource
from opentelemetry.sdk.trace import ReadableSpan as OTelReadableSpan
from opentelemetry.trace import SpanContext

from mlflow.entities import AssessmentSource, AssessmentSourceType, Expectation, Feedback
from mlflow.entities.span import Span, SpanType, create_mlflow_span
from mlflow.entities.trace_info import TraceInfo
from mlflow.entities.trace_location import TraceLocation
from mlflow.entities.trace_state import TraceState
from mlflow.store.tracking.sqlalchemy_store import SqlAlchemyStore
from mlflow.tracing.constant import (
    CostKey,
    SpanAttributeKey,
    TokenUsageKey,
    TraceMetadataKey,
    TraceTagKey,
)
from mlflow.tracing.utils import TraceJSONEncoder

ENV_CHOICES = ["prod", "staging", "dev"]
NAME_PREFIXES = ["agent_run", "qa_chain", "rag_pipeline", "summarizer"]
WEEK_MS = 7 * 24 * 60 * 60 * 1000

SEED_TRACES = 1000
SEED_SPANS_PER_TRACE = 10


@dataclass
class TraceSeedBundle:
    trace_info: TraceInfo
    spans: list[Span]
    assessments: list[Feedback | Expectation]


def generate_trace_data(
    experiment_id: str,
    num_spans: int,
    rng: random.Random,
) -> tuple[TraceInfo, list[Span]]:
    trace_id = f"tr-{uuid.uuid4().hex}"
    request_time = int(time.time() * 1000) - rng.randint(0, WEEK_MS)
    name_prefix = rng.choice(NAME_PREFIXES)
    trace_info = TraceInfo(
        trace_id=trace_id,
        trace_location=TraceLocation.from_experiment_id(experiment_id),
        request_time=request_time,
        state=rng.choice([TraceState.OK, TraceState.OK, TraceState.OK, TraceState.ERROR]),
        execution_duration=rng.randint(100, 5000),
        tags={
            TraceTagKey.TRACE_NAME: f"{name_prefix}_{trace_id[-4:]}",
            "env": rng.choice(ENV_CHOICES),
        },
    )

    span_types = [SpanType.LLM, SpanType.RETRIEVER, SpanType.TOOL, SpanType.CHAIN]
    base_ns = 1_000_000_000_000
    spans: list[Span] = []

    for i in range(num_spans):
        is_root = i == 0
        span_type = SpanType.AGENT if is_root else rng.choice(span_types)
        parent_id = None if is_root else rng.choice(range(max(0, i - 3), i))

        trace_num = rng.randint(1, 2**63)
        ctx = SpanContext(
            trace_id=trace_num,
            span_id=i + 1,
            is_remote=False,
            trace_flags=trace_api.TraceFlags(1),
            trace_state=trace_api.TraceState(),
        )

        parent_ctx = None
        if parent_id is not None:
            parent_ctx = SpanContext(
                trace_id=trace_num,
                span_id=parent_id + 1,
                is_remote=False,
                trace_flags=trace_api.TraceFlags(1),
                trace_state=trace_api.TraceState(),
            )

        attrs: dict[str, object] = {}
        if is_root:
            attrs[SpanAttributeKey.INPUTS] = json.dumps(
                {"query": "What is ML?"}, cls=TraceJSONEncoder
            )
            attrs[SpanAttributeKey.OUTPUTS] = json.dumps(
                {"response": "ML is..."}, cls=TraceJSONEncoder
            )

        otel_span = OTelReadableSpan(
            name=f"{span_type.lower()}_{i}" if not is_root else "agent_run",
            context=ctx,
            parent=parent_ctx,
            attributes={
                "mlflow.traceRequestId": json.dumps(trace_id),
                "mlflow.spanType": json.dumps(span_type, cls=TraceJSONEncoder),
                **attrs,
            },
            start_time=base_ns + i * 10_000_000,
            end_time=base_ns + i * 10_000_000 + rng.randint(5_000_000, 50_000_000),
            status=trace_api.Status(trace_api.StatusCode.OK),
            resource=_OTelResource.get_empty(),
        )
        spans.append(create_mlflow_span(otel_span, trace_id, span_type))

    return trace_info, spans


def seed_traces(
    store: SqlAlchemyStore,
    experiment_id: str,
    count: int,
    spans_per_trace: int,
) -> list[str]:
    rng = random.Random(123)
    trace_ids: list[str] = []
    for _ in range(count):
        ti, sp = generate_trace_data(experiment_id, spans_per_trace, rng)
        store.start_trace(ti)
        store.log_spans(experiment_id, sp)
        trace_ids.append(ti.trace_id)
    return trace_ids


def generate_iceberg_trace_seed_bundle(
    experiment_id: str,
    trace_index: int,
    spans_per_trace: int,
    assessments_per_trace: int,
    window_days: int,
    rng: random.Random | None = None,
    seed: int | None = None,
    now_ms: int | None = None,
    range_start_ms: int | None = None,
    range_end_ms: int | None = None,
) -> TraceSeedBundle:
    if spans_per_trace < 3:
        raise ValueError("spans_per_trace must be at least 3")
    if assessments_per_trace < 1:
        raise ValueError("assessments_per_trace must be at least 1")

    if rng is None:
        if seed is None:
            raise ValueError("seed must be provided when rng is not provided")
        rng = random.Random((seed << 32) ^ trace_index)

    current_time_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    if range_start_ms is not None or range_end_ms is not None:
        if range_start_ms is None or range_end_ms is None:
            raise ValueError("range_start_ms and range_end_ms must be provided together")
        if range_end_ms <= range_start_ms:
            raise ValueError("range_end_ms must be greater than range_start_ms")
        request_time = range_start_ms + rng.randint(0, max(range_end_ms - range_start_ms - 1, 0))
    else:
        window_ms = window_days * 24 * 60 * 60 * 1000
        request_time = current_time_ms - rng.randint(0, max(window_ms - 1, 0))
    execution_duration = rng.randint(250, 8_000)

    trace_id = f"tr-{trace_index:032x}"
    trace_name = f"{NAME_PREFIXES[trace_index % len(NAME_PREFIXES)]}_{trace_index % 10_000:04d}"
    session_id = f"session-{trace_index % 2_000:05d}"
    user_id = f"user-{trace_index % 250:04d}"
    git_branch = f"feature/iceberg-benchmark-{trace_index % 6}"
    git_commit = f"{trace_index:040x}"[:12]

    input_tokens = 200 + (trace_index % 600)
    output_tokens = 80 + (trace_index % 300)
    cache_read_tokens = 40 if trace_index % 5 == 0 else 0
    cache_creation_tokens = 15 if trace_index % 11 == 0 else 0
    total_tokens = input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens
    token_usage = {
        TokenUsageKey.INPUT_TOKENS: input_tokens,
        TokenUsageKey.OUTPUT_TOKENS: output_tokens,
        TokenUsageKey.TOTAL_TOKENS: total_tokens,
        TokenUsageKey.CACHE_READ_INPUT_TOKENS: cache_read_tokens,
        TokenUsageKey.CACHE_CREATION_INPUT_TOKENS: cache_creation_tokens,
    }

    trace_state = TraceState.ERROR if trace_index % 10 == 0 else TraceState.OK
    trace_info = TraceInfo(
        trace_id=trace_id,
        trace_location=TraceLocation.from_experiment_id(experiment_id),
        request_time=request_time,
        execution_duration=execution_duration,
        state=trace_state,
        tags={
            TraceTagKey.TRACE_NAME: trace_name,
            "env": ENV_CHOICES[trace_index % len(ENV_CHOICES)],
            "scenario": "iceberg-ui-benchmark",
        },
        trace_metadata={
            TraceMetadataKey.TRACE_USER: user_id,
            TraceMetadataKey.TRACE_SESSION: session_id,
            "mlflow.source.git.branch": git_branch,
            "mlflow.source.git.commit": git_commit,
            TraceMetadataKey.TOKEN_USAGE: json.dumps(token_usage),
            TraceMetadataKey.SIZE_STATS: json.dumps({"num_spans": spans_per_trace}),
        },
    )

    base_ns = request_time * 1_000_000
    trace_num = trace_index + 1
    spans: list[Span] = []

    root_span_end_ns = base_ns + execution_duration * 1_000_000
    root_status = (
        trace_api.StatusCode.ERROR if trace_state == TraceState.ERROR else trace_api.StatusCode.OK
    )
    root_attrs = {
        SpanAttributeKey.INPUTS: json.dumps(
            {"query": f"What happened in trace {trace_index}?"},
            cls=TraceJSONEncoder,
        ),
        SpanAttributeKey.OUTPUTS: json.dumps(
            {"response": f"Trace {trace_index} completed with state {trace_state.value}"},
            cls=TraceJSONEncoder,
        ),
        SpanAttributeKey.USER_ID: json.dumps(user_id),
        SpanAttributeKey.SESSION_ID: json.dumps(session_id),
    }
    spans.append(
        _create_seed_span(
            trace_id=trace_id,
            trace_num=trace_num,
            span_id=1,
            parent_span_id=None,
            span_type=SpanType.AGENT,
            name="agent_run",
            start_ns=base_ns,
            end_ns=root_span_end_ns,
            status=root_status,
            attributes=root_attrs,
        )
    )

    llm_start_ns = base_ns + 25 * 1_000_000
    llm_end_ns = llm_start_ns + max(execution_duration // 2, 50) * 1_000_000
    llm_cost = {
        CostKey.INPUT_COST: round(input_tokens * 0.000002, 6),
        CostKey.OUTPUT_COST: round(output_tokens * 0.000004, 6),
    }
    llm_cost[CostKey.TOTAL_COST] = round(
        llm_cost[CostKey.INPUT_COST] + llm_cost[CostKey.OUTPUT_COST], 6
    )
    llm_attrs = {
        SpanAttributeKey.CHAT_USAGE: json.dumps(token_usage),
        SpanAttributeKey.LLM_COST: json.dumps(llm_cost),
        SpanAttributeKey.MODEL: json.dumps(f"gpt-benchmark-{trace_index % 4}"),
        SpanAttributeKey.MODEL_PROVIDER: json.dumps("openai"),
        SpanAttributeKey.INPUTS: json.dumps({"messages": 3}, cls=TraceJSONEncoder),
        SpanAttributeKey.OUTPUTS: json.dumps({"tokens": output_tokens}, cls=TraceJSONEncoder),
    }
    spans.append(
        _create_seed_span(
            trace_id=trace_id,
            trace_num=trace_num,
            span_id=2,
            parent_span_id=1,
            span_type=SpanType.LLM,
            name=f"llm_call_{trace_index % 5}",
            start_ns=llm_start_ns,
            end_ns=llm_end_ns,
            status=trace_api.StatusCode.OK,
            attributes=llm_attrs,
        )
    )

    tool_start_ns = base_ns + 100 * 1_000_000
    tool_end_ns = tool_start_ns + max(execution_duration // 3, 25) * 1_000_000
    tool_status = trace_api.StatusCode.ERROR if trace_index % 20 == 0 else trace_api.StatusCode.OK
    tool_outputs = {"documents": 4, "cache_hit": trace_index % 7 == 0}
    if tool_status == trace_api.StatusCode.ERROR:
        tool_outputs = {"error": "tool timeout"}
    spans.append(
        _create_seed_span(
            trace_id=trace_id,
            trace_num=trace_num,
            span_id=3,
            parent_span_id=1,
            span_type=SpanType.TOOL,
            name=f"tool_search_{trace_index % 8}",
            start_ns=tool_start_ns,
            end_ns=tool_end_ns,
            status=tool_status,
            attributes={
                SpanAttributeKey.INPUTS: json.dumps({"k": 4}, cls=TraceJSONEncoder),
                SpanAttributeKey.OUTPUTS: json.dumps(tool_outputs, cls=TraceJSONEncoder),
            },
        )
    )

    for span_offset in range(3, spans_per_trace):
        span_id = span_offset + 1
        parent_span_id = 1 if span_id % 2 == 0 else 2
        span_type = SpanType.CHAIN if span_id % 2 == 0 else SpanType.RETRIEVER
        start_ns = base_ns + (125 + span_offset * 10) * 1_000_000
        end_ns = start_ns + rng.randint(10, 40) * 1_000_000
        spans.append(
            _create_seed_span(
                trace_id=trace_id,
                trace_num=trace_num,
                span_id=span_id,
                parent_span_id=parent_span_id,
                span_type=span_type,
                name=f"{span_type.lower()}_{span_offset}",
                start_ns=start_ns,
                end_ns=end_ns,
                status=trace_api.StatusCode.OK,
                attributes={
                    SpanAttributeKey.INPUTS: json.dumps(
                        {"trace_index": trace_index}, cls=TraceJSONEncoder
                    ),
                    SpanAttributeKey.OUTPUTS: json.dumps({"ok": True}, cls=TraceJSONEncoder),
                },
            )
        )

    source = AssessmentSource(
        source_type=AssessmentSourceType.HUMAN,
        source_id="iceberg-benchmark-seeder",
    )
    assessments: list[Feedback | Expectation] = [
        Feedback(
            trace_id=trace_id,
            name="response_quality",
            value=["bad", "ok", "good", "great"][trace_index % 4],
            rationale="Deterministic categorical benchmark signal",
            source=source,
            metadata={TraceMetadataKey.TRACE_SESSION: session_id},
            create_time_ms=request_time + 1_000,
        ),
        Feedback(
            trace_id=trace_id,
            name="groundedness_score",
            value=round(0.2 + ((trace_index * 7) % 80) / 100, 2),
            rationale="Deterministic numeric benchmark signal",
            source=source,
            metadata={TraceMetadataKey.TRACE_SESSION: session_id},
            create_time_ms=request_time + 2_000,
        ),
        Expectation(
            trace_id=trace_id,
            name="expected_topic",
            value=["billing", "support", "latency"][trace_index % 3],
            source=source,
            metadata={TraceMetadataKey.TRACE_SESSION: session_id},
            create_time_ms=request_time + 3_000,
        ),
    ]

    selected_assessments = assessments[:assessments_per_trace]
    for assessment_index, assessment in enumerate(selected_assessments, start=1):
        assessment.assessment_id = f"a-{trace_index:032x}-{assessment_index:02d}"

    return TraceSeedBundle(
        trace_info=trace_info,
        spans=spans,
        assessments=selected_assessments,
    )


def _create_seed_span(
    *,
    trace_id: str,
    trace_num: int,
    span_id: int,
    parent_span_id: int | None,
    span_type: SpanType,
    name: str,
    start_ns: int,
    end_ns: int,
    status: trace_api.StatusCode,
    attributes: dict[str, object],
) -> Span:
    context = SpanContext(
        trace_id=trace_num,
        span_id=span_id,
        is_remote=False,
        trace_flags=trace_api.TraceFlags(1),
        trace_state=trace_api.TraceState(),
    )
    parent = None
    if parent_span_id is not None:
        parent = SpanContext(
            trace_id=trace_num,
            span_id=parent_span_id,
            is_remote=False,
            trace_flags=trace_api.TraceFlags(1),
            trace_state=trace_api.TraceState(),
        )

    otel_span = OTelReadableSpan(
        name=name,
        context=context,
        parent=parent,
        attributes={
            SpanAttributeKey.REQUEST_ID: json.dumps(trace_id),
            SpanAttributeKey.SPAN_TYPE: json.dumps(span_type, cls=TraceJSONEncoder),
            **attributes,
        },
        start_time=start_ns,
        end_time=end_ns,
        status=trace_api.Status(status),
        resource=_OTelResource.get_empty(),
    )
    return create_mlflow_span(otel_span, trace_id, span_type)
