"""Bundled agent skills stay valid against the platform's own input rules."""

import importlib.util
import math
import re
from pathlib import Path

import pytest
import yaml

from server.identity import CreateIdentity
from server.models import DatabaseInput, TeamInput

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".agents/skills"
DOMAINS = ("energy", "webshop", "retail")
TEAM_ID = "team-" + "0" * 32


def frontmatter(path):
    match = re.match(r"^---\n(.*?)\n---\n", path.read_text(), re.DOTALL)
    assert match, f"{path} needs YAML frontmatter"
    return yaml.safe_load(match[1])


def yaml_block(name):
    text = (SKILLS / "demo-company/references" / f"{name}.md").read_text()
    block, = re.findall(r"```yaml\n(.*?)```", text, re.DOTALL)
    return yaml.safe_load(block)


def class_plan(participants, teams):
    """The sizing rules of demo-company/SKILL.md for a fresh installation."""
    used = teams[:min(len(teams), math.ceil(participants / 4))]
    members = {team["name"]: [] for team in used}
    for _ in range(participants):
        # Fewest members first; ties go to the earlier team (dicts keep blueprint order).
        team = min(members, key=lambda name: len(members[name]))
        members[team].append("writer" if "admin" in members[team] else "admin")
    return members


@pytest.mark.parametrize("name", ["quick-share", "demo-company"])
def test_skill_frontmatter_names_its_folder(name):
    meta = frontmatter(SKILLS / name / "SKILL.md")
    assert meta["name"] == name
    assert 20 < len(meta["description"]) <= 1024


@pytest.mark.parametrize("domain", DOMAINS)
def test_blueprint_passes_the_platform_input_rules(domain):
    company = yaml_block(domain)
    assert company["email_domain"].endswith(".example")
    assert len(company["teams"]) == 6
    databases = set()
    for team in company["teams"]:
        TeamInput(name=team["name"], description=team["description"])
        assert sorted(d["environment"] for d in team["databases"]) == ["development", "production"]
        for database in team["databases"]:
            DatabaseInput(team=TEAM_ID, **database)
            databases.add(database["name"])
    assert len(databases) == 12
    for person in yaml_block("people")["people"]:
        # Exactly one membership per person, with a role create_user accepts.
        CreateIdentity(name=person["username"], email=f"{person['username']}@{company['email_domain']}",
                       first_name=person["first_name"], last_name=person["last_name"],
                       memberships=[{"team": TEAM_ID, "role": "writer"}])


def test_people_pool_covers_the_largest_class():
    usernames = [p["username"] for p in yaml_block("people")["people"]]
    assert len(usernames) == len(set(usernames)) == 48


@pytest.mark.parametrize("participants", range(1, 49))
def test_every_class_size_gets_balanced_teams_without_readers(participants):
    plan = class_plan(participants, yaml_block("energy")["teams"])
    sizes = [len(roles) for roles in plan.values()]
    assert sum(sizes) == participants
    assert max(sizes) - min(sizes) <= 1 and min(sizes) >= 1
    assert all(roles.count("admin") == 1 and set(roles) <= {"admin", "writer"} for roles in plan.values())


@pytest.mark.parametrize("participants,sizes", [(1, [1]), (4, [4]), (5, [3, 2]), (10, [4, 3, 3]),
                                                (30, [5] * 6), (48, [8] * 6)])
def test_documented_class_sizes(participants, sizes):
    assert [len(roles) for roles in class_plan(participants, yaml_block("retail")["teams"]).values()] == sizes


def test_skill_files_are_linked_from_the_skill():
    skill = (SKILLS / "demo-company/SKILL.md").read_text()
    for reference in (*DOMAINS, "people"):
        assert f"(references/{reference}.md)" in skill
    skill = (SKILLS / "quick-share/SKILL.md").read_text()
    for asset in (*(SKILLS / "quick-share/assets").iterdir(), *(SKILLS / "quick-share/scripts").glob("*.py")):
        assert asset.name in skill


@pytest.mark.parametrize("name", ["quick-share", "demo-company"])
def test_skills_work_in_any_coding_agent(name):
    skill = (SKILLS / name / "SKILL.md").read_text()
    # No agent-specific tool names; every Claude Code recipe sits next to the Codex and Copilot ones.
    assert "mcp__" not in skill
    assert skill.count("claude mcp add") == skill.count("codex mcp login") >= 1
    assert "Copilot" in skill


@pytest.fixture
def sync_origins():
    spec = importlib.util.spec_from_file_location("sync_origins", SKILLS / "quick-share/scripts/sync_origins.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Response:
    def __init__(self, data=None):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class Keycloak:
    def __init__(self, client):
        self.client = client
        self.puts = []

    def get(self, path, params):
        assert (path, params) == ("/admin/realms/iceberg/clients", {"clientId": self.client["clientId"]})
        return Response([self.client])

    def put(self, path, json):
        self.puts.append((path, json))
        return Response()


def test_sync_origins_sets_redirects_for_every_origin(sync_origins):
    kc = Keycloak({"id": "abc", "clientId": "iceberg-admin", "secret": "keep",
                   "attributes": {"pkce.code.challenge.method": "S256"}})
    origins = ["https://admin.trycloudflare.com", "http://localhost:3000"]
    sync_origins.update_client(kc, "/admin/realms/iceberg", "iceberg-admin", origins)
    (path, client), = kc.puts
    assert path == "/admin/realms/iceberg/clients/abc"
    assert client["redirectUris"] == ["https://admin.trycloudflare.com/auth/callback", "http://localhost:3000/auth/callback"]
    assert client["webOrigins"] == origins
    assert client["attributes"] == {"pkce.code.challenge.method": "S256",
                                    "post.logout.redirect.uris": "https://admin.trycloudflare.com/##http://localhost:3000/"}
    assert client["secret"] == "keep"


def test_sync_origins_moves_only_users_of_the_old_issuer(sync_origins):
    old, new = "http://localhost:8080/realms/iceberg", "https://auth.trycloudflare.com/realms/iceberg"
    principals = [
        {"name": "portal-a", "properties": {"portal.oidc-issuer": old, "portal.oidc-subject": "s1"}},
        {"name": "portal-b", "properties": {"portal.oidc-issuer": "https://other/realms/iceberg"}},
        {"name": "root", "properties": {}},
    ]

    class Provider:
        def __init__(self):
            self.updates = []

        def management(self, path):
            assert path == "/principals"
            return {"principals": principals}

        def update_properties(self, path, properties):
            self.updates.append((path, properties))

    provider = Provider()
    assert sync_origins.migrate_issuer(provider, old + "/", new, lambda v: v) == ["portal-a"]
    assert provider.updates == [("/principals/portal-a", {"portal.oidc-issuer": new, "portal.oidc-subject": "s1"})]
    assert sync_origins.migrate_issuer(provider, new, new, lambda v: v) == []


@pytest.mark.parametrize("value,expected", [
    ("https://admin.trycloudflare.com/", "https://admin.trycloudflare.com"),
    ("https://10.1.2.3:3000", "https://10.1.2.3:3000"),
    ("http://localhost:3000", "http://localhost:3000"),
])
def test_sync_origins_accepts_origins(sync_origins, value, expected):
    assert sync_origins.origin(value) == expected


@pytest.mark.parametrize("value", ["http://10.1.2.3:3000", "https://10.1.2.3:3000/app", "ftp://host", "10.1.2.3"])
def test_sync_origins_rejects_insecure_or_invalid_origins(sync_origins, value):
    with pytest.raises(Exception, match="origin|localhost"):
        sync_origins.origin(value)
