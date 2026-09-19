// Seeds the demo story on a running local stack and captures the deck's screenshots.
// Usage: node scripts/presentation-screenshots.mjs [--only 03,24]
import {randomBytes} from 'node:crypto';
import {chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync} from 'node:fs';
import {parseArgs, parseEnv} from 'node:util';
import {chromium, expect} from '@playwright/test';

const env = parseEnv(readFileSync('.env', 'utf8'));
const {values: args} = parseArgs({options: {only: {type: 'string'}}});
const only = args.only ? new Set(args.only.split(',').map(id => id.trim())) : null;
const OUT = 'presentation/screenshots', STATE = '.local/presentation/state.json';
const RUSTFS = 'http://localhost:9001/rustfs/console/browser/', KEYCLOAK_USERS = env.KEYCLOAK_ORIGIN + '/admin/master/console/#/iceberg/users';

// Fictional grid operator. Roles differ per team for sander, which the access dialog shows.
// mila administers grid-planning and shares one of its tables with a municipality.
const TEAMS = [
  ['grid-planning', 'Capacity planning for the regional grid'],
  ['asset-management', 'Lifecycle of stations, cables and transformers'],
  ['outage-response', 'Outage detection and restoration'],
];
const DATABASES = [
  ['grid-capacity', 'grid-planning', 'development'], ['grid-capacity', 'grid-planning', 'production'],
  ['smart-meter-readings', 'grid-planning', 'development'], ['grid-assets', 'asset-management', 'development'],
  ['outage-events', 'outage-response', 'production'],
];
const USERS = [
  ['noor', 'Noor', 'Example', [['outage-response', 'writer']]],
  ['sander', 'Sander', 'Example', [['grid-planning', 'writer'], ['asset-management', 'reader']]],
  ['alex', 'Alex', 'Example', [['grid-planning', 'reader']]],
  ['mila', 'Mila', 'Example', [['grid-planning', 'admin']]],
];
const DEMO_USER = 'sander', DEMO_TEAM = 'grid-planning', DEMO_DATABASE = 'smart-meter-readings';
const TEAM_ADMIN = 'mila', SHARE = {name: 'municipality-heat-plan', recipient: 'Municipality of Example · heat transition team', description: 'Street-level consumption and solar production for the district heating plan', namespace: 'synthetic', table: 'neighborhood_electricity'};

// Passwords chosen at the forced first sign-in; kept outside git so the script can run again.
const state = existsSync(STATE) ? JSON.parse(readFileSync(STATE, 'utf8')) : {users: {}};
function saveState() { mkdirSync('.local/presentation', {recursive: true}); writeFileSync(STATE, JSON.stringify(state), {mode: 0o600}); chmodSync(STATE, 0o600); }

const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined});
const newContext = () => browser.newContext({viewport: {width: 2000, height: 1250}, deviceScaleFactor: 2, colorScheme: 'dark'});
const wanted = id => !only || only.has(id);
const need = (...ids) => ids.some(id => wanted(String(id).padStart(2, '0')));
async function shot(page, name) {
  if (!wanted(name.slice(0, 2))) return;
  await page.waitForTimeout(400);
  await page.screenshot({path: `${OUT}/${name}.png`});
  console.log(`${OUT}/${name}.png`);
}

// Keycloak sign-in; a temporary password is replaced on the way in.
async function signIn(page, origin, username, password, remember) {
  await page.goto(origin + '/auth/login');
  await page.locator('#username').fill(username);
  await page.locator('#password').fill(password);
  await page.locator('#kc-login').click();
  const change = page.locator('#password-new');
  await Promise.race([change.waitFor(), page.waitForURL(url => url.origin === origin)]);
  if (await change.isVisible()) {
    const chosen = randomBytes(24).toString('base64url');
    await change.fill(chosen);
    await page.locator('#password-confirm').fill(chosen);
    remember(chosen); saveState();
    await page.locator('input[type=submit], button[type=submit]').click();
  }
  await page.waitForURL(url => url.origin === origin);
}

async function api(context, method, path, data) {
  const response = await context.request.fetch(env.PORTAL_ORIGIN + path, {method, data, headers: {'X-Portal-Request': '1'}});
  if (!response.ok()) throw new Error(`${method} ${path}: ${response.status()} ${(await response.json()).error}`);
  return response.json();
}

async function seed(admin) {
  let overview = await api(admin, 'GET', '/api/overview');
  for (const [name, description] of TEAMS) if (!overview.teams.some(t => t.name === name)) await api(admin, 'POST', '/api/teams', {name, description});
  overview = await api(admin, 'GET', '/api/overview');
  const team = name => overview.teams.find(t => t.name === name).id;
  for (const [name, owner, environment] of DATABASES) {
    if (!overview.databases.some(d => d.name === name && d.environment === environment)) await api(admin, 'POST', '/api/databases', {name, team: team(owner), environment});
  }
  for (const [name, first_name, last_name, memberships] of USERS) {
    if (overview.users.some(u => u.name === name)) continue;
    const created = await api(admin, 'POST', '/api/identity/users', {name, first_name, last_name, email: `${name}@example.com`,
      memberships: memberships.map(([t, role]) => ({team: team(t), role}))});
    state.users[name] = created.identity.temporaryPassword; saveState();
  }
}

// Runs every cell of the notebook that is open in the editor and waits for its last result.
async function runNotebook(page, label, done, click) {
  await page.locator('#notebook-file').selectOption({label});
  const frame = page.frameLocator('#frame-host iframe');
  await frame.locator('.cm-content').first().waitFor({timeout: 120000});
  await frame.locator('.cm-content').first().click();
  await page.keyboard.press('Control+Shift+r');
  if (click) await frame.getByRole('button', {name: click, exact: true}).click({timeout: 120000});
  await frame.getByText(done).first().waitFor({timeout: 180000});
  return frame;
}
const toHeading = (frame, name) => frame.getByRole('heading', {name}).first().evaluate(h => h.scrollIntoView({block: 'start'}));
// Puts the code cell that contains the text at the top of the editor.
const toCell = (frame, text) => frame.locator('.cm-content').filter({hasText: text}).first().evaluate(c => c.scrollIntoView({block: 'start'}));

async function openNamespace(page, namespace) {
  await page.goto(env.USER_ORIGIN);
  await page.locator('#databases button').filter({hasText: DEMO_DATABASE}).first().click();
  await page.locator('.object-row').filter({hasText: namespace}).getByRole('button').click();
  await page.locator('.object-row').filter({hasText: 'TABLE'}).first().waitFor();
}
async function openTable(page, namespace, table) {
  await openNamespace(page, namespace);
  await page.locator('.object-row').filter({hasText: table}).getByRole('button').click();
  await page.getByRole('tab', {name: 'Overview', exact: true}).waitFor();
  await expect(page.getByRole('tabpanel')).toContainText('Format version');
}
const openTab = (page, name) => page.getByRole('tab', {name, exact: true}).click();
async function loadPreview(page) {
  await page.getByRole('button', {name: 'Load preview', exact: true}).click();
  await expect(page.getByRole('tabpanel')).toContainText('rows shown', {timeout: 60000});
}

try {
  mkdirSync(OUT, {recursive: true});

  // What a visitor of the user portal sees: the welcome screen, then the Keycloak sign-in page.
  const visitor = await newContext(), login = await visitor.newPage();
  await login.goto(env.USER_ORIGIN);
  await login.getByRole('link', {name: 'Sign in with Keycloak'}).waitFor();
  await shot(login, '07-user-welcome');
  await login.goto(env.USER_ORIGIN + '/auth/login');
  await login.locator('#username').waitFor();
  await shot(login, '01-keycloak-login');
  await visitor.close();

  const admin = await newContext(), portal = await admin.newPage();
  await signIn(portal, env.PORTAL_ORIGIN, env.PLATFORM_ADMIN_USERNAME, state.admin || env.PLATFORM_ADMIN_PASSWORD, chosen => { state.admin = chosen; });
  await seed(admin);
  const database = (await api(admin, 'GET', '/api/overview')).databases.find(d => d.name === DEMO_DATABASE && d.environment === 'development');

  // Team workspace: notebooks first, so the catalog and the bucket have tables to show.
  const member = await newContext(), workspace = await member.newPage();
  workspace.setDefaultTimeout(30000);
  await signIn(workspace, env.USER_ORIGIN, DEMO_USER, state.users[DEMO_USER], chosen => { state.users[DEMO_USER] = chosen; });
  await workspace.locator('#workspace-screen').waitFor();
  await workspace.locator('#team').selectOption(await workspace.locator('#team option').filter({hasText: DEMO_TEAM}).getAttribute('value'));
  await workspace.locator('#databases button').filter({hasText: DEMO_DATABASE}).first().click();
  await workspace.locator('.catalog-summary').waitFor();
  await shot(workspace, '08-user-catalog-empty');

  // The notebooks create both example tables; later shots need them, so a first run never skips this.
  const contents = await (await member.request.get(`${env.USER_ORIGIN}/api/contents?database=${database.id}`)).json();
  const seeded = ['synthetic', 'iceberg_v3'].every(name => contents.namespaces.some(ns => ns[0] === name));
  if (!seeded || need(10, 11, 12, 13, 14, 25, 26, 27, 28)) {
    await workspace.locator('#example-write').click();
    let frame = workspace.frameLocator('#frame-host iframe');
    await frame.locator('.cm-content').first().waitFor({timeout: 180000});
    frame = await runNotebook(workspace, '01 · Neighborhood data with PyIceberg', /Created and populated|Table already contains/, 'Create example table');
    await toHeading(frame, '2. Write to Iceberg');
    await shot(workspace, '10-notebook-01-created');

    frame = await runNotebook(workspace, '02 · Visualize with DuckDB', 'Totals by street');
    // The plots are the large inline images; marimo also inlines small icons.
    const plots = () => frame.locator('img[src^="data:image"]').evaluateAll(images => images.filter(i => i.naturalWidth >= 800).length);
    await expect.poll(plots, {timeout: 60000}).toBe(2);
    await frame.locator('img[src^="data:image"]').evaluateAll(images => images.find(i => i.naturalWidth >= 800).scrollIntoView({block: 'center'}));
    await shot(workspace, '11-notebook-02-duckdb-chart');

    frame = await runNotebook(workspace, '03 · Native DuckDB on Iceberg', 'Example Solar Street', 'Connect / refresh credentials');
    await toCell(frame, 'DESCRIBE selected_iceberg_table');
    await shot(workspace, '12-notebook-03-attach');
    await toCell(frame, 'iceberg_snapshots');
    await shot(workspace, '13-notebook-03-snapshots');
    await toHeading(frame, 'Aggregate the neighborhood example');
    await shot(workspace, '14-notebook-03-aggregate');

    frame = await runNotebook(workspace, '04 · Write Iceberg v3 with DuckDB', /deleted \d+ fault events/);
    await toCell(frame, 'CREATE TABLE');
    await shot(workspace, '25-notebook-04-v3-create');
    await toHeading(frame, '5. Update and delete rows');
    await shot(workspace, '26-notebook-04-v3-delete');

    frame = await runNotebook(workspace, '05 · Read Iceberg v3 with DuckDB', /Every write is a snapshot/);
    await toHeading(frame, '1. Variant');
    await shot(workspace, '27-notebook-05-variant');
    await toHeading(frame, '5. Row lineage');
    await shot(workspace, '28-notebook-05-lineage');
    await workspace.getByRole('button', {name: 'Catalog', exact: true}).click();
  }

  // The same tables in the catalog browser, without a notebook.
  await openNamespace(workspace, 'synthetic');
  await shot(workspace, '15-user-catalog-namespace');
  await openTable(workspace, 'synthetic', 'neighborhood_electricity');
  await shot(workspace, '16-user-table-overview');
  await openTab(workspace, 'Schema');
  await shot(workspace, '17-user-table-schema');
  await openTab(workspace, 'Snapshots');
  await shot(workspace, '18-user-table-snapshots');
  await workspace.getByRole('button', {name: 'Preview snapshot', exact: true}).last().click();
  await loadPreview(workspace);
  await shot(workspace, '19-user-table-preview');
  await openTable(workspace, 'iceberg_v3', 'sensor_events');
  await shot(workspace, '29-user-table-v3-overview');
  await openTab(workspace, 'Preview');
  await loadPreview(workspace);
  await shot(workspace, '30-user-table-v3-preview');

  // A team administrator shares one table with an external party, without a portal administrator.
  if (need(32, 33, 34, 35)) {
    const owner = await newContext(), sharing = await owner.newPage();
    sharing.setDefaultTimeout(30000);
    await signIn(sharing, env.USER_ORIGIN, TEAM_ADMIN, state.users[TEAM_ADMIN], chosen => { state.users[TEAM_ADMIN] = chosen; });
    await sharing.locator('#workspace-screen').waitFor();
    await sharing.locator('#databases button').filter({hasText: DEMO_DATABASE}).first().click();
    await sharing.locator('summary').filter({hasText: 'Data shares'}).click();
    await sharing.getByRole('button', {name: 'New data share', exact: true}).waitFor();
    const existing = (await (await owner.request.get(`${env.USER_ORIGIN}/api/shares?database=${database.id}`)).json()).shares.find(s => s.name === SHARE.name);
    await sharing.getByRole('button', {name: 'New data share', exact: true}).click();
    await sharing.getByLabel('Share name').fill(SHARE.name);
    await sharing.getByLabel('Recipient').fill(SHARE.recipient);
    await sharing.getByLabel('Description').fill(SHARE.description);
    await sharing.getByLabel(/^Expires/).fill(`${new Date().getUTCFullYear() + 1}-12-31`);
    await sharing.locator('.share-tree summary').filter({hasText: SHARE.namespace}).click();
    await sharing.getByLabel(SHARE.table).check();
    // Database facts above the form, so the capture says which database is shared.
    await sharing.locator('.catalog-summary').evaluate(s => s.scrollIntoView({block: 'start'}));
    await shot(sharing, '32-user-share-form');
    // The pictured secret must not outlive the capture: it is replaced once more at the end.
    if (existing) { await sharing.getByRole('button', {name: 'Cancel', exact: true}).click(); await sharing.getByRole('button', {name: 'New secret', exact: true}).first().click(); }
    else await sharing.getByRole('button', {name: 'Create share and show credential', exact: true}).click();
    await sharing.locator('.share-issued').waitFor();
    await sharing.locator('.share-issued h3').first().evaluate(h => h.scrollIntoView({block: 'start'}));
    await shot(sharing, '33-user-share-credential');
    await sharing.getByRole('button', {name: 'Done, I stored the secret', exact: true}).click();
    await sharing.getByRole('region', {name: 'Data shares', exact: true}).waitFor();
    await sharing.locator('summary').filter({hasText: 'Data shares'}).evaluate(h => h.scrollIntoView({block: 'start'}));
    await shot(sharing, '34-user-share-list');
    const share = (await (await owner.request.get(`${env.USER_ORIGIN}/api/shares?database=${database.id}`)).json()).shares.find(s => s.name === SHARE.name);
    const renewed = await owner.request.post(`${env.USER_ORIGIN}/api/shares/${share.id}/rotate`, {data: {}, headers: {'X-Portal-Request': '1'}});
    if (!renewed.ok()) throw new Error(`The pictured secret was not replaced: ${renewed.status()}`);
    await owner.close();
  }

  await portal.goto(env.PORTAL_ORIGIN);
  for (const [page, name] of [['databases', '02-admin-databases'], ['users', '03-admin-users'], ['teams', '04-admin-teams'], ['shares', '35-admin-shares'], ['infrastructure', '05-admin-infrastructure']]) {
    await portal.locator(`[data-page="${page}"]`).first().click();
    await portal.waitForLoadState('networkidle');
    await shot(portal, name);
  }
  await portal.locator('[data-page="explorer"]').first().click();
  for (const node of [DEMO_DATABASE, 'synthetic']) await portal.locator('details > summary').filter({hasText: node}).first().click();
  await portal.locator('details').filter({hasText: 'neighborhood_electricity'}).first().waitFor();
  await portal.getByRole('heading', {name: 'All databases'}).evaluate(h => h.scrollIntoView({block: 'start'}));
  await shot(portal, '20-admin-explorer');
  // A dialog is small on a 2000 px page: same 4000×2500 image from a narrower viewport.
  const close = await browser.newContext({viewport: {width: 1600, height: 1000}, deviceScaleFactor: 2.5, colorScheme: 'dark', storageState: await admin.storageState()});
  const access = await close.newPage();
  await access.goto(env.PORTAL_ORIGIN);
  await access.locator('[data-page="users"]').first().click();
  await access.getByRole('row').filter({hasText: DEMO_USER}).getByRole('button', {name: 'Edit access', exact: true}).click();
  await access.getByRole('dialog').waitFor();
  await shot(access, '24-admin-edit-access');
  await close.close();

  // The accounts behind the users, in the Keycloak admin console.
  const operator = await newContext(), keycloak = await operator.newPage();
  await keycloak.goto(KEYCLOAK_USERS);
  await keycloak.locator('#username').fill('admin');
  await keycloak.locator('#password').fill(env.KEYCLOAK_ADMIN_PASSWORD);
  await keycloak.locator('#kc-login').click();
  await keycloak.getByRole('grid').or(keycloak.getByRole('table')).first().getByText(DEMO_USER, {exact: true}).waitFor();
  await shot(keycloak, '23-keycloak-users');
  await operator.close();

  // The tables as plain objects in the bucket of the database.
  const storage = await newContext(), rustfs = await storage.newPage();
  const folder = async key => { await rustfs.goto(`${RUSTFS}?bucket=${database.bucket}&key=${encodeURIComponent(key)}`); await rustfs.getByText(/^Loaded \d+ objects/).waitFor(); };
  await rustfs.goto(RUSTFS);
  await rustfs.locator('#accessKey').fill(env.RUSTFS_ACCESS_KEY);
  await rustfs.locator('#secretKey').fill(env.RUSTFS_SECRET_KEY);
  await rustfs.getByRole('button', {name: 'Login', exact: true}).click();
  await rustfs.waitForURL(url => !url.pathname.includes('login'));
  await folder('synthetic/neighborhood_electricity/metadata/');
  await shot(rustfs, '21-rustfs-metadata-files');
  await rustfs.getByRole('row').filter({hasText: '00001-'}).getByRole('button', {name: 'Preview', exact: true}).click();
  await rustfs.getByRole('dialog').getByText('"format-version"').waitFor();
  await rustfs.getByRole('dialog').getByRole('button').first().click(); // maximize
  await shot(rustfs, '22-rustfs-metadata-json');
  await folder('iceberg_v3/sensor_events/data/');
  await shot(rustfs, '31-rustfs-v3-puffin');
  await storage.close();
  console.log('PASS');
} finally {
  await browser.close();
}
