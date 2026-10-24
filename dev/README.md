## MLflow Dev Scripts

This directory contains automation scripts for MLflow developers and the build infrastructure.

## Agent Registry preview

The Agent Registry is a read-only visual prototype. Agent and fleet records are
browser fixtures; monitoring and evaluation pages reuse existing experiment APIs.
The optional `--seed-agent-registry` flag on `run_dev_server.py` populates the
`agent-registry-preview` experiment. It is not an Agent Registry backend.

The investigation drawer contains separate, explicitly labeled browser-only
examples. No seeding or server changes are needed to inspect them:

- **Support agent → Agent snapshots → Investigate change** compares recorded
  composition and example execution evidence. Select a comparison trace to inspect
  it or compare it with a matching baseline case.
- **Dashboard → Tool calls → Inspect affected requests**, **AI assets → Tools/MCP
  servers**, and **Traces** open dependency evidence. Tool offers, invocations,
  failures, explicit retry links, and fallback links remain distinct.
- **Traces → Investigate runtime context** filters example activity by instance
  ID on any span, including activity without a snapshot and activity spanning
  multiple snapshots. Knowledge agent's dev examples demonstrate snapshot-free
  harness activity. Use an example as a baseline, then select another to compare
  context and execution across instances within the same example environment.

Investigation filters do not alter live charts or traces. Example counts are not
production aggregates, source/asset differences do not prove causality, and a
successful request does not prove answer quality. The trace explorer is read-only;
evaluation links open existing experiment-backed runs rather than fabricated
evaluation results. Closing a drawer preserves the originating page's filters.

## Job Statuses

[![Examples Action Status](https://img.shields.io/github/actions/workflow/status/mlflow/dev/examples.yml.svg?branch=master&event=schedule&label=Examples&style=for-the-badge&logo=github)](https://github.com/mlflow/dev/actions/workflows/examples.yml?query=event%3Aschedule)
[![Cross Version Tests Status](https://img.shields.io/github/actions/workflow/status/mlflow/dev/cross-version-tests.yml.svg?branch=master&event=schedule&label=Cross%20version%20tests&style=for-the-badge&logo=github)](https://github.com/mlflow/dev/actions/workflows/cross-version-tests.yml?query=event%3Aschedule)
[![R-devel Action Status](https://img.shields.io/github/actions/workflow/status/mlflow/dev/r.yml.svg?branch=master&event=schedule&label=r-devel&style=for-the-badge&logo=github)](https://github.com/mlflow/dev/actions/workflows/r.yml?query=event%3Aschedule)
[![Test Requirements Status](https://img.shields.io/github/actions/workflow/status/mlflow/dev/test-requirements.yml.svg?branch=master&event=schedule&label=test%20requirements&logo=github&style=for-the-badge)](https://github.com/mlflow/dev/actions/workflows/test-requirements.yml?query=event%3Aschedule)
[![Push Images Status](https://img.shields.io/github/actions/workflow/status/mlflow/mlflow/push-images.yml.svg?event=release&label=push-images&logo=github&style=for-the-badge)](https://github.com/mlflow/mlflow/actions/workflows/push-images.yml?query=event%3Arelease)
[![Slow Tests Status](https://img.shields.io/github/actions/workflow/status/mlflow/dev/slow-tests.yml.svg?branch=master&event=schedule&label=slow-tests&logo=github&style=for-the-badge)](https://github.com/mlflow/dev/actions/workflows/slow-tests.yml?query=event%3Aschedule)
[![Website E2E Tests Status](https://img.shields.io/github/actions/workflow/status/mlflow/mlflow-website/e2e.yml.svg?branch=main&event=schedule&label=website-e2e&logo=github&style=for-the-badge)](https://github.com/mlflow/mlflow-website/actions/workflows/e2e.yml?query=event%3Aschedule)
[![Update Model Catalog Status](https://img.shields.io/github/actions/workflow/status/mlflow/mlflow/update-model-catalog.yml.svg?branch=master&event=schedule&label=update-model-catalog&logo=github&style=for-the-badge)](https://github.com/mlflow/mlflow/actions/workflows/update-model-catalog.yml?query=event%3Aschedule)
[![Gateway Benchmark Status](https://img.shields.io/github/actions/workflow/status/mlflow/mlflow/gateway-benchmark.yml.svg?branch=master&event=schedule&label=gateway-benchmark&logo=github&style=for-the-badge)](https://github.com/mlflow/mlflow/actions/workflows/gateway-benchmark.yml?query=event%3Aschedule)
[![Tracing Benchmark Status](https://img.shields.io/github/actions/workflow/status/mlflow/mlflow/tracing-benchmark.yml.svg?branch=master&label=tracing-benchmark&logo=github&style=for-the-badge)](https://github.com/mlflow/mlflow/actions/workflows/tracing-benchmark.yml)
