import { describe, expect, it } from '@jest/globals';

import type { RunEntity } from '../experiment-tracking/types';
import { fleets } from './mockData';
import { getFleetMetricFilters, getFleetTraceFilters, getSelectedFleetMembers, matchesFleetRun } from './fleetScope';

describe('fleet membership scope', () => {
  const fleet = fleets[1];
  const members = getSelectedFleetMembers(fleet, 'all');
  const run = (agentId: string, environment?: string) =>
    ({
      data: {
        tags: [
          { key: 'mlflow.agent.id', value: agentId },
          ...(environment ? [{ key: 'mlflow.agent.environment', value: environment }] : []),
        ],
      },
    }) as RunEntity;

  it('includes all environments for member agents only', () => {
    expect(matchesFleetRun(members, run('support-agent', 'staging'))).toBe(true);
    expect(matchesFleetRun(members, run('knowledge-agent', 'dev'))).toBe(true);
    expect(matchesFleetRun(members, run('support-agent', 'dev'))).toBe(true);
    expect(matchesFleetRun(members, run('knowledge-agent', 'staging'))).toBe(true);
    expect(matchesFleetRun(members, run('refund-agent', 'prod'))).toBe(false);
  });

  it('includes unassigned activity for fleet members', () => {
    expect(matchesFleetRun([{ ...members[0], environment: undefined }], run('support-agent'))).toBe(true);
    expect(matchesFleetRun(members, run('support-agent'))).toBe(true);
  });

  it('uses the dev-only fleet scope for shared traces and charts', () => {
    expect(getFleetTraceFilters(fleet, members)).toEqual([
      expect.objectContaining({ key: 'mlflow.agent.preview.fleet.release-readiness', value: 'true' }),
    ]);
    expect(getFleetMetricFilters(fleet, members)).toEqual([
      "trace.tag.`mlflow.agent.preview.fleet.release-readiness` = 'true'",
    ]);
    expect(getFleetTraceFilters(fleet, getSelectedFleetMembers(fleet, 'support-agent'))).toContainEqual(
      expect.objectContaining({ key: 'mlflow.agent.id', value: 'support-agent' }),
    );
  });

  it.each(['dev', 'staging', 'prod', 'unassigned'])(
    'ignores legacy %s environment without dropping fleet scope',
    (environment) => {
      expect(getFleetMetricFilters(fleet, getSelectedFleetMembers(fleet, 'support-agent'), environment)).toEqual([
        "trace.tag.`mlflow.agent.preview.fleet.release-readiness` = 'true'",
        "trace.tag.`mlflow.agent.id` = 'support-agent'",
      ]);
    },
  );

  it('keeps unsupported multi-environment operators out of metric queries', () => {
    expect(getFleetMetricFilters(fleet, members, 'dev,staging')).toEqual(getFleetMetricFilters(fleet, members));
  });

  it('does not broaden an empty or unknown agent selection', () => {
    expect(getSelectedFleetMembers(fleet, 'none')).toEqual([]);
    expect(getSelectedFleetMembers(fleet, 'unknown')).toEqual([]);
    expect(matchesFleetRun([], run('support-agent', 'staging'))).toBe(false);
  });
});
