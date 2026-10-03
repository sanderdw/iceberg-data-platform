"""Bundled agent skills stay valid against the platform's own input rules."""

import importlib.util
import math
import os
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


@pytest.mark.parametrize("name", ["quick-share", "demo-company", "semantic-model"])
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
    assert "scripts/slips.py" in skill
    skill = (SKILLS / "quick-share/SKILL.md").read_text()
    for asset in (*(SKILLS / "quick-share/assets").iterdir(), *(SKILLS / "quick-share/scripts").glob("*.py")):
        assert asset.name in skill
    assert "demo-company/scripts/slips.py" in skill
    skill = (SKILLS / "semantic-model/SKILL.md").read_text()
    for path in (*(SKILLS / "semantic-model/references").iterdir(), *(SKILLS / "semantic-model/scripts").glob("*.py")):
        assert f"({path.parent.name}/{path.name})" in skill


@pytest.mark.parametrize("name", ["quick-share", "demo-company", "semantic-model"])
def test_skills_work_in_any_coding_agent(name):
    skill = (SKILLS / name / "SKILL.md").read_text()
    # No agent-specific tool names; every Claude Code recipe sits next to the Codex and Copilot ones.
    assert "mcp__" not in skill
    assert skill.count("claude mcp add") == skill.count("codex mcp login") >= 1
    assert "Copilot" in skill


def test_quick_share_tunnels_conversationalbi_only_when_installed():
    compose = yaml.safe_load((SKILLS / "quick-share/assets/compose.quickshare.yaml").read_text())
    tunnel = compose["services"]["cbi-tunnel"]
    assert tunnel["profiles"] == ["conversationalbi"]
    assert tunnel["command"] == "tunnel --url http://gateway:8082"
    assert not any("profiles" in s for name, s in compose["services"].items() if name != "cbi-tunnel")
    caddyfile = (SKILLS / "quick-share/assets/Caddyfile").read_text()
    site, = re.findall(r"^:8082 \{\n(.*?)^\}", caddyfile, re.DOTALL | re.MULTILINE)
    assert "reverse_proxy cbi-gateway:3007" in site


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


@pytest.fixture
def slips():
    spec = importlib.util.spec_from_file_location("slips", SKILLS / "demo-company/scripts/slips.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.qr = lambda url: f'<svg data-url="{url}"></svg>'  # segno is only installed when uv runs the script.
    return module


def credentials_template():
    """The summary format of demo-company/SKILL.md, filled in the way the skill writes it."""
    text = (SKILLS / "demo-company/SKILL.md").read_text()
    template = re.search(r"```markdown\n(# <Company>.*?)```", text, re.DOTALL)[1]
    return (template.replace("<Company>", "Voltara Energy").replace("<N>", "3")
            .replace("<team-name>: <team description>", "grid-operations: Keeps the grid <balanced>")
            .replace("| … | … | … | … | … |", "| 2 | daan-visser | Daan Visser | writer | existing, password unchanged |\n"
                     "| 3 | emma-smit | Emma <Smit> | writer | Tc6Yk1Rn8Jv5Pl0e |")
            .replace("<password>", "Hq7vX2kPz9Lm4Tw8"))


def test_slips_read_the_documented_credentials_format(slips):
    company, portal, people = slips.parse(credentials_template())
    assert company == "Voltara Energy" and portal == "http://localhost:3002"
    assert [(p["number"], p["username"], p["team"], p["role"]) for p in people] == [
        (1, "noor-bakker", "grid-operations", "admin"), (2, "daan-visser", "grid-operations", "writer"),
        (3, "emma-smit", "grid-operations", "writer")]


def test_slips_follow_the_shared_address_and_escape_text(slips, tmp_path):
    credentials = tmp_path / "demo-company-energy-credentials.md"
    credentials.write_text(credentials_template())
    (tmp_path / ".env").write_text("USER_ORIGIN=http://localhost:3002\nUSER_ORIGIN=https://users.trycloudflare.com/\n")
    slips.main([str(credentials)])
    output = tmp_path / "demo-company-energy-slips.html"
    page = output.read_text()
    assert page.count('<article class="slip">') == 3
    assert page.count('data-url="https://users.trycloudflare.com"') == 3 and "localhost" not in page
    assert "Hq7vX2kPz9Lm4Tw8" in page and "Emma &lt;Smit&gt;" in page and "<Smit>" not in page
    # An existing account keeps its password; its slip says so instead of showing a one-time password.
    assert page.count("ONE-TIME") == 2 and "Sign in with your existing password." in page
    if os.name != "nt":
        assert output.stat().st_mode & 0o777 == 0o600
    # A rerun replaces a file that was made readable by others with a new owner-only one.
    output.chmod(0o644)
    slips.main([str(credentials), "--portal", "https://class.example"])
    if os.name != "nt":
        assert output.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*.tmp"))
    assert 'data-url="https://class.example"' in output.read_text()


@pytest.fixture
def semantic_model():
    spec = importlib.util.spec_from_file_location("semantic_model", SKILLS / "semantic-model/scripts/semantic_model.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expression(sql):
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": sql}]}


def shop_model():
    def field(name, **extra):
        return {"name": name, "description": f"The {name}.", "expression": expression(name), **extra}

    return {
        "name": "shop", "description": "Orders. One row in PURCHASE per order.",
        "ai_context": {"instructions": "Time: UTC.\nGrain: one row per order.\nMissing values: none.\n"
                                       "Owner: Sales. Refresh: nightly.\nClassification: internal."},
        "datasets": [
            {"name": "LINE", "source": "lakehouse.shop.lines", "description": "An order line.", "primary_key": ["line_id"],
             "fields": [field("line_id"), field("order_id"), field("amount")]},
            {"name": "PURCHASE", "source": "lakehouse.shop.orders", "description": "An order.", "primary_key": ["order_id"],
             "fields": [field("order_id"), field("customer_id"), field("status", dimension={"is_time": False})]},
            {"name": "CUSTOMER", "source": "lakehouse.shop.customers", "description": "A customer.",
             "primary_key": ["customer_id"], "fields": [field("customer_id"), field("country")]},
        ],
        "relationships": [
            {"name": "line_purchase", "from": "LINE", "to": "PURCHASE", "from_columns": ["order_id"], "to_columns": ["order_id"]},
            {"name": "purchase_customer", "from": "PURCHASE", "to": "CUSTOMER",
             "from_columns": ["customer_id"], "to_columns": ["customer_id"]},
        ],
        "metrics": [{"name": "revenue", "description": "Sum of line amounts. Unit: euro.",
                     "expression": expression("sum(LINE.amount)")}],
    }


def test_semantic_model_check_accepts_a_complete_model(semantic_model):
    columns = {"LINE": {"line_id", "order_id", "amount"}, "PURCHASE": {"order_id", "customer_id", "status"},
               "CUSTOMER": {"customer_id", "country"}}
    assert semantic_model.check_structure(shop_model(), columns) == ([], [])


def test_semantic_model_check_reports_what_blocks_publishing(semantic_model):
    model = shop_model()
    model["ai_context"]["instructions"] = "Time: UTC."
    model["datasets"][1]["fields"][2]["dimension"] = True
    model["datasets"][2]["fields"][1]["description"] = ""
    model["relationships"][0]["to_columns"] = ["id"]
    model["metrics"].append({"name": "orders", "description": "Orders.", "expression": expression("count(*)")})
    errors, warnings = semantic_model.check_structure(model, {"CUSTOMER": {"customer_id"}})
    assert errors == [
        'Field PURCHASE.status: write dimension as {"is_time": true|false}, {} or leave it out.',
        "Field CUSTOMER.country is not a column of the table.",
        "Relationship line_purchase: PURCHASE.id is not a field.",
    ]
    assert "Field CUSTOMER.country has no description." in warnings
    assert "ai_context.instructions does not mention 'grain'." in warnings
    assert "Metric orders: end the description with its unit, for example 'Unit: percent.'" in warnings


def test_semantic_model_metrics_join_from_the_first_dataset_they_name(semantic_model):
    model = shop_model()
    sql = semantic_model.metric_query(model, {"expression": expression("sum(LINE.amount) / count(DISTINCT CUSTOMER.country)")})
    assert sql == ('SELECT sum(LINE.amount) / count(DISTINCT CUSTOMER.country) FROM "LINE"\n'
                   'JOIN "PURCHASE" ON "LINE"."order_id" = "PURCHASE"."order_id"\n'
                   'JOIN "CUSTOMER" ON "PURCHASE"."customer_id" = "CUSTOMER"."customer_id"')
    # Joins also run against the direction of a relationship.
    assert semantic_model.join_path(model, "CUSTOMER", ["PURCHASE"]) == [
        'JOIN "PURCHASE" ON "CUSTOMER"."customer_id" = "PURCHASE"."customer_id"']
    with pytest.raises(ValueError, match="names no dataset"):
        semantic_model.metric_query(model, {"expression": expression("count(*)")})


def test_semantic_model_questions_and_documents(semantic_model):
    assert semantic_model.parse_questions("-- Q: How many?\nSELECT 1;\n\n-- Q: Which?\nSELECT\n  2\n-- Q: empty\n") == [
        ("How many?", "SELECT 1"), ("Which?", "SELECT\n  2")]
    model = shop_model()
    document = semantic_model.Models.document(model)
    assert document["version"] == semantic_model.SPEC_VERSION
    assert semantic_model.first_model(document) == model
    assert semantic_model.first_model({"version": "0.2.0", "semantic_model": [model]}) == model
    assert semantic_model.table_reference(["sales", "eu"], "orders") == '"lakehouse"."sales.eu"."orders"'


def test_semantic_model_check_follows_ossie_and_governed_queries(semantic_model):
    model = shop_model()
    purchase = model["datasets"][1]["fields"]
    purchase[2].update(dimension={}, datatype="String")
    purchase.append({"name": "day", "description": "Order day.", "expression": expression("CAST(ordered_at AS DATE)"),
                     "dimension": {}, "datatype": "Date", "ai_context": {"synonyms": ["order date"]}})
    model["metrics"][0]["datatype"] = "Decimal"
    assert semantic_model.check_structure(model) == ([], [])

    purchase[3]["datatype"] = "Time"
    purchase[2]["datatype"] = "varchar"
    # Against the join direction: CUSTOMER is the one side, and customer_id is not PURCHASE's key.
    model["relationships"][1].update({"from": "CUSTOMER", "to": "PURCHASE"})
    errors, warnings = semantic_model.check_structure(model)
    assert errors == [("Field PURCHASE.status: datatype 'varchar' is not an Ossie datatype (String, Integer, Decimal, "
                       "Float, Boolean, Date, Time, DateTime, DateTimeTz, Opaque).")]
    assert warnings == [
        ("Field PURCHASE.day is a derived time dimension without a date or timestamp datatype, so governed queries "
         'cannot group it by day, week or month. Add "datatype": "Date".'),
        ("Relationship purchase_customer: PURCHASE.customer_id is not the whole primary key of PURCHASE, so governed "
         "queries do not join along it. Point it from the many side to the one side's primary key."),
    ]


@pytest.mark.parametrize("change", [
    lambda m: m,
    lambda m: m.update(description=""),
    lambda m: m["ai_context"].update(instructions="Time: UTC."),
    lambda m: m["datasets"][1]["fields"][2].update(dimension=True),
    lambda m: m["datasets"][0].update(primary_key=["nope"]),
    lambda m: m["relationships"][0].update(to_columns=["id"]),
    lambda m: m["relationships"][0].update(from_columns=["line_id"], to_columns=["customer_id"]),
    lambda m: m["datasets"][1]["fields"][2].update(dimension={}),
    lambda m: m["datasets"][1]["fields"][2].update(datatype="varchar"),
    lambda m: m["metrics"][0].update(datatype="money"),
    lambda m: m["datasets"][1]["fields"].append(
        {"name": "day", "description": "Day.", "expression": expression("CAST(ordered_at AS DATE)"),
         "dimension": {"is_time": True}}),
    lambda m: m["metrics"].append({"name": "revenue", "description": "Again.", "expression": expression("count(*)")}),
])
def test_semantic_model_skill_and_portal_validate_alike(semantic_model, change):
    from user_portal.semantic import rules

    model = shop_model()
    change(model)
    assert semantic_model.check_structure(model) == rules.check_structure(model)
