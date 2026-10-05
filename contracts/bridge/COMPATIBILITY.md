# Compatibility policy

The contract version is `contractVersion` in `GET /bridge/v1` and `info.version` in
[`v1/openapi.yaml`](v1/openapi.yaml). It follows semantic versioning, independent of the core
release version. The `v1` in paths, in the `contracts/bridge/v1/` folder and in the handshake's
`BRIDGE_CONTRACT` is the API generation, not the contract version.

The contract is pre-stable (0.x):

- **Patch** (0.1.x): additive changes only, such as a new endpoint, a new optional request field, a
  new response field, a new error `code` or a fix that doesn't change a request or response shape.
  New features are announced in `capabilities`, so an extension checks for a capability, not a
  version. Extensions must ignore unknown response fields.
- **Minor** (0.y.0): may remove, rename or change fields, or add required input. Every such change
  is listed in [CHANGELOG.md](CHANGELOG.md) and the core CHANGELOG.
- **1.0.0** is the first stable contract. From then on, minor versions are additive only and a
  major version is served under a new prefix (`/bridge/v2`) alongside the previous one for at
  least one core minor release.

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

- declare the contract range they support, for example `>=0.1,<0.2`, and refuse to start with a
  clear message when discovery reports a version outside it.
- treat error responses by their stable `code`, not by their `error` text.
- use only the capabilities they need, and test against both the current core and the latest
  stable core release.
