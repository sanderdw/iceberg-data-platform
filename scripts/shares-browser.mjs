// Browser checks of the data share panel against deterministic API fixtures; no running stack required.
import { chromium, expect } from '@playwright/test';
import { readFile, mkdir } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';

const database = {id: 'db-' + 'a'.repeat(32), name: 'Energy', team: 'analytics', environment: 'development'};
const recipient = '<img src=x onerror=alert(1)> Partner BV';
let role = 'reader', shares = [], calls = [], notebooks = [], environment = 'development', objectLimit = 50;
const workspace = () => ({user: {name: 'Team member'}, teams: [{id: 'analytics', name: 'Energy analytics', role}], databases: environment === 'development' ? [database] : [], activeTeam: 'analytics', activeRole: role, activeEnvironment: environment, environments: ['development', 'acceptance', 'production'], notebooks});
const issued = (share, secret) => ({share, credentials: {clientId: share.clientId, clientSecret: secret}, connection: {type: 'iceberg-rest', uri: 'https://catalog.example/api/catalog', warehouse: database.id, oauth2ServerUri: 'https://catalog.example/api/catalog/v1/oauth/tokens', scope: 'PRINCIPAL_ROLE:ALL', accessDelegation: 'vended-credentials', s3Endpoint: 'https://s3.example', credential: `${share.clientId}:${secret}`, identifiers: share.objects.map(o => ({kind: o.kind, identifier: [...o.namespace, o.name].join('.')}))}});
const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined});
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 1050}});
  // The fixture origin is not a secure context, so the page has no clipboard of its own.
  await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => { window.copied = text; }}}));
  const errors = []; page.on('pageerror', error => errors.push(error.message)); page.on('dialog', dialog => { errors.push('dialog: ' + dialog.message()); dialog.dismiss(); });
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.route('http://shares.test/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    let body, status = 200;
    if (path === '/api/session') body = {authenticated: true};
    else if (path === '/api/workspace') body = workspace();
    else if (path === '/api/environment') { environment = request.postDataJSON().environment; notebooks = []; body = workspace(); }
    else if (path === '/api/notebooks' && method === 'POST') {
      body = {id: 'notebook-1', database: database.id, environment, url: '/notebook-fixture', filesUrl: '/notebook-fixture'}; notebooks = [body];
    } else if (path === '/api/notebooks/notebook-1' && method === 'DELETE') { notebooks = []; body = {deleted: true}; }
    else if (path === '/notebook-fixture') return route.fulfill({contentType: 'text/html', body: '<p>Notebook fixture</p>'});
    else if (path === '/api/contents') body = url.searchParams.has('namespace') ? {namespaces: [], tables: [{name: 'readings', namespace: ['analytics']}], views: [{name: 'daily_energy', namespace: ['analytics']}]} : {namespaces: [['analytics']], tables: [], views: []};
    else if (path === '/api/details') body = {kind: 'database', database, catalogUri: 'https://catalog.example/api/catalog'};
    else if (path === '/api/shares' && method === 'GET') body = {shares, limits: {shares: 20, objects: objectLimit}};
    else if (path.startsWith('/api/shares')) {
      if (request.headers()['x-portal-request'] !== '1') throw new Error('Mutation without the CSRF header');
      const input = request.postDataJSON(); calls.push({method, path, input});
      if (method === 'POST' && path === '/api/shares') {
        const share = {id: 'share-' + 'b'.repeat(32), clientId: 'client-1', status: 'active', createdAt: 1789506000000, createdBy: 'Team admin', ...input, objects: input.objects.map(o => ({...o, granted: o.kind === 'table'})), extraGrants: [{kind: 'table', namespace: ['analytics'], name: 'renamed', privilege: 'TABLE_READ_DATA'}]};
        shares = [share]; body = issued(share, 'first-secret'); status = 201;
      } else if (method === 'POST') body = issued(shares[0], 'second-secret');
      else if (method === 'PATCH') { shares = [{...shares[0], ...input, objects: input.objects.map(o => ({...o, granted: true})), extraGrants: []}]; body = {share: shares[0]}; }
      else { shares = []; body = {deleted: true}; }
    }
    if (body) return route.fulfill({status, json: body});
    const asset = path === '/' ? 'user_portal/public/index.html' : path.startsWith('/fonts/') || path === '/favicon.svg' ? `public${path}` : `user_portal/public${path}`;
    const mime = path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : path.endsWith('.ttf') ? 'font/ttf' : path.endsWith('.svg') ? 'image/svg+xml' : 'text/html';
    await route.fulfill({body: await readFile(asset), contentType: mime});
  });
  await mkdir('test-results/shares', {recursive: true});
  await page.goto('http://shares.test');
  await page.locator('#databases button').click();
  await expect(page.locator('.catalog-summary')).toContainText('Energy analytics');
  await page.getByRole('button', {name: 'Data shares', exact: true}).click();
  await expect(page.locator('#share-permissions')).toBeVisible();
  await expect(page.getByRole('button', {name: 'New data share', exact: true})).toBeDisabled();
  const sharesUrl = page.url();
  await page.reload();
  await expect(page.locator('#shares-page')).toBeVisible();
  expect(page.url()).toBe(sharesUrl);
  await page.goBack();
  await expect(page.locator('#catalog-page')).toBeVisible();
  await expect(page.locator('.catalog-summary')).toContainText('Energy analytics');
  await page.goForward();
  await expect(page.locator('#shares-page')).toBeVisible();
  await page.getByRole('button', {name: 'Notebooks', exact: true}).click();
  await expect(page.locator('#notebook-empty')).toBeVisible();
  await expect(page.locator('#notebook-databases')).toContainText('Energy');
  await page.locator('#notebook-databases button').click();
  await expect(page.locator('#editor')).toBeVisible();
  await page.locator('#frame-host iframe').evaluate(frame => { frame.dataset.preserved = 'yes'; });
  await page.getByRole('navigation', {name: 'Workspace', exact: true}).getByRole('button', {name: 'Catalog', exact: true}).click();
  await expect(page.locator('.catalog-summary')).toContainText('Energy analytics');

  await page.getByRole('button', {name: 'Notebooks', exact: true}).click();
  await page.locator('#notebooks button').first().click();
  await expect(page.locator('#editor')).toBeVisible();
  await expect(page.locator('#frame-host iframe')).toHaveAttribute('data-preserved', 'yes');
  await page.evaluate(() => refreshWorkspace());
  await expect(page.locator('#frame-host iframe')).toHaveAttribute('data-preserved', 'yes');
  await page.reload();
  await expect(page.locator('#editor')).toBeVisible();
  expect(notebooks).toHaveLength(1);
  await page.getByRole('button', {name: 'Stop my session', exact: true}).click();
  await expect(page.locator('#notebook-empty')).toBeVisible();
  await expect(page.locator('#frame-host iframe')).toHaveCount(0);
  await page.locator('#workspace-nav [data-page=catalog]').click();

  role = 'admin';
  await page.getByRole('button', {name: 'Refresh catalog', exact: true}).click();
  await page.getByRole('button', {name: 'Data shares', exact: true}).click();
  await expect(page.locator('.shares')).toContainText('Nothing in this database is shared.');
  await page.getByRole('button', {name: 'New data share', exact: true}).click();
  await page.getByLabel('Share name').fill('partner');
  await page.getByLabel('Recipient').fill(recipient);
  await page.evaluate(() => { document.activeElement.blur(); return refreshWorkspace(); });
  await expect(page.getByLabel('Recipient')).toHaveValue(recipient);
  await page.locator('.share-tree summary').filter({hasText: 'analytics'}).click();
  await expect(page.locator('.share-warning')).toBeHidden();
  await page.getByLabel('daily_energy').check();
  await expect(page.locator('.share-warning')).toContainText('not a row or column filter');
  await page.getByRole('button', {name: 'Create share and show credential', exact: true}).click();
  await expect(page.locator('#notice')).toContainText('Select the tables a shared view reads.');
  if (calls.length) throw new Error('A view-only share reached the API');
  await page.getByLabel('readings').check();
  await expect(page.locator('.share-selection')).toContainText('SELECTED · 2');
  await page.getByLabel('Share name').fill('Sensor Events');
  await page.getByRole('button', {name: 'Create share and show credential', exact: true}).click();
  expect(await page.getByLabel('Share name').evaluate(input => input.validity.patternMismatch)).toBe(true);
  if (calls.length) throw new Error('An invalid share name reached the API');
  await expect(page.locator('.share-form')).toContainText('starting with a letter');
  for (const name of ['sensor-events', 'sensor_events', 'partner']) {
    await page.getByLabel('Share name').fill(name);
    expect(await page.getByLabel('Share name').evaluate(input => input.checkValidity())).toBe(true);
  }
  const expiryInput = page.getByLabel('Expires at the end of this UTC day (optional)');
  for (const date of ['', new Date().toISOString().slice(0, 10), '2031-05-01']) {
    await expiryInput.fill(date);
    expect(await expiryInput.evaluate(input => input.checkValidity())).toBe(true);
  }
  await expiryInput.fill('2020-01-01');
  expect(await expiryInput.evaluate(input => input.validity.rangeUnderflow)).toBe(true);
  await expiryInput.fill('');
  for (const [label, max] of [['Recipient', 120], ['Description', 280]]) {
    const input = page.getByLabel(label), original = await input.inputValue();
    await input.fill(''); await input.focus(); await page.keyboard.insertText('x'.repeat(max + 1));
    expect((await input.inputValue()).length).toBe(max);
    await input.fill(original);
  }
  await page.screenshot({path: 'test-results/shares/form-dark.png', fullPage: true});
  await page.getByRole('button', {name: 'Create share and show credential', exact: true}).click();

  const panel = page.locator('.share-issued');
  await expect(panel).toContainText('first-secret');
  await expect(panel).toContainText('shown once');
  await page.evaluate(() => refreshWorkspace());
  await expect(panel).toContainText('first-secret');
  await expect(panel.locator('pre, code, .view-sql')).toHaveCount(0);
  await expect(panel).not.toContainText('import duckdb');
  await page.getByRole('button', {name: 'Copy DuckDB snippet', exact: true}).click();
  const snippet = await page.evaluate(() => window.copied);
  expect(snippet).toContain('uv run read_share_duckdb.py');
  expect(snippet).toContain("ACCESS_DELEGATION_MODE 'vended_credentials'");
  expect(snippet).toContain("CLIENT_SECRET 'first-secret'");
  expect(snippet).toContain('# Table: "analytics.readings"');
  expect(snippet).toContain('# View: "analytics.daily_energy"');
  // Execute generated Python against a stub to check syntax and the actual SQL values,
  // including both layers of quoting, without installing extensions or contacting a catalog.
  const unusualSnippet = await page.evaluate(() => duckdbSnippet(
    {objects: [{kind: 'table', namespace: ['sales', 'eu'], name: 'order"items'}]},
    {clientId: 'client-1', clientSecret: `quote'"""\\secret`},
    {uri: 'https://catalog.example/api/catalog', warehouse: 'warehouse', oauth2ServerUri: 'https://catalog.example/token', scope: 'PRINCIPAL_ROLE:ALL'},
  ));
  const sql = JSON.parse(execFileSync('python3', ['-c', `
import json, sys, types
outputs = []
for script in json.load(sys.stdin):
    statements = []
    class Connection:
        def execute(self, sql): statements.append(sql)
        def sql(self, sql): statements.append(sql); return self
        def show(self): statements.append("SHOW")
    sys.modules["duckdb"] = types.SimpleNamespace(connect=Connection)
    exec(compile(script, "read_share_duckdb.py", "exec"), {"__name__": "__main__"})
    outputs.append(statements)
print(json.dumps(outputs))
`], {input: JSON.stringify([snippet, unusualSnippet]), encoding: 'utf8'}));
  if (sql[0][4] !== 'SELECT * FROM "shared"."analytics"."readings"' || sql[0][5] !== 'SHOW') throw new Error('Wrong shared table query');
  if (!sql[0][2].includes("CLIENT_ID 'client-1'") || !sql[0][3].includes(`ATTACH '${database.id}'`)) throw new Error('Wrong connection values');
  if (!sql[1][2].includes(`CLIENT_SECRET 'quote''"""\\secret'`) || sql[1][4] !== 'SELECT * FROM "shared"."sales.eu"."order""items"') throw new Error('Unsafe SQL escaping');
  const created = calls[0].input;
  if (created.database !== database.id || created.name !== 'partner' || created.expiresAt !== null || created.objects.length !== 2) throw new Error('Unexpected create request: ' + JSON.stringify(created));
  if (JSON.stringify(created.objects.map(o => [o.kind, o.namespace, o.name]).sort()) !== JSON.stringify([['table', ['analytics'], 'readings'], ['view', ['analytics'], 'daily_energy']])) throw new Error('Unexpected objects: ' + JSON.stringify(created.objects));
  await page.getByRole('button', {name: 'Copy credential', exact: true}).click();
  if (await page.evaluate(() => window.copied) !== 'client-1:first-secret') throw new Error('Credential was not copied');
  await page.screenshot({path: 'test-results/shares/credential-dark.png', fullPage: true});
  await page.getByRole('button', {name: 'Done, I stored the secret', exact: true}).click();

  const grid = page.getByRole('region', {name: 'Data shares', exact: true});
  await expect(grid).toContainText(recipient);
  await expect(grid.locator('img')).toHaveCount(0);
  await expect(page.locator('body')).not.toContainText('first-secret');
  await expect(grid).toContainText('Not granted (dropped or recreated): analytics.daily_energy');
  await expect(grid).toContainText('Still granted under a new name: analytics.renamed');
  shares[0].recipient = 'Updated by a teammate';
  await page.evaluate(() => refreshWorkspace());
  await expect(grid).toContainText('Updated by a teammate');
  shares[0].recipient = recipient;
  await page.evaluate(() => refreshWorkspace());
  await expect(grid).toContainText(recipient);
  for (const memberRole of ['reader', 'writer']) {
    role = memberRole;
    await page.getByRole('button', {name: 'Refresh data shares', exact: true}).click();
    await expect(grid).toContainText(recipient);
    await expect(page.locator('#share-permissions')).toBeVisible();
    for (const label of ['New data share', 'Edit', 'New secret', 'Revoke']) {
      const button = page.getByRole('button', {name: label, exact: true});
      await expect(button).toBeDisabled();
      await expect(button).toHaveAttribute('title', 'Only team administrators can manage data shares.');
    }
  }
  await page.getByLabel('Environment', {exact: true}).selectOption('production');
  await expect(page.locator('#team-shares')).toContainText('no databases');
  await expect(page.getByRole('region', {name: 'Data shares', exact: true})).toHaveCount(0);
  await page.getByLabel('Environment', {exact: true}).selectOption('development');
  await expect(grid).toContainText(recipient);
  await page.screenshot({path: 'test-results/shares/member-dark.png', fullPage: true});
  role = 'bucket-admin';
  const today = new Date().toISOString().slice(0, 10);
  shares[0].expiresAt = `${today}T23:59:59Z`;
  await page.getByRole('button', {name: 'Refresh data shares', exact: true}).click();
  await expect(page.locator('#share-permissions')).toBeHidden();
  await grid.getByRole('button', {name: 'Edit', exact: true}).click();
  await page.getByLabel('Recipient').fill('Updated on expiry day');
  await page.getByRole('button', {name: 'Save share', exact: true}).click();
  await expect(grid).toContainText('Updated on expiry day');
  expect(calls.at(-1).input.expiresAt).toBe(`${today}T23:59:59Z`);
  await grid.getByRole('button', {name: 'Edit', exact: true}).click();
  await expect(page.getByLabel('Share name')).toBeDisabled();
  await expect(page.locator('.share-selection')).toContainText('analytics.daily_energy');
  await page.locator('.share-selection .share-object').filter({hasText: 'daily_energy'}).getByRole('button', {name: 'Remove'}).click();
  await page.getByLabel('Expires at the end of this UTC day (optional)').fill('2031-05-01');
  await page.getByRole('button', {name: 'Save share', exact: true}).click();
  await expect(grid).not.toContainText('Not granted');
  await expect(grid).toContainText('2031-05-01 · end of day UTC');
  const edit = calls.at(-1);
  if (edit.method !== 'PATCH' || edit.input.expiresAt !== '2031-05-01T23:59:59Z' || edit.input.objects.length !== 1 || 'name' in edit.input) throw new Error('Unexpected edit request: ' + JSON.stringify(edit));

  await grid.getByRole('button', {name: 'New secret', exact: true}).click();
  await expect(page.locator('.share-issued')).toContainText('second-secret');
  await expect(panel.locator('pre, code, .view-sql')).toHaveCount(0);
  await page.getByRole('button', {name: 'Copy DuckDB snippet', exact: true}).click();
  const rotatedSnippet = await page.evaluate(() => window.copied);
  expect(rotatedSnippet).toContain("CLIENT_SECRET 'second-secret'");
  expect(rotatedSnippet).not.toContain('first-secret');
  await page.getByRole('button', {name: 'Switch to light mode', exact: true}).click();
  await page.setViewportSize({width: 390, height: 844});
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Mobile overflow in the credential panel');
  await page.screenshot({path: 'test-results/shares/credential-mobile.png', fullPage: true});
  await page.getByRole('button', {name: 'Done, I stored the secret', exact: true}).click();
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Mobile overflow in the share list');
  await grid.getByRole('button', {name: 'Revoke', exact: true}).click();
  if (calls.some(c => c.method === 'DELETE')) throw new Error('Revoked without confirmation');
  await grid.getByRole('button', {name: 'Cancel', exact: true}).click();
  await grid.getByRole('button', {name: 'Revoke', exact: true}).click();
  await grid.getByRole('button', {name: 'Revoke partner now', exact: true}).click();
  await expect(page.locator('.shares')).toContainText('Nothing in this database is shared.');
  objectLimit = 1;
  await page.getByRole('button', {name: 'Refresh data shares', exact: true}).click();
  await page.getByRole('button', {name: 'New data share', exact: true}).click();
  await page.getByLabel('Share name').fill('too-many');
  await page.locator('.share-tree summary').filter({hasText: 'analytics'}).click();
  await page.getByLabel('readings').check(); await page.getByLabel('daily_energy').check();
  const previousCalls = calls.length;
  await page.getByRole('button', {name: 'Create share and show credential', exact: true}).click();
  await expect(page.locator('#notice')).toContainText('Select no more than 1 tables and views.');
  expect(calls.length).toBe(previousCalls);
  if (errors.length) throw new Error(errors.join('\n'));
  console.log('PASS: workspace navigation, reader/writer share visibility with disabled admin controls, view warning and table requirement, one-time credential and snippet, escaping, drift hints, edit, new secret, confirmed revoke, light/mobile layouts');
} finally { await browser.close(); }
