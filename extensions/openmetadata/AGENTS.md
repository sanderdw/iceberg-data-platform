# Working on the OpenMetadata extension

- This folder is an independent project: its own `pyproject.toml`, `uv.lock`, tests, image and
  version. Run everything from `extensions/openmetadata`: `uv sync`, `uv run pytest`, `uv run ruff check .`.
- `CONTEXT.md` defines the words used here (listing, dataset, field, label, glossary term,
  classification tag). Use them that way in code, docs and messages, and add a word there when you
  introduce one.
- Never import from the platform's `server` or `user_portal` packages. Talk to the platform only
  through the Extension Bridge (`openmetadata_sync/bridge_client.py`), and only with the
  `listings` capability. This extension never asks for catalog tokens.
- `openmetadata_sync/mapping.py` is pure: listing in, OpenMetadata create requests out. Keep
  every naming rule there; `sync.py` only orders the calls and removes what is no longer listed.
- Everything this extension owns in OpenMetadata carries `OM_SERVICE_PREFIX`: services
  `<prefix>-<environment>`, metrics `<prefix>__…`, data products `<prefix>-share-…`. Removal only
  touches those names, and only after every listing was read and published.
- Never log, return or store `OM_JWT_TOKEN` or the Bridge client secret. `/api/status` shows
  counts, times and error codes only.
- `tests/conftest.py` fakes OpenMetadata 2.0.5's REST API. When a request shape changes, check
  it against the OpenMetadata JSON schemas of the pinned release and update the fake.
  `DECISIONS.md` records why the extension pushes instead of letting OpenMetadata pull.
