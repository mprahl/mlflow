import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';
import type { ReactNode } from 'react';
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { renderWithDesignSystem } from '../common/utils/TestUtils.react18';
import { testRoute, TestRouter } from '../common/utils/RoutingTestUtils';
import { Link, useParams } from '../common/utils/RoutingUtils';
import { MlflowSidebarAgentDetailItems } from '../common/components/MlflowSidebarAgentDetailItems';
import { MlflowSidebarFleetDetailItems } from '../common/components/MlflowSidebarFleetDetailItems';
import AgentDetailPage from './AgentDetailPage';
import { AgentCardView } from './AgentCardView';
import { getAgent } from './mockData';
import AgentRegistryPage from './AgentRegistryPage';
import FleetDetailPage, { FleetListPage } from './FleetPages';
import { isAgentRegistryActive } from '../common/components/MlflowSidebarAgentItems';
import type { Location } from '../common/utils/RoutingUtils';
import { copyToClipboard } from '../common/utils/copyToClipboard';
import { useSearchMlflowTraces } from '../shared/web-shared/genai-traces-table/hooks/useMlflowTraces';
import ExperimentEvaluationRunsPage from '../experiment-tracking/pages/experiment-evaluation-runs/ExperimentEvaluationRunsPage';
import type { ExperimentEvaluationRunsPageProps } from '../experiment-tracking/pages/experiment-evaluation-runs/ExperimentEvaluationRunsPage';
import type { RunEntity } from '../experiment-tracking/types';

jest.mock('../experiment-tracking/pages/experiment-evaluation-runs/ExperimentEvaluationRunsPage', () => ({
  __esModule: true,
  default: jest.fn(({ baseFilter, toolbarAddons, renderRunContext, readOnly }: ExperimentEvaluationRunsPageProps) => (
    <div>
      <div data-testid="agent-evaluation-query-filter">{baseFilter}</div>
      <div data-testid="agent-evaluation-read-only">{String(readOnly)}</div>
      {toolbarAddons}
      {renderRunContext?.({
        data: {
          tags: [
            { key: 'mlflow.agent.id', value: 'support-agent' },
            { key: 'mlflow.agent.snapshot_id', value: '7' },
            { key: 'mlflow.agent.version', value: '2.4.0' },
            { key: 'mlflow.agent.environment', value: 'prod' },
          ],
        },
      } as RunEntity)}
    </div>
  )),
}));

jest.mock('../shared/web-shared/genai-traces-table/hooks/useMlflowTraces', () => ({
  ...jest.requireActual<typeof import('../shared/web-shared/genai-traces-table/hooks/useMlflowTraces')>(
    '../shared/web-shared/genai-traces-table/hooks/useMlflowTraces',
  ),
  useSearchMlflowTraces: jest.fn(() => ({ data: [], isLoading: false, isFetching: false })),
}));

jest.mock('../common/utils/copyToClipboard', () => ({
  copyToClipboard: jest.fn(async () => true),
}));

jest.mock('./useAgentPreviewExperiment', () => ({
  useAgentPreviewExperiment: () => ({ data: 'preview-exp', isLoading: false, error: null }),
}));

jest.mock('../experiment-tracking/pages/experiment-overview/ExperimentGenAIOverviewPage', () => ({
  ExperimentGenAIOverviewPage: ({
    baseFilters,
    toolbarAddons,
    activeTabOverride,
    onTabChange,
    timelineContent,
  }: {
    baseFilters?: string[];
    toolbarAddons?: ReactNode;
    activeTabOverride?: string;
    onTabChange?: (tab: string) => void;
    timelineContent?: ReactNode;
  }) => (
    <div>
      <div data-testid="agent-dashboard-query-filters">{baseFilters?.join('|')}</div>
      <button type="button" onClick={() => onTabChange?.('quality')}>
        Quality
      </button>
      <span>{activeTabOverride}</span>
      {toolbarAddons}
      {activeTabOverride === 'timeline' && timelineContent}
    </div>
  ),
}));

jest.mock('../experiment-tracking/components/experiment-page/components/traces-v3/TracesV3Logs', () => ({
  TracesV3Logs: ({
    additionalFilters,
    initialGroupBySession,
  }: {
    additionalFilters?: Array<{ key?: string; value?: string }>;
    initialGroupBySession?: boolean;
  }) => (
    <div data-testid="agent-traces-query-filters">{JSON.stringify({ additionalFilters, initialGroupBySession })}</div>
  ),
}));

jest.mock('../experiment-tracking/components/experiment-page/components/traces-v3/TracesV3Toolbar', () => ({
  TracesV3Toolbar: () => <div>Trace time toolbar</div>,
}));

const AgentDetailWithSidebar = () => {
  const { agentId } = useParams<{ agentId: string }>();
  return (
    <div>
      <ul>
        <MlflowSidebarAgentDetailItems agentId={agentId ?? ''} collapsed={false} />
      </ul>
      <AgentDetailPage />
    </div>
  );
};

const renderPage = (path: string) =>
  renderWithDesignSystem(
    <TestRouter
      initialEntries={[path]}
      routes={[
        testRoute(<AgentRegistryPage />, '/agents'),
        testRoute(<AgentDetailWithSidebar />, '/agents/:agentId/:section'),
        testRoute(<FleetListPage />, '/fleets'),
        testRoute(<FleetDetailWithSidebar />, '/fleets/:fleetId'),
        testRoute(<FleetDetailWithSidebar />, '/fleets/:fleetId/:section'),
      ]}
    />,
  );

const FleetDetailWithSidebar = () => {
  const { fleetId } = useParams<{ fleetId: string }>();
  return (
    <div>
      <ul>
        <MlflowSidebarFleetDetailItems fleetId={fleetId ?? ''} collapsed={false} />
      </ul>
      <FleetDetailPage />
    </div>
  );
};

describe('agent registry preview', () => {
  beforeEach(() => {
    jest.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-10-23T12:00:00Z'));
  });

  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('groups Agents and Fleets under the same sidebar section', () => {
    expect(isAgentRegistryActive({ pathname: '/agents' } as Location)).toBe(true);
    expect(isAgentRegistryActive({ pathname: '/fleets/customer-experience' } as Location)).toBe(true);
  });

  it('uses the GenAI experiment overview journey', () => {
    renderPage('/agents/support-agent/overview');
    expect(screen.queryByRole('navigation', { name: 'Agent navigation' })).not.toBeInTheDocument();
    expect(screen.getByText('Build a continuous quality loop in 4 steps')).toBeInTheDocument();
    expect(screen.getByText('Publisher version 2.4.0')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Dashboard' })).toHaveAttribute(
      'href',
      '/agents/support-agent/dashboard?environment=all',
    );
    expect(screen.getByRole('link', { name: 'View timeline' })).toHaveAttribute(
      'href',
      '/agents/support-agent/dashboard?environment=all&dashboardTab=timeline',
    );
  });

  it('links an AI asset change in recent activity to its snapshot', () => {
    renderPage('/agents/support-agent/overview');
    expect(screen.getByRole('link', { name: 'support-system updated' })).toHaveAttribute(
      'href',
      '/agents/support-agent/snapshots?snapshot=8',
    );
    expect(screen.queryByText('Snapshot 8 created')).not.toBeInTheDocument();
  });

  it('keeps first-trace onboarding for a new agent with the RFC agent selector', async () => {
    const user = userEvent.setup();
    renderPage('/agents/starter-agent/overview');
    expect(screen.getByText('Send your first trace to MLflow in just a minute')).toBeInTheDocument();
    expect(screen.getByText(/MLFLOW_AGENT_ID='starter-agent'/)).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: 'AI Assistant' }));
    expect(screen.getByText(/mlflow.set_agent\(agent_id="starter-agent"\)/)).toBeInTheDocument();
    await user.click(screen.getByRole('link', { name: 'Traces' }));
    expect(screen.getByText('Start tracing your agent')).toBeInTheDocument();
  });

  it('filters the directory by endpoint availability', () => {
    renderPage('/agents');
    expect(screen.queryByText(/^(draft|active|deprecated)$/)).not.toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Access endpoint' })).toBeInTheDocument();
    expect(screen.getByText('Knowledge agent')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Has endpoint'));
    expect(screen.queryByText('Knowledge agent')).not.toBeInTheDocument();
    expect(screen.getByText('Support agent')).toBeInTheDocument();
  });

  it('uses a registry table for fleets', () => {
    renderPage('/fleets');
    expect(screen.getByRole('columnheader', { name: 'Selected environments' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Customer experience' })).toHaveAttribute(
      'href',
      '/fleets/customer-experience',
    );
  });

  it('uses the registry snapshot list with preview and comparison panes', async () => {
    const user = userEvent.setup();
    renderPage('/agents/support-agent/snapshots');
    expect(screen.getByRole('columnheader', { name: 'Snapshots' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Viewing snapshot #8' })).toBeInTheDocument();
    expect(screen.queryByText('Status')).not.toBeInTheDocument();
    expect(screen.queryByText(/^(draft|active|deprecated)$/)).not.toBeInTheDocument();
    expect(screen.getByText('support-system updated')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'support-system' })).toHaveAttribute(
      'href',
      '/prompts/support-system?promptVersion=4',
    );
    await user.click(screen.getByRole('radio', { name: 'Compare' }));
    expect(screen.getByText('Compare snapshot #8 with #7')).toBeInTheDocument();
    expect(screen.queryByText(/^(draft|active|deprecated)$/)).not.toBeInTheDocument();
    expect(screen.getByText('version 3')).toBeInTheDocument();
    expect(screen.getByText('version 4')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'version 3' })).toHaveAttribute(
      'href',
      '/prompts/support-system?promptVersion=3',
    );
    expect(screen.getByRole('link', { name: 'version 4' })).toHaveAttribute(
      'href',
      '/prompts/support-system?promptVersion=4',
    );
    await user.click(
      within(screen.getByRole('row', { name: /Snapshot #6\b/ })).getByRole('radio', {
        name: 'Select as compared version',
      }),
    );
    expect(screen.getByText('Compare snapshot #8 with #6')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Switch sides' }));
    expect(screen.getByText('Compare snapshot #6 with #8')).toBeInTheDocument();
    await user.click(screen.getByRole('radio', { name: 'Preview' }));
    await user.click(screen.getByRole('row', { name: /Snapshot #8\b/ }));
    expect(screen.getByRole('tab', { name: 'AI assets' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('Provider model')).toBeInTheDocument();
    expect(screen.queryByText('vprovider model')).not.toBeInTheDocument();
  });

  it('shows pinned assets in their registry sections', () => {
    renderPage('/agents/support-agent/prompts');
    expect(screen.getAllByRole('link', { name: 'support-system' })).toHaveLength(2);
    expect(screen.queryByRole('link', { name: 'refund-policy' })).not.toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Pinned version' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Used in snapshots' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Agent versions' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Last recorded' })).toHaveAttribute('aria-sort', 'descending');
    expect(screen.getAllByRole('link', { name: 'support-system' })[0]).toHaveAttribute(
      'href',
      '/prompts/support-system?promptVersion=4',
    );
    expect(screen.getAllByRole('link', { name: 'support-system' })[1]).toHaveAttribute(
      'href',
      '/prompts/support-system?promptVersion=3',
    );
  });

  it('defaults assets to the last 30 days and restores older versions with All time', async () => {
    jest.mocked(Date.now).mockReturnValue(Date.parse('2026-11-20T13:08:00Z'));
    const user = userEvent.setup();
    renderPage('/agents/support-agent/assets');
    expect(screen.getByRole('button', { name: 'Last 30 days' })).toBeInTheDocument();
    expect(screen.getAllByRole('link', { name: 'support-system' })).toHaveLength(1);
    expect(screen.getByRole('link', { name: 'support-system' })).toHaveAttribute(
      'href',
      '/prompts/support-system?promptVersion=4',
    );
    expect(screen.getByRole('link', { name: 'order-lookup' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Last 30 days' }));
    await user.click(screen.getByRole('option', { name: 'All time' }));
    expect(screen.getAllByRole('link', { name: 'support-system' })).toHaveLength(2);
  });

  it('combines asset time range with snapshot filters', async () => {
    const user = userEvent.setup();
    renderPage('/agents/support-agent/assets');
    await user.click(screen.getByTestId('mlflow.agent_registry.assets.filter.snapshots'));
    fireEvent.click(screen.getByText('Snapshot #6'));
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('button', { name: 'Last 30 days' }));
    await user.click(screen.getByRole('option', { name: 'Last 7 days' }));
    expect(screen.getByText('No AI assets match these filters.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Last 7 days' }));
    await user.click(screen.getByRole('option', { name: 'All time' }));
    expect(screen.getByRole('link', { name: 'support-system' })).toHaveAttribute(
      'href',
      '/prompts/support-system?promptVersion=3',
    );
  });

  it('keeps historical snapshot compositions complete regardless of age', () => {
    jest.mocked(Date.now).mockReturnValue(Date.parse('2027-01-01T00:00:00Z'));
    renderPage('/agents/support-agent/snapshots?snapshot=6');
    expect(screen.queryByRole('button', { name: 'Last 30 days' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'support-system' })).toHaveAttribute(
      'href',
      '/prompts/support-system?promptVersion=3',
    );
    expect(screen.getByRole('link', { name: 'order-lookup' })).toBeInTheDocument();
  });

  it('opens real traces scoped to the snapshot evidence environment', async () => {
    const user = userEvent.setup();
    renderPage('/agents/support-agent/snapshots?snapshot=8&environment=staging');
    await user.click(screen.getByRole('tab', { name: 'Traces' }));
    expect(useSearchMlflowTraces).toHaveBeenCalledWith(
      expect.objectContaining({
        limit: 5,
        pageSize: 5,
        filters: expect.arrayContaining([
          expect.objectContaining({ key: 'mlflow.agent.id', value: 'support-agent' }),
          expect.objectContaining({ key: 'mlflow.agent.snapshot_id', value: '8' }),
          expect.objectContaining({ key: 'mlflow.agent.environment', value: 'staging' }),
        ]),
      }),
    );
    await user.click(screen.getByRole('link', { name: 'View all traces' }));
    const props = JSON.parse(screen.getByTestId('agent-traces-query-filters').textContent ?? '{}');
    expect(props.additionalFilters).toContainEqual(
      expect.objectContaining({ key: 'mlflow.agent.snapshot_id', value: '8' }),
    );
    expect(props.additionalFilters).toContainEqual(
      expect.objectContaining({ key: 'mlflow.agent.environment', value: 'staging' }),
    );
  });

  it('filters linked AI assets by snapshot and search text', async () => {
    const user = userEvent.setup();
    renderPage('/agents/support-agent/prompts');
    await user.click(screen.getByTestId('mlflow.agent_registry.assets.filter.snapshots'));
    const snapshotOption = screen.getByText('Snapshot #7');
    fireEvent.click(snapshotOption);
    expect(screen.getAllByRole('link', { name: 'support-system' })).toHaveLength(1);
    expect(screen.getByRole('link', { name: 'support-system' })).toHaveAttribute(
      'href',
      '/prompts/support-system?promptVersion=3',
    );
    fireEvent.click(snapshotOption);
    expect(screen.getAllByRole('link', { name: 'support-system' })).toHaveLength(2);
    expect(screen.getAllByRole('link', { name: 'support-system' }).map((link) => link.getAttribute('href'))).toEqual([
      '/prompts/support-system?promptVersion=4',
      '/prompts/support-system?promptVersion=3',
    ]);
    await user.type(screen.getByPlaceholderText('Search prompts'), 'missing');
    expect(screen.getByText('No AI assets match these filters.')).toBeInTheDocument();
  });

  it('defaults to the shared All-assets table scoped to the selected snapshot', async () => {
    const user = userEvent.setup();
    renderPage('/agents/support-agent/snapshots?snapshot=8');
    expect(screen.getByRole('tab', { name: 'AI assets' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('columnheader', { name: 'Type' })).toBeInTheDocument();
    expect(screen.queryByTestId('mlflow.agent_registry.assets.filter.snapshots')).not.toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'Used in snapshots' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'support-system' })).toHaveAttribute(
      'href',
      '/prompts/support-system?promptVersion=4',
    );
    await user.click(screen.getByRole('row', { name: /Snapshot #7 Version 2.4.0/ }));
    expect(screen.getByRole('link', { name: 'support-system' })).toHaveAttribute(
      'href',
      '/prompts/support-system?promptVersion=3',
    );
    expect(screen.queryByText('Median latency')).not.toBeInTheDocument();
  });

  it('links snapshot details to the filtered dashboard', async () => {
    const user = userEvent.setup();
    renderPage('/agents/support-agent/snapshots?snapshot=7&environment=prod');
    await user.click(screen.getByRole('link', { name: 'View dashboard' }));
    expect(screen.getByTestId('agent-dashboard-query-filters')).toHaveTextContent(
      "trace.tag.`mlflow.agent.snapshot_id` = '7'",
    );
    expect(screen.getByTestId('agent-dashboard-query-filters')).toHaveTextContent(
      "trace.tag.`mlflow.agent.environment` = 'prod'",
    );
  });

  it('previews recent snapshot evaluations and opens the filtered full view', async () => {
    const user = userEvent.setup();
    renderPage('/agents/support-agent/snapshots?snapshot=7&environment=prod');
    await user.click(screen.getByRole('tab', { name: 'Evaluations' }));
    expect(screen.getByText('eval-support-prod-7')).toBeInTheDocument();
    expect(screen.queryByText('eval-support-dev-7')).not.toBeInTheDocument();
    await user.click(screen.getByRole('link', { name: 'View all evaluations' }));
    expect(screen.getByRole('combobox', { name: 'Snapshots' })).toBeInTheDocument();
    expect(screen.getByTestId('agent-evaluation-query-filter')).toHaveTextContent(
      "tags.`mlflow.agent.snapshot_id` = '7'",
    );
    expect(screen.getByTestId('agent-evaluation-query-filter')).toHaveTextContent(
      "tags.`mlflow.agent.environment` = 'prod'",
    );
  });

  it('leaves Agent plugins empty in the mock registry', () => {
    renderPage('/agents/support-agent/plugins');
    expect(screen.getByText('No agent plugins to show yet.')).toBeInTheDocument();
    expect(screen.queryByText('support-observability')).not.toBeInTheDocument();
  });

  it('sorts pinned asset versions by last recorded time', async () => {
    const user = userEvent.setup();
    renderPage('/agents/support-agent/prompts');
    await user.click(screen.getByRole('button', { name: 'Last recorded' }));
    expect(screen.getByRole('columnheader', { name: 'Last recorded' })).toHaveAttribute('aria-sort', 'ascending');
    expect(screen.getAllByRole('link', { name: 'support-system' })[0]).toHaveAttribute(
      'href',
      '/prompts/support-system?promptVersion=3',
    );
  });

  it('links MCP assets to their qualified registry entry', () => {
    renderPage('/agents/support-agent/mcp-servers');
    expect(screen.getByRole('link', { name: 'order-lookup' })).toHaveAttribute(
      'href',
      '/mcp-registry/com.example%2Forder-lookup?version=1.0.0',
    );
  });

  it('carries a fleet member environment into the agent page', async () => {
    const user = userEvent.setup();
    renderPage('/fleets/customer-experience');
    expect(screen.getByTestId('agent-dashboard-query-filters')).toHaveTextContent(
      "trace.tag.`mlflow.agent.preview.fleet.customer-experience` = 'true'",
    );
    await user.click(screen.getByRole('link', { name: 'Agents' }));
    expect(screen.getByRole('columnheader', { name: 'Owner' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Selected environment' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Support agent' })).toHaveAttribute(
      'href',
      '/agents/support-agent/dashboard?environment=prod',
    );
    await user.click(screen.getByRole('link', { name: 'Support agent' }));
    expect(screen.getByTestId('agent-dashboard-query-filters')).toHaveTextContent(
      "trace.tag.`mlflow.agent.environment` = 'prod'",
    );
  });

  it('filters fleet dashboard charts by environment and retains the selection across tabs', async () => {
    const user = userEvent.setup();
    renderPage('/fleets/release-readiness/dashboard?agents=support-agent&environment=staging');
    const filters = screen.getByTestId('agent-dashboard-query-filters');
    expect(filters).toHaveTextContent("trace.tag.`mlflow.agent.preview.fleet.release-readiness` = 'true'");
    expect(filters).toHaveTextContent("trace.tag.`mlflow.agent.id` = 'support-agent'");
    expect(filters).toHaveTextContent("trace.tag.`mlflow.agent.environment` = 'staging'");
    await user.click(screen.getByRole('button', { name: 'Quality' }));
    expect(filters).toHaveTextContent("trace.tag.`mlflow.agent.environment` = 'staging'");
    await user.click(screen.getByTestId('mlflow.agent_registry.environment_filter'));
    await user.click(screen.getByRole('option', { name: 'Staging' }));
    expect(filters).not.toHaveTextContent('mlflow.agent.environment');
    expect(filters).toHaveTextContent("trace.tag.`mlflow.agent.id` = 'support-agent'");
    await user.click(screen.getByRole('option', { name: 'Dev' }));
    expect(filters).toHaveTextContent("trace.tag.`mlflow.agent.environment` = 'dev'");
    await user.click(screen.getByRole('option', { name: 'Staging' }));
    expect(filters).not.toHaveTextContent('mlflow.agent.environment');
    expect(
      screen.getByText(/Charts show all fleet-member environments when multiple are selected/),
    ).toBeInTheDocument();
  });

  it('filters the fleet dashboard timeline across environments without dropping membership restrictions', async () => {
    const user = userEvent.setup();
    renderPage(
      '/fleets/release-readiness/dashboard?dashboardTab=timeline&environment=dev&startTimeLabel=CUSTOM&startTime=2026-10-01T00:00:00Z&endTime=2026-10-23T00:00:00Z',
    );
    expect(screen.queryByRole('link', { name: 'Support agent: Snapshot 8 evaluated' })).not.toBeInTheDocument();
    await user.click(screen.getByTestId('mlflow.agent_registry.environment_filter'));
    await user.click(screen.getByRole('option', { name: 'Staging' }));
    expect(screen.getByRole('link', { name: 'Support agent: Snapshot 8 evaluated' })).toHaveAttribute(
      'href',
      '/agents/support-agent/snapshots?environment=staging&snapshot=8',
    );
    await user.click(screen.getByRole('option', { name: 'Prod' }));
    expect(screen.queryByRole('link', { name: 'Support agent: Snapshot 7 evaluated' })).not.toBeInTheDocument();
    expect(screen.queryByText(/Charts show all fleet-member environments/)).not.toBeInTheDocument();
  });

  it('uses shared fleet traces with session grouping and exact fleet scope', () => {
    renderPage('/fleets/customer-experience/traces?groupBy=session');
    expect(screen.queryByRole('link', { name: 'Sessions' })).not.toBeInTheDocument();
    const props = JSON.parse(screen.getByTestId('agent-traces-query-filters').textContent ?? '{}');
    expect(props.initialGroupBySession).toBe(true);
    expect(props.additionalFilters).toContainEqual(
      expect.objectContaining({ key: 'mlflow.agent.preview.fleet.customer-experience', value: 'true' }),
    );
  });

  it.each(['staging', 'unassigned'])('restores the fleet trace environment %s from the URL', (environment) => {
    renderPage(`/fleets/release-readiness/traces?environment=${environment}`);
    const props = JSON.parse(screen.getByTestId('agent-traces-query-filters').textContent ?? '{}');
    expect(props.additionalFilters).toEqual([
      expect.objectContaining({ key: 'mlflow.agent.preview.fleet.release-readiness', value: 'true' }),
      expect.objectContaining({ key: 'mlflow.agent.environment', operator: '=', value: environment }),
    ]);
    expect(screen.getByTestId('mlflow.agent_registry.environment_filter')).toHaveTextContent(environment);
  });

  it('selects and clears fleet trace environments without losing membership scope or session grouping', async () => {
    const user = userEvent.setup();
    renderPage('/fleets/release-readiness/traces?agents=support-agent&groupBy=session');
    const scope = [
      expect.objectContaining({ key: 'mlflow.agent.preview.fleet.release-readiness', value: 'true' }),
      expect.objectContaining({ key: 'mlflow.agent.id', value: 'support-agent' }),
    ];
    await user.click(screen.getByTestId('mlflow.agent_registry.environment_filter'));
    await user.click(screen.getByRole('option', { name: 'Staging' }));
    await user.click(screen.getByRole('option', { name: 'Dev' }));
    expect(JSON.parse(screen.getByTestId('agent-traces-query-filters').textContent ?? '{}')).toEqual({
      additionalFilters: [
        ...scope,
        expect.objectContaining({ key: 'mlflow.agent.environment', operator: 'RLIKE', value: '^(staging|dev)$' }),
      ],
      initialGroupBySession: true,
    });
    await user.click(screen.getByRole('option', { name: 'Staging' }));
    await user.click(screen.getByRole('option', { name: 'Dev' }));
    expect(JSON.parse(screen.getByTestId('agent-traces-query-filters').textContent ?? '{}')).toEqual({
      additionalFilters: scope,
      initialGroupBySession: true,
    });
  });

  it('keeps the fleet agent multiselect across navigation and supports an empty selection', async () => {
    const user = userEvent.setup();
    renderPage('/fleets/customer-experience');
    await user.click(screen.getByRole('combobox', { name: 'Agents' }));
    await user.click(screen.getByRole('option', { name: 'Refund agent' }));
    expect(screen.getByTestId('agent-dashboard-query-filters')).toHaveTextContent(
      "trace.tag.`mlflow.agent.id` = 'support-agent'",
    );
    await user.click(screen.getByRole('option', { name: 'Support agent' }));
    expect(screen.getByText('No agents selected.')).toBeInTheDocument();
    expect(screen.queryByTestId('agent-dashboard-query-filters')).not.toBeInTheDocument();
    await user.click(screen.getByRole('option', { name: 'All agents' }));
    await user.click(screen.getByRole('option', { name: 'Refund agent' }));
    await user.keyboard('{Escape}');
    expect(screen.getByRole('link', { name: 'Traces' })).toHaveAttribute(
      'href',
      '/fleets/customer-experience/traces?agents=support-agent',
    );
    await user.click(screen.getByRole('link', { name: 'Traces' }));
    const props = JSON.parse(screen.getByTestId('agent-traces-query-filters').textContent ?? '{}');
    expect(props.additionalFilters).toContainEqual(
      expect.objectContaining({ key: 'mlflow.agent.id', value: 'support-agent' }),
    );
  });

  it('shows mixed fleet environments in an Agents-style directory', () => {
    renderPage('/fleets/release-readiness/agents');
    expect(screen.getByRole('link', { name: 'Support agent' })).toHaveAttribute(
      'href',
      '/agents/support-agent/dashboard?environment=staging',
    );
    expect(screen.getByRole('link', { name: 'Knowledge agent' })).toHaveAttribute(
      'href',
      '/agents/knowledge-agent/dashboard?environment=dev',
    );
    expect(screen.getByRole('columnheader', { name: 'Owner' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Selected environment' })).toBeInTheDocument();
  });

  it('reuses the read-only evaluation page for fleet memberships', () => {
    renderPage('/fleets/release-readiness/evaluation-runs');
    expect(screen.getByTestId('agent-evaluation-query-filter')).toHaveTextContent(
      "tags.`mlflow.agent.preview.fleet.release-readiness` = 'true'",
    );
    expect(screen.getByTestId('agent-evaluation-read-only')).toHaveTextContent('true');
    const props = jest.mocked(ExperimentEvaluationRunsPage).mock.calls.at(-1)?.[0];
    const run = (environment: string) =>
      ({
        data: {
          tags: [
            { key: 'mlflow.agent.id', value: 'support-agent' },
            { key: 'mlflow.agent.environment', value: environment },
          ],
        },
      }) as RunEntity;
    expect(props?.runFilter?.(run('staging'))).toBe(true);
    expect(props?.runFilter?.(run('dev'))).toBe(false);
  });

  it('links fleet evaluation agents to their dashboard while retaining snapshot evidence links', () => {
    renderPage('/fleets/customer-experience/evaluation-runs');
    expect(screen.getByRole('link', { name: 'Support agent' })).toHaveAttribute(
      'href',
      '/agents/support-agent/dashboard?environment=prod',
    );
    expect(screen.getByRole('link', { name: 'Snapshot #7' })).toHaveAttribute(
      'href',
      '/agents/support-agent/snapshots?environment=prod&snapshot=7',
    );
  });

  it('combines fleet evaluation environments and snapshots while ignoring legacy version filters', async () => {
    const user = userEvent.setup();
    renderPage('/fleets/release-readiness/evaluation-runs?version=2.4.0');
    const run = (agentId: string, environment: string, snapshotId = '8', version = '2.4.0') =>
      ({
        data: {
          tags: [
            { key: 'mlflow.agent.id', value: agentId },
            { key: 'mlflow.agent.environment', value: environment },
            { key: 'mlflow.agent.snapshot_id', value: snapshotId },
            { key: 'mlflow.agent.version', value: version },
          ],
        },
      }) as RunEntity;
    const matches = (candidate: RunEntity) =>
      jest.mocked(ExperimentEvaluationRunsPage).mock.calls.at(-1)?.[0]?.runFilter?.(candidate);

    await user.click(screen.getByTestId('mlflow.agent_registry.environment_filter'));
    await user.click(screen.getByRole('option', { name: 'Staging' }));
    expect(matches(run('support-agent', 'staging'))).toBe(true);
    expect(matches(run('knowledge-agent', 'dev'))).toBe(false);
    await user.click(screen.getByRole('option', { name: 'Dev' }));
    expect(matches(run('knowledge-agent', 'dev'))).toBe(true);
    expect(matches(run('support-agent', 'dev'))).toBe(false);
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('combobox', { name: 'Snapshots' }));
    await user.click(screen.getByRole('option', { name: 'Support agent: Snapshot #8' }));
    expect(matches(run('support-agent', 'staging'))).toBe(true);
    expect(matches(run('knowledge-agent', 'dev'))).toBe(false);
    await user.click(screen.getByRole('option', { name: 'Knowledge agent: Snapshot #2' }));
    expect(matches(run('knowledge-agent', 'dev', '2', '0.8.0'))).toBe(true);
    await user.click(screen.getByRole('option', { name: 'All snapshots' }));
    expect(matches(run('support-agent', 'staging', '7', '2.3.1'))).toBe(true);
    expect(screen.getByTestId('agent-evaluation-query-filter')).toHaveTextContent(
      "tags.`mlflow.agent.preview.fleet.release-readiness` = 'true'",
    );
  });

  it('restores fleet evaluation filters and matches missing snapshot tags regardless of version', () => {
    renderPage('/fleets/release-readiness/evaluation-runs?environment=staging&snapshot=none&version=none');
    const props = jest.mocked(ExperimentEvaluationRunsPage).mock.calls.at(-1)?.[0];
    const run = {
      data: {
        tags: [
          { key: 'mlflow.agent.id', value: 'support-agent' },
          { key: 'mlflow.agent.environment', value: 'staging' },
        ],
      },
    } as RunEntity;
    expect(props?.runFilter?.(run)).toBe(true);
    expect(
      props?.runFilter?.({
        ...run,
        data: { ...run.data, tags: [...run.data.tags, { key: 'mlflow.agent.version', value: '2.4.0' }] },
      }),
    ).toBe(true);
    expect(
      props?.runFilter?.({
        ...run,
        data: { ...run.data, tags: [...run.data.tags, { key: 'mlflow.agent.snapshot_id', value: '8' }] },
      }),
    ).toBe(false);
    expect(screen.getByRole('combobox', { name: 'Snapshots' })).toHaveTextContent('No snapshot');
    expect(screen.queryByRole('combobox', { name: 'Publisher versions' })).not.toBeInTheDocument();
  });

  it('passes the environment tag to the real dashboard', () => {
    renderPage('/agents/support-agent/dashboard');
    fireEvent.click(screen.getByTestId('mlflow.agent_registry.environment_filter'));
    fireEvent.click(screen.getByText('Prod'));
    expect(screen.getByTestId('agent-dashboard-query-filters')).toHaveTextContent(
      "trace.tag.`mlflow.agent.environment` = 'prod'",
    );
  });

  it('shows the metric preview limitation for multiple environments', () => {
    renderPage('/agents/support-agent/dashboard?environment=dev,prod');
    expect(screen.getByText(/Charts show all environments when multiple are selected/)).toBeInTheDocument();
    expect(screen.getByTestId('agent-dashboard-query-filters')).not.toHaveTextContent('mlflow.agent.environment');
  });

  it('passes the agent tag and session grouping to the real traces table', () => {
    renderPage('/agents/support-agent/traces?groupBy=session');
    const props = JSON.parse(screen.getByTestId('agent-traces-query-filters').textContent ?? '{}');
    expect(props.additionalFilters).toContainEqual(
      expect.objectContaining({ key: 'mlflow.agent.id', value: 'support-agent' }),
    );
    expect(props.initialGroupBySession).toBe(true);
  });

  it('filters the real traces table across two selected environments', () => {
    renderPage('/agents/support-agent/traces');
    fireEvent.click(screen.getByTestId('mlflow.agent_registry.environment_filter'));
    fireEvent.click(screen.getByText('Dev'));
    fireEvent.click(screen.getByText('Prod'));
    const props = JSON.parse(screen.getByTestId('agent-traces-query-filters').textContent ?? '{}');
    expect(props.additionalFilters).toContainEqual(
      expect.objectContaining({ key: 'mlflow.agent.environment', operator: 'RLIKE', value: '^(dev|prod)$' }),
    );
  });

  it('keeps Sessions out of navigation and redirects old URLs to grouped Traces', async () => {
    renderPage('/agents/support-agent/sessions?environment=prod');
    expect(screen.queryByRole('link', { name: 'Sessions' })).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('agent-traces-query-filters')).toBeInTheDocument());
    const props = JSON.parse(screen.getByTestId('agent-traces-query-filters').textContent ?? '{}');
    expect(props.additionalFilters).toContainEqual(
      expect.objectContaining({ key: 'mlflow.agent.id', value: 'support-agent' }),
    );
    expect(props.additionalFilters).toContainEqual(
      expect.objectContaining({ key: 'mlflow.agent.environment', value: 'prod' }),
    );
    expect(props.initialGroupBySession).toBe(true);
  });

  it('passes the agent tag to the real dashboard', () => {
    renderPage('/agents/support-agent/dashboard');
    expect(screen.getByTestId('agent-dashboard-query-filters')).toHaveTextContent(
      "trace.tag.`mlflow.agent.id` = 'support-agent'",
    );
  });

  it('filters the timeline by environment and links to snapshot evidence', () => {
    renderPage(
      '/agents/support-agent/dashboard?dashboardTab=timeline&environment=prod&startTimeLabel=CUSTOM&startTime=2026-10-01T00:00:00Z&endTime=2026-10-23T00:00:00Z',
    );
    expect(screen.getByRole('link', { name: 'Snapshot 7 first observed' })).toHaveAttribute(
      'href',
      '/agents/support-agent/snapshots?environment=prod&snapshot=7',
    );
    expect(screen.queryByText('Snapshot 8 evaluated')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Snapshot 7 activated' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'support-system updated' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'A2A sync failed' })).toHaveAttribute(
      'href',
      '/agents/support-agent/endpoints?environment=prod',
    );
  });

  it('applies publisher version and date range filters to the timeline', async () => {
    const user = userEvent.setup();
    renderPage(
      '/agents/support-agent/dashboard?dashboardTab=timeline&version=2.3.1&startTimeLabel=CUSTOM&startTime=2026-10-20T00:00:00Z&endTime=2026-10-22T00:00:00Z',
    );
    expect(screen.getByText('No activity matches these filters.')).toBeInTheDocument();
    await user.click(screen.getByRole('combobox', { name: 'Publisher version' }));
    await user.click(screen.getByRole('option', { name: 'All versions' }));
    expect(screen.getByRole('link', { name: 'Snapshot 8 evaluated' })).toBeInTheDocument();
    expect(screen.queryByText('Snapshot 7 evaluated')).not.toBeInTheDocument();
    expect(screen.queryByText('A2A sync failed')).not.toBeInTheDocument();
  });

  it('uses the real evaluation page with agent scoping and linked snapshot context', () => {
    renderPage('/agents/support-agent/evaluation-runs');
    expect(screen.getByTestId('agent-evaluation-query-filter')).toHaveTextContent(
      "tags.`mlflow.agent.id` = 'support-agent'",
    );
    expect(screen.getByTestId('agent-evaluation-read-only')).toHaveTextContent('true');
    expect(screen.getByRole('link', { name: 'Snapshot #7' })).toHaveAttribute(
      'href',
      '/agents/support-agent/snapshots?environment=prod&snapshot=7',
    );
    expect(screen.getByText('v2.4.0')).toBeInTheDocument();
  });

  it('honors and clears the snapshot filter on evaluation runs', async () => {
    const user = userEvent.setup();
    renderPage('/agents/support-agent/evaluation-runs?snapshot=8');
    expect(screen.getByTestId('agent-evaluation-query-filter')).toHaveTextContent(
      "tags.`mlflow.agent.snapshot_id` = '8'",
    );
    await user.click(screen.getByRole('combobox', { name: 'Snapshots' }));
    await user.click(screen.getByRole('option', { name: 'All snapshots' }));
    expect(screen.getByTestId('agent-evaluation-query-filter')).not.toHaveTextContent('mlflow.agent.snapshot_id');
  });

  it('combines snapshot and environment filters on evaluation runs', () => {
    renderPage('/agents/support-agent/evaluation-runs?snapshot=7&environment=prod');
    expect(screen.getByTestId('agent-evaluation-query-filter')).toHaveTextContent(
      "tags.`mlflow.agent.snapshot_id` = '7'",
    );
    expect(screen.getByTestId('agent-evaluation-query-filter')).toHaveTextContent(
      "tags.`mlflow.agent.environment` = 'prod'",
    );
  });

  it('filters multiple evaluation environments and snapshot-free runs without unsupported tag IN queries', () => {
    renderPage('/agents/support-agent/evaluation-runs?environment=dev,prod&snapshot=none&version=2.4.0');
    const props = jest.mocked(ExperimentEvaluationRunsPage).mock.calls.at(-1)?.[0];
    const run = (environment: string, snapshot?: string) =>
      ({
        data: {
          tags: [
            { key: 'mlflow.agent.environment', value: environment },
            ...(snapshot ? [{ key: 'mlflow.agent.snapshot_id', value: snapshot }] : []),
          ],
        },
      }) as RunEntity;
    expect(props?.baseFilter).toContain("tags.`mlflow.agent.version` = '2.4.0'");
    expect(props?.baseFilter).not.toContain(' IN ');
    expect(props?.runFilter?.(run('dev'))).toBe(true);
    expect(props?.runFilter?.(run('prod'))).toBe(true);
    expect(props?.runFilter?.(run('staging'))).toBe(false);
    expect(props?.runFilter?.(run('prod', '7'))).toBe(false);
  });

  it('selects and clears multiple evaluation snapshots and publisher versions', async () => {
    const user = userEvent.setup();
    renderPage('/agents/support-agent/evaluation-runs?snapshot=8&version=2.4.0');
    await user.click(screen.getByRole('combobox', { name: 'Snapshots' }));
    await user.click(screen.getByRole('option', { name: 'Snapshot #7' }));
    expect(screen.getByRole('option', { name: 'Snapshot #8' })).toBeInTheDocument();
    expect(screen.getByTestId('agent-evaluation-query-filter')).not.toHaveTextContent('mlflow.agent.snapshot_id');
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('combobox', { name: 'Publisher versions' }));
    await user.click(screen.getByRole('option', { name: 'Version 2.3.1' }));
    expect(screen.getByTestId('agent-evaluation-query-filter')).not.toHaveTextContent('mlflow.agent.version');
    const props = jest.mocked(ExperimentEvaluationRunsPage).mock.calls.at(-1)?.[0];
    const run = (snapshotId: string, publisherVersion: string) =>
      ({
        data: {
          tags: [
            { key: 'mlflow.agent.snapshot_id', value: snapshotId },
            { key: 'mlflow.agent.version', value: publisherVersion },
          ],
        },
      }) as RunEntity;
    expect(props?.runFilter?.(run('8', '2.4.0'))).toBe(true);
    expect(props?.runFilter?.(run('7', '2.3.1'))).toBe(true);
    expect(props?.runFilter?.(run('6', '2.3.1'))).toBe(false);
    expect(props?.runFilter?.(run('7', 'other'))).toBe(false);
    await user.click(screen.getByRole('option', { name: 'All versions' }));
    await user.keyboard('{Escape}');
    await user.click(screen.getByRole('combobox', { name: 'Snapshots' }));
    await user.click(screen.getByRole('option', { name: 'All snapshots' }));
    expect(jest.mocked(ExperimentEvaluationRunsPage).mock.calls.at(-1)?.[0]?.runFilter).toBeUndefined();
  });

  it('includes missing snapshot and version values only when selected', () => {
    renderPage('/agents/support-agent/evaluation-runs?snapshot=7,none&version=2.4.0,none&environment=prod,unassigned');
    const props = jest.mocked(ExperimentEvaluationRunsPage).mock.calls.at(-1)?.[0];
    expect(props?.baseFilter).toBe("tags.`mlflow.agent.id` = 'support-agent'");
    expect(props?.runFilter?.({ data: { tags: [] } } as unknown as RunEntity)).toBe(true);
    expect(props?.runFilter?.({ data: { tags: [{ key: 'mlflow.agent.snapshot_id', value: '8' }] } } as RunEntity)).toBe(
      false,
    );
  });

  it('keeps the cached A2A card without a sync failure banner', () => {
    renderPage('/agents/support-agent/endpoints');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByText(/The A2A card could not be fetched/)).not.toBeInTheDocument();
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'A2A card' }));
    expect(screen.getByText(/order-help/)).toBeInTheDocument();
  });

  it('shows capability cards with synced descriptions, tags, examples, and protocol support', async () => {
    const user = userEvent.setup();
    renderPage('/agents/support-agent/endpoints');
    await user.click(screen.getByRole('tab', { name: 'Capabilities' }));
    const capability = screen.getByRole('article', { name: 'Answer order questions' });
    expect(within(capability).getByText('Look up order status and delivery details.')).toBeInTheDocument();
    expect(within(capability).getByText('orders')).toBeInTheDocument();
    expect(within(capability).getByText('Where is my order?')).toBeInTheDocument();
    const protocol = screen.getByRole('list', { name: 'Protocol capabilities' });
    expect(within(protocol).getByText('Streaming')).toBeInTheDocument();
    expect(within(protocol).getByText('Supported')).toBeInTheDocument();
    expect(within(protocol).getByText('Not supported')).toBeInTheDocument();
  });

  it('renders endpoint-declared capabilities without inventing card details', async () => {
    const user = userEvent.setup();
    renderPage('/agents/refund-agent/endpoints');
    await user.click(screen.getByRole('tab', { name: 'Capabilities' }));
    expect(screen.getByRole('article', { name: 'Check refund eligibility' })).toBeInTheDocument();
    expect(screen.getByText('Endpoint declaration')).toBeInTheDocument();
    expect(screen.queryByText('Example requests')).not.toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Protocol capabilities' })).not.toBeInTheDocument();
  });

  it('renders card sections by default and preserves the original raw JSON', async () => {
    const user = userEvent.setup();
    renderPage('/agents/support-agent/endpoints');
    await user.click(screen.getByRole('tab', { name: 'A2A card' }));
    const rendered = screen.getByRole('region', { name: 'Rendered A2A card' });
    for (const name of ['Metadata', 'Capabilities', 'Skills', 'Security & authentication']) {
      expect(within(rendered).getByRole('heading', { name })).toBeInTheDocument();
    }
    expect(within(rendered).getByText('Look up order status and delivery details.')).toBeInTheDocument();
    expect(within(rendered).getByText('Bearer format')).toBeInTheDocument();
    expect(within(rendered).getByText('JWT')).toBeInTheDocument();
    await user.click(screen.getByRole('radio', { name: 'Raw JSON' }));
    const card = getAgent('support-agent')?.endpoints[0].card;
    expect(JSON.parse(screen.getByRole('region', { name: 'Raw A2A card' }).textContent ?? '{}')).toEqual(card);
    await user.click(screen.getByRole('button', { name: 'Copy A2A card' }));
    expect(copyToClipboard).toHaveBeenLastCalledWith(JSON.stringify(card, null, 2));
    await user.click(screen.getByRole('radio', { name: 'Rendered' }));
    expect(screen.getByRole('region', { name: 'Rendered A2A card' })).toBeInTheDocument();
  });

  it('renders current card interfaces, security scopes, and additional metadata', () => {
    renderWithDesignSystem(
      <AgentCardView
        card={{
          name: 'Example agent',
          supportedInterfaces: [
            { url: 'https://agent.example/a2a', protocolBinding: 'JSONRPC', protocolVersion: '1.0' },
          ],
          securitySchemes: {
            oauth: {
              oauth2SecurityScheme: { flows: { clientCredentials: { tokenUrl: 'https://issuer.example/token' } } },
            },
          },
          securityRequirements: [{ schemes: { oauth: { list: ['agent:invoke'] } } }],
          customField: { tier: 'internal' },
          documentationUrl: 'data:text/html,<script>alert(1)</script>',
        }}
      />,
    );
    expect(screen.getByRole('heading', { name: 'Interfaces' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'https://agent.example/a2a' })).toHaveAttribute(
      'href',
      'https://agent.example/a2a',
    );
    expect(screen.getByText('oauth (agent:invoke)')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Additional metadata' })).toBeInTheDocument();
    expect(screen.getByText('internal')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'data:text/html,<script>alert(1)</script>' })).not.toBeInTheDocument();
  });

  it('shows the official A2A client example for the selected endpoint', async () => {
    const user = userEvent.setup();
    renderPage('/agents/support-agent/endpoints');
    expect(screen.getByText('Python SDK')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'A2A documentation' })).toHaveAttribute(
      'href',
      'https://a2a-protocol.org/latest/tutorials/python/6-interact-with-server/',
    );
    expect(document.body).toHaveTextContent('from a2a.client import ClientConfig, create_client');
    expect(document.body).toHaveTextContent('A2A_BEARERAUTH_TOKEN');
    expect(document.body).toHaveTextContent('agent="https://agents.example.com/support"');
    await user.click(screen.getByRole('button', { name: 'Copy install command' }));
    expect(copyToClipboard).toHaveBeenLastCalledWith('pip install "a2a-sdk>=1.2.2,<2" httpx');
    await user.click(screen.getByRole('button', { name: 'Copy Python example' }));
    expect(copyToClipboard).toHaveBeenLastCalledWith(
      expect.stringContaining('agent="https://agents.example.com/support"'),
    );
    await user.click(screen.getByRole('row', { name: /^staging A2A/ }));
    expect(document.body).toHaveTextContent('agent="https://staging.agents.example.com/support"');
    expect(document.body).not.toHaveTextContent('agent="https://agents.example.com/support"');
  });

  it('reuses MCP connection formats for an MCP endpoint', async () => {
    const user = userEvent.setup();
    renderPage('/agents/refund-agent/endpoints');
    expect(screen.getByRole('radio', { name: 'Claude Code' })).toBeInTheDocument();
    expect(document.body).toHaveTextContent('claude mcp add');
    await user.click(screen.getByRole('radio', { name: '.mcp.json' }));
    expect(document.body).toHaveTextContent('"refund-agent-production"');
    expect(document.body).toHaveTextContent('"url": "https://agents.example.com/refunds/mcp"');
  });

  it('copies the endpoint URL with a compact icon-only control', async () => {
    const user = userEvent.setup();
    renderPage('/agents/refund-agent/endpoints');
    const copyButton = screen.getByRole('button', { name: 'Copy endpoint URL' });
    expect(copyButton).toHaveTextContent('');
    expect(copyButton).toHaveAttribute('title', 'Copy endpoint URL');
    await user.click(copyButton);
    expect(copyToClipboard).toHaveBeenLastCalledWith('https://agents.example.com/refunds/mcp');
  });

  it('changes the copied example to the selected card security requirement', async () => {
    const user = userEvent.setup();
    renderPage('/agents/support-agent/endpoints');
    await user.click(screen.getByRole('combobox', { name: 'Authentication' }));
    await user.click(screen.getByRole('option', { name: 'apiKeyAuth' }));
    expect(document.body).toHaveTextContent('"X-API-Key": os.environ["A2A_APIKEYAUTH_API_KEY"]');
    expect(document.body).not.toHaveTextContent('"Authorization": "Bearer "');
    await user.click(screen.getByRole('button', { name: 'Copy Python example' }));
    expect(copyToClipboard).toHaveBeenLastCalledWith(
      expect.stringContaining('"X-API-Key": os.environ["A2A_APIKEYAUTH_API_KEY"]'),
    );
  });

  it('resets endpoint selection when navigating to another agent', async () => {
    const user = userEvent.setup();
    renderWithDesignSystem(
      <TestRouter
        initialEntries={['/agents/support-agent/endpoints']}
        routes={[
          testRoute(
            <>
              <Link componentId="test.agent_endpoint.switch_agent" to="/agents/refund-agent/endpoints">
                Refund endpoint
              </Link>
              <AgentDetailPage />
            </>,
            '/agents/:agentId/:section',
          ),
        ]}
      />,
    );
    await user.click(screen.getByRole('row', { name: /^staging A2A/ }));
    await user.click(screen.getByRole('link', { name: 'Refund endpoint' }));
    expect(screen.getByRole('radio', { name: 'Claude Code' })).toBeInTheDocument();
    expect(screen.queryByText(/This agent has no access endpoint/)).not.toBeInTheDocument();
  });

  it.each([
    ['2026-10-21T09:59:59Z', false],
    ['2026-10-21T10:00:00Z', false],
    ['2026-10-21T10:00:01Z', true],
  ])('warns about stale A2A sync only after 24 hours at %s', (now, expectedWarning) => {
    const dateNow = jest.spyOn(Date, 'now').mockReturnValue(Date.parse(now));
    try {
      renderPage('/agents/support-agent/endpoints');
      const warning = screen.queryByRole('button', { name: 'Sync is more than 24 hours old' });
      expect(Boolean(warning)).toBe(expectedWarning);
    } finally {
      dateNow.mockRestore();
    }
  });

  it('explains the stale sync on hover and removes the warning for a recently synced endpoint', async () => {
    const dateNow = jest.spyOn(Date, 'now').mockReturnValue(Date.parse('2026-10-21T11:00:00Z'));
    try {
      const user = userEvent.setup();
      renderPage('/agents/support-agent/endpoints');
      await user.hover(screen.getByRole('button', { name: 'Sync is more than 24 hours old' }));
      expect(await screen.findByRole('tooltip')).toHaveTextContent('The latest sync failed');
      await user.click(screen.getByRole('row', { name: /^staging A2A/ }));
      expect(screen.queryByRole('button', { name: 'Sync is more than 24 hours old' })).not.toBeInTheDocument();
    } finally {
      dateNow.mockRestore();
    }
  });

  it('switches endpoint details and connection tabs without carrying over the previous card', async () => {
    const user = userEvent.setup();
    renderPage('/agents/support-agent/endpoints');
    expect(screen.getByRole('tab', { name: 'Connect' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByRole('tab', { name: 'Overview' })).not.toBeInTheDocument();
    expect(screen.getByText('Connect to this endpoint')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: 'Capabilities' }));
    await user.click(screen.getByRole('row', { name: /^staging A2A/ }));
    expect(screen.getByRole('tab', { name: 'Connect' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByText(/The A2A card could not be fetched/)).not.toBeInTheDocument();
    expect(screen.getByText('https://staging.agents.example.com/support')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: 'A2A card' }));
    expect(screen.getByText('No A2A card is available for this endpoint.')).toBeInTheDocument();
  });

  it('links an observed related agent to filtered traces', () => {
    renderPage('/agents/support-agent/related');
    expect(screen.getByRole('link', { name: 'View supporting traces' })).toHaveAttribute(
      'href',
      '/agents/support-agent/traces?environment=all&relatedAgent=refund-agent',
    );
  });

  it('shows one directory-style row per related agent', () => {
    renderPage('/agents/support-agent/related?environment=prod');
    for (const name of ['Name', 'Description', 'Owner', 'Relationship', 'Access endpoint', 'Evidence']) {
      expect(screen.getByRole('columnheader', { name })).toBeInTheDocument();
    }
    expect(screen.getAllByRole('link', { name: 'Refund agent' })).toHaveLength(1);
    expect(screen.getByRole('link', { name: 'Refund agent' })).toHaveAttribute(
      'href',
      '/agents/refund-agent/dashboard',
    );
    expect(screen.getByText('Payments')).toBeInTheDocument();
    expect(screen.getByText('Declared dependency')).toBeInTheDocument();
    expect(screen.getByText('Observed outbound call')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'View supporting traces' })).toHaveAttribute(
      'href',
      '/agents/support-agent/traces?environment=prod&relatedAgent=refund-agent',
    );
  });

  it('filters related agents by relationship and text', async () => {
    const user = userEvent.setup();
    renderPage('/agents/support-agent/related');
    await user.click(screen.getByRole('combobox', { name: 'Relationship' }));
    await user.click(screen.getByRole('option', { name: 'Declared dependency' }));
    expect(screen.getByRole('link', { name: 'Refund agent' })).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'View supporting traces' })).not.toBeInTheDocument();
    await user.type(screen.getByPlaceholderText('Search related agents'), 'missing');
    expect(screen.getByText('No related agents match these filters.')).toBeInTheDocument();
    await user.clear(screen.getByPlaceholderText('Search related agents'));
    expect(screen.getByRole('link', { name: 'Refund agent' })).toBeInTheDocument();
  });
});
