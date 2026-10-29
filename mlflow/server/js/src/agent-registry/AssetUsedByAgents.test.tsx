import { describe, expect, it } from '@jest/globals';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderWithDesignSystem } from '../common/utils/TestUtils.react18';
import { TestRouter, testRoute } from '../common/utils/RoutingTestUtils';
import { AssetUsedByAgents, getAgentsUsingAsset } from './AssetUsedByAgents';
import type { RegistryAssetVersion } from './AssetUsedByAgents';

describe('asset version Agent references', () => {
  it.each<{ asset: RegistryAssetVersion; agentIds: string[] }>([
    { asset: { kind: 'skill', name: 'incident-triage', version: '3' }, agentIds: ['support-agent'] },
    {
      asset: { kind: 'skill', name: 'documentation-research', version: '3' },
      agentIds: ['support-agent', 'knowledge-agent'],
    },
    { asset: { kind: 'skill', name: 'code-review', version: '3' }, agentIds: ['knowledge-agent'] },
    {
      asset: { kind: 'mcp', name: 'com.example/order-lookup', version: '1.2.0' },
      agentIds: ['support-agent', 'refund-agent'],
    },
    { asset: { kind: 'mcp', name: 'com.example/postgres-query', version: '1.1.0' }, agentIds: ['refund-agent'] },
    {
      asset: { kind: 'mcp', name: 'com.example/github-tools', version: '2.0.0' },
      agentIds: ['support-agent', 'knowledge-agent'],
    },
    { asset: { kind: 'mcp', name: 'com.example/docs-search', version: '2.1.0' }, agentIds: ['support-agent'] },
  ])('links seeded $asset.kind $asset.name version $asset.version', ({ asset, agentIds }) => {
    expect(getAgentsUsingAsset(asset).map(({ agent }) => agent.id)).toEqual(agentIds);
  });

  it('matches exact versions and full registry identities', () => {
    expect(
      getAgentsUsingAsset({ kind: 'skill', name: 'refund-policy', version: '2' }).map(({ agent }) => agent.id),
    ).toEqual(['support-agent', 'refund-agent']);
    expect(
      getAgentsUsingAsset({ kind: 'skill', name: 'refund-policy', version: '3' }).map(({ agent }) => agent.id),
    ).toEqual(['support-agent']);
    expect(getAgentsUsingAsset({ kind: 'skill', name: '@other/refund-policy', version: '2' })).toEqual([]);
    expect(getAgentsUsingAsset({ kind: 'mcp', name: 'com.example/order-lookup', version: '1.0.0' })).toHaveLength(1);
    expect(getAgentsUsingAsset({ kind: 'mcp', name: 'other.example/order-lookup', version: '1.0.0' })).toEqual([]);
    expect(
      getAgentsUsingAsset({ kind: 'prompt', name: 'support-system', version: '3' })[0].snapshots.map(
        (snapshot) => snapshot.id,
      ),
    ).toEqual([7, 6]);
    expect(
      getAgentsUsingAsset({ kind: 'prompt', name: 'support-system', version: '4' })[0].snapshots.map(
        (snapshot) => snapshot.id,
      ),
    ).toEqual([9, 8]);
  });

  it('groups Agents and expands exact snapshot evidence links', async () => {
    const user = userEvent.setup();
    renderWithDesignSystem(
      <TestRouter
        initialEntries={['/test']}
        routes={[
          testRoute(<AssetUsedByAgents asset={{ kind: 'skill', name: 'refund-policy', version: '2' }} />, '/test'),
        ]}
      />,
    );
    expect(screen.getAllByRole('link', { name: 'Support agent' })).toHaveLength(1);
    expect(screen.getByRole('link', { name: 'Refund agent' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Refund agent' })).toHaveAttribute(
      'href',
      '/agents/refund-agent/dashboard?environment=all&snapshot=3',
    );
    expect(screen.getAllByText('In snapshot')).toHaveLength(2);
    await user.click(screen.getByRole('button', { name: 'Snapshot references for Support agent' }));
    const references = within(screen.getByRole('region', { name: 'Snapshot references for Support agent' }));
    expect(references.getByRole('link', { name: 'Snapshot #8' })).toHaveAttribute(
      'href',
      '/agents/support-agent/snapshots?environment=all&snapshot=8',
    );
    expect(
      references.getAllByRole('link', { name: 'Snapshot traces' }).map((link) => link.getAttribute('href')),
    ).toEqual([
      '/agents/support-agent/traces?environment=all&snapshot=8',
      '/agents/support-agent/traces?environment=all&snapshot=7',
    ]);
    await user.type(screen.getByPlaceholderText('Search agents'), 'Payments');
    expect(screen.queryByRole('link', { name: 'Support agent' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Refund agent' })).toBeInTheDocument();
    await user.clear(screen.getByPlaceholderText('Search agents'));
    expect(screen.queryByTestId('mlflow.agent_registry.environment_filter')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Refund agent' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Support agent' })).toBeInTheDocument();
    expect(
      within(screen.getByRole('region', { name: 'Snapshot references for Support agent' }))
        .getAllByRole('link', { name: 'Snapshot traces' })
        .map((link) => link.getAttribute('href')),
    ).toEqual([
      '/agents/support-agent/traces?environment=all&snapshot=8',
      '/agents/support-agent/traces?environment=all&snapshot=7',
    ]);
  });

  it('does not infer usage for an unreferenced version', () => {
    renderWithDesignSystem(
      <TestRouter
        initialEntries={['/test']}
        routes={[
          testRoute(
            <AssetUsedByAgents asset={{ kind: 'mcp', name: 'com.example/order-lookup', version: '1.1.0' }} />,
            '/test',
          ),
        ]}
      />,
    );
    expect(screen.getByText('No agent snapshots reference this version.')).toBeInTheDocument();
  });
});
