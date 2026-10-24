import { useMemo } from 'react';
import { Alert, SimpleSelect, SimpleSelectOption, Typography, useDesignSystemTheme } from '@databricks/design-system';

import { useSearchParams } from '../common/utils/RoutingUtils';
import { ExperimentGenAIOverviewPage } from '../experiment-tracking/pages/experiment-overview/ExperimentGenAIOverviewPage';
import { useMonitoringFilters } from '../experiment-tracking/hooks/useMonitoringFilters';
import { agentPath, EnvironmentFilter, Events, getSelectedEnvironments, matchesEnvironment, Panel } from './components';
import { getAgentTraceMetricFilters, getAgentTraceNavigationFilters } from './agentTraceFilters';
import type { Agent } from './mockData';
import { useAgentPreviewExperiment } from './useAgentPreviewExperiment';
import { getAssetChangeEvents } from './assetChanges';
import { InvestigationButton } from './AgentInvestigations';

const AgentTimeline = ({
  agent,
  environment,
  snapshot,
  version,
}: {
  agent: Agent;
  environment: string;
  snapshot: string;
  version: string;
}) => {
  const [{ startTime, endTime }] = useMonitoringFilters();
  const events = [...agent.events.filter((event) => event.kind !== 'snapshot'), ...getAssetChangeEvents(agent)]
    .filter((event) => {
      const eventVersion = agent.snapshots.find((item) => item.id === event.snapshotId)?.publisherVersion;
      return (
        matchesEnvironment(environment, event.environment) &&
        (snapshot === 'all' || event.snapshotId === Number(snapshot) || event.kind === 'sync') &&
        (version === 'all' || eventVersion === version || event.kind === 'sync') &&
        (!startTime || Date.parse(event.at) >= Date.parse(startTime)) &&
        (!endTime || Date.parse(event.at) <= Date.parse(endTime))
      );
    })
    .sort((left, right) => right.at.localeCompare(left.at))
    .map((event) => ({
      ...event,
      href:
        event.kind === 'sync'
          ? agentPath(agent.id, 'endpoints', environment)
          : `${agentPath(agent.id, 'snapshots', environment)}&snapshot=${event.snapshotId}`,
    }));

  return (
    <Panel title="Agent timeline">
      <Events
        events={events}
        renderAction={(event) =>
          event.snapshotId !== undefined ? (
            <InvestigationButton mode="change" snapshotId={event.snapshotId}>
              Investigate change
            </InvestigationButton>
          ) : null
        }
      />
    </Panel>
  );
};

export const AgentDashboard = ({
  agent,
  environment,
  onEnvironmentChange,
}: {
  agent: Agent;
  environment: string;
  onEnvironmentChange: (value: string) => void;
}) => {
  const { theme } = useDesignSystemTheme();
  const [searchParams, setSearchParams] = useSearchParams();
  const { data: experimentId, isLoading, error } = useAgentPreviewExperiment();
  const snapshot = searchParams.get('snapshot') ?? 'all';
  const version = searchParams.get('version') ?? 'all';
  const activeTab = searchParams.get('dashboardTab') ?? 'usage';
  const filterOptions = useMemo(
    () => ({ agentId: agent.id, environment, snapshotId: snapshot, version }),
    [agent.id, environment, snapshot, version],
  );
  const metricFilters = useMemo(() => getAgentTraceMetricFilters(filterOptions), [filterOptions]);
  const navigationFilters = useMemo(() => getAgentTraceNavigationFilters(filterOptions), [filterOptions]);
  const updateParam = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    next.set(key, value);
    setSearchParams(next, { replace: true });
  };
  if (isLoading) return <Typography.Text color="secondary">Loading dashboard…</Typography.Text>;
  if (error || !experimentId) {
    return (
      <Alert
        componentId="mlflow.agent_registry.dashboard.preview_data_missing"
        type="info"
        closable={false}
        message="Agent preview metrics are unavailable"
        description="Restart the dev server with --seed-agent-registry to populate the read-only dashboard preview."
      />
    );
  }

  return (
    <div css={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      {activeTab !== 'timeline' && getSelectedEnvironments(environment).length > 1 && (
        <Alert
          componentId="mlflow.agent_registry.dashboard.multiple_environments"
          type="info"
          closable={false}
          message="Charts show all environments when multiple are selected. Select one environment to focus metrics."
          css={{ marginBottom: theme.spacing.sm }}
        />
      )}
      <ExperimentGenAIOverviewPage
        experimentIdOverride={experimentId}
        baseFilters={metricFilters}
        baseTracesNavigationFilters={navigationFilters}
        activeTabOverride={activeTab}
        onTabChange={(tab) => updateParam('dashboardTab', tab)}
        tooltipLinkUrlBuilder={() => agentPath(agent.id, 'traces', environment)}
        hideTooltipLinks
        hideIssueDetection
        toolbarAddons={
          <div css={{ display: 'flex', alignItems: 'center', gap: theme.spacing.sm, flexWrap: 'wrap' }}>
            <EnvironmentFilter value={environment} onChange={onEnvironmentChange} />
            {activeTab === 'tool-calls' && (
              <InvestigationButton mode="dependencies">Inspect affected requests</InvestigationButton>
            )}
            <SimpleSelect
              id="mlflow.agent_registry.dashboard.snapshot"
              componentId="mlflow.agent_registry.dashboard.snapshot"
              aria-label="Snapshot"
              value={snapshot}
              onChange={(event) => updateParam('snapshot', event.target.value)}
              css={{ width: 150 }}
            >
              <SimpleSelectOption value="all">All snapshots</SimpleSelectOption>
              {agent.snapshots.map((item) => (
                <SimpleSelectOption key={item.id} value={String(item.id)}>
                  Snapshot #{item.id}
                </SimpleSelectOption>
              ))}
            </SimpleSelect>
            <SimpleSelect
              id="mlflow.agent_registry.dashboard.version"
              componentId="mlflow.agent_registry.dashboard.version"
              aria-label="Publisher version"
              value={version}
              onChange={(event) => updateParam('version', event.target.value)}
              css={{ width: 150 }}
            >
              <SimpleSelectOption value="all">All versions</SimpleSelectOption>
              {Array.from(new Set(agent.snapshots.map((item) => item.publisherVersion))).map((item) => (
                <SimpleSelectOption key={item} value={item}>
                  Version {item}
                </SimpleSelectOption>
              ))}
            </SimpleSelect>
          </div>
        }
        timelineContent={
          <AgentTimeline agent={agent} environment={environment} snapshot={snapshot} version={version} />
        }
      />
    </div>
  );
};
