# Bridge contract changelog

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
