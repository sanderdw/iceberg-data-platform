// Exercises the account management UI with a disposable account; never logs passwords or tokens.
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {randomBytes} from 'node:crypto';
import {chromium, expect} from '@playwright/test';

const env = Object.fromEntries(readFileSync('.env', 'utf8').split('\n')
  .filter(line => line && !line.startsWith('#')).map(line => {
    const split = line.indexOf('='); return [line.slice(0, split), line.slice(split + 1)];
  }));
const adminURL = env.PORTAL_ORIGIN, usersURL = env.USER_ORIGIN;
const prefix = 'demo';
const project = 'iceberg-workspaces';
const kcURL = env.KEYCLOAK_ORIGIN;
const headers = {'X-Portal-Request': '1', 'Content-Type': 'application/json'};
const username = 'lifecycle-' + randomBytes(6).toString('hex');
const password = randomBytes(24).toString('base64url');
const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined});
const admin = await browser.newContext();
let principalId, subject, notebookId;

async function signin(context, base, name, secret, changePassword = false) {
  const page = await context.newPage(); page.setDefaultTimeout(20000);
  await page.goto(base + '/auth/login');
  await page.locator('#username').fill(name);
  await page.locator('#password').fill(secret);
  await page.locator('#kc-login').click();
  if (changePassword) {
    await page.locator('#password-new').fill(password);
    await page.locator('#password-confirm').fill(password);
    await page.locator('input[type=submit], button[type=submit]').click();
  }
  await page.waitForURL(url => url.origin === base);
  return page;
}

async function checked(response, status = 200) {
  assert.equal(response.status(), status, 'Expected successful platform API request');
  return response.json();
}

async function revoke(page, id) {
  await page.locator(`[data-delete="${id}"]`).click();
  await page.locator('#modal-form button[type=submit]').click();
  await expect(page.locator('#modal')).not.toBeVisible();
  principalId = null;
}

try {
  const page = await signin(admin, adminURL, prefix + '-admin', env.DEMO_ADMIN_PASSWORD);
  const overview = await checked(await admin.request.get(adminURL + '/api/overview'));
  const team = overview.teams.find(t => t.name === prefix + '-team').id;
  const database = overview.databases.find(d => d.name === prefix + '-demo').id;
  await page.locator('[data-page=users]').click();
  await page.locator('#create').click();
  await page.locator('[name=name]').fill(username);
  await page.locator('[name=first_name]').fill('Lifecycle');
  await page.locator('[name=last_name]').fill('Test');
  await page.locator('[name=email]').fill(username + '@example.test');
  await page.locator(`[name=teams][value="${team}"]`).check();
  await page.locator(`[name="role:${team}"]`).selectOption('writer');
  await page.locator('#modal-form button[type=submit]').click();
  await expect(page.locator('#temporary-password')).toBeVisible();
  const temporary = await page.locator('#temporary-password').innerText();
  await page.locator('#identity-done').click();
  const created = (await checked(await admin.request.get(adminURL + '/api/users'))).find(u => u.name === username);
  principalId = created.id; subject = created.identity.subject;
  assert.equal(created.identity.status, 'linked');
  console.log('PASS: portal creates a linked Keycloak user and displays a temporary password');

  const user = await browser.newContext();
  await signin(user, usersURL, username, temporary, true);
  const workspace = await checked(await user.request.get(usersURL + '/api/workspace'));
  assert.deepEqual(workspace.databases.map(d => d.id), [database]);
  const notebook = await checked(await user.request.post(usersURL + '/api/notebooks', {headers, data: {database}}), 201);
  notebookId = notebook.id;
  assert.match(notebookId, /^[a-f0-9]{32}$/);
  const output = execFileSync('docker', ['exec', '-i', project + '-marimo-' + notebookId,
    '/app/.venv/bin/python', '-'], {encoding: 'utf8', timeout: 60000, stdio: ['pipe', 'pipe', 'pipe'], input:
      "from user_portal.notebook.connection import connect\nc = connect()\nc.list_namespaces()\nprint('PASS')\n"});
  assert.match(output, /PASS/);
  console.log('PASS: first login requires a password change; new user opens a notebook with Polaris access');

  await page.locator(`[data-memberships="${principalId}"]`).click();
  await page.locator(`[name="role:${team}"]`).selectOption('reader');
  await page.locator('#modal-form button[type=submit]').click();
  await expect(page.locator('#modal')).not.toBeVisible();
  assert.equal((await checked(await admin.request.get(adminURL + '/api/users'))).find(u => u.id === principalId).memberships.find(m => m.team === team).role, 'reader');
  await revoke(page, principalId);
  const accounts = await checked(await admin.request.get(adminURL + '/api/identity/accounts?username=' + username));
  assert.equal(accounts[0].id, subject);
  assert.equal(accounts[0].enabled, true);
  assert.equal(accounts[0].linked, false);
  const denied = await user.request.get(usersURL + '/api/workspace');
  assert.ok([401, 403].includes(denied.status()));
  await expect.poll(() => execFileSync('docker', ['ps', '--filter', 'name=' + project + '-marimo-' + notebookId,
    '--format', '{{.Names}}'], {encoding: 'utf8'}).trim(), {timeout: 45000, intervals: [1000]}).toBe('');
  notebookId = null;
  console.log('PASS: revoke removes platform access and stops the notebook while preserving the Keycloak account');

  await page.locator('#create').click();
  await page.locator('#identity-link').click();
  await page.locator('[name=name]').fill(username + '-linked');
  await page.locator('#identity-search').fill(username);
  await page.locator('#find-identity').click();
  await expect(page.locator(`#identity-account option[value="${subject}"]`)).toBeAttached();
  await page.locator('#identity-account').selectOption(subject);
  await page.locator(`[name=teams][value="${team}"]`).check();
  await page.locator(`[name="role:${team}"]`).selectOption('reader');
  await page.locator('#modal-form button[type=submit]').click();
  await expect(page.locator('#identity-done')).toBeVisible();
  assert.equal(await page.locator('#temporary-password').count(), 0);
  await page.locator('#identity-done').click();
  principalId = (await checked(await admin.request.get(adminURL + '/api/users'))).find(u => u.name === username + '-linked').id;
  const linked = await browser.newContext();
  await signin(linked, usersURL, username, password);
  assert.equal((await linked.request.get(usersURL + '/api/workspace')).status(), 200);
  await revoke(page, principalId);
  console.log('PASS: existing account can be explicitly linked again with its unchanged password');
} finally {
  // Only resources created by this invocation are removed. Production Revoke
  // preserves Keycloak accounts; this test deletes its own disposable fixture.
  try {
    const usersResponse = await admin.request.get(adminURL + '/api/users');
    if (usersResponse.ok()) {
      for (const user of await usersResponse.json()) {
        if (![username, username + '-linked'].includes(user.name)) continue;
        subject ||= user.identity?.subject;
        const response = await admin.request.delete(adminURL + '/api/users/' + user.id, {headers, data: {}});
        assert.equal(response.status(), 200, 'Test account platform cleanup must succeed');
      }
    }
    const tokenResponse = await admin.request.post(kcURL + '/realms/master/protocol/openid-connect/token', {
      form: {grant_type: 'password', client_id: 'admin-cli', username: 'admin', password: env.KEYCLOAK_ADMIN_PASSWORD},
    });
    const token = (await checked(tokenResponse)).access_token;
    const authorization = {Authorization: 'Bearer ' + token};
    const accounts = await checked(await admin.request.get(kcURL + '/admin/realms/iceberg/users', {
      headers: authorization, params: {username, exact: 'true'},
    }));
    for (const account of accounts) {
      assert.equal(account.username, username);
      assert.equal(account.email, username + '@example.test');
      const response = await admin.request.delete(kcURL + '/admin/realms/iceberg/users/' + account.id, {headers: authorization});
      assert.equal(response.status(), 204);
    }
  } finally { await browser.close(); }
}
