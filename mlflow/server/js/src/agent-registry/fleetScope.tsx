import {
  DialogCombobox,
  DialogComboboxContent,
  DialogComboboxOptionList,
  DialogComboboxOptionListCheckboxItem,
  DialogComboboxOptionListSearch,
  DialogComboboxTrigger,
} from '@databricks/design-system';
import { escapeRegExp } from 'lodash';

import { FilterOperator, TracesTableColumnGroup } from '../shared/web-shared/genai-traces-table/types';
import type { TableFilter } from '../shared/web-shared/genai-traces-table/types';
import type { RunEntity } from '../experiment-tracking/types';
import type { Agent, Fleet } from './mockData';
import { getAgent } from './mockData';
import { getEnvironmentTraceTableFilters } from './agentTraceFilters';

export type FleetMember = Fleet['members'][number] & { agent: Agent };

export const getFleetScopeTag = (fleetId: string) => `mlflow.agent.preview.fleet.${fleetId}`;

export const getSelectedFleetMembers = (fleet: Fleet, selection: string): FleetMember[] => {
  const selected = selection.split(',');
  return fleet.members.flatMap((member) => {
    const agent = getAgent(member.agentId);
    return agent && (selection === 'all' || selected.includes(member.agentId)) ? [{ ...member, agent }] : [];
  });
};

export const getFleetTraceFilters = (fleet: Fleet, members: FleetMember[]): TableFilter[] => {
  const filters: TableFilter[] = [
    {
      column: TracesTableColumnGroup.TAG,
      key: getFleetScopeTag(fleet.id),
      operator: FilterOperator.EQUALS,
      value: 'true',
    },
  ];
  if (members.length !== fleet.members.length) {
    filters.push({
      column: TracesTableColumnGroup.TAG,
      key: 'mlflow.agent.id',
      operator: members.length === 1 ? FilterOperator.EQUALS : FilterOperator.RLIKE,
      value:
        members.length === 1
          ? members[0].agentId
          : `^(${members.map((member) => escapeRegExp(member.agentId)).join('|')})$`,
    });
  }
  return filters;
};

export const getFleetMetricFilters = (fleet: Fleet, members: FleetMember[], environment = 'all') =>
  [...getFleetTraceFilters(fleet, members), ...getEnvironmentTraceTableFilters(environment)]
    .filter((filter) => filter.operator === FilterOperator.EQUALS)
    .map(({ key, value }) => `trace.tag.\`${key}\` = '${String(value).replaceAll("'", "''")}'`);

export const matchesFleetRun = (members: FleetMember[], run: RunEntity) => {
  const tag = (key: string) => run.data.tags?.find((item) => item.key === key)?.value;
  return members.some((member) => member.agentId === tag('mlflow.agent.id'));
};

export const FleetAgentFilter = ({
  fleet,
  members,
  onChange,
}: {
  fleet: Fleet;
  members: FleetMember[];
  onChange: (value: string) => void;
}) => {
  const all = members.length === fleet.members.length;
  const selectedIds = members.map((member) => member.agentId);
  return (
    <DialogCombobox
      componentId="mlflow.agent_registry.fleet.agents_filter"
      label="Agents"
      multiSelect
      value={all ? [] : members.length ? members.map((member) => member.agent.name) : ['No agents']}
    >
      <DialogComboboxTrigger
        aria-label="Agents"
        placeholder="All agents"
        allowClear={false}
        css={{ maxWidth: '100%', minWidth: 0, '& button': { maxWidth: '100%', minWidth: 0 } }}
      />
      <DialogComboboxContent maxHeight={300}>
        <DialogComboboxOptionList>
          <DialogComboboxOptionListSearch>
            <DialogComboboxOptionListCheckboxItem value="All agents" checked={all} onChange={() => onChange('all')}>
              All agents
            </DialogComboboxOptionListCheckboxItem>
            {fleet.members.map((member) => {
              const name = getAgent(member.agentId)?.name ?? member.agentId;
              return (
                <DialogComboboxOptionListCheckboxItem
                  key={member.agentId}
                  value={name}
                  checked={selectedIds.includes(member.agentId)}
                  onChange={() => {
                    const next = selectedIds.includes(member.agentId)
                      ? selectedIds.filter((id) => id !== member.agentId)
                      : [...selectedIds, member.agentId];
                    onChange(next.length === fleet.members.length ? 'all' : next.length ? next.join(',') : 'none');
                  }}
                >
                  {name}
                </DialogComboboxOptionListCheckboxItem>
              );
            })}
          </DialogComboboxOptionListSearch>
        </DialogComboboxOptionList>
      </DialogComboboxContent>
    </DialogCombobox>
  );
};
