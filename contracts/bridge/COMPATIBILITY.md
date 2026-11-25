# Compatibility policy

The contract version is `contractVersion` in `GET /bridge/v1` and `info.version` in
[`v1/openapi.yaml`](v1/openapi.yaml). It follows semantic versioning, independent of the core
release version. The `v1` in paths, in the `contracts/bridge/v1/` folder and in the handshake's
`BRIDGE_CONTRACT` is the API generation, not the contract version.

The contract is stable since 1.0.0:

- **Patch** (1.0.x): a fix that doesn't change a request or response shape.
- **Minor** (1.y.0): additive changes only, such as a new endpoint, a new optional request field, a
  new response field or a new error `code`. New features are announced in `capabilities`, so an
  extension checks for a capability, not a version. Extensions must ignore unknown response fields.
- **Major** (2.0.0): may remove, rename or change fields, or add required input. A major version is
  served under a new prefix (`/bridge/v2`), alongside the previous one for at least one core minor
  release, and every change is listed in [CHANGELOG.md](CHANGELOG.md) and the core CHANGELOG.

The pre-stable 0.1.x contracts end with 1.0.0, which changed shapes without a transition period.
An extension built for 0.1.x does not work with core 1.0.0, and the reverse.

The same rules apply to [`v1/table-properties.md`](v1/table-properties.md),
[`v1/network.md`](v1/network.md), [`v1/handshake.md`](v1/handshake.md) and
[`v1/apps-menu/`](v1/apps-menu).

## Obligations

The core:

- keeps `contracts/bridge/v1/openapi.yaml` identical to the implementation. `test/test_bridge.py`
  fails when they differ; regenerate it with `uv run python -m scripts.bridge_contract`.
- records every contract change in [CHANGELOG.md](CHANGELOG.md).
- never requires an extension to read core configuration files, container names or the Polaris
  management API.

Extensions:

- declare the contract range they support, `>=1.0,<2` or narrower, as `bridge-contract` in their
  `pyproject.toml`, and refuse to start with a clear message when discovery reports a version
  outside it.
- treat error responses by their stable `code`, not by their `error` text.
- use only the capabilities they need, and test against both the current core and the latest
  stable core release. That test is skipped while the latest release offers a contract outside
  the extension's range.
