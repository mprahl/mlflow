import { Fragment, useState } from 'react';
import {
  CopyIcon,
  SegmentedControlButton,
  SegmentedControlGroup,
  Typography,
  useDesignSystemTheme,
} from '@databricks/design-system';
import { CodeSnippet } from '@databricks/web-shared/snippet';
import { z } from 'zod';

import { sanitizeHref } from '../common/utils/registryIcons';
import { CopyButton } from '../shared/building_blocks/CopyButton';
import { Panel } from './components';
import { cardSecuritySchema, getA2ASecurityRequirements } from './a2aConnection';
import { ProtocolCapabilities } from './AgentCapabilities';

const recordSchema = z.record(z.unknown());
const metadataKeys = [
  'name',
  'version',
  'protocolVersion',
  'description',
  'url',
  'provider',
  'documentationUrl',
  'iconUrl',
  'defaultInputModes',
  'defaultOutputModes',
];
const sectionKeys = [
  'capabilities',
  'skills',
  'securitySchemes',
  'security',
  'securityRequirements',
  'supportedInterfaces',
  'additionalInterfaces',
  'signatures',
];
const fieldLabels = {
  id: 'ID',
  url: 'URL',
  documentationUrl: 'Documentation URL',
  iconUrl: 'Icon URL',
  httpAuthSecurityScheme: 'HTTP authentication',
  apiKeySecurityScheme: 'API key',
  oauth2SecurityScheme: 'OAuth 2.0',
  openIdConnectSecurityScheme: 'OpenID Connect',
  mtlsSecurityScheme: 'Mutual TLS',
  openIdConnectUrl: 'Discovery URL',
  tokenUrl: 'Token URL',
  authorizationUrl: 'Authorization URL',
  bearerFormat: 'Bearer format',
  in: 'Location',
};
const fieldLabel = (name: string) =>
  Object.prototype.hasOwnProperty.call(fieldLabels, name)
    ? fieldLabels[name as keyof typeof fieldLabels]
    : name.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/^./, (letter) => letter.toUpperCase());

const CardValue = ({ value }: { value: unknown }) => {
  const { theme } = useDesignSystemTheme();
  if (value === null || value === undefined) return <Typography.Text color="secondary">Not specified</Typography.Text>;
  if (typeof value === 'boolean') return <Typography.Text>{value ? 'Enabled' : 'Disabled'}</Typography.Text>;
  if (typeof value === 'string') {
    const href = /^https?:\/\//i.test(value) ? sanitizeHref(value) : undefined;
    return href ? (
      <Typography.Link componentId="mlflow.agent_registry.card.link" href={href} openInNewTab>
        {value}
      </Typography.Link>
    ) : (
      <Typography.Text css={{ whiteSpace: 'pre-wrap' }}>{value}</Typography.Text>
    );
  }
  if (Array.isArray(value)) {
    return value.length ? (
      <ul css={{ margin: 0, paddingLeft: theme.spacing.md }}>
        {value.map((item, index) => (
          <li key={index}>
            <CardValue value={item} />
          </li>
        ))}
      </ul>
    ) : (
      <Typography.Text color="secondary">None</Typography.Text>
    );
  }
  const record = recordSchema.safeParse(value);
  return record.success ? <CardFields fields={record.data} /> : <Typography.Text>{String(value)}</Typography.Text>;
};

const CardFields = ({ fields }: { fields: Record<string, unknown> }) => {
  const { theme } = useDesignSystemTheme();
  return (
    <dl
      css={{
        display: 'grid',
        gridTemplateColumns: '140px minmax(0, 1fr)',
        gap: theme.spacing.sm,
        margin: 0,
        '@media (max-width: 760px)': { gridTemplateColumns: 'minmax(0, 1fr)' },
      }}
    >
      {Object.entries(fields).map(([name, value]) => (
        <Fragment key={name}>
          <dt>
            <Typography.Text color="secondary">{fieldLabel(name)}</Typography.Text>
          </dt>
          <dd css={{ margin: 0, minWidth: 0, overflowWrap: 'anywhere' }}>
            <CardValue value={value} />
          </dd>
        </Fragment>
      ))}
    </dl>
  );
};

export const AgentCardView = ({ card }: { card: Record<string, unknown> }) => {
  const { theme } = useDesignSystemTheme();
  const [view, setView] = useState('rendered');
  const raw = JSON.stringify(card, null, 2);
  const metadata = Object.fromEntries(metadataKeys.filter((key) => key in card).map((key) => [key, card[key]]));
  const additional = Object.fromEntries(
    Object.entries(card).filter(([key]) => !metadataKeys.includes(key) && !sectionKeys.includes(key)),
  );
  const skills = Array.isArray(card['skills']) ? card['skills'] : [];
  const capabilities = recordSchema.safeParse(card['capabilities']);
  const security = cardSecuritySchema.safeParse(card);
  const requirements = security.success ? getA2ASecurityRequirements(security.data) : [];

  return (
    <div css={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: theme.spacing.lg }}>
      <div
        css={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: theme.spacing.sm,
          flexWrap: 'wrap',
        }}
      >
        <SegmentedControlGroup
          name="agent-card-view"
          componentId="mlflow.agent_registry.card.view"
          value={view}
          onChange={(event) => setView(event.target.value)}
        >
          <SegmentedControlButton value="rendered">Rendered</SegmentedControlButton>
          <SegmentedControlButton value="raw">Raw JSON</SegmentedControlButton>
        </SegmentedControlGroup>
        <CopyButton
          componentId="mlflow.agent_registry.card.copy"
          copyText={raw}
          showLabel={false}
          type="tertiary"
          size="small"
          icon={<CopyIcon />}
          aria-label="Copy A2A card"
          title="Copy A2A card"
        />
      </div>
      {view === 'raw' ? (
        <div role="region" aria-label="Raw A2A card" css={{ minWidth: 0 }}>
          <CodeSnippet
            language="json"
            theme={theme.isDarkMode ? 'duotoneDark' : 'light'}
            style={{ padding: theme.spacing.md }}
          >
            {raw}
          </CodeSnippet>
        </div>
      ) : (
        <div
          role="region"
          aria-label="Rendered A2A card"
          css={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: theme.spacing.lg }}
        >
          <Panel title="Metadata">
            <CardFields fields={metadata} />
          </Panel>
          {card['capabilities'] !== undefined && (
            <Panel title="Capabilities">
              {capabilities.success ? (
                <div css={{ display: 'grid', gap: theme.spacing.md }}>
                  <ProtocolCapabilities capabilities={capabilities.data} />
                  <CardFields
                    fields={Object.fromEntries(
                      Object.entries(capabilities.data).filter(([, value]) => typeof value !== 'boolean'),
                    )}
                  />
                </div>
              ) : (
                <CardValue value={card['capabilities']} />
              )}
            </Panel>
          )}
          {(card['supportedInterfaces'] ?? card['additionalInterfaces']) !== undefined && (
            <Panel title="Interfaces">
              <CardValue value={card['supportedInterfaces'] ?? card['additionalInterfaces']} />
            </Panel>
          )}
          <Panel title="Skills">
            {skills.length ? (
              skills.map((value, index) => {
                const skill = recordSchema.safeParse(value);
                const name =
                  skill.success && typeof skill.data['name'] === 'string' ? skill.data['name'] : `Skill ${index + 1}`;
                return (
                  <section
                    key={index}
                    css={{
                      paddingBlock: theme.spacing.md,
                      borderBottom: `1px solid ${theme.colors.border}`,
                      '&:first-of-type': { paddingTop: 0 },
                      '&:last-of-type': { borderBottom: 0, paddingBottom: 0 },
                    }}
                  >
                    <Typography.Title level={5} css={{ marginTop: 0 }}>
                      {name}
                    </Typography.Title>
                    {skill.success ? (
                      <CardFields
                        fields={Object.fromEntries(Object.entries(skill.data).filter(([key]) => key !== 'name'))}
                      />
                    ) : (
                      <CardValue value={value} />
                    )}
                  </section>
                );
              })
            ) : (
              <Typography.Text color="secondary">No skills declared.</Typography.Text>
            )}
          </Panel>
          <Panel title="Security & authentication">
            <div css={{ display: 'grid', gap: theme.spacing.md }}>
              <div>
                <Typography.Text bold>
                  {requirements.length > 1 ? 'Authentication alternatives' : 'Authentication requirements'}
                </Typography.Text>
                {!security.success ? (
                  <div>
                    <Typography.Text color="secondary">
                      Security requirements could not be parsed. See the raw card.
                    </Typography.Text>
                  </div>
                ) : requirements.length ? (
                  requirements.map((requirement, index) => (
                    <div key={index}>
                      {index > 0 && <Typography.Text color="secondary">or</Typography.Text>}
                      <div>
                        <Typography.Text>
                          {Object.entries(requirement)
                            .map(([name, scopes]) => `${name}${scopes.length ? ` (${scopes.join(', ')})` : ''}`)
                            .join(' + ') || 'No authentication'}
                        </Typography.Text>
                      </div>
                    </div>
                  ))
                ) : (
                  <div>
                    <Typography.Text color="secondary">No authentication requirements declared.</Typography.Text>
                  </div>
                )}
              </div>
              {security.success &&
                Object.entries(security.data.securitySchemes).map(([name, scheme]) => (
                  <div key={name} css={{ display: 'grid', gap: theme.spacing.sm }}>
                    <Typography.Text bold>{name}</Typography.Text>
                    <CardValue value={scheme} />
                  </div>
                ))}
            </div>
          </Panel>
          {card['signatures'] !== undefined && (
            <Panel title="Signatures">
              <CardValue value={card['signatures']} />
            </Panel>
          )}
          {Object.keys(additional).length > 0 && (
            <Panel title="Additional metadata">
              <CardFields fields={additional} />
            </Panel>
          )}
        </div>
      )}
    </div>
  );
};
