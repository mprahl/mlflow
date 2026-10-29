import fleetFixtures from './mockFleets.json';

export type Environment = 'dev' | 'staging' | 'prod' | 'unassigned';
export type AssetKind = 'prompt' | 'skill' | 'plugin' | 'mcp' | 'model' | 'tool';

export type AgentAsset = {
  kind: AssetKind;
  name: string;
  version: string;
  href?: string;
};

export type AgentSnapshot = {
  id: number;
  publisherVersion: string;
  createdAt: string;
  source: string;
  sourceUrl: string;
  harness: string;
  assets: AgentAsset[];
  quality: number;
  latency: number;
  cost: number;
};

export type AccessEndpoint = {
  name: string;
  protocol: 'A2A' | 'MCP' | 'Custom';
  transport: string;
  url: string;
  advertisedVersion: string;
  capabilities: string[];
  syncStatus: 'synced' | 'failed' | 'manual';
  lastSynced?: string;
  failure?: string;
  card?: Record<string, unknown>;
};

export type AgentEvent = {
  instanceId?: string;
  id: string;
  at: string;
  title: string;
  detail: string;
  href?: string;
  environment?: Environment;
  snapshotId?: number;
  kind: 'snapshot' | 'evaluation' | 'trace' | 'sync';
};

export type AgentTrace = {
  id: string;
  at: string;
  environment: Environment;
  snapshotId?: number;
  session: string;
  latency: string;
  cost: string;
  status: 'OK' | 'Error';
  relatedAgentId?: string;
  observedTools?: string[];
};

export type AgentEvaluation = {
  id: string;
  at: string;
  environment: Environment;
  snapshotId: number;
  quality: number;
  cases: number;
};

export type RelatedAgent = {
  agentId: string;
  relationship: 'Declared dependency' | 'Observed outbound call';
  evidence: string;
};

export type Agent = {
  id: string;
  name: string;
  description: string;
  owner: string;
  capabilities: string[];
  snapshots: AgentSnapshot[];
  endpoints: AccessEndpoint[];
  events: AgentEvent[];
  evaluations: AgentEvaluation[];
  traces: AgentTrace[];
  related: RelatedAgent[];
};

export type Fleet = {
  id: string;
  name: string;
  description: string;
  members: { agentId: string; environment?: Environment }[];
};

const supportAssets7: AgentAsset[] = [
  { kind: 'prompt', name: 'support-system', version: '3', href: '/prompts/support-system?promptVersion=3' },
  { kind: 'skill', name: 'refund-policy', version: '2', href: '/skills/refund-policy?version=2' },
  {
    kind: 'mcp',
    name: 'order-lookup',
    version: '1.0.0',
    href: '/mcp-registry/com.example%2Forder-lookup?version=1.0.0',
  },
  { kind: 'tool', name: 'lookup_order', version: '1' },
  { kind: 'model', name: 'GPT 6.1 Sol', version: 'provider model' },
];

const supportAssets8: AgentAsset[] = [
  { kind: 'prompt', name: 'support-system', version: '4', href: '/prompts/support-system?promptVersion=4' },
  { kind: 'skill', name: 'refund-policy', version: '2', href: '/skills/refund-policy?version=2' },
  {
    kind: 'mcp',
    name: 'order-lookup',
    version: '1.0.0',
    href: '/mcp-registry/com.example%2Forder-lookup?version=1.0.0',
  },
  { kind: 'skill', name: 'incident-triage', version: '3', href: '/skills/incident-triage?version=3' },
  { kind: 'skill', name: 'documentation-research', version: '3', href: '/skills/documentation-research?version=3' },
  {
    kind: 'mcp',
    name: 'github-tools',
    version: '2.0.0',
    href: '/mcp-registry/com.example%2Fgithub-tools?version=2.0.0',
  },
  { kind: 'mcp', name: 'docs-search', version: '2.1.0', href: '/mcp-registry/com.example%2Fdocs-search?version=2.1.0' },
  { kind: 'tool', name: 'lookup_order', version: '1' },
  { kind: 'model', name: 'GPT 6.1 Sol', version: 'provider model' },
];

export const agents: Agent[] = [
  {
    id: 'support-agent',
    name: 'Support agent',
    description: 'Answers customer questions and coordinates refunds and order lookups.',
    owner: 'Customer Experience',
    capabilities: ['Answer order questions', 'Handle refund requests', 'Escalate complex cases'],
    snapshots: [
      {
        id: 9,
        publisherVersion: '2.4.0',
        createdAt: '2026-10-27T14:00:00Z',
        source: '8c17be4',
        sourceUrl: 'https://github.com/mlflow/mlflow/commit/8c17be4',
        harness: 'LangGraph 1.0',
        assets: [
          ...supportAssets8.map((asset) => {
            if (asset.kind === 'skill' && asset.name === 'refund-policy') {
              return { ...asset, version: '3', href: '/skills/refund-policy?version=3' };
            }
            if (asset.kind === 'mcp' && asset.name === 'order-lookup') {
              return { ...asset, version: '1.2.0', href: '/mcp-registry/com.example%2Forder-lookup?version=1.2.0' };
            }
            return asset;
          }),
          { kind: 'tool', name: 'prepare_refund', version: '1' },
        ],
        quality: 91,
        latency: 1.8,
        cost: 0.014,
      },
      {
        id: 8,
        publisherVersion: '2.4.0',
        createdAt: '2026-10-21T13:08:00Z',
        source: '8c17be4',
        sourceUrl: 'https://github.com/mlflow/mlflow/commit/8c17be4',
        harness: 'LangGraph 1.0',
        assets: supportAssets8,
        quality: 91,
        latency: 1.8,
        cost: 0.014,
      },
      {
        id: 7,
        publisherVersion: '2.4.0',
        createdAt: '2026-10-18T09:30:00Z',
        source: '8c17be4',
        sourceUrl: 'https://github.com/mlflow/mlflow/commit/8c17be4',
        harness: 'LangGraph 1.0',
        assets: supportAssets7,
        quality: 88,
        latency: 1.5,
        cost: 0.009,
      },
      {
        id: 6,
        publisherVersion: '2.3.1',
        createdAt: '2026-10-10T15:11:00Z',
        source: '7a12f2b',
        sourceUrl: 'https://github.com/mlflow/mlflow/commit/7a12f2b',
        harness: 'LangGraph 0.9',
        assets: supportAssets7.filter((asset) => asset.kind !== 'skill'),
        quality: 84,
        latency: 1.4,
        cost: 0.008,
      },
    ],
    endpoints: [
      {
        name: 'production',
        protocol: 'A2A',
        transport: 'HTTPS',
        url: 'https://agents.example.com/support',
        advertisedVersion: '2.4.0',
        capabilities: ['Answer order questions', 'Handle refund requests'],
        syncStatus: 'failed',
        lastSynced: '2026-10-20T10:00:00Z',
        failure: 'The A2A card could not be fetched. Showing the last synced card.',
        card: {
          name: 'Support agent',
          description: 'Customer support and refund assistance',
          version: '2.4.0',
          protocolVersion: '0.3',
          url: 'https://agents.example.com/support',
          provider: { organization: 'Customer Experience' },
          defaultInputModes: ['text/plain'],
          defaultOutputModes: ['text/plain'],
          capabilities: { streaming: true, pushNotifications: false },
          securitySchemes: {
            bearerAuth: { type: 'http', scheme: 'bearer', bearerFormat: 'JWT' },
            apiKeyAuth: { type: 'apiKey', in: 'header', name: 'X-API-Key' },
          },
          security: [{ bearerAuth: [] }, { apiKeyAuth: [] }],
          skills: [
            {
              id: 'order-help',
              name: 'Answer order questions',
              description: 'Look up order status and delivery details.',
              tags: ['orders', 'support'],
              examples: ['Where is my order?'],
            },
            {
              id: 'refunds',
              name: 'Handle refund requests',
              description: 'Check eligibility and coordinate a refund decision.',
              tags: ['refunds', 'policy'],
              examples: ['Can I return an item delivered yesterday?'],
            },
          ],
        },
      },
      {
        name: 'staging',
        protocol: 'A2A',
        transport: 'HTTPS',
        url: 'https://staging.agents.example.com/support',
        advertisedVersion: '2.4.0',
        capabilities: ['Answer order questions', 'Handle refund requests'],
        syncStatus: 'synced',
        lastSynced: '2026-10-21T14:20:00Z',
      },
    ],
    events: [
      {
        id: 'e1',
        at: '2026-10-22T09:10:00Z',
        title: 'A2A sync failed',
        detail: 'Production card is showing its last synced copy.',
        kind: 'sync',
      },
      {
        id: 'e2',
        at: '2026-10-21T15:30:00Z',
        title: 'Snapshot 8 evaluated',
        detail: 'Quality 91% · 124 cases',
        environment: 'staging',
        snapshotId: 8,
        kind: 'evaluation',
      },
      {
        id: 'e3',
        at: '2026-10-21T13:08:00Z',
        title: 'Snapshot 8 created',
        detail: 'System prompt changed from version 3 to 4.',
        snapshotId: 8,
        kind: 'snapshot',
      },
      {
        id: 'e4',
        at: '2026-10-20T08:40:00Z',
        title: 'Snapshot 7 first observed',
        detail: 'Production traces now reference snapshot 7.',
        environment: 'prod',
        snapshotId: 7,
        kind: 'trace',
      },
      {
        id: 'e6',
        at: '2026-10-18T16:05:00Z',
        title: 'Snapshot 7 evaluated',
        detail: 'Quality 88% · 116 cases',
        environment: 'dev',
        snapshotId: 7,
        kind: 'evaluation',
      },
      {
        id: 'e7',
        at: '2026-10-18T09:30:00Z',
        title: 'Snapshot 7 created',
        detail: 'Refund policy skill added.',
        snapshotId: 7,
        kind: 'snapshot',
      },
    ],
    evaluations: [
      {
        id: 'eval-support-staging-8',
        at: '2026-10-21T15:30:00Z',
        environment: 'staging',
        snapshotId: 8,
        quality: 91,
        cases: 124,
      },
      {
        id: 'eval-support-dev-7',
        at: '2026-10-18T16:05:00Z',
        environment: 'dev',
        snapshotId: 7,
        quality: 88,
        cases: 116,
      },
      {
        id: 'eval-support-prod-7',
        at: '2026-10-20T11:15:00Z',
        environment: 'prod',
        snapshotId: 7,
        quality: 86,
        cases: 220,
      },
    ],
    traces: [
      {
        id: 'tr-support-01',
        at: '2026-10-22T08:42:00Z',
        environment: 'prod',
        snapshotId: 7,
        session: 'Customer refund request',
        latency: '1.9 s',
        cost: '$0.012',
        status: 'OK',
        relatedAgentId: 'refund-agent',
        observedTools: ['lookup_order'],
      },
      {
        id: 'tr-support-02',
        at: '2026-10-21T16:14:00Z',
        environment: 'staging',
        snapshotId: 8,
        session: 'Order delivery question',
        latency: '1.7 s',
        cost: '$0.014',
        status: 'OK',
        observedTools: ['lookup_order'],
      },
      {
        id: 'tr-support-03',
        at: '2026-10-21T15:05:00Z',
        environment: 'staging',
        snapshotId: 8,
        session: 'Missing order lookup',
        latency: '2.8 s',
        cost: '$0.019',
        status: 'Error',
      },
      {
        id: 'tr-support-04',
        at: '2026-10-18T17:21:00Z',
        environment: 'dev',
        snapshotId: 7,
        session: 'Refund eligibility test',
        latency: '1.4 s',
        cost: '$0.008',
        status: 'OK',
        relatedAgentId: 'refund-agent',
      },
    ],
    related: [
      { agentId: 'refund-agent', relationship: 'Declared dependency', evidence: 'Declared in snapshot 8' },
      { agentId: 'refund-agent', relationship: 'Observed outbound call', evidence: '2 supporting traces' },
    ],
  },
  {
    id: 'refund-agent',
    name: 'Refund agent',
    description: 'Checks policy and prepares refund decisions for customer support.',
    owner: 'Payments',
    capabilities: ['Check refund eligibility', 'Prepare refund decision'],
    snapshots: [
      {
        id: 3,
        publisherVersion: '1.2.0',
        createdAt: '2026-10-16T11:00:00Z',
        source: 'a4e9d11',
        sourceUrl: 'https://github.com/mlflow/mlflow/commit/a4e9d11',
        harness: 'LangGraph 1.0',
        assets: [
          { kind: 'skill', name: 'refund-policy', version: '2', href: '/skills/refund-policy?version=2' },
          {
            kind: 'mcp',
            name: 'order-lookup',
            version: '1.2.0',
            href: '/mcp-registry/com.example%2Forder-lookup?version=1.2.0',
          },
          {
            kind: 'mcp',
            name: 'postgres-query',
            version: '1.1.0',
            href: '/mcp-registry/com.example%2Fpostgres-query?version=1.1.0',
          },
        ],
        quality: 94,
        latency: 1.1,
        cost: 0.006,
      },
    ],
    endpoints: [
      {
        name: 'production',
        protocol: 'MCP',
        transport: 'Streamable HTTP',
        url: 'https://agents.example.com/refunds/mcp',
        advertisedVersion: '1.2.0',
        capabilities: ['Check refund eligibility'],
        syncStatus: 'manual',
      },
    ],
    events: [
      {
        id: 'r1',
        at: '2026-10-16T11:00:00Z',
        title: 'Snapshot 3 created',
        detail: 'Refund policy skill version 2 pinned.',
        snapshotId: 3,
        kind: 'snapshot',
      },
    ],
    evaluations: [
      {
        id: 'eval-refund-prod-3',
        at: '2026-10-20T12:00:00Z',
        environment: 'prod',
        snapshotId: 3,
        quality: 94,
        cases: 180,
      },
    ],
    traces: [
      {
        id: 'tr-refund-01',
        at: '2026-10-22T08:42:10Z',
        environment: 'prod',
        snapshotId: 3,
        session: 'Refund eligibility',
        latency: '1.1 s',
        cost: '$0.006',
        status: 'OK',
      },
    ],
    related: [],
  },
  {
    id: 'knowledge-agent',
    name: 'Knowledge agent',
    description: 'Searches internal product documentation and returns cited answers.',
    owner: 'Developer Experience',
    capabilities: ['Search documentation', 'Summarize release notes'],
    snapshots: [
      {
        id: 2,
        publisherVersion: '0.8.0',
        createdAt: '2026-10-14T10:00:00Z',
        source: '4fc730a',
        sourceUrl: 'https://github.com/mlflow/mlflow/commit/4fc730a',
        harness: 'Claude Code 2.0',
        assets: [
          {
            kind: 'mcp',
            name: 'docs-search',
            version: '2.0.0',
            href: '/mcp-registry/com.example%2Fdocs-search?version=2.0.0',
          },
          {
            kind: 'skill',
            name: 'documentation-research',
            version: '3',
            href: '/skills/documentation-research?version=3',
          },
          { kind: 'skill', name: 'code-review', version: '3', href: '/skills/code-review?version=3' },
          {
            kind: 'mcp',
            name: 'github-tools',
            version: '2.0.0',
            href: '/mcp-registry/com.example%2Fgithub-tools?version=2.0.0',
          },
        ],
        quality: 86,
        latency: 2.2,
        cost: 0.011,
      },
    ],
    endpoints: [],
    events: [
      {
        id: 'k1',
        at: '2026-10-14T10:00:00Z',
        title: 'Snapshot 2 created',
        detail: 'Documentation search MCP server pinned.',
        snapshotId: 2,
        kind: 'snapshot',
      },
    ],
    evaluations: [
      {
        id: 'eval-knowledge-dev-2',
        at: '2026-10-20T14:00:00Z',
        environment: 'dev',
        snapshotId: 2,
        quality: 86,
        cases: 90,
      },
    ],
    traces: [
      {
        id: 'tr-knowledge-01',
        at: '2026-10-20T13:21:00Z',
        environment: 'dev',
        snapshotId: 2,
        session: 'Release notes question',
        latency: '2.2 s',
        cost: '$0.011',
        status: 'OK',
      },
    ],
    related: [],
  },
  {
    id: 'starter-agent',
    name: 'Starter agent',
    description: 'A newly registered agent waiting for its first trace.',
    owner: 'Developer Experience',
    capabilities: ['Answer product questions'],
    snapshots: [],
    endpoints: [],
    events: [],
    evaluations: [],
    traces: [],
    related: [],
  },
];

export const fleets = fleetFixtures as Fleet[];

export const getAgent = (id?: string) => agents.find((agent) => agent.id === id);
export const getFleet = (id?: string) => fleets.find((fleet) => fleet.id === id);
