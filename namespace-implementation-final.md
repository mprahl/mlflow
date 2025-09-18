## MLflow Namespaces for Multitenancy (Plugin-first)

Final design: all multitenancy logic lives in a separate plugin package with no schema or protobuf changes and minimal/no core code edits.

- Namespace stored as a reserved tag `mlflow.namespace` on top-level resources.
- One active namespace per request (derived from URL `?ns=` in the UI or `X-MLflow-Namespace` in SDK).
- Authorization via Kubernetes SelfSubjectAccessReview (SSAR).
- List/search operations are filtered by namespace; child operations are validated via the parent’s namespace.

### Resources and inheritance

- Top-level: experiments, registered models, prompts (tagged with `mlflow.namespace`).
- Children: runs inherit from experiment; model versions from registered model; prompt variants from prompt.

### Namespace selection

- UI: uses URL param `?ns=` as source of truth.
  - All links/navigation preserve `?ns=`.
- SDK/CLI: RequestHeaderProvider injects `Authorization: Bearer <MLFLOW_TRACKING_TOKEN>` and, if set, `X-MLflow-Namespace: <MLFLOW_NAMESPACE>`.

### Authorization (SSAR)

- Each API request is authorized via SSAR with resourceAttributes:
  - group: `community.mlflow.org`
  - resource: `experiments` | `models` | `prompts`
  - verb: `get` | `list` | `create` | `update` | `delete`
  - namespace: active namespace
- No caching; fail closed on errors/timeouts.

### Server plugin behavior

- Middleware:
  - Reads `X-MLflow-Namespace` if present.
- Namespaces discovery: `GET /ajax-api/2.0/mlflow/namespaces`
  - Lists all namespaces using a service account (in-cluster or kubeconfig), then filters via SSAR with the user’s token.
  - Returns 403 with a clear message when none are accessible.

### Store enforcement (plugin)

- Activated via store plugins using `ns:` scheme:
  - `--backend-store-uri ns:sqlite:////tmp/mlflow.db`
  - `--registry-store-uri ns:postgresql://...`
- The plugin constructs base stores (File/SQLAlchemy) and wraps them with namespace-enforcing proxies:
  - On create (top-level): add `mlflow.namespace` tag.
  - On get/update/delete: verify resource namespace.
  - On list/search: inject `tags.`mlflow.namespace` = '<ns>'`.
  - Runs: validate via parent experiment.
  - Registered models: internal name prefixed `<ns>::<name>` to prevent cross-namespace name collisions.

### UI summary

- Namespace switcher fetches namespaces without `?ns=` and defaults to the first accessible, updating the URL.
- Fetchers (REST/GraphQL) read `?ns=` and set `X-MLflow-Namespace`; they abort early with a clear error if `?ns=` is missing (except for the namespaces endpoint).
- All links preserve `?ns=` automatically

### Deployment

- Install plugin: `pip install -e ./mlflow-multitenancy-plugin[server]`.
- Start with the plugin app and `ns:` stores (WSGI server):
  - `mlflow server --host 127.0.0.1 --port 5000 \
--backend-store-uri ns:sqlite:////tmp/mlflow.db \
--registry-store-uri ns:sqlite:////tmp/mlflow.db \
--workers 2 --gunicorn-opts "" --app-name multitenancy`

### Env vars (plugin)

- K8s connectivity: `MLFLOW_K8S_API`, `MLFLOW_K8S_CA_FILE`, `MLFLOW_K8S_INSECURE_SKIP_TLS_VERIFY` (optional).
- UI dev-only token: `localStorage.setItem('mlflow.k8s.bearerToken', '<token>')`.

### Not included

- No protobuf changes.
- No path-based namespaces.
- No persistent core code changes beyond choosing the app entry point and using `ns:` stores.
