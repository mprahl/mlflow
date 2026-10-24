import { ArrowLeftIcon, Typography, UserGroupIcon, useDesignSystemTheme } from '@databricks/design-system';

import { fleetNavItems, fleetSectionPath } from '../../agent-registry/FleetNavConfig';
import { getFleet } from '../../agent-registry/mockData';
import { matchPath, useLocation } from '../utils/RoutingUtils';
import { MlflowSidebarLink } from './MlflowSidebarLink';

export const MlflowSidebarFleetDetailItems = ({ fleetId, collapsed }: { fleetId: string; collapsed: boolean }) => {
  const { theme } = useDesignSystemTheme();
  const { pathname, search } = useLocation();
  const activeSection = matchPath('/fleets/:fleetId/:section', pathname)?.params.section ?? 'dashboard';
  const selectedAgents = new URLSearchParams(search).get('agents');
  const suffix = selectedAgents ? `?agents=${encodeURIComponent(selectedAgents)}` : '';
  return (
    <>
      <MlflowSidebarLink
        css={{ border: `1px solid ${theme.colors.actionDefaultBorderDefault}`, marginBottom: theme.spacing.sm }}
        to="/fleets"
        componentId="mlflow.agent_registry.fleet.sidebar.back"
        isActive={() => false}
        icon={<ArrowLeftIcon />}
        collapsed={collapsed}
        tooltipContent="Back to fleets"
      >
        <UserGroupIcon />
        <Typography.Text ellipsis>{getFleet(fleetId)?.name ?? fleetId}</Typography.Text>
      </MlflowSidebarLink>
      {fleetNavItems.map((item) => (
        <MlflowSidebarLink
          key={item.key}
          css={{ paddingLeft: collapsed ? undefined : theme.spacing.lg }}
          to={`${fleetSectionPath(fleetId, item.key)}${suffix}`}
          componentId="mlflow.agent_registry.fleet.sidebar.section"
          isActive={() => activeSection === item.key}
          collapsed={collapsed}
          icon={item.icon}
        >
          {item.label}
        </MlflowSidebarLink>
      ))}
    </>
  );
};
