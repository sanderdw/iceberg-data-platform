// A notebook on Kubernetes, end to end: browser sign-in as demo-writer, start a notebook pod, open it
// through the portal's proxy, and from inside the pod renew the session token and read the catalog.
//   node dev/kubernetes/notebook_smoke.mjs <namespace> <users origin>
// Needs kubectl, the chart installed with demo=true, and Playwright's Chromium.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { chromium } from '@playwright/test';

const [namespace = 'iceberg', usersURL] = process.argv.slice(2);
assert.ok(usersURL, 'Pass the user portal origin, such as https://users.example.com');
const kubectl = (...args) => execFileSync('kubectl', ['-n', namespace, ...args], {encoding: 'utf8'});
const password = Buffer.from(kubectl('get', 'secret', 'iceberg-platform', '-o',
  'jsonpath={.data.DEMO_WRITER_PASSWORD}'), 'base64').toString();
const headers = {'X-Portal-Request': '1', 'Content-Type': 'application/json'};
const browser = await chromium.launch({headless: true});
let context, id;
try {
  context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  await page.goto(usersURL);
  await page.getByRole('link', {name: 'Sign in with Keycloak'}).click();
  await page.locator('#username').fill('demo-writer');
  await page.locator('#password').fill(password);
  await page.locator('#kc-login').click();
  await page.waitForURL(url => url.origin === usersURL);
  const workspace = await (await context.request.get(usersURL + '/api/workspace')).json();
  const database = workspace.databases.find(d => d.name === 'demo-demo').id;
  console.log('PASS: demo-writer signed in to the user portal over HTTPS');

  const started = Date.now();
  const response = await context.request.post(usersURL + '/api/notebooks', {headers, data: {database}, timeout: 300000});
  assert.equal(response.status(), 201, await response.text());
  const notebook = await response.json();
  id = notebook.id;
  console.log(`PASS: notebook pod started in ${Math.round((Date.now() - started) / 1000)} s`);
  const editor = await context.request.get(usersURL + notebook.url);
  assert.equal(editor.status(), 200);
  console.log('PASS: the portal proxies the marimo editor');

  const pod = `iceberg-workspaces-marimo-${id}`;
  const result = execFileSync('kubectl', ['-n', `${namespace}-notebooks`, 'exec', '-i', pod, '--',
    '/app/.venv/bin/python', '-'], {encoding: 'utf8', timeout: 120000, input: `
import json, os, urllib.request
request = urllib.request.Request(os.environ["ICEBERG_SESSION_TOKEN_URL"],
                                 headers={"Authorization": "Bearer " + os.environ["MARIMO_GATEWAY_TOKEN"]})
token = json.load(urllib.request.urlopen(request, timeout=20))["access_token"]
print("PASS: the pod renewed its session token at the portal")
from pyiceberg.catalog import load_catalog
catalog = load_catalog("iceberg", type="rest", uri=os.environ["ICEBERG_CATALOG_URI"], token=token,
                       warehouse=os.environ["ICEBERG_DATABASE"], **{"s3.endpoint": os.environ["ICEBERG_S3_ENDPOINT"]})
print("PASS: the pod lists the catalog:", len(catalog.list_namespaces()), "namespaces")
`});
  process.stdout.write(result);
  assert.match(result, /lists the catalog/);
} finally {
  if (context && id) await context.request.delete(usersURL + `/api/notebooks/${id}`, {headers});
  await browser.close();
}
