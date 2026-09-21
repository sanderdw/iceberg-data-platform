// Team overview checks against API fixtures; no running platform required.
import { chromium, expect } from '@playwright/test';
import { readFile, mkdir } from 'node:fs/promises';

const teams = [
  {id: 'analytics', name: 'Energy analytics', description: 'Understand energy use together.', role: 'reader'},
  {id: 'research', name: 'Research', description: '', role: 'writer'},
];
let team = 'analytics', environment = 'development', failMembers = false, delayedMembers = null;
const members = {
  analytics: [{id: 'alice', name: 'alice', role: 'reader'}, {id: 'bob', name: '<img src=x onerror=alert(1)>', role: 'admin'}],
  research: [{id: 'alice', name: 'alice', role: 'writer'}],
};
const database = {id: 'energy-dev', name: 'Energy', team: 'analytics', environment: 'development'};
const workspace = () => ({user: {id: 'alice', name: 'alice'}, teams, activeTeam: team, activeRole: teams.find(t => t.id === team).role, activeEnvironment: environment, environments: ['development', 'acceptance', 'production'], databases: team === 'analytics' && environment === 'development' ? [database] : [], notebooks: []});
const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined});
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 1050}}), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => { errors.push(dialog.message()); dialog.dismiss(); });
  await page.route('http://team.test/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path === '/api/session') return route.fulfill({json: {authenticated: true}});
    if (path === '/api/workspace') return route.fulfill({json: workspace()});
    if (path === '/api/team' && request.method() === 'PATCH') { team = request.postDataJSON().team; return route.fulfill({json: workspace()}); }
    if (path === '/api/environment') { environment = request.postDataJSON().environment; return route.fulfill({json: workspace()}); }
    if (path === '/api/team') {
      const response = {team, members: members[team]};
      if (delayedMembers) { const delay = delayedMembers; delayedMembers = null; await delay; }
      return route.fulfill(failMembers ? {status: 503, json: {error: 'Team members are temporarily unavailable.'}} : {json: response});
    }
    if (path === '/api/contents') return route.fulfill({json: {namespaces: [], tables: [], views: []}});
    if (path === '/api/details') return route.fulfill({json: {kind: 'database', database}});
    if (path === '/api/shares') return route.fulfill({json: {shares: [], limits: {shares: 20, objects: 50}}});
    const asset = path === '/' ? 'user_portal/public/index.html' : path.startsWith('/fonts/') || path === '/favicon.svg' ? `public${path}` : `user_portal/public${path}`;
    const contentType = path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : path.endsWith('.ttf') ? 'font/ttf' : path.endsWith('.svg') ? 'image/svg+xml' : 'text/html';
    return route.fulfill({body: await readFile(asset), contentType});
  });
  await page.goto('http://team.test/#team');
  await expect(page.locator('#team-page')).toBeVisible();
  await expect(page.locator('#team-name')).toHaveText('Energy analytics');
  await expect(page.locator('#team-description')).toHaveText(teams[0].description);
  await expect(page.locator('#team-members')).toContainText('alice (you)');
  await expect(page.locator('#team-members')).toContainText(members.analytics[1].name);
  await expect(page.locator('#team-members img')).toHaveCount(0);
  await expect(page.locator('#team-summary')).toContainText('Your active notebooks0');
  await expect(page.locator('#team-summary')).toContainText('Your roleRead');
  await expect(page.locator('#team-databases')).toContainText('Energy');
  await mkdir('test-results/team', {recursive: true});
  await page.screenshot({path: 'test-results/team/overview-dark.png', fullPage: true});
  await page.locator('#team-databases').getByRole('button', {name: 'Browse →', exact: true}).click();
  await expect(page.locator('#catalog-page')).toBeVisible();
  await expect(page.locator('.catalog-summary')).toContainText('Energy');
  await page.goBack();
  await expect(page.locator('#team-page')).toBeVisible();
  await page.reload();
  await expect(page.locator('#team-members')).toContainText('alice (you)');
  await expect(page.locator('#workspace-nav [data-page=team]')).toHaveAttribute('aria-current', 'page');
  for (const [label, name] of [['Go to notebooks →', 'notebooks'], ['View data shares →', 'shares'], ['Browse catalog →', 'catalog']]) {
    await page.getByRole('button', {name: label, exact: true}).click();
    await expect(page.locator(`#${name}-page`)).toBeVisible();
    await page.locator('#workspace-nav [data-page=team]').click();
  }
  members.analytics[1] = {id: 'bob', name: 'bob', role: 'writer'};
  await page.evaluate(() => refreshWorkspace());
  await expect(page.locator('#team-members')).toContainText('bob');
  await expect(page.locator('#team-members')).not.toContainText('Administrator');
  await page.getByLabel('Environment', {exact: true}).selectOption('production');
  await expect(page.locator('#team-summary')).toContainText('Production');
  await expect(page.locator('#team-databases')).toContainText('no databases');
  await expect(page.locator('#team-members')).toContainText('bob');
  let release;
  delayedMembers = new Promise(resolve => { release = resolve; });
  const pending = page.waitForRequest(request => new URL(request.url()).pathname === '/api/team');
  await page.getByRole('button', {name: 'Refresh team', exact: true}).click();
  await pending;
  await page.getByLabel('Active team', {exact: true}).selectOption('research');
  await expect(page.locator('#team-name')).toHaveText('Research');
  await expect(page.locator('#team-members')).toContainText('alice (you)');
  await expect(page.locator('#team-members')).not.toContainText('bob');
  const oldResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/team');
  release(); await oldResponse;
  await expect(page.getByRole('button', {name: 'Refresh team', exact: true})).toBeEnabled();
  await expect(page.locator('#team-members')).not.toContainText('bob');
  await expect(page.locator('#team-description')).toBeHidden();
  await expect(page.locator('#team-summary')).toContainText('Read & write');
  failMembers = true;
  await page.getByRole('button', {name: 'Refresh team', exact: true}).click();
  await expect(page.locator('#team-members')).toContainText('temporarily unavailable');
  failMembers = false;
  await page.getByRole('button', {name: 'Refresh team', exact: true}).click();
  await expect(page.locator('#team-members')).toContainText('alice (you)');
  await page.getByLabel('Active team', {exact: true}).selectOption('analytics');
  await page.getByLabel('Environment', {exact: true}).selectOption('development');
  await expect(page.locator('#team-databases')).toContainText('Energy');
  await page.getByRole('button', {name: 'Switch to light mode', exact: true}).click();
  await page.screenshot({path: 'test-results/team/overview-light.png', fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  await expect(page.locator('#team-members')).toContainText('bob');
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.screenshot({path: 'test-results/team/overview-mobile.png', fullPage: true});
  expect(errors).toEqual([]);
  console.log('PASS: team summary, scoped members, safe text, context switching, stale responses, refresh/recovery, workspace links, route restoration, dark/light/mobile layouts');
} finally { await browser.close(); }
