"""Populate a tracking server with linked registry assets and traces for the agent preview."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path

import mlflow
from mlflow.entities import Dataset, DatasetInput, InputTag, SpanType
from mlflow.entities.assessment import Feedback
from mlflow.entities.assessment_source import AssessmentSource, AssessmentSourceType
from mlflow.entities.mcp_server import MCPStatus
from mlflow.tracing.client import TracingClient
from mlflow.tracing.constant import SpanAttributeKey, TraceMetadataKey
from mlflow.tracking import MlflowClient
from mlflow.utils.mlflow_tags import MLFLOW_RUN_TYPE, MLFLOW_RUN_TYPE_GENAI_EVALUATE

EXPERIMENT_NAME = "agent-registry-preview"
AGENT_ID_TAG = "mlflow.agent.id"
ENVIRONMENT_TAG = "mlflow.agent.environment"
SNAPSHOT_TAG = "mlflow.agent.snapshot_id"
VERSION_TAG = "mlflow.agent.version"


@dataclass(frozen=True)
class AgentSeed:
    agent_id: str
    name: str
    environments: tuple[str, ...]
    count: int
    snapshot_id: str
    version: str

    def snapshot_for_environment(self, environment: str) -> str:
        if self.agent_id == "support-agent" and environment == "staging":
            return "8"
        return self.snapshot_id


AGENTS = (
    AgentSeed("support-agent", "Support agent", ("dev", "staging", "prod"), 8, "7", "2.4.0"),
    AgentSeed("refund-agent", "Refund agent", ("dev", "prod"), 4, "3", "1.2.0"),
    AgentSeed("knowledge-agent", "Knowledge agent", ("dev", "staging"), 4, "2", "0.8.0"),
)


def seed_registry_assets(client: MlflowClient) -> None:
    if client.get_prompt("support-system") is None:
        client.create_prompt(
            name="support-system",
            description="Customer support instructions used by the agent preview.",
            tags={"mlflow.agent.preview": "true"},
        )
        for version in range(1, 5):
            client.create_prompt_version(
                name="support-system",
                template=(
                    "You are a customer support agent. Answer questions about orders and refunds. "
                    f"Use policy revision {version} and ask for clarification when needed."
                ),
                description=f"Preview prompt revision {version}",
            )

    if not any(skill.name == "refund-policy" for skill in client.search_skills()):
        client.create_skill(
            name="refund-policy",
            description="Check refund eligibility and explain the next steps.",
        )
        for _ in range(2):
            client.create_skill_version(name="refund-policy", status="active")

    for name, mcp_version, description in (
        ("com.example/order-lookup", "1.0.0", "Look up an order for the support agent."),
        ("com.example/docs-search", "2.0.0", "Search knowledge documents for the knowledge agent."),
    ):
        if any(server.name == name for server in client.search_mcp_servers()):
            continue
        client.create_mcp_server(name=name, description=description)
        client.create_mcp_server_version(
            server_json={
                "name": name,
                "version": mcp_version,
                "title": name.replace("-", " ").title(),
                "description": description,
            },
            status=MCPStatus.ACTIVE,
            tools=[],
        )


def seed_agent_traces(experiment_id: str, agent: AgentSeed) -> None:
    source = AssessmentSource(
        source_type=AssessmentSourceType.CODE, source_id="agent-registry-preview"
    )
    assessments = []
    now_ns = time.time_ns()

    for index in range(agent.count):
        environment = agent.environments[index % len(agent.environments)]
        snapshot = agent.snapshot_for_environment(environment)
        start_ns = now_ns - (agent.count - index) * 8 * 60 * 60 * 1_000_000_000
        duration_ms = 1050 + index * 90
        question = f"{agent.name} question {index + 1}"
        tags = {
            AGENT_ID_TAG: agent.agent_id,
            ENVIRONMENT_TAG: environment,
            SNAPSHOT_TAG: snapshot,
            VERSION_TAG: agent.version,
        }
        if agent.agent_id == "support-agent" and index % 2 == 0:
            tags["mlflow.agent.related_agent_id"] = "refund-agent"
        root = mlflow.start_span_no_context(
            name=agent.name,
            span_type=SpanType.AGENT,
            inputs={"question": question},
            tags=tags,
            metadata={TraceMetadataKey.TRACE_SESSION: f"{agent.agent_id}-session-{index // 2 + 1}"},
            experiment_id=experiment_id,
            start_time_ns=start_ns,
        )
        tool = mlflow.start_span_no_context(
            name="lookup_order" if agent.agent_id == "support-agent" else "search_context",
            span_type=SpanType.TOOL,
            parent_span=root,
            start_time_ns=start_ns + 100_000_000,
        )
        tool.end(outputs={"matches": 2}, end_time_ns=start_ns + 350_000_000)

        llm = mlflow.start_span_no_context(
            name="gpt-4o",
            span_type=SpanType.LLM,
            parent_span=root,
            start_time_ns=start_ns + 400_000_000,
        )
        input_tokens = 210 + index * 15
        output_tokens = 85 + index * 5
        llm.set_attribute(
            SpanAttributeKey.CHAT_USAGE,
            {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
        )
        llm.set_attribute(
            SpanAttributeKey.LLM_COST,
            {
                "input_cost": 0.001 + index * 0.0001,
                "output_cost": 0.002 + index * 0.0002,
                "total_cost": 0.003 + index * 0.0003,
            },
        )
        llm.set_attribute(SpanAttributeKey.MODEL, "gpt-4o")
        llm.end(
            outputs={"answer": f"Answer to {question}"},
            end_time_ns=start_ns + duration_ms * 1_000_000,
        )
        root.end(
            outputs={"answer": f"Answer to {question}"},
            status="ERROR" if index % 6 == 5 else "OK",
            end_time_ns=start_ns + (duration_ms + 100) * 1_000_000,
        )
        assessments.append((root.trace_id, index % 6 != 5))

    mlflow.flush_trace_async_logging()
    for trace_id, passed in assessments:
        mlflow.log_assessment(
            trace_id=trace_id,
            assessment=Feedback(name="quality", value=passed, source=source),
        )


def seed_agent_evaluation_runs(experiment_id: str, agent: AgentSeed) -> None:
    client = MlflowClient()
    source = AssessmentSource(
        source_type=AssessmentSourceType.CODE, source_id="agent-registry-preview"
    )
    selections: list[tuple[str, str | None]] = [
        (environment, agent.snapshot_for_environment(environment))
        for environment in agent.environments
    ]
    if agent.agent_id == "support-agent":
        selections.insert(0, ("dev", "6"))
        selections.append(("unassigned", None))
    for index, (environment, snapshot_id) in enumerate(selections):
        name = f"eval-{agent.agent_id.removesuffix('-agent')}-{environment}"
        if snapshot_id:
            name += f"-{snapshot_id}"
        marker = "mlflow.agent.preview.evaluation_id"
        if client.search_runs(
            [experiment_id], filter_string=f"tags.`{marker}` = '{name}'", max_results=1
        ):
            continue
        tags = {
            MLFLOW_RUN_TYPE: MLFLOW_RUN_TYPE_GENAI_EVALUATE,
            AGENT_ID_TAG: agent.agent_id,
            ENVIRONMENT_TAG: environment,
            marker: name,
        }
        if snapshot_id:
            version = (
                "2.3.1"
                if agent.agent_id == "support-agent" and snapshot_id == "6"
                else agent.version
            )
            tags.update({SNAPSHOT_TAG: snapshot_id, VERSION_TAG: version})
        start_ms = int(time.time() * 1000) - (len(selections) - index) * 8 * 60 * 60 * 1000
        run = client.create_run(experiment_id, start_time=start_ms, tags=tags, run_name=name)
        run_id = run.info.run_id
        client.log_inputs(
            run_id,
            datasets=[
                DatasetInput(
                    Dataset(
                        name=f"{agent.agent_id}-checks",
                        digest="preview1",
                        source_type="code",
                        source=json.dumps({"tags": {"mlflow.source.name": __file__}}),
                        schema=json.dumps({
                            "mlflow_colspec": [{"type": "string", "name": "question"}]
                        }),
                        profile=json.dumps({"num_rows": 2}),
                    ),
                    tags=[InputTag("mlflow.data.context", "eval")],
                )
            ],
        )
        assessments = []
        for case_index in range(2):
            start_ns = (start_ms + case_index * 2000) * 1_000_000
            root = mlflow.start_span_no_context(
                name=f"{agent.name} evaluation",
                span_type=SpanType.AGENT,
                inputs={"question": f"{agent.name} evaluation case {case_index + 1}"},
                tags=tags,
                metadata={TraceMetadataKey.SOURCE_RUN: run_id},
                experiment_id=experiment_id,
                start_time_ns=start_ns,
            )
            root.end(
                outputs={"answer": "Preview evaluation response"},
                end_time_ns=start_ns + 1_000_000_000,
            )
            assessments.append((root.trace_id, environment != "dev" or case_index == 1))
        mlflow.flush_trace_async_logging()
        for trace_id, passed in assessments:
            mlflow.log_assessment(
                trace_id=trace_id,
                assessment=Feedback(name="quality", value=passed, source=source),
            )
        client.log_metric(run_id, "quality/mean", sum(passed for _, passed in assessments) / 2)
        client.log_param(run_id, "cases", 2)
        client.set_terminated(run_id, end_time=start_ms + 4000)


def repair_preview_traces(tracking_uri: str, experiment_id: str) -> int:
    client = MlflowClient(tracking_uri=tracking_uri)
    tracing_client = TracingClient(tracking_uri=tracking_uri)
    agents_by_id = {agent.agent_id: agent for agent in AGENTS}
    repaired = 0
    page_token = None
    while True:
        traces = client.search_traces(
            locations=[experiment_id],
            max_results=100,
            page_token=page_token,
            include_spans=False,
        )
        for trace in traces:
            agent_id = trace.info.tags.get(AGENT_ID_TAG)
            session_id = trace.info.trace_metadata.get(
                TraceMetadataKey.TRACE_SESSION
            ) or trace.info.tags.get(TraceMetadataKey.TRACE_SESSION)
            agent = agents_by_id.get(agent_id)
            if not (agent and session_id and session_id.startswith(f"{agent_id}-session-")):
                continue
            changed = False
            if not trace.info.trace_metadata.get(TraceMetadataKey.TRACE_SESSION):
                trace.info.trace_metadata[TraceMetadataKey.TRACE_SESSION] = session_id
                tracing_client.start_trace(trace.info)
                changed = True
            environment = trace.info.tags.get(ENVIRONMENT_TAG)
            if environment in agent.environments:
                expected_tags = {
                    SNAPSHOT_TAG: agent.snapshot_for_environment(environment),
                    VERSION_TAG: agent.version,
                }
                for key, value in expected_tags.items():
                    if trace.info.tags.get(key) != value:
                        client.set_trace_tag(trace.info.trace_id, key, value)
                        changed = True
            repaired += int(changed)
        if not (page_token := traces.token):
            return repaired


def seed_fleet_scopes(client: MlflowClient, experiment_id: str) -> tuple[int, int]:
    fixtures = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "mlflow/server/js/src/agent-registry/mockFleets.json"
        ).read_text()
    )
    memberships = {
        fleet["id"]: {member["agentId"]: member.get("environment") for member in fleet["members"]}
        for fleet in fixtures
    }
    agent_ids = {agent.agent_id for agent in AGENTS}

    def scope_tags(tags: dict[str, str]) -> dict[str, str]:
        agent_id = tags.get(AGENT_ID_TAG)
        if agent_id not in agent_ids:
            return {}
        environment = tags.get(ENVIRONMENT_TAG) or "unassigned"
        return {
            f"mlflow.agent.preview.fleet.{fleet_id}": str(
                agent_id in members
                and (members[agent_id] is None or members[agent_id] == environment)
            ).lower()
            for fleet_id, members in memberships.items()
        }

    trace_updates = 0
    page_token = None
    while True:
        traces = client.search_traces(
            locations=[experiment_id], max_results=100, page_token=page_token, include_spans=False
        )
        for trace in traces:
            updates = {
                key: value
                for key, value in scope_tags(trace.info.tags).items()
                if trace.info.tags.get(key) != value
            }
            for key, value in updates.items():
                client.set_trace_tag(trace.info.trace_id, key, value)
            trace_updates += bool(updates)
        if not (page_token := traces.token):
            break

    run_updates = 0
    page_token = None
    while True:
        runs = client.search_runs([experiment_id], max_results=100, page_token=page_token)
        for run in runs:
            updates = {
                key: value
                for key, value in scope_tags(run.data.tags).items()
                if run.data.tags.get(key) != value
            }
            for key, value in updates.items():
                client.set_tag(run.info.run_id, key, value)
            run_updates += bool(updates)
        if not (page_token := runs.token):
            return trace_updates, run_updates


def seed(tracking_uri: str) -> None:
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_registry_uri(tracking_uri)
    client = MlflowClient(tracking_uri=tracking_uri, registry_uri=tracking_uri)
    seed_registry_assets(client)
    experiment = client.get_experiment_by_name(EXPERIMENT_NAME)
    experiment_id = (
        experiment.experiment_id if experiment else client.create_experiment(EXPERIMENT_NAME)
    )

    for agent in AGENTS:
        existing = client.search_traces(
            locations=[experiment_id],
            filter_string=f"tags.`{AGENT_ID_TAG}` = '{agent.agent_id}'",
            max_results=1,
            include_spans=False,
        )
        if not existing:
            seed_agent_traces(experiment_id, agent)
        seed_agent_evaluation_runs(experiment_id, agent)

    repair_preview_traces(tracking_uri, experiment_id)
    seed_fleet_scopes(client, experiment_id)
    print(f"Agent registry preview data seeded in experiment {experiment_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracking-uri", required=True)
    args = parser.parse_args()
    seed(args.tracking_uri)
