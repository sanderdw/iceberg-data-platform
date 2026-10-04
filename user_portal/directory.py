"""Trusted metadata directory plus catalog requests made as the actual user."""

import os
import time
from dataclasses import dataclass, field

import httpx
from pydantic import ValidationError

from server import extensions
from server.models import DatabaseInput, Environment, ServiceError
from server.polaris import MAX_SHARES, PolarisProvider, enc
from server.storage import RustFSStorage
from server.validation import validation_message

from .catalog import object_details, properties, semantic_model_details
from .semantic import compiler, ossie, rules
from .semantic.errors import SemanticError


def validate_namespace(parts):
    if len(parts) > 100 or any(
        not p or len(p) > 256 or p in (".", "..") or "\x1f" in p or "\0" in p for p in parts
    ):
        raise ServiceError(422, "Invalid namespace.")


def shared_objects(info, kind, namespace):
    """The objects of one kind that a share names in one namespace, sorted by name."""
    return sorted(
        [{"name": o["name"], "namespace": o["namespace"]} for o in info["sharedObjects"]
         if o["kind"] == kind and o["namespace"] == namespace],
        key=lambda o: o["name"],
    )


@dataclass
class UserSession:
    id: str
    user_id: str
    name: str
    client_id: str
    secret: str = field(repr=False)
    token: str = field(repr=False)
    token_until: float
    expires: float
    team: str
    environment: Environment = "development"
    oidc_subject: str = ""
    oidc_issuer: str = ""
    oidc_session: object | None = field(default=None, repr=False)
    # MCP sessions have no active team: they see the shares received by every team of the user.
    all_teams: bool = False


class UserDirectory:
    def __init__(self, env):
        # This client stays in the trusted gateway, never in a notebook runtime.
        self.metadata = PolarisProvider(env, RustFSStorage(env))
        self.http = httpx.Client(timeout=15)
        self.url = env.get("POLARIS_URL", "http://polaris:8181").rstrip("/")
        self.s3_endpoint = env.get("USER_S3_ENDPOINT", "http://rustfs:9000")

    def close(self):
        self.metadata.close()
        self.http.close()

    def authenticate(self, client_id, secret):
        try:
            response = self.http.post(
                f"{self.url}/api/catalog/v1/oauth/tokens",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": secret,
                    "scope": "PRINCIPAL_ROLE:ALL",
                },
                headers={"Polaris-Realm": "POLARIS"},
            )
        except httpx.HTTPError as exc:
            raise ServiceError(503, "The data provider is unavailable.") from exc
        if response.status_code in (400, 401, 403):
            raise ServiceError(401, "Incorrect username or client secret.")
        if response.is_error:
            raise ServiceError(502, "Sign-in to the data provider failed.")
        result = response.json()
        return result["access_token"], time.monotonic() + max(0, result["expires_in"] - 30)

    def login(self, username, secret, session_id):
        user = next((u for u in self.metadata.list_users() if u["name"] == username), None)
        if not user or not user.get("clientId"):
            raise ServiceError(401, "Incorrect username or client secret.")
        token, token_until = self.authenticate(user["clientId"], secret)
        teams = [t for t in self.metadata.list_teams() if t["id"] in user["teams"]]
        if not teams:
            raise ServiceError(403, "You are not assigned to an available team.")
        return UserSession(
            session_id,
            user["id"],
            user["name"],
            user["clientId"],
            secret,
            token,
            token_until,
            time.monotonic() + 28800,
            teams[0]["id"],
        )

    def profile(self, session):
        # Re-read metadata so deletion, team removal and moves affect active sessions.
        try:
            principal = self.metadata.require_user(f"/principals/{enc(session.user_id)}")
        except ServiceError as exc:
            if exc.status == 404:
                raise ServiceError(401, "Your user no longer exists. Sign in again.") from exc
            raise
        if session.oidc_subject and (
            principal["properties"].get("portal.oidc-subject") != session.oidc_subject
            or principal["properties"].get("portal.oidc-issuer") != session.oidc_issuer
            or principal["properties"].get("portal.identity-status", "linked") != "linked"
        ):
            raise ServiceError(403, "Your identity is no longer linked to this user.")
        user = self.metadata.user(principal)
        roles = {m["team"]: m["role"] for m in user["memberships"]}
        all_teams = self.metadata.list_teams()
        team_names = {t["id"]: t["name"] for t in all_teams}
        teams = [{**t, "role": roles[t["id"]]} for t in all_teams if t["id"] in roles]
        if not teams:
            raise ServiceError(403, "You no longer have any available teams.")
        all_databases = self.metadata.list_databases()
        databases = [
            d
            for d in all_databases
            if d["team"] in {t["id"] for t in teams}
        ]
        recipients = [t["id"] for t in teams] if session.all_teams else [session.team] if session.team in roles else []
        received, recipient = {}, {}
        if recipients:
            for share in self.metadata.list_shares(drift=True):
                if share.get("recipientTeam") not in recipients:
                    continue
                # Team-share grants sit on each member's own role, so shares received by
                # several of the user's teams combine.
                received.setdefault(share["database"], []).extend(
                    {**o, "granted": True} for o in self.metadata.received_objects(share)
                )
                recipient[share["database"]] = min(
                    recipient.get(share["database"], share["recipientTeam"]), share["recipientTeam"],
                    key=recipients.index,
                )
        owners = set(recipients) if session.all_teams else {session.team}
        shared_databases = [
            {**d, "shared": True, "sharedWithTeam": recipient[d["id"]],
             "ownerTeamName": team_names.get(d["team"], d["team"]),
             "sharedObjects": list({(o["kind"], tuple(o["namespace"]), o["name"]): o
                                    for o in received[d["id"]]}.values())}
            for d in all_databases
            if d["id"] in received and d["team"] not in owners and d["status"] == "ready"
        ]
        return {
            "sharedDatabases": shared_databases,
            "user": {"id": user["id"], "name": user["name"]},
            "teams": teams,
            "databases": [d for d in databases if d["status"] == "ready"],
            "deletingDatabases": [d for d in databases if d["status"] == "deleting"],
        }

    def manage_team(self, session):
        profile = self.profile(session)
        team = next((t for t in profile["teams"] if t["id"] == session.team), None)
        if not team or team["role"] not in ("admin", "bucket-admin"):
            raise ServiceError(403, "Only team administrators can manage databases.")
        return team

    def manage_database(self, session, id, *, allow_deleting=False):
        team = self.manage_team(session)
        catalog = self.metadata.require(f"/catalogs/{enc(id)}")
        if not self.metadata.managed(catalog):
            raise ServiceError(404, "Database not found.")
        database = self.metadata.database(catalog)
        if database["team"] != team["id"] or database["environment"] != session.environment:
            raise ServiceError(403, "This database is unavailable within your active team and environment.")
        if database["status"] == "deleting" and not allow_deleting:
            raise ServiceError(409, "This database is being deleted.")
        return database

    def create_database(self, session, name, description=""):
        self.manage_team(session)
        return self.metadata.create_database(DatabaseInput(
            name=name, description=description, team=session.team, environment=session.environment,
        ))

    def rename_database(self, session, id, name):
        self.manage_database(session, id)
        return self.metadata.rename_database(
            id, name, expected_team=session.team, expected_environment=session.environment,
        )

    def delete_database(self, session, id, confirm_name):
        database = self.manage_database(session, id, allow_deleting=True)
        if confirm_name != database["name"]:
            raise ServiceError(422, "Type the database name exactly to confirm deletion.")
        self.metadata.delete_database(
            id, expected_team=session.team, expected_environment=session.environment, expected_name=confirm_name,
        )
        return {"deleted": True, "database": id, "name": database["name"], "environment": database["environment"]}

    def team_members(self, session):
        profile = self.profile(session)
        team = next((t for t in profile["teams"] if t["id"] == session.team), None)
        if not team:
            raise ServiceError(403, "You are not a member of this team.")
        members = [
            {"id": user["id"], "name": user["name"], "role": membership["role"]}
            for user in self.metadata.list_users()
            for membership in user["memberships"]
            if membership["team"] == team["id"]
        ]
        return {"team": team["id"], "members": sorted(members, key=lambda member: member["name"].casefold())}

    def database(self, session, database, profile=None):
        profile = profile or self.profile(session)
        result = next(
            (
                d
                for d in [*profile["databases"], *profile.get("sharedDatabases", [])]
                if d["id"] == database
                and (d["team"] == session.team or d.get("sharedWithTeam") == session.team)
                and d["environment"] == session.environment
            ),
            None,
        )
        if not result:
            raise ServiceError(403, "This database is unavailable within your active team and environment.")
        return result

    def share_database(self, session, database):
        """The database and caller, only for a current administrator of the owning team.

        Shares are written with the platform identity, so this check is the whole
        authorization. It is re-read from Polaris on every request, never cached.
        """
        profile = self.profile(session)
        result = self.database(session, database, profile)
        if result.get("shared"):
            raise ServiceError(403, "Only the owning team can manage this data share.")
        role = next(t["role"] for t in profile["teams"] if t["id"] == result["team"])
        if role not in ("admin", "bucket-admin"):
            raise ServiceError(403, "Only team administrators can manage data shares.")
        return result, profile["user"]

    def share(self, session, id):
        principal = self.metadata.require_share(id)
        self.share_database(session, principal["properties"]["portal.database"])
        return principal

    def share_teams(self, session):
        """Teams that the active team can share with: every other team."""
        self.manage_team(session)
        return [{"id": t["id"], "name": t["name"]} for t in self.metadata.list_teams() if t["id"] != session.team]

    def received_shares(self, session):
        """Shares received by the active team, or by every team of an MCP session, in every environment."""
        profile = self.profile(session)
        teams = {t["id"] for t in profile["teams"]}
        if not session.all_teams and session.team not in teams:
            raise ServiceError(403, "You are not a member of this team.")
        recipients = teams if session.all_teams else {session.team}
        databases = {d["id"]: d for d in self.metadata.list_databases()
                     if d["status"] == "ready" and (session.all_teams or d["environment"] == session.environment)}
        team_names = {t["id"]: t["name"] for t in self.metadata.list_teams()}
        return [{"id": s["id"], "name": s["name"], "description": s["description"],
                 "database": s["database"], "databaseName": databases[s["database"]]["name"],
                 "environment": databases[s["database"]]["environment"], "recipientTeam": s["recipientTeam"],
                 "ownerTeam": databases[s["database"]]["team"],
                 "ownerTeamName": team_names.get(databases[s["database"]]["team"], databases[s["database"]]["team"]),
                 "objects": s["objects"], "expiresAt": s["expiresAt"]}
                for s in self.metadata.list_shares(drift=True)
                if s.get("recipientTeam") in recipients and s["database"] in databases]

    def outgoing_shares(self, session, database):
        if self.database(session, database).get("shared"):
            raise ServiceError(403, "Only the owning team can list outgoing shares.")
        return {
            "shares": self.metadata.list_shares(database, drift=True),
            "limits": {"shares": MAX_SHARES, "objects": 50},
        }

    def semantic_model_tables(self, session, database, namespace, name):
        """The Iceberg tables that a semantic model's datasets read, each once."""
        detail = self.details(session, database, namespace, "semantic-model", name)
        tables = {}
        for dataset in (d for m in detail["models"] for d in m["datasets"]):
            if dataset["table"]:
                table = {"kind": "table", **dataset["table"]}
                tables[(tuple(table["namespace"]), table["name"])] = table
        return list(tables.values())

    def model_tables(self, session, database, data, add):
        """Check that the tables the selected semantic models read are selected too.

        A model only describes tables; the recipient reads them with the same credential. With
        `add` the missing tables join the selection, as selecting a model does in the portal;
        otherwise each one is a warning. A model that is missing, hidden or switched off is skipped,
        as in the portal; an unavailable provider fails the request, so a retry adds the tables.
        """
        objects = [o.model_dump() for o in data.objects]
        selected = {(o["kind"], tuple(o["namespace"]), o["name"]) for o in objects}
        added, warnings = [], []
        for model in [o for o in objects if o["kind"] == "semantic-model"]:
            try:
                tables = self.semantic_model_tables(session, database, model["namespace"], model["name"])
            except ServiceError as exc:
                if exc.status not in (403, 404, 406):
                    raise
                continue
            for table in tables:
                key = ("table", tuple(table["namespace"]), table["name"])
                if key in selected:
                    continue
                path = ".".join([*table["namespace"], table["name"]])
                if add:
                    selected.add(key)
                    objects.append(table)
                    added.append(table)
                else:
                    warnings.append(f"{model['name']} reads {path}, which is not selected.")
        if added:
            try:
                data = type(data).model_validate({**data.model_dump(by_alias=True, exclude_unset=True), "objects": objects})
            except ValidationError as exc:
                raise ServiceError(422, validation_message(exc.errors(), "/api/shares")) from None
        return data, added, warnings

    def create_share(self, session, data, *, include_model_tables=False):
        database, user = self.share_database(session, data.database)
        data, added, warnings = self.model_tables(session, data.database, data, include_model_tables)
        created = self.metadata.create_share(data, user, expected_team=database["team"])
        return {**created, "addedTables": added, "warnings": warnings}

    def update_share(self, session, id, data, *, include_model_tables=False):
        principal = self.share(session, id)
        added, warnings = [], []
        if data.objects is not None:
            database = principal["properties"]["portal.database"]
            data, added, warnings = self.model_tables(session, database, data, include_model_tables)
        return {"share": self.metadata.update_share(id, data), "addedTables": added, "warnings": warnings}

    def rotate_share(self, session, id):
        self.share(session, id)
        return self.metadata.rotate_share(id)

    def delete_share(self, session, id, confirm_name=None):
        principal = self.share(session, id)
        name = principal["properties"]["portal.name"]
        if confirm_name is not None and confirm_name != name:
            raise ServiceError(422, "Type the share name exactly to confirm revoking it.")
        self.metadata.delete_share(id)
        return {"deleted": True, "id": id, "name": name}

    def request(self, session, path):
        return self.send(session, "GET", path)

    def send(self, session, method, path, body=None):
        """One catalog request as the user. Polaris's own messages are never passed on: they can echo input."""
        if session.token_until <= time.monotonic():
            if session.oidc_subject:
                raise ServiceError(401, "Your Keycloak session expired. Sign in again.")
            session.token, session.token_until = self.authenticate(session.client_id, session.secret)
        headers = {"Authorization": f"Bearer {session.token}", "Polaris-Realm": "POLARIS"}
        try:
            if method == "GET":
                response = self.http.get(self.url + path, headers=headers)
            else:
                response = self.http.request(method, self.url + path, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ServiceError(503, "The data provider is unavailable.") from exc
        if response.is_error:
            # 406: Polaris has this feature switched off. 503: it is temporarily unavailable, and retrying helps.
            # 400 and 409 only answer changes: a rejected document, or a newer version than the one read.
            known = (401, 403, 404, 406, 503) + ((400, 409) if method != "GET" else ())
            status = response.status_code if response.status_code in known else 502
            if status == 503:
                raise ServiceError(503, "The data provider is temporarily unavailable. Try again shortly.")
            if status == 406:
                raise ServiceError(406, "Semantic models are switched off in Polaris.")
            if status == 400:
                raise ServiceError(400, "The catalog rejected this request. Names use letters, digits, - and _.")
            if status == 409:
                raise ServiceError(409, "Someone changed this object meanwhile. Read it again and retry.")
            if status == 403 and method != "GET":
                raise ServiceError(403, "Your role may not change this. Changing semantic models needs the Writer role.")
            raise ServiceError(status, "This content is unavailable with your permissions.")
        return response.json() if response.content else {}

    def login_oidc(self, claims, token, lifetime, session_id):
        mapping = claims.get("polaris", {})
        name = mapping.get("principal_name")
        if not isinstance(name, str) or not name.startswith("portal-"):
            raise ServiceError(403, "Your identity is not linked to a platform user.")
        principal = self.metadata.require_user(f"/principals/{enc(name)}")
        # Polaris's management API does not expose numeric entity IDs. Zero
        # requests lookup by the immutable portal principal name, not username.
        if mapping.get("principal_id") != 0:
            raise ServiceError(403, "Your identity mapping is invalid.")
        user = self.metadata.user(principal)
        until = time.monotonic() + lifetime
        session = UserSession(
            session_id, user["id"], user["name"], "", "", token, until, until, "",
            oidc_subject=claims["sub"], oidc_issuer=claims["iss"],
        )
        profile = self.profile(session)
        session.team = profile["teams"][0]["id"]
        # Exercise the external token against Polaris before accepting the login.
        if profile["databases"]:
            self.request(session, "/api/catalog/v1/config?warehouse=" + enc(profile["databases"][0]["id"]))
        return session

    def pages(self, session, path, key):
        values, seen, token = [], set(), None
        while True:
            url = path + (("&" if "?" in path else "?") + f"pageToken={enc(token)}" if token else "")
            page = self.request(session, url)
            values.extend(page.get(key, []))
            token = page.get("next-page-token")
            if not token:
                return values
            if token in seen:
                raise ServiceError(502, "The data provider returned invalid pagination.")
            seen.add(token)

    def semantic_models(self, session, database, namespace):
        """Semantic model names in a namespace; none when the feature is off or the role cannot list them."""
        path = (
            f"/api/catalog/polaris/v1/{enc(database)}/namespaces/{enc(chr(31).join(namespace))}/semantic-models"
        )
        try:
            items = self.pages(session, path, "identifiers")
        except ServiceError as exc:
            if exc.status in (403, 404, 406):
                return []
            raise
        return sorted(
            [{"name": m["name"], "namespace": m["namespace"]} for m in items], key=lambda m: m["name"]
        )

    def list_semantic_models(self, session, database, namespace):
        """The semantic models of one namespace, after the same access check as `contents`."""
        info = self.database(session, database)
        if info.get("shared"):
            return shared_objects(info, "semantic-model", namespace)
        return self.semantic_models(session, database, namespace)

    def contents(self, session, database, namespace):
        info = self.database(session, database)
        if info.get("shared"):
            objects = info["sharedObjects"]
            children = sorted({tuple(o["namespace"][:len(namespace) + 1]) for o in objects
                               if o["namespace"][:len(namespace)] == namespace
                               and len(o["namespace"]) > len(namespace)})
            return {
                "database": database, "namespace": namespace,
                "namespaces": [list(parts) for parts in children],
                "tables": shared_objects(info, "table", namespace),
                "views": shared_objects(info, "view", namespace),
                # The share role cannot list: show exactly the models the share names.
                "semanticModels": shared_objects(info, "semantic-model", namespace),
            }
        prefix = f"/api/catalog/v1/{enc(database)}"
        encoded = enc("\x1f".join(namespace))
        children = self.pages(
            session, f"{prefix}/namespaces" + (f"?parent={encoded}" if namespace else ""), "namespaces"
        )
        result = {
            "database": database,
            "namespace": namespace,
            "namespaces": sorted(children),
            "tables": [],
            "views": [],
            "semanticModels": [],
        }
        if namespace:
            for kind in ("tables", "views"):
                items = self.pages(session, f"{prefix}/namespaces/{encoded}/{kind}", "identifiers")
                result[kind] = sorted(
                    [{"name": t["name"], "namespace": t["namespace"]} for t in items], key=lambda t: t["name"]
                )
            result["semanticModels"] = self.semantic_models(session, database, namespace)
        return result

    def details(self, session, database, namespace, kind, name=None):
        database_info = self.database(session, database)
        if kind == "database":
            return {
                "kind": kind,
                "database": database_info,
                "catalogUri": self.metadata.public_url + "/api/catalog",
            }
        if database_info.get("shared"):
            objects = database_info["sharedObjects"]
            if kind == "namespace":
                if not any(o["namespace"][:len(namespace)] == namespace for o in objects):
                    raise ServiceError(404, "Namespace not shared with this team.")
                return {"kind": kind, "namespace": namespace, "properties": {}}
            if not any(o["kind"] == kind and o["namespace"] == namespace and o["name"] == name
                       for o in objects):
                raise ServiceError(403, "This object is not shared with your active team.")
        path = f"/api/catalog/v1/{enc(database)}/namespaces/{enc(chr(31).join(namespace))}"
        if kind == "namespace":
            loaded = self.request(session, path)
            return {
                "kind": kind,
                "namespace": namespace,
                "properties": properties(loaded.get("properties", {})),
            }
        if kind == "semantic-model":
            polaris = f"/api/catalog/polaris/v1/{enc(database)}/namespaces/{enc(chr(31).join(namespace))}"
            return semantic_model_details(
                name, namespace, self.request(session, f"{polaris}/semantic-models/{enc(name)}")
            )
        loaded = self.request(session, f"{path}/{kind}s/{enc(name)}")
        return {"name": name, "namespace": namespace,
                **object_details(kind, loaded, extensions.extensions(os.environ))}

    def semantic_model_path(self, database, namespace, name=None):
        path = f"/api/catalog/polaris/v1/{enc(database)}/namespaces/{enc(chr(31).join(namespace))}/semantic-models"
        return path + (f"/{enc(name)}" if name else "")

    def load_semantic_model(self, session, database, namespace, name):
        """The model as Polaris stores it, after the same access check as `details`."""
        info = self.database(session, database)
        if info.get("shared") and not any(
            o["kind"] == "semantic-model" and o["namespace"] == namespace and o["name"] == name
            for o in info["sharedObjects"]
        ):
            raise ServiceError(403, "This object is not shared with your active team.")
        return self.request(session, self.semantic_model_path(database, namespace, name))

    def publish_semantic_model(self, session, database, namespace, name, model, entity_version=None):
        """Create a model, or replace the version the caller read; Polaris checks the Writer role."""
        targets = [(d.get("name"), *target) for d in rules.dicts(rules.first_model(model).get("datasets"))
                   if (target := ossie.dataset_target(d.get("source"), namespace))]
        schemas, unreadable = self.semantic_tables(session, database, targets)
        model, warnings = rules.validate(model, {name: set(columns) for name, columns in schemas.items()})
        warnings = [f"Table {table} is missing or not readable, so its fields were not checked against its columns."
                    for table in unreadable] + warnings
        document = rules.document(model)
        try:
            current = self.request(session, self.semantic_model_path(database, namespace, name))
        except ServiceError as exc:
            if exc.status != 404:
                raise
            current = None
        if current is None:
            if entity_version is not None:
                raise ServiceError(404, f"There is no semantic model {name!r} to replace. Leave out entity_version.")
            created = self.send(session, "POST", self.semantic_model_path(database, namespace),
                                {"name": name, "document": document})
            action, version = "created", created.get("entity-version")
        else:
            stored = current.get("entity-version")
            if entity_version is None or str(entity_version) != str(stored):
                raise ServiceError(409, f"Semantic model {name!r} exists at version {stored}. Read it with "
                                        "describe_semantic_model, and pass that entityVersion to replace it.")
            updated = self.send(session, "PUT", self.semantic_model_path(database, namespace, name),
                                {"document": document, "entity-version": stored})
            action, version = "updated", updated.get("entity-version")
        return {"action": action, "name": name, "namespace": namespace, "entityVersion": version,
                "warnings": warnings}

    def delete_semantic_model(self, session, database, namespace, name, confirm_name):
        if confirm_name != name:
            raise ServiceError(422, "Type the semantic model's name exactly to confirm deleting it.")
        self.send(session, "DELETE", self.semantic_model_path(database, namespace, name))
        return {"deleted": True, "name": name, "namespace": namespace}

    def semantic_query_guide(self, session, database, namespace, name):
        """What query_semantic_model can answer: each metric's dataset and the dimensions reachable from it."""
        try:
            model = ossie.parse(self.load_semantic_model(session, database, namespace, name), tuple(namespace))
        except SemanticError as exc:
            return {"metrics": {}, "dimensions": {}, "problems": [str(exc)]}
        homes, problems = {}, []
        for metric in model.metrics:
            try:
                homes[metric.name] = compiler.metric_home(model, metric, compiler.expressions.parse(metric.expression))
            except SemanticError as exc:
                problems.append(f"Metric {metric.name}: {exc}")
        return {"metrics": homes,
                "dimensions": {home: compiler.reachable(model, home) for home in sorted({h for h in homes.values() if h})},
                "problems": problems}

    def semantic_tables(self, session, database, targets):
        """{dataset: {column: Iceberg type}} for `(dataset, namespace, table)` targets, and the tables not readable."""
        schemas, unreadable = {}, []
        for dataset, namespace, table in targets:
            try:
                found = self.details(session, database, list(namespace), "table", table)
            except ServiceError as exc:
                if exc.status not in (403, 404):
                    raise
                unreadable.append(".".join([*namespace, table]))
                continue
            schemas[dataset] = {c["name"]: c["type"] for c in found["columns"] if "." not in c["name"]}
        return schemas, unreadable

    def semantic_job(self, session, database, compiled, unreadable):
        """The worker job for a compiled governed query, as long as every table it binds is readable."""
        missing = sorted({".".join([*b["namespace"], b["table"]]) for b in compiled.bindings} & set(unreadable))
        if missing:
            raise SemanticError(403, "A table this query needs is missing or not readable with your permissions: "
                                     + ", ".join(missing) + ".", "table_not_readable", {"tables": missing})
        return {"uri": self.url + "/api/catalog", "warehouse": database, "token": session.token,
                "s3Endpoint": self.s3_endpoint, "bindings": compiled.bindings, "sql": compiled.sql,
                "params": compiled.params, "limit": compiled.limit}

    def semantic_query_job(self, session, database, namespace, name, spec, max_rows=200):
        """Compile a governed query from the stored model, to run as the user in a worker process."""
        loaded = self.load_semantic_model(session, database, namespace, name)
        model = ossie.parse(loaded, tuple(namespace))
        schemas, unreadable = self.semantic_tables(
            session, database, [(d.name, d.namespace, d.table) for d in model.datasets])
        compiled = compiler.compile_query(model, schemas, spec, max_rows)
        return model, compiled, self.semantic_job(session, database, compiled, unreadable)

    def semantic_values_job(self, session, database, namespace, name, field, search=None, limit=50):
        """Compile a lookup of one dimension's distinct values, to run as the user in a worker process."""
        model = ossie.parse(self.load_semantic_model(session, database, namespace, name), tuple(namespace))
        wanted = field.partition(".")[0]
        schemas, unreadable = self.semantic_tables(
            session, database, [(d.name, d.namespace, d.table) for d in model.datasets if d.name == wanted])
        compiled = compiler.compile_values(model, schemas, field, search, limit)
        return compiled, self.semantic_job(session, database, compiled, unreadable)

    def preview_request(self, session, database, namespace, name, snapshot_id, limit):
        details = self.details(session, database, namespace, "table", name)
        selected = snapshot_id or details["currentSnapshotId"]
        if selected is not None and selected not in {s["id"] for s in details["snapshots"]}:
            raise ServiceError(404, "This snapshot is no longer available. Refresh the table.")
        return {
            "uri": self.url + "/api/catalog",
            "database": database,
            "namespace": namespace,
            "table": name,
            "snapshotId": selected,
            "limit": limit,
            "token": session.token,
            "s3Endpoint": self.s3_endpoint,
        }
