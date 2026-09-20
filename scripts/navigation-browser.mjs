// Navigation and live list checks use fixtures; no running platform or real credentials.
import { chromium, expect } from '@playwright/test';
import { readFile, mkdir } from 'node:fs/promises';

const overview = {health: {status: 'online', provider: 'polaris'}, databases: [], users: [], shares: [], teams: [{id: 'team-a', name: 'Analytics', description: ''}]};
const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined});
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.clock.install();
  await page.route('http://navigation.test/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/session') return route.fulfill({json: {authenticated: true, userManagement: 'keycloak'}});
    if (path === '/api/overview') return route.fulfill({json: overview});
    const asset = path === '/' ? 'public/index.html' : `public${path}`;
    const contentType = path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : path.endsWith('.ttf') ? 'font/ttf' : path.endsWith('.svg') ? 'image/svg+xml' : 'text/html';
    return route.fulfill({body: await readFile(asset), contentType});
  });
  await page.goto('http://navigation.test');
  await page.getByRole('button', {name: 'Users', exact: true}).click();
  await expect(page).toHaveURL(/#users$/);
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
  overview.users.push({id: 'user-a', name: 'alice', memberships: [{team: 'team-a', role: 'reader'}], createdAt: Date.now(), identity: {status: 'linked'}});
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
  expect(errors).toEqual([]);
  console.log('PASS: admin reload and Back/Forward, persistent search, timed/focus list updates, unsaved form preservation, user-creation spacing');
} finally { await browser.close(); }
