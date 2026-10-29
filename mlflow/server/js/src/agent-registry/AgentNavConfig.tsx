import type { ReactNode } from 'react';
import {
  ChartLineIcon,
  ForkHorizontalIcon,
  McpIcon,
  ModelsIcon,
  PlusMinusSquareIcon,
  PuzzleIcon,
} from '@databricks/design-system';

export type AgentNavItem = { key: string; label: string; icon: ReactNode };

export const agentNavSections: { label: string; items: AgentNavItem[] }[] = [
  { label: '', items: [{ key: 'overview', label: 'Overview', icon: <ChartLineIcon /> }] },
  {
    label: 'Observability',
    items: [
      { key: 'traces', label: 'Traces', icon: <ForkHorizontalIcon /> },
      { key: 'dashboard', label: 'Dashboard', icon: <ChartLineIcon /> },
    ],
  },
  {
    label: 'Evaluation',
    items: [{ key: 'evaluation-runs', label: 'Evaluation runs', icon: <PlusMinusSquareIcon /> }],
  },
  {
    label: 'Composition & access',
    items: [
      { key: 'snapshots', label: 'Agent snapshots', icon: <ModelsIcon /> },
      { key: 'assets', label: 'AI assets', icon: <PuzzleIcon /> },
      { key: 'endpoints', label: 'Access endpoints', icon: <McpIcon /> },
    ],
  },
];

const assetLabels = {
  prompts: 'Prompts',
  tools: 'Tools',
  skills: 'Skills',
  plugins: 'Agent plugins',
  'mcp-servers': 'MCP servers',
  models: 'Models',
};

export const getAgentSectionLabel = (section: string) =>
  agentNavSections.flatMap(({ items }) => items).find((item) => item.key === section)?.label ??
  assetLabels[section as keyof typeof assetLabels] ??
  'Overview';
