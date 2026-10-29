import { useMemo, useState } from 'react';
import {
  AggregationType,
  MetricViewType,
  TraceMetricKey,
  TIME_BUCKET_DIMENSION_KEY,
} from '@databricks/web-shared/model-trace-explorer';
import { useQuery } from '../common/utils/reactQueryHooks';
import { queryTraceMetrics } from '../experiment-tracking/pages/experiment-overview/hooks/useTraceMetricsQuery';
import { useGenAIOverviewEvalState } from '../experiment-tracking/pages/experiment-overview/hooks/useGenAIOverviewEvalState';
import { GenAIOverviewEvalScoresChart } from '../experiment-tracking/pages/experiment-overview/components/GenAIOverviewEvalScoresChart';
import {
  Alert,
  ChartLineIcon,
  SimpleSelect,
  SimpleSelectOption,
  Typography,
  useDesignSystemTheme,
} from '@databricks/design-system';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { ExperimentGenAIOverviewPage } from '../experiment-tracking/pages/experiment-overview/ExperimentGenAIOverviewPage';
import { useOverviewChartContext } from '../experiment-tracking/pages/experiment-overview/OverviewChartContext';
import {
  OverviewChartContainer,
  OverviewChartHeader,
} from '../experiment-tracking/pages/experiment-overview/components/OverviewChartComponents';
import { formatTimestampForTraceMetrics } from '../experiment-tracking/pages/experiment-overview/utils/chartUtils';
import { useSnapshotChangeMarkers } from '../experiment-tracking/pages/experiment-overview/components/SnapshotChangeMarkers';
import { MonitoringConfigProvider } from '../experiment-tracking/hooks/useMonitoringConfig';
import { useMonitoringFilters, useMonitoringFiltersTimeRange } from '../experiment-tracking/hooks/useMonitoringFilters';
import { TracesV3Toolbar } from '../experiment-tracking/components/experiment-page/components/traces-v3/TracesV3Toolbar';
import { useSearchMlflowTraces } from '../shared/web-shared/genai-traces-table/hooks/useMlflowTraces';
import { createTraceLocationForExperiment } from '../shared/web-shared/genai-traces-table';
import { useAgentPreviewExperiment } from './useAgentPreviewExperiment';
import { getAgentTraceMetricFilters, getAgentTraceTableFilters } from './agentTraceFilters';
import { agentPath, Events, Panel } from './components';
import { getAssetChangeEvents } from './assetChanges';
import { AGENT_INSTANCE_ID_KEY } from './agentTraceColumns';
import type { Agent, AgentEvent, AgentSnapshot } from './mockData';

const colors = ['#2272B4', '#00A972', '#D99800', '#B450A5', '#D44A4A'];

export const SnapshotActivityCharts = ({ agent }: { agent: Agent }) => {
  const { timeBuckets, timeIntervalSeconds, experimentIds, startTimeMs, endTimeMs, filters } =
    useOverviewChartContext();
  const { theme } = useDesignSystemTheme();
  const traffic = useQuery({
    queryKey: [
      'agent-snapshot-traffic',
      agent.id,
      agent.snapshots.map((snapshot) => snapshot.id),
      experimentIds,
      startTimeMs,
      endTimeMs,
      timeIntervalSeconds,
      filters,
    ],
    queryFn: () =>
      Promise.all(
        agent.snapshots.map(async (snapshot) => ({
          id: snapshot.id,
          response: await queryTraceMetrics({
            experiment_ids: experimentIds,
            view_type: MetricViewType.TRACES,
            metric_name: TraceMetricKey.TRACE_COUNT,
            aggregations: [{ aggregation_type: AggregationType.COUNT }],
            time_interval_seconds: timeIntervalSeconds,
            start_time_ms: startTimeMs,
            end_time_ms: endTimeMs,
            filters: [...(filters ?? []), `trace.tag.\`mlflow.agent.snapshot_id\` = '${snapshot.id}'`],
          }),
        })),
      ),
    enabled: experimentIds.length > 0,
  });
  const counts = new Map(
    (traffic.data ?? []).map(({ id, response }) => [
      id,
      new Map(
        (response.data_points ?? []).map((point) => [
          Date.parse(point.dimensions[TIME_BUCKET_DIMENSION_KEY]),
          point.values[AggregationType.COUNT] ?? 0,
        ]),
      ),
    ]),
  );
  const data = timeBuckets.map((timestampMs) => ({
    timestampMs,
    name: formatTimestampForTraceMetrics(timestampMs, timeIntervalSeconds),
    changes: agent.snapshots.filter(
      (snapshot) =>
        Date.parse(snapshot.createdAt) >= timestampMs &&
        Date.parse(snapshot.createdAt) < timestampMs + timeIntervalSeconds * 1000,
    ).length,
    ...Object.fromEntries(
      agent.snapshots.map((snapshot) => [String(snapshot.id), counts.get(snapshot.id)?.get(timestampMs) ?? 0]),
    ),
  }));
  const markers = useSnapshotChangeMarkers(data);
  return (
    <div css={{ display: 'grid', gap: theme.spacing.md }}>
      <OverviewChartContainer componentId="mlflow.agent_registry.snapshot_traffic">
        <OverviewChartHeader icon={<ChartLineIcon />} title="Traffic by snapshot" value="Trace count" />
        {traffic.error ? (
          <Typography.Text color="error">Snapshot traffic is unavailable.</Typography.Text>
        ) : traffic.isLoading ? (
          <Typography.Text>Loading snapshot traffic...</Typography.Text>
        ) : (
          <div css={{ height: 230 }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data} margin={{ top: 20, right: 20, left: 0, bottom: 0 }}>
                <CartesianGrid vertical={false} strokeDasharray="3 3" />
                <XAxis dataKey="name" />
                <YAxis allowDecimals={false} />
                <Tooltip />
                <Legend />
                {agent.snapshots.map((snapshot, index) => (
                  <Line
                    key={snapshot.id}
                    name={`Snapshot #${snapshot.id}`}
                    dataKey={String(snapshot.id)}
                    stroke={colors[index % colors.length]}
                    strokeWidth={2}
                    dot={false}
                  />
                ))}
                {markers}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </OverviewChartContainer>
      <OverviewChartContainer componentId="mlflow.agent_registry.snapshot_frequency">
        <OverviewChartHeader
          icon={<ChartLineIcon />}
          title="Snapshot change frequency"
          value={`${data.reduce((total, point) => total + point.changes, 0)} snapshots`}
        />
        <div css={{ height: 180 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data}>
              <XAxis dataKey="name" />
              <YAxis allowDecimals={false} />
              <Tooltip />
              <Bar name="New snapshots" dataKey="changes" fill={theme.colors.blue400} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </OverviewChartContainer>
    </div>
  );
};

export const SnapshotPerformance = ({ agent, snapshot }: { agent: Agent; snapshot: AgentSnapshot }) => {
  const { data: experimentId, isLoading, error } = useAgentPreviewExperiment();
  if (isLoading) return <Typography.Text>Loading snapshot performance...</Typography.Text>;
  if (error || !experimentId) return <Typography.Text>Snapshot metrics are unavailable.</Typography.Text>;
  return (
    <ExperimentGenAIOverviewPage
      experimentIdOverride={experimentId}
      baseFilters={getAgentTraceMetricFilters({ agentId: agent.id, snapshotId: String(snapshot.id) })}
      activeTabOverride="usage"
      hideTabs
      hideIssueDetection
      hideTooltipLinks
    />
  );
};

export const SnapshotEvaluationStatistics = ({ agent, snapshot }: { agent: Agent; snapshot: AgentSnapshot }) => {
  const { theme } = useDesignSystemTheme();
  const { data: experimentId } = useAgentPreviewExperiment();
  const state = useGenAIOverviewEvalState(
    experimentId ?? '',
    `tags.\`mlflow.agent.id\` = '${agent.id.replaceAll("'", "''")}' AND tags.\`mlflow.agent.snapshot_id\` = '${snapshot.id}'`,
  );
  if (!experimentId || state.status === 'loading') {
    return <Typography.Text>Loading evaluation statistics...</Typography.Text>;
  }
  if (state.status === 'error') return <Typography.Text>Evaluation statistics are unavailable.</Typography.Text>;
  if (state.status === 'empty') {
    return <Typography.Text color="secondary">No evaluation results for this snapshot.</Typography.Text>;
  }
  const latest = state.scorePoints[state.scorePoints.length - 1];
  return (
    <div css={{ display: 'grid', gap: theme.spacing.lg }}>
      <div
        css={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: theme.spacing.md }}
      >
        {[
          ['Evaluation runs', state.runCount],
          ...state.assessmentScoreNames.map((name) => [
            name.replace('/mean', ''),
            latest?.scores[name] == null ? 'Not scored' : Number(latest.scores[name]).toFixed(2),
          ]),
        ].map(([label, value]) => (
          <div key={label}>
            <Typography.Text color="secondary">{label}</Typography.Text>
            <Typography.Title level={3}>{value}</Typography.Title>
          </div>
        ))}
      </div>
      <Panel title="Evaluation quality over time">
        <GenAIOverviewEvalScoresChart
          experimentId={experimentId}
          assessmentScoreNames={state.assessmentScoreNames}
          scorePoints={state.scorePoints}
          runUrlBuilder={(runUuid) =>
            `${agentPath(agent.id, 'evaluation-runs')}?${new URLSearchParams({ snapshot: String(snapshot.id), selectedRunUuid: runUuid })}`
          }
        />
      </Panel>
    </div>
  );
};

const SnapshotTimelineContent = ({ agent, snapshotIds }: { agent: Agent; snapshotIds: number[] }) => {
  const { theme } = useDesignSystemTheme();
  const [kind, setKind] = useState('all');
  const [{ startTime, endTime }] = useMonitoringFilters();
  const timeRange = useMonitoringFiltersTimeRange();
  const { data: experimentId } = useAgentPreviewExperiment();
  const locations = useMemo(
    () => (experimentId ? [createTraceLocationForExperiment(experimentId)] : []),
    [experimentId],
  );
  const filters = useMemo(() => getAgentTraceTableFilters({ agentId: agent.id }), [agent.id]);
  const { data: traces = [], error } = useSearchMlflowTraces({
    locations,
    filters,
    timeRange,
    limit: 100,
    pageSize: 100,
    enablePagination: false,
    disabled: !experimentId,
  });
  const runtimeEvents: AgentEvent[] = traces.map((trace) => ({
    id: trace.trace_id,
    at: trace.request_time,
    title: `Trace: ${trace.request_preview ?? trace.trace_id}`,
    detail: `${trace.state} · ${trace.execution_duration ?? ''}`,
    kind: 'trace',
    instanceId: trace.trace_metadata?.[AGENT_INSTANCE_ID_KEY],
    snapshotId: Number(trace.tags['mlflow.agent.snapshot_id']) || undefined,
    href: `${agentPath(agent.id, 'traces')}?${new URLSearchParams({ selectedTraceId: trace.trace_id })}`,
  }));
  const events = [
    ...getAssetChangeEvents(agent),
    ...agent.events
      .filter((event) => event.kind !== 'snapshot' && event.kind !== 'trace')
      .map((event) => ({
        ...event,
        href:
          event.kind === 'sync'
            ? agentPath(agent.id, 'endpoints')
            : event.snapshotId
              ? `${agentPath(agent.id, 'snapshots')}?snapshot=${event.snapshotId}`
              : undefined,
      })),
    ...runtimeEvents,
  ]
    .filter(
      (event) =>
        (!event.snapshotId || snapshotIds.includes(event.snapshotId)) &&
        (kind === 'all' || event.kind === kind) &&
        (!startTime || Date.parse(event.at) >= Date.parse(startTime)) &&
        (!endTime || Date.parse(event.at) <= Date.parse(endTime)),
    )
    .sort((left, right) => right.at.localeCompare(left.at));
  return (
    <div css={{ display: 'grid', gap: theme.spacing.md }}>
      <TracesV3Toolbar viewState="logs" />
      <SimpleSelect
        id="snapshot-timeline-kind"
        componentId="mlflow.agent_registry.timeline.kind"
        aria-label="Activity type"
        value={kind}
        onChange={(event) => setKind(event.target.value)}
      >
        <SimpleSelectOption value="all">All activity</SimpleSelectOption>
        <SimpleSelectOption value="snapshot">Snapshot changes</SimpleSelectOption>
        <SimpleSelectOption value="trace">Traces</SimpleSelectOption>
        <SimpleSelectOption value="evaluation">Evaluations</SimpleSelectOption>
      </SimpleSelect>
      {error && (
        <Alert
          componentId="mlflow.agent_registry.timeline.error"
          type="warning"
          message="Runtime activity is unavailable. Snapshot changes are still shown."
        />
      )}
      <Events events={events} />
    </div>
  );
};

export const SnapshotTimeline = (props: { agent: Agent; snapshotIds: number[] }) => (
  <MonitoringConfigProvider>
    <SnapshotTimelineContent {...props} />
  </MonitoringConfigProvider>
);
