import { Button, CopyIcon, Drawer, Tabs, Typography, useDesignSystemTheme } from '@databricks/design-system';
import { FormattedMessage } from 'react-intl';
import { CodeSnippet } from '@databricks/web-shared/snippet';

import { CopyButton } from '../shared/building_blocks/CopyButton';

const shellQuote = (value: string) => `'${value.replaceAll("'", `'"'"'`)}'`;

const SetupCopyBox = ({ text, componentId }: { text: string; componentId: string }) => {
  const { theme } = useDesignSystemTheme();
  return (
    <div
      css={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: theme.spacing.sm,
        padding: `${theme.spacing.sm}px ${theme.spacing.md}px`,
        border: `1px solid ${theme.colors.border}`,
        borderRadius: theme.borders.borderRadiusMd,
        backgroundColor: theme.colors.backgroundSecondary,
      }}
    >
      <Typography.Text css={{ minWidth: 0, flex: 1, whiteSpace: 'pre-wrap', fontFamily: 'monospace' }}>
        {text}
      </Typography.Text>
      <CopyButton
        componentId={componentId}
        copyText={text}
        showLabel={false}
        size="small"
        type="tertiary"
        icon={<CopyIcon />}
        aria-label="Copy agent tracing setup"
      />
    </div>
  );
};

export const AgentTracingSetup = ({ agentId }: { agentId: string }) => {
  const { theme } = useDesignSystemTheme();
  const trackingUri = typeof window !== 'undefined' ? window.location.origin : 'http://localhost:5000';
  const cliSetup = `export MLFLOW_TRACKING_URI=${shellQuote(trackingUri)}\nexport MLFLOW_AGENT_ID=${shellQuote(agentId)}`;
  const agentPrompt = `Add MLflow tracing to my instrumented application. Use tracking server ${trackingUri} and the existing agent ID ${agentId}. Select that agent with mlflow.set_agent(agent_id=${JSON.stringify(agentId)}) or MLFLOW_AGENT_ID. Run one traced operation and confirm that it appears under the agent. Do not create a new experiment or put credentials in the repository.`;
  const pythonSetup = `import mlflow\n\nmlflow.set_tracking_uri(${JSON.stringify(trackingUri)})\nmlflow.set_agent(agent_id=${JSON.stringify(agentId)})\n\n@mlflow.trace\ndef answer(question):\n    return f"Answer: {question}"\n\nanswer("Hello")`;

  return (
    <div css={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
      <Tabs.Root componentId="mlflow.agent_registry.tracing_setup.tabs" defaultValue="cli">
        <Tabs.List tabListCss={{ marginBottom: theme.spacing.mid }}>
          <Tabs.Trigger value="cli">
            <FormattedMessage defaultMessage="CLI" description="Agent tracing setup CLI tab" />
          </Tabs.Trigger>
          <Tabs.Trigger value="ai-assistant">
            <FormattedMessage defaultMessage="AI Assistant" description="Agent tracing setup AI assistant tab" />
          </Tabs.Trigger>
        </Tabs.List>
        <Tabs.Content value="cli">
          <Typography.Text color="secondary" size="sm">
            Set these variables, then run your instrumented agent application.
          </Typography.Text>
          <div css={{ marginTop: theme.spacing.sm }}>
            <SetupCopyBox componentId="mlflow.agent_registry.tracing_setup.cli.copy" text={cliSetup} />
          </div>
        </Tabs.Content>
        <Tabs.Content value="ai-assistant">
          <div css={{ display: 'flex', flexDirection: 'column', gap: theme.spacing.sm }}>
            <Typography.Text color="secondary" size="sm">
              Copy this prompt into Claude, Cursor, Copilot, or another coding agent.
            </Typography.Text>
            <SetupCopyBox componentId="mlflow.agent_registry.tracing_setup.ai.copy" text={agentPrompt} />
          </div>
        </Tabs.Content>
      </Tabs.Root>
      <Drawer.Root>
        <Drawer.Trigger>
          <Button
            componentId="mlflow.agent_registry.tracing_setup.manual.open"
            type="tertiary"
            size="small"
            css={{ alignSelf: 'flex-end', marginTop: theme.spacing.sm }}
          >
            <FormattedMessage
              defaultMessage="Set up manually instead"
              description="Agent tracing manual setup action"
            />
          </Button>
        </Drawer.Trigger>
        <Drawer.Content
          componentId="mlflow.agent_registry.tracing_setup.manual"
          width="70vw"
          title={<FormattedMessage defaultMessage="Set up tracing" description="Agent tracing manual setup title" />}
        >
          <div css={{ display: 'flex', flexDirection: 'column', gap: theme.spacing.lg }}>
            <div>
              <Typography.Title level={4}>1. Connect to the tracking server and select this agent</Typography.Title>
              <Typography.Text color="secondary">
                The agent ID keeps tracing attached to this agent. A snapshot is optional.
              </Typography.Text>
            </div>
            <div css={{ position: 'relative' }}>
              <CopyButton
                componentId="mlflow.agent_registry.tracing_setup.python.copy"
                copyText={pythonSetup}
                showLabel={false}
                icon={<CopyIcon />}
                aria-label="Copy Python tracing example"
                css={{ position: 'absolute', right: theme.spacing.sm, top: theme.spacing.sm, zIndex: 1 }}
              />
              <CodeSnippet language="python" theme={theme.isDarkMode ? 'duotoneDark' : 'light'}>
                {pythonSetup}
              </CodeSnippet>
            </div>
            <div>
              <Typography.Title level={4}>2. Run your agent</Typography.Title>
              <Typography.Text>Open Traces to verify that the first request appears under this agent.</Typography.Text>
            </div>
          </div>
        </Drawer.Content>
      </Drawer.Root>
    </div>
  );
};
