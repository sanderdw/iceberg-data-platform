// Real browser OIDC and per-user notebook data access. No tokens in output or arguments.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { chromium } from '@playwright/test';

const env = Object.fromEntries(readFileSync('.env', 'utf8').split('\n')
  .filter(line => line && !line.startsWith('#')).map(line => {
    const split = line.indexOf('='); return [line.slice(0, split), line.slice(split + 1)];
  }));
const adminURL = env.PORTAL_ORIGIN, usersURL = env.USER_ORIGIN;
const prefix = 'demo';
const project = 'iceberg-workspaces';
const headers = {'X-Portal-Request': '1', 'Content-Type': 'application/json'};
const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined});
const contexts = [], notebooks = [];
const namespace = 'oidc_smoke_' + Date.now();

async function signin(account, baseURL) {
  const context = await browser.newContext(); contexts.push(context);
  const page = await context.newPage(); page.setDefaultTimeout(20000);
  await page.goto(baseURL);
  await page.getByRole('link', {name: 'Sign in with Keycloak'}).click();
  await page.locator('#username').fill(prefix + '-' + account);
  await page.locator('#password').fill(env[`DEMO_${account.toUpperCase()}_PASSWORD`]);
  await page.locator('#kc-login').click();
  await page.waitForURL(url => url.origin === baseURL);
  return {context, page};
}

async function notebook(context, database) {
  const response = await context.request.post(usersURL + '/api/notebooks', {headers, data: {database}});
  assert.equal(response.status(), 201, 'Notebook must start with user identity');
  const result = await response.json(); notebooks.push([context, result.id]);
  return result;
}

function execute(id, code) {
  assert.match(id, /^[a-f0-9]{32}$/);
  const result = execFileSync('docker', ['exec', '-i', `${project}-marimo-${id}`,
    '/app/.venv/bin/python', '-'], {input: code, encoding: 'utf8', timeout: 120000, stdio: ['pipe', 'pipe', 'pipe']});
  assert.match(result, /PASS/);
}

try {
  const admin = await signin('admin', adminURL);
  assert.equal((await admin.context.request.get(adminURL + '/api/overview')).status(), 200);
  const overview = await (await admin.context.request.get(adminURL + '/api/overview')).json();
  const privateDB = overview.databases.find(d => d.name === prefix + '-private').id;
  // Same browser session crosses to the second OIDC client without another password.
  await admin.page.goto(usersURL + '/auth/login');
  await admin.page.waitForURL(usersURL + '/');
  assert.equal((await admin.context.request.get(usersURL + '/api/session')).status(), 200);
  assert.equal((await (await admin.context.request.get(usersURL + '/api/session')).json()).authenticated, true);
  console.log('PASS: admin role and SSO across both portals');

  const writer = await signin('writer', usersURL);
  const workspace = await (await writer.context.request.get(usersURL + '/api/workspace')).json();
  assert.deepEqual(workspace.databases.map(d => d.name), [prefix + '-demo']);
  const database = workspace.databases[0].id;
  const writeNotebook = await notebook(writer.context, database);
  execute(writeNotebook.id, `
import os
import pyarrow as pa
from user_portal.notebook.connection import connect
from user_portal.notebook.duckdb_connection import connect_duckdb
assert os.environ.get('ICEBERG_ACCESS_TOKEN')
assert 'ICEBERG_CLIENT_SECRET' not in os.environ
c = connect()
c.create_namespace('${namespace}')
rows = pa.table({'value': [1, 2, 3]})
t = c.create_table(('${namespace}', 'events'), schema=rows.schema)
t.append(rows)
assert t.scan().to_arrow().num_rows == 3
db = connect_duckdb(['${namespace}'], 'events')
assert db.execute('select sum(value) from lakehouse.${namespace}.events').fetchone()[0] == 6
db.close()
print('PASS')
`);
  console.log('PASS: writer notebook creates and reads Iceberg data through PyIceberg and DuckDB');

  const reader = await signin('reader', usersURL);
  const readNotebook = await notebook(reader.context, database);
  execute(readNotebook.id, `
import os
from pyiceberg.exceptions import ForbiddenError
from user_portal.notebook.connection import connect
from user_portal.notebook.duckdb_connection import connect_duckdb
c = connect()
assert c.load_table(('${namespace}', 'events')).scan().to_arrow().num_rows == 3
try:
    c.create_namespace('${namespace}_denied')
except ForbiddenError:
    pass
else:
    raise AssertionError('Reader obtained write access')
db = connect_duckdb(['${namespace}'], 'events')
assert db.execute('select count(*) from lakehouse.${namespace}.events').fetchone()[0] == 3
db.close()
os.environ['ICEBERG_DATABASE'] = '${privateDB}'
try:
    connect().list_namespaces()
except ForbiddenError:
    pass
else:
    raise AssertionError('Reader obtained access to another team through Polaris')
print('PASS')
`);
  const preview = await reader.context.request.post(usersURL + '/api/preview', {
    headers, data: {database, namespace: [namespace], table: 'events', limit: 3},
  });
  assert.equal(preview.status(), 200, 'Reader table preview must use the Keycloak token');
  assert.equal((await reader.context.request.get(usersURL + '/api/contents?database=' + privateDB)).status(), 403);
  await reader.page.goto(adminURL + '/auth/login');
  await reader.page.waitForURL(url => url.origin === adminURL);
  assert.equal((await reader.context.request.get(adminURL + '/api/teams')).status(), 401);
  console.log('PASS: reader can read and preview; writes, other-team access and admin login are denied');

  const outsider = await signin('outsider', usersURL);
  assert.equal((await outsider.context.request.get(usersURL + '/api/workspace')).status(), 401);
  console.log('PASS: unlinked Keycloak identity is denied');

  execute(writeNotebook.id, `
from user_portal.notebook.connection import connect
c = connect()
c.drop_table(('${namespace}', 'events'))
c.drop_namespace('${namespace}')
print('PASS')
`);
  const oldCookies = await reader.context.cookies();
  const logout = await reader.context.request.delete(usersURL + '/api/session', {headers, data: {}});
  assert.equal(logout.status(), 200);
  await reader.context.addCookies(oldCookies);
  assert.equal((await reader.context.request.get(usersURL + '/api/workspace')).status(), 401);
  const stopped = execFileSync('docker', ['ps', '-q', '--filter', `name=${project}-marimo-${readNotebook.id}`], {encoding: 'utf8'});
  assert.equal(stopped.trim(), '');
  console.log('PASS: local logout invalidates replayed session cookies and stops notebook execution');
  await reader.page.goto((await logout.json()).logoutUrl);
  await reader.page.locator('#kc-logout').click();
  await reader.page.waitForURL(usersURL + '/');
  await reader.page.goto(usersURL + '/auth/login');
  await reader.page.locator('#username').waitFor({state: 'visible'});
  console.log('PASS: confirmed Keycloak logout ends SSO and requires a new login');
} finally {
  for (const [context, id] of notebooks) {
    await context.request.delete(usersURL + '/api/notebooks/' + id, {headers, data: {}}).catch(() => {});
  }
  for (const context of contexts) {
    await context.request.delete(usersURL + '/api/session', {headers, data: {}}).catch(() => {});
    await context.request.delete(adminURL + '/api/session', {headers, data: {}}).catch(() => {});
  }
  await browser.close();
}
