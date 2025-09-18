import { getDefaultHeaders } from '@mlflow/mlflow/src/common/utils/FetchUtils';
import { matchPredefinedError } from '../../errors';

// Normalizes various header inputs into a plain object
const normalizeHeaders = (h: HeadersInit | undefined): Record<string, string> => {
  if (!h) return {};
  if (h instanceof Headers) {
    const out: Record<string, string> = {};
    h.forEach((v, k) => (out[k] = v));
    return out;
  }
  if (Array.isArray(h)) {
    return Object.fromEntries(h as Array<[string, string]>);
  }
  return { ...(h as Record<string, string>) };
};

// Wrapper around global fetch to inject auth/namespace headers by default
// eslint-disable-next-line no-restricted-globals
export const fetchFn: typeof fetch = (input: RequestInfo | URL, init?: RequestInit) => {
  const merged: RequestInit = { ...(init || {}) };
  const headers: Record<string, string> = {
    ...normalizeHeaders(init?.headers),
    ...getDefaultHeaders(document.cookie),
  };
  try {
    if (!('Authorization' in headers) && typeof window !== 'undefined' && window.localStorage) {
      const devToken = window.localStorage.getItem('mlflow.k8s.bearerToken');
      if (devToken) {
        headers['Authorization'] = `Bearer ${devToken}`;
      }
    }
    if (!('X-MLflow-Namespace' in headers)) {
      let ns: string | null = null;
      if (typeof window !== 'undefined') {
        const hash = window.location.hash || '';
        const qIndex = hash.indexOf('?');
        if (qIndex >= 0) {
          const qs = new URLSearchParams(hash.substring(qIndex + 1));
          ns = qs.get('ns');
        }
        if (!ns) {
          const search = window.location.search || '';
          if (search) {
            const qs2 = new URLSearchParams(search.startsWith('?') ? search.substring(1) : search);
            ns = qs2.get('ns');
          }
        }
      }
      if (ns) headers['X-MLflow-Namespace'] = ns;
    }
  } catch {
    // no-op
  }
  merged.headers = headers;
  return fetch(input, merged);
};

export const makeRequest = async <T>(path: string, method: 'POST' | 'GET', body?: T) => {
  const options: RequestInit = {
    method,
    headers: {
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      ...getDefaultHeaders(document.cookie),
    },
  };

  if (body) {
    options.body = JSON.stringify(body);
  }
  const response = await fetchFn(path, options);

  if (!response.ok) {
    const error = matchPredefinedError(response);
    try {
      const errorMessageFromResponse = await (await response.json()).message;
      if (errorMessageFromResponse) {
        error.message = errorMessageFromResponse;
      }
    } catch {
      // do nothing
    }
    throw error;
  }

  return response.json();
};

export const getAjaxUrl = (relativeUrl: any) => {
  if (process.env['MLFLOW_USE_ABSOLUTE_AJAX_URLS'] === 'true' && !relativeUrl.startsWith('/')) {
    return '/' + relativeUrl;
  }
  return relativeUrl;
};
