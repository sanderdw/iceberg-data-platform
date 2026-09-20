// Exercise the real portal UI against deterministic report/catalog responses.
import { chromium, expect } from '@playwright/test';
import { readFile, mkdir } from 'node:fs/promises';

const database = {id: 'energy-dev', name: 'Energy', team: 'analytics', environment: 'development'};
let environment = 'development', items = [], runs = [], deny = false, counter = 0;
const workspace = () => ({user: {id: 'analyst', name: 'Analyst'}, teams: [{id: 'analytics', name: 'Energy analytics', role: 'reader'}], databases: environment === 'development' ? [database] : [], activeTeam: 'analytics', activeRole: 'reader', activeEnvironment: environment, environments: ['development', 'acceptance', 'production'], notebooks: []});
const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined});
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 1050}}), errors = [];
  page.on('pageerror', e => { errors.push(e.message); console.error(e); });
  await page.route('http://reports.test/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname, method = request.method();
    let body;
    if (path === '/api/session') body = {authenticated: true};
    else if (path === '/api/workspace') body = workspace();
    else if (path === '/api/environment') { environment = request.postDataJSON().environment; body = workspace(); }
    else if (path === '/api/contents') body = url.searchParams.has('namespace') ? {namespaces: [], tables: [{name: 'readings'}], views: []} : {namespaces: [['analytics']], tables: [], views: []};
    else if (path === '/api/details') body = {columns: [{name: 'street', type: 'string'}, {name: 'kwh', type: 'double'}, {name: 'timestamp', type: 'timestamptz'}]};
    else if (path === '/api/report-sql') body = {sql: 'SELECT street AS dimension, sum(kwh) AS value FROM source GROUP BY street LIMIT 500', parameters: {}};
    else if (path === '/api/report-jobs' && method === 'POST') { runs.push(request.postDataJSON()); body = {id: String(runs.length), status: 'running'}; }
    else if (path.startsWith('/api/report-jobs/')) body = method === 'DELETE' ? {status: 'cancelled'} : {status: 'done', cards: [{name: 'Street consumption', width: 'full', ...(deny ? {error: 'Data access changed. Refresh the report with your current permissions.'} : {result: {data: {columns: [{name: 'dimension', type: 'VARCHAR'}, {name: 'value', type: 'BIGINT'}], rows: [['<script>alert(1)</script>', '9007199254740993']], timezone: 'Europe/Amsterdam', snapshotId: '9007199254740993'}, svg: '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="200"><rect x="20" y="30" width="280" height="40" fill="#59c2bb"/><text x="20" y="110">Street consumption</text></svg>', cached: false, executedAt: 1789506000}})}]};
    else if ((path === '/api/reports' || path === '/api/dashboards') && method === 'POST') {
      body = {id: (++counter).toString(16).padStart(32, '0'), kind: path.endsWith('dashboards') ? 'dashboard' : 'report', body: request.postDataJSON().definition, revision: 1, owner: 'analyst', updated: 1789506000}; items.push(body);
    } else if (path === '/api/reports') body = {items: environment === 'development' ? items : []};
    else if (path.startsWith('/api/reports/')) body = items.find(i => i.id === path.split('/').at(-1));
    if (body) return route.fulfill({json: body});
    if (path.startsWith('/api/')) throw new Error(`Unexpected API request ${method} ${path}`);
    const asset = path === '/' ? 'user_portal/public/index.html' : path.startsWith('/fonts/') || path === '/favicon.svg' ? `public${path}` : `user_portal/public${path}`;
    await route.fulfill({body: await readFile(asset), contentType: path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : path.endsWith('.ttf') ? 'font/ttf' : path.endsWith('.svg') ? 'image/svg+xml' : 'text/html'});
  });
  await mkdir('test-results/reports-ui', {recursive: true});
  await page.goto('http://reports.test');
  await page.locator('[data-page="reports"]').click();
  await page.getByRole('button', {name: 'New report', exact: true}).click();
  await page.getByLabel('Name', {exact: true}).fill('Street consumption');
  await page.getByRole('button', {name: 'analytics /', exact: true}).click();
  await page.getByRole('button', {name: 'readings', exact: true}).click();
  await expect(page.getByLabel('Group by', {exact: true}).locator('option[value="street"]')).toHaveCount(1);
  await page.getByLabel('Summarize', {exact: true}).selectOption('sum');
  await page.getByLabel('Measure', {exact: true}).selectOption('kwh');
  await page.getByLabel('Group by', {exact: true}).selectOption('street');
  await page.getByRole('button', {name: 'Add filter', exact: true}).click();
  await page.getByLabel('Filter 1 value', {exact: true}).fill('Solar Street');
  await page.getByLabel('Chart type', {exact: true}).selectOption('bar');
  await page.getByRole('button', {name: 'Save', exact: true}).click();
  await expect(page.locator('[data-save-status]')).toContainText('Saved');
  expect(items[0].body.builder.filters[0].value).toBe('Solar Street');
  const reportUrl = page.url(); await page.reload();
  await expect(page.getByLabel('Name', {exact: true})).toHaveValue('Street consumption');
  expect(page.url()).toBe(reportUrl);
  await page.getByRole('button', {name: 'Run report', exact: true}).click();
  await expect(page.locator('[data-run-status]')).toContainText('completed');
  await page.getByText('Result data', {exact: true}).click();
  await expect(page.getByRole('cell', {name: '9007199254740993', exact: true})).toBeVisible();
  await expect(page.locator('[data-results] script')).toHaveCount(0);
  await page.screenshot({path: 'test-results/reports-ui/builder-dark.png', fullPage: true});
  await page.getByRole('button', {name: 'Edit as SQL copy', exact: true}).click();
  await expect(page.getByLabel('SQL query', {exact: true})).toHaveValue(/FROM source/);
  await page.getByRole('button', {name: 'Save', exact: true}).click();
  await expect(page.locator('[data-save-status]')).toContainText('Saved');
  expect(items).toHaveLength(2); expect(items[0].body.mode).toBe('builder');
  await page.getByRole('button', {name: '← All reports', exact: true}).click();
  await page.getByRole('button', {name: 'New dashboard', exact: true}).click();
  await page.getByLabel('Name', {exact: true}).fill('Energy overview');
  await page.getByRole('button', {name: 'Add report', exact: true}).click();
  await page.getByLabel('Card 1 width').selectOption('full');
  await page.getByLabel('Card 1 filter column').fill('street');
  await page.getByRole('button', {name: 'Save', exact: true}).click();
  await expect(page.locator('[data-save-status]')).toContainText('Saved');
  await page.getByLabel('Filter', {exact: true}).fill('Solar Street');
  await page.getByRole('button', {name: 'Run dashboard', exact: true}).click();
  await expect(page.locator('[data-run-status]')).toContainText('completed');
  expect(runs.at(-1).filter_value).toBe('Solar Street');
  await page.getByRole('button', {name: 'Switch to light mode', exact: true}).click();
  await page.screenshot({path: 'test-results/reports-ui/dashboard-light.png', fullPage: true});
  deny = true; await page.getByRole('button', {name: 'Refresh now', exact: true}).click();
  await expect(page.locator('[data-results]')).toContainText('Data access changed');
  await expect(page.locator('[data-results] img')).toHaveCount(0);
  await page.setViewportSize({width: 390, height: 844});
  if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error('Dashboard overflows mobile viewport');
  await page.screenshot({path: 'test-results/reports-ui/dashboard-mobile.png', fullPage: true});
  await page.locator('#environment').selectOption('production');
  await expect(page.locator('#report-root')).not.toContainText('Data access changed');
  await expect(page.locator('#report-root')).not.toContainText('Street consumption');
  if (errors.length) throw new Error(errors.join('\n'));
  console.log('PASS: visual authoring, durable routes, SQL copies, dashboard layout/filters, permission errors, exact values, escaping, context reset, dark/light/mobile');
} finally { await browser.close(); }
