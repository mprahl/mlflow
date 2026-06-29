import { describe, beforeEach, afterEach, jest, test, expect } from '@jest/globals';
import { renderHook, waitFor } from '@testing-library/react';
import React from 'react';

import { TracesServiceV3 } from '../../model-trace-explorer/api';
import type { ModelTrace, ModelTraceInfoV3 } from '../../model-trace-explorer/ModelTrace.types';
import { QueryClient, QueryClientProvider } from '../../query-client/queryClient';

import { useGetTracesBatch } from './useGetTraces';

const makeTraceInfo = (traceId: string): ModelTraceInfoV3 => ({
  trace_id: traceId,
  trace_location: { type: 'MLFLOW_EXPERIMENT', mlflow_experiment: { experiment_id: 'exp-1' } },
  request_time: '1625247600000',
  state: 'OK',
  trace_metadata: {},
  tags: {},
});

const makeTrace = (traceId: string): ModelTrace & { info: ModelTraceInfoV3 } => ({
  info: makeTraceInfo(traceId),
  data: { spans: [] },
});

describe('useGetTracesBatch', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient();
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );

  test('fetches traces in chunks', async () => {
    const batchGetSpy = jest.spyOn(TracesServiceV3, 'getBatchTracesV3').mockImplementation(async ({ traceIds }) => ({
      traces: traceIds.map(makeTrace),
    }));

    const traceInfos = [makeTraceInfo('trace-1'), makeTraceInfo('trace-2'), makeTraceInfo('trace-3')];
    const { result } = renderHook(() => useGetTracesBatch(traceInfos, 2), { wrapper });

    await waitFor(() => {
      expect(result.current.data).toHaveLength(3);
    });

    expect(batchGetSpy).toHaveBeenCalledTimes(2);
    expect(batchGetSpy).toHaveBeenNthCalledWith(1, { traceIds: ['trace-1', 'trace-2'] });
    expect(batchGetSpy).toHaveBeenNthCalledWith(2, { traceIds: ['trace-3'] });
    expect(result.current.data.map((trace) => trace.info.trace_id)).toEqual(['trace-1', 'trace-2', 'trace-3']);
  });
});
