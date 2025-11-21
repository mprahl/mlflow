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

  it('resolves graphql requests via the ajax url helper', async () => {
    const resolvedUrl = '/graphql';
    getAjaxUrl.mockImplementation(() => resolvedUrl);

    await graphqlFetch('graphql', { headers: { 'X-Test': '1' } });

    expect(getAjaxUrl).toHaveBeenCalledWith('graphql');
    expect(fetchMock).toHaveBeenCalledWith(
      resolvedUrl,
      expect.objectContaining({
        headers: expect.any(Headers),
      }),
    );
  });
});
