"""Starting points for a pipeline: a complete definition to adapt, with the secrets it needs."""

TEMPLATES = [
    {
        "id": "cbs-statline",
        "title": "Public REST API: CBS StatLine (OData, paginated JSON)",
        "description": "Statistics Netherlands open data (CC BY 4.0). Loads the observations of table 83140NED "
                       "(energy balance), 100,000 rows per page, following @odata.nextLink. Change the table id "
                       "to load another StatLine table.",
        "sourceType": "rest_api",
        "namespace": "raw_cbs",
        "source": {
            "client": {"base_url": "https://datasets.cbs.nl/odata/v1/CBS/83140NED/",
                       "paginator": {"type": "json_link", "next_url_path": "['@odata.nextLink']"}},
            "resources": [{"name": "observations", "endpoint": {"path": "Observations", "data_selector": "value"}}],
        },
        "resources": {"observations": {"writeDisposition": "replace"}},
        "secrets": [],
    },
    {
        "id": "postgres-incremental",
        "title": "PostgreSQL table, incremental on updated_at",
        "description": "Loads rows changed since the last run, by updated_at, and merges them on id. A cursor does "
                       "not see deleted rows: run with fullRefresh now and then. Give the account read-only "
                       "rights at the source.",
        "sourceType": "sql_database",
        "namespace": "raw_crm",
        "source": {"dialect": "postgresql", "host": "db.example.com", "port": 5432, "database": "crm",
                   "username": "reader", "password": "$secret:password", "tls": "verify-full", "schema": "public",
                   "tables": [{"name": "customers", "incremental": {"cursor": "updated_at"}}]},
        "resources": {"customers": {"writeDisposition": "merge", "primaryKey": ["id"]}},
        "secrets": ["password"],
    },
    {
        "id": "mysql-tables",
        "title": "MySQL or MariaDB tables, full copy",
        "description": "Copies whole tables on every run, replacing the previous copy. The table keeps its "
                       "identity, so data shares and descriptions stay.",
        "sourceType": "sql_database",
        "namespace": "raw_shop",
        "source": {"dialect": "mysql", "host": "db.example.com", "port": 3306, "database": "shop",
                   "username": "reader", "password": "$secret:password", "tls": "verify-full",
                   "tables": [{"name": "orders"}, {"name": "products"}]},
        "resources": {"orders": {"writeDisposition": "replace"}, "products": {"writeDisposition": "replace"}},
        "secrets": ["password"],
    },
    {
        "id": "airports-csv",
        "title": "CSV file over HTTPS: airport codes",
        "description": "The airport codes of datahub.io's core datasets (ODC Public Domain Dedication and License), "
                       "one CSV file, replaced on every run. Pairs with the flights example.",
        "sourceType": "filesystem",
        "namespace": "raw_airports",
        "source": {"url": "https://raw.githubusercontent.com/datasets/airport-codes/main/data/airport-codes.csv",
                   "format": "csv", "table": "airports"},
        "resources": {"airports": {"writeDisposition": "replace"}},
        "secrets": [],
    },
]


def find(id):
    return next((t for t in TEMPLATES if t["id"] == id), None)
