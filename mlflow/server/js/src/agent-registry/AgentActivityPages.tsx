import { useMemo, useState } from 'react';
import {
  Button,
  Alert,
  ChevronDownIcon,
  ClockIcon,
  DialogCombobox,
  DialogComboboxContent,
  DialogComboboxCustomButtonTriggerWrapper,
  DialogComboboxOptionList,
  DialogComboboxOptionListCheckboxItem,
  DialogComboboxOptionListSearch,
  DialogComboboxOptionListSelectItem,
  DialogComboboxTrigger,
  Table,
  TableCell,
  TableHeader,
  TableRow,
  Tag,
  Typography,
  useDesignSystemTheme,
} from '@databricks/design-system';

import { agentPath, EnvironmentFilter, EnvironmentTag, formatDate, getSelectedEnvironments } from './components';
import { Link, useSearchParams } from '../common/utils/RoutingUtils';
import type { Agent } from './mockData';
import ExperimentEvaluationRunsPage from '../experiment-tracking/pages/experiment-evaluation-runs/ExperimentEvaluationRunsPage';
import type { RunEntity } from '../experiment-tracking/types';
import { useAgentPreviewExperiment } from './useAgentPreviewExperiment';

const timeRanges = ['Last 7 days', 'Last 30 days', 'All time'] as const;
export type AgentTimeRange = (typeof timeRanges)[number];

export const TimeSelector = ({
  value,
  onChange,
}: { value?: AgentTimeRange; onChange?: (value: AgentTimeRange) => void } = {}) => {
  const [range, setRange] = useState<AgentTimeRange>('Last 7 days');
  const selectedRange = value ?? range;
  return (
    <DialogCombobox componentId="mlflow.agent_registry.time_selector" label="Time" value={[selectedRange]}>
      <DialogComboboxCustomButtonTriggerWrapper>
        <Button
          componentId="mlflow.agent_registry.time_selector.button"
          icon={<ClockIcon />}
          endIcon={<ChevronDownIcon />}
        >
          {selectedRange}
        </Button>
      </DialogComboboxCustomButtonTriggerWrapper>
      <DialogComboboxContent>
        <DialogComboboxOptionList>
          {timeRanges.map((option) => (
            <DialogComboboxOptionListSelectItem
              key={option}
              value={option}
              title={option}
              checked={selectedRange === option}
              onChange={() => {
                setRange(option);
                onChange?.(option);
              }}
            >
              {option}
            </DialogComboboxOptionListSelectItem>
          ))}
        </DialogComboboxOptionList>
      </DialogComboboxContent>
    </DialogCombobox>
  );
};

export const getSelectedFilterValues = (value: string) =>
  Array.from(new Set(value.split(',').filter((item) => item && item !== 'all')));

export const EvaluationFilter = ({
  componentId,
  label,
  allLabel,
  options,
  selected,
  onChange,
}: {
  componentId: string;
  label: string;
  allLabel: string;
  options: { value: string; label: string }[];
  selected: string[];
  onChange: (values: string[]) => void;
}) => (
  <DialogCombobox
    componentId={componentId}
    label={label}
    multiSelect
    value={options.filter((option) => selected.includes(option.value)).map((option) => option.label)}
  >
    <DialogComboboxTrigger
      aria-label={label}
      placeholder={allLabel}
      allowClear={false}
      css={{ maxWidth: '100%', minWidth: 0, '& button': { maxWidth: '100%', minWidth: 0 } }}
    />
    <DialogComboboxContent maxHeight={300}>
      <DialogComboboxOptionList>
        <DialogComboboxOptionListSearch>
          <DialogComboboxOptionListCheckboxItem
            value={allLabel}
            checked={selected.length === 0}
            onChange={() => onChange([])}
          >
            {allLabel}
          </DialogComboboxOptionListCheckboxItem>
          {options.map((option) => (
            <DialogComboboxOptionListCheckboxItem
              key={option.value}
              value={option.label}
              checked={selected.includes(option.value)}
              onChange={() =>
                onChange(
                  selected.includes(option.value)
                    ? selected.filter((value) => value !== option.value)
                    : [...selected, option.value],
                )
              }
            >
              {option.label}
            </DialogComboboxOptionListCheckboxItem>
          ))}
        </DialogComboboxOptionListSearch>
      </DialogComboboxOptionList>
    </DialogComboboxContent>
  </DialogCombobox>
);

export const AgentEvaluationRunsPage = ({
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
  const selectedSnapshots = getSelectedFilterValues(snapshot);
  const selectedVersions = getSelectedFilterValues(version);
  const selectedEnvironments = getSelectedEnvironments(environment);
  const needsEnvironmentFilter = selectedEnvironments.length > 1 || selectedEnvironments.includes('unassigned');
  const needsSnapshotFilter = selectedSnapshots.length > 1 || selectedSnapshots.includes('none');
  const needsVersionFilter = selectedVersions.length > 1 || selectedVersions.includes('none');
  const getTag = (run: RunEntity, key: string) => run.data.tags?.find((tag) => tag.key === key)?.value;
  const quote = (value: string) => `'${value.replaceAll("'", "''")}'`;
  const baseFilter = [
    `tags.\`mlflow.agent.id\` = ${quote(agent.id)}`,
    selectedSnapshots.length === 1 && !needsSnapshotFilter
      ? `tags.\`mlflow.agent.snapshot_id\` = ${quote(selectedSnapshots[0])}`
      : '',
    selectedVersions.length === 1 && !needsVersionFilter
      ? `tags.\`mlflow.agent.version\` = ${quote(selectedVersions[0])}`
      : '',
    selectedEnvironments.length === 1 && !needsEnvironmentFilter
      ? `tags.\`mlflow.agent.environment\` = ${quote(selectedEnvironments[0])}`
      : '',
  ]
    .filter(Boolean)
    .join(' AND ');
  const runFilter = useMemo(
    () =>
      needsEnvironmentFilter || needsSnapshotFilter || needsVersionFilter
        ? (run: RunEntity) =>
            (!needsEnvironmentFilter ||
              getSelectedEnvironments(environment).includes(getTag(run, 'mlflow.agent.environment') ?? 'unassigned')) &&
            (!needsSnapshotFilter ||
              getSelectedFilterValues(snapshot).includes(getTag(run, 'mlflow.agent.snapshot_id') || 'none')) &&
            (!needsVersionFilter ||
              getSelectedFilterValues(version).includes(getTag(run, 'mlflow.agent.version') || 'none'))
        : undefined,
    [environment, needsEnvironmentFilter, needsSnapshotFilter, needsVersionFilter, snapshot, version],
  );
  const updateFilter = (key: string, values: string[]) => {
    const next = new URLSearchParams(searchParams);
    if (values.length === 0) next.delete(key);
    else next.set(key, values.join(','));
    setSearchParams(next);
  };
  const toolbar = (
    <div css={{ display: 'flex', gap: theme.spacing.sm, flexWrap: 'wrap' }}>
      <EnvironmentFilter value={environment} onChange={onEnvironmentChange} />
      <EvaluationFilter
        componentId="mlflow.agent_registry.evaluations.snapshot"
        label="Snapshots"
        allLabel="All snapshots"
        selected={selectedSnapshots}
        onChange={(values) => updateFilter('snapshot', values)}
        options={[
          { value: 'none', label: 'No snapshot' },
          ...agent.snapshots.map((item) => ({ value: String(item.id), label: `Snapshot #${item.id}` })),
        ]}
      />
      <EvaluationFilter
        componentId="mlflow.agent_registry.evaluations.version"
        label="Publisher versions"
        allLabel="All versions"
        selected={selectedVersions}
        onChange={(values) => updateFilter('version', values)}
        options={[
          { value: 'none', label: 'No version' },
          ...Array.from(new Set(agent.snapshots.map((item) => item.publisherVersion))).map((item) => ({
            value: item,
            label: `Version ${item}`,
          })),
        ]}
      />
    </div>
  );
  if (isLoading) return <Typography.Text color="secondary">Loading evaluation runs...</Typography.Text>;
  if (error || !experimentId) {
    return (
      <Alert
        componentId="mlflow.agent_registry.evaluations.unavailable"
        type="info"
        message="Agent preview evaluation runs are unavailable."
        closable={false}
      />
    );
  }
  return (
    <div css={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <ExperimentEvaluationRunsPage
        experimentIdOverride={experimentId}
        scopeKey={agent.id}
        baseFilter={baseFilter}
        runFilter={runFilter}
        toolbarAddons={toolbar}
        readOnly
        emptyState={
          <div css={{ display: 'flex', flexDirection: 'column', gap: theme.spacing.md, height: '100%' }}>
            <div css={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 240 }}>
              <Typography.Text color="secondary">No evaluation runs match these agent filters.</Typography.Text>
            </div>
          </div>
        }
        renderRunContext={(run) => {
          const snapshotId = getTag(run, 'mlflow.agent.snapshot_id');
          const runSnapshot = agent.snapshots.find((item) => String(item.id) === snapshotId);
          const runVersion = getTag(run, 'mlflow.agent.version') ?? runSnapshot?.publisherVersion;
          const runEnvironment = getTag(run, 'mlflow.agent.environment') ?? 'unassigned';
          return (
            <div
              css={{ display: 'flex', alignItems: 'center', gap: theme.spacing.xs, flexWrap: 'wrap' }}
              onClick={(event) => event.stopPropagation()}
            >
              {snapshotId ? (
                <span title={runSnapshot ? `${runSnapshot.source} · ${runSnapshot.harness}` : undefined}>
                  <Link
                    componentId="mlflow.agent_registry.evaluations.snapshot_link"
                    to={`${agentPath(agent.id, 'snapshots', runEnvironment)}&snapshot=${encodeURIComponent(snapshotId)}`}
                  >
                    Snapshot #{snapshotId}
                  </Link>
                </span>
              ) : (
                <Typography.Text color="secondary" size="sm">
                  No snapshot
                </Typography.Text>
              )}
              {runVersion && (
                <Typography.Text color="secondary" size="sm">
                  v{runVersion}
                </Typography.Text>
              )}
            </div>
          );
        }}
      />
    </div>
  );
};

export const AgentEvaluationRunsTable = ({
  agent,
  evaluations,
  compact = false,
}: {
  agent: Agent;
  evaluations: Agent['evaluations'];
  compact?: boolean;
}) => (
  <Table
    scrollable
    css={
      compact
        ? {
            '& [role=row]': { minWidth: 600 },
            '& [role=cell]:first-of-type, & [role=columnheader]:first-of-type': { flex: 2 },
          }
        : undefined
    }
    empty={!evaluations.length ? <Typography.Text>No evaluation runs match these filters.</Typography.Text> : undefined}
  >
    <TableRow isHeader>
      {[
        'Run name',
        ...(!compact ? ['Status', 'Snapshot', 'Dataset', 'Dataset version'] : []),
        'Quality',
        'Cases',
        'Start time',
      ].map((header) => (
        <TableHeader componentId="mlflow.agent_registry.evaluations.header" key={header}>
          {header}
        </TableHeader>
      ))}
    </TableRow>
    {evaluations.map((evaluation) => (
      <TableRow key={evaluation.id}>
        <TableCell>
          <Typography.Text bold ellipsis title={evaluation.id} css={{ width: '100%', minWidth: 0 }}>
            {evaluation.id}
          </Typography.Text>
        </TableCell>
        {!compact && (
          <>
            <TableCell>
              <Tag componentId="mlflow.agent_registry.evaluations.status" color="lime">
                Finished
              </Tag>
            </TableCell>
            <TableCell>#{evaluation.snapshotId}</TableCell>
            <TableCell>{agent.name} evaluation</TableCell>
            <TableCell>v1</TableCell>
          </>
        )}
        <TableCell>{evaluation.quality}%</TableCell>
        <TableCell>{evaluation.cases}</TableCell>
        <TableCell>{formatDate(evaluation.at)}</TableCell>
      </TableRow>
    ))}
  </Table>
);
