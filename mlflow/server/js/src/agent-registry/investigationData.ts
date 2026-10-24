import type { ModelTrace, ModelTraceSpanV3 } from '../shared/web-shared/model-trace-explorer/ModelTrace.types';
import type { Environment } from './mockData';

export type InvestigationSpan = {
  id: string;
  name: string;
  type: 'LLM' | 'TOOL';
  startMs: number;
  durationMs: number;
  instanceId?: string;
  harness?: string;
  snapshotId?: number;
  asset?: string;
  offeredTools?: string[];
  inputTokens?: number;
  outputTokens?: number;
  error?: string;
  retryOf?: string;
  fallbackFor?: string;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
};

export type InvestigationTrace = {
  id: string;
  agentId: string;
  request: string;
  caseId: string;
  at: string;
  environment: Environment;
  session: string;
  snapshotIds: number[];
  publisherVersion?: string;
  instanceId?: string;
  harness?: string;
  availableSkills?: string[];
  activatedSkills?: string[];
  outcome: 'OK' | 'ERROR';
  assessment?: string;
  durationMs: number;
  spans: InvestigationSpan[];
};

const plan = (snapshotId?: number, inputTokens = 200): InvestigationSpan => ({
  id: 'plan',
  name: 'Plan response',
  type: 'LLM',
  startMs: 0,
  durationMs: 300,
  snapshotId,
  asset: snapshotId === 8 ? 'prompts:/support-system/4' : snapshotId === 7 ? 'prompts:/support-system/3' : undefined,
  offeredTools: ['lookup_order', 'escalate_case'],
  inputTokens,
  outputTokens: 40,
  inputs: { question: 'Can I return order 123?' },
  outputs: { tool: 'lookup_order' },
});

const lookup = (overrides: Partial<InvestigationSpan> = {}): InvestigationSpan => ({
  id: 'lookup',
  name: 'lookup_order',
  type: 'TOOL',
  startMs: 300,
  durationMs: 250,
  asset: 'mcp:/com.example/order-lookup/1.0.0',
  inputs: { order_id: '123' },
  outputs: { delivered: true, eligible: true },
  ...overrides,
});

const answer = (snapshotId?: number, startMs = 600): InvestigationSpan => ({
  id: 'answer',
  name: 'Compose answer',
  type: 'LLM',
  startMs,
  durationMs: 300,
  snapshotId,
  inputTokens: 300,
  outputTokens: 80,
  inputs: { eligible: true },
  outputs: { answer: 'Your order is eligible for a return.' },
});

const supportTrace = (
  overrides: Pick<InvestigationTrace, 'id' | 'at' | 'snapshotIds' | 'spans'> & Partial<InvestigationTrace>,
): InvestigationTrace => ({
  agentId: 'support-agent',
  request: 'Can I return order 123?',
  caseId: 'refund-eligibility',
  environment: 'prod',
  session: 'support-example-session',
  publisherVersion: '2.4.0',
  instanceId: 'support-worker-a',
  harness: 'LangGraph 1.0',
  availableSkills: ['skills:/refund-policy/2'],
  activatedSkills: ['skills:/refund-policy/2'],
  outcome: 'OK',
  assessment: 'Refund policy: passed (example assessment)',
  durationMs: 900,
  ...overrides,
});

// Deliberately separate from the seeded experiment: these traces never leave the browser.
export const investigationTraces: InvestigationTrace[] = [
  supportTrace({
    id: 'example-support-before',
    at: '2026-10-20T10:00:00Z',
    snapshotIds: [7],
    spans: [plan(7), lookup({ snapshotId: 7 }), answer(7)],
  }),
  supportTrace({
    id: 'example-support-after',
    at: '2026-10-22T10:00:00Z',
    snapshotIds: [8],
    durationMs: 1800,
    spans: [
      plan(8, 400),
      lookup({ snapshotId: 8 }),
      {
        ...plan(8, 600),
        id: 'verify',
        name: 'Verify return eligibility',
        startMs: 600,
        inputs: { previous_result: 'eligible', instruction: 'Confirm eligibility before responding' },
        outputs: { tool: 'lookup_order', reason: 'Verify eligibility' },
      },
      lookup({ id: 'verify-lookup', snapshotId: 8, startMs: 1000 }),
      answer(8, 1500),
    ],
  }),
  supportTrace({
    id: 'example-support-recovered',
    at: '2026-10-22T11:00:00Z',
    snapshotIds: [8],
    instanceId: 'support-worker-b',
    assessment: 'Self-service resolution: failed (example assessment)',
    durationMs: 1400,
    spans: [
      plan(8),
      lookup({ snapshotId: 8, error: 'HTTP 401: expired credential', outputs: {} }),
      lookup({
        id: 'retry-lookup',
        snapshotId: 8,
        startMs: 600,
        error: 'HTTP 401: expired credential',
        retryOf: 'lookup',
        outputs: {},
      }),
      {
        id: 'escalate',
        name: 'escalate_case',
        type: 'TOOL',
        snapshotId: 8,
        startMs: 950,
        durationMs: 350,
        fallbackFor: 'retry-lookup',
        inputs: { reason: 'Order lookup unavailable' },
        outputs: { ticket_id: 'example-ticket', response: 'A support specialist will follow up.' },
      },
    ],
  }),
  supportTrace({
    id: 'example-support-failed',
    at: '2026-10-22T12:00:00Z',
    snapshotIds: [8],
    instanceId: 'support-worker-b',
    outcome: 'ERROR',
    assessment: undefined,
    spans: [
      plan(8),
      lookup({ snapshotId: 8, error: 'HTTP 401: expired credential', outputs: {} }),
      lookup({
        id: 'retry-lookup',
        snapshotId: 8,
        startMs: 600,
        error: 'HTTP 401: expired credential',
        retryOf: 'lookup',
        outputs: {},
      }),
    ],
  }),
  supportTrace({
    id: 'example-support-mixed',
    at: '2026-10-21T13:08:00Z',
    snapshotIds: [7, 8],
    spans: [plan(7), lookup({ snapshotId: 7, instanceId: 'support-worker-b' }), answer(8)],
    assessment: undefined,
  }),
  supportTrace({
    id: 'example-support-unattributed',
    at: '2026-10-23T10:00:00Z',
    environment: 'unassigned',
    snapshotIds: [],
    publisherVersion: undefined,
    instanceId: undefined,
    harness: undefined,
    availableSkills: undefined,
    activatedSkills: undefined,
    assessment: undefined,
    spans: [{ ...plan(), offeredTools: undefined }, lookup(), answer()],
  }),
  {
    id: 'example-knowledge-current',
    agentId: 'knowledge-agent',
    request: 'Find the return policy',
    caseId: 'policy-search',
    at: '2026-10-22T09:00:00Z',
    environment: 'dev',
    session: 'knowledge-example-session-1',
    snapshotIds: [],
    instanceId: 'knowledge-worker-a',
    harness: 'Claude Code 2.0',
    outcome: 'OK',
    durationMs: 600,
    spans: [
      {
        id: 'search',
        name: 'search_context',
        type: 'TOOL',
        startMs: 100,
        durationMs: 400,
        inputs: { query: 'return policy' },
        outputs: { matches: ['Return policy'] },
        asset: 'mcp:/com.example/docs-search/2.0.0',
      },
    ],
  },
  {
    id: 'example-knowledge-error',
    agentId: 'knowledge-agent',
    request: 'Find the return policy',
    caseId: 'policy-search',
    at: '2026-10-22T09:05:00Z',
    environment: 'dev',
    session: 'knowledge-example-session-2',
    snapshotIds: [],
    instanceId: 'knowledge-worker-b',
    harness: 'Claude Code 2.1',
    outcome: 'ERROR',
    durationMs: 600,
    spans: [
      {
        id: 'search',
        name: 'search_context',
        type: 'TOOL',
        startMs: 100,
        durationMs: 400,
        error: 'Search backend configuration missing',
        inputs: { query: 'return policy' },
        outputs: {},
      },
    ],
  },
];

export const getTraceInstances = (trace: InvestigationTrace) =>
  Array.from(
    new Set([trace.instanceId, ...trace.spans.map((span) => span.instanceId)].filter((id): id is string => !!id)),
  );

export const getTraceTokens = (trace: InvestigationTrace) =>
  trace.spans.reduce((total, span) => total + (span.inputTokens ?? 0) + (span.outputTokens ?? 0), 0);

export const getSnapshotAttribution = (trace: InvestigationTrace) =>
  trace.snapshotIds.length === 0
    ? 'No snapshot'
    : trace.snapshotIds.length === 1
      ? `Snapshot #${trace.snapshotIds[0]}`
      : `Multiple snapshots (${trace.snapshotIds.map((id) => `#${id}`).join(', ')})`;

export const getDependencyEvidence = (trace: InvestigationTrace, tool: string) => {
  const calls = trace.spans.filter((span) => span.type === 'TOOL' && span.name === tool);
  const failures = calls.filter((span) => span.error);
  const offers = trace.spans.filter((span) => span.offeredTools?.includes(tool));
  const retries = calls.filter((span) => span.retryOf && calls.some((call) => call.id === span.retryOf));
  const fallbacks = trace.spans.filter(
    (span) => span.fallbackFor && failures.some((failure) => failure.id === span.fallbackFor),
  );
  return { calls, failures, offers, retries, fallbacks };
};

export const toModelTrace = (trace: InvestigationTrace): ModelTrace => {
  const start = BigInt(Date.parse(trace.at)) * 1_000_000n;
  const timestamp = (offset: number) => String(start + BigInt(offset) * 1_000_000n);
  const spanAttributes = (span: InvestigationSpan): ModelTraceSpanV3['attributes'] => ({
    'mlflow.spanType': JSON.stringify(span.type),
    'mlflow.spanInputs': JSON.stringify(span.inputs),
    'mlflow.spanOutputs': JSON.stringify(span.outputs),
    'service.instance.id': JSON.stringify(span.instanceId ?? trace.instanceId ?? 'Not recorded'),
    'agent.preview.harness': JSON.stringify(span.harness ?? trace.harness ?? 'Not recorded'),
    ...(span.snapshotId !== undefined ? { 'mlflow.agent.snapshot_id': JSON.stringify(String(span.snapshotId)) } : {}),
    ...(span.asset ? { 'agent.preview.observed_asset': JSON.stringify(span.asset) } : {}),
    ...(span.offeredTools ? { 'agent.preview.offered_tools': JSON.stringify(span.offeredTools) } : {}),
    ...(span.retryOf ? { 'agent.preview.retry_of': JSON.stringify(span.retryOf) } : {}),
    ...(span.fallbackFor ? { 'agent.preview.fallback_for': JSON.stringify(span.fallbackFor) } : {}),
    ...(span.inputTokens !== undefined
      ? {
          'mlflow.chat.tokenUsage': JSON.stringify({
            input_tokens: span.inputTokens,
            output_tokens: span.outputTokens ?? 0,
            total_tokens: span.inputTokens + (span.outputTokens ?? 0),
          }),
        }
      : {}),
  });
  return {
    info: {
      trace_id: trace.id,
      trace_location: {
        type: 'MLFLOW_EXPERIMENT',
        mlflow_experiment: { experiment_id: 'agent-investigation-example' },
      },
      request_time: trace.at,
      execution_duration: `${trace.durationMs / 1000}s`,
      state: trace.outcome,
      tags: { 'mlflow.agent.id': trace.agentId, 'mlflow.agent.environment': trace.environment },
    },
    data: {
      spans: [
        {
          trace_id: trace.id,
          span_id: 'root',
          trace_state: '',
          parent_span_id: null,
          name: trace.request,
          start_time_unix_nano: timestamp(0),
          end_time_unix_nano: timestamp(trace.durationMs),
          status: { code: trace.outcome === 'OK' ? 'STATUS_CODE_OK' : 'STATUS_CODE_ERROR' },
          attributes: {
            'mlflow.spanType': JSON.stringify('AGENT'),
            'mlflow.spanInputs': JSON.stringify({ question: trace.request }),
            'mlflow.spanOutputs': JSON.stringify({ outcome: trace.outcome }),
            'service.instance.id': JSON.stringify(trace.instanceId ?? 'Not recorded'),
          },
        },
        ...trace.spans.map(
          (span): ModelTraceSpanV3 => ({
            trace_id: trace.id,
            span_id: span.id,
            trace_state: '',
            parent_span_id: 'root',
            name: span.name,
            start_time_unix_nano: timestamp(span.startMs),
            end_time_unix_nano: timestamp(span.startMs + span.durationMs),
            status: { code: span.error ? 'STATUS_CODE_ERROR' : 'STATUS_CODE_OK', message: span.error },
            attributes: spanAttributes(span),
          }),
        ),
      ],
    },
  };
};
