import { test, expect } from '@playwright/test';

const headers = { 'X-Portal-Request': '1', 'Content-Type': 'application/json' };

test('portal admin explores namespaces, tables and views; database credentials are denied', async ({ page, context, request }) => {
  const suffix = Date.now().toString(36);
  const name = `explorer_${suffix}`;
  const api = context.request;
  let team, database, user;
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  async function create(path, data) {
    const response = await api.post(`/api/${path}`, { headers, data });
    expect(response.status()).toBe(201);
    return response.json();
  }
  try {
    expect((await request.get('/api/admin/explorer/databases')).status()).toBe(401);
    await api.post('/api/session', { headers, data: { password: process.env.PORTAL_PASSWORD } });
    team = await create('teams', { name: `explorer-team-${suffix}` });
    database = await create('databases', { name, team: team.id });
    const account = await create('users', { name: `explorer-user-${suffix}`, teams: [team.id], role: 'admin' });
    user = account.user;
    const oauth = await request.post(`${process.env.POLARIS_URL}/api/catalog/v1/oauth/tokens`, { form: {
      grant_type: 'client_credentials', scope: 'PRINCIPAL_ROLE:ALL', client_id: account.credentials.clientId, client_secret: account.credentials.clientSecret,
    } });
    expect(oauth.status()).toBe(200);
    const token = (await oauth.json()).access_token;
    expect((await request.get('/api/admin/explorer/databases', { headers: { Authorization: `Bearer ${token}` } })).status()).toBe(401);
    const catalog = `${process.env.POLARIS_URL}/api/catalog/v1/${name}`;
    const catalogHeaders = { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' };
    async function catalogPost(path, data) {
      const response = await request.post(catalog + path, { headers: catalogHeaders, data });
      expect(response.ok()).toBe(true);
    }
    await catalogPost('/namespaces', { namespace: ['analytics'], properties: {} });
    await catalogPost('/namespaces', { namespace: ['analytics', 'nested'], properties: {} });
    await catalogPost('/namespaces', { namespace: ['empty'], properties: {} });
    const schema = { type: 'struct', 'schema-id': 0, fields: [{ id: 1, name: 'id', required: true, type: 'long' }] };
    const nested = `/namespaces/${encodeURIComponent('analytics\x1fnested')}`;
    await catalogPost(`${nested}/tables`, { name: 'events', schema });
    await catalogPost(`${nested}/views`, { name: 'event_report', schema, 'view-version': {
      'version-id': 1, 'schema-id': 0, 'timestamp-ms': Date.now(), summary: {}, 'default-namespace': ['analytics', 'nested'],
      representations: [{ type: 'sql', sql: 'SELECT id FROM events', dialect: 'spark' }],
    } });

    await page.goto('/');
    await page.getByRole('button', { name: 'Catalog', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Catalog.' })).toBeVisible();
    await expect(page.getByText('Portal administrator · read only')).toBeVisible();
    await page.getByRole('textbox', { name: 'Search databases' }).fill(name);
    await expect(page.locator('#explorer-tree > details:visible')).toHaveCount(1);
    // A failed branch stays retryable, without presenting a partial listing as complete.
    await page.route('**/api/admin/explorer/contents?*', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'Temporarily unavailable.' }) }), { times: 1 });
    await page.locator('summary strong').getByText(name, { exact: true }).click();
    await expect(page.getByRole('alert')).toContainText('Temporarily unavailable');
    await page.getByRole('button', { name: 'Try again' }).click();
    await page.locator('summary strong').getByText('analytics', { exact: true }).click();
    await page.locator('summary strong').getByText('nested', { exact: true }).click();
    await expect(page.locator('.explorer-object').filter({ hasText: 'events' })).toContainText('Table');
    await expect(page.locator('.explorer-object').filter({ hasText: 'event_report' })).toContainText('View');
    await page.locator('summary strong').getByText('empty', { exact: true }).click();
    await expect(page.getByText('This namespace is empty.')).toBeVisible();
    await page.screenshot({ path: 'test-results/explorer-desktop.png', fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator('.explorer-object').filter({ hasText: 'event_report' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: 'test-results/explorer-mobile.png', fullPage: true });
    await page.getByRole('textbox', { name: 'Search databases' }).fill('no-matching-database');
    await expect(page.getByText('No databases match this search.')).toBeVisible();
    await page.getByRole('button', { name: 'Refresh catalog' }).click();
    await expect(page.locator('summary strong').getByText(name, { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Sign out', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Welcome to Iceberg' })).toBeVisible();
    expect((await api.get(`/api/admin/explorer/contents?database=${name}`)).status()).toBe(401);
    expect(errors).toEqual([]);
  } finally {
    await api.post('/api/session', { headers, data: { password: process.env.PORTAL_PASSWORD } });
    if (user) expect((await api.delete(`/api/users/${user.id}`, { headers })).ok()).toBe(true);
    if (database) expect((await api.delete(`/api/databases/${database.id}`, { headers })).ok()).toBe(true);
    if (team) expect((await api.delete(`/api/teams/${team.id}`, { headers })).ok()).toBe(true);
  }
});
