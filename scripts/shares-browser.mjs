// Browser checks of the data share panel against deterministic API fixtures; no running stack required.
import { chromium, expect } from '@playwright/test';
import { readFile, mkdir } from 'node:fs/promises';

const database = {id: 'db-' + 'a'.repeat(32), name: 'Energy', team: 'analytics', environment: 'development'};
const recipient = '<img src=x onerror=alert(1)> Partner BV';
let role = 'reader', shares = [], calls = [];
const issued = (share, secret) => ({share, credentials: {clientId: share.clientId, clientSecret: secret}, connection: {type: 'iceberg-rest', uri: 'https://catalog.example/api/catalog', warehouse: database.id, oauth2ServerUri: 'https://catalog.example/api/catalog/v1/oauth/tokens', scope: 'PRINCIPAL_ROLE:ALL', accessDelegation: 'vended-credentials', s3Endpoint: 'https://s3.example', credential: `${share.clientId}:${secret}`, identifiers: share.objects.map(o => ({kind: o.kind, identifier: [...o.namespace, o.name].join('.')}))}});
const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined});
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 1050}});
  // The fixture origin is not a secure context, so the page has no clipboard of its own.
  await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => { window.copied = text; }}}));
  const errors = []; page.on('pageerror', error => errors.push(error.message)); page.on('dialog', dialog => { errors.push('dialog: ' + dialog.message()); dialog.dismiss(); });
  await page.route('http://shares.test/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    let body, status = 200;
    if (path === '/api/session') body = {authenticated: true};
    else if (path === '/api/workspace') body = {user: {name: 'Team admin'}, teams: [{id: 'analytics', name: 'Energy analytics', role}], databases: [database], activeTeam: 'analytics', activeRole: role, activeEnvironment: 'development', environments: ['development', 'acceptance', 'production'], notebooks: []};
    else if (path === '/api/contents') body = url.searchParams.has('namespace') ? {namespaces: [], tables: [{name: 'readings', namespace: ['analytics']}], views: [{name: 'daily_energy', namespace: ['analytics']}]} : {namespaces: [['analytics']], tables: [], views: []};
    else if (path === '/api/details') body = {kind: 'database', database, catalogUri: 'https://catalog.example/api/catalog'};
    else if (path === '/api/shares' && method === 'GET') body = {shares, limits: {shares: 20, objects: 50}};
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
  await expect(page.getByText('Data shares', {exact: true})).toHaveCount(0);

  role = 'admin';
  await page.getByRole('button', {name: 'Refresh catalog', exact: true}).click();
  await page.getByText('Data shares', {exact: true}).click();
  await expect(page.locator('.shares')).toContainText('Nothing in this database is shared.');
  await page.getByRole('button', {name: 'New data share', exact: true}).click();
  await page.getByLabel('Share name').fill('partner');
  await page.getByLabel('Recipient').fill(recipient);
  await page.locator('.share-tree summary').filter({hasText: 'analytics'}).click();
  await expect(page.locator('.share-warning')).toBeHidden();
  await page.getByLabel('daily_energy').check();
  await expect(page.locator('.share-warning')).toContainText('not a row or column filter');
  await page.getByRole('button', {name: 'Create share and show credential', exact: true}).click();
  await expect(page.locator('#notice')).toContainText('Select the tables a shared view reads.');
  if (calls.length) throw new Error('A view-only share reached the API');
  await page.getByLabel('readings').check();
  await expect(page.locator('.share-selection')).toContainText('SELECTED · 2');
  await page.screenshot({path: 'test-results/shares/form-dark.png', fullPage: true});
  await page.getByRole('button', {name: 'Create share and show credential', exact: true}).click();

  const panel = page.locator('.share-issued');
  await expect(panel).toContainText('first-secret');
  await expect(panel).toContainText('shown once');
  await expect(panel.locator('.view-sql')).toContainText('catalog.load_table("analytics.readings")');
  await expect(panel.locator('.view-sql')).toContainText('"header.X-Iceberg-Access-Delegation": "vended-credentials"');
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
  if (errors.length) throw new Error(errors.join('\n'));
  console.log('PASS: data shares hidden from non-administrators, view warning and table requirement, one-time credential and snippet, escaping, drift hints, edit, new secret, confirmed revoke, light/mobile layouts');
} finally { await browser.close(); }
