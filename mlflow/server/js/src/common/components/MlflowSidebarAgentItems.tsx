import { SparkleDoubleIcon, UserGroupIcon, useDesignSystemTheme } from '@databricks/design-system';
import { FormattedMessage } from 'react-intl';

import { matchPath } from '../utils/RoutingUtils';
import type { Location } from '../utils/RoutingUtils';
import { MlflowSidebarLink } from './MlflowSidebarLink';

export const isAgentRegistryActive = (location: Location) =>
  Boolean(
    matchPath({ path: '/agents', end: true }, location.pathname) ||
    matchPath('/agents/*', location.pathname) ||
    matchPath({ path: '/fleets', end: true }, location.pathname) ||
    matchPath('/fleets/*', location.pathname),
  );

const isAgentsListActive = (location: Location) =>
  Boolean(matchPath({ path: '/agents', end: true }, location.pathname) || matchPath('/agents/*', location.pathname));

const isFleetsActive = (location: Location) =>
  Boolean(matchPath({ path: '/fleets', end: true }, location.pathname) || matchPath('/fleets/*', location.pathname));

export const MlflowSidebarAgentItems = ({ collapsed }: { collapsed: boolean }) => {
  const { theme } = useDesignSystemTheme();

  return (
    <>
      <li
        css={{
          display: 'flex',
          alignItems: 'center',
          gap: theme.spacing.sm,
          justifyContent: collapsed ? 'center' : 'flex-start',
          paddingInline: collapsed ? 0 : theme.spacing.sm,
          paddingBlock: collapsed ? 7 : theme.spacing.sm,
          border: collapsed ? `1px solid ${theme.colors.actionDefaultBorderDefault}` : 'none',
          borderRadius: theme.borders.borderRadiusSm,
          marginBottom: collapsed ? theme.spacing.sm : 0,
          boxSizing: 'border-box',
        }}
      >
        <SparkleDoubleIcon />
        {!collapsed && <FormattedMessage defaultMessage="Agents" description="Sidebar agent group heading" />}
      </li>
      <MlflowSidebarLink
        css={{ paddingLeft: collapsed ? undefined : theme.spacing.lg }}
        to="/agents"
        componentId="mlflow.sidebar.agents_list_link"
        isActive={isAgentsListActive}
        icon={<SparkleDoubleIcon />}
        collapsed={collapsed}
      >
        <FormattedMessage defaultMessage="Agents" description="Sidebar agent list link" />
      </MlflowSidebarLink>
      <MlflowSidebarLink
        css={{ paddingLeft: collapsed ? undefined : theme.spacing.lg }}
        to="/fleets"
        componentId="mlflow.sidebar.fleets_link"
        isActive={isFleetsActive}
        icon={<UserGroupIcon />}
        collapsed={collapsed}
      >
        <FormattedMessage defaultMessage="Fleets" description="Sidebar fleets link" />
      </MlflowSidebarLink>
    </>
  );
};
