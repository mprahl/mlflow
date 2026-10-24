import {
  BeakerIcon,
  ChartLineIcon,
  ForkHorizontalIcon,
  PlusMinusSquareIcon,
  SparkleIcon,
  Typography,
  useDesignSystemTheme,
} from '@databricks/design-system';

import { Link } from '../common/utils/RoutingUtils';
import {
  GenAIOverviewCompactStageCard,
  GenAIOverviewStageCard,
} from '../experiment-tracking/pages/experiment-overview/components/GenAIOverviewStageCard';
import type { GenAIOverviewStageStatus } from '../experiment-tracking/pages/experiment-overview/genAIOverview.types';
import { agentPath, EnvironmentFilter, Events, matchesEnvironment, Panel } from './components';
import type { Agent, AgentEvent } from './mockData';
import { AgentTracingSetup } from './AgentTracingSetup';
import { getAssetChangeEvents } from './assetChanges';

const ready = (count: number): GenAIOverviewStageStatus => ({
  status: 'ready',
  totalCount: count,
  activity: [2, 5, 3, 6, 4, 7, 5].map((value, index) => ({ timestampMs: index, count: value })),
});

export const AgentJourneyOverview = ({
  agent,
  environment,
  onEnvironmentChange,
}: {
  agent: Agent;
  environment: string;
  onEnvironmentChange: (value: string) => void;
}) => {
  const { theme } = useDesignSystemTheme();
  const traces = agent.traces.filter((trace) => matchesEnvironment(environment, trace.environment));
  const evaluations = agent.evaluations.filter((evaluation) => matchesEnvironment(environment, evaluation.environment));
  const activityEvents: AgentEvent[] = [
    ...agent.events.filter((event) => event.kind === 'sync'),
    ...getAssetChangeEvents(agent),
  ]
    .filter((event) => matchesEnvironment(environment, event.environment))
    .sort((left, right) => right.at.localeCompare(left.at));

  if (!agent.traces.length) {
    return (
      <main
        css={{
          display: 'flex',
          flex: 1,
          overflowY: 'auto',
          padding: `${theme.spacing.sm}px ${theme.spacing.lg}px ${theme.spacing.lg}px`,
        }}
      >
        <div
          css={{
            width: '100%',
            maxWidth: 800,
            alignSelf: 'center',
            display: 'flex',
            flexDirection: 'column',
            gap: theme.spacing.xl,
            margin: '0 auto',
          }}
        >
          <section css={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: theme.spacing.md }}>
            <SparkleIcon aria-hidden="true" color="ai" css={{ fontSize: theme.spacing.md }} />
            <Typography.Title level={2} withoutMargins>
              Send your first trace to MLflow in just a minute
            </Typography.Title>
          </section>
          <section css={{ display: 'flex', flexDirection: 'column', gap: theme.spacing.sm }}>
            <div
              css={{
                overflow: 'hidden',
                border: `1px solid ${theme.colors.border}`,
                borderRadius: theme.borders.borderRadiusLg,
                backgroundColor: theme.colors.backgroundPrimary,
                boxShadow: theme.shadows.sm,
              }}
            >
              <GenAIOverviewStageCard
                componentId="mlflow.agent_registry.overview.trace_setup"
                icon={<ForkHorizontalIcon />}
                label="1. Trace"
                caption="traces"
                description="Start tracing your agent to send its requests to MLflow."
                status={{ status: 'empty', activity: [] }}
                content={<AgentTracingSetup agentId={agent.id} />}
                expanded
              />
            </div>
            <div
              css={{
                display: 'flex',
                flexDirection: 'column',
                gap: theme.spacing.md,
                marginTop: theme.spacing.sm,
                paddingTop: theme.spacing.md,
                borderTop: `1px solid ${theme.colors.border}`,
              }}
            >
              <Typography.Text color="secondary" size="sm">
                Unlocks with your first trace
              </Typography.Text>
              <div
                css={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
                  gap: theme.spacing.lg,
                  '@media (max-width: 800px)': { gridTemplateColumns: 'minmax(0, 1fr)' },
                }}
              >
                <GenAIOverviewCompactStageCard
                  icon={<SparkleIcon />}
                  label="2. Analyze"
                  description="Review traces to find failure modes and quality issues."
                />
                <GenAIOverviewCompactStageCard
                  icon={<BeakerIcon />}
                  label="3. Eval / Test"
                  description="Turn issues into regression tests before release."
                />
                <GenAIOverviewCompactStageCard
                  icon={<ChartLineIcon />}
                  label="4. Monitor"
                  description="Monitor production quality and usage over time."
                />
              </div>
            </div>
          </section>
        </div>
      </main>
    );
  }

  return (
    <main
      css={{
        display: 'flex',
        flex: 1,
        overflowY: 'auto',
        padding: `${theme.spacing.sm}px ${theme.spacing.lg}px ${theme.spacing.lg}px`,
      }}
    >
      <div
        css={{
          width: '100%',
          maxWidth: 800,
          alignSelf: 'center',
          display: 'flex',
          flexDirection: 'column',
          gap: theme.spacing.xl,
          margin: '0 auto',
        }}
      >
        <section css={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: theme.spacing.md }}>
          <SparkleIcon aria-hidden="true" color="ai" css={{ fontSize: theme.spacing.md }} />
          <Typography.Title level={2} withoutMargins>
            Let's improve your agent with MLflow
          </Typography.Title>
        </section>
        <div
          css={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: theme.spacing.md,
            flexWrap: 'wrap',
          }}
        >
          <div css={{ display: 'flex', flexDirection: 'column', gap: theme.spacing.xs }}>
            <Typography.Title level={3} withoutMargins>
              Build a continuous quality loop in 4 steps
            </Typography.Title>
            <Typography.Text color="secondary" size="sm">
              Trace, analyze, evaluate, and monitor this agent as it evolves.
            </Typography.Text>
          </div>
          <EnvironmentFilter value={environment} onChange={onEnvironmentChange} />
        </div>
        <div
          css={{
            border: `1px solid ${theme.colors.border}`,
            borderRadius: theme.borders.borderRadiusLg,
            backgroundColor: theme.colors.backgroundPrimary,
            '& > :not(:last-child)': { borderBottom: `1px solid ${theme.colors.border}` },
          }}
        >
          <GenAIOverviewStageCard
            componentId="mlflow.agent_registry.overview.trace"
            icon={<ForkHorizontalIcon />}
            label="Trace"
            caption="Sample agent traces"
            description="Inspect requests and spans."
            status={ready(traces.length)}
            artifactHref={agentPath(agent.id, 'traces', environment)}
            artifactLabel="View traces"
          />
          <GenAIOverviewStageCard
            componentId="mlflow.agent_registry.overview.analyze"
            icon={<SparkleIcon />}
            label="Analyze"
            caption="Errors in sample traces"
            description="Find failures in agent behavior."
            status={ready(traces.filter((trace) => trace.status === 'Error').length)}
            artifactHref={agentPath(agent.id, 'traces', environment)}
            artifactLabel="Explore traces"
          />
          <GenAIOverviewStageCard
            componentId="mlflow.agent_registry.overview.evaluate"
            icon={<PlusMinusSquareIcon />}
            label="Evaluate"
            caption="Evaluation runs"
            description="Compare the agent's snapshots."
            status={ready(evaluations.length)}
            artifactHref={agentPath(agent.id, 'evaluation-runs', environment)}
            artifactLabel="View evaluations"
          />
          <GenAIOverviewStageCard
            componentId="mlflow.agent_registry.overview.monitor"
            icon={<ChartLineIcon />}
            label="Monitor"
            caption="Recent trace activity"
            description="Review quality and usage over time."
            status={ready(traces.length)}
            artifactHref={agentPath(agent.id, 'dashboard', environment)}
            artifactLabel="View dashboard"
          />
        </div>
        <div
          css={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: theme.spacing.lg }}
        >
          <Panel
            title="Latest agent snapshot"
            action={
              <Link componentId="mlflow.agent_registry.overview.snapshots" to={agentPath(agent.id, 'snapshots')}>
                View snapshots
              </Link>
            }
          >
            <Typography.Text bold>Snapshot #{agent.snapshots[0]?.id}</Typography.Text>
            <div>
              <Typography.Text color="secondary">
                Publisher version {agent.snapshots[0]?.publisherVersion}
              </Typography.Text>
            </div>
          </Panel>
          <Panel
            title="Access endpoints"
            action={
              <Link componentId="mlflow.agent_registry.overview.endpoints" to={agentPath(agent.id, 'endpoints')}>
                View endpoints
              </Link>
            }
          >
            <Typography.Text bold>{agent.endpoints.length} access routes</Typography.Text>
            <div>
              <Typography.Text color="secondary">
                {agent.endpoints.map((endpoint) => endpoint.name).join(', ') || 'No endpoints registered'}
              </Typography.Text>
            </div>
          </Panel>
        </div>
        <Panel
          title="Recent agent activity"
          action={
            <Link
              componentId="mlflow.agent_registry.overview.timeline"
              to={`${agentPath(agent.id, 'dashboard', environment)}&dashboardTab=timeline`}
            >
              View timeline
            </Link>
          }
        >
          <Events events={activityEvents.slice(0, 5)} compact />
        </Panel>
      </div>
    </main>
  );
};
