import { useMemo, useState } from 'react';
import {
  DialogCombobox,
  DialogComboboxContent,
  DialogComboboxOptionList,
  DialogComboboxOptionListCheckboxItem,
  DialogComboboxOptionListSearch,
  DialogComboboxTrigger,
  PuzzleIcon,
  Table,
  TableCell,
  TableFilterInput,
  TableFilterLayout,
  TableHeader,
  TableRow,
  TextBoxIcon,
  Typography,
  useDesignSystemTheme,
} from '@databricks/design-system';

import { Link } from '../common/utils/RoutingUtils';
import { SkillIcon } from '../skill-registry/components/SkillIcon';
import { MCPServerIcon } from '../mcp-registry/components/MCPServerIcon';
import { agentPath, formatDate } from './components';
import type { Agent, AgentAsset, AgentSnapshot, AssetKind } from './mockData';
import { TimeSelector, type AgentTimeRange } from './AgentActivityPages';

type AssetUsage = {
  asset: AgentAsset;
  snapshots: AgentSnapshot[];
};

const assetSections = {
  assets: undefined,
  prompts: 'prompt',
  tools: 'tool',
  skills: 'skill',
  plugins: 'plugin',
  'mcp-servers': 'mcp',
  models: 'model',
} satisfies Record<string, AssetKind | undefined>;

const assetKindLabels: Record<AssetKind, string> = {
  prompt: 'Prompt',
  skill: 'Skill',
  plugin: 'Agent plugin',
  mcp: 'MCP server',
  model: 'Model',
  tool: 'Tool',
};

const AssetIcon = ({ kind, name }: { kind: AssetKind; name: string }) => {
  if (kind === 'prompt') return <TextBoxIcon />;
  if (kind === 'mcp') return <MCPServerIcon name={name} />;
  if (kind === 'skill' || kind === 'plugin') return <SkillIcon name={name} />;
  return <PuzzleIcon />;
};

const UsageFilter = ({
  componentId,
  label,
  options,
  selected,
  onToggle,
}: {
  componentId: string;
  label: string;
  options: { value: string; label: string }[];
  selected: string[];
  onToggle: (value: string) => void;
}) => (
  <DialogCombobox
    componentId={componentId}
    label={label}
    multiSelect
    value={options.filter((option) => selected.includes(option.value)).map((option) => option.label)}
  >
    <DialogComboboxTrigger
      aria-label={label}
      data-testid={componentId}
      placeholder={`All ${label.toLowerCase()}`}
      allowClear={false}
    />
    <DialogComboboxContent maxHeight={300}>
      <DialogComboboxOptionList>
        <DialogComboboxOptionListSearch>
          {options.map((option) => (
            <DialogComboboxOptionListCheckboxItem
              key={option.value}
              value={option.label}
              checked={selected.includes(option.value)}
              onChange={() => onToggle(option.value)}
            >
              {option.label}
            </DialogComboboxOptionListCheckboxItem>
          ))}
        </DialogComboboxOptionListSearch>
      </DialogComboboxOptionList>
    </DialogComboboxContent>
  </DialogCombobox>
);

const getAssetUsage = (agent: Agent): AssetUsage[] => {
  const byAsset = new Map<string, AssetUsage>();
  for (const snapshot of agent.snapshots) {
    for (const asset of snapshot.assets) {
      if (asset.kind === 'plugin') continue;
      const key = `${asset.kind}:${asset.name}:${asset.version}`;
      let usage = byAsset.get(key);
      if (!usage) {
        usage = { asset, snapshots: [] };
        byAsset.set(key, usage);
      }
      usage.snapshots.push(snapshot);
    }
  }
  for (const usage of byAsset.values()) {
    usage.snapshots.sort((left, right) => right.createdAt.localeCompare(left.createdAt));
  }
  return Array.from(byAsset.values()).sort(
    (left, right) =>
      right.snapshots[0].createdAt.localeCompare(left.snapshots[0].createdAt) ||
      left.asset.name.localeCompare(right.asset.name),
  );
};

export const AgentAssetsTable = ({
  agent,
  section,
  snapshotId,
}: {
  agent: Agent;
  section: string;
  snapshotId?: number;
}) => {
  const { theme } = useDesignSystemTheme();
  const [search, setSearch] = useState('');
  const [snapshotIds, setSnapshotIds] = useState<string[]>([]);
  const [agentVersions, setAgentVersions] = useState<string[]>([]);
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc');
  const [timeRange, setTimeRange] = useState<AgentTimeRange>('Last 30 days');
  const now = Date.now();
  const cutoff = now - (timeRange === 'Last 7 days' ? 7 : 30) * 24 * 60 * 60 * 1000;
  const rows = useMemo(() => getAssetUsage(agent), [agent]);
  const selectedKind = assetSections[section as keyof typeof assetSections];
  const versions = Array.from(new Set(agent.snapshots.map((snapshot) => snapshot.publisherVersion)));
  const visibleRows = rows
    .map((row) => ({
      ...row,
      snapshots: row.snapshots.filter(
        (snapshot) =>
          (snapshotId === undefined || snapshot.id === snapshotId) &&
          (snapshotIds.length === 0 || snapshotIds.includes(String(snapshot.id))) &&
          (agentVersions.length === 0 || agentVersions.includes(snapshot.publisherVersion)),
      ),
    }))
    .filter((row) => {
      if (selectedKind && row.asset.kind !== selectedKind) return false;
      if (
        search &&
        !`${row.asset.name} ${row.asset.kind} ${row.asset.version}`.toLowerCase().includes(search.toLowerCase())
      ) {
        return false;
      }
      if (row.snapshots.length === 0) return false;
      if (snapshotId !== undefined || timeRange === 'All time') return true;
      const lastRecorded = Date.parse(row.snapshots[0].createdAt);
      return lastRecorded >= cutoff && lastRecorded <= now;
    })
    .sort(
      (left, right) =>
        (sortDirection === 'desc' ? -1 : 1) * left.snapshots[0].createdAt.localeCompare(right.snapshots[0].createdAt) ||
        left.asset.name.localeCompare(right.asset.name),
    );
  const toggle = (current: string[], value: string, update: (values: string[]) => void) =>
    update(current.includes(value) ? current.filter((item) => item !== value) : [...current, value]);

  return (
    <div css={{ display: 'flex', flexDirection: 'column', gap: theme.spacing.sm, minWidth: 0 }}>
      <TableFilterLayout css={{ width: '100%', justifyContent: 'flex-start', flexWrap: 'wrap', gap: theme.spacing.sm }}>
        {snapshotId === undefined && <TimeSelector value={timeRange} onChange={setTimeRange} />}
        <TableFilterInput
          componentId="mlflow.agent_registry.assets.search"
          placeholder={`Search ${section === 'assets' ? 'AI assets' : section}`}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          css={{ width: 260 }}
        />
        {snapshotId === undefined && (
          <div css={{ display: 'flex', gap: theme.spacing.sm, flexWrap: 'wrap' }}>
            <UsageFilter
              componentId="mlflow.agent_registry.assets.filter.snapshots"
              label="Snapshots"
              options={agent.snapshots.map((snapshot) => ({
                value: String(snapshot.id),
                label: `Snapshot #${snapshot.id}`,
              }))}
              selected={snapshotIds}
              onToggle={(value) => toggle(snapshotIds, value, setSnapshotIds)}
            />
            <UsageFilter
              componentId="mlflow.agent_registry.assets.filter.agent_versions"
              label="Agent versions"
              options={versions.map((version) => ({ value: version, label: `Version ${version}` }))}
              selected={agentVersions}
              onToggle={(value) => toggle(agentVersions, value, setAgentVersions)}
            />
          </div>
        )}
      </TableFilterLayout>
      <Table
        scrollable
        css={{
          minHeight: visibleRows.length ? undefined : 240,
          '& [role=row]': { minWidth: snapshotId !== undefined ? 480 : section === 'assets' ? 680 : 600 },
          '& [role=columnheader]:first-of-type, & [role=cell]:first-of-type': { flex: 1.8 },
          '& [role=columnheader]:last-of-type, & [role=cell]:last-of-type': { flex: 1.5 },
        }}
        empty={
          visibleRows.length === 0 ? (
            <div
              css={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 240, width: '100%' }}
            >
              <Typography.Text color="secondary">
                {section === 'plugins' ? 'No agent plugins to show yet.' : 'No AI assets match these filters.'}
              </Typography.Text>
            </div>
          ) : undefined
        }
      >
        <TableRow isHeader>
          <TableHeader componentId="mlflow.agent_registry.assets.header">Name</TableHeader>
          {section === 'assets' && <TableHeader componentId="mlflow.agent_registry.assets.header">Type</TableHeader>}
          <TableHeader componentId="mlflow.agent_registry.assets.header" multiline>
            Pinned version
          </TableHeader>
          {snapshotId === undefined && (
            <>
              <TableHeader componentId="mlflow.agent_registry.assets.header" multiline>
                Used in snapshots
              </TableHeader>
              <TableHeader componentId="mlflow.agent_registry.assets.header" multiline>
                Agent versions
              </TableHeader>
            </>
          )}
          <TableHeader
            componentId="mlflow.agent_registry.assets.header"
            sortable
            sortDirection={sortDirection}
            onToggleSort={() => setSortDirection(sortDirection === 'desc' ? 'asc' : 'desc')}
          >
            Last recorded
          </TableHeader>
        </TableRow>
        {visibleRows.map((row) => {
          const matchingSnapshots = row.snapshots;
          const latestSnapshot = matchingSnapshots[0];
          return (
            <TableRow
              key={`${row.asset.kind}:${row.asset.name}:${row.asset.version}`}
              css={{ height: theme.general.buttonHeight }}
            >
              <TableCell>
                <span css={{ display: 'flex', gap: theme.spacing.sm, alignItems: 'center', minWidth: 0 }}>
                  <span css={{ display: 'flex', flexShrink: 0 }}>
                    <AssetIcon kind={row.asset.kind} name={row.asset.name} />
                  </span>
                  {row.asset.href ? (
                    <Link
                      componentId="mlflow.agent_registry.assets.name"
                      to={row.asset.href}
                      title={row.asset.name}
                      css={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                    >
                      {row.asset.name}
                    </Link>
                  ) : (
                    <Typography.Text ellipsis title={row.asset.name}>
                      {row.asset.name}
                    </Typography.Text>
                  )}
                </span>
              </TableCell>
              {section === 'assets' && <TableCell>{assetKindLabels[row.asset.kind]}</TableCell>}
              <TableCell>
                {row.asset.version === 'provider model' ? 'Provider model' : `v${row.asset.version}`}
              </TableCell>
              {snapshotId === undefined && (
                <>
                  <TableCell>
                    <span css={{ display: 'flex', gap: theme.spacing.xs, flexWrap: 'wrap' }}>
                      {matchingSnapshots.map((snapshot) => (
                        <Link
                          key={snapshot.id}
                          componentId="mlflow.agent_registry.assets.snapshot_link"
                          to={`${agentPath(agent.id, 'snapshots')}?snapshot=${snapshot.id}`}
                        >
                          #{snapshot.id}
                        </Link>
                      ))}
                    </span>
                  </TableCell>
                  <TableCell>
                    {Array.from(new Set(matchingSnapshots.map((snapshot) => snapshot.publisherVersion))).join(', ')}
                  </TableCell>
                </>
              )}
              <TableCell>{formatDate(latestSnapshot.createdAt)}</TableCell>
            </TableRow>
          );
        })}
      </Table>
    </div>
  );
};
