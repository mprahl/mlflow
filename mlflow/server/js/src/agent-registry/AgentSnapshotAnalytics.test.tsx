import { describe, expect, it, jest } from '@jest/globals';
import { screen } from '@testing-library/react';
import { renderWithDesignSystem } from '../common/utils/TestUtils.react18';
import { OverviewChartProvider } from '../experiment-tracking/pages/experiment-overview/OverviewChartContext';
import { useQuery } from '../common/utils/reactQueryHooks';
import { SnapshotActivityCharts } from './AgentSnapshotAnalytics';
import { agents } from './mockData';

jest.mock('recharts', () => ({
  ...jest.requireActual<typeof import('../../__mocks__/recharts')>('../../__mocks__/recharts'),
  CartesianGrid: () => null,
}));

jest.mock('../common/utils/reactQueryHooks', () => ({
  ...jest.requireActual<typeof import('../common/utils/reactQueryHooks')>('../common/utils/reactQueryHooks'),
  useQuery: jest.fn(() => ({ data: [{ id: 9, response: {} }], isLoading: false, error: null })),
}));

describe('snapshot traffic and change charts', () => {
  it('handles empty API responses without losing the snapshot frequency chart', () => {
    renderWithDesignSystem(
      <OverviewChartProvider
        experimentIds={['preview-exp']}
        timeIntervalSeconds={86400}
        timeBuckets={[Date.parse('2026-10-27T00:00:00Z')]}
        filters={["trace.tag.`mlflow.agent.id` = 'support-agent'"]}
      >
        <SnapshotActivityCharts agent={agents[0]} />
      </OverviewChartProvider>,
    );
    expect(screen.getByText('Traffic by snapshot')).toBeInTheDocument();
    expect(screen.getByText('Snapshot change frequency')).toBeInTheDocument();
    expect(screen.getByText('1 snapshots')).toBeInTheDocument();
    expect(useQuery).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: expect.arrayContaining(['agent-snapshot-traffic', 'support-agent']) }),
    );
  });
});
