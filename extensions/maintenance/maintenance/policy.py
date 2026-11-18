"""Maintenance policies: one per database, with the defaults of Apache Iceberg and Amazon S3 Tables.

| Operation | Default | Bounds |
| --- | --- | --- |
| Expire snapshots | on: older than 5 days (120 hours), keep the last 1 | 1 hour to 10 years; keep 1 to 10,000 |
| Compaction | on: 128 MB files, 5 input files, 30% deleted rows, 2 GB input per table and run | 16 to 512 MB |
| Orphan files | off: older than 3 days | never younger than 1 day |
| Rewrite manifests | on: when a table has more than 100 | 2 to 100,000 |
| Dangling deletes | on | |

Table properties history.expire.max-snapshot-age-ms and history.expire.min-snapshots-to-keep, and each branch
and tag's own retention, override the policy's snapshot expiry, as Iceberg defines.
"""

import copy

from .errors import MaintenanceError

DEFAULTS = {
    "expireSnapshots": {"enabled": True, "olderThanHours": 120, "retainLast": 1},
    "compaction": {"enabled": True, "targetFileSizeMb": 128, "minInputFiles": 5, "deleteRatio": 0.3,
                   "maxInputMbPerRun": 2048},
    "orphanFiles": {"enabled": False, "olderThanHours": 72},
    "rewriteManifests": {"enabled": True, "minManifests": 100},
    "danglingDeletes": {"enabled": True},
}
# (type, minimum, maximum) per setting; booleans have no bounds.
BOUNDS = {
    "expireSnapshots": {"enabled": (bool,), "olderThanHours": (int, 1, 87600), "retainLast": (int, 1, 10000)},
    "compaction": {"enabled": (bool,), "targetFileSizeMb": (int, 16, 512), "minInputFiles": (int, 1, 1000),
                   "deleteRatio": (float, 0.0, 1.0), "maxInputMbPerRun": (int, 16, 102400)},
    "orphanFiles": {"enabled": (bool,), "olderThanHours": (int, 24, 87600)},
    "rewriteManifests": {"enabled": (bool,), "minManifests": (int, 2, 100000)},
    "danglingDeletes": {"enabled": (bool,)},
}
OPERATIONS = tuple(DEFAULTS)
LABELS = {"expireSnapshots": "Expire snapshots", "compaction": "Compact data files",
          "orphanFiles": "Remove orphan files", "rewriteManifests": "Rewrite manifests",
          "danglingDeletes": "Remove dangling deletes"}
MAX_EXCLUDED = 500


def defaults():
    return copy.deepcopy(DEFAULTS)


def setting(operation, key, value):
    spec = BOUNDS[operation][key]
    kind = spec[0]
    if kind is bool:
        if not isinstance(value, bool):
            raise MaintenanceError(422, f"{operation}.{key} is true or false.", "invalid_policy")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MaintenanceError(422, f"{operation}.{key} is a number.", "invalid_policy")
    if kind is int and value != int(value):
        raise MaintenanceError(422, f"{operation}.{key} is a whole number.", "invalid_policy")
    value = kind(value)
    if not spec[1] <= value <= spec[2]:
        raise MaintenanceError(422, f"{operation}.{key} is between {spec[1]} and {spec[2]}.", "invalid_policy")
    return value


def merge(settings, base=None):
    """A complete, checked policy: `settings` (possibly partial) applied over `base` or the defaults."""
    merged = copy.deepcopy(base) if base else defaults()
    if not isinstance(settings, dict):
        raise MaintenanceError(422, "A policy is an object of operations.", "invalid_policy")
    for operation, values in settings.items():
        if operation not in BOUNDS:
            raise MaintenanceError(422, f"Unknown operation {operation}. Use {', '.join(OPERATIONS)}.",
                                   "invalid_policy")
        if not isinstance(values, dict):
            raise MaintenanceError(422, f"{operation} is an object.", "invalid_policy")
        for key, value in values.items():
            if key not in BOUNDS[operation]:
                raise MaintenanceError(422, f"Unknown setting {operation}.{key}.", "invalid_policy")
            merged[operation][key] = setting(operation, key, value)
    compaction = merged["compaction"]
    if compaction["maxInputMbPerRun"] < compaction["targetFileSizeMb"]:
        raise MaintenanceError(422, "compaction.maxInputMbPerRun must be at least the target file size.",
                               "invalid_policy")
    return merged


def excluded(names):
    if not isinstance(names, list) or len(names) > MAX_EXCLUDED:
        raise MaintenanceError(422, f"exclude is a list of at most {MAX_EXCLUDED} tables.", "invalid_policy")
    clean = []
    for name in names:
        if not isinstance(name, str) or not 3 <= len(name) <= 300 or "." not in name or name != name.strip():
            raise MaintenanceError(422, "Name excluded tables as namespace.table.", "invalid_policy")
        if name not in clean:
            clean.append(name)
    return sorted(clean)
