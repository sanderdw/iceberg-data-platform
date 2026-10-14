// Seeds the demo story on a running local stack and captures the deck's screenshots.
// Usage: node scripts/presentation-screenshots.mjs [--only 03,24] [--seed-only]
// --seed-only stops once grid-sensors exists, so the data can be loaded and modelled by an agent first.
import {randomBytes} from 'node:crypto';
import {spawn, spawnSync} from 'node:child_process';
import {chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {parseArgs, parseEnv} from 'node:util';
import {chromium, expect} from '@playwright/test';

const env = parseEnv(readFileSync('.env', 'utf8'));
const {values: args} = parseArgs({options: {only: {type: 'string'}, 'seed-only': {type: 'boolean'}}});
const only = args.only ? new Set(args.only.split(',').map(id => id.trim())) : null;
const OUT = 'presentation/screenshots', STATE = '.local/presentation/state.json';
const RUSTFS = 'http://localhost:9001/rustfs/console/browser/', KEYCLOAK_USERS = env.KEYCLOAK_ORIGIN + '/admin/master/console/#/iceberg/users';

// Fictional grid operator. Roles differ per team for sander, which the access dialog shows.
// mila administers grid-planning and creates grid-sensors herself. sander loads the synthetic feeder
// SCADA export there from their own computer with the DuckDB CLI, and builds the feeder_load
// semantic model with the semantic-model skill (work files in presentation/grid). mila shares the
// model with a research partner and with outage-response, where noor reads it without owning a
// database, and lists it in the data catalog, which the OpenMetadata extension publishes. Both team
// administrators enable Conversational BI, and noor asks it about the model.
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
  ['noor', 'Noor', 'Example', [['outage-response', 'admin']]],
  ['sander', 'Sander', 'Example', [['grid-planning', 'writer'], ['asset-management', 'reader']]],
  ['alex', 'Alex', 'Example', [['grid-planning', 'reader']]],
  ['mila', 'Mila', 'Example', [['grid-planning', 'admin']]],
];
const GRID_DATABASE = {name: 'grid-sensors', description: 'Feeder load measurements with their semantic model'};
const DEMO_USER = 'sander', DEMO_TEAM = 'grid-planning', DEMO_DATABASE = GRID_DATABASE.name;
const NAMESPACE = 'grid_sensors', TABLE = 'feeder_load', MODEL = 'feeder_load', MODEL_TABLES = 3;
const MODEL_FILE = `presentation/grid/semantic-models/${NAMESPACE}/${MODEL}.json`, QUESTIONS_FILE = MODEL_FILE.replace(/\.json$/, '.questions.sql');
const TEAM_ADMIN = 'mila', SHARE = {name: 'feeder-load-research', recipient: 'University of Example · grid research', description: 'Synthetic feeder load with its semantic model, for congestion research'};
const TEAM_SHARE = {name: 'feeder-load', team: 'outage-response', description: 'Feeder load with the agreed overload metrics'}, RECIPIENT = 'noor';
const LISTING = {name: 'feeder-load-catalog', contact: 'grid-planning@example.com', description: 'Feeder load with the agreed overload metrics, for every team to find'};
// Example notebook 09 writes a second product with Polars into the same database, listed in the catalog as well.
const POLARS = {notebook: '09 · Write an AI-ready grid forecast with Polars', namespace: 'ai_grid_forecast', model: 'mv_load_forecast', tables: 8};
const POLARS_SHARE = {name: 'mv-load-forecast', team: 'outage-response', description: 'MV load forecast with the agreed congestion metrics'};
const POLARS_QUESTION = 'Which substations get the most offtake congestion points?';
const POLARS_LISTING = {name: 'mv-load-forecast-catalog', contact: 'grid-planning@example.com', description: 'Synthetic MV load forecast and congestion outlook, for every team to find'};
// The OpenMetadata extension's local catalog (compose.openmetadata.yaml) and its default administrator.
const OM_ORIGIN = env.OM_UI_URL || 'http://localhost:8585', OM_ADMIN = ['admin@open-metadata.org', 'admin'];
const CBI_ORIGIN = 'http://localhost:3007', CBI_QUESTION = 'Which feeders ran above their rating in the week of 21 September, and for how many hours?';
// iceberg_connect.py keeps sander's sign-in here instead of in the real ~/.config.
const CONNECT_ENV = {...process.env, XDG_CONFIG_HOME: resolve('.local/presentation/config')};

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

// Signing out stops the notebook runtimes of that session, so reruns do not run out of notebook slots.
async function signOut(context) {
  const response = await context.request.delete(env.USER_ORIGIN + '/api/session', {data: {}, headers: {'X-Portal-Request': '1'}});
  if (!response.ok()) throw new Error(`Sign-out: ${response.status()}`);
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
async function runNotebook(page, label, done) {
  await page.locator('#notebook-file').selectOption({label});
  const frame = page.frameLocator('#frame-host iframe');
  await frame.locator('.cm-content').first().waitFor({timeout: 120000});
  await frame.locator('.cm-content').first().click();
  await page.keyboard.press('Control+Shift+r');
  await frame.getByText(done).first().waitFor({timeout: 180000});
  // Text of a markdown cell shows up long before the queries below it have finished.
  await expect(frame.locator('[data-status="queued"], [data-status="running"]')).toHaveCount(0, {timeout: 180000});
  return frame;
}
const toHeading = (frame, name) => frame.getByRole('heading', {name}).first().evaluate(h => h.scrollIntoView({block: 'start'}));

async function openNamespace(page, namespace) {
  await page.goto(env.USER_ORIGIN);
  await page.locator('#databases button').filter({hasText: DEMO_DATABASE}).first().click();
  await page.locator('.object-row').filter({hasText: namespace}).getByRole('button').click();
  await page.locator('.object-row').filter({hasText: 'TABLE'}).first().waitFor();
}
// The feeder_load table and the feeder_load semantic model share a name, so a row is found by name and kind.
const objectRow = (page, name, kind) => page.locator('.object-row').filter({hasText: kind}).filter({has: page.locator('strong').getByText(name, {exact: true})});
async function openTable(page, namespace, table) {
  await openNamespace(page, namespace);
  await objectRow(page, table, 'Iceberg table').getByRole('button').click();
  await page.getByRole('tab', {name: 'Overview', exact: true}).waitFor();
  await expect(page.getByRole('tabpanel')).toContainText('Format version');
}
async function openModel(page, name) {
  await objectRow(page, name, 'Semantic model').getByRole('button').click();
  await page.getByRole('tab', {name: 'Diagram', exact: true}).waitFor();
  await expect(page.getByRole('tabpanel')).toContainText('AI context');
}
// The whole diagram fits below the model's name once the page header scrolls away.
async function showDiagram(page) {
  await openTab(page, 'Diagram');
  await page.locator('.sd-box').first().waitFor();
  await page.locator('#namespace-title').evaluate(h => { h.scrollIntoView({block: 'start'}); scrollBy(0, -48); });
}
// The demo databases live in development; the portal remembers the last environment per session.
async function useDevelopment(page) {
  if (await page.locator('#environment').inputValue() === 'development') return;
  await page.locator('#environment').selectOption('development');
  await expect(page.locator('#environment')).toHaveValue('development');
}
// Selecting the model in the share form selects the tables it reads as well.
async function pickModel(page, namespace = NAMESPACE, model = MODEL, tables = MODEL_TABLES) {
  await page.locator('.share-tree summary').filter({hasText: namespace}).click();
  await page.locator('.share-tree .share-object').filter({hasText: 'Semantic model'}).filter({hasText: model}).locator('input').check();
  await expect(page.locator('.share-selection')).toContainText(`SELECTED · ${tables + 1}`);
}
// A share for another team only: its members read the model with their own accounts, no credential.
async function fillTeamShare(page, shareDatabase, share, ...model) {
  const teams = await (await page.context().request.get(`${env.USER_ORIGIN}/api/share-teams`)).json();
  await shareDatabase.getByRole('button', {name: 'New data share', exact: true}).click();
  await page.getByLabel('Share name').fill(share.name);
  await page.getByLabel('Another team', {exact: true}).check();
  await page.getByLabel('Recipient team', {exact: true}).selectOption(teams.find(t => t.name === share.team).id);
  await page.getByLabel('Externally', {exact: true}).uncheck();
  await page.getByLabel('Description', {exact: true}).fill(share.description);
  await pickModel(page, ...model);
  await page.locator('.database-shares').filter({hasText: DEMO_DATABASE}).first().evaluate(s => s.scrollIntoView({block: 'start'}));
}
// A listing in the data catalog only: no team, no external party, a contact for access requests.
async function fillListing(form, listing, ...model) {
  await form.goto(env.USER_ORIGIN);
  await form.locator('#workspace-screen').waitFor();
  await toPage(form, 'Data shares');
  const shareDatabase = form.locator('.database-shares').filter({has: form.getByRole('heading', {name: DEMO_DATABASE, exact: true})});
  await shareDatabase.getByRole('button', {name: 'New data share', exact: true}).click();
  await form.getByLabel('Share name').fill(listing.name);
  await form.getByLabel('Externally', {exact: true}).uncheck();
  await form.getByLabel('Listed in the data catalog', {exact: true}).check();
  await form.getByLabel('Contact for access requests').fill(listing.contact);
  await form.getByLabel('Description', {exact: true}).fill(listing.description);
  await pickModel(form, ...model);
  return shareDatabase;
}
// Creates the listing unless it exists, and returns the name of its OpenMetadata data product.
async function createListing(form, shareDatabase, context, database, listing) {
  const shares = async () => (await (await context.request.get(`${env.USER_ORIGIN}/api/shares?database=${database.id}`)).json()).shares;
  if ((await shares()).some(s => s.name === listing.name)) await form.getByRole('button', {name: 'Cancel', exact: true}).click();
  else await form.getByRole('button', {name: 'Create share', exact: true}).click();
  await shareDatabase.getByText(listing.name, {exact: true}).waitFor();
  return 'iceberg-' + (await shares()).find(s => s.name === listing.name).id;
}
async function omSignIn(om) {
  om.setDefaultTimeout(30000);
  await om.goto(OM_ORIGIN + '/signin');
  await om.locator('#email').fill(OM_ADMIN[0]);
  await om.locator('#password').fill(OM_ADMIN[1]);
  await om.getByRole('button', {name: 'Sign In', exact: true}).click();
  await om.waitForURL(url => !url.pathname.includes('signin'));
}
// The extension polls every minute; the data product shows up once the listing is published.
async function openDataProduct(om, product, listing) {
  for (let attempt = 0; ; attempt++) {
    await om.goto(`${OM_ORIGIN}/dataProduct/${product}`);
    if (await om.getByText(listing.name, {exact: true}).first().waitFor({timeout: 10000}).then(() => true, () => false)) break;
    if (attempt > 30) throw new Error(`${product} did not reach OpenMetadata`);
    await om.waitForTimeout(10000);
  }
  // The description opens collapsed; its `more` link has no height, so it is clicked through the DOM.
  await om.getByText('more', {exact: true}).first().dispatchEvent('click');
  await om.locator('img[alt^="Tables and keys"]').first().waitFor();
}
const toPage = (page, name) => page.locator('#workspace-nav').getByRole('button', {name, exact: true}).click();
const openTab = (page, name) => page.getByRole('tab', {name, exact: true}).click();
async function loadPreview(page) {
  await page.getByRole('button', {name: 'Load preview', exact: true}).click();
  await expect(page.getByRole('tabpanel')).toContainText('rows shown', {timeout: 60000});
}

function run(command, argv, {cwd, input} = {}) {
  const result = spawnSync(command, argv, {env: CONNECT_ENV, cwd, input, stdio: [input ? 'pipe' : 'inherit', 'inherit', 'inherit']});
  if (result.status !== 0) throw new Error(`${command} ${argv.join(' ')}: exit ${result.status}`);
}
// iceberg_connect.py login as the signed-in user: the device code is approved in that user's browser session.
async function connectAs(page) {
  const login = spawn('uv', ['run', 'user_portal/client/iceberg_connect.py', 'login'], {env: {...CONNECT_ENV, BROWSER: 'true'}});
  const done = new Promise((resolve, reject) => login.on('exit', code => code === 0 ? resolve() : reject(new Error(`iceberg_connect.py login: exit ${code}`))));
  const url = await new Promise((resolve, reject) => {
    let out = '';
    login.stdout.on('data', chunk => { out += chunk; const match = out.match(/Open (\S+) and confirm/); if (match) resolve(match[1]); });
    login.on('exit', () => reject(new Error('iceberg_connect.py login printed no URL: ' + out)));
  });
  const approve = await page.context().newPage();
  await approve.goto(url);
  // Keycloak asks to confirm the code, then for consent; #kc-login is the confirming button on both.
  for (let step = 0; step < 3 && !(await approve.getByText(/successful|you may close/i).count()); step++) {
    await approve.locator('#kc-login').click();
    await approve.waitForLoadState('networkidle');
  }
  await done;
  await approve.close();
}

// The extension's own Keycloak client; a context already signed in to a portal passes straight through.
// A team administrator enables Conversational BI once per team and environment.
async function enableCbi(page, team) {
  const row = page.locator('nav.models .not-enabled').filter({hasText: `${team} · development`});
  if (await row.count()) { await row.getByRole('button', {name: 'Enable', exact: true}).click(); await row.waitFor({state: 'detached'}); }
}
async function cbiSignIn(page, username) {
  await page.goto(CBI_ORIGIN + '/');
  await page.getByRole('button', {name: /Sign in with Keycloak/}).click();
  if (username) {
    await page.locator('#username').fill(username);
    await page.locator('#password').fill(state.users[username]);
    await page.locator('#kc-login').click();
  }
  await page.waitForURL(url => url.origin === CBI_ORIGIN);
  await page.locator('nav.models').waitFor({timeout: 60000});
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

  // A team administrator creates a database from the user portal, without a portal administrator.
  const owner = await newContext(), sharing = await owner.newPage();
  sharing.setDefaultTimeout(30000);
  await signIn(sharing, env.USER_ORIGIN, TEAM_ADMIN, state.users[TEAM_ADMIN], chosen => { state.users[TEAM_ADMIN] = chosen; });
  await sharing.locator('#workspace-screen').waitFor();
  await useDevelopment(sharing);
  await toPage(sharing, 'Databases');
  await sharing.locator('#new-database').click();
  await sharing.getByLabel('Database name', {exact: true}).fill(GRID_DATABASE.name);
  await sharing.getByLabel('Description (optional)').fill(GRID_DATABASE.description);
  await shot(sharing, '36-user-databases-create');
  const form = sharing.locator('#database-form');
  if ((await api(admin, 'GET', '/api/overview')).databases.some(d => d.name === GRID_DATABASE.name)) await form.getByRole('button', {name: 'Cancel', exact: true}).click();
  else {
    await form.getByRole('button', {name: 'Create database', exact: true}).click();
    await sharing.locator('#managed-databases').getByText(GRID_DATABASE.name, {exact: true}).waitFor({timeout: 120000});
  }
  const database = (await api(admin, 'GET', '/api/overview')).databases.find(d => d.name === DEMO_DATABASE && d.environment === 'development');

  // Team workspace: the notebook first, so the catalog and the bucket have tables to show.
  const member = await newContext(), workspace = await member.newPage();
  workspace.setDefaultTimeout(30000);
  await signIn(workspace, env.USER_ORIGIN, DEMO_USER, state.users[DEMO_USER], chosen => { state.users[DEMO_USER] = chosen; });
  await workspace.locator('#workspace-screen').waitFor();
  await workspace.locator('#team').selectOption(await workspace.locator('#team option').filter({hasText: DEMO_TEAM}).getAttribute('value'));
  await useDevelopment(workspace);
  await workspace.locator('#databases button').filter({hasText: DEMO_DATABASE}).first().click();
  await workspace.locator('.catalog-summary').waitFor();
  await shot(workspace, '08-user-catalog-empty');

  if (args['seed-only']) {
    await signOut(member);
    console.log(`Seeded. Load the data into ${database.id} (${DEMO_DATABASE}), then run again.`);
    process.exit(0);
  }
  // sander loads the SCADA export from their own computer with the DuckDB CLI, as themselves; later shots need it.
  const contents = await (await member.request.get(`${env.USER_ORIGIN}/api/contents?database=${database.id}`)).json();
  if (!contents.namespaces.some(ns => ns[0] === NAMESPACE)) {
    await connectAs(workspace);
    run('uv', ['run', 'presentation/grid/grid_sensors.py', '.local/presentation/scada-export']);
    run('uv', ['run', '../../user_portal/client/iceberg_connect.py', 'shell', database.id, '--write'], {cwd: '.local/presentation', input: readFileSync('presentation/grid/load.sql')});
  }
  // The model comes from the semantic-model skill's work files, published only when it is missing.
  const inNamespace = await (await member.request.get(`${env.USER_ORIGIN}/api/contents?database=${database.id}&namespace=${NAMESPACE}`)).json();
  if (existsSync(MODEL_FILE) && !JSON.stringify(inNamespace.semanticModels ?? inNamespace).includes(`"${MODEL}"`)) {
    await connectAs(workspace);
    run('uv', ['run', '.agents/skills/semantic-model/scripts/semantic_model.py', 'publish', database.id, NAMESPACE, MODEL_FILE, '--name', MODEL, '--questions', QUESTIONS_FILE]);
  }

  // The starter notebook, started through the API on the feeder_load table: it lists the tables and previews it.
  if (need(41)) {
    const started = await member.request.post(`${env.USER_ORIGIN}/api/notebooks`, {data: {database: database.id, namespace: [NAMESPACE], table: TABLE}, headers: {'X-Portal-Request': '1'}});
    if (!started.ok()) throw new Error(`Start notebook: ${started.status()}`);
    await workspace.reload();
    await workspace.locator('#workspace-screen').waitFor();
    await toPage(workspace, 'Notebooks');
    await workspace.locator('#notebook-databases button').filter({hasText: DEMO_DATABASE}).first().click();
    const starter = workspace.frameLocator('#frame-host iframe');
    await starter.locator('.cm-content').first().waitFor({timeout: 180000});
    await starter.locator('.cm-content').first().click();
    await workspace.keyboard.press('Control+Shift+r');
    await starter.getByRole('heading', {name: 'Available objects'}).first().waitFor({timeout: 180000});
    await expect(starter.locator('[data-status="queued"], [data-status="running"]')).toHaveCount(0, {timeout: 180000});
    await toHeading(starter, 'Available objects');
    await shot(workspace, '41-user-notebook-grid');
    await workspace.locator('#close-notebook').click();
    await workspace.locator('#editor').waitFor({state: 'hidden'});
    await toPage(workspace, 'Catalog');
  }

  // Every route of the user portal, callable in the browser with the same session.
  if (need(45)) {
    await workspace.goto(env.USER_ORIGIN + '/docs');
    await workspace.locator('.opblock').first().waitFor({timeout: 60000});
    await shot(workspace, '45-user-api-docs');
  }

  // Getting started: notebook, your own tools or an AI agent over MCP.
  if (need(48)) {
    await workspace.goto(env.USER_ORIGIN);
    await workspace.locator('#guide-nav').click();
    await workspace.locator('#guide .guide-track').first().waitFor();
    await shot(workspace, '48-user-guide');
  }

  // The same tables in the catalog browser, without a notebook.
  await openNamespace(workspace, NAMESPACE);
  await shot(workspace, '15-user-catalog-namespace');
  await openTable(workspace, NAMESPACE, TABLE);
  await shot(workspace, '16-user-table-overview');
  await openTab(workspace, 'Schema');
  await shot(workspace, '17-user-table-schema');
  await openTab(workspace, 'Snapshots');
  await shot(workspace, '18-user-table-snapshots');
  await workspace.getByRole('button', {name: 'Preview snapshot', exact: true}).last().click();
  await loadPreview(workspace);
  await shot(workspace, '19-user-table-preview');

  // The meaning next to the tables: the semantic model the skill stored in Polaris.
  if (need(43, 44, 49, 51, 52)) {
    await openNamespace(workspace, NAMESPACE);
    await openModel(workspace, MODEL);
    await shot(workspace, '43-user-model-overview');
    await showDiagram(workspace);
    await shot(workspace, '44-user-model-diagram');
    // Hovering a dataset follows its relationships; the promo video fades between these states.
    const dataset = name => workspace.locator('.sd-box').filter({has: workspace.locator('.sd-name').getByText(name, {exact: true})});
    await dataset('FEEDER_LOAD').hover();
    await shot(workspace, '51-user-model-hover-feeder-load');
    await dataset('SUBSTATION').hover();
    await shot(workspace, '52-user-model-hover-substation');
    await workspace.mouse.move(0, 0);
    await openTab(workspace, 'Metrics & relationships');
    await expect(workspace.getByRole('tabpanel')).toContainText('overload_hours');
    await shot(workspace, '49-user-model-metrics');
  }

  // The deck's demo: example notebook 09 writes the MV load forecast with Polars 2 and stores its
  // semantic model. 63 and 66 share a slide, so they are near-square halves like 58 and 59.
  if (need(63, 66, 67, 68, 69)) {
    const half = await browser.newContext({viewport: {width: 1600, height: 1620}, deviceScaleFactor: 2.5, colorScheme: 'dark', storageState: await member.storageState()});
    const polars = await half.newPage();
    polars.setDefaultTimeout(30000);
    await polars.goto(env.USER_ORIGIN);
    await polars.locator('#workspace-screen').waitFor();
    await toPage(polars, 'Notebooks');
    await polars.locator('#notebook-databases button').filter({hasText: DEMO_DATABASE}).first().click();
    const notebook = await runNotebook(polars, POLARS.notebook, 'Ready to analyse');
    await toHeading(notebook, '3. Derive the congestion outlook with expressions');
    await shot(polars, '63-user-notebook-polars');
    await toHeading(notebook, '4. Turn the contract into quality evidence');
    await shot(polars, '66-user-notebook-polars-quality');
    await polars.locator('#close-notebook').click();
    await polars.locator('#editor').waitFor({state: 'hidden'});
    await half.close();
    await openNamespace(workspace, POLARS.namespace);
    await openModel(workspace, POLARS.model);
    await shot(workspace, '68-user-model-overview-polars');
    await showDiagram(workspace);
    await shot(workspace, '67-user-model-diagram-polars');
    await openTab(workspace, 'Metrics & relationships');
    await expect(workspace.getByRole('tabpanel')).toContainText('congestion_points');
    await shot(workspace, '69-user-model-metrics-polars');
  }

  await signOut(member);

  // A team administrator shares the semantic model with an external party, without a portal administrator.
  if (need(32, 33, 34, 35, 37, 38, 39, 40, 50)) {
    await sharing.goto(env.USER_ORIGIN);
    await sharing.locator('#workspace-screen').waitFor();
    await toPage(sharing, 'Data shares');
    const shareDatabase = sharing.locator('.database-shares').filter({has: sharing.getByRole('heading', {name: DEMO_DATABASE, exact: true})});
    await shareDatabase.getByRole('button', {name: 'New data share', exact: true}).waitFor();
    const existing = (await (await owner.request.get(`${env.USER_ORIGIN}/api/shares?database=${database.id}`)).json()).shares.find(s => s.name === SHARE.name);
    await shareDatabase.getByRole('button', {name: 'New data share', exact: true}).click();
    await sharing.getByLabel('Share name').fill(SHARE.name);
    await sharing.getByLabel('Recipient', {exact: true}).fill(SHARE.recipient);
    await sharing.getByLabel('Description').fill(SHARE.description);
    await sharing.getByLabel(/^Expires/).fill(`${new Date().getUTCFullYear() + 1}-12-31`);
    await pickModel(sharing);
    // Keep the database name above the share form in the capture.
    await sharing.locator('.database-shares').filter({hasText: DEMO_DATABASE}).first().evaluate(s => s.scrollIntoView({block: 'start'}));
    await shot(sharing, '32-user-share-form');
    // The pictured secret must not outlive the capture: it is replaced once more at the end.
    if (existing) { await sharing.getByRole('button', {name: 'Cancel', exact: true}).click(); await shareDatabase.getByRole('button', {name: 'New secret', exact: true}).first().click(); }
    else await sharing.getByRole('button', {name: 'Create share and show credential', exact: true}).click();
    await sharing.locator('.share-issued').waitFor();
    // The shared names, with the address a recipient loads the model from.
    await sharing.locator('.share-issued').getByRole('heading', {name: 'Shared names', exact: true}).evaluate(h => h.scrollIntoView({block: 'center'}));
    await shot(sharing, '33-user-share-credential');
    await sharing.getByRole('button', {name: 'Done, I stored the secret', exact: true}).click();
    await shareDatabase.getByRole('region', {name: 'Data shares', exact: true}).waitFor();

    // The same model for another team: its members read it with their own accounts, no credential.
    const shares = (await (await owner.request.get(`${env.USER_ORIGIN}/api/shares?database=${database.id}`)).json()).shares;
    await fillTeamShare(sharing, shareDatabase, TEAM_SHARE);
    await shot(sharing, '37-user-share-team-form');
    if (shares.some(s => s.name === TEAM_SHARE.name)) await sharing.getByRole('button', {name: 'Cancel', exact: true}).click();
    else await sharing.getByRole('button', {name: 'Create share', exact: true}).click();
    await shareDatabase.getByText(TEAM_SHARE.name, {exact: true}).waitFor();
    await sharing.locator('#shares-page').evaluate(h => h.scrollIntoView({block: 'start'}));
    await shot(sharing, '34-user-share-list');
    const share = (await (await owner.request.get(`${env.USER_ORIGIN}/api/shares?database=${database.id}`)).json()).shares.find(s => s.name === SHARE.name);
    const renewed = await owner.request.post(`${env.USER_ORIGIN}/api/shares/${share.id}/rotate`, {data: {}, headers: {'X-Portal-Request': '1'}});
    if (!renewed.ok()) throw new Error(`The pictured secret was not replaced: ${renewed.status()}`);

    // noor's team owns no database in development, yet reads the shared model and its tables there.
    const recipient = await newContext(), received = await recipient.newPage();
    received.setDefaultTimeout(30000);
    await signIn(received, env.USER_ORIGIN, RECIPIENT, state.users[RECIPIENT], chosen => { state.users[RECIPIENT] = chosen; });
    await received.locator('#workspace-screen').waitFor();
    if (await received.locator('#environment').inputValue() !== 'development') await received.locator('#environment').selectOption('development');
    await toPage(received, 'Databases');
    await received.locator('#received-databases').getByText(DEMO_DATABASE).first().waitFor();
    await shot(received, '38-user-received-databases');
    await toPage(received, 'Catalog');
    await received.locator('#databases button').filter({hasText: DEMO_DATABASE}).first().click();
    await received.locator('#catalog-database-context').waitFor();
    await received.locator('.object-row').filter({hasText: NAMESPACE}).getByRole('button').click();
    await objectRow(received, MODEL, 'Semantic model').waitFor();
    await shot(received, '39-user-received-catalog');
    if (need(50)) {
      await openModel(received, MODEL);
      await showDiagram(received);
      await shot(received, '50-user-received-model-diagram');
    }
    if (need(40)) {
      await toPage(received, 'Notebooks');
      await received.locator('#notebook-databases button').filter({hasText: DEMO_DATABASE}).first().click();
      const starter = received.frameLocator('#frame-host iframe');
      await starter.locator('.cm-content').first().waitFor({timeout: 180000});
      await starter.locator('.cm-content').first().click();
      await received.keyboard.press('Control+Shift+r');
      await starter.getByRole('heading', {name: 'Available objects'}).first().waitFor({timeout: 180000});
      await expect(starter.locator('[data-status="queued"], [data-status="running"]')).toHaveCount(0, {timeout: 180000});
      await toHeading(starter, 'Available objects');
      await shot(received, '40-user-received-notebook');
    }
    await signOut(recipient);
    await recipient.close();
  }
  // mila shares the Polars model with outage-response; noor sees only the shared objects, read-only.
  if (need(70, 71)) {
    await sharing.goto(env.USER_ORIGIN);
    await sharing.locator('#workspace-screen').waitFor();
    await toPage(sharing, 'Data shares');
    const shareDatabase = sharing.locator('.database-shares').filter({has: sharing.getByRole('heading', {name: DEMO_DATABASE, exact: true})});
    await shareDatabase.getByRole('button', {name: 'New data share', exact: true}).waitFor();
    const shares = (await (await owner.request.get(`${env.USER_ORIGIN}/api/shares?database=${database.id}`)).json()).shares;
    await fillTeamShare(sharing, shareDatabase, POLARS_SHARE, POLARS.namespace, POLARS.model, POLARS.tables);
    await shot(sharing, '70-user-share-team-form-polars');
    if (shares.some(s => s.name === POLARS_SHARE.name)) await sharing.getByRole('button', {name: 'Cancel', exact: true}).click();
    else await sharing.getByRole('button', {name: 'Create share', exact: true}).click();
    await shareDatabase.getByText(POLARS_SHARE.name, {exact: true}).waitFor();

    const recipient = await newContext(), received = await recipient.newPage();
    received.setDefaultTimeout(30000);
    await signIn(received, env.USER_ORIGIN, RECIPIENT, state.users[RECIPIENT], chosen => { state.users[RECIPIENT] = chosen; });
    await received.locator('#workspace-screen').waitFor();
    if (await received.locator('#environment').inputValue() !== 'development') await received.locator('#environment').selectOption('development');
    await toPage(received, 'Databases');
    await received.locator('#received-databases').getByText(DEMO_DATABASE).first().waitFor();
    await toPage(received, 'Catalog');
    await received.locator('#databases button').filter({hasText: DEMO_DATABASE}).first().click();
    await received.locator('#catalog-database-context').waitFor();
    await received.locator('.object-row').filter({hasText: POLARS.namespace}).getByRole('button').click();
    await objectRow(received, POLARS.model, 'Semantic model').waitFor();
    await shot(received, '71-user-received-catalog-polars');
    await signOut(recipient);
    await recipient.close();
  }
  // mila lists the model in the data catalog: metadata only. The OpenMetadata extension publishes it
  // as a data product with its tables, glossary terms, metrics and an ER diagram.
  if (need(58, 59, 60, 61, 62)) {
    // 58 and 59 share a slide: each fills a near-square half, so they are 4000×4050 from a 1600 px viewport.
    const half = await browser.newContext({viewport: {width: 1600, height: 1620}, deviceScaleFactor: 2.5, colorScheme: 'dark', storageState: await owner.storageState()});
    const form = await half.newPage();
    const shareDatabase = await fillListing(form, LISTING);
    await shareDatabase.evaluate(s => s.scrollIntoView({block: 'start'}));
    await shot(form, '58-user-share-listed-form');
    const product = await createListing(form, shareDatabase, owner, database, LISTING);

    const dataProduct = await half.newPage();
    await omSignIn(dataProduct);
    await openDataProduct(dataProduct, product, LISTING);
    // Owner, contact and model at the top, the ER diagram at the bottom: scroll just past the banner.
    await dataProduct.locator('img[alt^="Tables and keys"]').first().evaluate(diagram => {
      diagram.scrollIntoView({block: 'end'});
      let box = diagram.parentElement;
      while (box && box.scrollTop === 0) box = box.parentElement;
      (box || document.scrollingElement).scrollBy(0, 40);
    });
    await shot(dataProduct, '59-om-data-product');
    await half.close();

    const catalog = await newContext(), om = await catalog.newPage();
    await omSignIn(om);
    await openDataProduct(om, product, LISTING);
    await om.getByRole('heading', {name: 'Tables and keys'}).first().evaluate(h => h.scrollIntoView({block: 'start'}));
    await shot(om, '60-om-data-product-diagram');
    await om.goto(`${OM_ORIGIN}/table/iceberg-development.${database.id}.${NAMESPACE}.${TABLE}`);
    await om.getByRole('cell', {name: 'utilisation_pct'}).first().waitFor();
    await shot(om, '61-om-table');
    await om.goto(`${OM_ORIGIN}/metric/iceberg__${database.id}__${NAMESPACE}__${MODEL}__overload_hours`);
    await om.getByText('overload_hours').first().waitFor();
    await om.waitForLoadState('networkidle').catch(() => {});
    await shot(om, '62-om-metric');
    await catalog.close();
  }
  // The Polars model in the data catalog: 72 and 73 share a slide as near-square halves.
  if (need(72, 73, 74, 75)) {
    const half = await browser.newContext({viewport: {width: 1600, height: 1620}, deviceScaleFactor: 2.5, colorScheme: 'dark', storageState: await owner.storageState()});
    const form = await half.newPage();
    const shareDatabase = await fillListing(form, POLARS_LISTING, POLARS.namespace, POLARS.model, POLARS.tables);
    await shareDatabase.evaluate(s => s.scrollIntoView({block: 'start'}));
    await shot(form, '72-user-share-listed-form-polars');
    const product = await createListing(form, shareDatabase, owner, database, POLARS_LISTING);
    const dataProduct = await half.newPage();
    await omSignIn(dataProduct);
    await openDataProduct(dataProduct, product, POLARS_LISTING);
    // The ER diagram of the listed model, drawn from its keys and relationships.
    await dataProduct.getByRole('heading', {name: 'Tables and keys'}).first().evaluate(h => h.scrollIntoView({block: 'start'}));
    await shot(dataProduct, '73-om-data-product-polars');
    await half.close();

    const catalog = await newContext(), om = await catalog.newPage();
    await omSignIn(om);
    await om.goto(`${OM_ORIGIN}/table/iceberg-development.${database.id}.${POLARS.namespace}.load_forecast`);
    await om.getByRole('cell', {name: 'utilization_pct'}).first().waitFor();
    await shot(om, '74-om-table-polars');
    await om.goto(`${OM_ORIGIN}/metric/iceberg__${database.id}__${POLARS.namespace}__${POLARS.model}__congestion_points`);
    await om.getByText('congestion_points').first().waitFor();
    await om.waitForLoadState('networkidle').catch(() => {});
    await shot(om, '75-om-metric-polars');
    await catalog.close();
  }
  // Conversational BI, the first extension. Each team administrator enables it once per environment;
  // noor then asks about the model grid-planning shared with outage-response.
  if (need(54, 55, 56)) {
    const chat = await owner.newPage();
    await cbiSignIn(chat);
    await chat.locator('nav.models .not-enabled').filter({hasText: `${DEMO_TEAM} · development`}).waitFor().catch(() => {});
    await shot(chat, '54-cbi-enable');
    await enableCbi(chat, DEMO_TEAM);
    await chat.locator('nav.models button.db').filter({hasText: MODEL}).first().waitFor();
    await chat.close();

    const asker = await newContext(), bi = await asker.newPage();
    bi.setDefaultTimeout(30000);
    await cbiSignIn(bi, RECIPIENT);
    await enableCbi(bi, TEAM_SHARE.team);
    await bi.locator('nav.models .database-group', {hasText: 'Shared with your team'}).locator('button.db', {hasText: MODEL}).first().click();
    await bi.locator('h1', {hasText: MODEL}).waitFor();
    const input = bi.locator('[data-copilotkit] textarea').first();
    await input.fill(CBI_QUESTION);
    await input.press('Enter');
    await bi.locator('.query-card').first().waitFor({timeout: 120000});
    await bi.locator('.gen-card canvas[role=img]').first().waitFor({timeout: 120000});
    // Chart labels are drawn on the canvas, so a feeder name in the page text means the written answer has arrived.
    await bi.getByText(/Dockside-4 Church/).first().waitFor({timeout: 120000});
    await bi.waitForTimeout(3000);
    await shot(bi, '55-cbi-answer');
    await bi.locator('.query-card summary', {hasText: 'SQL'}).first().click();
    await bi.locator('.query-card').first().evaluate(card => card.scrollIntoView({block: 'start'}));
    await shot(bi, '56-cbi-query');
    await asker.close();
  }
  // noor asks the Polars model grid-planning shared with outage-response.
  if (need(76, 77)) {
    const asker = await newContext(), bi = await asker.newPage();
    bi.setDefaultTimeout(30000);
    await cbiSignIn(bi, RECIPIENT);
    await enableCbi(bi, POLARS_SHARE.team);
    await bi.locator('nav.models .database-group', {hasText: 'Shared with your team'}).locator('button.db', {hasText: POLARS.model}).first().click();
    await bi.locator('h1', {hasText: POLARS.model}).waitFor();
    const input = bi.locator('[data-copilotkit] textarea').first();
    await input.fill(POLARS_QUESTION);
    await input.press('Enter');
    await bi.locator('.query-card').first().waitFor({timeout: 120000});
    await bi.locator('.gen-card canvas[role=img]').first().waitFor({timeout: 120000}).catch(() => {});
    // The written answer names the substation with the most congestion points.
    await bi.getByText(/Dunmore|SS-DUN/).first().waitFor({timeout: 120000});
    await bi.waitForTimeout(3000);
    await shot(bi, '76-cbi-answer-polars');
    await bi.locator('.query-card summary', {hasText: 'SQL'}).first().click();
    // The chat keeps to the bottom while the expanded SQL grows; scroll up once it has settled.
    await bi.waitForTimeout(1500);
    await bi.locator('.query-card').first().evaluate(card => card.scrollIntoView({block: 'start'}));
    await shot(bi, '77-cbi-query-polars');
    await asker.close();
  }
  await owner.close();

  await portal.goto(env.PORTAL_ORIGIN);
  for (const [page, name] of [['databases', '02-admin-databases'], ['users', '03-admin-users'], ['teams', '04-admin-teams'], ['shares', '35-admin-shares'], ['infrastructure', '05-admin-infrastructure']]) {
    await portal.locator(`[data-page="${page}"]`).first().click();
    await portal.waitForLoadState('networkidle');
    await shot(portal, name);
  }
  if (need(57)) {
    await portal.locator('[data-page="teams"]').first().click();
    const accounts = portal.locator('section.panel').filter({has: portal.getByRole('heading', {name: /Extension service accounts/})});
    await accounts.waitFor();
    await accounts.evaluate(panel => panel.scrollIntoView({block: 'center'}));
    await shot(portal, '57-admin-extension-accounts');
  }
  await portal.locator('[data-page="explorer"]').first().click();
  for (const node of [DEMO_DATABASE, NAMESPACE]) await portal.locator('details > summary').filter({hasText: node}).first().click();
  await portal.locator('details').filter({hasText: 'substation'}).first().waitFor();
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
  // Creating a user: a Keycloak account plus a role per team. Filled in, never submitted.
  if (need(46)) {
    await access.keyboard.press('Escape');
    await access.locator('#create').click();
    const create = access.getByRole('dialog');
    await create.locator('[name=name]').fill('jonas');
    await create.locator('[name=first_name]').fill('Jonas');
    await create.locator('[name=last_name]').fill('Example');
    await create.locator('[name=email]').fill('jonas@example.com');
    const row = create.locator('.team-role').filter({hasText: TEAM_SHARE.team});
    await row.locator('[name=teams]').check();
    await row.locator('select').selectOption('writer');
    await shot(access, '46-admin-user-create');
  }
  await close.close();
  // Getting started in the administration portal: portal, AI agent or API.
  if (need(47)) {
    await portal.locator('[data-page="guide"]').first().click();
    await portal.locator('.guide-track').first().waitFor();
    await shot(portal, '47-admin-guide');
  }

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
  await folder(`${NAMESPACE}/${TABLE}/metadata/`);
  await shot(rustfs, '21-rustfs-metadata-files');
  await rustfs.getByRole('row').filter({hasText: '.metadata.json'}).last().getByRole('button', {name: 'Preview', exact: true}).click();
  await rustfs.getByRole('dialog').getByText('"format-version"').waitFor();
  await rustfs.getByRole('dialog').getByRole('button').first().click(); // maximize
  await shot(rustfs, '22-rustfs-metadata-json');
  await storage.close();
  console.log('PASS');
} finally {
  await browser.close();
}
