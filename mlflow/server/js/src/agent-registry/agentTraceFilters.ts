import { FilterOperator, TracesTableColumnGroup } from '../shared/web-shared/genai-traces-table/types';
import type { TableFilter } from '../shared/web-shared/genai-traces-table/types';
import { getSelectedEnvironments } from './components';

const AGENT_ID_TAG = 'mlflow.agent.id';
const ENVIRONMENT_TAG = 'mlflow.agent.environment';
const SNAPSHOT_TAG = 'mlflow.agent.snapshot_id';
const VERSION_TAG = 'mlflow.agent.version';
const RELATED_AGENT_TAG = 'mlflow.agent.related_agent_id';

const escapeFilterValue = (value: string) => value.replaceAll("'", "''");

export const getEnvironmentTraceTableFilters = (environment = 'all'): TableFilter[] => {
  const environments = getSelectedEnvironments(environment);
  return environments.length
    ? [
        {
          column: TracesTableColumnGroup.TAG,
          key: ENVIRONMENT_TAG,
          operator: environments.length === 1 ? FilterOperator.EQUALS : FilterOperator.RLIKE,
          value: environments.length === 1 ? environments[0] : `^(${environments.join('|')})$`,
        },
      ]
    : [];
};

export const getAgentTraceTableFilters = ({
  agentId,
  environment,
  snapshotId,
  version,
  relatedAgentId,
}: {
  agentId: string;
  environment?: string;
  snapshotId?: string;
  version?: string;
  relatedAgentId?: string;
}): TableFilter[] => {
  const tags = [
    [AGENT_ID_TAG, agentId],
    [SNAPSHOT_TAG, snapshotId],
    [VERSION_TAG, version],
    [RELATED_AGENT_TAG, relatedAgentId],
  ];
  const filters = tags
    .filter((entry): entry is [string, string] => Boolean(entry[1]) && entry[1] !== 'all')
    .map(([key, value]) => ({ column: TracesTableColumnGroup.TAG, key, operator: FilterOperator.EQUALS, value }));
  return [...filters, ...getEnvironmentTraceTableFilters(environment)];
};

export const getAgentTraceMetricFilters = (options: Parameters<typeof getAgentTraceTableFilters>[0]) =>
  getAgentTraceTableFilters(options)
    .filter(({ key, operator }) => key !== ENVIRONMENT_TAG || operator === FilterOperator.EQUALS)
    .map(({ key, operator, value }) => `trace.tag.\`${key}\` ${operator} '${escapeFilterValue(String(value))}'`);

export const getAgentTraceNavigationFilters = (options: Parameters<typeof getAgentTraceTableFilters>[0]) =>
  getAgentTraceTableFilters(options).map(
    ({ key, operator, value }) => `${TracesTableColumnGroup.TAG}::${operator}::${value}::${key}`,
  );
