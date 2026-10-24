import { describe, expect, it } from '@jest/globals';

import {
  getAgentTraceMetricFilters,
  getAgentTraceNavigationFilters,
  getAgentTraceTableFilters,
} from './agentTraceFilters';

describe('agent trace filters', () => {
  it('scopes the real trace table and chart queries to an agent and environment', () => {
    const options = { agentId: 'support-agent', environment: 'prod', snapshotId: '7', version: '2.4.0' };

    expect(getAgentTraceTableFilters(options)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ key: 'mlflow.agent.id', value: 'support-agent' }),
        expect.objectContaining({ key: 'mlflow.agent.environment', value: 'prod' }),
      ]),
    );
    expect(getAgentTraceMetricFilters(options)).toContain("trace.tag.`mlflow.agent.id` = 'support-agent'");
    expect(getAgentTraceMetricFilters(options)).toContain("trace.tag.`mlflow.agent.snapshot_id` = '7'");
    expect(getAgentTraceNavigationFilters(options)).toContain('TAG::=::support-agent::mlflow.agent.id');
  });

  it('omits all-value filters and escapes metric values', () => {
    const filters = getAgentTraceMetricFilters({ agentId: "agent'o", environment: 'all' });

    expect(filters).toEqual(["trace.tag.`mlflow.agent.id` = 'agent''o'"]);
  });

  it('uses one regex filter for multiple selected environments', () => {
    const options = { agentId: 'support-agent', environment: 'dev,prod' };

    expect(getAgentTraceTableFilters(options)).toContainEqual(
      expect.objectContaining({ key: 'mlflow.agent.environment', operator: 'RLIKE', value: '^(dev|prod)$' }),
    );
    expect(getAgentTraceMetricFilters(options)).toEqual(["trace.tag.`mlflow.agent.id` = 'support-agent'"]);
  });
});
