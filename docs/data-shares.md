# Data shares

**Data shares** in the user portal lists the shares of the active team's databases, plus the shares that other teams have shared with you. Only Administrators can manage shares.

Choose **New data share** under a database, pick the tables, views and semantic models, and optionally set an expiry. Then choose the recipient:

- **Another team:** its current and future members get read-only access with their own accounts. The database appears in their **Catalog** and **Notebooks**, even if they own no databases.
- **Externally:** the client ID and secret are shown once. **Copy DuckDB snippet** copies a runnable script. Send both over a secure channel. The recipient saves the script as `read_share_duckdb.py` and runs `uv run read_share_duckdb.py`.
- **Listed in the data catalog:** the share's metadata is published to your organization's data catalog, so others can find the data product and ask you for access. This choice appears only when a data catalog extension, such as [OpenMetadata](openmetadata.md), is installed. Add a **contact for access requests**, such as a team mailbox.

You can combine destinations. A listing on its own grants no access and has no credential.

The recipient can read only the selected objects and can't list anything. A view shares only its definition, so you must add every table it reads, and the recipient can read those tables in full. Selecting a semantic model selects the tables its datasets read; if you remove one, the form warns that the recipient can't query the model without it, and saving names every table that is still missing. External recipients load a shared model with a GET on the address shown with the credential. **Edit** changes the selection, expiry or listing, **New secret** replaces the secret, and **Revoke** ends access immediately. To change the team or external recipient, revoke the share and create a new one.

## What a listing publishes

A listing gives the data catalog extension the share's name, description, contact, owning team and database, and for each selected object:

- **Tables:** name, description, columns with their types and descriptions, partition fields, row count, last update, and the pipeline that produced it with its upstream tables, as far as they are listed too.
- **Views:** name, description and columns. Not the view's SQL.
- **Semantic models:** datasets, fields, relationships and metrics with their expressions, descriptions and AI context, and the labels of fields.

It never gives data, view SQL, storage locations, snapshots or credentials. The extension decides what its catalog shows; see [OpenMetadata](openmetadata.md#what-is-published). A semantic model's expressions and dataset sources can name tables that aren't listed, so check a model before you list it. Unlisting a share, revoking it or letting it expire removes it from the catalog. See [the resource model](develop/resource-model.md#data-shares) for the details.
