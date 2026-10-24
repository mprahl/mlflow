import { useMemo } from 'react';
import {
  Alert,
  Table,
  TableCell,
  TableHeader,
  TableRow,
  Typography,
  useDesignSystemTheme,
} from '@databricks/design-system';

import { useSearchParams } from '../common/utils/RoutingUtils';
import { MonitoringConfigProvider } from '../experiment-tracking/hooks/useMonitoringConfig';
import { useMonitoringFiltersTimeRange } from '../experiment-tracking/hooks/useMonitoringFilters';
import { TracesV3Logs } from '../experiment-tracking/components/experiment-page/components/traces-v3/TracesV3Logs';
import { TracesV3Toolbar } from '../experiment-tracking/components/experiment-page/components/traces-v3/TracesV3Toolbar';
import { EnvironmentFilter, formatDate } from './components';
import { createTraceLocationForExperiment } from '../shared/web-shared/genai-traces-table';
import { useSearchMlflowTraces } from '../shared/web-shared/genai-traces-table/hooks/useMlflowTraces';
import { getAgentTraceTableFilters } from './agentTraceFilters';
import { useAgentPreviewExperiment } from './useAgentPreviewExperiment';

const AgentSnapshotTracesContent = ({
  agentId,
  snapshotId,
  environment,
}: {
  agentId: string;
  snapshotId: number;
  environment: string;
}) => {
  const { data: experimentId, isLoading: experimentLoading, error: experimentError } = useAgentPreviewExperiment();
  const timeRange = useMonitoringFiltersTimeRange();
  const locations = useMemo(
    () => (experimentId ? [createTraceLocationForExperiment(experimentId)] : []),
    [experimentId],
  );
  const filters = useMemo(
    () => getAgentTraceTableFilters({ agentId, snapshotId: String(snapshotId), environment }),
    [agentId, snapshotId, environment],
  );
  const {
    data: traces,
    isLoading,
    error,
  } = useSearchMlflowTraces({
    locations,
    filters,
    timeRange,
    pageSize: 5,
    limit: 5,
    enablePagination: false,
    disabled: !experimentId,
  });
  if (experimentLoading || (experimentId && isLoading)) {
    return <Typography.Text color="secondary">Loading recent traces...</Typography.Text>;
  }
  if (experimentError || error || !experimentId) {
    return (
      <Alert
        componentId="mlflow.agent_registry.snapshot.traces.error"
        type="warning"
        message="Recent traces are unavailable."
        closable={false}
      />
    );
  }
  const recent = [...(traces ?? [])]
    .sort((left, right) => right.request_time.localeCompare(left.request_time))
    .slice(0, 5);
  return (
    <Table
      scrollable
      css={{
        '& [role=row]': { minWidth: 560 },
        '& [role=cell]:first-of-type, & [role=columnheader]:first-of-type': { flex: 2 },
        '& [role=cell]:last-of-type, & [role=columnheader]:last-of-type': { flex: 1.5 },
      }}
      empty={
        !recent.length ? (
          <Typography.Text color="secondary">No recent traces match this snapshot and environment.</Typography.Text>
        ) : undefined
      }
    >
      <TableRow isHeader>
        {['Request', 'Environment', 'Latency', 'Status', 'Time'].map((header) => (
          <TableHeader key={header} componentId="mlflow.agent_registry.snapshot.traces.header">
            {header}
          </TableHeader>
        ))}
      </TableRow>
      {recent.map((trace) => (
        <TableRow key={trace.trace_id}>
          <TableCell>
            <Typography.Text
              ellipsis
              title={trace.request_preview ?? trace.request ?? trace.trace_id}
              css={{ width: '100%', minWidth: 0 }}
            >
              {trace.request_preview ?? trace.request ?? trace.trace_id}
            </Typography.Text>
          </TableCell>
          <TableCell>{trace.tags['mlflow.agent.environment'] ?? 'Unassigned'}</TableCell>
          <TableCell>{trace.execution_duration ?? '—'}</TableCell>
          <TableCell>{trace.state}</TableCell>
          <TableCell>{formatDate(trace.request_time)}</TableCell>
        </TableRow>
      ))}
    </Table>
  );
};

export const AgentSnapshotTraces = (props: { agentId: string; snapshotId: number; environment: string }) => (
  <MonitoringConfigProvider>
    <AgentSnapshotTracesContent {...props} />
  </MonitoringConfigProvider>
);

const AgentLiveTracesContent = ({
  agentId,
  experimentId,
  environment,
  onEnvironmentChange,
  relatedAgentId,
}: {
  agentId: string;
  experimentId: string;
  environment: string;
  onEnvironmentChange: (value: string) => void;
  relatedAgentId?: string | null;
}) => {
  const { theme } = useDesignSystemTheme();
  const [searchParams] = useSearchParams();
  const timeRange = useMonitoringFiltersTimeRange();
  const filters = useMemo(
    () =>
      getAgentTraceTableFilters({
        agentId,
        environment,
        snapshotId: searchParams.get('snapshot') ?? undefined,
        version: searchParams.get('version') ?? undefined,
        relatedAgentId: relatedAgentId ?? undefined,
      }),
    [agentId, environment, relatedAgentId, searchParams],
  );

  return (
    <div
      css={{
        display: 'flex',
        flexDirection: 'column',
        gap: theme.spacing.sm,
        flex: 1,
        minHeight: 0,
        overflow: 'hidden',
      }}
    >
      <TracesV3Toolbar viewState="logs" />
      <EnvironmentFilter value={environment} onChange={onEnvironmentChange} />
      <TracesV3Logs
        experimentIds={[experimentId]}
        timeRange={timeRange}
        additionalFilters={filters}
        columnStorageKeyPrefix={`agent-${agentId}`}
        initialGroupBySession={searchParams.get('groupBy') === 'session'}
        disableActions
        drawerWidth="80vw"
      />
    </div>
  );
};

export const AgentLiveTracesPage = ({
  agentId,
  environment,
  onEnvironmentChange,
  relatedAgentId,
}: {
  agentId: string;
  environment: string;
  onEnvironmentChange: (value: string) => void;
  relatedAgentId?: string | null;
}) => {
  const { data: experimentId, isLoading, error } = useAgentPreviewExperiment();
  if (isLoading) return <Typography.Text color="secondary">Loading traces…</Typography.Text>;
  if (error || !experimentId) {
    return (
      <Alert
        componentId="mlflow.agent_registry.traces.preview_data_missing"
        type="info"
        closable={false}
        message="Agent preview traces are unavailable"
        description="Restart the dev server with --seed-agent-registry to populate the read-only trace preview."
      />
    );
  }
  return (
    <MonitoringConfigProvider>
      <AgentLiveTracesContent
        agentId={agentId}
        experimentId={experimentId}
        environment={environment}
        onEnvironmentChange={onEnvironmentChange}
        relatedAgentId={relatedAgentId}
      />
    </MonitoringConfigProvider>
  );
};
