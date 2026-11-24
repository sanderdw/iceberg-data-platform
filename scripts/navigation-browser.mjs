// Navigation and live list checks use fixtures; no running platform or real credentials.
import { chromium, expect } from '@playwright/test';
import { readFile, mkdir } from 'node:fs/promises';

const overview = {health: {status: 'online', provider: 'polaris'}, databases: [], users: [], shares: [], teams: [{id: 'team-a', name: 'Analytics', description: ''}],
  extensions: [{id: 'dbt', origin: 'http://dbt.test', title: 'Pipelines', purpose: 'Build, test and schedule dbt models'}]};
// What the session says about the user portal: the Apps menu and the read-only bar link to it.
const workspaces = {userPortalUrl: 'http://workspaces.test', userPortalPages: [{id: 'catalog', title: 'Catalog', url: 'http://workspaces.test/#catalog'}, {id: 'notebooks', title: 'Notebooks', url: 'http://workspaces.test/#notebooks'}]};
const overflow = target => target.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
// The header's parts, to tell one row from a wrapped one.
const headerRow = target => target.evaluate(() => {
  const tops = [...document.querySelectorAll('.header > .brand, .header nav .nav-item, .header-actions > *')].map(node => { const box = node.getBoundingClientRect(); return Math.round(box.top + box.height / 2); });
  return {height: document.querySelector('.header').getBoundingClientRect().height, spread: Math.max(...tops) - Math.min(...tops), labels: [...document.querySelectorAll('.header nav .nav-item')].map(node => node.textContent)};
});
const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined});
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.clock.install();
  await page.route('http://navigation.test/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/session') return route.fulfill({json: {authenticated: true, access: 'admin', user: 'ada.admin', loginUrl: '/auth/login', userManagement: 'keycloak', ...workspaces}});
    if (path === '/api/users/user-a' && route.request().method() === 'PATCH') return route.fulfill({json: {user: overview.users[0]}});
    if (path === '/api/overview') return route.fulfill({json: overview});
    const asset = path === '/' ? 'public/index.html' : `public${path}`;
    const contentType = path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : path.endsWith('.ttf') ? 'font/ttf' : path.endsWith('.svg') ? 'image/svg+xml' : 'text/html';
    return route.fulfill({body: await readFile(asset), contentType});
  });
  await page.goto('http://navigation.test');
  await expect(page.getByRole('heading', {name: 'Teams.', exact: true})).toBeVisible();
  // One header row; no breadcrumb strip, no counters, and nothing read-only for a platform administrator.
  await expect(page.locator('.topbar, .stats, .read-only-badge, .read-only-bar')).toHaveCount(0);
  await expect(page.locator('.header nav .nav-item')).toHaveText(['Teams', 'Users', 'Databases0', 'Data shares', 'Catalog', 'Infrastructure']);
  await expect(page.locator('.panel-heading h2').first()).toHaveText('All teams 1');
  // The account menu: closed until asked for, reachable by keyboard, closed by Escape (focus returns) or a click elsewhere.
  const account = page.locator('#account-toggle'), accountMenu = page.locator('#account-menu');
  await expect(account).toHaveText('ada.admin');
  await expect(account).toHaveAttribute('aria-expanded', 'false');
  await expect(accountMenu).toBeHidden();
  await account.focus();
  await page.keyboard.press('Enter');
  await expect(account).toHaveAttribute('aria-expanded', 'true');
  await expect(accountMenu.locator('.account-identity')).toHaveText('ada.adminPlatform administratorpolaris online');
  await expect(accountMenu.getByRole('button')).toHaveText(['Getting started', 'Light / ○', 'Sign out']);
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', {name: 'Getting started'})).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(accountMenu).toBeHidden();
  await expect(account).toHaveAttribute('aria-expanded', 'false');
  await expect(account).toBeFocused();
  await account.click();
  await page.getByRole('button', {name: 'Switch to light mode'}).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(accountMenu).toBeVisible();
  await page.getByRole('button', {name: 'Switch to dark mode'}).click();
  await page.getByRole('heading', {name: 'Teams.', exact: true}).click();
  await expect(accountMenu).toBeHidden();
  // The Apps menu: the user portal's pages and the extensions, each in a new tab; one menu open at a time.
  const apps = page.locator('.apps-toggle'), appsMenu = page.locator('.apps-menu');
  await expect(apps).toHaveAttribute('aria-expanded', 'false');
  await expect(appsMenu).toBeHidden();
  await account.click();
  await apps.click();
  await expect(apps).toHaveAttribute('aria-expanded', 'true');
  await expect(accountMenu).toBeHidden();
  await expect(appsMenu.locator('a.apps-link strong')).toHaveText(['Catalog', 'Notebooks', 'Pipelines']);
  await expect(appsMenu.getByRole('link', {name: /Catalog/})).toHaveAttribute('href', 'http://workspaces.test/#catalog');
  await expect(appsMenu.getByRole('link', {name: /Pipelines/})).toHaveAttribute('href', 'http://dbt.test/');
  for (const link of await appsMenu.locator('a.apps-link').all()) { await expect(link).toHaveAttribute('target', '_blank'); await expect(link).toHaveAttribute('rel', 'noopener'); }
  await page.keyboard.press('Escape');
  await expect(appsMenu).toBeHidden();
  await expect(apps).toHaveAttribute('aria-expanded', 'false');
  await expect(apps).toBeFocused();
  await apps.click();
  await account.click();
  await expect(appsMenu).toBeHidden();
  await expect(accountMenu).toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByRole('button', {name: 'Users', exact: true}).click();
  await expect(page).toHaveURL(/#users$/);
  // The Apps menu survives a new page: it still opens, and closes on a click elsewhere.
  await apps.click();
  await expect(appsMenu).toBeVisible();
  await page.getByRole('heading', {name: 'Users.', exact: true}).click();
  await expect(appsMenu).toBeHidden();
  await page.reload();
  await expect(page.getByRole('heading', {name: 'Users.', exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Teams', exact: true}).click();
  await page.goBack();
  await expect(page.getByRole('heading', {name: 'Users.', exact: true})).toBeVisible();
  await page.goForward();
  await expect(page.getByRole('heading', {name: 'Teams.', exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Users', exact: true}).click();
  await page.getByRole('button', {name: 'Create user', exact: true}).first().click();
  await page.locator('#modal-form input[name=name]').fill('draft-user');
  await page.getByLabel('First name').fill('Unsaved');
  const gap = await page.evaluate(() => document.querySelector('#modal-form').getBoundingClientRect().top - document.querySelector('.identity-mode').getBoundingClientRect().bottom);
  expect(gap).toBeGreaterThanOrEqual(32);
  overview.users.push({id: 'user-a', name: 'alice', teams: ['team-a'], memberships: [{team: 'team-a', role: 'reader'}], createdAt: Date.now(), identity: {status: 'linked'}});
  await page.clock.runFor(31000);
  await expect(page.getByLabel('First name')).toHaveValue('Unsaved');
  await expect(page.locator('#modal-form input[name=name]')).toHaveValue('draft-user');
  await mkdir('test-results/navigation', {recursive: true});
  await page.screenshot({path: 'test-results/navigation/user-form.png'});
  await page.keyboard.press('Escape');
  await page.clock.runFor(31000);
  await expect(page.locator('#table')).toContainText('alice');
  await page.locator('#search').fill('alice');
  await page.reload();
  await expect(page.locator('#search')).toHaveValue('alice');
  await expect(page.locator('#table')).toContainText('alice');
  overview.users[0].name = 'alice-renamed';
  await page.evaluate(() => { document.activeElement.blur(); window.dispatchEvent(new Event('focus')); });
  await expect(page.locator('#table')).toContainText('alice-renamed');
  // A status message stays on its page and is cleared by navigation.
  await page.locator('tbody tr').filter({hasText: 'alice-renamed'}).getByRole('button', {name: 'Edit access'}).click();
  await page.getByRole('dialog').getByRole('button', {name: 'Save', exact: true}).click();
  await expect(page.locator('#status')).toHaveText('[ Team access and permissions updated. ]');
  await page.clock.runFor(31000);
  await expect(page.locator('#status')).toHaveText('[ Team access and permissions updated. ]');
  await page.getByRole('button', {name: 'Teams', exact: true}).click();
  await expect(page.locator('#status')).toBeEmpty();
  await page.goBack();
  await expect(page.getByRole('heading', {name: 'Users.', exact: true})).toBeVisible();
  await expect(page.locator('#status')).toBeEmpty();
  // Getting started: portal, MCP agent and API, with commands for this origin. It sits in the account menu, so a narrower window keeps it.
  await page.setViewportSize({width: 1100, height: 800});
  await expect(page.getByRole('button', {name: 'Getting started'})).toHaveCount(0);
  await account.click();
  await page.getByRole('button', {name: 'Getting started'}).click();
  await expect(page.getByRole('heading', {name: 'Getting started.', exact: true})).toBeVisible();
  await expect(accountMenu).toBeHidden();
  expect(await overflow(page)).toBeLessThanOrEqual(0);
  await page.setViewportSize({width: 1440, height: 1000});
  for (const name of ['Point and click.', 'Ask an AI agent.', 'Script it.']) await expect(page.getByRole('heading', {name, exact: true})).toBeVisible();
  await expect(page.locator('#guide-agent pre')).toHaveCount(3);
  await expect(page.locator('#guide-agent pre').first()).toContainText('url = "http://navigation.test/mcp"');
  await expect(page.locator('#guide-agent pre').last()).toContainText('iceberg-admin http://navigation.test/mcp');
  await expect(page.locator('#guide-api pre').first()).toContainText("post('/api/identity/users'");
  await expect(page.locator('#guide-api a[href="/docs"]')).toBeVisible();
  await page.getByRole('button', {name: /02 \/ AGENT/}).click();
  await expect(page.locator('#guide-agent')).toBeInViewport();
  await page.screenshot({path: 'test-results/navigation/guide.png', fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  expect(await overflow(page)).toBeLessThanOrEqual(0);
  await page.screenshot({path: 'test-results/navigation/guide-mobile.png', fullPage: true});
  await page.getByRole('button', {name: 'Start with a team'}).click();
  await expect(page.getByRole('heading', {name: 'Teams.', exact: true})).toBeVisible();
  // A phone: nothing scrolls sideways, and both menus stay on the screen.
  expect(await overflow(page)).toBeLessThanOrEqual(0);
  for (const [toggle, menu] of [[account, accountMenu], [apps, appsMenu]]) {
    await toggle.click();
    const box = await menu.boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(390);
    await page.keyboard.press('Escape');
  }
  // A laptop: one header row of about 60 px with every label in full.
  await page.setViewportSize({width: 1280, height: 800});
  expect(await overflow(page)).toBeLessThanOrEqual(0);
  let header = await headerRow(page);
  expect(header.height).toBeLessThanOrEqual(64);
  expect(header.spread).toBeLessThanOrEqual(2);
  // A team administrator: read-only. Everything shows; nothing that changes the platform does.
  const readOnly = await browser.newPage({viewport: {width: 1280, height: 800}});
  readOnly.on('pageerror', error => errors.push(error.message));
  await readOnly.route('http://navigation.test/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/session') return route.fulfill({json: {authenticated: true, access: 'viewer', user: 'tom.admin', loginUrl: '/auth/login', userManagement: 'keycloak', ...workspaces}});
    if (path === '/api/overview') return route.fulfill({json: {...overview, databases: [{id: 'db-a', name: 'sales', team: 'team-a', environment: 'development', status: 'ready', users: []}]}});
    if (path === '/api/admin/explorer/databases') return route.fulfill({json: {databases: []}});
    const asset = path === '/' ? 'public/index.html' : `public${path}`;
    const contentType = path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : path.endsWith('.ttf') ? 'font/ttf' : path.endsWith('.svg') ? 'image/svg+xml' : 'text/html';
    return route.fulfill({body: await readFile(asset), contentType});
  });
  await readOnly.goto('http://navigation.test');
  await expect(readOnly.getByRole('heading', {name: 'Teams.', exact: true})).toBeVisible();
  // Two signals: the pill in the header, and one bar on the pages where a team administrator could expect to change something.
  await expect(readOnly.locator('.header .read-only-badge')).toHaveText('Read-only');
  const bar = readOnly.locator('.read-only-bar'), workspacesLink = bar.getByRole('link', {name: 'Open Workspaces ↗', exact: true});
  await expect(bar).toContainText('You have read-only access. Manage your team’s databases and data shares in Workspaces.');
  await expect(workspacesLink).toHaveAttribute('href', 'http://workspaces.test');
  await expect(workspacesLink).toHaveAttribute('target', '_blank');
  await expect(workspacesLink).toHaveAttribute('rel', 'noopener');
  expect((await bar.boundingBox()).height).toBeLessThanOrEqual(48);
  // The widest header, on a laptop: still one row with every label in full, and nothing scrolls sideways.
  expect(await overflow(readOnly)).toBeLessThanOrEqual(0);
  header = await headerRow(readOnly);
  expect(header.height).toBeLessThanOrEqual(64);
  expect(header.spread).toBeLessThanOrEqual(2);
  expect(header.labels).toEqual(['Teams', 'Users', 'Databases1', 'Data shares', 'Catalog', 'Infrastructure']);
  await expect(readOnly.locator('.brand > span')).toBeVisible();
  await readOnly.locator('#account-toggle').click();
  await expect(readOnly.locator('#account-menu .account-identity')).toHaveText('tom.adminTeam administrator · read-onlypolaris online');
  await readOnly.keyboard.press('Escape');
  await expect(readOnly.locator('thead th')).not.toContainText(['Actions']);
  await expect(readOnly.locator('tbody')).toContainText('Analytics');
  for (const name of ['Create team', 'Edit', 'Delete']) await expect(readOnly.getByRole('button', {name, exact: true})).toHaveCount(0);
  await readOnly.getByRole('button', {name: 'Databases'}).first().click();
  await expect(readOnly.locator('tbody')).toContainText('sales');
  for (const name of ['Create database', 'Rename', 'Move', 'Delete']) await expect(readOnly.getByRole('button', {name, exact: true})).toHaveCount(0);
  await readOnly.screenshot({path: 'test-results/navigation/read-only.png', fullPage: true});
  for (const [name, heading, shown] of [['Users', 'Users.', 1], ['Data shares', 'Data shares.', 1], ['Catalog', 'Catalog.', 0], ['Teams', 'Teams.', 1]]) {
    await readOnly.getByRole('button', {name, exact: true}).click();
    await expect(readOnly.getByRole('heading', {name: heading, exact: true})).toBeVisible();
    await expect(bar).toHaveCount(shown);
    await expect(readOnly.getByText('read-only').filter({visible: true})).toHaveCount(1 + shown);
  }
  // Getting started keeps its own note instead of the bar.
  await readOnly.locator('#account-toggle').click();
  await readOnly.getByRole('button', {name: 'Getting started'}).click();
  await expect(bar).toHaveCount(0);
  await expect(readOnly.locator('.read-only-note')).toBeVisible();
  await readOnly.setViewportSize({width: 390, height: 844});
  await readOnly.getByRole('button', {name: 'Teams', exact: true}).click();
  await expect(bar).toBeVisible();
  expect(await overflow(readOnly)).toBeLessThanOrEqual(0);
  expect((await headerRow(readOnly)).height).toBeLessThanOrEqual(64);
  expect(errors).toEqual([]);
  console.log('PASS: admin reload and Back/Forward, persistent search, timed/focus list updates, unsaved form preservation, user-creation spacing, page-scoped status, the Apps and account menus, getting started guide, one header row without sideways scrolling at 1280 and 390, read-only team administrators');
} finally { await browser.close(); }
