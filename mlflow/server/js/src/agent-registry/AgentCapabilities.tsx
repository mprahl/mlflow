import {
  CheckCircleIcon,
  CircleOffIcon,
  SparkleIcon,
  Tag,
  Typography,
  useDesignSystemTheme,
} from '@databricks/design-system';
import { z } from 'zod';

import { formatDate } from './components';
import type { AccessEndpoint } from './mockData';

const skillsSchema = z.array(
  z.object({
    name: z.string(),
    description: z.string().optional(),
    tags: z.array(z.string()).optional(),
    examples: z.array(z.string()).optional(),
  }),
);
const protocolSchema = z.record(z.unknown());

export const ProtocolCapabilities = ({ capabilities }: { capabilities: Record<string, unknown> }) => {
  const { theme } = useDesignSystemTheme();
  const flags = Object.entries(capabilities).filter(
    (entry): entry is [string, boolean] => typeof entry[1] === 'boolean',
  );
  return (
    <div
      role="list"
      aria-label="Protocol capabilities"
      css={{
        display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 200px), 1fr))',
        gap: theme.spacing.sm,
      }}
    >
      {flags.map(([name, supported]) => (
        <div
          key={name}
          role="listitem"
          css={{
            display: 'flex',
            gap: theme.spacing.sm,
            alignItems: 'flex-start',
            padding: theme.spacing.md,
            border: `1px solid ${theme.colors.border}`,
            borderRadius: theme.borders.borderRadiusMd,
            minWidth: 0,
          }}
        >
          <span
            css={{
              display: 'flex',
              marginTop: theme.spacing.xs,
              color: supported ? theme.colors.actionPrimaryBackgroundDefault : theme.colors.textSecondary,
            }}
          >
            {supported ? <CheckCircleIcon /> : <CircleOffIcon />}
          </span>
          <div css={{ display: 'flex', flexDirection: 'column', gap: theme.spacing.xs, minWidth: 0 }}>
            <Typography.Text bold css={{ overflowWrap: 'anywhere' }}>
              {name.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/^./, (letter) => letter.toUpperCase())}
            </Typography.Text>
            <Typography.Text color="secondary" size="sm">
              {supported ? 'Supported' : 'Not supported'}
            </Typography.Text>
          </div>
        </div>
      ))}
    </div>
  );
};

export const AgentCapabilities = ({ endpoint }: { endpoint: AccessEndpoint }) => {
  const { theme } = useDesignSystemTheme();
  const parsedSkills = skillsSchema.safeParse(endpoint.card?.['skills']);
  const skills = parsedSkills.success ? parsedSkills.data : [];
  const protocol = protocolSchema.safeParse(endpoint.card?.['capabilities']);
  const hasProtocolFlags = protocol.success && Object.values(protocol.data).some((value) => typeof value === 'boolean');

  return (
    <div css={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: theme.spacing.lg }}>
      <div>
        <div css={{ display: 'flex', alignItems: 'center', gap: theme.spacing.sm, marginBottom: theme.spacing.xs }}>
          <Typography.Title level={4} withoutMargins>
            Advertised capabilities
          </Typography.Title>
          <Tag componentId="mlflow.agent_registry.capabilities.count">{endpoint.capabilities.length}</Tag>
        </div>
        <Typography.Text size="sm" color="secondary">
          {endpoint.card
            ? `A2A card${endpoint.lastSynced ? ` · Synced ${formatDate(endpoint.lastSynced)}` : ''}`
            : 'Endpoint declaration'}
        </Typography.Text>
      </div>
      {endpoint.capabilities.length ? (
        <div
          css={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 260px), 1fr))',
            gap: theme.spacing.md,
          }}
        >
          {endpoint.capabilities.map((name) => {
            const skill = skills.find((item) => item.name === name);
            return (
              <article
                key={name}
                aria-label={name}
                css={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: theme.spacing.md,
                  minWidth: 0,
                  padding: theme.spacing.md,
                  border: `1px solid ${theme.colors.border}`,
                  borderRadius: theme.borders.borderRadiusMd,
                }}
              >
                <div css={{ display: 'flex', alignItems: 'flex-start', gap: theme.spacing.sm }}>
                  <span
                    css={{
                      flexShrink: 0,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      width: theme.spacing.xl,
                      height: theme.spacing.xl,
                      borderRadius: theme.borders.borderRadiusSm,
                      backgroundColor: theme.colors.backgroundSecondary,
                      color: theme.colors.actionPrimaryBackgroundDefault,
                    }}
                  >
                    <SparkleIcon />
                  </span>
                  <Typography.Title
                    level={5}
                    withoutMargins
                    css={{ paddingTop: theme.spacing.xs, overflowWrap: 'anywhere' }}
                  >
                    {name}
                  </Typography.Title>
                </div>
                {skill?.description && <Typography.Text color="secondary">{skill.description}</Typography.Text>}
                {skill?.tags?.length ? (
                  <div css={{ display: 'flex', flexWrap: 'wrap', gap: theme.spacing.xs }}>
                    {skill.tags.map((tag) => (
                      <Tag key={tag} componentId="mlflow.agent_registry.capabilities.tag">
                        {tag}
                      </Tag>
                    ))}
                  </div>
                ) : null}
                {skill?.examples?.length ? (
                  <div
                    css={{
                      display: 'grid',
                      gap: theme.spacing.xs,
                      marginTop: 'auto',
                      paddingTop: theme.spacing.sm,
                      borderTop: `1px solid ${theme.colors.border}`,
                    }}
                  >
                    <Typography.Text size="sm" color="secondary">
                      Example requests
                    </Typography.Text>
                    {skill.examples.map((example, index) => (
                      <Typography.Text key={index} css={{ overflowWrap: 'anywhere' }}>
                        {example}
                      </Typography.Text>
                    ))}
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      ) : (
        <div css={{ display: 'flex', alignItems: 'center', justifyContent: 'center', minHeight: 200 }}>
          <Typography.Text color="secondary">No advertised capabilities.</Typography.Text>
        </div>
      )}
      {hasProtocolFlags && (
        <section css={{ display: 'grid', gap: theme.spacing.md }}>
          <Typography.Title level={4} withoutMargins>
            Protocol support
          </Typography.Title>
          <ProtocolCapabilities capabilities={protocol.data} />
        </section>
      )}
    </div>
  );
};
