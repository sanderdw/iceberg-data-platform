"""Shared storage identity and independent runtime credentials/lifecycle."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
from docker.errors import NotFound

from server.models import ServiceError
from user_portal.directory import UserSession
from user_portal.runtime import LABEL, NotebookRuntime


@pytest.fixture
def runtime(monkeypatch):
    daemon = Mock()
    volumes = {}

    def get_volume(name):
        if name not in volumes:
            raise NotFound("missing")
        return volumes[name]

    def create_volume(name, labels):
        volumes[name] = Mock(attrs={"Labels": labels})
        return volumes[name]

    daemon.volumes.get.side_effect = get_volume
    daemon.volumes.create.side_effect = create_volume

    def create_network(name, **kwargs):
        network = Mock(attrs={})
        network.name = name
        return network

    daemon.networks.create.side_effect = create_network

    def run_container(*args, **kwargs):
        return Mock(attrs={"NetworkSettings": {"Networks": {kwargs["network"]: {"IPAddress": "10.0.0.2"}}}})

    daemon.containers.run.side_effect = run_container
    monkeypatch.setattr("user_portal.runtime.docker.from_env", lambda **kw: daemon)
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.get.return_value.status_code = 200
    monkeypatch.setattr("user_portal.runtime.httpx.Client", lambda **kw: client)
    return NotebookRuntime({"USER_PORTAL_CONTAINER": "gateway"})


def session(name="sander", environment="development", team="team-a"):
    return UserSession(
        name, name, name, name, f"{name}-secret", "token", 999999999, 999999999, team, environment
    )


def test_oidc_runtime_receives_only_user_access_token(runtime):
    member = replace(session(), oidc_subject="keycloak-subject", secret="", client_id="")
    runtime.start(member, "db1", [], None)
    env = runtime.docker.containers.run.call_args.kwargs["environment"]
    assert env["ICEBERG_ACCESS_TOKEN"] == "token"
    assert "ICEBERG_CLIENT_SECRET" not in env
    assert "ICEBERG_CLIENT_ID" not in env


def mounted_volume(call):
    volumes = call.kwargs["volumes"]
    assert len(volumes) == 1
    name, mount = next(iter(volumes.items()))
    assert mount == {"bind": "/work", "mode": "rw"}
    return name


def test_two_members_two_environments_create_exactly_two_filespaces(runtime):
    members = [session("sander"), session("alex")]
    workspaces = []
    for env in ("development", "production"):
        for member in members:
            workspaces.append(runtime.start(replace(member, environment=env), f"db1-{env}", [], None))
    calls = runtime.docker.containers.run.call_args_list
    assert mounted_volume(calls[0]) == mounted_volume(calls[1])
    assert mounted_volume(calls[2]) == mounted_volume(calls[3])
    assert mounted_volume(calls[0]) != mounted_volume(calls[2])
    assert runtime.docker.volumes.create.call_count == 2
    for call, name in zip(calls, ["sander", "alex", "sander", "alex"], strict=True):
        assert call.kwargs["environment"]["ICEBERG_CLIENT_SECRET"] == f"{name}-secret"
    # Another database in Development reuses the team's existing filespace.
    runtime.start(members[0], "db2-development", [], None)
    assert mounted_volume(runtime.docker.containers.run.call_args) == mounted_volume(calls[0])
    assert runtime.docker.volumes.create.call_count == 2
    assert runtime.start(members[0], "db1-development", [], None) is workspaces[0]
    with pytest.raises(ServiceError):
        runtime.get(workspaces[0].id, members[1])
    runtime.stop_session("sander")
    assert {w.user_id for w in runtime.workspaces.values()} == {"alex"}
    runtime.docker.volumes.get.return_value.remove.assert_not_called()
    # Same user in a second session may mount the shared files concurrently.
    runtime.start(replace(members[1], id="alex-second-session"), "db1-development", [], None)
    assert runtime.docker.volumes.create.call_count == 2


def test_other_teams_get_separate_files_even_for_same_database_name(runtime):
    runtime.start(session(), "db1", [], None)
    runtime.start(session(team="team-b"), "db1", [], None)
    calls = runtime.docker.containers.run.call_args_list
    assert mounted_volume(calls[0]) != mounted_volume(calls[1])


def test_notebooks_allow_egress_with_separate_networks_and_writable_packages(runtime):
    runtime.start(session(), "db1", [], None)
    runtime.start(session("alex"), "db1", [], None)
    networks = runtime.docker.networks.create.call_args_list
    assert networks[0].args[0] != networks[1].args[0]
    for network, container in zip(networks, runtime.docker.containers.run.call_args_list, strict=True):
        assert network.kwargs["internal"] is False
        assert container.kwargs["network"] == network.args[0]
        assert not container.kwargs.get("ports")
        assert container.kwargs["read_only"] is True
        env = container.kwargs["environment"]
        assert env["MARIMO_UV_TARGET"] == "/tmp/packages"
        assert env["MARIMO_UV_TARGET"] in env["PYTHONPATH"].split(":")


def test_notebook_container_joins_the_stack_as_compose_one_off(runtime):
    runtime.start(session(), "db1", [], None)
    labels = runtime.docker.containers.run.call_args.kwargs["labels"]
    assert labels == {
        LABEL: runtime.scope,
        "com.docker.compose.project": runtime.scope,
        "com.docker.compose.service": "notebook",
        "com.docker.compose.oneoff": "True",
    }
    # Networks and volumes stay outside Compose so `down` never touches notebook files.
    assert runtime.docker.networks.create.call_args.kwargs["labels"] == {LABEL: runtime.scope}
    assert runtime.docker.volumes.create.call_args.kwargs["labels"] == {LABEL: runtime.scope}


def test_volume_from_another_stack_is_rejected(runtime):
    runtime.docker.volumes.get.side_effect = None
    runtime.docker.volumes.get.return_value.attrs = {"Labels": {LABEL: "other-stack"}}
    with pytest.raises(ServiceError, match="does not belong"):
        runtime.start(session(), "db1", [], None)
    runtime.docker.containers.run.assert_not_called()
