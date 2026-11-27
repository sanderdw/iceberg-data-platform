"""Maintenance policies are Apache Polaris policies: one per system policy type, attached to a catalog (database),
a namespace or a table. This module turns the API's settings into Polaris policy content and back.

| Type key | Polaris type | API setting | Content key | Default | Bounds |
| --- | --- | --- | --- | --- | --- |
| snapshotExpiry | system.snapshot-expiry | enabled | enable | on | |
| | | maxSnapshotAgeDays | config.max_snapshot_age_days | 5 | 1 to 3650 |
| | | minSnapshotsToKeep | config.min_snapshot_to_keep | 1 | 1 to 10,000 |
| dataCompaction | system.data-compaction | enabled | enable | on | |
| | | targetFileSizeBytes | config.target-file-size-bytes | 128 MiB | 16 to 512 MiB |
| | | minInputFiles | config.min-input-files | 5 | 1 to 1000 |
| | | deleteRatioThreshold | config.delete-ratio-threshold | 0.3 | 0 to 1 |
| | | removeDanglingDeletes | config.remove-dangling-deletes | true | |
| | | maxInputBytesPerRun | config.max-input-bytes-per-run | 2 GiB | 16 MiB to 100 GiB |
| metadataCompaction | system.metadata-compaction | enabled | enable | on | |
| | | minCountToMerge | config.min-count-to-merge | 100 | 2 to 100,000 |
| orphanFileRemoval | system.orphan-file-removal | enabled | enable | off | |
| | | maxOrphanFileAgeInDays | max_orphan_file_age_in_days | 3 | 1 to 3650 |

The snapshot-expiry and orphan keys are the ones Polaris documents; the data-compaction keys are Apache
Iceberg's `rewrite_data_files` options, except `max-input-bytes-per-run`, which is Maintenance's own, as is
`min-count-to-merge` (after Iceberg's `commit.manifest.min-count-to-merge`). Content keys Maintenance does not
know are kept when it updates a policy. Table properties history.expire.* and each branch and tag's own
retention override snapshot expiry, as Iceberg defines.
"""

import copy
import json
import math
import re

from .errors import MaintenanceError

CONTENT_VERSION = "2025-02-03"
# The namespace that holds the policies Maintenance creates. Polaris keeps every policy in a namespace.
HOME = "_maintenance"
TYPES = {"snapshotExpiry": "system.snapshot-expiry", "dataCompaction": "system.data-compaction",
         "metadataCompaction": "system.metadata-compaction", "orphanFileRemoval": "system.orphan-file-removal"}
KEYS = {ptype: key for key, ptype in TYPES.items()}
TITLES = {"snapshotExpiry": "Expire snapshots", "dataCompaction": "Compact data files",
          "metadataCompaction": "Rewrite manifests", "orphanFileRemoval": "Remove orphan files"}
# The policies Maintenance creates: the database's own, attached to the catalog, and one disabled policy per type
# that exclusions attach to namespaces and tables.
CATALOG_NAMES = {key: ptype.removeprefix("system.") for key, ptype in TYPES.items()}
EXCLUDED_NAMES = {key: "excluded-" + ptype.removeprefix("system.") for key, ptype in TYPES.items()}
MIB = 1 << 20
# Per type: API setting -> (content key, at the top level instead of in config, kind, minimum, maximum, default).
FIELDS = {
    "snapshotExpiry": {
        "maxSnapshotAgeDays": ("max_snapshot_age_days", False, int, 1, 3650, 5),
        "minSnapshotsToKeep": ("min_snapshot_to_keep", False, int, 1, 10000, 1),
    },
    "dataCompaction": {
        "targetFileSizeBytes": ("target-file-size-bytes", False, int, 16 * MIB, 512 * MIB, 128 * MIB),
        "minInputFiles": ("min-input-files", False, int, 1, 1000, 5),
        "deleteRatioThreshold": ("delete-ratio-threshold", False, float, 0.0, 1.0, 0.3),
        "removeDanglingDeletes": ("remove-dangling-deletes", False, bool, None, None, True),
        "maxInputBytesPerRun": ("max-input-bytes-per-run", False, int, 16 * MIB, 100 * 1024 * MIB, 2048 * MIB),
    },
    "metadataCompaction": {
        "minCountToMerge": ("min-count-to-merge", False, int, 2, 100000, 100),
    },
    "orphanFileRemoval": {
        "maxOrphanFileAgeInDays": ("max_orphan_file_age_in_days", True, int, 1, 3650, 3),
    },
}
ENABLED = {"snapshotExpiry": True, "dataCompaction": True, "metadataCompaction": True, "orphanFileRemoval": False}
# Polaris's own documentation writes this key with underscores; a policy written that way is read too.
READ_ALSO = {("dataCompaction", "targetFileSizeBytes"): "target_file_size_bytes"}
# The two operations that delete: an enabled policy of these types is applied only when it states its age and
# holds nothing Maintenance would ignore, such as other units, `locations` or a prefix mode. Otherwise it would
# run with the defaults and could delete more than the policy's author meant. max_ref_age_days is known and not
# applied: refs are then kept longer, never shorter.
DESTRUCTIVE = {
    "snapshotExpiry": {"required": "max_snapshot_age_days", "top": set(),
                       "config": {"max_snapshot_age_days", "min_snapshot_to_keep", "max_ref_age_days"}},
    "orphanFileRemoval": {"required": "max_orphan_file_age_in_days", "top": {"max_orphan_file_age_in_days"},
                          "config": set()},
}
MAX_EXCLUSIONS = 500
PART = r"[A-Za-z0-9_$-]{1,128}"
TARGET = re.compile(rf"^{PART}(\.{PART}){{0,7}}$")


def defaults():
    return {key: {"enabled": ENABLED[key], **{name: spec[5] for name, spec in FIELDS[key].items()}} for key in TYPES}


def invalid(message):
    return MaintenanceError(422, message, "invalid_policy")


def value_of(key, name, value, *, lenient=False):
    """A checked setting. `lenient` also takes strings, as another engine may have written them in Polaris."""
    kind, low, high = FIELDS[key][name][2:5]
    label = f"{key}.{name}"
    if lenient and isinstance(value, str):
        text = value.strip().lower()
        if kind is bool and text in ("true", "false"):
            value = text == "true"
        elif kind is not bool:
            try:
                value = float(text)
            except ValueError:
                pass
    if isinstance(value, float) and not math.isfinite(value):
        raise invalid(f"{label} is a finite number.")
    if kind is bool:
        if not isinstance(value, bool):
            raise invalid(f"{label} is true or false.")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise invalid(f"{label} is a number.")
    if kind is int and value != int(value):
        raise invalid(f"{label} is a whole number.")
    value = kind(value)
    if not low <= value <= high:
        raise invalid(f"{label} is between {low} and {high}.")
    return value


def check(merged):
    compaction = merged["dataCompaction"]
    if compaction["maxInputBytesPerRun"] < compaction["targetFileSizeBytes"]:
        raise invalid("dataCompaction.maxInputBytesPerRun must be at least the target file size.")
    return merged


def merge(settings, base=None):
    """Complete, checked settings of every type: `settings` (possibly partial) over `base` or the defaults."""
    merged = copy.deepcopy(base) if base else defaults()
    if not isinstance(settings, dict):
        raise invalid("settings is an object of policy types.")
    for key, values in settings.items():
        if key not in TYPES:
            raise invalid(f"Unknown policy type {key}. Use {', '.join(TYPES)}.")
        if not isinstance(values, dict):
            raise invalid(f"{key} is an object.")
        for name, value in values.items():
            if name == "enabled":
                if not isinstance(value, bool):
                    raise invalid(f"{key}.enabled is true or false.")
                merged[key]["enabled"] = value
            elif name in FIELDS[key]:
                merged[key][name] = value_of(key, name, value)
            else:
                raise invalid(f"Unknown setting {key}.{name}.")
    return check(merged)


def parse(content):
    """Policy content as Polaris returns it: a JSON string, or an object."""
    if isinstance(content, dict):
        return content
    try:
        parsed = json.loads(content or "{}")
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def from_content(key, content):
    """(settings, problem) of a Polaris policy of type `key`. With a problem, Maintenance does not apply it: the
    settings are then the defaults, switched off."""
    parsed = parse(content)
    settings = defaults()[key]
    if parsed is None:
        return {**settings, "enabled": False}, "The policy's content is not a JSON object."
    if not isinstance(parsed.get("enable"), bool):
        return {**settings, "enabled": False}, "The policy's content has no enable of true or false."
    settings["enabled"] = parsed["enable"]
    config = parsed.get("config") if isinstance(parsed.get("config"), dict) else {}
    if settings["enabled"] and key in DESTRUCTIVE:
        rules = DESTRUCTIVE[key]
        top = set(parsed) - {"version", "enable", "config"} - rules["top"]
        unknown = sorted(top | {f"config.{k}" for k in set(config) - rules["config"]})
        where = parsed if rules["required"] in rules["top"] else config
        if unknown:
            return {**defaults()[key], "enabled": False}, (
                f"The policy sets {', '.join(unknown)}, which Maintenance does not apply, so it does not run it.")
        if where.get(rules["required"]) is None:
            return {**defaults()[key], "enabled": False}, (
                f"The policy has no {rules['required']}, so Maintenance does not run it.")
    for name, (content_key, top, *_rest) in FIELDS[key].items():
        source = parsed if top else config
        found = source.get(content_key, source.get(READ_ALSO.get((key, name), ""), None))
        if found is None:
            continue
        try:
            settings[name] = value_of(key, name, found, lenient=True)
        except MaintenanceError as exc:
            return {**defaults()[key], "enabled": False}, f"{content_key}: {exc} Maintenance does not apply it."
    if key == "dataCompaction" and settings["maxInputBytesPerRun"] < settings["targetFileSizeBytes"]:
        return {**defaults()[key], "enabled": False}, ("max-input-bytes-per-run is below target-file-size-bytes. "
                                                       "Maintenance does not apply it.")
    return settings, None


def to_content(key, settings, existing=None):
    """Polaris policy content for `settings`, keeping what an existing policy's content holds besides them."""
    content = copy.deepcopy(parse(existing) or {}) if existing is not None else {}
    content["version"] = CONTENT_VERSION
    content["enable"] = bool(settings["enabled"])
    config = content.get("config") if isinstance(content.get("config"), dict) else {}
    for name, (content_key, top, *_rest) in FIELDS[key].items():
        if top:
            content[content_key] = settings[name]
        else:
            config[content_key] = settings[name]
        alias = READ_ALSO.get((key, name))
        if alias:
            config.pop(alias, None)
    if config:
        content["config"] = config
    return content


def disabled_content():
    return {"version": CONTENT_VERSION, "enable": False}


def effective(applicable, catalog_level):
    """Per type, what applies to one table: {"settings", "policy", "problem"}.

    `applicable` is Polaris's applicable-policies answer for the table, `catalog_level` the one for the catalog,
    which tells a policy attached to the catalog from one attached to a namespace."""
    result = {}
    for key, ptype in TYPES.items():
        entry = next((p for p in applicable if p.get("policy-type") == ptype), None)
        if entry is None:
            result[key] = {"settings": {**defaults()[key], "enabled": False}, "policy": None, "problem": None}
            continue
        settings, problem = from_content(key, entry.get("content"))
        source = catalog_level.get(key)
        same = source is not None and (source.get("name"), source.get("namespace")) == (
            entry.get("name"), entry.get("namespace"))
        applied = "table" if not entry.get("inherited") else "catalog" if same else "namespace"
        result[key] = {"settings": settings, "problem": problem, "policy": {
            "name": str(entry.get("name", "")), "namespace": ".".join(entry.get("namespace") or []),
            "version": int(entry.get("version") or 0), "appliedAt": applied}}
    return result


def exclusions(entries):
    """Checked exclusions: [{target, level, types}], one per target, sorted."""
    if not isinstance(entries, list) or len(entries) > MAX_EXCLUSIONS:
        raise MaintenanceError(422, f"exclusions is a list of at most {MAX_EXCLUSIONS} entries.", "invalid_exclusions")
    clean = {}
    for entry in entries:
        target, level = entry.get("target"), entry.get("level")
        types = entry.get("types") or list(TYPES)
        if level not in ("namespace", "table"):
            raise MaintenanceError(422, "An exclusion's level is namespace or table.", "invalid_exclusions")
        if not isinstance(target, str) or not TARGET.match(target) or target.split(".")[0] == HOME:
            raise MaintenanceError(422, "Name an excluded namespace as namespace and a table as namespace.table.",
                                   "invalid_exclusions")
        if level == "table" and "." not in target:
            raise MaintenanceError(422, f"Name the table {target} as namespace.table.", "invalid_exclusions")
        if any(t not in TYPES for t in types):
            raise MaintenanceError(422, f"An exclusion's types are {', '.join(TYPES)}.", "invalid_exclusions")
        if (level, target) in clean:
            raise MaintenanceError(422, f"{target} is excluded twice.", "invalid_exclusions")
        clean[(level, target)] = {"target": target, "level": level, "types": [t for t in TYPES if t in types]}
    return sorted(clean.values(), key=lambda e: (e["target"], e["level"]))


def target_of(level, target):
    """A Polaris attachment target."""
    return {"type": "namespace" if level == "namespace" else "table-like", "path": target.split(".")}
