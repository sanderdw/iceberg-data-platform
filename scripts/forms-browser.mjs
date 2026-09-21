// Audit native form validation with real browser controls and deterministic API fixtures.
import {chromium, expect} from '@playwright/test';
import {readFile} from 'node:fs/promises';

const team = {id: 'team-' + 'a'.repeat(32), name: 'analytics', description: ''};
const database = {id: 'db-' + 'b'.repeat(32), name: 'warehouse', team: team.id, environment: 'development', status: 'ready'};
const account = {id: 'portal-' + 'c'.repeat(32), name: 'analyst', teams: [team.id], memberships: [{team: team.id, role: 'reader'}], identity: {status: 'linked'}};
const overview = {health: {status: 'online', provider: 'polaris'}, teams: [team], databases: [database], users: [account], shares: []};
const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined});
let signedIn = true, keycloak = true;
const errors = [], mutations = [], lookups = [];
try {
  const page = await browser.newPage();
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.route('http://forms.test/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (path === '/api/session' && request.method() === 'GET') return route.fulfill({json: {authenticated: signedIn, ...(keycloak ? {userManagement: 'keycloak'} : {})}});
    if (path === '/api/overview') return route.fulfill({json: overview});
    if (path === '/api/identity/accounts') { lookups.push(url.searchParams.get('username')); return route.fulfill({json: [{id: 'subject-1', username: 'external.user', enabled: true, linked: false}]}); }
    if (path.startsWith('/api/')) {
      mutations.push({path, method: request.method(), input: request.postDataJSON()});
      const json = path === '/api/identity/users' ? {user: account, identity: {username: 'analyst', temporaryPassword: 'fixture-secret'}} : {};
      return route.fulfill({json});
    }
    const asset = path === '/' ? 'public/index.html' : `public${path}`;
    const contentType = path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : path.endsWith('.ttf') ? 'font/ttf' : path.endsWith('.svg') ? 'image/svg+xml' : 'text/html';
    return route.fulfill({body: await readFile(asset), contentType});
  });
  await page.goto('http://forms.test');
  const submit = () => page.locator('#modal-form [type=submit]').click();
  const close = () => page.getByRole('button', {name: 'Close', exact: true}).click();
  const valid = input => input.evaluate(node => node.checkValidity());
  async function nativeLimit(input, max) {
    await input.fill(''); await input.focus(); await page.keyboard.insertText('x'.repeat(max + 1));
    expect((await input.inputValue()).length).toBe(max);
  }
  async function nameChecks() {
    const name = page.locator('#modal-form [name=name]');
    await expect(name).toHaveAccessibleDescription(/starting with a letter/);
    for (const bad of ['', 'ab', 'Sensor Events', '1sensor', '-sensor', 'sensor.events']) {
      await name.fill(bad); expect(await valid(name)).toBe(false);
      const before = mutations.length; await submit(); expect(mutations.length).toBe(before);
    }
    for (const good of ['sensor-events', 'sensor_events', 'a12', 'a'.repeat(48)]) {
      await name.fill(good); expect(await valid(name)).toBe(true);
    }
    await nativeLimit(name, 48); await name.fill('analyst');
  }
  for (const section of ['Teams', 'Databases', 'Users']) {
    await page.locator(`[data-page=${section.toLowerCase()}]`).click();
    await page.locator('#create').click(); await nameChecks();
    if (section !== 'Users') {
      await nativeLimit(page.locator('[name=description]'), 280);
      await page.locator('[name=description]').fill('');
      expect(await valid(page.locator('[name=description]'))).toBe(true);
    }
    if (section === 'Databases') {
      await expect(page.locator('[name=environment] option')).toHaveText(['Development', 'Acceptance', 'Production']);
      expect(await valid(page.locator('[name=team]'))).toBe(true);
    }
    await close();
  }
  await page.getByRole('button', {name: 'Teams', exact: true}).click();
  await page.locator('[data-edit-team]').first().click(); await nameChecks(); await close();
  await page.locator('[data-page=databases]').click();
  await page.locator('[data-move]').first().click();
  expect(await valid(page.locator('[name=team]'))).toBe(true); await close();
  await page.getByRole('button', {name: 'Users', exact: true}).click();
  await page.locator('#create').click(); await page.locator('[name=name]').fill('analyst');
  for (const field of ['first_name', 'last_name']) {
    const input = page.locator(`[name=${field}]`);
    for (const bad of ['', '   ']) { await input.fill(bad); expect(await valid(input)).toBe(false); }
    for (const good of ['Élodie', "O’Neill", 'Van der Meer']) { await input.fill(good); expect(await valid(input)).toBe(true); }
    await nativeLimit(input, 100);
    await input.fill(field === 'first_name' ? '  Alice  ' : '  Analyst  ');
  }
  const email = page.getByLabel('Email');
  for (const bad of ['', 'alice', 'alice@localhost', 'alice@@example.test', 'alice @example.test']) { await email.fill(bad); expect(await valid(email)).toBe(false); }
  await nativeLimit(email, 254); await email.fill('alice+test@example.test'); expect(await valid(email)).toBe(true);
  await submit(); await expect(page.locator('.form-error')).toContainText('Select at least one team.'); expect(mutations).toHaveLength(0);
  await page.locator('[name=teams]').check(); await expect(page.locator(`[name="role:${team.id}"]`)).toBeEnabled();
  await submit(); await expect(page.locator('#temporary-password')).toBeVisible();
  expect(mutations.at(-1).input).toEqual({name: 'analyst', first_name: 'Alice', last_name: 'Analyst', email: 'alice+test@example.test', memberships: [{team: team.id, role: 'reader'}]});
  await page.getByRole('button', {name: 'Done', exact: true}).click();
  await page.locator('#create').click(); await page.locator('#identity-link').click(); await nameChecks();
  await page.locator('#find-identity').click(); await expect(page.locator('.form-error')).toContainText('Enter the Keycloak username.'); expect(lookups).toHaveLength(0);
  await page.locator('#identity-search').fill('  external.user  '); await page.locator('#find-identity').click();
  await expect(page.locator('#identity-account option')).toHaveCount(2); expect(lookups).toEqual(['external.user']);
  expect(await valid(page.locator('#identity-account'))).toBe(false);
  await page.locator('#identity-account').selectOption('subject-1'); expect(await valid(page.locator('#identity-account'))).toBe(true);
  await page.locator('#identity-search').fill('someone-else'); expect(await valid(page.locator('#identity-account'))).toBe(false); await close();
  await page.locator('[data-memberships]').first().click(); await page.locator('[name=teams]').uncheck();
  await expect(page.locator(`[name="role:${team.id}"]`)).toBeDisabled(); await submit();
  await expect(page.locator('.form-error')).toContainText('Select at least one team.'); await close();
  overview.teams.push(...Array.from({length: 100}, (_, index) => ({id: `team-${index.toString(16).padStart(32, '0')}`, name: `extra-${index}`})));
  await page.reload(); await page.locator('[data-memberships]').first().click();
  await page.locator('[name=teams]').evaluateAll(inputs => inputs.forEach(input => { if (!input.checked) input.click(); }));
  const beforeLimit = mutations.length; await submit();
  await expect(page.locator('.form-error')).toContainText('Select no more than 100 teams.');
  expect(mutations.length).toBe(beforeLimit); await close(); overview.teams.splice(1);
  keycloak = false; await page.reload(); await page.locator('#create').click(); await nameChecks();
  await submit(); await expect(page.locator('.form-error')).toContainText('Select at least one team.'); await close();
  signedIn = false; await page.reload();
  const password = page.locator('[name=password]'); await expect(password).toBeVisible();
  expect(await valid(password)).toBe(false); await nativeLimit(password, 1024);
  await password.fill('  unchanged secret  '); expect(await password.inputValue()).toBe('  unchanged secret  ');
  await page.route('http://user-forms.test/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/session') return route.fulfill({json: {authenticated: false}});
    const asset = path === '/' ? 'user_portal/public/index.html' : path.startsWith('/fonts/') || path === '/favicon.svg' ? `public${path}` : `user_portal/public${path}`;
    const contentType = path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : path.endsWith('.ttf') ? 'font/ttf' : path.endsWith('.svg') ? 'image/svg+xml' : 'text/html';
    return route.fulfill({body: await readFile(asset), contentType});
  });
  await page.goto('http://user-forms.test');
  const username = page.locator('[name=username]'), secret = page.locator('[name=secret]');
  await expect(username).toBeVisible(); await username.fill('ab'); expect(await valid(username)).toBe(false);
  await username.fill('abc'); expect(await valid(username)).toBe(true); await nativeLimit(username, 48);
  expect(await valid(secret)).toBe(false); await nativeLimit(secret, 1024);
  await secret.fill('  unchanged client secret  '); expect(await secret.inputValue()).toBe('  unchanged client secret  ');
  expect(errors).toEqual([]);
  console.log('PASS: create/edit names, descriptions, teams/environments, identity names/email, account lookup/link, memberships, service accounts, both legacy login forms and browser console');
} finally { await browser.close(); }
