import 'whatwg-fetch';

import { graphqlFetch } from './client';

jest.mock('@mlflow/mlflow/src/common/utils/FetchUtils', () => ({
  getAjaxUrl: jest.fn(),
}));

const { getAjaxUrl } = jest.requireMock('@mlflow/mlflow/src/common/utils/FetchUtils');

describe('graphqlFetch', () => {
  const originalFetch = global.fetch;
  const fetchMock = jest.fn();

  beforeEach(() => {
    fetchMock.mockResolvedValue({ ok: true });
    // @ts-expect-error assigning mock fetch
    global.fetch = fetchMock;
    getAjaxUrl.mockReset();
  });

  afterAll(() => {
    global.fetch = originalFetch;
  });

  it('prefixes graphql requests with the workspace-aware ajax url', async () => {
    const prefixedUrl = '/mlflow/workspaces/team-a/graphql';
    getAjaxUrl.mockImplementation((relativeUrl: string) => `/mlflow/workspaces/team-a/${relativeUrl}`);

    await graphqlFetch('graphql', { headers: { 'X-Test': '1' } });

    expect(getAjaxUrl).toHaveBeenCalledWith('graphql');
    expect(fetchMock).toHaveBeenCalledWith(
      prefixedUrl,
      expect.objectContaining({
        headers: expect.any(Headers),
      }),
    );
  });
});
