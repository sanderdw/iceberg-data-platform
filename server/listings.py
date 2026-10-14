"""Listings: data shares that their owning team publishes to a data catalog.

A listing extension reads them through the Bridge (capability `listings`) and publishes them in
its catalog. A listing carries names, schemas, descriptions and semantic models, never data or a
credential: the share's principal is not activated for it, and the definitions are read with a
temporary role that holds no data privilege (`PolarisProvider.listing_access`).
"""

import hashlib
import json

from . import catalog_metadata
from .models import ServiceError


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def listed(provider, id=None):
    """(share, listing header) for every listed share of a ready database, or only the share `id`.

    Reading a share's objects reads its grants: one listing reads only its own share's.
    """
    databases = {d["id"]: d for d in provider.list_databases() if d["status"] == "ready"}
    teams = {t["id"]: t["name"] for t in provider.list_teams()}
    result = []
    for share in provider.list_shares(id=id, drift=True):
        database = databases.get(share["database"])
        if not share["listed"] or not database:
            continue
        header = {
            "id": share["id"],
            "name": share["name"],
            "description": share["description"],
            "contact": share["contact"],
            "expiresAt": share["expiresAt"],
            "owner": {"team": database["team"], "teamName": teams.get(database["team"], "")},
            "database": {"id": database["id"], "name": database["name"], "environment": database["environment"]},
            # What the share's role really grants: renamed objects under their new name.
            "objects": provider.received_objects(share),
        }
        result.append((share, {**header, "version": digest(header)}))
    return result


def summaries(provider):
    """Cheap to poll: management calls only. `version` changes when a listing's header or objects do;
    schema and semantic model edits show only in the full listing."""
    return [
        {"id": h["id"], "version": h["version"], "databaseId": h["database"]["id"],
         "environment": h["database"]["environment"]}
        for _, h in listed(provider)
    ]


def listing(provider, id, extensions=None):
    header = next((h for _, h in listed(provider, id)), None)
    if header is None:
        raise ServiceError(404, "This listing does not exist.")
    database = header["database"]["id"]
    tables = {(tuple(o["namespace"]), o["name"]) for o in header["objects"] if o["kind"] in ("table", "view")}
    objects = []
    for o, loaded in provider.listing_metadata(database, header["objects"]):
        if o["kind"] == "table":
            objects.append(catalog_metadata.listed_table(o["namespace"], o["name"], loaded, database=database,
                                                         listed=tables, extensions=extensions))
        elif o["kind"] == "view":
            objects.append(catalog_metadata.listed_view(o["namespace"], o["name"], loaded))
        else:
            objects.append(catalog_metadata.listed_semantic_model(o["namespace"], o["name"], loaded))
    found = {(o["kind"], tuple(o["namespace"]), o["name"]) for o in objects}
    return {
        **{k: v for k, v in header.items() if k != "objects"},
        "objects": objects,
        "unavailable": [o for o in header["objects"] if (o["kind"], tuple(o["namespace"]), o["name"]) not in found],
    }
