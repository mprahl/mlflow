import tarfile
from unittest import mock

import pytest

from dev.seed_agent_registry_mock import (
    AGENT_ID_TAG,
    ENVIRONMENT_TAG,
    EXPERIMENT_NAME,
    INSTANCE_ID_KEY,
    MCP_SERVERS,
    SKILLS,
    get_preview_instance_id,
    repair_preview_traces,
    seed_mcp_access_endpoints,
    seed_registry_assets,
)
from mlflow.entities import Trace, TraceData, TraceInfo, TraceLocation, TraceState
from mlflow.genai.skill_content.archive import validate_skill_archive
from mlflow.store.entities.paged_list import PagedList
from mlflow.tracking import MlflowClient


@pytest.fixture
def registry_client(tmp_path):
    uri = f"sqlite:///{tmp_path / 'mlflow.db'}"
    client = MlflowClient(tracking_uri=uri, registry_uri=uri)

    def register_local_skill(*, archive, name, organization, digest, status):
        validate_skill_archive(archive, compressed=True)
        with tarfile.open(archive, "r:gz") as packaged:
            content = packaged.extractfile("SKILL.md").read().decode()
        assert f"name: {name}\n" in content
        assert "## Revision " in content
        assert "Identify the relevant inputs and evidence" in content
        return client.create_skill_version(name=name, organization=organization, status=status)

    with mock.patch.object(
        client, "_register_local_skill", side_effect=register_local_skill
    ) as upload:
        yield client
        upload.assert_called()


def test_seed_registry_assets_is_repeatable(registry_client):
    client = registry_client
    for _ in range(2):
        seed_registry_assets(client)
        assert len(client.search_skills()) == len(SKILLS)
        assert len(client.search_mcp_servers()) == len(MCP_SERVERS)
        for seed in SKILLS:
            assert client.get_skill(name=seed.name).icons == [{"src": seed.icon}]
            assert {
                version.version for version in client.search_skill_versions(name=seed.name)
            } == {1, 2, 3}
        for seed in MCP_SERVERS:
            assert client.get_mcp_server(seed.name).icons == [
                {"src": seed.icon, "source": "server"}
            ]
            versions = client.search_mcp_server_versions(seed.name)
            assert {version.version for version in versions} == set(seed.versions)
            assert all(version.server_json["icons"] == [{"src": seed.icon}] for version in versions)
            endpoints = client.search_mcp_access_endpoints(server_name=seed.name)
            assert len(endpoints) == 6
            slug = seed.name.rsplit("/", 1)[-1]
            assert {
                (endpoint.url, endpoint.server_version, endpoint.transport_type)
                for endpoint in endpoints
            } == {
                (
                    f"https://{slug}.{environment}.example.com/{version}/mcp",
                    version,
                    "streamable-http",
                )
                for version in seed.versions
                for environment in ("prod", "staging")
            }


def test_seed_registry_assets_preserves_existing_versions_and_metadata(registry_client):
    client = registry_client
    client.create_skill(name="refund-policy", description="Existing policy")
    for _ in range(2):
        client.create_skill_version(name="refund-policy", status="active")
    icons = [{"src": "https://example.com/custom.png"}]
    name = "com.example/order-lookup"
    client.create_mcp_server(name=name, description="Existing order lookup", icons=icons)
    server_json = {"name": name, "version": "1.0.0", "description": "Pinned version"}
    client.create_mcp_server_version(server_json=server_json, tools=[])
    existing_url = "https://order-lookup.prod.example.com/1.0.0/mcp"
    client.set_mcp_server_alias(name=name, alias="stable", version="1.0.0")
    client.create_mcp_access_endpoint(server_name=name, url=existing_url, server_alias="stable")
    client.create_mcp_access_endpoint(
        server_name=name, url="https://custom.example.com/mcp", server_version="1.0.0"
    )

    seed_registry_assets(client)

    skill = client.get_skill(name="refund-policy")
    assert skill.description == "Existing policy"
    assert skill.icons == [{"src": SKILLS[0].icon}]
    assert client.get_skill_version(name="refund-policy", version=2).status == "active"
    assert len(client.search_skill_versions(name="refund-policy")) == 3
    assert client.get_mcp_server(name).description == "Existing order lookup"
    assert client.get_mcp_server(name).icons == [{**icons[0], "source": "server"}]
    assert client.get_mcp_server_version(name, "1.0.0").server_json == server_json
    assert len(client.search_mcp_server_versions(name)) == 3
    endpoints = client.search_mcp_access_endpoints(server_name=name)
    assert len(endpoints) == 7
    assert (
        next(endpoint for endpoint in endpoints if endpoint.url == existing_url).server_version
        is None
    )
    assert (
        next(endpoint for endpoint in endpoints if endpoint.url == existing_url).server_alias
        == "stable"
    )
    assert (
        next(
            endpoint for endpoint in endpoints if endpoint.url == "https://custom.example.com/mcp"
        ).server_version
        == "1.0.0"
    )
    assert seed_mcp_access_endpoints(client) == 0


@pytest.mark.parametrize(
    ("agent_id", "environment", "index", "expected"),
    [
        ("support-agent", "prod", 0, "support-prod-worker-1"),
        ("support-agent", "prod", 1, "support-prod-worker-2"),
        ("support-agent", "prod", 2, "support-prod-worker-1"),
        ("refund-agent", "dev", 1, "refund-dev-worker-2"),
        ("support-agent", "unassigned", 0, "support-unassigned-worker-1"),
    ],
)
def test_get_preview_instance_id(agent_id, environment, index, expected):
    assert get_preview_instance_id(agent_id, environment, index) == expected


def test_repair_preview_instance_ids():
    def trace(trace_id, agent_id="support-agent", instance_id=None):
        return Trace(
            info=TraceInfo(
                trace_id=trace_id,
                trace_location=TraceLocation.from_experiment_id("2"),
                request_time=123456,
                execution_duration=100,
                state=TraceState.OK,
                tags={AGENT_ID_TAG: agent_id, ENVIRONMENT_TAG: "prod"},
                trace_metadata={
                    "mlflow.sourceRun": "evaluation-run",
                    **({INSTANCE_ID_KEY: instance_id} if instance_id else {}),
                },
            ),
            data=TraceData(spans=[]),
        )

    first = trace("first")
    second = trace("second")
    existing = trace("existing", instance_id="keep-this-instance")
    unrelated = trace("unrelated", agent_id="not-a-preview-agent")
    with (
        mock.patch(
            "dev.seed_agent_registry_mock.MlflowClient",
            **{
                "return_value.get_experiment.return_value.name": EXPERIMENT_NAME,
                "return_value.search_traces.side_effect": [
                    PagedList([first], "page-2"),
                    PagedList([second, existing, unrelated], None),
                    PagedList([first, second, existing, unrelated], None),
                ],
            },
        ) as client_type,
        mock.patch("dev.seed_agent_registry_mock.TracingClient") as tracing_type,
    ):
        client = client_type.return_value
        assert repair_preview_traces("http://localhost:5000", "2") == 2
        assert first.info.trace_metadata[INSTANCE_ID_KEY] == "support-prod-worker-1"
        assert second.info.trace_metadata[INSTANCE_ID_KEY] == "support-prod-worker-2"
        assert first.info.trace_metadata["mlflow.sourceRun"] == "evaluation-run"
        assert existing.info.trace_metadata[INSTANCE_ID_KEY] == "keep-this-instance"
        assert INSTANCE_ID_KEY not in unrelated.info.trace_metadata
        assert repair_preview_traces("http://localhost:5000", "2") == 0
        assert client_type.call_args_list == [
            mock.call(tracking_uri="http://localhost:5000"),
            mock.call(tracking_uri="http://localhost:5000"),
        ]
        assert tracing_type.call_args_list == client_type.call_args_list
        assert client.get_experiment.call_args_list == [mock.call("2"), mock.call("2")]
        assert client.search_traces.call_args_list == [
            mock.call(locations=["2"], max_results=100, page_token=None, include_spans=False),
            mock.call(locations=["2"], max_results=100, page_token="page-2", include_spans=False),
            mock.call(locations=["2"], max_results=100, page_token=None, include_spans=False),
        ]
        assert tracing_type.return_value.start_trace.call_args_list == [
            mock.call(first.info),
            mock.call(second.info),
        ]
        client.set_trace_tag.assert_not_called()


def test_repair_rejects_non_preview_experiment():
    with mock.patch(
        "dev.seed_agent_registry_mock.MlflowClient",
        **{"return_value.get_experiment.return_value.name": "production"},
    ) as client_type:
        with pytest.raises(ValueError, match="Only the agent-registry-preview experiment"):
            repair_preview_traces("http://localhost:5000", "123")
        client_type.assert_called_once_with(tracking_uri="http://localhost:5000")
        client_type.return_value.get_experiment.assert_called_once_with("123")
        client_type.return_value.search_traces.assert_not_called()
