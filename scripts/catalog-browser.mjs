// Browser checks against deterministic API fixtures; no running stack required.
import { chromium, expect } from '@playwright/test';
import { readFile, mkdir } from 'node:fs/promises';

const current = '9007199254740993', old = '9007199254740991';
const database = {id: 'warehouse-dev', name: 'Energy', team: 'analytics', environment: 'development'};
const columns = [{id: 1, name: 'meter_id', type: 'long', required: true, doc: 'Meter identifier'}, {id: 2, name: 'kwh', type: 'double', required: false, doc: '<script>alert(1)</script>'}];
const table = {
  kind: 'table', name: 'readings', uuid: '71f9a6ca-31c7-4f73-85d3-18aa0a0ad013', location: 's3://energy/analytics/readings', formatVersion: 2,
  schemaId: 0, columns, properties: {'write.parquet.compression-codec': 'zstd', owner: 'Energy analytics'},
  updatedAt: 1789506000000, currentSnapshotId: current,
  snapshots: [{id: current, parentId: old, timestamp: 1789506000000, summary: {operation: 'append', 'total-records': '26880', 'total-data-files': '4', 'total-files-size': '185000'}}, {id: old, parentId: null, timestamp: 1789419600000, summary: {operation: 'append'}}],
  history: [{snapshotId: current, timestamp: 1789506000000}, {snapshotId: old, timestamp: 1789419600000}],
  refs: [{name: 'main', type: 'branch', snapshotId: current}, {name: 'daily-baseline', type: 'tag', snapshotId: old}],
  partitionSpecs: [{'spec-id': 0, fields: [{'field-id': 1000, name: 'day', 'source-id': 3, transform: 'day'}]}], defaultSpecId: 0,
  sortOrders: [{'order-id': 0, fields: []}], defaultSortOrderId: 0,
};
const view = {kind: 'view', uuid: 'view-uuid', formatVersion: 1, schemaId: 0, columns, properties: {}, currentVersionId: 1,
  versions: [{id: 1, schemaId: 0, timestamp: 1789506000000, defaultNamespace: ['analytics'], representations: [{dialect: 'spark', sql: 'SELECT meter_id, SUM(kwh) AS kwh\nFROM readings\nGROUP BY meter_id'}]}]};
let previewCalls = [], deny = false;
const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined});
try {
  const page = await browser.newPage({viewport: {width: 1440, height: 1050}});
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  await page.route('http://catalog.test/**', async route => {
    const url = new URL(route.request().url()), path = url.pathname;
    let body;
    if (path === '/api/session') body = {authenticated: true};
    else if (path === '/api/workspace') body = {user: {name: 'Analyst'}, teams: [{id: 'analytics', name: 'Energy analytics', role: 'reader'}], databases: [database], activeTeam: 'analytics', activeRole: 'reader', activeEnvironment: 'development', environments: ['development', 'acceptance', 'production'], notebooks: []};
    else if (path === '/api/contents') body = url.searchParams.has('namespace') ? {namespaces: [], tables: [{name: 'readings'}], views: [{name: 'daily_energy'}]} : {namespaces: [['analytics']], tables: [], views: []};
    else if (path === '/api/details') body = ({database: {kind: 'database', database}, namespace: {kind: 'namespace', properties: {owner: 'Energy analytics'}}, table, view})[url.searchParams.get('kind')];
    else if (path === '/api/preview') {
      const input = route.request().postDataJSON(); previewCalls.push(input);
      if (deny) return route.fulfill({status: 403, json: {error: 'This content is unavailable with your permissions.'}});
      body = {columns: ['meter_id', 'kwh'], rows: [['9007199254740993', '1.25'], ['2', null]], snapshotId: input.snapshot_id, limit: 100};
    }
    if (body) return route.fulfill({json: body});
    const asset = path === '/' ? 'user_portal/public/index.html' : path.startsWith('/fonts/') || path === '/favicon.svg' ? `public${path}` : `user_portal/public${path}`;
    const mime = path.endsWith('.js') ? 'text/javascript' : path.endsWith('.css') ? 'text/css' : path.endsWith('.ttf') ? 'font/ttf' : path.endsWith('.svg') ? 'image/svg+xml' : 'text/html';
    await route.fulfill({body: await readFile(asset), contentType: mime});
  });
  await mkdir('test-results/catalog', {recursive: true});
  await page.goto('http://catalog.test');
  await page.locator('#databases button').click();
  await expect(page.locator('.catalog-summary')).toContainText('Energy analytics');
  await page.getByRole('button', {name: 'Open →', exact: true}).click();
  await page.getByRole('searchbox', {name: 'Filter objects'}).fill('no match');
  await expect(page.getByText('No matching objects.', {exact: true})).toBeVisible();
  await page.getByRole('searchbox', {name: 'Filter objects'}).fill('');
  await page.locator('.object-row').filter({hasText: 'readings'}).getByRole('button').click();
  await expect(page.getByRole('tabpanel')).toContainText(current);
  await page.screenshot({path: 'test-results/catalog/table-dark.png', fullPage: true});
  await page.getByRole('tab', {name: 'Schema', exact: true}).click();
  await expect(page.getByRole('tabpanel')).toContainText('<script>alert(1)</script>');
  await expect(page.locator('#catalog-tab-panel script')).toHaveCount(0);
  await page.getByRole('tab', {name: 'Schema', exact: true}).press('ArrowRight');
  await expect(page.getByRole('tab', {name: 'Preview', exact: true})).toHaveAttribute('aria-selected', 'true');
  if (previewCalls.length) throw new Error('Preview ran without an explicit click');
  await page.getByRole('tab', {name: 'Snapshots', exact: true}).click();
  await expect(page.getByRole('tabpanel')).toContainText('daily-baseline');
  await page.screenshot({path: 'test-results/catalog/snapshots-dark.png', fullPage: true});
  await page.getByRole('button', {name: 'Preview snapshot', exact: true}).last().click();
  await expect(page.getByRole('combobox', {name: 'Preview snapshot', exact: true})).toHaveValue(old);
  await page.getByRole('button', {name: 'Load preview', exact: true}).click();
  await expect(page.getByRole('tabpanel')).toContainText('2 rows shown');
  if (previewCalls[0].snapshot_id !== old || previewCalls[0].limit !== 100) throw new Error('Preview snapshot or limit changed');
  await expect(page.getByRole('cell', {name: current, exact: true})).toBeVisible();
  deny = true;
  await page.getByRole('button', {name: 'Load preview', exact: true}).click();
  await expect(page.getByRole('tabpanel')).toContainText('unavailable with your permissions');
  await expect(page.getByRole('region', {name: 'Table preview', exact: true})).toHaveCount(0);
  await page.getByRole('button', {name: 'Refresh catalog', exact: true}).click();
  await expect(page.locator('#namespace-title')).toHaveText('readings');
  await expect(page.getByRole('tab', {name: 'Overview', exact: true})).toHaveAttribute('aria-selected', 'true');
  await page.getByRole('button', {name: 'Switch to light mode', exact: true}).click();
  await page.screenshot({path: 'test-results/catalog/table-light.png', fullPage: true});
  await page.setViewportSize({width: 390, height: 844});
  for (const name of ['Overview', 'Schema', 'Snapshots', 'Partitioning & sort', 'Properties', 'Preview']) {
    await page.getByRole('tab', {name, exact: true}).click();
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)) throw new Error(`Mobile overflow in ${name}`);
  }
  await page.screenshot({path: 'test-results/catalog/table-mobile.png', fullPage: true});
  await page.locator('#breadcrumbs').getByRole('button', {name: 'analytics', exact: true}).click();
  await page.locator('.object-row').filter({hasText: 'daily_energy'}).getByRole('button').click();
  await page.getByRole('tab', {name: 'SQL definition', exact: true}).click();
  await expect(page.locator('.view-sql')).toContainText('SUM(kwh)');
  await expect(page.getByRole('tab', {name: 'Snapshots', exact: true})).toHaveCount(0);
  await expect(page.getByRole('tab', {name: 'Preview', exact: true})).toHaveCount(0);
  await page.screenshot({path: 'test-results/catalog/view-mobile.png', fullPage: true});
  await page.getByRole('tab', {name: 'Versions', exact: true}).click();
  await page.getByText('Version 1 · current', {exact: false}).click();
  await expect(page.locator('.view-sql')).toBeVisible();
  if (errors.length) throw new Error(errors.join('\n'));
  console.log('PASS: catalog navigation/filtering, schema escaping, keyboard tabs, precise snapshot selection, bounded on-demand previews, permission errors, refresh, views, dark/light/mobile layouts');
} finally { await browser.close(); }
