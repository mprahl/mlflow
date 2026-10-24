import { afterEach, describe, expect, it, jest } from '@jest/globals';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ComponentProps } from 'react';

import { renderWithDesignSystem } from '../common/utils/TestUtils.react18';
import { TestRouter, testRoute } from '../common/utils/RoutingTestUtils';
import { useSearchParams } from '../common/utils/RoutingUtils';
import { AgentInvestigationDrawer, InvestigationButton } from './AgentInvestigations';
import { agents } from './mockData';
import { ModelTraceExplorerViewStateProvider } from '../shared/web-shared/model-trace-explorer/v2/ModelTraceExplorerViewStateContext';

jest.mock('../shared/web-shared/model-trace-explorer/v2/ModelTraceExplorerViewStateContext', () => ({
  ModelTraceExplorerViewStateProvider: jest.fn(
    ({ children }: ComponentProps<typeof ModelTraceExplorerViewStateProvider>) => <div>{children}</div>,
  ),
}));
jest.mock('../shared/web-shared/model-trace-explorer/v2/ModelTraceExplorerDetailView', () => ({
  ModelTraceExplorerDetailView: () => <div>Shared trace explorer</div>,
}));

const Page = ({ agentId }: { agentId: string }) => {
  const [params] = useSearchParams();
  const agent = agents.find((item) => item.id === agentId);
  if (!agent) throw new Error(`Unknown test agent ${agentId}`);
  return (
    <>
      <output data-testid="page-params">{params.toString()}</output>
      <InvestigationButton mode="change" snapshotId={8}>
        Investigate change
      </InvestigationButton>
      <AgentInvestigationDrawer agent={agent} environment={params.get('environment') ?? 'all'} />
    </>
  );
};

const renderPage = (query: string, agentId = 'support-agent') =>
  renderWithDesignSystem(
    <TestRouter
      initialEntries={[`/agents/${agentId}/traces?${query}`]}
      routes={[testRoute(<Page agentId={agentId} />, '/agents/:agentId/traces')]}
    />,
  );

const selectOption = async (label: string, option: string) => {
  const user = userEvent.setup();
  await user.click(screen.getByRole('combobox', { name: label }));
  await user.click(screen.getByRole('option', { name: option }));
};

afterEach(() => {
  jest.clearAllMocks();
});

describe('agent investigations', () => {
  it('opens from an action and restores the original filters on close', async () => {
    const user = userEvent.setup();
    renderPage('environment=prod&snapshot=8&groupBy=session');
    await user.click(screen.getByRole('button', { name: 'Investigate change' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('Illustrative evidence - read-only preview');
    expect(screen.getByLabelText('Baseline')).toHaveValue('7');
    expect(screen.getByLabelText('Comparison')).toHaveValue('8');
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByTestId('page-params')).toHaveTextContent('environment=prod&snapshot=8&groupBy=session');
    expect(screen.getByTestId('page-params')).not.toHaveTextContent('investigation');
  });

  it('shows exact cohort evidence, excludes mixed attribution, and opens read-only trace comparison', async () => {
    const user = userEvent.setup();
    renderPage('investigation=change&environment=prod');
    expect(screen.getByRole('row', { name: /prompt: support-system 3 4/ })).toBeInTheDocument();
    expect(screen.getByRole('row', { name: /Baseline .* 1 2 1 620/ })).toBeInTheDocument();
    expect(screen.getByText(/1 mixed-snapshot example/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'example-support-mixed' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'example-support-after' }));
    await user.click(screen.getByRole('button', { name: 'Compare matched baseline trace' }));
    expect(jest.mocked(ModelTraceExplorerViewStateProvider).mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({
        readOnly: true,
        assessmentsPaneEnabled: false,
        hideAssessments: true,
        modelTrace: expect.objectContaining({ info: expect.objectContaining({ trace_id: 'example-support-after' }) }),
        comparisonModelTrace: expect.objectContaining({
          info: expect.objectContaining({ trace_id: 'example-support-before' }),
        }),
      }),
    );
    expect(screen.getByRole('link', { name: 'Open evaluation runs' })).toHaveAttribute(
      'href',
      '/agents/support-agent/evaluation-runs?environment=prod&snapshot=8',
    );
    expect(screen.getByText(/Evaluation runs use existing experiment data/)).toBeInTheDocument();
  });

  it('switches comparison direction and reports unavailable snapshots explicitly', async () => {
    const user = userEvent.setup();
    renderPage('investigation=change&environment=prod&investigationSnapshot=999');
    expect(screen.getByText(/A selected snapshot is unavailable/)).toBeInTheDocument();
    await selectOption('Comparison', 'Snapshot #8');
    await selectOption('Baseline', 'Snapshot #7');
    await user.click(screen.getByRole('button', { name: 'Switch sides' }));
    expect(screen.getByRole('row', { name: /prompt: support-system 4 3/ })).toBeInTheDocument();
  });

  it('shows errors even when the request succeeds and follows explicit fallback evidence', async () => {
    const user = userEvent.setup();
    renderPage('investigation=dependencies&environment=prod');
    expect(screen.getByLabelText('Tool')).toHaveValue('lookup_order');
    await selectOption('Request outcome', 'Tool errors with successful request');
    expect(screen.queryByRole('button', { name: 'example-support-failed' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'example-support-recovered' }));
    expect(
      screen.getByText(/2 invocation\(s\), 2 error\(s\), 1 explicit retry link\(s\), 1 explicit fallback/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'escalate_case' }));
    expect(jest.mocked(ModelTraceExplorerViewStateProvider).mock.calls.at(-1)?.[0]).toEqual(
      expect.objectContaining({ readOnly: true, selectedSpanIdOnRender: 'escalate' }),
    );
    await user.click(screen.getByRole('tab', { name: 'Runtime context' }));
    expect(screen.getByText('Self-service resolution: failed (example assessment)')).toBeInTheDocument();
  });

  it('does not label repeated calls as retries and distinguishes offered-only tools', async () => {
    const user = userEvent.setup();
    renderPage('investigation=dependencies&environment=prod');
    await user.click(screen.getByRole('button', { name: 'example-support-after' }));
    expect(screen.getByText(/0 explicit retry link/)).toBeInTheDocument();
    await selectOption('Tool', 'escalate_case');
    await user.click(screen.getByRole('button', { name: 'example-support-before' }));
    expect(screen.getByText(/Offered in 1 model call\(s\). 0 invocation/)).toBeInTheDocument();
  });

  it('filters by child-span instance and keeps mixed attribution visible', async () => {
    const user = userEvent.setup();
    renderPage('investigation=runtime&environment=prod');
    await user.click(screen.getByRole('combobox', { name: 'Instance IDs' }));
    await user.click(screen.getByRole('option', { name: 'support-worker-b' }));
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('button', { name: 'example-support-before' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'example-support-mixed' }));
    expect(screen.getByRole('row', { name: 'Snapshot attribution Multiple snapshots (#7, #8)' })).toBeInTheDocument();
    expect(
      screen.getByRole('row', { name: 'Observed instance IDs (any span) support-worker-a, support-worker-b' }),
    ).toBeInTheDocument();
  });

  it('inspects snapshot-free harness context without inventing publisher or skill provenance', async () => {
    const user = userEvent.setup();
    renderPage('investigation=runtime&environment=dev', 'knowledge-agent');
    await selectOption('Snapshot attribution', 'No snapshot');
    await user.click(screen.getByRole('button', { name: 'example-knowledge-error' }));
    expect(screen.getByRole('row', { name: 'Observed harness Claude Code 2.1' })).toBeInTheDocument();
    expect(screen.getByRole('row', { name: 'Publisher version Not recorded' })).toBeInTheDocument();
    expect(screen.getByRole('row', { name: 'Verified available skills Not recorded' })).toBeInTheDocument();
    expect(screen.getByRole('row', { name: 'Snapshot-declared harness Not available' })).toBeInTheDocument();
  });

  it('compares runtime evidence across instances without requiring snapshots', async () => {
    const user = userEvent.setup();
    renderPage('investigation=runtime&environment=dev', 'knowledge-agent');
    await user.click(screen.getByRole('button', { name: 'example-knowledge-current' }));
    await user.click(screen.getByRole('button', { name: 'Use as baseline' }));
    await user.click(screen.getByRole('button', { name: 'example-knowledge-error' }));
    expect(screen.getByRole('row', { name: 'Observed harness Claude Code 2.0 Claude Code 2.1' })).toBeInTheDocument();
    expect(screen.getByRole('row', { name: 'Snapshot attribution No snapshot No snapshot' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Clear baseline' }));
    expect(screen.queryByRole('columnheader', { name: 'Baseline evidence' })).not.toBeInTheDocument();
  });

  it('clears selected evidence when the environment changes and never substitutes another agent', async () => {
    const user = userEvent.setup();
    renderPage('investigation=runtime&environment=prod');
    await user.click(screen.getByRole('button', { name: 'example-support-before' }));
    await selectOption('Example environment', 'staging');
    expect(screen.getByText(/No investigation examples for this agent and environment/)).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'Runtime context' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'example-knowledge-current' })).not.toBeInTheDocument();
  });

  it('shows missing snapshot and instance metadata as unknown for unassigned activity', async () => {
    const user = userEvent.setup();
    renderPage('investigation=runtime&environment=unassigned');
    await user.click(screen.getByRole('button', { name: 'example-support-unattributed' }));
    expect(screen.getByRole('row', { name: 'Snapshot attribution No snapshot' })).toBeInTheDocument();
    expect(
      within(screen.getByRole('row', { name: 'Observed instance IDs (any span) Not recorded' })).getByText(
        'Not recorded',
      ),
    ).toBeInTheDocument();
  });
});
