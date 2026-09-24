// Team overview checks against API fixtures; no running platform required.
import { chromium, expect } from '@playwright/test';
import { readFile, mkdir } from 'node:fs/promises';

const teams = [
  {id: 'analytics', name: 'Energy analytics', description: 'Understand energy use together.', role: 'reader'},
  {id: 'research', name: 'Research', description: '', role: 'writer'},
];
let team = 'analytics', environment = 'development', failMembers = false, delayedMembers = null, keycloak = false, catalogUri = 'http://team.test/api/catalog';
const members = {
  analytics: [{id: 'alice', name: 'alice', role: 'reader'}, {id: 'bob', name: '<img src=x onerror=alert(1)>', role: 'admin'}],
  research: [{id: 'alice', name: 'alice', role: 'writer'}],
};
const database = {id: 'energy-dev', name: 'Energy', team: 'analytics', environment: 'development'};
const databases = [database];
const workspace = () => ({user: {id: 'alice', name: 'alice'}, teams, activeTeam: team, activeRole: teams.find(t => t.id === team).role, activeEnvironment: environment, environments: ['development', 'acceptance', 'production'], databases: databases.filter(db => db.team === team && db.environment === environment), deletingDatabases: [], notebooks: [], catalogUri});
const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined});
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 1050}}), errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => { errors.push(dialog.message()); dialog.dismiss(); });
  await page.route('http://team.test/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path === '/api/session') return route.fulfill({json: {authenticated: true, ...(keycloak ? {loginUrl: '/auth/login'} : {})}});
    if (path === '/api/workspace') return route.fulfill({json: workspace()});
    if (path === '/api/team' && request.method() === 'PATCH') { team = request.postDataJSON().team; return route.fulfill({json: workspace()}); }
    if (path === '/api/environment') { environment = request.postDataJSON().environment; return route.fulfill({json: workspace()}); }
    if (path === '/api/team') {
      const response = {team, members: members[team]};
      if (delayedMembers) { const delay = delayedMembers; delayedMembers = null; await delay; }
      return route.fulfill(failMembers ? {status: 503, json: {error: 'Team members are temporarily unavailable.'}} : {json: response});
    }
    if (path === '/api/databases' && request.method() === 'POST') {
      const db = {id: 'db-created', team, environment, ...request.postDataJSON()};
      databases.push(db); return route.fulfill({status: 201, json: db});
    }
    if (path.startsWith('/api/databases/') && request.method() === 'PATCH') {
      const db = databases.find(row => row.id === path.split('/').at(-1));
      db.name = request.postDataJSON().name; return route.fulfill({json: db});
    }
    if (path.startsWith('/api/databases/') && request.method() === 'DELETE') {
      const index = databases.findIndex(row => row.id === path.split('/').at(-1));
      if (request.postDataJSON().confirm_name !== databases[index].name) return route.fulfill({status: 422, json: {error: 'Name mismatch'}});
      databases.splice(index, 1); return route.fulfill({json: {deleted: true}});
    }
    if (path === '/api/contents') return route.fulfill({json: {namespaces: [], tables: [], views: []}});
    if (path === '/api/details') return route.fulfill({json: {kind: 'database', database}});
    if (path === '/api/received-shares') return route.fulfill({json: []});
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
  await page.getByRole('button', {name: 'Manage databases', exact: true}).click();
  await expect(page.locator('#databases-page')).toBeVisible();
  await expect(page.locator('#managed-databases')).toContainText('Energy');
  await expect(page.locator('#new-database')).toBeHidden();
  await expect(page.locator('#managed-databases').getByRole('button', {name: 'Rename'})).toHaveCount(0);
  await page.reload();
  await expect(page.locator('#databases-page')).toBeVisible();
  await expect(page.locator('#workspace-nav [data-page=databases]')).toHaveAttribute('aria-current', 'page');
  await page.locator('#workspace-nav [data-page=team]').click();
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
  await page.getByRole('button', {name: 'Getting started →', exact: true}).click();
  await expect(page.locator('#guide-page')).toBeVisible();
  await expect(page.locator('#guide-computer')).toContainText('Enable Keycloak sign-in');
  await expect(page.locator('#guide-agent pre')).toHaveCount(0);
  keycloak = true;
  await page.reload();
  await expect(page.locator('#guide-nav')).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('#guide-computer a[href="/iceberg_connect.py"]')).toBeVisible();
  // DuckDB CLI only: install for the chosen operating system, sign in, then read-only and writable attach.
  await expect(page.locator('#guide-computer pre')).toHaveCount(4);
  await expect(page.locator('#guide-computer .guide-warning')).toHaveCount(0);
  await page.locator('#guide-computer').getByRole('tab', {name: 'macOS', exact: true}).click();
  await expect(page.locator('#guide-computer pre').nth(0)).toHaveText('brew install uv duckdb');
  await page.locator('#guide-computer').getByRole('tab', {name: 'Windows', exact: true}).click();
  await expect(page.locator('#guide-computer').getByRole('tab', {name: 'Windows', exact: true})).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#guide-computer pre').nth(0)).toHaveText('winget install --id=astral-sh.uv -e\nwinget install DuckDB.cli');
  await expect(page.locator('#guide-computer pre')).toHaveCount(4);
  await expect(page.locator('#guide-computer pre').nth(2)).toHaveText('uv run iceberg_connect.py shell energy-dev');
  await expect(page.locator('#guide-computer pre').nth(3)).toHaveText('uv run iceberg_connect.py shell energy-dev --write');
  // One recipe per coding agent: Codex, GitHub Copilot in VS Code and Claude Code.
  await expect(page.locator('#guide-agent pre')).toHaveCount(3);
  await expect(page.locator('#guide-agent pre').nth(0)).toHaveText('[mcp_servers.iceberg-user]\nurl = "http://team.test/mcp"\nscopes = ["openid", "profile", "offline_access"]\n\n[mcp_servers.iceberg-user.oauth]\nclient_id = "iceberg-mcp"');
  await expect(page.locator('#guide-agent pre').nth(1)).toContainText('"clientId": "iceberg-mcp"');
  await expect(page.locator('#guide-agent pre').nth(2)).toHaveText('claude mcp add --transport http --client-id iceberg-mcp --callback-port 3010 iceberg-user http://team.test/mcp');
  await expect(page.locator('#guide-agent .guide-prompts li')).toHaveCount(2);
  await page.getByLabel('Environment', {exact: true}).selectOption('production');
  await expect(page.locator('#guide-computer pre').nth(2)).toHaveText('uv run iceberg_connect.py shell <database>');
  await expect(page.locator('#guide-computer pre').nth(3)).toHaveText('uv run iceberg_connect.py shell <database> --write');
  await page.getByLabel('Environment', {exact: true}).selectOption('development');
  await expect(page.locator('#guide-computer pre').nth(2)).toContainText('energy-dev');
  // A catalog on localhost only works on the platform's own machine; this page runs elsewhere.
  catalogUri = 'http://localhost:8181/api/catalog';
  await page.evaluate(() => refreshWorkspace());
  await expect(page.locator('#guide-computer .guide-warning')).toContainText('cannot reach this platform from other computers');
  catalogUri = 'http://team.test/api/catalog';
  await page.evaluate(() => refreshWorkspace());
  await expect(page.locator('#guide-computer .guide-warning')).toHaveCount(0);
  await page.locator('.guide-index button').nth(2).click();
  await expect(page.locator('#guide-agent')).toBeInViewport();
  await page.screenshot({path: 'test-results/team/guide-dark.png', fullPage: true});
  await page.locator('#guide-portal').getByRole('button', {name: 'Open the catalog', exact: true}).click();
  await expect(page.locator('#catalog-page')).toBeVisible();
  await page.locator('#workspace-nav [data-page=team]').click();
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
  await page.locator('#guide-nav').click();
  await expect(page.locator('#guide-agent')).toContainText('iceberg-user');
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.screenshot({path: 'test-results/team/guide-mobile-light.png', fullPage: true});
  await page.locator('#workspace-nav [data-page=team]').click();
  teams[0].role = 'admin';
  await page.getByRole('button', {name: 'Refresh team', exact: true}).click();
  await expect(page.locator('#team-page').getByRole('button', {name: 'Rename'})).toHaveCount(0);
  await page.getByRole('button', {name: 'Manage databases', exact: true}).click();
  await expect(page.locator('#new-database')).toBeVisible();
  await page.getByLabel('Environment', {exact: true}).selectOption('production');
  await page.locator('#new-database').click();
  await page.getByLabel('Database name', {exact: true}).fill('self_service');
  await page.getByLabel('Description (optional)').fill('Created by the team');
  await page.locator('#database-form').getByRole('button', {name: 'Create database'}).click();
  await expect(page.locator('#managed-databases')).toContainText('self_service');
  await page.getByRole('button', {name: 'Rename'}).click();
  await page.getByLabel('Database name', {exact: true}).fill('renamed_data');
  await page.getByRole('button', {name: 'Save name'}).click();
  await expect(page.locator('#managed-databases')).toContainText('renamed_data');
  await page.getByRole('button', {name: 'Delete', exact: true}).click();
  await expect(page.locator('#database-form')).toContainText('Production');
  await page.getByLabel('Database name', {exact: true}).fill('wrong_name');
  await page.getByRole('button', {name: 'Delete database permanently'}).click();
  await expect(page.locator('#notice')).toContainText('Type the database name exactly');
  await page.getByLabel('Database name', {exact: true}).fill('renamed_data');
  await page.getByRole('button', {name: 'Delete database permanently'}).click();
  await expect(page.locator('#managed-databases')).toContainText('no databases');
  await page.locator('#new-database').click();
  await page.getByLabel('Database name', {exact: true}).fill('draft_database');
  await page.getByLabel('Environment', {exact: true}).selectOption('development');
  await expect(page.locator('#database-form')).toBeEmpty();
  await expect(page.locator('#managed-databases')).toContainText('Energy');
  expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
  await page.screenshot({path: 'test-results/team/databases-mobile.png', fullPage: true});
  await page.setViewportSize({width: 1440, height: 1050});
  await page.screenshot({path: 'test-results/team/databases-light.png', fullPage: true});
  await page.getByRole('button', {name: 'Switch to dark mode', exact: true}).click();
  await page.screenshot({path: 'test-results/team/databases-dark.png', fullPage: true});
  expect(errors).toEqual([]);
  console.log('PASS: team summary, scoped members, safe text, context switching, stale responses, refresh/recovery, workspace links, route restoration, getting started guide, database lifecycle, dark/light/mobile layouts');
} finally { await browser.close(); }
