import { useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Drawer,
  SimpleSelect,
  SimpleSelectOption,
  Table,
  TableCell,
  TableHeader,
  TableRow,
  Tabs,
  Typography,
  useDesignSystemTheme,
} from '@databricks/design-system';

import { Link, useSearchParams } from '../common/utils/RoutingUtils';
import { ModelTraceExplorerContextProvider } from '../shared/web-shared/model-trace-explorer/v2/ModelTraceExplorerContext';
import { ModelTraceExplorerPreferencesProvider } from '../shared/web-shared/model-trace-explorer/v2/ModelTraceExplorerPreferencesContext';
import { ModelTraceExplorerViewStateProvider } from '../shared/web-shared/model-trace-explorer/v2/ModelTraceExplorerViewStateContext';
import { ModelTraceExplorerDetailView } from '../shared/web-shared/model-trace-explorer/v2/ModelTraceExplorerDetailView';
import { EvaluationFilter } from './AgentActivityPages';
import { agentPath, formatDate, getSelectedEnvironments, Panel } from './components';
import type { Agent } from './mockData';
import { getSnapshotAssetChanges } from './assetChanges';
import {
  getDependencyEvidence,
  getSnapshotAttribution,
  getTraceInstances,
  getTraceTokens,
  investigationTraces,
  toModelTrace,
} from './investigationData';
import type { InvestigationTrace } from './investigationData';

type Investigation = 'change' | 'dependencies' | 'runtime';
const titles = {
  change: 'Behavior changes',
  dependencies: 'Dependency failures',
  runtime: 'Runtime context',
};

export const InvestigationButton = ({
  mode,
  snapshotId,
  baselineId,
  tool,
  children,
}: {
  mode: Investigation;
  snapshotId?: number;
  baselineId?: number;
  tool?: string;
  children: React.ReactNode;
}) => {
  const [params, setParams] = useSearchParams();
  return (
    <Button
      componentId="mlflow.agent_registry.investigation.open"
      size="small"
      type="tertiary"
      onClick={() => {
        const next = new URLSearchParams(params);
        next.set('investigation', mode);
        if (snapshotId !== undefined) next.set('investigationSnapshot', String(snapshotId));
        else next.delete('investigationSnapshot');
        if (baselineId !== undefined) next.set('investigationBaseline', String(baselineId));
        else next.delete('investigationBaseline');
        if (tool) next.set('investigationTool', tool);
        else next.delete('investigationTool');
        setParams(next);
      }}
    >
      {children}
    </Button>
  );
};

const EmptyEvidence = ({ children }: { children: React.ReactNode }) => (
  <div css={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 160, width: '100%' }}>
    <Typography.Text color="secondary">{children}</Typography.Text>
  </div>
);

const tokens = (trace: InvestigationTrace) =>
  trace.spans.some((span) => span.inputTokens !== undefined) ? getTraceTokens(trace) : 'Not recorded';

const EvidenceTable = ({
  traces,
  selectedId,
  onSelect,
  tool,
}: {
  traces: InvestigationTrace[];
  selectedId?: string;
  onSelect: (trace: InvestigationTrace) => void;
  tool?: string;
}) => (
  <Table
    scrollable
    empty={!traces.length ? <EmptyEvidence>No example traces match these filters.</EmptyEvidence> : undefined}
  >
    <TableRow isHeader>
      {[
        'Example trace',
        'Time',
        'Composition',
        'Instance IDs',
        ...(tool ? ['Tool evidence'] : ['Tokens']),
        'Outcome',
      ].map((header) => (
        <TableHeader key={header} componentId="mlflow.agent_registry.investigation.header">
          {header}
        </TableHeader>
      ))}
    </TableRow>
    {traces.map((trace) => {
      const evidence = tool ? getDependencyEvidence(trace, tool) : undefined;
      return (
        <TableRow key={trace.id} aria-selected={trace.id === selectedId}>
          <TableCell>
            <Button componentId="mlflow.agent_registry.investigation.trace" type="link" onClick={() => onSelect(trace)}>
              {trace.id}
            </Button>
          </TableCell>
          <TableCell>{formatDate(trace.at)}</TableCell>
          <TableCell>{getSnapshotAttribution(trace)}</TableCell>
          <TableCell>{getTraceInstances(trace).join(', ') || 'Not recorded'}</TableCell>
          <TableCell>
            {evidence ? `${evidence.calls.length} calls / ${evidence.failures.length} errors` : tokens(trace)}
          </TableCell>
          <TableCell>{trace.outcome}</TableCell>
        </TableRow>
      );
    })}
  </Table>
);

const TraceEvidence = ({
  trace,
  agent,
  comparison,
  spanId,
  initialTab = 'execution',
}: {
  trace: InvestigationTrace;
  agent: Agent;
  comparison?: InvestigationTrace;
  spanId?: string;
  initialTab?: 'execution' | 'context';
}) => {
  const { theme } = useDesignSystemTheme();
  const modelTrace = useMemo(() => toModelTrace(trace), [trace]);
  const comparisonTrace = useMemo(() => (comparison ? toModelTrace(comparison) : undefined), [comparison]);
  const [tab, setTab] = useState<string>(initialTab);
  const contextRows = (item: InvestigationTrace) => {
    const declaredHarnesses = item.snapshotIds.flatMap((id) => {
      const snapshot = agent.snapshots.find((candidate) => candidate.id === id);
      return snapshot ? [`#${id}: ${snapshot.harness}`] : [];
    });
    return [
      ['Snapshot attribution', getSnapshotAttribution(item)],
      ['Publisher version', item.publisherVersion ?? 'Not recorded'],
      ['Observed instance IDs (any span)', getTraceInstances(item).join(', ') || 'Not recorded'],
      [
        'Observed harness',
        Array.from(new Set([item.harness, ...item.spans.map((span) => span.harness)].filter(Boolean))).join(', ') ||
          'Not recorded',
      ],
      ['Snapshot-declared harness', declaredHarnesses.join('; ') || 'Not available'],
      [
        'Verified available skills',
        item.availableSkills ? item.availableSkills.join(', ') || 'None verified' : 'Not recorded',
      ],
      [
        'Observed skill activations',
        item.activatedSkills ? item.activatedSkills.join(', ') || 'None observed' : 'Not recorded',
      ],
      ['Session', item.session],
      ['Final assessment', item.assessment ?? 'Not assessed'],
    ];
  };
  const rows = contextRows(trace);
  const comparisonRows = comparison ? contextRows(comparison) : undefined;
  return (
    <Panel title={comparison ? `Compare ${comparison.id} with ${trace.id}` : trace.id}>
      <Tabs.Root componentId="mlflow.agent_registry.investigation.evidence_tabs" value={tab} onValueChange={setTab}>
        <Tabs.List>
          <Tabs.Trigger value="execution">Execution evidence</Tabs.Trigger>
          <Tabs.Trigger value="context">Runtime context</Tabs.Trigger>
        </Tabs.List>
        <Tabs.Content value="context">
          <Table>
            <TableRow isHeader>
              <TableHeader componentId="mlflow.agent_registry.investigation.context_header">Field</TableHeader>
              {comparison && (
                <TableHeader componentId="mlflow.agent_registry.investigation.context_header">
                  Baseline evidence
                </TableHeader>
              )}
              <TableHeader componentId="mlflow.agent_registry.investigation.context_header">
                {comparison ? 'Comparison evidence' : 'Recorded evidence'}
              </TableHeader>
            </TableRow>
            {rows.map(([label, value], index) => (
              <TableRow key={label}>
                <TableCell>{label}</TableCell>
                {comparisonRows && <TableCell>{comparisonRows[index][1]}</TableCell>}
                <TableCell>{value}</TableCell>
              </TableRow>
            ))}
          </Table>
          <Typography.Text color="secondary">
            Missing observations do not establish non-use or identical configuration.
          </Typography.Text>
        </Tabs.Content>
        <Tabs.Content value="execution">
          <Typography.Text color="secondary">
            {comparison ? 'Example trace comparison. Span differences are not proof of causality.' : trace.request}
          </Typography.Text>
          <div css={{ height: 520, display: 'flex', marginTop: theme.spacing.sm }}>
            <ModelTraceExplorerPreferencesProvider>
              <ModelTraceExplorerContextProvider isSearchVisible>
                <ModelTraceExplorerViewStateProvider
                  key={`${trace.id}:${comparison?.id ?? ''}:${spanId ?? ''}`}
                  modelTrace={modelTrace}
                  comparisonModelTrace={comparisonTrace}
                  comparisonRole={comparison ? 'candidate' : undefined}
                  selectedSpanIdOnRender={spanId}
                  assessmentsPaneEnabled={false}
                  hideAssessments
                  readOnly
                >
                  <ModelTraceExplorerDetailView modelTraceInfo={modelTrace.info} enableGraphView={false} />
                </ModelTraceExplorerViewStateProvider>
              </ModelTraceExplorerContextProvider>
            </ModelTraceExplorerPreferencesProvider>
          </div>
        </Tabs.Content>
      </Tabs.Root>
    </Panel>
  );
};

const BehaviorChanges = ({ agent, traces }: { agent: Agent; traces: InvestigationTrace[] }) => {
  const { theme } = useDesignSystemTheme();
  const [params] = useSearchParams();
  const [candidateId, setCandidateId] = useState(
    params.get('investigationSnapshot') ?? String(agent.snapshots[0]?.id ?? 'none'),
  );
  const [baselineId, setBaselineId] = useState(
    params.get('investigationBaseline') ??
      String(
        agent.snapshots[agent.snapshots.findIndex((snapshot) => String(snapshot.id) === candidateId) + 1]?.id ?? 'none',
      ),
  );
  const [selectedId, setSelectedId] = useState<string>();
  const [compare, setCompare] = useState(false);
  const baseline = agent.snapshots.find((snapshot) => String(snapshot.id) === baselineId);
  const candidate = agent.snapshots.find((snapshot) => String(snapshot.id) === candidateId);
  const cohort = (id: string) =>
    traces.filter((trace) =>
      id === 'none'
        ? trace.snapshotIds.length === 0
        : trace.snapshotIds.length === 1 && String(trace.snapshotIds[0]) === id,
    );
  const before = cohort(baselineId);
  const after = cohort(candidateId);
  const visible = traces.filter((trace) => before.includes(trace) || after.includes(trace));
  const selected = visible.find((trace) => trace.id === selectedId);
  const matched =
    selected && after.includes(selected) ? before.find((trace) => trace.caseId === selected.caseId) : undefined;
  const changes = candidate && baseline ? getSnapshotAssetChanges(agent, candidate, baseline) : [];
  const changeRows = [
    ...(baseline?.source !== candidate?.source
      ? [['Source', baseline?.source ?? 'Not recorded', candidate?.source ?? 'Not recorded']]
      : []),
    ...(baseline?.harness !== candidate?.harness
      ? [['Harness', baseline?.harness ?? 'Not recorded', candidate?.harness ?? 'Not recorded']]
      : []),
    ...changes.map(({ asset, change, previousVersion }) => [
      `${asset.kind}: ${asset.name}`,
      change === 'added' ? 'Not declared' : (previousVersion ?? asset.version),
      change === 'removed' ? 'Not declared' : asset.version,
    ]),
  ];
  return (
    <div css={{ display: 'grid', gap: theme.spacing.md }}>
      <div css={{ display: 'flex', gap: theme.spacing.sm, flexWrap: 'wrap' }}>
        {[
          { label: 'Baseline', value: baselineId, set: setBaselineId },
          { label: 'Comparison', value: candidateId, set: setCandidateId },
        ].map(({ label, value, set }) => (
          <label key={label}>
            <Typography.Text>{label}</Typography.Text>
            <SimpleSelect
              componentId="mlflow.agent_registry.investigation.snapshot"
              id={`investigation-${label}`}
              aria-label={label}
              value={value}
              onChange={(event) => {
                set(event.target.value);
                setSelectedId(undefined);
                setCompare(false);
              }}
            >
              <SimpleSelectOption value="none">No snapshot</SimpleSelectOption>
              {agent.snapshots.map((snapshot) => (
                <SimpleSelectOption key={snapshot.id} value={String(snapshot.id)}>
                  Snapshot #{snapshot.id}
                </SimpleSelectOption>
              ))}
            </SimpleSelect>
          </label>
        ))}
        <Button
          componentId="mlflow.agent_registry.investigation.swap"
          onClick={() => {
            setBaselineId(candidateId);
            setCandidateId(baselineId);
            setSelectedId(undefined);
            setCompare(false);
          }}
        >
          Switch sides
        </Button>
      </div>
      {(!baseline && baselineId !== 'none') || (!candidate && candidateId !== 'none') ? (
        <Alert
          componentId="mlflow.agent_registry.investigation.invalid_snapshot"
          type="warning"
          closable={false}
          message="A selected snapshot is unavailable. Select a recorded snapshot or No snapshot."
        />
      ) : baselineId === candidateId ? (
        <EmptyEvidence>Select different cohorts to investigate a change.</EmptyEvidence>
      ) : (
        <>
          <Panel title="Recorded composition changes">
            {changeRows.length ? (
              <Table>
                <TableRow isHeader>
                  {['Field', 'Baseline', 'Comparison'].map((label) => (
                    <TableHeader key={label} componentId="mlflow.agent_registry.investigation.change_header">
                      {label}
                    </TableHeader>
                  ))}
                </TableRow>
                {changeRows.map(([label, left, right]) => (
                  <TableRow key={label}>
                    <TableCell>{label}</TableCell>
                    <TableCell>{left}</TableCell>
                    <TableCell>{right}</TableCell>
                  </TableRow>
                ))}
              </Table>
            ) : (
              <Typography.Text color="secondary">
                {baseline && candidate
                  ? 'No recorded asset, source, or harness changes.'
                  : 'Composition is unknown for activity without a snapshot.'}
              </Typography.Text>
            )}
          </Panel>
          <Panel title="Example cohort evidence">
            <Table>
              <TableRow isHeader>
                {['Cohort', 'Example window', 'Traces', 'LLM calls', 'Tool calls', 'Recorded tokens'].map((label) => (
                  <TableHeader key={label} componentId="mlflow.agent_registry.investigation.cohort_header">
                    {label}
                  </TableHeader>
                ))}
              </TableRow>
              {[
                { label: 'Baseline', items: before },
                { label: 'Comparison', items: after },
              ].map(({ label, items }) => {
                const dates = items.map((trace) => trace.at).sort();
                const tokenCoverage = items.filter((trace) =>
                  trace.spans.some((span) => span.inputTokens !== undefined),
                ).length;
                return (
                  <TableRow key={label}>
                    <TableCell>{label}</TableCell>
                    <TableCell>
                      {dates.length
                        ? `${formatDate(dates[0])} - ${formatDate(dates[dates.length - 1])}`
                        : 'No examples'}
                    </TableCell>
                    <TableCell>{items.length}</TableCell>
                    <TableCell>
                      {items.reduce((n, trace) => n + trace.spans.filter((span) => span.type === 'LLM').length, 0)}
                    </TableCell>
                    <TableCell>
                      {items.reduce((n, trace) => n + trace.spans.filter((span) => span.type === 'TOOL').length, 0)}
                    </TableCell>
                    <TableCell>
                      {tokenCoverage
                        ? `${items.reduce((n, trace) => n + getTraceTokens(trace), 0)} (${tokenCoverage}/${items.length} traces)`
                        : 'Not recorded'}
                    </TableCell>
                  </TableRow>
                );
              })}
            </Table>
            <Typography.Text color="secondary">
              Totals describe these fixtures only, not production metrics.{' '}
              {traces.filter((trace) => trace.snapshotIds.length > 1).length} mixed-snapshot example(s) excluded from
              single-snapshot cohorts.
            </Typography.Text>
          </Panel>
          <EvidenceTable
            traces={visible}
            selectedId={selected?.id}
            onSelect={(trace) => {
              setSelectedId(trace.id);
              setCompare(false);
            }}
          />
          {selected && (
            <>
              <div css={{ display: 'flex', alignItems: 'center', gap: theme.spacing.sm }}>
                <Button
                  componentId="mlflow.agent_registry.investigation.compare_trace"
                  disabled={!matched}
                  onClick={() => setCompare(!compare)}
                >
                  {compare ? 'Inspect comparison trace only' : 'Compare matched baseline trace'}
                </Button>
                {!matched && (
                  <Typography.Text color="secondary">
                    Select a comparison trace with a matching baseline case.
                  </Typography.Text>
                )}
                <Link
                  componentId="mlflow.agent_registry.investigation.evaluations"
                  to={`${agentPath(agent.id, 'evaluation-runs', selected.environment)}&snapshot=${selected.snapshotIds[0] ?? 'none'}`}
                >
                  Open evaluation runs
                </Link>
              </div>
              <Typography.Text color="secondary">
                Evaluation runs use existing experiment data, not these example traces.
              </Typography.Text>
              <TraceEvidence
                key={selected.id}
                trace={selected}
                agent={agent}
                comparison={compare ? matched : undefined}
              />
            </>
          )}
        </>
      )}
    </div>
  );
};

const DependencyFailures = ({ agent, traces }: { agent: Agent; traces: InvestigationTrace[] }) => {
  const { theme } = useDesignSystemTheme();
  const [params] = useSearchParams();
  const tools = Array.from(
    new Set(
      traces.flatMap((trace) =>
        trace.spans.flatMap((span) => [...(span.type === 'TOOL' ? [span.name] : []), ...(span.offeredTools ?? [])]),
      ),
    ),
  ).sort();
  const [tool, setTool] = useState(
    params.get('investigationTool') ??
      tools.find((name) => traces.some((trace) => getDependencyEvidence(trace, name).failures.length)) ??
      tools[0] ??
      '',
  );
  const [filter, setFilter] = useState('all');
  const [selection, setSelection] = useState<{ traceId: string; spanId?: string }>();
  const visible = traces.filter((trace) => {
    const evidence = getDependencyEvidence(trace, tool);
    return (
      (evidence.calls.length > 0 || evidence.offers.length > 0) &&
      (filter === 'all' ||
        (filter === 'errors' ? evidence.failures.length > 0 : evidence.failures.length > 0 && trace.outcome === 'OK'))
    );
  });
  const selected = visible.find((trace) => trace.id === selection?.traceId);
  const evidence = selected ? getDependencyEvidence(selected, tool) : undefined;
  return (
    <div css={{ display: 'grid', gap: theme.spacing.md }}>
      <div css={{ display: 'flex', gap: theme.spacing.sm }}>
        <SimpleSelect
          componentId="mlflow.agent_registry.investigation.tool"
          id="investigation-tool"
          aria-label="Tool"
          value={tool}
          onChange={(event) => {
            setTool(event.target.value);
            setSelection(undefined);
          }}
        >
          <SimpleSelectOption value="">Select a tool</SimpleSelectOption>
          {tools.map((name) => (
            <SimpleSelectOption key={name} value={name}>
              {name}
            </SimpleSelectOption>
          ))}
        </SimpleSelect>
        <SimpleSelect
          componentId="mlflow.agent_registry.investigation.failures"
          id="investigation-failures"
          aria-label="Request outcome"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        >
          <SimpleSelectOption value="all">All requests</SimpleSelectOption>
          <SimpleSelectOption value="errors">Tool errors</SimpleSelectOption>
          <SimpleSelectOption value="recovered">Tool errors with successful request</SimpleSelectOption>
        </SimpleSelect>
      </div>
      <EvidenceTable
        traces={visible}
        tool={tool}
        selectedId={selected?.id}
        onSelect={(trace) =>
          setSelection({ traceId: trace.id, spanId: getDependencyEvidence(trace, tool).failures[0]?.id })
        }
      />
      {selected && evidence && (
        <>
          <Panel title="Offered, invoked, and recovered">
            <Typography.Text>
              {evidence.offers.length
                ? `Offered in ${evidence.offers.length} model call(s).`
                : 'No offer recorded; availability is unknown.'}{' '}
              {evidence.calls.length} invocation(s), {evidence.failures.length} error(s), {evidence.retries.length}{' '}
              explicit retry link(s), {evidence.fallbacks.length} explicit fallback link(s).
            </Typography.Text>
            <Table>
              <TableRow isHeader>
                {['Span', 'Evidence', 'Result', 'Observed registry reference'].map((label) => (
                  <TableHeader key={label} componentId="mlflow.agent_registry.investigation.dependency_header">
                    {label}
                  </TableHeader>
                ))}
              </TableRow>
              {[...evidence.offers, ...evidence.calls, ...evidence.fallbacks].map((span) => (
                <TableRow key={span.id}>
                  <TableCell>
                    <Button
                      componentId="mlflow.agent_registry.investigation.span"
                      type="link"
                      onClick={() => setSelection({ traceId: selected.id, spanId: span.id })}
                    >
                      {span.name}
                    </Button>
                  </TableCell>
                  <TableCell>
                    {span.retryOf
                      ? `Retry of ${span.retryOf}`
                      : span.fallbackFor
                        ? `Fallback for ${span.fallbackFor}`
                        : span.type === 'LLM'
                          ? 'Tool offered'
                          : 'Tool invoked'}
                  </TableCell>
                  <TableCell>{span.error ?? 'OK'}</TableCell>
                  <TableCell>{span.asset ?? 'Not recorded'}</TableCell>
                </TableRow>
              ))}
            </Table>
            <Typography.Text color="secondary">
              Repeated calls without explicit links are not labeled retries. Request success does not establish answer
              quality.
            </Typography.Text>
          </Panel>
          <TraceEvidence
            key={`${selected.id}:${selection?.spanId}`}
            trace={selected}
            agent={agent}
            spanId={selection?.spanId}
          />
        </>
      )}
    </div>
  );
};

const RuntimeContext = ({ agent, traces }: { agent: Agent; traces: InvestigationTrace[] }) => {
  const { theme } = useDesignSystemTheme();
  const [instances, setInstances] = useState<string[]>([]);
  const [attribution, setAttribution] = useState('all');
  const [selectedId, setSelectedId] = useState<string>();
  const [baselineId, setBaselineId] = useState<string>();
  const visible = traces.filter(
    (trace) =>
      (!instances.length || getTraceInstances(trace).some((id) => instances.includes(id))) &&
      (attribution === 'all' ||
        (attribution === 'none' ? trace.snapshotIds.length === 0 : trace.snapshotIds.length > 1)),
  );
  const selected = visible.find((trace) => trace.id === selectedId);
  const baseline = traces.find((trace) => trace.id === baselineId);
  return (
    <div css={{ display: 'grid', gap: theme.spacing.md }}>
      <div css={{ display: 'flex', gap: theme.spacing.sm }}>
        <EvaluationFilter
          componentId="mlflow.agent_registry.investigation.instances"
          label="Instance IDs"
          allLabel="All instances"
          options={Array.from(new Set(traces.flatMap(getTraceInstances)))
            .sort()
            .map((id) => ({ value: id, label: id }))}
          selected={instances}
          onChange={setInstances}
        />
        <SimpleSelect
          componentId="mlflow.agent_registry.investigation.attribution"
          id="investigation-attribution"
          aria-label="Snapshot attribution"
          value={attribution}
          onChange={(event) => setAttribution(event.target.value)}
        >
          <SimpleSelectOption value="all">All attribution</SimpleSelectOption>
          <SimpleSelectOption value="none">No snapshot</SimpleSelectOption>
          <SimpleSelectOption value="mixed">Multiple snapshots</SimpleSelectOption>
        </SimpleSelect>
      </div>
      <Typography.Text color="secondary">
        Matches instance IDs on any span. These filters apply only to example evidence, not the live traces table.
      </Typography.Text>
      <EvidenceTable traces={visible} selectedId={selected?.id} onSelect={(trace) => setSelectedId(trace.id)} />
      {baseline && (
        <Typography.Text color="secondary">
          Baseline: {baseline.id}. Kept within this example environment, even when excluded by the instance filter.
        </Typography.Text>
      )}
      {selected && (
        <>
          <div css={{ display: 'flex', gap: theme.spacing.sm }}>
            <Button
              componentId="mlflow.agent_registry.investigation.runtime_baseline"
              onClick={() => setBaselineId(selected.id)}
            >
              Use as baseline
            </Button>
            {baseline && (
              <Button
                componentId="mlflow.agent_registry.investigation.clear_baseline"
                onClick={() => setBaselineId(undefined)}
              >
                Clear baseline
              </Button>
            )}
          </div>
          <TraceEvidence
            key={selected.id}
            trace={selected}
            agent={agent}
            initialTab="context"
            comparison={baseline?.id !== selected.id ? baseline : undefined}
          />
        </>
      )}
    </div>
  );
};

const InvestigationContent = ({
  agent,
  mode,
  environment,
}: {
  agent: Agent;
  mode: Investigation;
  environment: string;
}) => {
  const { theme } = useDesignSystemTheme();
  const samples = investigationTraces.filter((trace) => trace.agentId === agent.id);
  const environments = getSelectedEnvironments(environment);
  const [selectedEnvironment, setSelectedEnvironment] = useState(
    environments.length === 1 ? environments[0] : (environments[0] ?? samples[0]?.environment ?? 'unassigned'),
  );
  const traces = samples.filter((trace) => trace.environment === selectedEnvironment);
  return (
    <div css={{ display: 'grid', gap: theme.spacing.md }}>
      <Alert
        componentId="mlflow.agent_registry.investigation.preview"
        type="info"
        closable={false}
        message="Illustrative evidence - read-only preview"
        description="These browser-only examples are not fetched from your agent or included in dashboard metrics. No agent execution, registration, or backend writes occur."
      />
      <label>
        <Typography.Text>Example environment</Typography.Text>
        <SimpleSelect
          componentId="mlflow.agent_registry.investigation.environment"
          id="investigation-environment"
          aria-label="Example environment"
          value={selectedEnvironment}
          onChange={(event) => setSelectedEnvironment(event.target.value)}
        >
          {['dev', 'staging', 'prod', 'unassigned'].map((value) => (
            <SimpleSelectOption key={value} value={value}>
              {value}
            </SimpleSelectOption>
          ))}
        </SimpleSelect>
      </label>
      {!traces.length ? (
        <EmptyEvidence>
          No investigation examples for this agent and environment. Live activity is unchanged.
        </EmptyEvidence>
      ) : mode === 'change' ? (
        <BehaviorChanges key={selectedEnvironment} agent={agent} traces={traces} />
      ) : mode === 'dependencies' ? (
        <DependencyFailures key={selectedEnvironment} agent={agent} traces={traces} />
      ) : (
        <RuntimeContext key={selectedEnvironment} agent={agent} traces={traces} />
      )}
    </div>
  );
};

export const AgentInvestigationDrawer = ({ agent, environment }: { agent: Agent; environment: string }) => {
  const [params, setParams] = useSearchParams();
  const mode = params.get('investigation');
  if (mode !== 'change' && mode !== 'dependencies' && mode !== 'runtime') return null;
  return (
    <Drawer.Root
      open
      modal
      onOpenChange={(open) => {
        if (!open) {
          const next = new URLSearchParams(params);
          ['investigation', 'investigationSnapshot', 'investigationBaseline', 'investigationTool'].forEach((key) =>
            next.delete(key),
          );
          setParams(next);
        }
      }}
    >
      <Drawer.Content
        componentId="mlflow.agent_registry.investigation.drawer"
        title={`${titles[mode]} - ${agent.name}`}
        width="94vw"
      >
        <InvestigationContent
          key={`${agent.id}:${mode}:${params.get('investigationSnapshot')}`}
          agent={agent}
          mode={mode}
          environment={environment}
        />
      </Drawer.Content>
    </Drawer.Root>
  );
};
