import { test, expect } from '@playwright/test';

const headers = { 'X-Portal-Request': '1', 'Content-Type': 'application/json' };

// Shares are created in the user portal. The administration page only lists and revokes them,
// so the overview is extended with one share and the revoke request is observed.
test('portal admin reviews and revokes data shares', async ({ page, context }) => {
  const id = 'share-' + 'a'.repeat(32), recipient = '<img src=x onerror=alert(1)> Partner BV';
  const errors = [], revoked = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('dialog', dialog => { errors.push('dialog: ' + dialog.message()); dialog.dismiss(); });
  await context.request.post('/api/session', { headers, data: { password: process.env.PORTAL_PASSWORD } });
  await page.route('**/api/overview', async route => {
    const response = await route.fetch(), overview = await response.json();
    const shares = revoked.length ? [] : [{ id, name: 'partner', recipient, description: '', database: 'db-' + 'b'.repeat(32), objects: [{ kind: 'table', namespace: ['sales', 'eu'], name: 'orders' }, { kind: 'view', namespace: ['sales'], name: 'report' }], expiresAt: '2031-05-01T23:59:59+00:00', createdAt: 1789506000000, createdBy: 'team-admin', clientId: 'client-1', status: 'active' }];
    await route.fulfill({ response, json: { ...overview, shares } });
  });
  await page.route('**/api/shares/*', async route => {
    const request = route.request();
    if (request.method() !== 'DELETE' || request.headers()['x-portal-request'] !== '1') throw new Error('Unexpected share request');
    revoked.push(new URL(request.url()).pathname);
    await route.fulfill({ json: { deleted: true } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Data shares', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Data shares.', exact: true })).toBeVisible();
  const row = page.locator('#share-table tbody tr');
  await expect(row).toContainText(recipient);
  await expect(row.locator('img')).toHaveCount(0);
  await expect(row).toContainText('sales.eu.orders');
  await expect(row).toContainText('sales.report');
  await expect(row).toContainText('1 May 2031');
  await expect(page.getByRole('button', { name: /^Create/ })).toHaveCount(0);
  await page.getByPlaceholder('Search shares…').fill('nothing');
  await expect(page.locator('#share-table')).toContainText('No results found');
  await page.getByPlaceholder('Search shares…').fill('partner');
  await page.getByRole('button', { name: 'Revoke', exact: true }).click();
  await expect(page.locator('dialog, .modal').first()).toContainText('stops working immediately');
  expect(revoked).toEqual([]);
  await page.getByRole('button', { name: 'Revoke', exact: true }).last().click();
  await expect(page.locator('#share-table')).toContainText('[ NO DATA SHARES ]');
  expect(revoked).toEqual([`/api/shares/${id}`]);
  expect(errors).toEqual([]);
});
