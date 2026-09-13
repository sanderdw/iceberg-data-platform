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
    daemon.networks.create.side_effect = lambda name, **kw: Mock(name=name, attrs={})

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


def test_volume_from_another_stack_is_rejected(runtime):
    runtime.docker.volumes.get.side_effect = None
    runtime.docker.volumes.get.return_value.attrs = {"Labels": {LABEL: "other-stack"}}
    with pytest.raises(ServiceError, match="does not belong"):
        runtime.start(session(), "db1", [], None)
    runtime.docker.containers.run.assert_not_called()
