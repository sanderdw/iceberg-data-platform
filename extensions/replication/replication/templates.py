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


def setup_sql(source_type, source, tables=()):
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
    # A delete carries only the key unless the table's replica identity is FULL: a partitioned table then gets its
    # delete in the wrong partition, and a table without a key cannot be matched at all (DECISIONS.md).
    full = [name for name, spec in sorted((tables or {}).items())
            if isinstance(spec, dict) and spec.get("mode") == "cdc"
            and (spec.get("partition") or spec.get("appendOnly"))]
    for name in full:
        lines.append(f"ALTER TABLE {'.'.join(quote(part) for part in name.split('.', 1))} REPLICA IDENTITY FULL;")
    lines.append("-- Tables without a primary key also need REPLICA IDENTITY FULL.")
    return "\n".join(lines)


def warnings(tables):
    """What the database owner must do for the chosen tables beyond the setup SQL's basics."""
    found = []
    for name, spec in sorted((tables or {}).items()):
        if spec.get("mode") == "cdc" and spec.get("partition"):
            found.append(f"{name} is partitioned: its source table needs REPLICA IDENTITY FULL, or deleted rows stay "
                         "visible in DuckDB.")
    return found
