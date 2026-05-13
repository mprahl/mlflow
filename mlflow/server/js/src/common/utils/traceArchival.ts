export const TRACE_ARCHIVAL_RETENTION_TAG_KEY = 'mlflow.trace.archivalRetention';
export const TRACE_ARCHIVAL_RETENTION_PATTERN = /^[1-9][0-9]*[mhd]$/;
export const TRACE_ARCHIVAL_RETENTION_MAX_LENGTH = 32;
export const TRACE_ARCHIVAL_URI_PATTERN = /^[a-zA-Z][a-zA-Z0-9+.-]*:.+$/;

export type TraceArchivalRetentionValidationResult = {
  valid: boolean;
  error?: string;
};

export type TraceArchivalLocationValidationResult = {
  valid: boolean;
  error?: string;
};

export type TraceArchivalRetentionUnit = 'm' | 'h' | 'd';
export const DEFAULT_TRACE_ARCHIVAL_RETENTION_UNIT: TraceArchivalRetentionUnit = 'd';

type TraceArchivalRetentionTagPayload = {
  type: 'duration';
  value: string;
};

export const validateTraceArchivalRetention = (value: string): TraceArchivalRetentionValidationResult => {
  const trimmedValue = value.trim();
  if (!trimmedValue) {
    return { valid: true };
  }

  if (trimmedValue.length > TRACE_ARCHIVAL_RETENTION_MAX_LENGTH) {
    return {
      valid: false,
      error: `Trace archival retention must be at most ${TRACE_ARCHIVAL_RETENTION_MAX_LENGTH} characters.`,
    };
  }

  if (!TRACE_ARCHIVAL_RETENTION_PATTERN.test(trimmedValue)) {
    return {
      valid: false,
      error: "Trace archival retention must use the format <int><unit>, where unit is one of 'm', 'h', or 'd'.",
    };
  }

  return { valid: true };
};

export const validateTraceArchivalLocation = (value: string): TraceArchivalLocationValidationResult => {
  const trimmedValue = value.trim();
  if (!trimmedValue) {
    return { valid: true };
  }

  if (!TRACE_ARCHIVAL_URI_PATTERN.test(trimmedValue)) {
    return {
      valid: false,
      error: 'Trace archival location must look like a URI, for example s3://bucket/path.',
    };
  }

  return { valid: true };
};

export const parseTraceArchivalRetention = (
  value: string | null | undefined,
): { amount: string; unit: TraceArchivalRetentionUnit } => {
  const trimmedValue = value?.trim() ?? '';
  const match = trimmedValue.match(/^([1-9][0-9]*)([mhd])$/);
  if (!match) {
    return { amount: '', unit: DEFAULT_TRACE_ARCHIVAL_RETENTION_UNIT };
  }

  return {
    amount: match[1],
    unit: match[2] as TraceArchivalRetentionUnit,
  };
};

export const formatTraceArchivalRetention = (amount: string, unit: TraceArchivalRetentionUnit) => {
  const trimmedAmount = amount.trim();
  return trimmedAmount ? `${trimmedAmount}${unit}` : '';
};

export const getTraceArchivalRetentionValidationError = (amount: string, unit: TraceArchivalRetentionUnit) => {
  const result = validateTraceArchivalRetention(formatTraceArchivalRetention(amount, unit));
  return result.valid ? undefined : result.error;
};

export const formatTraceArchivalRetentionForDisplay = (value: string | null | undefined) => {
  const trimmedValue = value?.trim() ?? '';
  if (!trimmedValue) {
    return '';
  }

  if (!TRACE_ARCHIVAL_RETENTION_PATTERN.test(trimmedValue)) {
    return trimmedValue;
  }

  const { amount, unit } = parseTraceArchivalRetention(trimmedValue);
  const unitLabels: Record<TraceArchivalRetentionUnit, [string, string]> = {
    d: ['day', 'days'],
    h: ['hour', 'hours'],
    m: ['minute', 'minutes'],
  };
  const pluralizedUnit = amount === '1' ? unitLabels[unit][0] : unitLabels[unit][1];

  return `${amount} ${pluralizedUnit}`;
};

export const encodeTraceArchivalRetentionTag = (value: string) =>
  JSON.stringify({
    type: 'duration',
    value: value.trim(),
  } satisfies TraceArchivalRetentionTagPayload);

export const decodeTraceArchivalRetentionTag = (value?: string | null) => {
  const trimmedValue = value?.trim() ?? '';
  if (!trimmedValue) {
    return '';
  }

  try {
    const payload = JSON.parse(trimmedValue);
    if (
      typeof payload === 'object' &&
      payload !== null &&
      payload.type === 'duration' &&
      typeof payload.value === 'string'
    ) {
      return payload.value.trim();
    }
  } catch {
    return '';
  }

  return '';
};
