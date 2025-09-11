const { createProxyMiddleware } = require('http-proxy-middleware');

// eslint-disable-next-line
module.exports = function (app) {
  // The MLflow Gunicorn server is running on port 5000, so we should redirect server requests
  // (eg /ajax-api) to that port.
  // Exception: If the caller has specified an MLFLOW_PROXY, we instead forward server requests
  // there.
  // eslint-disable-next-line no-undef
  const defaultProxyTarget = process.env.MLFLOW_PROXY || 'http://localhost:5000/';
  // eslint-disable-next-line no-undef
  const defaultProxyStaticTarget = process.env.MLFLOW_STATIC_PROXY || defaultProxyTarget;

  // Load namespace mapping JSON without using fs/path to satisfy linter
  let namespaceToUrl = {};
  try {
    // eslint-disable-next-line global-require, import/no-dynamic-require
    namespaceToUrl = require('../public/namespaces.json');
  } catch (e) {
    // eslint-disable-next-line no-console
    console.warn('namespaces.json not found or invalid, proxy will use defaults.');
  }

  const resolveTargetFromRequest = (req, fallbackTarget) => {
    try {
      // Prefer namespace from Express path params if available (e.g. '/:namespace/ajax-api')
      const pathSeg = (req.params && req.params.namespace) || '';
      if (pathSeg && namespaceToUrl[pathSeg]) {
        const target = String(namespaceToUrl[pathSeg]);
        const sanitized = target.split('#')[0];
        return /\/$/.test(sanitized) ? sanitized : `${sanitized}/`;
      }
      // Fallback: infer from Referer path: http://host/<namespace>/#/...
      const referer = req.headers.referer || '';
      if (referer) {
        const url = new URL(referer);
        const refSeg = (url.pathname || '/').split('/').filter(Boolean)[0];
        if (refSeg && namespaceToUrl[refSeg]) {
          const target = String(namespaceToUrl[refSeg]);
          const sanitized = target.split('#')[0];
          return /\/$/.test(sanitized) ? sanitized : `${sanitized}/`;
        }
      }
    } catch (e) {
      // ignore
    }
    return fallbackTarget;
  };
  app.use(
    createProxyMiddleware('/ajax-api', {
      target: defaultProxyTarget,
      secure: false,
      changeOrigin: true,
      router: (req) => resolveTargetFromRequest(req, defaultProxyTarget),
    }),
  );
  app.use(
    '/:namespace/ajax-api',
    createProxyMiddleware({
      target: defaultProxyTarget,
      secure: false,
      changeOrigin: true,
      router: (req) => resolveTargetFromRequest(req, defaultProxyTarget),
      pathRewrite: (path) => path.replace(/^\/[^^/]+/, ''),
    }),
  );
  app.use(
    createProxyMiddleware('/graphql', {
      target: defaultProxyTarget,
      secure: false,
      changeOrigin: true,
      router: (req) => resolveTargetFromRequest(req, defaultProxyTarget),
    }),
  );
  app.use(
    '/:namespace/graphql',
    createProxyMiddleware({
      target: defaultProxyTarget,
      secure: false,
      changeOrigin: true,
      router: (req) => resolveTargetFromRequest(req, defaultProxyTarget),
      pathRewrite: (path) => path.replace(/^\/[^^/]+/, ''),
    }),
  );
  app.use(
    createProxyMiddleware('/get-artifact', {
      target: defaultProxyStaticTarget,
      ws: true,
      secure: false,
      changeOrigin: true,
      router: (req) => resolveTargetFromRequest(req, defaultProxyStaticTarget),
    }),
  );
  app.use(
    '/:namespace/get-artifact',
    createProxyMiddleware({
      target: defaultProxyStaticTarget,
      ws: true,
      secure: false,
      changeOrigin: true,
      router: (req) => resolveTargetFromRequest(req, defaultProxyStaticTarget),
      pathRewrite: (path) => path.replace(/^\/[^^/]+/, ''),
    }),
  );
  app.use(
    createProxyMiddleware('/model-versions/get-artifact', {
      target: defaultProxyStaticTarget,
      ws: true,
      secure: false,
      changeOrigin: true,
      router: (req) => resolveTargetFromRequest(req, defaultProxyStaticTarget),
    }),
  );
  app.use(
    '/:namespace/model-versions/get-artifact',
    createProxyMiddleware({
      target: defaultProxyStaticTarget,
      ws: true,
      secure: false,
      changeOrigin: true,
      router: (req) => resolveTargetFromRequest(req, defaultProxyStaticTarget),
      pathRewrite: (path) => path.replace(/^\/[^^/]+/, ''),
    }),
  );
};
