import { test, expect } from '@playwright/test';

function sample() {
  const now = new Date().toISOString();
  const section = data => ({ status: 'online', updatedAt: now, data });
  return {
    sampledAt: now,
    services: section([{ id: 'polaris', name: 'Apache Polaris', status: 'online', latencyMs: 12,
      availabilityPercent: 50, samples: 2, checkedAt: now,
      history: [{ at: now, ok: false, latencyMs: null }, { at: now, ok: true, latencyMs: 12 }] }]),
    containers: section([{ name: 'polaris', state: 'running', status: 'online', cpuPercent: 5,
      memoryBytes: 1024 ** 3, memoryLimitBytes: 2 * 1024 ** 3, startedAt: now, restarts: 1 }]),
    postgres: section({ bytes: 1024 ** 2, connections: 4, maxConnections: 100, active: 2,
      idle: 2, blocked: 0, deadlocks: 0, transactionsPerSecond: 3, latencyMs: 7 }),
    storage: section({ bytes: null, objects: null, complete: false, buckets: [
      { database: 'Alpha', bucket: 'alpha', team: 'team', bytes: 42, objects: 1, status: 'online' },
      { database: 'Zulu', bucket: 'zulu', team: 'team', bytes: 100, objects: 2, status: 'online' },
      { database: '<img src=x onerror=alert(1)>', bucket: 'broken', team: 'team', status: 'unavailable' },
    ] }),
    disk: section({ totalBytes: 1024 ** 4, usedBytes: 1024 ** 3, freeBytes: 1024 ** 4 - 1024 ** 3 }),
  };
}

test('infrastructure refresh, partial failure, sorting, mobile and navigation lifecycle', async ({ page }) => {
  await page.clock.install();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let requests = 0, unavailable = false;
  await page.route('**/api/session', route => route.fulfill({ json: { authenticated: true } }));
  await page.route('**/api/overview', route => route.fulfill({ json: {
    health: { status: 'online', provider: 'Apache Polaris' }, databases: [], users: [],
    teams: [{ id: 'team', name: 'Analytics' }],
  } }));
  await page.route('**/api/infrastructure', route => {
    requests++;
    return route.fulfill(unavailable ? { status: 503, json: { error: 'Monitoring temporarily unavailable.' } } : { json: sample() });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Infrastructure', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'PostgreSQL metadata' })).toBeVisible();
  await expect(page.locator('.metric-card').filter({ hasText: 'Managed bucket data' })).toContainText('—');
  const bucketRows = page.locator('.metric-section').filter({ has: page.getByRole('heading', { name: 'Buckets by database' }) }).locator('tbody tr');
  await expect(bucketRows.first()).toContainText('Zulu');
  await page.getByLabel('Sort buckets').selectOption('database');
  await expect(bucketRows.first()).toContainText('<img src=x onerror=alert(1)>');
  await expect(page.locator('#monitor-content img')).toHaveCount(0);
  unavailable = true;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Monitoring temporarily unavailable');
  await expect(page.locator('#monitor-updated')).toContainText('Connection lost');
  await expect(page.locator('.metric-heading').filter({ hasText: 'PostgreSQL metadata' })).toContainText('Unavailable');
  unavailable = false;
  await page.getByRole('button', { name: 'Refresh', exact: true }).click();
  await expect(page.locator('#monitor-updated')).toContainText('auto-refresh');
  await expect(page.locator('#monitor-error')).toBeEmpty();
  await page.screenshot({ path: 'test-results/infrastructure-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/infrastructure-mobile.png', fullPage: true });
  const before = requests;
  await page.clock.runFor(16000);
  await expect.poll(() => requests).toBeGreaterThan(before);
  await page.getByRole('button', { name: 'Teams', exact: true }).click();
  const after = requests;
  await page.clock.runFor(30000);
  expect(requests).toBe(after);
  expect(errors).toEqual([]);
});

test('missing monitoring remains retryable', async ({ page }) => {
  await page.route('**/api/session', route => route.fulfill({ json: { authenticated: true } }));
  await page.route('**/api/overview', route => route.fulfill({ json: {
    health: { status: 'offline', provider: 'Apache Polaris' }, databases: [], users: [], teams: [],
  } }));
  await page.route('**/api/infrastructure', route => route.fulfill({ status: 503, json: { error: 'Monitoring is not configured.' } }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Infrastructure', exact: true }).click();
  await expect(page.locator('#monitor-error')).toContainText('not configured');
  await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled();
  await expect(page.locator('#monitor-content')).toHaveAttribute('aria-busy', 'false');
});
