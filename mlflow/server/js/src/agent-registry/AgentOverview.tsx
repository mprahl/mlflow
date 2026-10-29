import {
  CopyIcon,
  Table,
  TableCell,
  TableHeader,
  TableRow,
  Tag,
  Typography,
  useDesignSystemTheme,
} from '@databricks/design-system';
import { useMemo } from 'react';
import { Link } from '../common/utils/RoutingUtils';
import { CopyButton } from '../shared/building_blocks/CopyButton';
import { ExperimentGenAIJourneyOverviewPage } from '../experiment-tracking/pages/experiment-overview/ExperimentGenAIJourneyOverviewPage';
import { MonitoringConfigProvider } from '../experiment-tracking/hooks/useMonitoringConfig';
import { useAgentPreviewExperiment } from './useAgentPreviewExperiment';
import { AgentTracingSetup } from './AgentTracingSetup';
import { AgentAssetsTable } from './AgentAssetsTable';
import { agentPath, formatDate, Panel } from './components';
import type { Agent } from './mockData';

export const AgentOverview = ({ agent }: { agent: Agent }) => {
  const { theme } = useDesignSystemTheme();
  const { data: experimentId, isLoading, error } = useAgentPreviewExperiment();
  const filters = useMemo(() => [`trace.tag.\`mlflow.agent.id\` = '${agent.id.replaceAll("'", "''")}'`], [agent.id]);
  const routes = useMemo(
    () => ({
      traces: agentPath(agent.id, 'traces'),
      evaluations: agentPath(agent.id, 'evaluation-runs'),
      dashboard: agentPath(agent.id, 'dashboard'),
    }),
    [agent.id],
  );
  const endpoints = (
    <Panel
      title="Access endpoints"
      action={
        <Link componentId="mlflow.agent_registry.overview.endpoints" to={agentPath(agent.id, 'endpoints')}>
          View all endpoints
        </Link>
      }
    >
      <Table
        scrollable
        css={{
          '& [role=row]': { minWidth: 620 },
          '& [role=cell]:last-of-type, & [role=columnheader]:last-of-type': { flex: 2.5 },
        }}
        empty={!agent.endpoints.length ? <Typography.Text>No access endpoints registered.</Typography.Text> : undefined}
      >
        <TableRow isHeader>
          {['Endpoint', 'Protocol', 'Address'].map((label) => (
            <TableHeader key={label} componentId="mlflow.agent_registry.overview.endpoints.header">
              {label}
            </TableHeader>
          ))}
        </TableRow>
        {agent.endpoints.map((endpoint) => (
          <TableRow key={endpoint.name}>
            <TableCell>
              <Link
                componentId="mlflow.agent_registry.overview.endpoint"
                to={`${agentPath(agent.id, 'endpoints')}?endpoint=${encodeURIComponent(endpoint.name)}`}
              >
                {endpoint.name}
              </Link>
            </TableCell>
            <TableCell>
              <Tag componentId="mlflow.agent_registry.overview.protocol">{endpoint.protocol}</Tag>
            </TableCell>
            <TableCell>
              <div css={{ display: 'flex', alignItems: 'center', minWidth: 0, gap: theme.spacing.sm }}>
                <Typography.Text ellipsis title={endpoint.url}>
                  {endpoint.url}
                </Typography.Text>
                <CopyButton
                  componentId="mlflow.agent_registry.overview.copy_endpoint"
                  copyText={endpoint.url}
                  showLabel={false}
                  icon={<CopyIcon />}
                  size="small"
                  type="tertiary"
                  aria-label={`Copy ${endpoint.name} endpoint`}
                />
              </div>
            </TableCell>
          </TableRow>
        ))}
      </Table>
    </Panel>
  );
  const details = (
    <div css={{ display: 'grid', gap: theme.spacing.lg, minWidth: 0 }}>
      <Panel
        title="AI assets"
        action={
          <Link componentId="mlflow.agent_registry.overview.assets" to={agentPath(agent.id, 'assets')}>
            View AI assets
          </Link>
        }
      >
        <AgentAssetsTable key={agent.id} agent={agent} section="assets" pageSize={5} />
      </Panel>
      <Panel
        title="Snapshots"
        action={
          <Link
            componentId="mlflow.agent_registry.overview.timeline"
            to={`${agentPath(agent.id, 'snapshots')}?view=timeline`}
          >
            View timeline
          </Link>
        }
      >
        <Table scrollable css={{ '& [role=row]': { minWidth: 600 } }}>
          <TableRow isHeader>
            {['Snapshot', 'Publisher version', 'Created', 'Source'].map((label) => (
              <TableHeader key={label} componentId="mlflow.agent_registry.overview.snapshots.header">
                {label}
              </TableHeader>
            ))}
          </TableRow>
          {agent.snapshots.map((snapshot) => (
            <TableRow key={snapshot.id}>
              <TableCell>
                <Link
                  componentId="mlflow.agent_registry.overview.snapshot"
                  to={`${agentPath(agent.id, 'snapshots')}?snapshot=${snapshot.id}`}
                >
                  Snapshot #{snapshot.id}
                </Link>
              </TableCell>
              <TableCell>{snapshot.publisherVersion}</TableCell>
              <TableCell>{formatDate(snapshot.createdAt)}</TableCell>
              <TableCell>{snapshot.source}</TableCell>
            </TableRow>
          ))}
        </Table>
      </Panel>
    </div>
  );
  if (isLoading) return <Typography.Text>Loading overview...</Typography.Text>;
  if (error || !experimentId) {
    return (
      <>
        {endpoints}
        <Typography.Text>Overview activity is unavailable.</Typography.Text>
        {details}
      </>
    );
  }
  return (
    <MonitoringConfigProvider>
      <ExperimentGenAIJourneyOverviewPage
        experimentIdOverride={experimentId}
        baseTraceFilters={filters}
        baseRunFilter={`tags.\`mlflow.agent.id\` = '${agent.id.replaceAll("'", "''")}'`}
        routes={routes}
        readOnly
        headerContent={endpoints}
        footerContent={details}
        tracingSetup={<AgentTracingSetup agentId={agent.id} />}
      />
    </MonitoringConfigProvider>
  );
};
