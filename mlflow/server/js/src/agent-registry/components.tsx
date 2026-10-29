import type { ReactNode } from 'react';
import {
  Table,
  TableCell,
  TableHeader,
  TableRow,
  Tag,
  Typography,
  useDesignSystemTheme,
} from '@databricks/design-system';

import { Link } from '../common/utils/RoutingUtils';
import type { AgentEvent, AgentTrace, Environment } from './mockData';

export const agentPath = (agentId: string, section = 'overview', environment?: string) =>
  `/agents/${encodeURIComponent(agentId)}/${section}${environment ? `?environment=${encodeURIComponent(environment)}` : ''}`;

export const fleetPath = (fleetId: string) => `/fleets/${encodeURIComponent(fleetId)}`;

export const formatDate = (value: string) =>
  new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(
    new Date(value),
  );

export const Panel = ({ title, action, children }: { title: ReactNode; action?: ReactNode; children: ReactNode }) => {
  const { theme } = useDesignSystemTheme();
  return (
    <section
      css={{
        minWidth: 0,
      }}
    >
      <div
        css={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: theme.spacing.sm,
          marginBottom: theme.spacing.md,
        }}
      >
        <Typography.Title level={4} withoutMargins>
          {title}
        </Typography.Title>
        {action}
      </div>
      {children}
    </section>
  );
};

export const getSelectedEnvironments = (_value: string): string[] => [];

export const matchesEnvironment = (_value: string, _environment?: string) => true;

export const EnvironmentFilter = (_props: { value: string; onChange: (value: string) => void }) => null;

export const EnvironmentTag = (_props: { environment?: Environment }) => null;

export const Events = ({ events, compact = false }: { events: AgentEvent[]; compact?: boolean }) => {
  const { theme } = useDesignSystemTheme();
  return events.length ? (
    <div>
      {events.map((event) => (
        <div
          key={event.id}
          css={{
            display: 'grid',
            gridTemplateColumns: compact ? '100px minmax(0, 1fr)' : '145px minmax(0, 1fr)',
            gap: theme.spacing.md,
            paddingBlock: theme.spacing.sm,
            borderTop: `1px solid ${theme.colors.actionDefaultBorderDefault}`,
          }}
        >
          <Typography.Text size="sm" color="secondary">
            {formatDate(event.at)}
          </Typography.Text>
          <div css={{ display: 'flex', flexDirection: 'column', gap: theme.spacing.xs }}>
            <div css={{ display: 'flex', alignItems: 'center', gap: theme.spacing.xs, flexWrap: 'wrap' }}>
              {event.href ? (
                <Link componentId="mlflow.agent_registry.activity.open_snapshot" to={event.href}>
                  {event.title}
                </Link>
              ) : (
                <Typography.Text bold>{event.title}</Typography.Text>
              )}
              {event.instanceId && (
                <Typography.Text code title="Instance ID">
                  {event.instanceId}
                </Typography.Text>
              )}
            </div>
            <Typography.Text size="sm" color="secondary">
              {event.detail}
            </Typography.Text>
          </div>
        </div>
      ))}
    </div>
  ) : (
    <Typography.Text color="secondary">No activity matches these filters.</Typography.Text>
  );
};

export const Traces = ({ traces, agentId }: { traces: AgentTrace[]; agentId?: string }) => {
  return traces.length ? (
    <Table scrollable>
      <TableRow isHeader>
        {[
          'Trace',
          'Time',
          'Environment',
          'Snapshot',
          'Latency',
          'Cost',
          'Status',
          ...(agentId ? ['Evidence'] : []),
        ].map((label) => (
          <TableHeader key={label} componentId="mlflow.agent_registry.traces.header">
            {label}
          </TableHeader>
        ))}
      </TableRow>
      {traces.map((trace) => (
        <TableRow key={trace.id}>
          <TableCell>
            <Typography.Text ellipsis title={trace.session}>
              {trace.session}
            </Typography.Text>
          </TableCell>
          <TableCell>{formatDate(trace.at)}</TableCell>
          <TableCell>
            <EnvironmentTag environment={trace.environment} />
          </TableCell>
          <TableCell>{trace.snapshotId ? `#${trace.snapshotId}` : 'Unassigned'}</TableCell>
          <TableCell>{trace.latency}</TableCell>
          <TableCell>{trace.cost}</TableCell>
          <TableCell>{trace.status}</TableCell>
          {agentId && (
            <TableCell>
              <Link
                componentId="mlflow.agent_registry.trace.open"
                to={`${agentPath(agentId, 'traces', trace.environment)}${trace.snapshotId ? `&snapshot=${trace.snapshotId}` : ''}`}
              >
                View traces
              </Link>
            </TableCell>
          )}
        </TableRow>
      ))}
    </Table>
  ) : (
    <Typography.Text color="secondary">No traces match this selection.</Typography.Text>
  );
};
