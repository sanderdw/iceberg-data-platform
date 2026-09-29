"""Registered extension stacks, from `PLATFORM_EXTENSIONS=dbt=http://localhost:3004,...`.

The list is static configuration: both portals and the bridge read it from their own
environment, so no service depends on another to know which extensions exist.
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


def client_id(extension):
    return f"ext-{extension}"


def mcp_client_id(extension):
    return f"ext-{extension}-mcp"


def public(env):
    """What the portals may show: ids and origins, never secrets."""
    return [{"id": id, "origin": url} for id, url in extensions(env).items()]
