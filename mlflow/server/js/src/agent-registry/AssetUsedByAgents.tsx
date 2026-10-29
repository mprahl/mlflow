import { Fragment, useState, type ReactNode } from 'react';
import {
  Button,
  ChevronRightIcon,
  Table,
  TableCell,
  TableFilterInput,
  TableFilterLayout,
  TableHeader,
  TableRow,
  Tabs,
  Tag,
  Typography,
  useDesignSystemTheme,
} from '@databricks/design-system';

import { Link } from '../common/utils/RoutingUtils';
import { agentPath, EnvironmentFilter, formatDate, getSelectedEnvironments } from './components';
import { agents } from './mockData';
import type { Agent, AgentAsset } from './mockData';

export type RegistryAssetVersion = { kind: 'prompt' | 'skill' | 'mcp'; name: string; version: string };

const matchesAsset = (candidate: AgentAsset, asset: RegistryAssetVersion) => {
  if (candidate.kind !== asset.kind || candidate.version !== asset.version) return false;
  if (!candidate.href) return asset.kind !== 'mcp' && candidate.name === asset.name;
  const prefix = { prompt: '/prompts/', skill: '/skills/', mcp: '/mcp-registry/' }[asset.kind];
  const url = new URL(candidate.href, 'http://mlflow.local');
  return (
    url.origin === 'http://mlflow.local' &&
    url.pathname.startsWith(prefix) &&
    decodeURIComponent(url.pathname.slice(prefix.length)) === asset.name
  );
};

export const getAgentsUsingAsset = (asset: RegistryAssetVersion, records: Agent[] = agents) =>
  records.flatMap((agent) => {
    const snapshots = agent.snapshots.filter((snapshot) => snapshot.assets.some((item) => matchesAsset(item, asset)));
    return snapshots.length ? [{ agent, snapshots }] : [];
  });

export const AssetUsedByAgentsTab = ({ asset }: { asset: RegistryAssetVersion }) => (
  <Tabs.Trigger value="agents">Used by agents ({getAgentsUsingAsset(asset).length})</Tabs.Trigger>
);

export const AssetUsageTabList = ({ children }: { children: ReactNode }) => {
  const { theme } = useDesignSystemTheme();
  return (
    <Tabs.List
      scrollAreaViewportCss={{ backgroundImage: 'none' }}
      css={{
        '& [role="tab"]': {
          height: theme.general.heightSm,
          '&[data-state="active"]': {
            boxShadow: 'none',
            borderBottom: `${theme.spacing.xs}px solid ${theme.colors.actionPrimaryBackgroundDefault}`,
            paddingBottom: theme.spacing.xs,
          },
        },
      }}
    >
      {children}
    </Tabs.List>
  );
};

export const AssetUsedByAgents = ({ asset }: { asset: RegistryAssetVersion }) => {
  const { theme } = useDesignSystemTheme();
  const [search, setSearch] = useState('');
  const [environment, setEnvironment] = useState('all');
  const [expanded, setExpanded] = useState<string[]>([]);
  const selectedEnvironments = getSelectedEnvironments(environment);
  const references = getAgentsUsingAsset(asset);
  const rows = references.flatMap(({ agent, snapshots }) => {
    const snapshotIds = new Set(snapshots.map((snapshot) => snapshot.id));
    const traces = agent.traces.filter((trace) => trace.snapshotId != null && snapshotIds.has(trace.snapshotId));
    const evaluations = agent.evaluations.filter((evaluation) => snapshotIds.has(evaluation.snapshotId));
    const environments = Array.from(new Set([...traces, ...evaluations].map((item) => item.environment)));
    const visibleTraces = traces.filter(
      (trace) => !selectedEnvironments.length || selectedEnvironments.includes(trace.environment),
    );
    if (
      !`${agent.name} ${agent.owner} ${agent.description}`.toLowerCase().includes(search.trim().toLowerCase()) ||
      (selectedEnvironments.length && !environments.some((value) => selectedEnvironments.includes(value)))
    ) {
      return [];
    }
    return [
      {
        agent,
        snapshots,
        environments,
        lastTrace: visibleTraces
          .map((trace) => trace.at)
          .sort()
          .at(-1),
      },
    ];
  });

  return (
    <div css={{ display: 'flex', flexDirection: 'column', gap: theme.spacing.md, minWidth: 0 }}>
      <TableFilterLayout>
        <TableFilterInput
          componentId="mlflow.agent_registry.asset_usage.search"
          placeholder="Search agents"
          ignoreFilterMediaSizing
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          css={{ width: 240, maxWidth: '100%' }}
        />
        <EnvironmentFilter value={environment} onChange={setEnvironment} />
      </TableFilterLayout>
      <Table
        scrollable
        css={{
          '& [role=row]': { minWidth: 680 },
          '& [role=columnheader]:first-of-type, & [role=cell]:first-of-type': { flex: 1.5 },
        }}
        empty={
          !rows.length ? (
            <div
              css={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 240, width: '100%' }}
            >
              <Typography.Text color="secondary">
                {references.length ? 'No agents match these filters.' : 'No agent snapshots reference this version.'}
              </Typography.Text>
            </div>
          ) : undefined
        }
      >
        <TableRow isHeader>
          {['Agent', 'Evidence', 'Snapshots', 'Last snapshot trace'].map((header) => (
            <TableHeader key={header} componentId="mlflow.agent_registry.asset_usage.header" multiline>
              {header}
            </TableHeader>
          ))}
        </TableRow>
        {rows.map(({ agent, snapshots, environments, lastTrace }) => (
          <Fragment key={agent.id}>
            <TableRow>
              <TableCell>
                <div css={{ display: 'flex', flexDirection: 'column', minWidth: 0, gap: theme.spacing.xs }}>
                  <Link
                    componentId="mlflow.agent_registry.asset_usage.agent"
                    to={`${agentPath(agent.id, 'dashboard', environment)}${snapshots.length === 1 ? `&snapshot=${snapshots[0].id}` : ''}`}
                  >
                    {agent.name}
                  </Link>
                  <Typography.Text color="secondary" size="sm" ellipsis title={agent.owner}>
                    {agent.owner}
                  </Typography.Text>
                </div>
              </TableCell>
              <TableCell>
                <Tag componentId="mlflow.agent_registry.asset_usage.evidence">In snapshot</Tag>
              </TableCell>
              <TableCell>
                <Button
                  componentId="mlflow.agent_registry.asset_usage.expand"
                  type="link"
                  aria-label={`Snapshot references for ${agent.name}`}
                  aria-expanded={expanded.includes(agent.id)}
                  icon={
                    <ChevronRightIcon css={{ transform: expanded.includes(agent.id) ? 'rotate(90deg)' : undefined }} />
                  }
                  onClick={() =>
                    setExpanded(
                      expanded.includes(agent.id) ? expanded.filter((id) => id !== agent.id) : [...expanded, agent.id],
                    )
                  }
                >
                  {snapshots.length}
                </Button>
              </TableCell>
              <TableCell>{lastTrace ? formatDate(lastTrace) : 'Not observed'}</TableCell>
            </TableRow>
            {expanded.includes(agent.id) && (
              <TableRow>
                <TableCell aria-colspan={4}>
                  <div
                    role="region"
                    aria-label={`Snapshot references for ${agent.name}`}
                    css={{ width: '100%', display: 'grid', gap: theme.spacing.sm, padding: theme.spacing.sm }}
                  >
                    {snapshots.map((snapshot) => (
                      <div
                        key={snapshot.id}
                        css={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: theme.spacing.md }}
                      >
                        <Link
                          componentId="mlflow.agent_registry.asset_usage.snapshot"
                          to={`${agentPath(agent.id, 'snapshots', environment)}&snapshot=${snapshot.id}`}
                        >
                          Snapshot #{snapshot.id}
                        </Link>
                        <Typography.Text color="secondary">Version {snapshot.publisherVersion}</Typography.Text>
                        <Link
                          componentId="mlflow.agent_registry.asset_usage.dashboard"
                          to={`${agentPath(agent.id, 'dashboard', environment)}&snapshot=${snapshot.id}`}
                        >
                          Dashboard
                        </Link>
                        <Link
                          componentId="mlflow.agent_registry.asset_usage.traces"
                          to={`${agentPath(agent.id, 'traces', environment)}&snapshot=${snapshot.id}`}
                        >
                          Snapshot traces
                        </Link>
                        <Link
                          componentId="mlflow.agent_registry.asset_usage.evaluations"
                          to={`${agentPath(agent.id, 'evaluation-runs', environment)}&snapshot=${snapshot.id}`}
                        >
                          Snapshot evaluations
                        </Link>
                      </div>
                    ))}
                  </div>
                </TableCell>
              </TableRow>
            )}
          </Fragment>
        ))}
      </Table>
    </div>
  );
};
