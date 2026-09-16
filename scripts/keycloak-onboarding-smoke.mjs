// Fresh, disposable stacks only: replaces the initial administrator password.
import assert from 'node:assert/strict';
import {randomBytes} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {parseEnv} from 'node:util';
import {chromium, expect} from '@playwright/test';

const env = parseEnv(readFileSync('.env', 'utf8'));
const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined});
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(env.PORTAL_ORIGIN + '/auth/login');
  await page.locator('#username').fill(env.PLATFORM_ADMIN_USERNAME);
  await page.locator('#password').fill(env.PLATFORM_ADMIN_PASSWORD);
  await page.locator('#kc-login').click();
  const password = randomBytes(24).toString('base64url');
  await page.locator('#password-new').fill(password);
  await page.locator('#password-confirm').fill(password);
  await page.locator('input[type=submit], button[type=submit]').click();
  await page.waitForURL(env.PORTAL_ORIGIN + '/');
  const response = await context.request.get(env.PORTAL_ORIGIN + '/api/overview');
  assert.equal(response.status(), 200);
  const overview = await response.json();
  assert.equal(overview.users.length, 0, 'Ordinary bootstrap must not seed platform users');
  assert.equal(overview.teams.length, 0, 'Ordinary bootstrap must not seed teams');
  assert.equal(overview.databases.length, 0, 'Ordinary bootstrap must not seed databases');
  await expect.poll(async () => (await context.request.get(env.PORTAL_ORIGIN + '/api/infrastructure')).status()).toBe(200);
  await page.goto(env.USER_ORIGIN + '/auth/login');
  await page.waitForURL(url => url.origin === env.USER_ORIGIN);
  const session = await context.request.get(env.USER_ORIGIN + '/api/session');
  assert.equal((await session.json()).authenticated, false, 'Portal administration must not confer data access');
  console.log('PASS: fresh administrator changes password, opens an empty platform and Infrastructure; data access requires an explicit link');
} finally {
  await browser.close();
}
