import { ReferenceLine } from 'recharts';
import { useDesignSystemTheme } from '@databricks/design-system';
import { useOverviewChartContext } from '../OverviewChartContext';
import { formatTimestampForTraceMetrics } from '../utils/chartUtils';

export const useSnapshotChangeMarkers = (
  data: { name: string; timestampMs?: number }[],
  yAxisId: string | number = 0,
) => {
  const { theme } = useDesignSystemTheme();
  const { snapshotAnnotations = [], timeIntervalSeconds, timeBuckets } = useOverviewChartContext();
  const buckets = new Map<string, string[]>();
  for (const annotation of snapshotAnnotations) {
    const time = Date.parse(annotation.at);
    const timestamp = timeBuckets.find((value) => value <= time && time < value + timeIntervalSeconds * 1000);
    const bucket =
      timestamp === undefined
        ? undefined
        : data.find((point) => point.name === formatTimestampForTraceMetrics(timestamp, timeIntervalSeconds));
    if (bucket) buckets.set(bucket.name, [...(buckets.get(bucket.name) ?? []), annotation.label]);
  }
  return [...buckets].map(([name, labels]) => (
    <ReferenceLine
      key={name}
      yAxisId={yAxisId}
      x={name}
      stroke={theme.colors.textSecondary}
      strokeDasharray="3 3"
      label={{ value: labels.join(', '), position: 'insideTopRight', fontSize: 10, fill: theme.colors.textSecondary }}
    />
  ));
};
