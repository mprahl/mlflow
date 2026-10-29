import { useMemo } from 'react';
import { Alert, Tag, Typography, useDesignSystemTheme } from '@databricks/design-system';

import { Link, useSearchParams } from '../common/utils/RoutingUtils';
import { ExperimentGenAIOverviewPage } from '../experiment-tracking/pages/experiment-overview/ExperimentGenAIOverviewPage';
import ExperimentEvaluationRunsPage from '../experiment-tracking/pages/experiment-evaluation-runs/ExperimentEvaluationRunsPage';
import { MonitoringConfigProvider } from '../experiment-tracking/hooks/useMonitoringConfig';
import { useMonitoringFilters, useMonitoringFiltersTimeRange } from '../experiment-tracking/hooks/useMonitoringFilters';
import { TracesV3Logs } from '../experiment-tracking/components/experiment-page/components/traces-v3/TracesV3Logs';
import { TracesV3Toolbar } from '../experiment-tracking/components/experiment-page/components/traces-v3/TracesV3Toolbar';
import { useAgentPreviewExperiment } from './useAgentPreviewExperiment';
import { agentPath, EnvironmentFilter, Events, getSelectedEnvironments, matchesEnvironment, Panel } from './components';
import { getEnvironmentTraceTableFilters } from './agentTraceFilters';
import { EvaluationFilter, getSelectedFilterValues } from './AgentActivityPages';
import { fleetSectionPath } from './FleetNavConfig';
import { getAssetChangeEvents } from './assetChanges';
import {
  getFleetMetricFilters,
  getFleetScopeTag,
  getFleetTraceFilters,
  getSelectedFleetMembers,
  matchesFleetRun,
} from './fleetScope';
import type { FleetMember } from './fleetScope';
import type { Fleet } from './mockData';
import { getAgent } from './mockData';
import { agentTraceColumns } from './agentTraceColumns';

const FleetTimeline = ({ members, environment }: { members: FleetMember[]; environment: string }) => {
  const [{ startTime, endTime }] = useMonitoringFilters();
  const events = members
    .flatMap((member) => {
      const changes = getAssetChangeEvents(member.agent);
      return [
        ...member.agent.events.filter(
          (event) => event.kind !== 'snapshot' || !changes.some((change) => change.snapshotId === event.snapshotId),
        ),
        ...changes,
      ]
        .filter(
          (event) =>
            matchesEnvironment(member.environment ?? 'all', event.environment) &&
            matchesEnvironment(environment, event.environment),
        )
        .map((event) => ({
          ...event,
          id: `${member.agentId}-${event.id}`,
          title: `${member.agent.name}: ${event.title}`,
          href:
            event.kind === 'sync'
              ? agentPath(member.agentId, 'endpoints', member.environment ?? environment)
              : event.snapshotId
                ? `${agentPath(member.agentId, 'snapshots', member.environment ?? environment)}&snapshot=${event.snapshotId}`
                : agentPath(member.agentId, 'dashboard', member.environment ?? environment),
        }));
    })
    .filter(
      (event) =>
        (!startTime || Date.parse(event.at) >= Date.parse(startTime)) &&
        (!endTime || Date.parse(event.at) <= Date.parse(endTime)),
    )
    .sort((left, right) => right.at.localeCompare(left.at));
  return (
    <Panel title="Fleet timeline">
      <Events events={events} />
    </Panel>
  );
};

const FleetTraces = ({
  fleet,
  members,
  experimentId,
}: {
  fleet: Fleet;
  members: FleetMember[];
  experimentId: string;
}) => {
  const [params, setParams] = useSearchParams();
  const environment = params.get('environment') ?? 'all';
  const { theme } = useDesignSystemTheme();
  const timeRange = useMonitoringFiltersTimeRange();
  const filters = useMemo(
    () => [...getFleetTraceFilters(fleet, members), ...getEnvironmentTraceTableFilters(environment)],
    [fleet, members, environment],
  );
  return (
    <div
      css={{
        display: 'flex',
        flexDirection: 'column',
        flex: 1,
        minHeight: 0,
        gap: theme.spacing.sm,
        overflow: 'hidden',
      }}
    >
      <TracesV3Toolbar viewState="logs" />
      <EnvironmentFilter
        value={environment}
        onChange={(value) => {
          const next = new URLSearchParams(params);
          if (value === 'all') next.delete('environment');
          else next.set('environment', value);
          setParams(next);
        }}
      />
      <TracesV3Logs
        experimentIds={[experimentId]}
        timeRange={timeRange}
        additionalFilters={filters}
        additionalDefaultColumns={agentTraceColumns}
        columnStorageKeyPrefix={`fleet-${fleet.id}`}
        initialGroupBySession={params.get('groupBy') === 'session'}
        disableActions
        drawerWidth="80vw"
      />
    </div>
  );
};

const FleetDashboard = ({
  fleet,
  members,
  experimentId,
}: {
  fleet: Fleet;
  members: FleetMember[];
  experimentId: string;
}) => {
  const [params, setParams] = useSearchParams();
  const { theme } = useDesignSystemTheme();
  const environment = params.get('environment') ?? 'all';
  const activeTab = params.get('dashboardTab') ?? 'usage';
  const hasMultipleEnvironments = getSelectedEnvironments(environment).length > 1;
  const filters = useMemo(() => getFleetMetricFilters(fleet, members, environment), [fleet, members, environment]);
  if (members.length > 1 && members.length < fleet.members.length) {
    return (
      <Alert
        componentId="mlflow.agent_registry.fleet.dashboard.partial_selection"
        type="info"
        message="Select one agent or all fleet members to view combined metrics."
        closable={false}
      />
    );
  }
  return (
    <div css={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      {activeTab !== 'timeline' && hasMultipleEnvironments && (
        <Alert
          componentId="mlflow.agent_registry.fleet.dashboard.multiple_environments"
          type="info"
          closable={false}
          message="Charts show all fleet-member environments when multiple are selected. Select one environment to focus metrics."
          css={{ marginBottom: theme.spacing.sm }}
        />
      )}
      <ExperimentGenAIOverviewPage
        experimentIdOverride={experimentId}
        baseFilters={filters}
        activeTabOverride={activeTab}
        onTabChange={(tab) => {
          const next = new URLSearchParams(params);
          next.set('dashboardTab', tab);
          setParams(next, { replace: true });
        }}
        toolbarAddons={
          <EnvironmentFilter
            value={environment}
            onChange={(value) => {
              const next = new URLSearchParams(params);
              if (value === 'all') next.delete('environment');
              else next.set('environment', value);
              setParams(next);
            }}
          />
        }
        hideIssueDetection
        hideTooltipLinks={hasMultipleEnvironments}
        tooltipLinkUrlBuilder={(_experimentId, timestampMs, intervalSeconds) => {
          const next = new URLSearchParams(params);
          next.delete('dashboardTab');
          next.set('startTimeLabel', 'CUSTOM');
          next.set('startTime', new Date(timestampMs).toISOString());
          next.set('endTime', new Date(timestampMs + intervalSeconds * 1000).toISOString());
          return `${fleetSectionPath(fleet.id, 'traces')}?${next}`;
        }}
        timelineContent={<FleetTimeline members={members} environment={environment} />}
      />
    </div>
  );
};

const FleetEvaluations = ({
  fleet,
  members,
  experimentId,
}: {
  fleet: Fleet;
  members: FleetMember[];
  experimentId: string;
}) => {
  const { theme } = useDesignSystemTheme();
  const [params, setParams] = useSearchParams();
  const environment = params.get('environment') ?? 'all';
  const snapshot = params.get('snapshot') ?? 'all';
  const fleetMembers = getSelectedFleetMembers(fleet, 'all');
  const runFilter = useMemo(() => {
    const environments = getSelectedEnvironments(environment);
    const snapshots = getSelectedFilterValues(snapshot);
    return (run: Parameters<typeof matchesFleetRun>[1]) => {
      const tag = (key: string) => run.data.tags?.find((item) => item.key === key)?.value;
      const snapshotId = tag('mlflow.agent.snapshot_id');
      return (
        matchesFleetRun(members, run) &&
        (!environments.length || environments.includes(tag('mlflow.agent.environment') || 'unassigned')) &&
        (!snapshots.length || snapshots.includes(snapshotId ? `${tag('mlflow.agent.id')}:${snapshotId}` : 'none'))
      );
    };
  }, [members, environment, snapshot]);
  const updateFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (!value || value === 'all') next.delete(key);
    else next.set(key, value);
    next.delete('selectedRunUuid');
    next.delete('compareToRunUuid');
    setParams(next);
  };
  return (
    <ExperimentEvaluationRunsPage
      experimentIdOverride={experimentId}
      scopeKey={`fleet-${fleet.id}`}
      baseFilter={`tags.\`${getFleetScopeTag(fleet.id)}\` = 'true'`}
      runFilter={runFilter}
      toolbarAddons={
        <div css={{ display: 'flex', gap: theme.spacing.sm, flexWrap: 'wrap' }}>
          <EnvironmentFilter value={environment} onChange={(value) => updateFilter('environment', value)} />
          <EvaluationFilter
            componentId="mlflow.agent_registry.fleet.evaluations.snapshot"
            label="Snapshots"
            allLabel="All snapshots"
            selected={getSelectedFilterValues(snapshot)}
            onChange={(values) => updateFilter('snapshot', values.join(','))}
            options={[
              { value: 'none', label: 'No snapshot' },
              ...fleetMembers.flatMap(({ agent }) =>
                agent.snapshots.map((item) => ({
                  value: `${agent.id}:${item.id}`,
                  label: `${agent.name}: Snapshot #${item.id}`,
                })),
              ),
            ]}
          />
        </div>
      }
      readOnly
      emptyState={<Typography.Text color="secondary">No evaluation runs match these fleet filters.</Typography.Text>}
      renderRunContext={(run) => {
        const tag = (key: string) => run.data.tags?.find((item) => item.key === key)?.value;
        const agentId = tag('mlflow.agent.id');
        const snapshot = tag('mlflow.agent.snapshot_id');
        const environment = tag('mlflow.agent.environment') ?? 'unassigned';
        return (
          <div
            css={{ display: 'flex', gap: theme.spacing.xs, alignItems: 'center', flexWrap: 'wrap' }}
            onClick={(event) => event.stopPropagation()}
          >
            {agentId && (
              <Link
                componentId="mlflow.agent_registry.fleet.evaluation.agent"
                to={agentPath(agentId, 'dashboard', environment)}
              >
                {getAgent(agentId)?.name ?? agentId}
              </Link>
            )}
            {agentId && snapshot && (
              <Link
                componentId="mlflow.agent_registry.fleet.evaluation.snapshot"
                to={`${agentPath(agentId, 'snapshots', environment)}&snapshot=${encodeURIComponent(snapshot)}`}
              >
                Snapshot #{snapshot}
              </Link>
            )}
          </div>
        );
      }}
    />
  );
};

export const FleetMonitoring = ({
  fleet,
  members,
  section,
}: {
  fleet: Fleet;
  members: FleetMember[];
  section: string;
}) => {
  const { data: experimentId, isLoading, error } = useAgentPreviewExperiment();
  const { theme } = useDesignSystemTheme();
  if (!members.length) {
    return (
      <div css={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 300 }}>
        <Typography.Text color="secondary">No agents selected.</Typography.Text>
      </div>
    );
  }
  if (isLoading) return <Typography.Text color="secondary">Loading fleet activity...</Typography.Text>;
  if (error || !experimentId) {
    return (
      <Alert
        componentId="mlflow.agent_registry.fleet.data_missing"
        type="info"
        message="Fleet preview data is unavailable."
        closable={false}
      />
    );
  }
  if (section === 'traces') {
    return (
      <MonitoringConfigProvider>
        <FleetTraces fleet={fleet} members={members} experimentId={experimentId} />
      </MonitoringConfigProvider>
    );
  }
  if (section === 'evaluation-runs') {
    return <FleetEvaluations fleet={fleet} members={members} experimentId={experimentId} />;
  }
  return <FleetDashboard fleet={fleet} members={members} experimentId={experimentId} />;
};
