import type { ReactNode } from 'react';
import { useDesignSystemTheme } from '@databricks/design-system';
import { Bar, BarChart, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

import {
  DEFAULT_CHART_CONTENT_HEIGHT,
  OverviewChartContainer,
  OverviewChartEmptyState,
  OverviewChartHeader,
} from '../experiment-tracking/pages/experiment-overview/components/OverviewChartComponents';

export const AgentMockChart = ({
  icon,
  title,
  value,
  values,
  type,
  action,
}: {
  icon: ReactNode;
  title: ReactNode;
  value: ReactNode;
  values: number[];
  type: 'bar' | 'line';
  action?: ReactNode;
}) => {
  const { theme } = useDesignSystemTheme();
  const data = values.map((point, index) => ({
    name: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][index],
    value: point,
  }));
  const axisProps = { tick: { fill: theme.colors.textSecondary, fontSize: 11 }, axisLine: false, tickLine: false };

  return (
    <OverviewChartContainer componentId="mlflow.agent_registry.dashboard.chart">
      <div css={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <OverviewChartHeader icon={icon} title={title} value={value} />
        {action}
      </div>
      <div css={{ height: DEFAULT_CHART_CONTENT_HEIGHT }}>
        {values.length ? (
          <ResponsiveContainer width="100%" height="100%">
            {type === 'bar' ? (
              <BarChart data={data} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
                <XAxis dataKey="name" {...axisProps} />
                <YAxis {...axisProps} />
                <Tooltip />
                <Bar dataKey="value" fill={theme.colors.blue400} radius={[4, 4, 0, 0]} />
              </BarChart>
            ) : (
              <LineChart data={data} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
                <XAxis dataKey="name" {...axisProps} />
                <YAxis {...axisProps} />
                <Tooltip />
                <Line type="monotone" dataKey="value" stroke={theme.colors.blue400} strokeWidth={2} dot={false} />
              </LineChart>
            )}
          </ResponsiveContainer>
        ) : (
          <OverviewChartEmptyState />
        )}
      </div>
    </OverviewChartContainer>
  );
};
