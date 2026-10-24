import { useMemo, useState } from 'react';
import {
  Button,
  Header,
  PlusIcon,
  SimpleSelect,
  SimpleSelectOption,
  Spacer,
  Table,
  TableCell,
  TableFilterInput,
  TableFilterLayout,
  TableHeader,
  TableRow,
  Tag,
  ToggleButton,
  Typography,
  useDesignSystemTheme,
} from '@databricks/design-system';
import { FormattedMessage } from 'react-intl';

import { ScrollablePageWrapper } from '../common/components/ScrollablePageWrapper';
import { Link } from '../common/utils/RoutingUtils';
import { agentPath, formatDate } from './components';
import { agents, fleets } from './mockData';

const AgentRegistryPage = () => {
  const { theme } = useDesignSystemTheme();
  const [search, setSearch] = useState('');
  const [fleetId, setFleetId] = useState('all');
  const [hasEndpoint, setHasEndpoint] = useState(false);
  const visibleAgents = useMemo(
    () =>
      agents.filter((agent) => {
        const query = search.toLowerCase();
        return (
          (agent.name.toLowerCase().includes(query) ||
            agent.capabilities.some((capability) => capability.toLowerCase().includes(query))) &&
          (fleetId === 'all' ||
            fleets.find((fleet) => fleet.id === fleetId)?.members.some((member) => member.agentId === agent.id)) &&
          (!hasEndpoint || agent.endpoints.length > 0)
        );
      }),
    [search, fleetId, hasEndpoint],
  );

  return (
    <ScrollablePageWrapper css={{ overflow: 'hidden', display: 'flex', flexDirection: 'column', flex: 1 }}>
      <Spacer shrinks={false} />
      <Header
        title={<FormattedMessage defaultMessage="Agents" description="Agent registry list page title" />}
        buttons={
          <Button componentId="mlflow.agent_registry.create" type="primary" icon={<PlusIcon />} disabled>
            <FormattedMessage defaultMessage="Create agent" description="Create agent preview action" />
          </Button>
        }
      />
      <Spacer shrinks={false} />
      <div css={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, gap: theme.spacing.md }}>
        <TableFilterLayout>
          <TableFilterInput
            componentId="mlflow.agent_registry.search"
            placeholder="Search agents or capabilities"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            css={{ width: 270 }}
          />
          <ToggleButton
            componentId="mlflow.agent_registry.filter.endpoint"
            pressed={hasEndpoint}
            onPressedChange={setHasEndpoint}
            aria-label="Has endpoint"
          >
            <FormattedMessage defaultMessage="Has endpoint" description="Agent registry endpoint availability filter" />
          </ToggleButton>
          <SimpleSelect
            id="mlflow.agent_registry.filter.fleet"
            componentId="mlflow.agent_registry.filter.fleet"
            aria-label="Fleet"
            value={fleetId}
            onChange={(event) => setFleetId(event.target.value)}
            css={{ width: 210 }}
          >
            <SimpleSelectOption value="all">All fleets</SimpleSelectOption>
            {fleets.map((fleet) => (
              <SimpleSelectOption value={fleet.id} key={fleet.id}>
                {fleet.name}
              </SimpleSelectOption>
            ))}
          </SimpleSelect>
        </TableFilterLayout>
        <Table
          scrollable
          empty={!visibleAgents.length ? <Typography.Text>No agents match these filters.</Typography.Text> : undefined}
        >
          <TableRow isHeader>
            <TableHeader componentId="mlflow.agent_registry.table.header">Name</TableHeader>
            <TableHeader componentId="mlflow.agent_registry.table.header">Description</TableHeader>
            <TableHeader componentId="mlflow.agent_registry.table.header">Owner</TableHeader>
            <TableHeader componentId="mlflow.agent_registry.table.header">Latest snapshot</TableHeader>
            <TableHeader componentId="mlflow.agent_registry.table.header">Access endpoint</TableHeader>
            <TableHeader componentId="mlflow.agent_registry.table.header">Last activity</TableHeader>
          </TableRow>
          {visibleAgents.map((agent) => (
            <TableRow key={agent.id} css={{ height: theme.general.buttonHeight }}>
              <TableCell css={{ alignItems: 'center' }}>
                <Link componentId="mlflow.agent_registry.agent.link" to={agentPath(agent.id)}>
                  {agent.name}
                </Link>
              </TableCell>
              <TableCell css={{ alignItems: 'center' }}>
                <Typography.Text ellipsis title={agent.description}>
                  {agent.description}
                </Typography.Text>
              </TableCell>
              <TableCell css={{ alignItems: 'center' }}>{agent.owner}</TableCell>
              <TableCell css={{ alignItems: 'center' }}>
                {agent.snapshots[0] ? (
                  <Link componentId="mlflow.agent_registry.snapshot.link" to={agentPath(agent.id, 'snapshots')}>
                    #{agent.snapshots[0].id}
                  </Link>
                ) : (
                  '—'
                )}
              </TableCell>
              <TableCell css={{ alignItems: 'center' }}>
                {agent.endpoints.length ? (
                  <Link componentId="mlflow.agent_registry.endpoints.link" to={agentPath(agent.id, 'endpoints')}>
                    <Tag componentId="mlflow.agent_registry.endpoint.protocol" color="indigo">
                      {agent.endpoints[0].protocol}
                    </Tag>{' '}
                    {agent.endpoints.length} endpoint{agent.endpoints.length === 1 ? '' : 's'}
                  </Link>
                ) : (
                  '—'
                )}
              </TableCell>
              <TableCell css={{ alignItems: 'center' }}>
                {agent.events[0] ? formatDate(agent.events[0].at) : '—'}
              </TableCell>
            </TableRow>
          ))}
        </Table>
      </div>
    </ScrollablePageWrapper>
  );
};

export default AgentRegistryPage;
