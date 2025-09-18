import { matchPredefinedError } from '@databricks/web-shared/errors';
import { getDefaultHeaders } from '../../../common/utils/FetchUtils';

function serializeRequestBody(payload: any | FormData | Blob) {
  if (payload === undefined) {
    return undefined;
  }
  return typeof payload === 'string' || payload instanceof FormData || payload instanceof Blob
    ? payload
    : JSON.stringify(payload);
}

// Helper method to make a request to the backend.
export const loggedModelsDataRequest = async (
  url: string,
  method: 'POST' | 'GET' | 'PATCH' | 'DELETE' = 'GET',
  body?: any,
) => {
  const headers: Record<string, string> = {
    ...(body ? { 'Content-Type': 'application/json' } : {}),
    ...getDefaultHeaders(document.cookie),
  };
  // Inject dev Authorization header if available and not already present
  try {
    if (!('Authorization' in headers) && typeof window !== 'undefined' && window.localStorage) {
      const devToken = window.localStorage.getItem('mlflow.k8s.bearerToken');
      if (devToken) {
        headers['Authorization'] = `Bearer ${devToken}`;
      }
    }
    // Inject namespace header from URL if not explicitly provided
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
      if (ns) {
        headers['X-MLflow-Namespace'] = ns;
      }
    }
  } catch {
    // no-op
  }
  const response = await fetch(url, {
    method,
    body: serializeRequestBody(body),
    headers,
  });
  if (!response.ok) {
    const predefinedError = matchPredefinedError(response);
    if (predefinedError) {
      try {
        // Attempt to use message from the response
        const message = (await response.json()).message;
        predefinedError.message = message ?? predefinedError.message;
      } catch {
        // If the message can't be parsed, use default one
      }
      throw predefinedError;
    }
  }
  return response.json();
};
