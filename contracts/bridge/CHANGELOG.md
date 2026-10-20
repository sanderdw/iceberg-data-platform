# Bridge contract changelog

## 0.1.2 - 2026-10-17

Released with core 0.9.9. Additive: extensions built for 0.1.0 and 0.1.1 keep working.

- Discovery: `network` has `platform`, `docker` or `kubernetes`, and `namespace`, the Kubernetes
  namespace of the platform. On Kubernetes the aliases are Services in that namespace and `name`
  is the namespace (contracts/bridge/v1/network.md).
- On Kubernetes, an extension's namespace for isolated jobs carries `io.iceberg-platform.isolated: "true"`.

## 0.1.1 - 2026-10-14

Released with core 0.9.1. Additive: extensions built for 0.1.0 keep working.

- Capability `listings`: data shares that their owning team lists in a data catalog. A
  registered listing extension (`PLATFORM_LISTING_EXTENSIONS`) polls `GET /listings`, then
  reads `GET /listings/{shareId}` for the names, column schemas, descriptions, partition
  fields, producer conventions and semantic models of the listed objects. The response has
  an `ETag` and answers `If-None-Match` with 304. A listing carries no data, view SQL,
  storage location, snapshots or credential. Semantic model fields carry their `label`, and
  datasets, fields and metrics their `aiContext` rows (a field's or metric's synonyms stay in
  `synonyms`).
- Discovery: each entry of `extensions` has `listing`, true for a listing extension.

## 0.1.0 - 2026-10-04

First version, released with core 0.7.0.

- Discovery (`GET /bridge/v1`): contract version, capabilities, OIDC, catalog and storage
  endpoints, environments, network aliases and registered extensions.
- Capability `user-context`: `GET /me` returns the signed-in user's teams, roles and databases,
  re-checked on every call. Each entry of `sharedDatabases` has `recipientTeams` (the user's teams
  that received it, in membership order), `sharedObjects` (the tables, views and semantic models the
  user can read), `sharedWithTeam` (the first recipient team) and `ownerTeamName`.
- Capabilities `automation-principals` and `automation-tokens`: automation principals per team,
  environment and extension, enabled by team administrators, and one-hour `read` or `write`
  catalog tokens.
- Capability `shared-data`: a team share received by a team reaches the read role of that team's
  automation principals in the environment of the shared database, never the write role.
  `GET /automation-principals/{id}` lists these databases under `sharedDatabases`, each with the
  objects it can read, and a `read` token can load them by name.
- Table-property conventions v1 for producers, lineage and quality.
- Network aliases and role labels, and the extension handshake file.
