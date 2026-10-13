# Data shares

**Data shares** in the user portal lists the shares of the active team's databases, plus the shares that other teams have shared with you. Only Administrators can manage shares.

Choose **New data share** under a database, pick the tables, views and semantic models, and optionally set an expiry. Then choose the recipient:

- **Another team:** its current and future members get read-only access with their own accounts. The database appears in their **Catalog** and **Notebooks**, even if they own no databases.
- **Externally:** the client ID and secret are shown once. **Copy DuckDB snippet** copies a runnable script. Send both over a secure channel. The recipient saves the script as `read_share_duckdb.py` and runs `uv run read_share_duckdb.py`.

The recipient can read only the selected objects and can't list anything. A view shares only its definition, so you must add every table it reads, and the recipient can read those tables in full. Selecting a semantic model selects the tables its datasets read; if you remove one, the form warns that the recipient can't query the model without it, and saving names every table that is still missing. External recipients load a shared model with a GET on the address shown with the credential. **Edit** changes the selection or expiry, **New secret** replaces the secret, and **Revoke** ends access immediately. To change the recipients, revoke the share and create a new one. See [the resource model](develop/resource-model.md#data-shares) for the details.
