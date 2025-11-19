const { createProxyMiddleware } = require('http-proxy-middleware');

const workspaceAwareFilter = (basePath) => (pathname) => {
  const [pathWithoutQuery] = pathname.split('?');
  if (pathWithoutQuery.startsWith(basePath)) {
    return true;
  }
  if (!pathWithoutQuery.startsWith('/workspaces/')) {
    return false;
  }
  const remainder = pathWithoutQuery.replace(/^\/workspaces\/[^/]+/, '');
  return remainder.startsWith(basePath);
};

// eslint-disable-next-line
module.exports = function (app) {
  // The MLflow Gunicorn server is running on port 5000, so we should redirect server requests
  // (eg /ajax-api) to that port.
  // Exception: If the caller has specified an MLFLOW_PROXY, we instead forward server requests
  // there.
  // eslint-disable-next-line no-undef
  const proxyTarget = process.env.MLFLOW_PROXY || 'http://localhost:5000/';
  // eslint-disable-next-line no-undef
  const proxyStaticTarget = process.env.MLFLOW_STATIC_PROXY || proxyTarget;
  app.use(
    createProxyMiddleware('/ajax-api', {
      target: proxyTarget,
      changeOrigin: true,
    }),
  );
  app.use(
    createProxyMiddleware(workspaceAwareFilter('/graphql'), {
      target: proxyTarget,
      changeOrigin: true,
    }),
  );
  app.use(
    createProxyMiddleware(workspaceAwareFilter('/get-artifact'), {
      target: proxyStaticTarget,
      ws: true,
      changeOrigin: true,
    }),
  );
  app.use(
    createProxyMiddleware(workspaceAwareFilter('/model-versions/get-artifact'), {
      target: proxyStaticTarget,
      ws: true,
      changeOrigin: true,
    }),
  );
};
