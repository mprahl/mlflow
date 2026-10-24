import type { DocumentTitleHandle } from '../common/utils/RoutingUtils';
import { createLazyRouteElement } from '../common/utils/RoutingUtils';

export const getAgentRegistryRouteDefs = () => [
  {
    path: '/agents',
    element: createLazyRouteElement(() => import('./AgentRegistryPage')),
    pageId: 'mlflow.agent-registry.list',
    handle: { getPageTitle: () => 'Agents' } satisfies DocumentTitleHandle,
  },
  {
    path: '/agents/:agentId/:section',
    element: createLazyRouteElement(() => import('./AgentDetailPage')),
    pageId: 'mlflow.agent-registry.detail.section',
    handle: { getPageTitle: (params) => `Agent: ${params['agentId']}` } satisfies DocumentTitleHandle,
  },
  {
    path: '/agents/:agentId',
    element: createLazyRouteElement(() => import('./AgentDetailPage')),
    pageId: 'mlflow.agent-registry.detail',
    handle: { getPageTitle: (params) => `Agent: ${params['agentId']}` } satisfies DocumentTitleHandle,
  },
  {
    path: '/fleets',
    element: createLazyRouteElement(() => import('./FleetPages').then((module) => ({ default: module.FleetListPage }))),
    pageId: 'mlflow.agent-registry.fleets',
    handle: { getPageTitle: () => 'Agent fleets' } satisfies DocumentTitleHandle,
  },
  {
    path: '/fleets/:fleetId/:section',
    element: createLazyRouteElement(() => import('./FleetPages')),
    pageId: 'mlflow.agent-registry.fleet-detail.section',
    handle: { getPageTitle: (params) => `Fleet: ${params['fleetId']}` } satisfies DocumentTitleHandle,
  },
  {
    path: '/fleets/:fleetId',
    element: createLazyRouteElement(() => import('./FleetPages')),
    pageId: 'mlflow.agent-registry.fleet-detail',
    handle: { getPageTitle: (params) => `Fleet: ${params['fleetId']}` } satisfies DocumentTitleHandle,
  },
];
