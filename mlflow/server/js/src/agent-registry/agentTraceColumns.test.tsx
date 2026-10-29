import { describe, expect, it } from '@jest/globals';
import { useDesignSystemTheme } from '@databricks/design-system';
import { useIntl } from 'react-intl';

import { renderWithDesignSystem, screen } from '../common/utils/TestUtils.react18';
import { traceInfoCellRenderer } from '../shared/web-shared/genai-traces-table/cellRenderers/rendererFunctions';
import { convertTraceInfoV3ToRunEvalEntry } from '../shared/web-shared/genai-traces-table/utils/TraceUtils';
import { AGENT_INSTANCE_ID_KEY, agentTraceColumns } from './agentTraceColumns';

const InstanceCell = ({ instanceId }: { instanceId?: string }) => {
  const intl = useIntl();
  const { theme } = useDesignSystemTheme();
  return traceInfoCellRenderer(
    false,
    agentTraceColumns[0].id,
    {
      currentRunValue: convertTraceInfoV3ToRunEvalEntry({
        trace_id: 'tr-instance-column',
        trace_location: { type: 'MLFLOW_EXPERIMENT', mlflow_experiment: { experiment_id: 'test-experiment' } },
        request_time: '2026-10-24T12:00:00Z',
        state: 'OK',
        tags: { 'mlflow.agent.id': 'support-agent', 'mlflow.agent.snapshot_id': '8' },
        trace_metadata: instanceId === undefined ? {} : { [AGENT_INSTANCE_ID_KEY]: instanceId },
      }),
    },
    () => {},
    intl,
    theme,
  );
};

describe('agent trace instance column', () => {
  it('renders the recorded instance ID with its full value available on hover', () => {
    renderWithDesignSystem(<InstanceCell instanceId="support-worker-a" />);
    expect(screen.getByText('support-worker-a')).toHaveAttribute('title', 'support-worker-a');
  });

  it.each([undefined, ''])('leaves %s instance metadata empty rather than inferring it', (instanceId) => {
    const { container } = renderWithDesignSystem(<InstanceCell instanceId={instanceId} />);
    expect(container.textContent).toBe('');
  });
});
