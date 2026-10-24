import { useQuery } from '../common/utils/reactQueryHooks';
import { ErrorWrapper } from '../common/utils/ErrorWrapper';
import { MlflowService } from '../experiment-tracking/sdk/MlflowService';

export const AGENT_PREVIEW_EXPERIMENT_NAME = 'agent-registry-preview';

export const useAgentPreviewExperiment = () =>
  useQuery<string | null, Error>(['agent-registry-preview', 'experiment'], {
    queryFn: async () => {
      try {
        const response = await MlflowService.getExperimentByName({ experiment_name: AGENT_PREVIEW_EXPERIMENT_NAME });
        return response?.experiment?.experimentId ?? null;
      } catch (error) {
        if (error instanceof ErrorWrapper && error.getStatus() === 404) return null;
        throw error;
      }
    },
    retry: false,
  });
