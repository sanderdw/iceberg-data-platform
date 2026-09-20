# Reporting releases and continuation notes

Updated: 2026-09-20. This is the handoff for continuing the portal reporting feature.
The versions below are proposed milestones, not published releases or fixed dates.
The repository version is currently 0.3.1; the first reporting implementation is in
the working tree and listed under Unreleased. Check the current branch and diff
before continuing, and preserve existing work.

## Keep these decisions

- Visual authoring is the priority; SQL is an advanced option.
- Read live Apache Iceberg tables. Do not add dbt model builds, transformation
  pipelines or materialized reporting tables. Bounded result caching is acceptable.
- Share definitions within the active team and environment. Execute as the viewer,
  never as the report author. Recheck access for cache hits and result delivery.
- Resolve the latest committed snapshot at the start of a run. Cards using the
  same table share that snapshot; there is no cross-table transaction guarantee.
- Keep editable portal definitions independent of dbt Charts YAML.
- Use the verified native DuckDB query path followed by generated dbt Charts
  `values` boards. The dbt Charts 0.8.0 named DuckDB-profile path bypassed the
  dbt-duckdb connection plugin in the integration probe. Do not assume that adapter
  path works without a new proof. See [runtime findings](../user_portal/reporting/README.md).

## Current implementation

Implemented: table selection, visual filters/aggregates/grouping/time buckets,
sorting/limits, table/KPI/bar/line/area/scatter output, SQL preview and SQL copies,
named SQL parameters, saving/copying reports, revision conflicts, and dashboards
with ordered half/full-width cards and one mapped equality filter.

Definitions persist in a separate SQLite volume. Execution uses disposable
containers, viewer credentials, snapshot pinning, cancellation, resource limits,
and a 60-second session-scoped cache. SQL currently queries one selected table as
`source`; its expression/function allowlist also permits CTEs and self-joins.
Charts are static SVG images with an exact-value result table alongside them.

Last verification: 273 tests passed, 9 platform-specific tests skipped; report,
catalog, share and navigation browser checks passed. Live Polaris/RustFS checks
covered append freshness, dashboard filters, SQL, cache isolation, revoked access
and Iceberg v3 updates/deletions. All five graphical chart types rendered. The
reporting dependency audit found no known vulnerabilities at the time of the run.
These are historical results; run the checks appropriate to subsequent changes.

The local portal was started at http://localhost:3002. At the last account check,
there were no platform users or configured demo passwords. Automated smoke tests
remove their temporary users and tables; they do not leave a manual demo behind.

## Start here when resuming

1. Read this file, inspect `git status`, and review the existing implementation.
2. Check whether demo accounts now exist. If needed, create the optional local
   fixtures using the commands below; do not recreate them on every start.
3. Sign in as `demo-writer`, run Example 01 to create the synthetic neighborhood
   table, then create and save a report and dashboard in the UI.
4. Sign in as `demo-reader` to verify that shared definitions use the reader's
   own data permissions. Test another team/environment and verify isolation.
5. Complete the first-release acceptance checklist before adding the next feature.

```bash
# Optional local fixtures; passwords are written to .env, not this document.
uv run python -m scripts.setup --demo
docker compose run --build --rm keycloak-bootstrap --demo
```

Use `DEMO_WRITER_PASSWORD` and `DEMO_READER_PASSWORD` from `.env`. The writer and
reader belong to the demo team; select Development and its demo database. See
[demo setup](keycloak.md#verification-and-optional-demo-data) and
[reporting usage](../user_portal/README.md#reports-and-dashboards).

## Proposed 0.4 — Ship the reporting foundation

The feature code exists. Focus this milestone on manual acceptance and packaging.

- [ ] Exercise the complete Keycloak browser flow with persistent demo data:
  create a visual report, inspect SQL, save a SQL copy, and create a dashboard.
- [ ] Confirm author/administrator editing and reader copying, revision conflicts,
  cancellation, reload/Back/Forward, and context changes during an active query.
- [ ] Verify saved definitions survive a gateway restart and a backup/restore of
  the reports volume. Keep it separate from notebook-writable volumes.
- [ ] Run CI and verify installation bundles include matching portal, notebook
  and reporting images on the supported architectures.
- [ ] Update release metadata and notes when preparing an actual release. Version
  changes and publication follow [the release workflow](releasing.md).

Acceptance: a person can start from the documented setup, create a report over
live Iceberg data, share its definition with a teammate, see a new commit after
refresh, and recover saved definitions after restarting the gateway. No data
permission is gained by opening someone else's dashboard.

## Proposed 0.5 — Improve authoring and dashboard filters

- [ ] Make the builder more compact and guide users through table, filters,
  summarize/group, sort/limit and visualization without excessive scrolling.
- [ ] Offer chart-axis pickers from query output and sensible defaults. Add custom
  axis labels and number/date formats while preserving exact table values.
- [ ] Add SQL syntax highlighting, schema completion and parameter controls so
  ordinary parameter entry does not require editing JSON.
- [ ] Add typed dashboard filters: date range, number, text and category selection,
  with explicit per-card mappings. Bound category lookups and use viewer access.
- [ ] Show useful field-level validation, schema-change messages, empty states,
  and per-card progress. Keep Run explicit rather than querying every keystroke.
- [ ] Migrate definitions explicitly if their format changes; keep existing
  reports and dashboards readable.

Acceptance: both builder and SQL reports use the same typed filter semantics;
timezone/date boundaries are tested; existing saved definitions still open;
keyboard, mobile, light and dark browser flows pass.

## Proposed 0.6 — Query multiple tables safely

Implement the dependency/credential foundation before exposing joins in the UI.

- [ ] Add explicit table bindings for approved same-database SQL dependencies.
  Resolve aliases and CTE scopes; reject dynamic or unresolved table references.
- [ ] Authorize every table in the active team/environment and provision a
  distinct location-scoped storage secret for each dependency.
- [ ] Pin every dependency's snapshot for the run. Include all table UUIDs,
  schema IDs and snapshots in cache keys and reauthorization checks.
- [ ] Add visual inner/left joins with explicit keys and clear warnings about
  duplicate rows and inflated aggregates. Do not infer joins from names alone.
- [ ] Expand SQL functions only through reviewed allowlist changes and tests.

Acceptance: real integration fixtures join at least two tables, including an
access-denied dependency. Revocation, schema replacement and expired snapshots
cannot return fresh or cached data. A dashboard refresh remains consistent per
table during concurrent commits. Cross-team/environment queries stay unavailable.

## Proposed 0.7 — Explore and export results

- [ ] Prove a maintained interactive rendering contract before adding hover,
  legend selection or chart-click filtering. The current SVG path remains usable.
- [ ] Implement drill-through as a new bounded, authorized query with the
  selected filters. A browser chart must not receive the full source dataset.
- [ ] Add exact-value CSV export and evaluate PNG/PDF dashboard export. Export
  as the current viewer and preserve the run's filters, timezone and snapshots.
- [ ] Add optional auto-refresh only for visible dashboards, with cancellation,
  concurrency limits and backoff when the service is busy.

Acceptance: interactions and exports respect viewer permissions, cancellation
and response limits. Inactive tabs stop auto-refresh. Exact exported values do
not come from the approximate numeric projection used to draw charts.

## Proposed 0.8 — Revision workflows and measured performance

- [ ] Expose revision history and restoration; restoration creates a new revision.
- [ ] Define and implement explicit editor grants if author/admin editing is too
  restrictive. Sharing a definition must still grant no data access.
- [ ] Add an explicit environment-promotion flow with destination table mappings,
  compatibility checks and fresh authorization; never silently reuse source IDs.
- [ ] Benchmark selective and aggregate queries on representative data and
  concurrent viewers. Separate query, renderer-startup and queue timings.
- [ ] Add operational metrics for queue depth, timeouts and cache behavior without
  recording tokens, SQL parameters or result data.
- [ ] Evaluate a renderer pool only if measurements justify it and viewer/job
  isolation can be preserved. Move definition storage to PostgreSQL only if
  multiple gateway replicas become a requirement, with a migration/restore plan.

Acceptance: revision changes remain conflict-safe and auditable, promotion cannot
cross permission boundaries, and performance choices are backed by repeatable
measurements rather than latency promises from the small smoke-test fixture.

## Keep out of scope until separately requested

Scheduled email delivery, public/anonymous dashboard links, cross-environment
queries, arbitrary YAML/Jinja imports, semantic metric management, transformation
pipelines and materialized aggregates. These require additional product and
authorization decisions and are not implied by the releases above.

## Implementation map and verification

| Area | Files |
| --- | --- |
| Portal authoring | `user_portal/public/reports.js`, `app.js`, `index.html`, `style.css` |
| Definitions and SQL validation | `user_portal/reporting/definitions.py` |
| Definition persistence | `user_portal/reporting/store.py` |
| API, jobs, authorization and cache | `user_portal/reporting/api.py` |
| Worker isolation and query/render execution | `user_portal/reporting/runtime.py`, `execution.py`, `worker.py` |
| Worker dependencies | `user_portal/reporting/pyproject.toml`, `uv.lock`, `Dockerfile` |
| Packaging | `compose.users.yaml`, `scripts/release.py`, `.github/workflows/release.yml` |
| Automated coverage | `test/test_report_definitions.py`, `test/test_reporting_api.py`, `test/test_reporting.py`, `scripts/reporting-browser.mjs`, `scripts/reporting_smoke.py`, `scripts/reports_api_smoke.py` |

```bash
npm run verify
npm run test:reports-ui
npm run test:catalog
npm run test:shares-ui
npm run test:navigation-ui
npm run check:release

# Rebuild when worker/gateway code or dependencies change.
docker compose -f compose.users.yaml --profile images build
docker compose -f compose.users.yaml up -d --wait users

# Requires Polaris/RustFS and the reporting/notebook images; cleans its fixtures.
uv run --all-groups python -m scripts.reporting_smoke
```

The reporting image uses Python 3.13 and its own lock; the gateway uses Python 3.14.
Preserve this separation until compatibility is verified. Renderer title/label
fields can interpret Jinja: keep user text out of template-bearing configuration.
The portal already displays report names as plain text, and the KPI label is fixed.
Do not loosen these boundaries when adding custom labels or an interactive renderer.
