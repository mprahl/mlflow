import { describe, expect, test } from '@jest/globals';
import { DEFAULT_TRACE_ARCHIVAL_RETENTION_UNIT, parseTraceArchivalRetention } from './traceArchival';

describe('traceArchival', () => {
  test('treats invalid retention strings as empty input state', () => {
    expect(parseTraceArchivalRetention('abcd')).toEqual({
      amount: '',
      unit: DEFAULT_TRACE_ARCHIVAL_RETENTION_UNIT,
    });
    expect(parseTraceArchivalRetention('0d')).toEqual({
      amount: '',
      unit: DEFAULT_TRACE_ARCHIVAL_RETENTION_UNIT,
    });
  });
});
