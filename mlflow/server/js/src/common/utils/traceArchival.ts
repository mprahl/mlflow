export const TRACE_ARCHIVAL_RETENTION_TAG_KEY = 'mlflow.trace.archivalRetention';
export const TRACE_ARCHIVAL_RETENTION_PATTERN = /^[1-9][0-9]*[mhd]$/;
export const TRACE_ARCHIVAL_RETENTION_MAX_LENGTH = 32;

export type TraceArchivalRetentionValidationResult = {
  valid: boolean;
  error?: string;
};

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
