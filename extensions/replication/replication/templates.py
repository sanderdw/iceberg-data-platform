"""Starting points for a replication, and the SQL a database owner runs once to prepare the source."""

TEMPLATES = [
    {
        "id": "postgres-cdc",
        "title": "PostgreSQL: change data capture",
        "description": "Copies the existing rows once, then follows inserts, updates and deletes from PostgreSQL's "
                       "write-ahead log through a replication slot and a publication. Needs wal_level = logical and "
                       "an account with the REPLICATION attribute; setupSql shows what the database owner runs.",
        "sourceType": "postgres",
        "namespace": "raw_crm",
        "source": {"host": "db.example.com", "port": 5432, "database": "crm", "username": "olake_reader",
                   "password": "$secret:password", "tls": "verify-full", "slot": "iceberg_crm",
                   "publication": "iceberg_crm"},
        "tables": {"public.orders": {"mode": "cdc", "partition": "/{created_at,month}"},
                   "public.customers": {"mode": "cdc"}},
        "options": {"deleteMode": "pos", "firstRun": "copy"},
        "secrets": ["password"],
    },
    {
        "id": "postgres-incremental",
        "title": "PostgreSQL: incremental on a cursor column",
        "description": "Reads rows whose cursor column (such as updated_at) grew since the last run and upserts them "
                       "on the primary key. Needs no replication slot, but does not see deleted rows.",
        "sourceType": "postgres",
        "namespace": "raw_crm",
        "source": {"host": "db.example.com", "port": 5432, "database": "crm", "username": "olake_reader",
                   "password": "$secret:password", "tls": "verify-full"},
        "tables": {"public.customers": {"mode": "incremental", "cursor": "updated_at"}},
        "options": {"deleteMode": "pos", "firstRun": "copy"},
        "secrets": ["password"],
    },
]


def find(id):
    return next((t for t in TEMPLATES if t["id"] == id), None)


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def setup_sql(source_type, source, tables=(), keyless=()):
    """What the database owner runs once so the replication can follow changes. Never contains a secret."""
    if source_type != "postgres":
        return ""
    user = quote(source.get("username") or "olake_reader")
    names = sorted(tables) or ["public.my_table"]
    schemas = sorted({name.split(".", 1)[0] for name in names})
    listed = ", ".join(".".join(quote(part) for part in name.split(".", 1)) for name in names)
    lines = [
        "-- Once per server (needs a restart): ALTER SYSTEM SET wal_level = logical;",
        f"ALTER ROLE {user} WITH REPLICATION;",
        *(f"GRANT USAGE ON SCHEMA {quote(schema)} TO {user};" for schema in schemas),
        f"GRANT SELECT ON {listed} TO {user};",
    ]
    if source.get("publication"):
        lines.append(f"CREATE PUBLICATION {quote(source['publication'])} FOR TABLE {listed};")
    if source.get("slot"):
        lines.append(f"SELECT pg_create_logical_replication_slot('{source['slot']}', 'pgoutput');")
    for name in replica_identity_full(tables, keyless):
        lines.append(f"ALTER TABLE {'.'.join(quote(part) for part in name.split('.', 1))} REPLICA IDENTITY FULL;")
    lines.append("-- Tables without a primary key need REPLICA IDENTITY FULL, and then so do the others.")
    return "\n".join(lines)


def cdc_tables(tables):
    return {name: spec for name, spec in (tables or {}).items() if isinstance(spec, dict) and spec.get("mode") == "cdc"}


def appended(tables, keyless=()):
    """Change-capture tables OLake appends to: no primary key (from the last source check), or appendOnly."""
    return sorted(name for name, spec in cdc_tables(tables).items() if spec.get("appendOnly") or name in keyless)


def replica_identity_full(tables, keyless=()):
    """Tables whose source needs REPLICA IDENTITY FULL (DECISIONS.md).

    A delete carries only the key otherwise: a partitioned table gets its delete in the wrong partition. And an
    update leaves unchanged large (TOAST) values out: the run reads them back from the table, but OLake can only do
    that when it appends to none of the replication's tables, so with one append-only table every table needs it.
    """
    cdc = cdc_tables(tables)
    if appended(tables, keyless):
        return sorted(cdc)
    return sorted(name for name, spec in cdc.items() if spec.get("partition"))


def warnings(tables, keyless=()):
    """What the database owner must do for the chosen tables beyond the setup SQL's basics."""
    found = []
    for name, spec in sorted(cdc_tables(tables).items()):
        if spec.get("partition"):
            found.append(f"{name} is partitioned: its source table needs REPLICA IDENTITY FULL, or deleted rows stay "
                         "visible in DuckDB.")
    extra = appended(tables, keyless)
    if extra:
        found.append(f"{', '.join(extra)} {'is' if len(extra) == 1 else 'are'} appended to (no primary key or "
                     "appendOnly): every change-capture table of this replication needs REPLICA IDENTITY FULL, or "
                     "large values an update leaves unchanged arrive as __olake_unavailable_value__.")
    return found
