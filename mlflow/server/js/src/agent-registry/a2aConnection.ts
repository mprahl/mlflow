import { z } from 'zod';

import type { AccessEndpoint } from './mockData';

const requirementSchema = z.record(z.array(z.string()));
export const cardSecuritySchema = z.object({
  securitySchemes: z.record(z.unknown()).default({}),
  security: z.array(requirementSchema).optional(),
  securityRequirements: z
    .array(
      z.object({
        schemes: z.record(z.object({ list: z.array(z.string()).default([]) })).default({}),
      }),
    )
    .optional(),
});

export const getA2ASecurityRequirements = (security: z.infer<typeof cardSecuritySchema>) =>
  security.securityRequirements?.map(({ schemes }) =>
    Object.fromEntries(Object.entries(schemes).map(([name, scopes]) => [name, scopes.list])),
  ) ??
  security.security ??
  [];

const schemeSchema = z.discriminatedUnion('type', [
  z.object({ type: z.literal('http'), scheme: z.string() }),
  z.object({ type: z.literal('apiKey'), in: z.enum(['header', 'query', 'cookie']), name: z.string().min(1) }),
  z.object({ type: z.literal('oauth2'), flows: z.record(z.object({ tokenUrl: z.string().optional() })).optional() }),
  z.object({ type: z.literal('openIdConnect'), openIdConnectUrl: z.string() }),
  z.object({ type: z.literal('mutualTLS') }),
]);

const wrappedSchemeTypes = {
  httpAuthSecurityScheme: 'http',
  apiKeySecurityScheme: 'apiKey',
  oauth2SecurityScheme: 'oauth2',
  openIdConnectSecurityScheme: 'openIdConnect',
  mtlsSecurityScheme: 'mutualTLS',
};

const parseScheme = (value: unknown) => {
  const object = z.record(z.unknown()).safeParse(value);
  if (!object.success) return schemeSchema.safeParse(value);
  const wrappers = Object.entries(wrappedSchemeTypes).filter(([key]) => key in object.data);
  if (wrappers.length > 1) return schemeSchema.safeParse(undefined);
  if (wrappers.length === 0) return schemeSchema.safeParse(value);
  const [key, type] = wrappers[0];
  const body = z.record(z.unknown()).safeParse(object.data[key]);
  return schemeSchema.safeParse(body.success ? { ...body.data, type, in: body.data['location'] } : undefined);
};

export type A2AConnectionExample = {
  id: string;
  label: string;
  code: string | null;
  notes: string[];
};

const buildExample = (
  url: string,
  requirement: Record<string, string[]>,
  schemes: Record<string, unknown>,
  id: string,
): A2AConnectionExample => {
  const names = Object.keys(requirement);
  const label = names.join(' + ') || 'No authentication';
  const notes: string[] = [];
  const headers: Record<string, string> = {};
  const params: Record<string, string> = {};
  const cookies: Record<string, string> = {};
  const setup: string[] = [];
  const options: string[] = [];
  const destinations = new Set<string>();
  let needsTls = false;
  const unavailable = (message: string): A2AConnectionExample => ({ id, label, code: null, notes: [message] });

  for (const [name, scopes] of Object.entries(requirement)) {
    const parsed = parseScheme(schemes[name]);
    if (!parsed.success) {
      return unavailable(
        `The synced card's "${name}" security scheme is missing or unsupported. Use the provider's authentication setup.`,
      );
    }
    const scheme = parsed.data;
    const prefix = `A2A_${name.replace(/[^a-zA-Z0-9]/g, '_').toUpperCase()}`;
    const environment = (suffix: string) => `os.environ[${JSON.stringify(`${prefix}_${suffix}`)}]`;
    let destination: string;

    switch (scheme.type) {
      case 'apiKey': {
        const values = { header: headers, query: params, cookie: cookies }[scheme.in];
        values[scheme.name] = environment('API_KEY');
        destination = `${scheme.in}:${scheme.in === 'header' ? scheme.name.toLowerCase() : scheme.name}`;
        notes.push(`${name}: API key in ${scheme.in} "${scheme.name}" from ${prefix}_API_KEY.`);
        if (scheme.in === 'query') notes.push('Query-string credentials can appear in URL logs.');
        break;
      }
      case 'http': {
        destination = 'header:authorization';
        if (scheme.scheme.toLowerCase() === 'bearer') {
          headers['Authorization'] = `"Bearer " + ${environment('TOKEN')}`;
          notes.push(`${name}: bearer token from ${prefix}_TOKEN.`);
        } else if (scheme.scheme.toLowerCase() === 'basic') {
          options.push(`auth=httpx.BasicAuth(${environment('USERNAME')}, ${environment('PASSWORD')})`);
          notes.push(`${name}: basic authentication from ${prefix}_USERNAME and ${prefix}_PASSWORD.`);
        } else {
          return unavailable(`HTTP ${scheme.scheme} authentication requires the provider's client setup.`);
        }
        break;
      }
      case 'oauth2':
      case 'openIdConnect': {
        destination = 'header:authorization';
        headers['Authorization'] = `"Bearer " + ${environment('ACCESS_TOKEN')}`;
        notes.push(
          `${name}: obtain an access token using the provider's ${scheme.type === 'oauth2' ? 'OAuth 2.0' : 'OpenID Connect'} flow and set ${prefix}_ACCESS_TOKEN.${scopes.length ? ` Required scopes: ${scopes.join(', ')}.` : ''}`,
        );
        if (scheme.type === 'openIdConnect') notes.push(`Discovery: ${scheme.openIdConnectUrl}`);
        else {
          for (const flow of Object.values(scheme.flows ?? {})) {
            if (flow.tokenUrl) notes.push(`Token endpoint: ${flow.tokenUrl}`);
          }
        }
        break;
      }
      case 'mutualTLS': {
        destination = 'tls';
        needsTls = true;
        setup.push(
          'tls = ssl.create_default_context()',
          `tls.load_cert_chain(${environment('CERT_FILE')}, keyfile=${environment('KEY_FILE')})`,
        );
        options.push('verify=tls');
        notes.push(
          `${name}: client certificate and private key paths from ${prefix}_CERT_FILE and ${prefix}_KEY_FILE.`,
        );
        break;
      }
    }
    if (destinations.has(destination)) {
      return unavailable(
        "This combination uses conflicting credential locations. Use the provider's authentication setup.",
      );
    }
    destinations.add(destination);
  }

  for (const [option, values] of Object.entries({ headers, params, cookies })) {
    if (Object.keys(values).length) {
      options.push(
        `${option}={${Object.entries(values)
          .map(([key, value]) => `${JSON.stringify(key)}: ${value}`)
          .join(', ')}}`,
      );
    }
  }
  if (!names.length) notes.push('No authentication requirements are declared in the synced card.');
  const code = `import asyncio${names.length ? '\nimport os' : ''}${needsTls ? '\nimport ssl' : ''}

import httpx
from a2a.client import ClientConfig, create_client
from a2a.helpers import new_text_message
from a2a.types import Role, SendMessageRequest


async def main():
${setup.map((line) => `    ${line}\n`).join('')}    async with httpx.AsyncClient(
${options.map((line) => `        ${line},\n`).join('')}        timeout=60.0,
    ) as http_client:
        client = await create_client(
            agent=${JSON.stringify(url)},
            client_config=ClientConfig(httpx_client=http_client, streaming=False),
        )
        request = SendMessageRequest(
            message=new_text_message("How can you help me?", role=Role.ROLE_USER)
        )
        try:
            async for response in client.send_message(request):
                print(response)
        finally:
            await client.close()


asyncio.run(main())`;
  return { id, label, code, notes };
};

export const getA2AConnectionExamples = ({
  url,
  card,
}: Pick<AccessEndpoint, 'url' | 'card'>): A2AConnectionExample[] => {
  if (!card) {
    return [
      {
        ...buildExample(url, {}, {}, '0'),
        label: 'Unknown',
        notes: ['No synced Agent Card is available. Confirm authentication requirements with the endpoint provider.'],
      },
    ];
  }
  const parsed = cardSecuritySchema.safeParse(card);
  if (!parsed.success) {
    return [
      {
        id: '0',
        label: 'Unknown',
        code: null,
        notes: [
          'The synced card has invalid security requirements. Confirm authentication with the endpoint provider.',
        ],
      },
    ];
  }
  const security = parsed.data;
  const requirements = getA2ASecurityRequirements(security);
  return (requirements.length ? requirements : [{}]).map((requirement, index) =>
    buildExample(url, requirement, security.securitySchemes, String(index)),
  );
};
