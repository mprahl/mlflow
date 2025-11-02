import { getAjaxUrl } from '@mlflow/mlflow/src/common/utils/FetchUtils';
import type { ModelTrace, ModelTraceData } from '@databricks/web-shared/model-trace-explorer';

// returns ModelTrace if the request is successful, otherwise returns an error message
export async function getTraceArtifact(requestId: string): Promise<ModelTrace | string> {
  try {
    // eslint-disable-next-line no-restricted-globals -- See go/spog-fetch
    const endpoint = getAjaxUrl('ajax-api/2.0/mlflow/get-trace-artifact');
    const url = `${endpoint}?${new URLSearchParams({ request_id: requestId }).toString()}`;
    const result = await fetch(url);
    const text = await result.text();

    const jsonData = JSON.parse(text);
    // successful request containing span data
    if (jsonData.spans) {
      return {
        info: {
          request_id: requestId,
        },
        data: jsonData as ModelTraceData,
      };
    }

    if (jsonData.error_code) {
      return jsonData.message;
    }

    return 'Unknown error occurred';
  } catch (e) {
    if (e instanceof Error) {
      return e.message;
    }

    if (typeof e === 'string') {
      return e;
    }

    return 'Unknown error occurred';
  }
}
