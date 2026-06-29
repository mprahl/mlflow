import { compact, isNil } from 'lodash';
import { useCallback } from 'react';

import { isV3ModelTraceInfo } from '../../model-trace-explorer/ModelTraceExplorer.utils';
import type { ModelTrace } from '../../model-trace-explorer/ModelTrace.types';
import { TracesServiceV3 } from '../../model-trace-explorer/api';
import { useArrayMemo } from '../../model-trace-explorer/hooks/useArrayMemo';
import { useQueries, useQueryClient } from '../../query-client/queryClient';

export type GetTraceFunction = (traceId?: string, traceInfo?: ModelTrace['info']) => Promise<ModelTrace | undefined>;

const QUERY_KEY = 'getTrace';
const BATCH_QUERY_KEY = 'batchGetTraces';
const DEFAULT_BATCH_SIZE = 100;

// unfortunately the util from model-trace-explorer
// requires the whole trace object, not just the info
function getModelTraceId(traceInfo: ModelTrace['info']): string {
  return isV3ModelTraceInfo(traceInfo) ? traceInfo.trace_id : (traceInfo.request_id ?? '');
}

export function useGetTraces(getTrace?: GetTraceFunction, traceInfos?: ModelTrace['info'][]) {
  const queryClient = useQueryClient();

  const queries = useQueries({
    queries: (traceInfos ?? []).map((traceInfo) => {
      const traceId = getModelTraceId(traceInfo);

      return {
        queryKey: [QUERY_KEY, traceId],
        queryFn: async () => {
          return getTrace?.(traceId, traceInfo);
        },
        enabled: !isNil(getTrace) && Boolean(traceId),
        refetchOnWindowFocus: false,
        retry: 1,
        keepPreviousData: true,
      };
    }),
  });

  const data = useArrayMemo(compact(queries.map((query) => query.data)));
  const isLoading = queries.some((query) => query.isLoading);
  const invalidateSingleTraceQuery = useCallback(
    (traceId?: string) => {
      queryClient.invalidateQueries({ queryKey: [QUERY_KEY, traceId] });
    },
    [queryClient],
  );

  return {
    data,
    isLoading,
    invalidateSingleTraceQuery,
  };
}

function chunkTraceInfos(traceInfos: ModelTrace['info'][], chunkSize: number) {
  const chunks = [];
  for (let startIndex = 0; startIndex < traceInfos.length; startIndex += chunkSize) {
    chunks.push(traceInfos.slice(startIndex, startIndex + chunkSize));
  }
  return chunks;
}

export function useGetTracesBatch(traceInfos?: ModelTrace['info'][], chunkSize = DEFAULT_BATCH_SIZE) {
  const queryClient = useQueryClient();
  const traceInfoChunks = useArrayMemo(chunkTraceInfos(traceInfos ?? [], chunkSize));

  const queries = useQueries({
    queries: traceInfoChunks.map((traceInfoChunk) => {
      const traceIds = traceInfoChunk.map(getModelTraceId).filter(Boolean);

      return {
        queryKey: [BATCH_QUERY_KEY, traceIds],
        queryFn: async () => {
          const response = await TracesServiceV3.getBatchTracesV3({ traceIds });
          return response.traces ?? [];
        },
        enabled: traceIds.length > 0,
        refetchOnWindowFocus: false,
        retry: 1,
        keepPreviousData: true,
      };
    }),
  });

  const data = useArrayMemo(queries.flatMap((query) => query.data ?? []));
  const isLoading = queries.some((query) => query.isLoading);
  const invalidateSingleTraceQuery = useCallback(
    (_traceId?: string) => {
      queryClient.invalidateQueries({ queryKey: [BATCH_QUERY_KEY] });
    },
    [queryClient],
  );

  return {
    data,
    isLoading,
    invalidateSingleTraceQuery,
  };
}
