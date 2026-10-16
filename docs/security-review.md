# Security review

This page lists what is still open after two reviews of the platform. Fixed findings are in the [changelog](changelog.md); the trust boundaries the platform is designed around are in the [security model](security.md).

- **Architecture and security review of 0.9.2** (October 2026). A source-only review of the core, the four extensions, the installers, the Compose files, the workflows and the agent skills, in independent passes that each tried to refute the others' findings.
- **[Cloudflare security-audit skill](https://github.com/cloudflare/security-audit-skill)** (October 2026, quick profile). A second review of the 0.9.3 code: hunters per area, an independent verifier per finding, and local checks in a sandbox without network or secrets.

Both ran without a live Polaris, Keycloak or RustFS, so behaviour that depends on them is listed under [not verified](#not-verified). Neither is an independent audit. Nothing found is reachable without an account, and nothing reaches another team's data without a share, a membership or a team role.

## Open findings

### Team members can rewrite the record the portals trust

The portals read a database's owning team and environment from its Polaris catalog properties. Polaris lets team Writers and Administrators, and pipelines that write as the team, edit those properties through its management API. A team member could therefore make a database look like another team's, move it between environments as far as extensions are concerned, or hide it from the portals.

0.9.3 removes the consequences found so far: storage policies, database moves and the admin page no longer trust these properties. The root cause stays: keep the ownership record on an entity only the platform identity can write, and check it on every read. Until then, give the Writer role only to people you would trust as that team's Administrator.

*Priority: high.*

### Code a team member wrote runs with the viewer's session

- **Notebooks** are served on the user portal's own address. Output a notebook renders runs with the portal session of whoever opens it, so running a teammate's notebook lets their code do whatever you can do in the portal, such as creating a data share. Serve notebooks from a separate origin. *Priority: high.*
- **The dbt docs site** runs scripts on the dbt viewer's address. Since 0.9.3 it serves only successful docs runs of a revision that was the head of `main`, so the risk is limited to code an Administrator merged. A separate origin, or a sandboxed page, removes it. *Priority: medium.*

### Data and text from other teams are trusted too far

- The semantic-model helpers in notebooks run a model's SQL as written, including a model received through a data share. Governed queries check every expression against an allow-list; the helpers do not.
- Descriptions, comments and model instructions reach AI agents as plain text, also across teams through shares, with no marking that they are data and not instructions.

*Priority: medium.*

### Races between the two portals and the Bridge

The admin portal, the user portal and the Bridge write to Polaris independently, without transactions.

- A membership change can race a database or share created in the other portal, so a removed member can keep a grant that nothing later revokes.
- Reading shares revokes expired ones as a side effect, from three processes at once. One share whose revocation keeps failing makes every share listing fail.
- Creating a database has compensation but no persisted intent, so a crash halfway leaves a database without roles, which blocks later membership changes.

*Priority: medium.*

### Availability with many users

These matter most in classes.

- One lock serialises every user-portal request, including notebook starts of up to 45 seconds and bucket deletions. The admin portal has the same pattern.
- Every request re-reads whole Polaris collections, notebooks fetch a token for every catalog request, and the Bridge re-grants every database on every token request.
- The notebook proxy buffers whole responses in memory, and the user portal has no memory limit. The extension gateways read request bodies before checking the caller, without a size limit.
- dbt's run queue is unbounded, so one member can hold the team's run slots.
- Nothing limits disk use: notebook and run volumes, collected run artifacts and dlt staging can fill the host.
- Docker's default address pools allow about 25 notebook sessions and runs at once, below the class sizes the documentation mentions.
- Restarting a dbt or Ingestion gateway or launcher, or one failed status check, can leave a run container behind or block a project until the run limit.
- In the OpenMetadata extension, one listing that fails to publish blocks the removal of every other team's withdrawn listings.

*Priority: medium.*

### Sign-in and identity

- Removing the `platform-admin` role takes effect when the session's access token expires, up to 15 minutes later.
- Keycloak clients keep the default full scope, so tokens of other first-party clients also carry the `platform-admin` role.
- The extensions' browser sign-in keeps its state on the server only, not bound to the browser, which allows signing a victim in to the attacker's account.
- A flood of sign-in attempts from many addresses can push out other people's sign-ins in progress.
- Rotating the portal client secrets or the Keycloak administrator password in `.env` breaks sign-in or startup, because Keycloak only reads them when the realm is first created.

*Priority: low to medium.*

### Supply chain and releases

- The installers check the release against a checksum file from the same release, which proves integrity, not origin.
- Release bundles name container images by tag, not by digest, and the extension release workflows do not require a commit on `main`. Whether that matters depends on who can push tags to the repository; pinning digests at release time removes it.
- Changes that cross the boundary between the core and the extensions do not always run the extensions' integration tests.

*Priority: low to medium.*

### Maintainability

- The extensions' sign-in, Bridge client and launcher code, and the semantic query engine, are copied between projects by hand, and the copies already differ.
- The token checks of the Bridge and the extensions have no unit tests; only the Docker smoke tests cover them.
- The extensions' SQLite stores have no schema versions, and dbt keeps every run record.
- The portals' REST APIs accept only a session cookie, so scripts have no first-class way to call them.

*Priority: low.*

## Not yet reviewed

The Cloudflare audit did not reach these areas; a next review should start here.

- The semantic query engine: compiler, query workers and the paths that publish semantic models.
- The agent skills' scripts.
- How Ingestion turns values from a parent API response into requests for child resources.
- Whether team roles can change a catalog's storage configuration, and how narrowly Polaris scopes the storage credentials it hands out.
- How the dbt runner publishes semantic models and pipeline properties after a build.

## Not verified

These depend on Polaris, Keycloak or RustFS at runtime, which neither review could run:

- whether Polaris refuses tokens it already issued to a principal once that principal is deleted;
- how narrowly vended storage credentials are scoped to a table's location;
- Keycloak's handling of `${VAR}` substitution in the realm import and of loopback redirect ports;
- forwarded-address handling behind a proxy (`FORWARDED_ALLOW_IPS`) end to end.
