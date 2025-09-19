# MLflow Multitenancy Plugin

A comprehensive plugin that enables secure multitenancy in MLflow deployments through Kubernetes-native authorization and namespace isolation. This plugin provides both client-side header injection and server-side authorization enforcement using Kubernetes RBAC.

## 🌟 Features

- **Client-side Authentication**: Automatic injection of authorization headers and namespace identifiers
- **Server-side Authorization**: Kubernetes SSAR (SelfSubjectAccessReview) integration for fine-grained access control
- **Namespace Isolation**: Complete separation of experiments, models, and prompts by Kubernetes namespace
- **Zero Core Changes**: Drop-in plugin that works with existing MLflow deployments
- **RBAC Integration**: Leverages Kubernetes RBAC for permissions management

## 📋 Prerequisites

- Python 3.8+
- MLflow 2.13+
- Kubernetes cluster (for server-side features)
- Valid Kubernetes bearer tokens for authentication

## 🚀 OpenShift Deployment

You can see sample deployment manifests for OpenShift at [deployment.yaml](./deployment.yaml).

## 🚀 Installation

### Development Installation

```bash
# Install with server dependencies (includes Kubernetes client)
pip install -e ./mlflow-multitenancy-plugin[server]

# Or install client-only version
pip install -e ./mlflow-multitenancy-plugin
```

### Production Installation

```bash
# From PyPI (when published)
pip install mlflow-multitenancy-plugin[server]
```

## 🖥️ Client Usage

The plugin automatically injects authentication headers into all MLflow client requests when the appropriate environment variables are set.

### Environment Variables

| Variable                | Description                                | Required |
| ----------------------- | ------------------------------------------ | -------- |
| `MLFLOW_TRACKING_TOKEN` | Kubernetes bearer token for authentication | Yes      |
| `MLFLOW_NAMESPACE`      | Target namespace for MLflow operations     | Yes      |

### Example Client Code

```python
import os
import mlflow

# Set authentication credentials
os.environ["MLFLOW_TRACKING_TOKEN"] = "your-k8s-bearer-token"
os.environ["MLFLOW_NAMESPACE"] = "my-team-namespace"
os.environ["MLFLOW_TRACKING_URI"] = "https://your-mlflow-server.com"

# Use MLflow normally - headers are automatically injected
with mlflow.start_run():
    mlflow.log_param("param1", "value1")
    mlflow.log_metric("accuracy", 0.95)
```

### URL Enhancement

The plugin automatically enhances MLflow URLs printed to stdout with namespace query parameters, ensuring links work correctly in multi-tenant environments.

## 🖧 Server Usage

The server component provides comprehensive authorization and namespace isolation through middleware and store wrappers.

### Quick Start

Run MLflow server with the multitenancy plugin:

```bash
mlflow server \
  --host 0.0.0.0 \
  --port 5000 \
  --backend-store-uri ns:postgresql://user:pass@localhost/mlflow \
  --registry-store-uri ns:postgresql://user:pass@localhost/mlflow \
  --artifacts-destination s3://my-bucket/artifacts \
  --workers 4 \
  --gunicorn-opts "" \
  --app-name multitenancy
```

### Server Configuration

#### Store URI Schemes

Use the `ns:` prefix to enable namespace-aware stores:

- **Tracking Store**: `ns:postgresql://user:pass@host/db`
- **Model Registry**: `ns:sqlite:////path/to/registry.db`
- **File Store**: `ns:file:///path/to/mlruns`

#### WSGI Server Compatibility

| Server   | Supported | Notes                                |
| -------- | --------- | ------------------------------------ |
| Gunicorn | ✅        | Recommended for production           |
| Waitress | ✅        | Good alternative to Gunicorn         |
| uWSGI    | ✅        | Enterprise deployments               |
| Uvicorn  | ❌        | ASGI server, incompatible with Flask |

### Authorization Flow

1. **Request Interception**: Middleware extracts namespace from `X-MLflow-Namespace` header
2. **Token Validation**: Bearer token extracted from `Authorization` header
3. **SSAR Check**: Kubernetes SelfSubjectAccessReview validates permissions
4. **Resource Mapping**: Request paths mapped to MLflow resources (experiments, models, prompts)
5. **Verb Determination**: HTTP methods mapped to RBAC verbs (get, list, create, update, delete)
6. **Namespace Scoping**: Store operations automatically scoped to authorized namespace

### Kubernetes RBAC Setup

Create appropriate RBAC resources in your cluster:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  namespace: my-team-namespace
  name: mlflow-user
rules:
  - apiGroups: ["community.mlflow.org"]
    resources: ["experiments", "models", "prompts"]
    verbs: ["get", "list", "create", "update", "delete"]

---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: mlflow-user-binding
  namespace: my-team-namespace
subjects:
  - kind: User
    name: user@company.com
    apiGroup: rbac.authorization.k8s.io
roleRef:
  kind: Role
  name: mlflow-user
  apiGroup: rbac.authorization.k8s.io
```

## 🔧 Configuration

### Environment Variables (Server)

| Variable                              | Description                 | Default       |
| ------------------------------------- | --------------------------- | ------------- |
| `MLFLOW_K8S_API`                      | Kubernetes API server URL   | Auto-detected |
| `MLFLOW_K8S_CA_FILE`                  | Path to CA certificate file | Auto-detected |
| `MLFLOW_K8S_INSECURE_SKIP_TLS_VERIFY` | Skip TLS verification       | `false`       |

### Namespace Validation

Namespaces must follow Kubernetes naming conventions:

- Lowercase alphanumeric characters and hyphens only
- Start and end with alphanumeric characters
- Maximum 63 characters
- No consecutive hyphens

## 🐛 Troubleshooting

### Common Issues

#### Flask WSGI Error

```
Flask.__call__() missing 1 required positional argument: 'start_response'
```

**Solution**: Use a WSGI server like Gunicorn. Add `--gunicorn-opts ""` to your command.

#### Authorization Failures

```
Access denied by SSAR: Forbidden
```

**Solutions**:

- Verify your Kubernetes bearer token is valid
- Check RBAC permissions in the target namespace
- Ensure the namespace exists and is accessible

#### Token Extraction Issues

```
Missing bearer token for authorization
```

**Solutions**:

- Set `MLFLOW_TRACKING_TOKEN` environment variable
- Verify `Authorization: Bearer <token>` header is present
- Check for token forwarding headers (`X-Forwarded-Access-Token`)

### Debug Mode

Enable verbose logging for troubleshooting:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## 📚 API Reference

### Resource Types

| MLflow Resource | API Paths                                   | RBAC Resource |
| --------------- | ------------------------------------------- | ------------- |
| Experiments     | `/experiments/*`, `/runs/*`                 | `experiments` |
| Models          | `/registered-models/*`, `/model-versions/*` | `models`      |
| Prompts         | `/prompts/*`, `/prompt-versions/*`          | `prompts`     |

### RBAC Verbs

| HTTP Method | Path Pattern | RBAC Verb |
| ----------- | ------------ | --------- |
| GET         | `/search*`   | `list`    |
| GET         | Other paths  | `get`     |
| POST        | `/create*`   | `create`  |
| POST        | Other paths  | `create`  |
| PUT/PATCH   | `/update*`   | `update`  |
| PUT/PATCH   | Other paths  | `update`  |
| DELETE      | Any path     | `delete`  |

## Open Questions/To Dos

- It may be better to add the `mlflow.namespace` tag to every object and enforce this at creation time to avoid having to query for the parent resource. This seems less error prone and easier to maintain.
- Add support for recording the user from the token. Example to get the current user's information: `curl -H "Authorization: Bearer $(oc whoami --show-token)" $(oc whoami --show-server)/apis/user.openshift.io/v1/users/~`. This should be configurable for the OpenShift user case and JWTs.
- Add tests and documentation.
- Add the Artifact Repository implementation with namespace isolation.
