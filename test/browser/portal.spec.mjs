import { test, expect } from '@playwright/test';

const headers = { 'X-Portal-Request': '1', 'Content-Type': 'application/json' };

test('central teams, memberships, move and deletion through FastAPI', async ({ page, context }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const suffix = Date.now().toString(36);
  const first = `browser-first-${suffix}`, second = `browser-second-${suffix}`;
  const renamed = `browser-renamed-${suffix}`, database = `browser_${suffix}`, username = `browser-user-${suffix}`;
  const request = context.request;
  try {
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Welcome to Iceberg' })).toBeVisible();
    await page.getByLabel('Admin password').fill(process.env.PORTAL_PASSWORD);
    await page.getByRole('button', { name: 'Open workspace' }).click();
    await expect(page.getByRole('heading', { name: 'Databases.' })).toBeVisible();
    await page.getByRole('button', { name: 'Teams', exact: true }).click();
    for (const name of [first, second]) {
      await page.getByRole('button', { name: 'Create team', exact: true }).click();
      await page.getByLabel('Team name').fill(name);
      await page.getByRole('dialog').getByRole('button', { name: 'Create team' }).click();
      await expect(page.getByRole('dialog')).not.toBeVisible();
    }
    const firstRow = page.locator('tbody tr').filter({ hasText: first });
    await firstRow.getByRole('button', { name: 'Edit', exact: true }).click();
    await page.getByLabel('Team name').fill(renamed);
    await page.getByLabel('Description').fill('Centrally managed browser team');
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(page.getByRole('dialog')).not.toBeVisible();
    await expect(page.locator('tbody')).toContainText(renamed);
    await page.screenshot({ path: 'test-results/teams-fastapi.png', fullPage: true });

    await page.getByRole('button', { name: 'Databases', exact: false }).first().click();
    await page.getByRole('button', { name: 'Create database', exact: true }).click();
    await page.getByLabel('Database name').fill(database);
    await page.getByRole('combobox', { name: 'Team', exact: true }).selectOption({ label: renamed });
    await page.getByRole('dialog').getByRole('button', { name: 'Create database' }).click();
    await expect(page.getByRole('dialog')).not.toBeVisible();
    await page.getByRole('textbox', { name: 'Search' }).fill(database);
    await page.getByRole('button', { name: `Connect to ${database}` }).click();
    await expect(page.locator('pre')).toContainText('http://localhost:8181/api/catalog');
    await page.getByRole('button', { name: 'Add user' }).click();
    await page.getByLabel('Username').fill(username);
    await expect(page.getByLabel(renamed, { exact: true })).toBeChecked();
    await page.getByLabel(renamed, { exact: true }).uncheck();
    await page.getByRole('dialog').getByRole('button', { name: 'Create user' }).click();
    await expect(page.locator('.form-error')).toContainText('Select at least one team');
    await page.getByLabel(renamed, { exact: true }).check();
    await page.getByLabel(second, { exact: true }).check();
    await page.getByRole('combobox', { name: 'Access', exact: true }).selectOption('reader');
    await page.getByRole('dialog').getByRole('button', { name: 'Create user' }).click();
    await expect(page.getByRole('heading', { name: 'Your user is ready' })).toBeVisible();
    await expect(page.locator('.credential code')).toHaveCount(2);
    await page.getByRole('button', { name: 'Saved securely' }).click();

    await page.getByRole('button', { name: 'Users', exact: true }).click();
    const userRow = page.locator('tbody tr').filter({ hasText: username });
    await expect(userRow).toContainText(renamed);
    await expect(userRow).toContainText(second);
    await userRow.getByRole('button', { name: 'Edit role', exact: true }).click();
    await expect(page.getByRole('combobox', { name: 'Access', exact: true })).toHaveValue('reader');
    await page.getByRole('combobox', { name: 'Access', exact: true }).selectOption('writer');
    await page.screenshot({ path: 'test-results/edit-user-role.png', fullPage: true });
    await page.getByRole('dialog').getByRole('button', { name: 'Save', exact: true }).click();
    await expect(page.getByRole('dialog')).not.toBeVisible();
    await expect(userRow).toContainText('Read & write');
    await userRow.getByRole('button', { name: 'Edit role', exact: true }).click();
    await page.getByRole('combobox', { name: 'Access', exact: true }).selectOption('bucket-admin');
    await page.getByRole('dialog').getByRole('button', { name: 'Save', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'S3 access is ready', exact: true })).toBeVisible();
    await expect(page.locator('.credential code')).toHaveCount(2);
    await page.getByRole('button', { name: 'Saved securely' }).click();
    for (const role of ['reader', 'bucket-admin']) {
      await userRow.getByRole('button', { name: 'Edit role', exact: true }).click();
      await page.getByRole('combobox', { name: 'Access', exact: true }).selectOption(role);
      await page.getByRole('dialog').getByRole('button', { name: 'Save', exact: true }).click();
      await expect(page.getByRole('dialog')).not.toBeVisible();
    }
    await expect(userRow).toContainText('Database + bucket administration');
    await userRow.getByRole('button', { name: 'Edit teams' }).click();
    await page.getByLabel(renamed, { exact: true }).uncheck();
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(page.getByRole('dialog')).not.toBeVisible();
    await expect(userRow).not.toContainText(renamed);
    await page.screenshot({ path: 'test-results/users-fastapi.png', fullPage: true });

    await page.getByRole('button', { name: 'Databases', exact: false }).first().click();
    await page.getByRole('textbox', { name: 'Search' }).fill(database);
    await page.getByRole('button', { name: 'Move', exact: true }).click();
    await page.getByRole('combobox', { name: 'Team', exact: true }).selectOption({ label: second });
    await page.getByRole('dialog').getByRole('button', { name: 'Move' }).click();
    await expect(page.getByRole('dialog')).not.toBeVisible();
    await expect(page.locator('tbody tr')).toContainText(second);
    await page.getByRole('button', { name: 'Delete', exact: true }).click();
    await expect(page.getByRole('dialog')).toContainText('permanently deleted');
    await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click();
    await expect(page.getByRole('dialog')).not.toBeVisible();
    await expect(page.locator('tbody tr').filter({ hasText: database })).toHaveCount(0);

    await page.getByRole('button', { name: 'Teams', exact: true }).click();
    const secondRow = page.locator('tbody tr').filter({ hasText: second });
    await secondRow.getByRole('button', { name: 'Delete' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click();
    await expect(page.locator('.form-error')).toContainText('last team');
    await page.getByRole('button', { name: 'Cancel' }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: 'test-results/teams-mobile-fastapi.png', fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.getByRole('button', { name: 'Users', exact: true }).click();
    await page.locator('tbody tr').filter({ hasText: username }).getByRole('button', { name: 'Revoke' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click();
    await expect(page.getByRole('dialog')).not.toBeVisible();
    await page.getByRole('button', { name: 'Teams', exact: true }).click();
    for (const name of [renamed, second]) {
      await page.locator('tbody tr').filter({ hasText: name }).getByRole('button', { name: 'Delete' }).click();
      await page.getByRole('dialog').getByRole('button', { name: 'Delete' }).click();
      await expect(page.getByRole('dialog')).not.toBeVisible();
    }
    expect(errors).toEqual([]);
  } finally {
    // Clean only resources with this test's unique names, even after an assertion fails.
    await request.post('/api/session', { headers, data: { password: process.env.PORTAL_PASSWORD } });
    const overview = await (await request.get('/api/overview')).json();
    for (const u of overview.users.filter(u => u.name === username)) await request.delete(`/api/users/${u.id}`, { headers });
    for (const d of overview.databases.filter(d => d.name === database)) await request.delete(`/api/databases/${d.id}`, { headers });
    for (const t of overview.teams.filter(t => [first, second, renamed].includes(t.name))) await request.delete(`/api/teams/${t.id}`, { headers });
  }
});
