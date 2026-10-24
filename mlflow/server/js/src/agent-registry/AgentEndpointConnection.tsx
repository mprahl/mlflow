import { useState } from 'react';
import {
  CopyIcon,
  SimpleSelect,
  SimpleSelectOption,
  Typography,
  useDesignSystemTheme,
} from '@databricks/design-system';
import { CodeSnippet } from '@databricks/web-shared/snippet';

import { CopyButton } from '../shared/building_blocks/CopyButton';
import { ConnectionInstructions } from '../mcp-registry/components/ConnectionInstructions';
import { deriveClientName } from '../mcp-registry/installInstructions';
import { overlayButtonStyles } from '../mcp-registry/styles';
import { ConnectionSource, TransportType } from '../mcp-registry/types';
import type { AccessEndpoint } from './mockData';
import { getA2AConnectionExamples } from './a2aConnection';

const ConnectionCode = ({ code, language }: { code: string; language: 'text' | 'python' }) => {
  const { theme } = useDesignSystemTheme();
  return (
    <div css={{ position: 'relative', minWidth: 0 }}>
      <CopyButton
        componentId="mlflow.agent_registry.endpoint.copy_code"
        aria-label={language === 'python' ? 'Copy Python example' : 'Copy install command'}
        copyText={code}
        showLabel={false}
        icon={<CopyIcon />}
        css={overlayButtonStyles(theme)}
      />
      <CodeSnippet
        language={language}
        theme={theme.isDarkMode ? 'duotoneDark' : 'light'}
        style={{ padding: theme.spacing.md, paddingRight: theme.spacing.xl + theme.spacing.sm }}
      >
        {code}
      </CodeSnippet>
    </div>
  );
};

export const AgentEndpointConnection = ({ endpoint, agentId }: { endpoint: AccessEndpoint; agentId: string }) => {
  const { theme } = useDesignSystemTheme();
  const [authentication, setAuthentication] = useState('0');
  if (endpoint.protocol === 'MCP') {
    return (
      <ConnectionInstructions
        source={ConnectionSource.REMOTE}
        derivedName={deriveClientName(`${agentId}-${endpoint.name}`)}
        remote={{
          type: endpoint.transport.toLowerCase() === 'sse' ? TransportType.SSE : TransportType.STREAMABLE_HTTP,
          url: endpoint.url,
        }}
      />
    );
  }
  if (endpoint.protocol !== 'A2A') {
    return (
      <Typography.Text color="secondary">
        Use the endpoint provider's client and authentication requirements.
      </Typography.Text>
    );
  }

  const examples = getA2AConnectionExamples(endpoint);
  const selected = examples.find((example) => example.id === authentication) ?? examples[0];

  return (
    <div css={{ display: 'flex', flexDirection: 'column', gap: theme.spacing.md, minWidth: 0 }}>
      <div css={{ display: 'flex', alignItems: 'center', gap: theme.spacing.md, flexWrap: 'wrap' }}>
        <Typography.Text bold>Python SDK</Typography.Text>
        <Typography.Link
          componentId="mlflow.agent_registry.endpoint.a2a_docs"
          href="https://a2a-protocol.org/latest/tutorials/python/6-interact-with-server/"
          openInNewTab
        >
          A2A documentation
        </Typography.Link>
        <Typography.Link
          componentId="mlflow.agent_registry.endpoint.a2a_inspector"
          href="https://github.com/a2aproject/a2a-inspector"
          openInNewTab
        >
          Agent Inspector
        </Typography.Link>
      </div>
      <ConnectionCode language="text" code='pip install "a2a-sdk>=1.2.2,<2" httpx' />
      <div css={{ display: 'flex', alignItems: 'center', gap: theme.spacing.sm, flexWrap: 'wrap' }}>
        <Typography.Text bold>Authentication</Typography.Text>
        {examples.length > 1 ? (
          <SimpleSelect
            componentId="mlflow.agent_registry.endpoint.authentication"
            id="mlflow.agent_registry.endpoint.authentication"
            aria-label="Authentication"
            value={selected.id}
            onChange={(event) => setAuthentication(event.target.value)}
            css={{ minWidth: 180, maxWidth: '100%' }}
          >
            {examples.map((example) => (
              <SimpleSelectOption key={example.id} value={example.id}>
                {example.label}
              </SimpleSelectOption>
            ))}
          </SimpleSelect>
        ) : (
          <Typography.Text>{selected.label}</Typography.Text>
        )}
      </div>
      {selected.notes.map((note) => (
        <Typography.Text key={note} color="secondary" size="sm" css={{ overflowWrap: 'anywhere' }}>
          {note}
        </Typography.Text>
      ))}
      {selected.code && <ConnectionCode language="python" code={selected.code} />}
      <Typography.Text size="sm" color="secondary">
        Authentication follows the last synced card and may have changed. The SDK resolves the live Agent Card at
        connection time.
      </Typography.Text>
    </div>
  );
};
