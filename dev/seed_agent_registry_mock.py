"""Populate a tracking server with linked registry assets and traces for the agent preview."""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import mlflow
from mlflow.entities import Dataset, DatasetInput, InputTag, SpanType
from mlflow.entities.assessment import Feedback
from mlflow.entities.assessment_source import AssessmentSource, AssessmentSourceType
from mlflow.entities.mcp_server import MCPStatus
from mlflow.entities.skill import RegistryIcon
from mlflow.exceptions import MlflowException
from mlflow.genai.skill_content.archive import package_skill_tree
from mlflow.store.tracking.mcp_server_registry.abstract_mixin import MCPIcon
from mlflow.tracing.client import TracingClient
from mlflow.tracing.constant import SpanAttributeKey, TraceMetadataKey
from mlflow.tracking import MlflowClient
from mlflow.utils.mlflow_tags import MLFLOW_RUN_TYPE, MLFLOW_RUN_TYPE_GENAI_EVALUATE

EXPERIMENT_NAME = "agent-registry-preview"
AGENT_ID_TAG = "mlflow.agent.id"
ENVIRONMENT_TAG = "mlflow.agent.environment"
SNAPSHOT_TAG = "mlflow.agent.snapshot_id"
VERSION_TAG = "mlflow.agent.version"
INSTANCE_ID_KEY = "service.instance.id"


def get_preview_instance_id(agent_id: str, environment: str, index: int) -> str:
    return f"{agent_id.removesuffix('-agent')}-{environment}-worker-{index % 2 + 1}"


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


@dataclass(frozen=True)
class SkillSeed:
    name: str
    description: str
    icon: str


@dataclass(frozen=True)
class MCPServerSeed:
    name: str
    title: str
    description: str
    icon: str
    versions: tuple[str, ...]


SKILLS = (
    SkillSeed(
        "refund-policy",
        "Check refund eligibility and explain the next steps.",
        "https://www.paypalobjects.com/webstatic/icon/pp258.png",
    ),
    SkillSeed(
        "code-review",
        "Review pull requests for correctness, security, and missing tests.",
        "https://github.githubassets.com/favicons/favicon.svg",
    ),
    SkillSeed(
        "incident-triage",
        "Correlate service alerts, identify likely causes, and prepare incident handoffs.",
        "https://www.datadoghq.com/favicon.ico",
    ),
    SkillSeed(
        "documentation-research",
        "Find authoritative documentation and produce answers with source references.",
        "https://www.python.org/static/favicon.ico",
    ),
)

MCP_SERVERS = (
    MCPServerSeed(
        "com.example/order-lookup",
        "Order lookup",
        "Look up orders, delivery progress, and return eligibility for support agents.",
        "https://www.paypalobjects.com/webstatic/icon/pp258.png",
        ("1.0.0", "1.1.0", "1.2.0"),
    ),
    MCPServerSeed(
        "com.example/docs-search",
        "Documentation search",
        "Search product documentation and return relevant passages with citations.",
        "https://www.python.org/static/favicon.ico",
        ("1.0.0", "2.0.0", "2.1.0"),
    ),
    MCPServerSeed(
        "com.example/github-tools",
        "GitHub tools",
        "Inspect repositories, pull requests, issues, and workflow results.",
        "https://github.githubassets.com/favicons/favicon.svg",
        ("1.0.0", "1.1.0", "2.0.0"),
    ),
    MCPServerSeed(
        "com.example/postgres-query",
        "PostgreSQL query",
        "Explore database schemas and run read-only queries for data investigations.",
        "https://www.postgresql.org/favicon.ico",
        ("0.9.0", "1.0.0", "1.1.0"),
    ),
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

    for seed in SKILLS:
        icons: list[RegistryIcon] = [{"src": seed.icon}]
        try:
            skill = client.get_skill(name=seed.name)
        except MlflowException as error:
            if error.error_code != "RESOURCE_DOES_NOT_EXIST":
                raise
            skill = client.create_skill(name=seed.name, description=seed.description, icons=icons)
        if not skill.icons:
            client.update_skill(name=seed.name, icons=icons)
        versions = client.search_skill_versions(name=seed.name, max_results=3)
        for revision in range(len(versions) + 1, 4):
            steps = (
                "Identify the relevant inputs and evidence before proceeding.",
                "Record assumptions and cite the sources used in the response.",
                "Summarize findings, unresolved questions, and recommended next steps.",
            )
            content = (
                f"---\nname: {seed.name}\ndescription: {json.dumps(seed.description)}\n---\n\n"
                f"# {seed.name.replace('-', ' ').title()}\n\n{seed.description}\n\n"
                f"## Revision {revision}\n\n"
                + "\n".join(f"- {step}" for step in steps[:revision])
                + "\n"
            )
            with TemporaryDirectory(prefix="mlflow-preview-skill-") as temporary:
                root = Path(temporary)
                skill_dir = root / seed.name
                skill_dir.mkdir()
                (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
                archive = package_skill_tree(skill_dir, root / "content.tar.gz")
                client.create_skill_version(name=seed.name, source=str(archive), status="active")

    for server_seed in MCP_SERVERS:
        server_icons: list[MCPIcon] = [{"src": server_seed.icon}]
        try:
            server = client.get_mcp_server(server_seed.name)
        except MlflowException as error:
            if error.error_code != "RESOURCE_DOES_NOT_EXIST":
                raise
            server = client.create_mcp_server(
                name=server_seed.name, description=server_seed.description, icons=server_icons
            )
        if not server.icons:
            client.update_mcp_server(name=server_seed.name, icons=server_icons)
        for server_version in server_seed.versions:
            try:
                client.get_mcp_server_version(server_seed.name, server_version)
            except MlflowException as error:
                if error.error_code != "RESOURCE_DOES_NOT_EXIST":
                    raise
                client.create_mcp_server_version(
                    server_json={
                        "name": server_seed.name,
                        "version": server_version,
                        "title": server_seed.title,
                        "description": (
                            f"{server_seed.description} Preview release {server_version}."
                        ),
                        "icons": server_icons,
                    },
                    status=MCPStatus.ACTIVE,
                    tools=[],
                )

    seed_mcp_access_endpoints(client)


def seed_mcp_access_endpoints(client: MlflowClient) -> int:
    created = 0
    for seed in MCP_SERVERS:
        existing_urls: set[str] = set()
        page_token = None
        while True:
            endpoints = client.search_mcp_access_endpoints(
                server_name=seed.name, page_token=page_token
            )
            existing_urls.update(endpoint.url for endpoint in endpoints)
            if not (page_token := endpoints.token):
                break
        slug = seed.name.rsplit("/", 1)[-1]
        for version in seed.versions:
            for environment in ("prod", "staging"):
                url = f"https://{slug}.{environment}.example.com/{version}/mcp"
                if url not in existing_urls:
                    client.create_mcp_access_endpoint(
                        server_name=seed.name, url=url, server_version=version
                    )
                    existing_urls.add(url)
                    created += 1
    return created


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
        instance_id = get_preview_instance_id(
            agent.agent_id, environment, index // len(agent.environments)
        )
        root = mlflow.start_span_no_context(
            name=agent.name,
            span_type=SpanType.AGENT,
            inputs={"question": question},
            tags=tags,
            metadata={
                TraceMetadataKey.TRACE_SESSION: f"{agent.agent_id}-session-{index // 2 + 1}",
                INSTANCE_ID_KEY: instance_id,
            },
            experiment_id=experiment_id,
            start_time_ns=start_ns,
        )
        root.set_attribute(INSTANCE_ID_KEY, instance_id)
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
            instance_id = get_preview_instance_id(agent.agent_id, environment, case_index)
            root = mlflow.start_span_no_context(
                name=f"{agent.name} evaluation",
                span_type=SpanType.AGENT,
                inputs={"question": f"{agent.name} evaluation case {case_index + 1}"},
                tags=tags,
                metadata={TraceMetadataKey.SOURCE_RUN: run_id, INSTANCE_ID_KEY: instance_id},
                experiment_id=experiment_id,
                start_time_ns=start_ns,
            )
            root.set_attribute(INSTANCE_ID_KEY, instance_id)
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
    if client.get_experiment(experiment_id).name != EXPERIMENT_NAME:
        raise ValueError(f"Only the {EXPERIMENT_NAME} experiment can be repaired")
    tracing_client = TracingClient(tracking_uri=tracking_uri)
    agents_by_id = {agent.agent_id: agent for agent in AGENTS}
    instance_counts: dict[tuple[str, str], int] = defaultdict(int)
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
            if not agent:
                continue
            environment = trace.info.tags.get(ENVIRONMENT_TAG) or "unassigned"
            scope = (agent.agent_id, environment)
            changed = False
            if not trace.info.trace_metadata.get(INSTANCE_ID_KEY):
                trace.info.trace_metadata[INSTANCE_ID_KEY] = get_preview_instance_id(
                    agent.agent_id, environment, instance_counts[scope]
                )
                changed = True
            instance_counts[scope] += 1
            has_preview_session = bool(
                session_id and session_id.startswith(f"{agent.agent_id}-session-")
            )
            if (
                session_id
                and has_preview_session
                and not trace.info.trace_metadata.get(TraceMetadataKey.TRACE_SESSION)
            ):
                trace.info.trace_metadata[TraceMetadataKey.TRACE_SESSION] = session_id
                changed = True
            if changed:
                tracing_client.start_trace(trace.info)
            if has_preview_session and environment in agent.environments:
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
