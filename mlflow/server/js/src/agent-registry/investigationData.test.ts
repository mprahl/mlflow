import { describe, expect, it } from '@jest/globals';
import {
  getDependencyEvidence,
  getSnapshotAttribution,
  getTraceInstances,
  getTraceTokens,
  investigationTraces,
  toModelTrace,
} from './investigationData';
import { getSnapshotAssetChanges } from './assetChanges';
import { agents } from './mockData';
import { parseModelTraceToTreeWithMultipleRoots } from '../shared/web-shared/model-trace-explorer/v2/ModelTraceExplorer.utils';

const example = (id: string) => {
  const trace = investigationTraces.find((item) => item.id === id);
  if (!trace) throw new Error(`Missing example ${id}`);
  return trace;
};

describe('investigation evidence', () => {
  it('computes recorded tokens from model spans without counting tool calls', () => {
    expect(getTraceTokens(example('example-support-before'))).toBe(620);
    expect(getTraceTokens(example('example-support-after'))).toBe(1460);
  });

  it('distinguishes repeated calls from explicit retries and fallbacks', () => {
    const repeated = getDependencyEvidence(example('example-support-after'), 'lookup_order');
    expect(repeated.calls).toHaveLength(2);
    expect(repeated.offers).toHaveLength(2);
    expect(repeated.retries).toHaveLength(0);
    expect(repeated.fallbacks).toHaveLength(0);
    const recovered = getDependencyEvidence(example('example-support-recovered'), 'lookup_order');
    expect(recovered.failures.map((span) => span.id)).toEqual(['lookup', 'retry-lookup']);
    expect(recovered.retries.map((span) => span.id)).toEqual(['retry-lookup']);
    expect(recovered.fallbacks.map((span) => span.id)).toEqual(['escalate']);
  });

  it('distinguishes tools offered from tools invoked', () => {
    const evidence = getDependencyEvidence(example('example-support-before'), 'escalate_case');
    expect(evidence.offers).toHaveLength(1);
    expect(evidence.calls).toHaveLength(0);
  });

  it('includes instances recorded only on child spans', () => {
    expect(getTraceInstances(example('example-support-mixed'))).toEqual(['support-worker-a', 'support-worker-b']);
    expect(getTraceInstances(example('example-support-unattributed'))).toEqual([]);
  });

  it.each([
    ['example-support-before', 'Snapshot #7'],
    ['example-support-mixed', 'Multiple snapshots (#7, #8)'],
    ['example-support-unattributed', 'No snapshot'],
  ])('preserves attribution for %s', (id, expected) => {
    expect(getSnapshotAttribution(example(id))).toBe(expected);
  });

  it('compares arbitrary snapshot pairs in either direction', () => {
    const agent = agents[0];
    const [candidate, baseline] = agent.snapshots;
    expect(getSnapshotAssetChanges(agent, candidate, baseline)).toEqual([
      expect.objectContaining({
        change: 'updated',
        previousVersion: '3',
        asset: expect.objectContaining({ version: '4' }),
      }),
    ]);
    expect(getSnapshotAssetChanges(agent, baseline, candidate)).toEqual([
      expect.objectContaining({
        change: 'updated',
        previousVersion: '4',
        asset: expect.objectContaining({ version: '3' }),
      }),
    ]);
    expect(getSnapshotAssetChanges(agent, candidate, candidate)).toEqual([]);
  });

  it.each(investigationTraces)('renders $id with the real trace parser and bounded span times', (trace) => {
    const model = toModelTrace(trace);
    const roots = parseModelTraceToTreeWithMultipleRoots(model);
    expect(roots).toHaveLength(1);
    expect(roots[0].children).toHaveLength(trace.spans.length);
    expect(roots[0].title).toBe(trace.request);
    for (const span of trace.spans) {
      expect(span.startMs + span.durationMs).toBeLessThanOrEqual(trace.durationMs);
      if (span.retryOf) expect(trace.spans.some((item) => item.id === span.retryOf)).toBe(true);
      if (span.fallbackFor) expect(trace.spans.some((item) => item.id === span.fallbackFor && item.error)).toBe(true);
    }
    expect(model.info).toMatchObject({ trace_id: trace.id, state: trace.outcome });
  });
});
