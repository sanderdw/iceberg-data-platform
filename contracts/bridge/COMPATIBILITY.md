# Compatibility policy

The contract version is `contractVersion` in `GET /bridge/v1` and `info.version` in
[`v1/openapi.yaml`](v1/openapi.yaml). It follows semantic versioning, independent of the core
release version.

- **Patch** (1.0.x): documentation and fixes that do not change a request or response shape.
- **Minor** (1.x.0): additive changes only, such as a new endpoint, a new optional request field, a
  new response field or a new error `code`. New features are announced in `capabilities`, so an
  extension checks for a capability, not a version. Extensions must ignore unknown response fields.
- **Major**: any removal, rename, type change or new required input. A major version is served
  under a new prefix (`/bridge/v2`). The previous major version is served alongside it for at
  least one core minor release, and its removal is announced in the core CHANGELOG.

The same rules apply to [`v1/table-properties.md`](v1/table-properties.md),
[`v1/network.md`](v1/network.md) and [`v1/handshake.md`](v1/handshake.md).

## Obligations

The core:

- keeps `contracts/bridge/v1/openapi.yaml` identical to the implementation. `test/test_bridge.py`
  fails when they differ; regenerate it with `uv run python -m scripts.bridge_contract`.
- records every contract change in [CHANGELOG.md](CHANGELOG.md).
- never requires an extension to read core configuration files, container names or the Polaris
  management API.

Extensions:

- declare the contract range they support, for example `>=1.0,<2`, and refuse to start with a
  clear message when discovery reports a version outside it.
- treat error responses by their stable `code`, not by their `error` text.
- use only the capabilities they need, and test against both the current core and the latest
  stable core release.
