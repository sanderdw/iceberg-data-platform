"""Registered extensions, from `PLATFORM_EXTENSIONS=conversationalbi=http://localhost:3007,...`.

The list is static configuration: both portals and the bridge read it from their own
environment, so no service depends on another to know which extensions exist.
`PLATFORM_LISTING_EXTENSIONS=openmetadata,...` names the registered extensions that publish
listed data shares to a data catalog (Bridge capability `listings`).
"""

import re
from urllib.parse import urlsplit

EXTENSION_ID = re.compile(r"^[a-z][a-z0-9]{1,23}$")


def origin(url):
    parsed = urlsplit(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path not in ("", "/") \
            or parsed.query or parsed.fragment or "@" in parsed.netloc:
        raise ValueError(f"Invalid extension origin: {url!r}")
    return f"{parsed.scheme}://{parsed.netloc}"


def parse(value):
    """`id=value` pairs separated by commas, as an ordered dictionary."""
    result = {}
    for item in filter(None, (part.strip() for part in (value or "").split(","))):
        id, sep, rest = item.partition("=")
        if not sep or not EXTENSION_ID.match(id) or id in result:
            raise ValueError(f"Invalid extension entry: {id!r}")
        result[id] = rest.strip()
    return result


def extensions(env):
    """Extension id → origin."""
    return {id: origin(url) for id, url in parse(env.get("PLATFORM_EXTENSIONS", "")).items()}


def listing_extensions(env):
    """Registered extensions that may read listings, in configuration order."""
    known = extensions(env)
    found = []
    for id in filter(None, (part.strip() for part in env.get("PLATFORM_LISTING_EXTENSIONS", "").split(","))):
        if id not in known or id in found:
            raise ValueError(f"Invalid listing extension: {id!r}")
        found.append(id)
    return found


def client_id(extension):
    return f"ext-{extension}"


def mcp_client_id(extension):
    return f"ext-{extension}-mcp"


# How the platform's own extensions are named in the Apps menus of the portals and of every extension (Bridge
# capability `app-directory`). Another extension shows its id.
DIRECTORY = {
    "conversationalbi": ("Conversational BI", "Ask questions about your semantic models"),
    "dbt": ("Pipelines", "Build, test and schedule dbt models"),
    "ingestion": ("Ingestion", "Load APIs, files and one-time database copies"),
    "replication": ("Replication", "Keep operational databases in step"),
    "maintenance": ("Maintenance", "Keep your tables compact and tidy"),
    "evaluations": ("Evaluations", "Check Conversational BI's answers"),
    "openmetadata": ("OpenMetadata", "Your data products in OpenMetadata"),
}
# The platform's extensions that act for a team in one environment through an automation principal, enabled per
# team environment in each extension's Settings. OpenMetadata publishes listings and has no such switch.
TEAM_ENVIRONMENTS = ("conversationalbi", "dbt", "ingestion", "replication", "maintenance", "evaluations")
# Where an extension's Settings › Environments is, under its origin (contracts/bridge/v1/settings).
SETTINGS_PAGE = "/#/settings/environments"
# The user portal's pages, which every Apps menu links to, in the order of the portal's own navigation.
PORTAL_PAGES = [("team", "Team overview"), ("databases", "Databases"), ("catalog", "Catalog"),
                ("notebooks", "Notebooks"), ("shares", "Data shares")]


def public(env):
    """What the portals may show: ids, origins, names and whether one publishes listings, never secrets."""
    listing = set(listing_extensions(env))
    return [{"id": id, "origin": url, "listing": id in listing, "title": DIRECTORY.get(id, (id, ""))[0],
             "purpose": DIRECTORY.get(id, (id, ""))[1]} for id, url in extensions(env).items()]


def portal_pages(user_origin):
    return [{"id": id, "title": title, "url": f"{user_origin.rstrip('/')}/#{id}"} for id, title in PORTAL_PAGES]
