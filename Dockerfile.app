FROM registry.access.redhat.com/ubi9/nodejs-20:9.6 AS ui-builder
WORKDIR /opt/app-root/src
USER 0
RUN npm install -g yarn
COPY --chown=1001:0 mlflow/server/js/ .
USER 1001
RUN yarn install --silent \
    && yarn build

FROM registry.access.redhat.com/ubi9/python-311:9.6
ARG MLFLOW_VERSION=3.3.2
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

# Install upstream MLflow (Python package)
USER 0
RUN python -m pip install --no-cache-dir "mlflow==${MLFLOW_VERSION}"

# Copy built UI from builder stage and install the multitenancy plugin from this repo
COPY --from=ui-builder /opt/app-root/src/build /tmp/mlflow-ui-build
COPY mlflow-multitenancy-plugin /tmp/mlflow-multitenancy-plugin

RUN set -eux; \
    # Install the plugin (with server extras)
    rm -rf /tmp/mlflow-multitenancy-plugin/src/*.egg-info || true; \
    python -m pip install --no-cache-dir \
      "/tmp/mlflow-multitenancy-plugin[server]"; \
    # Copy the UI build into the installed mlflow package
    py_site=$(python -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])'); \
    mkdir -p "$py_site/mlflow/server/js/build"; \
    rm -rf "$py_site/mlflow/server/js/build"/* || true; \
    cp -r /tmp/mlflow-ui-build/* "$py_site/mlflow/server/js/build/"; \
    rm -rf /tmp/mlflow-ui-build /tmp/mlflow-multitenancy-plugin

EXPOSE 5000

# Default command. Override in Kubernetes as needed.
USER 1001
CMD ["mlflow", "server", "--host", "0.0.0.0", "--port", "5000"]


