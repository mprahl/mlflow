import { describe, expect, it } from '@jest/globals';

import { getA2AConnectionExamples } from './a2aConnection';

const url = 'https://agents.example.com/support';
const example = (scheme: unknown) =>
  getA2AConnectionExamples({
    url,
    card: { securitySchemes: { credential: scheme }, security: [{ credential: [] }] },
  })[0];

describe('A2A connection security', () => {
  it.each([
    { securitySchemes: { credential: { type: 'http', scheme: 'bearer' } }, security: [{ credential: [] }] },
    {
      securitySchemes: { credential: { httpAuthSecurityScheme: { scheme: 'Bearer' } } },
      securityRequirements: [{ schemes: { credential: { list: [] } } }],
    },
  ])('supports legacy and current bearer card formats', (card) => {
    const result = getA2AConnectionExamples({ url, card })[0];
    expect(result.code).toContain('"Authorization": "Bearer " + os.environ["A2A_CREDENTIAL_TOKEN"]');
    expect(result.code).not.toContain('os.environ.get');
  });

  it.each([
    ['header', 'headers'],
    ['query', 'params'],
    ['cookie', 'cookies'],
  ])('places API keys in %s', (location, option) => {
    expect(example({ apiKeySecurityScheme: { location, name: 'X-API-Key' } }).code).toContain(
      `${option}={"X-API-Key": os.environ["A2A_CREDENTIAL_API_KEY"]}`,
    );
  });

  it('uses HTTPX basic authentication', () => {
    expect(example({ type: 'http', scheme: 'basic' }).code).toContain(
      'auth=httpx.BasicAuth(os.environ["A2A_CREDENTIAL_USERNAME"], os.environ["A2A_CREDENTIAL_PASSWORD"])',
    );
  });

  it('keeps OAuth scopes and credential acquisition outside the snippet', () => {
    const [result] = getA2AConnectionExamples({
      url,
      card: {
        securitySchemes: {
          oauth: {
            oauth2SecurityScheme: { flows: { clientCredentials: { tokenUrl: 'https://issuer.example/token' } } },
          },
        },
        securityRequirements: [{ schemes: { oauth: { list: ['agent:invoke'] } } }],
      },
    });
    expect(result.code).toContain('A2A_OAUTH_ACCESS_TOKEN');
    expect(result.notes.join(' ')).toContain('agent:invoke');
    expect(result.notes.join(' ')).toContain('https://issuer.example/token');
    expect(result.code).not.toContain('client_secret');
  });

  it('supports OIDC access tokens and mutual TLS without disabling verification', () => {
    const oidc = example({
      type: 'openIdConnect',
      openIdConnectUrl: 'https://issuer.example/.well-known/openid-configuration',
    });
    expect(oidc.code).toContain('A2A_CREDENTIAL_ACCESS_TOKEN');
    expect(oidc.notes.join(' ')).toContain('https://issuer.example/.well-known/openid-configuration');
    const mtls = example({ mtlsSecurityScheme: {} });
    expect(mtls.code).toContain('ssl.create_default_context()');
    expect(mtls.code).toContain('tls.load_cert_chain');
    expect(mtls.code).toContain('verify=tls');
  });

  it('keeps alternatives separate and combines requirements within an alternative', () => {
    const securitySchemes = {
      token: { type: 'http', scheme: 'bearer' },
      key: { type: 'apiKey', in: 'header', name: 'X-API-Key' },
    };
    const alternatives = getA2AConnectionExamples({
      url,
      card: { securitySchemes, security: [{ token: [] }, { key: [] }, {}] },
    });
    expect(alternatives).toHaveLength(3);
    expect(alternatives[0].code).not.toContain('X-API-Key');
    expect(alternatives[1].code).not.toContain('Authorization');
    expect(alternatives[2].code).not.toContain('import os');
    const [combined] = getA2AConnectionExamples({ url, card: { securitySchemes, security: [{ token: [], key: [] }] } });
    expect(combined.code).toContain('Authorization');
    expect(combined.code).toContain('X-API-Key');
  });

  it.each([
    { security: [{ missing: [] }] },
    { security: 'invalid' },
    { securitySchemes: { custom: { type: 'custom' } }, security: [{ custom: [] }] },
    { securitySchemes: { key: { type: 'apiKey', in: 'invalid', name: 'key' } }, security: [{ key: [] }] },
  ])('does not silently omit unsupported or malformed requirements', (card) => {
    expect(getA2AConnectionExamples({ url, card })[0].code).toBeNull();
  });

  it('rejects combinations that overwrite credentials', () => {
    expect(
      getA2AConnectionExamples({
        url,
        card: {
          securitySchemes: { token: { type: 'http', scheme: 'bearer' }, basic: { type: 'http', scheme: 'basic' } },
          security: [{ token: [], basic: [] }],
        },
      })[0].code,
    ).toBeNull();
  });

  it('does not assume bearer auth when the card is missing or anonymous', () => {
    const [missing] = getA2AConnectionExamples({ url });
    expect(missing.notes.join(' ')).toContain('No synced Agent Card');
    expect(missing.code).not.toContain('Authorization');
    expect(getA2AConnectionExamples({ url, card: { security: [] } })[0].label).toBe('No authentication');
  });
});
