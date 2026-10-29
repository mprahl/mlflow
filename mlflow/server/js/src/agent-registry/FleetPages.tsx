import { useMemo, useState } from 'react';
import {
  Button,
  Breadcrumb,
  Header,
  PageWrapper,
  PlusIcon,
  Spacer,
  Table,
  TableCell,
  TableFilterInput,
  TableFilterLayout,
  TableHeader,
  TableRow,
  Tag,
  Typography,
  useDesignSystemTheme,
} from '@databricks/design-system';

import { ScrollablePageWrapper } from '../common/components/ScrollablePageWrapper';
import { ExperimentPageMainContent } from '../experiment-tracking/pages/experiment-page-tabs/ExperimentPageMainContent';
import { Link, useParams, useSearchParams } from '../common/utils/RoutingUtils';
import { agentPath } from './components';
import { fleets, getAgent, getFleet } from './mockData';
import { fleetNavItems, fleetSectionPath } from './FleetNavConfig';
import { FleetAgentFilter, getSelectedFleetMembers } from './fleetScope';
import type { FleetMember } from './fleetScope';
import { FleetMonitoring } from './FleetMonitoring';

export const FleetListPage = () => {
  const { theme } = useDesignSystemTheme();
  const [search, setSearch] = useState('');
  const visibleFleets = fleets.filter((fleet) =>
    `${fleet.name} ${fleet.description} ${fleet.members.map((member) => getAgent(member.agentId)?.name ?? '').join(' ')}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  return (
    <ScrollablePageWrapper css={{ overflow: 'hidden', display: 'flex', flexDirection: 'column', flex: 1 }}>
      <Spacer shrinks={false} />
      <Header
        title="Fleets"
        buttons={
          <Button componentId="mlflow.agent_registry.fleets.create" type="primary" icon={<PlusIcon />} disabled>
            Create fleet
          </Button>
        }
      />
      <Spacer shrinks={false} />
      <TableFilterLayout css={{ marginBottom: theme.spacing.md }}>
        <TableFilterInput
          componentId="mlflow.agent_registry.fleets.search"
          placeholder="Search fleets"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </TableFilterLayout>
      <div css={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
        <Table
          scrollable
          css={{
            '& [role=row]': { minWidth: 760 },
            '& [role=cell]:nth-of-type(2), & [role=columnheader]:nth-of-type(2)': { flex: 2 },
          }}
          empty={
            !visibleFleets.length ? (
              <div
                css={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 300, width: '100%' }}
              >
                <Typography.Text color="secondary">No fleets match this search.</Typography.Text>
              </div>
            ) : undefined
          }
        >
          <TableRow isHeader>
            <TableHeader componentId="mlflow.agent_registry.fleets.table.header">Name</TableHeader>
            <TableHeader componentId="mlflow.agent_registry.fleets.table.header">Description</TableHeader>
            <TableHeader componentId="mlflow.agent_registry.fleets.table.header">Agents</TableHeader>
            <TableHeader componentId="mlflow.agent_registry.fleets.table.header">Owners</TableHeader>
          </TableRow>
          {visibleFleets.map((fleet) => (
            <TableRow key={fleet.id} css={{ height: theme.general.buttonHeight }}>
              <TableCell css={{ alignItems: 'center' }}>
                <Link componentId="mlflow.agent_registry.fleet" to={`/fleets/${fleet.id}`}>
                  {fleet.name}
                </Link>
              </TableCell>
              <TableCell css={{ alignItems: 'center' }}>
                <Typography.Text ellipsis title={fleet.description} css={{ width: '100%', minWidth: 0 }}>
                  {fleet.description}
                </Typography.Text>
              </TableCell>
              <TableCell css={{ alignItems: 'center' }}>
                <Link componentId="mlflow.agent_registry.fleet.members_link" to={fleetSectionPath(fleet.id, 'agents')}>
                  {fleet.members.length} agents
                </Link>
              </TableCell>
              <TableCell css={{ alignItems: 'center' }}>
                <Typography.Text
                  ellipsis
                  css={{ width: '100%', minWidth: 0 }}
                  title={Array.from(
                    new Set(fleet.members.map((member) => getAgent(member.agentId)?.owner).filter(Boolean)),
                  ).join(', ')}
                >
                  {Array.from(
                    new Set(fleet.members.map((member) => getAgent(member.agentId)?.owner).filter(Boolean)),
                  ).join(', ')}
                </Typography.Text>
              </TableCell>
            </TableRow>
          ))}
        </Table>
      </div>
    </ScrollablePageWrapper>
  );
};

const FleetMembersTable = ({ members }: { members: FleetMember[] }) => {
  const { theme } = useDesignSystemTheme();
  const [search, setSearch] = useState('');
  const visibleMembers = members.filter(({ agent }) =>
    `${agent.name} ${agent.description} ${agent.owner}`.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <div css={{ display: 'flex', flexDirection: 'column', gap: theme.spacing.md, minHeight: 0 }}>
      <TableFilterLayout>
        <TableFilterInput
          componentId="mlflow.agent_registry.fleet.members.search"
          placeholder="Search fleet agents"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          css={{ width: 270 }}
        />
      </TableFilterLayout>
      <Table
        scrollable
        css={{
          '& [role=row]': { minWidth: 1000 },
          '& [role=cell]:nth-of-type(2), & [role=columnheader]:nth-of-type(2)': { flex: 1.7 },
        }}
        empty={
          !visibleMembers.length ? (
            <div
              css={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 300, width: '100%' }}
            >
              <Typography.Text color="secondary">No agents match this selection.</Typography.Text>
            </div>
          ) : undefined
        }
      >
        <TableRow isHeader>
          {['Name', 'Description', 'Owner', 'Access endpoint', 'Activity'].map((header) => (
            <TableHeader key={header} componentId="mlflow.agent_registry.fleet.members.header" multiline>
              {header}
            </TableHeader>
          ))}
        </TableRow>
        {visibleMembers.map((member) => (
          <TableRow key={member.agentId} css={{ minHeight: theme.general.buttonHeight }}>
            <TableCell css={{ alignItems: 'center' }}>
              <Link
                componentId="mlflow.agent_registry.fleet.member"
                to={agentPath(member.agentId, 'dashboard', member.environment)}
              >
                {member.agent?.name}
              </Link>
            </TableCell>
            <TableCell css={{ alignItems: 'center' }}>
              <Typography.Text ellipsis title={member.agent?.description} css={{ width: '100%', minWidth: 0 }}>
                {member.agent?.description}
              </Typography.Text>
            </TableCell>
            <TableCell css={{ alignItems: 'center' }}>{member.agent?.owner}</TableCell>
            <TableCell css={{ alignItems: 'center' }}>
              {member.agent.endpoints.length ? (
                <Link
                  componentId="mlflow.agent_registry.fleet.member.endpoints"
                  to={agentPath(member.agentId, 'endpoints', member.environment)}
                >
                  <Tag componentId="mlflow.agent_registry.fleet.member.protocol" color="indigo">
                    {member.agent.endpoints[0].protocol}
                  </Tag>{' '}
                  {member.agent.endpoints.length} endpoint{member.agent.endpoints.length === 1 ? '' : 's'}
                </Link>
              ) : (
                '—'
              )}
            </TableCell>
            <TableCell css={{ alignItems: 'center' }}>
              <div css={{ display: 'flex', flexDirection: 'column', gap: theme.spacing.xs }}>
                <Link
                  componentId="mlflow.agent_registry.fleet.member.dashboard"
                  to={agentPath(member.agentId, 'dashboard', member.environment)}
                >
                  Dashboard
                </Link>
                <Link
                  componentId="mlflow.agent_registry.fleet.member.traces"
                  to={agentPath(member.agentId, 'traces', member.environment)}
                >
                  Traces
                </Link>
              </div>
            </TableCell>
          </TableRow>
        ))}
      </Table>
    </div>
  );
};

const FleetDetailPage = () => {
  const { fleetId, section = 'dashboard' } = useParams<{ fleetId: string; section?: string }>();
  const fleet = getFleet(fleetId);
  const { theme } = useDesignSystemTheme();
  const [params, setParams] = useSearchParams();
  const selection = section === 'agents' ? 'all' : (params.get('agents') ?? 'all');
  const members = useMemo(() => (fleet ? getSelectedFleetMembers(fleet, selection) : []), [fleet, selection]);
  if (!fleet) {
    return (
      <PageWrapper>
        <Typography.Title level={2}>Fleet not found</Typography.Title>
        <Link componentId="mlflow.agent_registry.fleet.missing.back" to="/fleets">
          Back to fleets
        </Link>
      </PageWrapper>
    );
  }
  return (
    <div
      css={{
        display: 'flex',
        flexDirection: 'column',
        flex: 1,
        height: '100%',
        minHeight: 0,
        padding: theme.spacing.md,
      }}
    >
      <ExperimentPageMainContent
        breadcrumbs={
          <Breadcrumb includeTrailingCaret={false}>
            <Breadcrumb.Item>
              <Link componentId="mlflow.agent_registry.fleet.back" to="/fleets">
                Fleets
              </Link>
            </Breadcrumb.Item>
            <Breadcrumb.Item>
              <Link componentId="mlflow.agent_registry.fleet.detail" to={fleetSectionPath(fleet.id)}>
                {fleet.name}
              </Link>
            </Breadcrumb.Item>
            <Breadcrumb.Item>
              {fleetNavItems.find((item) => item.key === section)?.label ?? 'Dashboard'}
            </Breadcrumb.Item>
          </Breadcrumb>
        }
        headerDescription={<Typography.Text color="secondary">{fleet.description}</Typography.Text>}
      >
        <div css={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, gap: theme.spacing.md }}>
          {section !== 'agents' && (
            <div css={{ display: 'flex', alignItems: 'center', gap: theme.spacing.md, flexWrap: 'wrap' }}>
              <FleetAgentFilter
                fleet={fleet}
                members={members}
                onChange={(value) => {
                  const next = new URLSearchParams(params);
                  if (value === 'all') next.delete('agents');
                  else next.set('agents', value);
                  next.delete('selectedRunUuid');
                  next.delete('compareToRunUuid');
                  setParams(next);
                }}
              />
              <Typography.Text color="secondary" size="sm">
                {members.length} of {fleet.members.length} agents
              </Typography.Text>
            </div>
          )}
          <div
            css={{
              display: 'flex',
              flexDirection: 'column',
              flex: 1,
              minHeight: 0,
              overflow: section === 'traces' || section === 'evaluation-runs' ? 'hidden' : 'auto',
            }}
          >
            {section === 'agents' ? (
              <FleetMembersTable key={fleet.id} members={members} />
            ) : (
              <FleetMonitoring fleet={fleet} members={members} section={section} />
            )}
          </div>
        </div>
      </ExperimentPageMainContent>
    </div>
  );
};

export default FleetDetailPage;
