import { useEffect, useState, type ReactNode } from 'react';
import {
  Breadcrumb,
  Button,
  ColumnsIcon,
  CopyIcon,
  ExpandMoreIcon,
  PageWrapper,
  SegmentedControlButton,
  SegmentedControlGroup,
  SimpleSelect,
  SimpleSelectOption,
  Tag,
  Table,
  TableCell,
  TableFilterInput,
  TableFilterLayout,
  TableHeader,
  TableRow,
  Tabs,
  Tooltip,
  Typography,
  WarningIcon,
  ZoomMarqueeSelection,
  useDesignSystemTheme,
} from '@databricks/design-system';

import { Link, useNavigate, useParams, useSearchParams } from '../common/utils/RoutingUtils';
import { CopyButton } from '../shared/building_blocks/CopyButton';
import { RegistryVersionList } from '../common/components/RegistryVersionList';
import { MCPServerVersionDiffSelectorButton } from '../mcp-registry/components/MCPServerVersionDiffSelectorButton';
import { ExperimentPageMainContent } from '../experiment-tracking/pages/experiment-page-tabs/ExperimentPageMainContent';
import { agentPath, formatDate, matchesEnvironment, Panel } from './components';
import type { AccessEndpoint, Agent, AgentSnapshot } from './mockData';
import { getAgent } from './mockData';
import { AgentJourneyOverview } from './AgentJourneyOverview';
import { getAgentSectionLabel } from './AgentNavConfig';
import { AgentDashboard } from './AgentDashboard';
import { AgentTracingSetup } from './AgentTracingSetup';
import { AgentEvaluationRunsPage, AgentEvaluationRunsTable, EvaluationFilter } from './AgentActivityPages';
import { AgentLiveTracesPage, AgentSnapshotTraces } from './AgentLiveTracesPage';
import { AgentAssetsTable } from './AgentAssetsTable';
import { AgentEndpointConnection } from './AgentEndpointConnection';
import { AgentCardView } from './AgentCardView';
import { AgentCapabilities } from './AgentCapabilities';
import { getSnapshotAssetChanges } from './assetChanges';
import { SnapshotPerformance, SnapshotEvaluationStatistics, SnapshotTimeline } from './AgentSnapshotAnalytics';
import { AgentOverview } from './AgentOverview';

const assetSections = ['assets', 'prompts', 'tools', 'skills', 'plugins', 'mcp-servers', 'models'];

const RegistryDetailLayout = ({ list, children }: { list: ReactNode; children: ReactNode }) => {
  const { theme } = useDesignSystemTheme();
  return (
    <div
      css={{
        display: 'flex',
        height: '100%',
        minWidth: 0,
        minHeight: 0,
        overflow: 'hidden',
        '@media (max-width: 760px)': { flexDirection: 'column' },
      }}
    >
      <div
        css={{
          flex: '0 0 320px',
          display: 'flex',
          flexDirection: 'column',
          minHeight: 0,
          gap: theme.spacing.sm,
          '@media (max-width: 760px)': { flex: '0 0 240px' },
        }}
      >
        {list}
      </div>
      <div
        css={{
          flex: 1,
          minWidth: 0,
          minHeight: 0,
          borderLeft: `1px solid ${theme.colors.border}`,
          padding: theme.spacing.md,
          paddingTop: 0,
          overflowY: 'auto',
          '@media (max-width: 760px)': {
            borderLeft: 0,
            borderTop: `1px solid ${theme.colors.border}`,
            paddingTop: theme.spacing.md,
          },
        }}
      >
        {children}
      </div>
    </div>
  );
};

const SnapshotDetails = ({ snapshot, agent }: { snapshot: AgentSnapshot; agent: Agent }) => {
  const { theme } = useDesignSystemTheme();
  const [searchParams] = useSearchParams();
  const environment = searchParams.get('environment') ?? 'all';
  const scopedPath = (section: string) => `${agentPath(agent.id, section, environment)}&snapshot=${snapshot.id}`;
  const evaluations = agent.evaluations
    .filter(
      (evaluation) => evaluation.snapshotId === snapshot.id && matchesEnvironment(environment, evaluation.environment),
    )
    .sort((left, right) => right.at.localeCompare(left.at))
    .slice(0, 5);
  return (
    <div css={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: theme.spacing.lg }}>
      <div
        css={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: theme.spacing.md,
          flexWrap: 'wrap',
        }}
      >
        <Typography.Title level={3} withoutMargins>
          Viewing snapshot #{snapshot.id}
        </Typography.Title>
        <Link componentId="mlflow.agent_registry.snapshot.dashboard" to={scopedPath('dashboard')}>
          View dashboard
        </Link>
      </div>
      <Panel title="Details">
        <div
          css={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: theme.spacing.md }}
        >
          <div>
            <Typography.Text color="secondary">Publisher version</Typography.Text>
            <div>{snapshot.publisherVersion}</div>
          </div>
          <div>
            <Typography.Text color="secondary">Created</Typography.Text>
            <div>{formatDate(snapshot.createdAt)}</div>
          </div>
          <div>
            <Typography.Text color="secondary">Source</Typography.Text>
            <div>
              <a href={snapshot.sourceUrl} target="_blank" rel="noopener noreferrer">
                {snapshot.source}
              </a>
            </div>
          </div>
          <div>
            <Typography.Text color="secondary">Harness</Typography.Text>
            <div>{snapshot.harness}</div>
          </div>
        </div>
      </Panel>
      <Tabs.Root componentId="mlflow.agent_registry.snapshot.tabs" defaultValue="assets" valueHasNoPii>
        <Tabs.List scrollAreaViewportCss={{ backgroundImage: 'none' }}>
          {[
            ['assets', 'AI assets'],
            ['traces', 'Traces'],
            ['evaluations', 'Evaluations'],
          ].map(([value, label]) => (
            <Tabs.Trigger
              key={value}
              value={value}
              css={{
                height: theme.general.heightSm,
                '&[data-state="active"]': {
                  boxShadow: 'none',
                  borderBottom: `${theme.spacing.xs}px solid ${theme.colors.actionPrimaryBackgroundDefault}`,
                  paddingBottom: theme.spacing.xs,
                },
              }}
            >
              {label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        <Tabs.Content value="assets">
          <AgentAssetsTable agent={agent} section="assets" snapshotId={snapshot.id} />
        </Tabs.Content>
        <Tabs.Content value="traces">
          <Panel
            title="Snapshot performance"
            action={
              <Link componentId="mlflow.agent_registry.snapshot.all_traces" to={scopedPath('traces')}>
                View all traces
              </Link>
            }
          >
            <SnapshotPerformance agent={agent} snapshot={snapshot} />
          </Panel>
        </Tabs.Content>
        <Tabs.Content value="evaluations">
          <Panel
            title="Evaluation statistics"
            action={
              <Link componentId="mlflow.agent_registry.snapshot.all_evaluations" to={scopedPath('evaluation-runs')}>
                View all evaluations
              </Link>
            }
          >
            <SnapshotEvaluationStatistics agent={agent} snapshot={snapshot} />
          </Panel>
        </Tabs.Content>
      </Tabs.Root>
    </div>
  );
};

const SnapshotCompare = ({
  baseline,
  candidate,
  onSwitchSides,
  agentId,
  environment,
}: {
  baseline: AgentSnapshot;
  candidate: AgentSnapshot;
  onSwitchSides: () => void;
  agentId: string;
  environment: string;
}) => {
  const { theme } = useDesignSystemTheme();
  const assetKeys = Array.from(
    new Set([...baseline.assets, ...candidate.assets].map((asset) => `${asset.kind}:${asset.name}`)),
  );
  const rows: { label: string; before: string; after: string; beforeHref?: string; afterHref?: string }[] = [
    { label: 'Source', before: baseline.source, after: candidate.source },
    { label: 'Harness', before: baseline.harness, after: candidate.harness },
    ...assetKeys.map((key) => {
      const left = baseline.assets.find((asset) => `${asset.kind}:${asset.name}` === key);
      const right = candidate.assets.find((asset) => `${asset.kind}:${asset.name}` === key);
      return {
        label: `${left?.kind ?? right?.kind} · ${left?.name ?? right?.name}`,
        before: left ? (left.version === 'provider model' ? 'Provider model' : `version ${left.version}`) : '—',
        after: right ? (right.version === 'provider model' ? 'Provider model' : `version ${right.version}`) : '—',
        beforeHref: left?.href,
        afterHref: right?.href,
      };
    }),
  ];
  return (
    <Panel
      title={`Compare snapshot #${baseline.id} with #${candidate.id}`}
      action={
        <Button
          componentId="mlflow.agent_registry.snapshot.switch_sides"
          aria-label="Switch sides"
          title="Switch sides"
          icon={<ExpandMoreIcon css={{ transform: 'rotate(90deg)' }} />}
          onClick={onSwitchSides}
        />
      }
    >
      <Table scrollable>
        <TableRow isHeader>
          <TableHeader componentId="mlflow.agent_registry.snapshot.compare.header">Field</TableHeader>
          {[baseline, candidate].map((snapshot) => (
            <TableHeader
              key={snapshot.id}
              componentId="mlflow.agent_registry.snapshot.compare.header"
              wrapContent={false}
            >
              <div css={{ display: 'grid', gap: theme.spacing.xs }}>
                <Typography.Text bold>Snapshot #{snapshot.id}</Typography.Text>
                <Link
                  componentId="mlflow.agent_registry.snapshot.compare.dashboard"
                  to={`${agentPath(agentId, 'dashboard', environment)}&snapshot=${snapshot.id}`}
                >
                  View dashboard
                </Link>
              </div>
            </TableHeader>
          ))}
        </TableRow>
        {rows.map((row) => (
          <TableRow
            key={row.label}
            css={{ backgroundColor: row.before !== row.after ? theme.colors.backgroundSecondary : undefined }}
          >
            <TableCell>{row.label}</TableCell>
            <TableCell>
              {row.beforeHref ? (
                <Link componentId="mlflow.agent_registry.compare.baseline_asset" to={row.beforeHref}>
                  {row.before}
                </Link>
              ) : (
                row.before
              )}
            </TableCell>
            <TableCell>
              {row.afterHref ? (
                <Link componentId="mlflow.agent_registry.compare.candidate_asset" to={row.afterHref}>
                  {row.after}
                </Link>
              ) : (
                row.after
              )}
            </TableCell>
          </TableRow>
        ))}
      </Table>
    </Panel>
  );
};

const SnapshotsSection = ({ agent }: { agent: Agent }) => {
  const { theme } = useDesignSystemTheme();
  const [searchParams, setSearchParams] = useSearchParams();
  const [comparisonId, setComparisonId] = useState<number | null>(null);
  const timeline = searchParams.get('view') === 'timeline';
  const setView = (view: string) => {
    const next = new URLSearchParams(searchParams);
    if (view === 'timeline') next.set('view', view);
    else next.delete('view');
    setSearchParams(next);
  };
  const [search, setSearch] = useState('');
  const [versions, setVersions] = useState<string[]>(() =>
    (searchParams.get('version') ?? '').split(',').filter((value) => value && value !== 'all'),
  );
  const [harnesses, setHarnesses] = useState<string[]>([]);
  const visibleSnapshots = agent.snapshots.filter(
    (snapshot) =>
      (!versions.length || versions.includes(snapshot.publisherVersion)) &&
      (!harnesses.length || harnesses.includes(snapshot.harness)) &&
      `Snapshot #${snapshot.id} ${snapshot.publisherVersion} ${snapshot.source} ${snapshot.harness} ${snapshot.assets.map((asset) => asset.name).join(' ')}`
        .toLowerCase()
        .includes(search.trim().toLowerCase()),
  );
  const selectedId = Number(searchParams.get('snapshot')) || agent.snapshots[0]?.id;
  const selected = visibleSnapshots.find((snapshot) => snapshot.id === selectedId) ?? visibleSnapshots[0];
  const comparison = visibleSnapshots.find((snapshot) => snapshot.id === comparisonId);
  const selectSnapshot = (id: number) => {
    if (!selected) return;
    const next = new URLSearchParams(searchParams);
    next.set('snapshot', String(id));
    setSearchParams(next);
    if (id === comparisonId) setComparisonId(selected.id);
  };
  if (!agent.snapshots.length) return <Typography.Text>No snapshots have been registered.</Typography.Text>;
  return (
    <RegistryDetailLayout
      list={
        <>
          <SegmentedControlGroup
            name="agent-snapshot-view"
            value={timeline ? 'timeline' : comparison ? 'compare' : 'preview'}
            componentId="mlflow.agent_registry.snapshot.view"
          >
            <SegmentedControlButton
              value="preview"
              onClick={() => {
                setComparisonId(null);
                setView('preview');
              }}
            >
              <span css={{ display: 'flex', alignItems: 'center', gap: theme.spacing.xs }}>
                <ZoomMarqueeSelection />
                Preview
              </span>
            </SegmentedControlButton>
            <SegmentedControlButton
              value="compare"
              disabled={visibleSnapshots.length < 2}
              onClick={() => {
                setView('compare');
                setComparisonId(visibleSnapshots.find((snapshot) => snapshot.id !== selected?.id)?.id ?? null);
              }}
            >
              <span css={{ display: 'flex', alignItems: 'center', gap: theme.spacing.xs }}>
                <ColumnsIcon />
                Compare
              </span>
            </SegmentedControlButton>
            <SegmentedControlButton
              value="timeline"
              onClick={() => {
                setComparisonId(null);
                setView('timeline');
              }}
            >
              Timeline
            </SegmentedControlButton>
          </SegmentedControlGroup>
          <TableFilterInput
            componentId="mlflow.agent_registry.snapshots.search"
            placeholder="Search snapshots"
            ignoreFilterMediaSizing
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setComparisonId(null);
            }}
            css={{ width: '100%' }}
          />
          <div css={{ display: 'flex', flexWrap: 'wrap', gap: theme.spacing.sm }}>
            <EvaluationFilter
              componentId="mlflow.agent_registry.snapshots.versions"
              label="Publisher versions"
              allLabel="All versions"
              options={Array.from(new Set(agent.snapshots.map((snapshot) => snapshot.publisherVersion))).map(
                (version) => ({
                  value: version,
                  label: `Version ${version}`,
                }),
              )}
              selected={versions}
              onChange={(values) => {
                setVersions(values);
                setComparisonId(null);
              }}
            />
            <EvaluationFilter
              componentId="mlflow.agent_registry.snapshots.harnesses"
              label="Harnesses"
              allLabel="All harnesses"
              options={Array.from(new Set(agent.snapshots.map((snapshot) => snapshot.harness))).map((harness) => ({
                value: harness,
                label: harness,
              }))}
              selected={harnesses}
              onChange={(values) => {
                setHarnesses(values);
                setComparisonId(null);
              }}
            />
          </div>
          <RegistryVersionList
            versions={visibleSnapshots}
            getVersionKey={(snapshot) => String(snapshot.id)}
            selectedKey={selected ? String(selected.id) : undefined}
            onSelect={(id) => selectSnapshot(Number(id))}
            header="Snapshots"
            componentId="mlflow.agent_registry.snapshots"
            emptyState={
              <div css={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 120 }}>
                <Typography.Text color="secondary">No matching snapshots.</Typography.Text>
              </div>
            }
            renderVersion={(snapshot) => {
              const changes = getSnapshotAssetChanges(agent, snapshot);
              return (
                <div css={{ display: 'flex', flexDirection: 'column', gap: theme.spacing.xs, minWidth: 0 }}>
                  <Typography.Text bold>Snapshot #{snapshot.id}</Typography.Text>
                  <Typography.Text size="sm" color="secondary">
                    Version {snapshot.publisherVersion}
                  </Typography.Text>
                  {changes.length > 0 && (
                    <Typography.Text size="sm" color="secondary">
                      {changes.map((change) => `${change.asset.name} ${change.change}`).join(', ')}
                    </Typography.Text>
                  )}
                  <Typography.Text size="sm" color="secondary">
                    {formatDate(snapshot.createdAt)}
                  </Typography.Text>
                </div>
              );
            }}
            compareMode={
              comparison && selected
                ? {
                    comparedKey: String(comparison.id),
                    renderControls: (id, { isSelected, isCompared }) => (
                      <MCPServerVersionDiffSelectorButton
                        isSelectedBaseline={isSelected}
                        isSelectedCompared={isCompared}
                        onSelectBaseline={() => selectSnapshot(Number(id))}
                        onSelectCompared={() =>
                          Number(id) === selected.id ? selectSnapshot(comparison.id) : setComparisonId(Number(id))
                        }
                      />
                    ),
                  }
                : undefined
            }
          />
        </>
      }
    >
      {timeline ? (
        <SnapshotTimeline agent={agent} snapshotIds={visibleSnapshots.map((item) => item.id)} />
      ) : !selected ? (
        <div css={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: 240 }}>
          <Typography.Text color="secondary">No snapshots match these filters.</Typography.Text>
        </div>
      ) : comparison ? (
        <SnapshotCompare
          baseline={selected}
          candidate={comparison}
          onSwitchSides={() => selectSnapshot(comparison.id)}
          agentId={agent.id}
          environment={searchParams.get('environment') ?? 'all'}
        />
      ) : (
        <SnapshotDetails snapshot={selected} agent={agent} />
      )}
    </RegistryDetailLayout>
  );
};

const EndpointDetail = ({ endpoint, agentId }: { endpoint: AccessEndpoint; agentId: string }) => {
  const { theme } = useDesignSystemTheme();
  const [view, setView] = useState('Connect');
  const isSyncStale =
    endpoint.protocol === 'A2A' &&
    endpoint.lastSynced &&
    Date.now() - Date.parse(endpoint.lastSynced) > 24 * 60 * 60 * 1000;
  return (
    <div css={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: theme.spacing.lg }}>
      <Typography.Title level={3} withoutMargins>
        {endpoint.name}
      </Typography.Title>
      <Panel title="Details">
        <div
          css={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))', gap: theme.spacing.md }}
        >
          <div>
            <Typography.Text color="secondary">Protocol</Typography.Text>
            <div>
              {endpoint.protocol} · {endpoint.transport}
            </div>
          </div>
          <div>
            <Typography.Text color="secondary">Advertised version</Typography.Text>
            <div>{endpoint.advertisedVersion}</div>
          </div>
          <div>
            <Typography.Text color="secondary">Sync status</Typography.Text>
            <div>{endpoint.syncStatus}</div>
          </div>
          <div>
            <div css={{ display: 'flex', alignItems: 'center', gap: theme.spacing.xs }}>
              <Typography.Text color="secondary">Last successful sync</Typography.Text>
              {isSyncStale && (
                <Tooltip
                  componentId="mlflow.agent_registry.endpoint.stale_sync"
                  delayDuration={0}
                  side="top"
                  content={`Last successful sync was more than 24 hours ago.${endpoint.syncStatus === 'failed' ? ' The latest sync failed; the cached A2A card remains available.' : ''}`}
                >
                  <Button
                    componentId="mlflow.agent_registry.endpoint.stale_sync_info"
                    type="tertiary"
                    size="small"
                    aria-label="Sync is more than 24 hours old"
                    icon={<WarningIcon css={{ color: theme.colors.textValidationWarning }} />}
                  />
                </Tooltip>
              )}
            </div>
            <div>{endpoint.lastSynced ? formatDate(endpoint.lastSynced) : 'Not synced'}</div>
          </div>
        </div>
      </Panel>
      <Tabs.Root componentId="mlflow.agent_registry.endpoint.tabs" value={view} onValueChange={setView} valueHasNoPii>
        <Tabs.List scrollAreaViewportCss={{ backgroundImage: 'none' }}>
          {['Connect', 'Capabilities', 'A2A card'].map((tab) => (
            <Tabs.Trigger
              key={tab}
              value={tab}
              css={{
                height: theme.general.heightSm,
                '&[data-state="active"]': {
                  boxShadow: 'none',
                  borderBottom: `${theme.spacing.xs}px solid ${theme.colors.actionPrimaryBackgroundDefault}`,
                  paddingBottom: theme.spacing.xs,
                },
              }}
            >
              {tab}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        <Tabs.Content value="Capabilities">
          <AgentCapabilities endpoint={endpoint} />
        </Tabs.Content>
        <Tabs.Content value="A2A card">
          <Panel title="Last synced A2A card">
            {endpoint.card ? (
              <>
                <Typography.Text size="sm" color="secondary" css={{ display: 'block', marginBottom: theme.spacing.md }}>
                  Cached copy · last synced {endpoint.lastSynced ? formatDate(endpoint.lastSynced) : 'unknown'}
                </Typography.Text>
                <AgentCardView card={endpoint.card} />
              </>
            ) : (
              <Typography.Text color="secondary">No A2A card is available for this endpoint.</Typography.Text>
            )}
          </Panel>
        </Tabs.Content>
        <Tabs.Content value="Connect">
          <Panel title="Connect to this endpoint">
            <Typography.Text>URL</Typography.Text>
            <div css={{ display: 'flex', alignItems: 'center', gap: theme.spacing.xs, flexWrap: 'wrap' }}>
              <code css={{ overflowWrap: 'anywhere' }}>{endpoint.url}</code>
              <CopyButton
                componentId="mlflow.agent_registry.endpoint.copy_url"
                copyText={endpoint.url}
                showLabel={false}
                type="tertiary"
                size="small"
                icon={<CopyIcon />}
                aria-label="Copy endpoint URL"
                title="Copy endpoint URL"
              />
            </div>
            <div css={{ marginTop: theme.spacing.md }}>
              <AgentEndpointConnection endpoint={endpoint} agentId={agentId} />
            </div>
          </Panel>
        </Tabs.Content>
      </Tabs.Root>
    </div>
  );
};

const EndpointsSection = ({ agent }: { agent: Agent }) => {
  const { theme } = useDesignSystemTheme();
  const [params, setParams] = useSearchParams();
  const selected = agent.endpoints.find((endpoint) => endpoint.name === params.get('endpoint')) ?? agent.endpoints[0];
  return (
    <div css={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, gap: theme.spacing.md }}>
      {selected && (
        <SimpleSelect
          id="agent-access-endpoint"
          componentId="mlflow.agent_registry.endpoints.select"
          aria-label="Access endpoint"
          value={selected.name}
          css={{ width: 360, maxWidth: '100%' }}
          onChange={(event) => {
            const next = new URLSearchParams(params);
            next.set('endpoint', event.target.value);
            setParams(next);
          }}
        >
          {agent.endpoints.map((endpoint) => (
            <SimpleSelectOption key={endpoint.name} value={endpoint.name}>
              {endpoint.name} · {endpoint.protocol}
            </SimpleSelectOption>
          ))}
        </SimpleSelect>
      )}
      <div css={{ flex: 1, minHeight: 0, overflowY: 'auto' }}>
        {selected ? (
          <EndpointDetail key={selected.name} endpoint={selected} agentId={agent.id} />
        ) : (
          <Typography.Text color="secondary">
            This agent has no access endpoint. Its snapshots and traces remain available.
          </Typography.Text>
        )}
      </div>
    </div>
  );
};

const RelatedAgentsSection = ({ agent, environment }: { agent: Agent; environment: string }) => {
  const { theme } = useDesignSystemTheme();
  const [search, setSearch] = useState('');
  const rows = Array.from(new Set(agent.related.map((related) => related.agentId)))
    .map((agentId) => ({
      agentId,
      agent: getAgent(agentId),
      relationships: agent.related.filter((related) => related.agentId === agentId),
    }))
    .filter(
      (row) =>
        row.relationships.length > 0 &&
        `${row.agent?.name ?? row.agentId} ${row.agent?.description ?? ''} ${row.agent?.owner ?? ''}`
          .toLowerCase()
          .includes(search.toLowerCase()),
    );

  return (
    <div css={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, gap: theme.spacing.md }}>
      <TableFilterLayout>
        <TableFilterInput
          componentId="mlflow.agent_registry.related.search"
          placeholder="Search related agents"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          css={{ width: 270 }}
        />
      </TableFilterLayout>
      <Table
        scrollable
        css={{
          '& [role=row]': { minWidth: 1000 },
          '& [role=columnheader]:nth-of-type(2), & [role=cell]:nth-of-type(2)': { flex: 1.6 },
          '& [role=columnheader]:nth-of-type(4), & [role=cell]:nth-of-type(4)': { flex: 1.6 },
          '& [role=columnheader]:last-of-type, & [role=cell]:last-of-type': { flex: 1.6 },
        }}
        empty={
          rows.length === 0 ? (
            <div
              css={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 240, width: '100%' }}
            >
              <Typography.Text color="secondary">
                {agent.related.length
                  ? 'No related agents match these filters.'
                  : 'No related agents declared or observed.'}
              </Typography.Text>
            </div>
          ) : undefined
        }
      >
        <TableRow isHeader>
          {['Name', 'Description', 'Owner', 'Relationship', 'Access endpoint', 'Evidence'].map((header) => (
            <TableHeader key={header} componentId="mlflow.agent_registry.related.header">
              {header}
            </TableHeader>
          ))}
        </TableRow>
        {rows.map((row) => (
          <TableRow key={row.agentId} css={{ minHeight: theme.general.buttonHeight }}>
            <TableCell css={{ alignItems: 'center' }}>
              <Link componentId="mlflow.agent_registry.related.agent" to={agentPath(row.agentId, 'dashboard')}>
                {row.agent?.name ?? row.agentId}
              </Link>
            </TableCell>
            <TableCell css={{ alignItems: 'center' }}>
              <Typography.Text
                ellipsis
                title={row.agent?.description}
                css={{ display: 'block', width: '100%', minWidth: 0 }}
              >
                {row.agent?.description || '—'}
              </Typography.Text>
            </TableCell>
            <TableCell css={{ alignItems: 'center' }}>{row.agent?.owner ?? '—'}</TableCell>
            <TableCell css={{ alignItems: 'center' }}>
              <div css={{ display: 'flex', flexDirection: 'column', gap: theme.spacing.xs }}>
                {row.relationships.map((related) => (
                  <Typography.Text key={related.relationship}>{related.relationship}</Typography.Text>
                ))}
              </div>
            </TableCell>
            <TableCell css={{ alignItems: 'center' }}>
              {row.agent?.endpoints.length ? (
                <Link componentId="mlflow.agent_registry.related.endpoints" to={agentPath(row.agentId, 'endpoints')}>
                  <Tag componentId="mlflow.agent_registry.related.protocol" color="indigo">
                    {row.agent.endpoints[0].protocol}
                  </Tag>{' '}
                  {row.agent.endpoints.length} endpoint{row.agent.endpoints.length === 1 ? '' : 's'}
                </Link>
              ) : (
                '—'
              )}
            </TableCell>
            <TableCell css={{ alignItems: 'center' }}>
              <div css={{ display: 'flex', flexDirection: 'column', gap: theme.spacing.xs }}>
                {row.relationships.map((related) => (
                  <div key={related.relationship} title={related.evidence}>
                    {related.relationship === 'Observed outbound call' ? (
                      <Link
                        componentId="mlflow.agent_registry.related.traces"
                        to={`${agentPath(agent.id, 'traces', environment)}&relatedAgent=${encodeURIComponent(row.agentId)}`}
                      >
                        View supporting traces
                      </Link>
                    ) : (
                      <Typography.Text color="secondary">{related.evidence}</Typography.Text>
                    )}
                  </div>
                ))}
              </div>
            </TableCell>
          </TableRow>
        ))}
      </Table>
    </div>
  );
};

const AgentDetailPage = () => {
  const { agentId, section = 'overview' } = useParams<{ agentId: string; section?: string }>();
  const agent = getAgent(agentId);
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const environment = searchParams.get('environment') ?? 'all';
  const relatedAgentId = searchParams.get('relatedAgent');
  const { theme } = useDesignSystemTheme();
  const setEnvironment = (value: string) => {
    const next = new URLSearchParams(searchParams);
    next.set('environment', value);
    setSearchParams(next);
  };
  useEffect(() => {
    if (section === 'dashboard' && searchParams.get('dashboardTab') === 'timeline' && agent) {
      const next = new URLSearchParams(searchParams);
      next.delete('dashboardTab');
      next.set('view', 'timeline');
      navigate(`${agentPath(agent.id, 'snapshots')}?${next}`, { replace: true });
    }
    if (section === 'sessions' && agent) {
      const next = new URLSearchParams(searchParams);
      next.set('groupBy', 'session');
      navigate(`${agentPath(agent.id, 'traces')}?${next.toString()}`, { replace: true });
    }
  }, [agent, navigate, searchParams, section]);
  if (!agent) {
    return (
      <PageWrapper>
        <Typography.Title level={2}>Agent not found</Typography.Title>
        <Link componentId="mlflow.agent_registry.missing.back" to="/agents">
          Back to agents
        </Link>
      </PageWrapper>
    );
  }
  if (section === 'sessions') return null;
  const isAssetSection = assetSections.includes(section) || section === 'related';
  const assetTab =
    section === 'assets' ? (searchParams.get('type') ?? searchParams.get('assetTab') ?? 'assets') : section;
  return (
    <div
      css={{
        display: 'flex',
        flexDirection: 'column',
        flex: 1,
        height: '100%',
        padding: theme.spacing.md,
        minHeight: 0,
      }}
    >
      <div css={{ display: 'flex', flex: 1, minHeight: 0, minWidth: 0 }}>
        <ExperimentPageMainContent
          breadcrumbs={
            <Breadcrumb includeTrailingCaret={false}>
              <Breadcrumb.Item>
                <Link componentId="mlflow.agent_registry.detail.back" to="/agents">
                  Agents
                </Link>
              </Breadcrumb.Item>
              <Breadcrumb.Item>
                <Link componentId="mlflow.agent_registry.detail.agent" to={agentPath(agent.id)}>
                  {agent.name}
                </Link>
              </Breadcrumb.Item>
              <Breadcrumb.Item>{getAgentSectionLabel(section)}</Breadcrumb.Item>
            </Breadcrumb>
          }
          headerDescription={
            <Typography.Text color="secondary">
              {agent.description} · Owner: {agent.owner}
            </Typography.Text>
          }
        >
          <div
            css={{
              flex: 1,
              minHeight: 0,
              display: section === 'overview' ? 'flex' : undefined,
              flexDirection: 'column',
              overflowY: ['overview', 'snapshots', 'endpoints'].includes(section) ? 'hidden' : 'auto',
              paddingRight: theme.spacing.sm,
            }}
          >
            {section === 'overview' && <AgentOverview agent={agent} />}
            {section === 'dashboard' && (
              <AgentDashboard agent={agent} environment={environment} onEnvironmentChange={setEnvironment} />
            )}
            {section === 'snapshots' && <SnapshotsSection key={agent.id} agent={agent} />}
            {section === 'traces' &&
              (agent.traces.length ? (
                <AgentLiveTracesPage
                  agentId={agent.id}
                  environment={environment}
                  onEnvironmentChange={setEnvironment}
                  relatedAgentId={relatedAgentId}
                />
              ) : (
                <div
                  css={{
                    display: 'flex',
                    flexDirection: 'column',
                    gap: theme.spacing.lg,
                    maxWidth: 860,
                    margin: '0 auto',
                  }}
                >
                  <Typography.Title level={2}>Start tracing your agent</Typography.Title>
                  <AgentTracingSetup agentId={agent.id} />
                </div>
              ))}
            {section === 'evaluation-runs' && (
              <AgentEvaluationRunsPage agent={agent} environment={environment} onEnvironmentChange={setEnvironment} />
            )}
            {isAssetSection && (
              <div css={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: theme.spacing.md, minWidth: 0 }}>
                <SimpleSelect
                  componentId="mlflow.agent_registry.assets.type"
                  id="mlflow.agent_registry.assets.type"
                  aria-label="Type"
                  value={assetTab}
                  css={{ width: 220, maxWidth: '100%' }}
                  onChange={(event) =>
                    navigate(`${agentPath(agent.id, 'assets', environment)}&type=${event.target.value}`)
                  }
                >
                  <SimpleSelectOption value="assets">All asset types</SimpleSelectOption>
                  <SimpleSelectOption value="prompts">Prompts</SimpleSelectOption>
                  <SimpleSelectOption value="tools">Tools</SimpleSelectOption>
                  <SimpleSelectOption value="skills">Skills</SimpleSelectOption>
                  <SimpleSelectOption value="plugins">Agent plugins</SimpleSelectOption>
                  <SimpleSelectOption value="mcp-servers">MCP servers</SimpleSelectOption>
                  <SimpleSelectOption value="models">Models</SimpleSelectOption>
                  <SimpleSelectOption value="related">Related agents</SimpleSelectOption>
                </SimpleSelect>
                {assetTab === 'related' ? (
                  <RelatedAgentsSection key={agent.id} agent={agent} environment={environment} />
                ) : (
                  <AgentAssetsTable agent={agent} section={assetTab} />
                )}
                {assetTab === 'tools' && (
                  <Panel title="Tools observed in traces">
                    {agent.traces.some((trace) => trace.observedTools?.length) ? (
                      <div css={{ display: 'grid', gap: theme.spacing.sm }}>
                        {Array.from(new Set(agent.traces.flatMap((trace) => trace.observedTools ?? []))).map((tool) => {
                          const sourceTrace = agent.traces.find((trace) => trace.observedTools?.includes(tool));
                          return (
                            <div key={tool}>
                              <Typography.Text>{tool}</Typography.Text>{' '}
                              {sourceTrace && (
                                <Link
                                  componentId="mlflow.agent_registry.asset.observed_tool_trace"
                                  to={`${agentPath(agent.id, 'traces', sourceTrace.environment)}${sourceTrace.snapshotId ? `&snapshot=${sourceTrace.snapshotId}` : ''}`}
                                >
                                  View supporting traces
                                </Link>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    ) : (
                      <Typography.Text color="secondary">No tool calls observed in sample traces.</Typography.Text>
                    )}
                  </Panel>
                )}
              </div>
            )}
            {section === 'endpoints' && <EndpointsSection key={agent.id} agent={agent} />}
          </div>
        </ExperimentPageMainContent>
      </div>
    </div>
  );
};

export default AgentDetailPage;
