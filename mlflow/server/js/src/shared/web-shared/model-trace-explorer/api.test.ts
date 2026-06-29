import { beforeEach, describe, expect, it, jest } from '@jest/globals';

import { fetchAPI } from './ModelTraceExplorer.request.utils';
import { getBatchTracesV3 } from './api';

jest.mock('./ModelTraceExplorer.request.utils', () => ({
  fetchAPI: jest.fn(),
  getAjaxUrl: jest.fn((url: string) => url),
}));

describe('getBatchTracesV3', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('maps protobuf trace spans to model trace data', async () => {
    const traceInfo = { trace_id: 'tr-1' };
    const spans = [{ trace_id: 'tr-1', span_id: 'span-1' }];
    jest.mocked(fetchAPI).mockResolvedValue({
      traces: [{ trace_info: traceInfo, spans }],
    });

    const result = await getBatchTracesV3({ traceIds: ['tr-1'] });

    expect(fetchAPI).toHaveBeenCalledWith('ajax-api/3.0/mlflow/traces/batchGet?trace_ids=tr-1', 'GET');
    expect(result).toEqual({
      traces: [{ info: traceInfo, data: { spans } }],
    });
  });
});
