import { Fragment } from 'react';
import { ArrowLeftIcon, SparkleDoubleIcon, Typography, useDesignSystemTheme } from '@databricks/design-system';

import { agentNavSections } from '../../agent-registry/AgentNavConfig';
import { agentPath } from '../../agent-registry/components';
import { getAgent } from '../../agent-registry/mockData';
import { matchPath, useLocation } from '../utils/RoutingUtils';
import { MlflowSidebarLink } from './MlflowSidebarLink';

export const MlflowSidebarAgentDetailItems = ({ agentId, collapsed }: { agentId: string; collapsed: boolean }) => {
  const { theme } = useDesignSystemTheme();
  const { pathname, search } = useLocation();
  const agent = getAgent(agentId);
  const activeSection = matchPath('/agents/:agentId/:section', pathname)?.params.section ?? 'overview';
  const environment = new URLSearchParams(search).get('environment') ?? 'all';

  return (
    <>
      <MlflowSidebarLink
        css={{ border: `1px solid ${theme.colors.actionDefaultBorderDefault}`, marginBottom: theme.spacing.sm }}
        to="/agents"
        componentId="mlflow.agent_registry.sidebar.back"
        isActive={() => false}
        icon={<ArrowLeftIcon />}
        collapsed={collapsed}
        tooltipContent="Back to agents"
      >
        <SparkleDoubleIcon />
        <Typography.Text ellipsis>{agent?.name ?? agentId}</Typography.Text>
      </MlflowSidebarLink>
      {agentNavSections.map(({ label, items }) => (
        <Fragment key={label}>
          {label &&
            (collapsed ? (
              <div
                css={{
                  paddingLeft: theme.spacing.lg,
                  marginTop: theme.spacing.sm,
                  marginBottom: theme.spacing.sm,
                  borderBottom: `1px solid ${theme.colors.actionDefaultBorderDefault}`,
                }}
              />
            ) : (
              <li css={{ paddingLeft: theme.spacing.lg, marginTop: theme.spacing.sm, marginBottom: theme.spacing.sm }}>
                <Typography.Text size="sm" color="secondary">
                  {label}
                </Typography.Text>
              </li>
            ))}
          {items.map((item) => (
            <MlflowSidebarLink
              css={{ paddingLeft: collapsed ? undefined : theme.spacing.lg }}
              key={item.key}
              to={agentPath(agentId, item.key, environment)}
              componentId="mlflow.agent_registry.sidebar.section"
              isActive={() => activeSection === item.key}
              collapsed={collapsed}
              icon={item.icon}
            >
              {item.label}
            </MlflowSidebarLink>
          ))}
        </Fragment>
      ))}
    </>
  );
};
