## MLflow Dev Scripts

This directory contains automation scripts for MLflow developers and the build infrastructure.

## Agent Registry preview

The Agent Registry is a read-only visual prototype. Agent and fleet records are
browser fixtures; monitoring and evaluation pages reuse existing experiment APIs.
The optional `--seed-agent-registry` flag on `run_dev_server.py` populates the
`agent-registry-preview` experiment. It is not an Agent Registry backend.

Agent and fleet trace tables include an **Instance ID** column by default, also
shown alongside trace activity in snapshot timelines. It reads `service.instance.id` from trace
metadata; missing IDs remain empty rather than being inferred from an agent,
session, or snapshot. The standard column picker can hide or restore the column.
The seed helper populates two worker IDs per agent/environment where activity
permits and backfills missing IDs on existing preview traces. In **Traces →
Filters**, select **Instance ID**, choose **=**, and enter an ID such as
`support-prod-worker-1` to demonstrate instance filtering.

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
