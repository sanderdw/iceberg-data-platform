import { chromium, expect } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
let input = ''; for await (const chunk of process.stdin) input += chunk;
const data = JSON.parse(input);
const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined});
let page;
try {
  const context = await browser.newContext({viewport: {width: 1500, height: 1100}});
  await context.addCookies([{name: 'iceberg_user_session', value: data.cookie, url: data.baseURL, httpOnly: true, sameSite: 'Strict'}]);
  page = await context.newPage(); page.setDefaultTimeout(30000);
  page.on('pageerror', error => console.error('Browser error:', error.message));
  page.on('console', message => { if (message.type() === 'error') console.error('Browser console:', message.text()); });
  // Keep credentials and query tokens out of network diagnostics.
  page.on('requestfailed', request => console.error('Request failed:', request.method(), new URL(request.url()).pathname, request.failure()?.errorText));
  page.on('response', response => { if (response.status() >= 400) console.error('HTTP error:', response.status(), new URL(response.url()).pathname); });
  await page.goto(data.baseURL);
  await page.locator('#databases button').filter({hasText: data.databaseName}).click();
  await page.locator('#example-write').click();
  // Reuse the runtime provisioned before Chromium launched, keeping Docker's
  // network topology stable while the editor's module graph loads.
  await expect(page.locator('#frame-host iframe')).toHaveAttribute('src', data.notebook.examples[0].url);
  let frame = page.frameLocator('#frame-host iframe');
  await frame.locator('.cm-content').first().waitFor();
  await frame.locator('.cm-content').first().click();
  await page.keyboard.press('Control+Shift+r');
  await frame.getByRole('button', {name: 'Create example table', exact: true}).waitFor();
  // Merely executing all cells must not write to Iceberg.
  const query = new URLSearchParams({database: data.database});
  const before = await context.request.get(new URL(`/api/contents?${query}`, data.baseURL).href);
  if ((await before.json()).namespaces.some(ns => ns[0] === 'synthetic')) throw new Error('Notebook wrote before explicit button click');
  await frame.getByRole('button', {name: 'Create example table', exact: true}).click();
  await frame.getByText('Created and populated: 26,880 rows.', {exact: true}).waitFor({timeout:60000});
  await frame.getByRole('button', {name: 'Create example table', exact: true}).click();
  await frame.getByText('Table already contains 26,880 rows; append skipped.', {exact: true}).waitFor({timeout:60000});
  await mkdir('test-results/examples', {recursive:true});
  await page.screenshot({path:'test-results/examples/pyiceberg.png',fullPage:true});
  await page.locator('#notebook-file').selectOption({label:'02 · Visualize with DuckDB'});
  frame = page.frameLocator('#frame-host iframe');
  await frame.locator('.cm-content').first().waitFor();
  await frame.locator('.cm-content').first().click();
  await page.keyboard.press('Control+Shift+r');
  await frame.getByText('Explore the neighborhood', {exact:true}).first().waitFor({timeout:60000});
  await frame.getByText('Totals by street', {exact:true}).first().waitFor({timeout:60000});
  const plotSources = async () => frame.locator('img[src^="data:image"]').evaluateAll(images => [...new Set(images.filter(i => i.naturalWidth >= 800).map(i => i.src))]);
  await expect.poll(async () => (await plotSources()).length, {timeout:30000}).toBe(2);
  // Native marimo dropdown drives the SQL dependency graph and the plot.
  const originalPlots = await plotSources();
  const dropdown = frame.locator('select:visible:has(option[value="Example Solar Street"])').first();
  await dropdown.selectOption({label:'Example Solar Street'});
  await expect(dropdown).toHaveValue('Example Solar Street');
  await expect.poll(async () => (await plotSources()).some(source => !originalPlots.includes(source)), {timeout:30000}).toBe(true);
  await page.screenshot({path:'test-results/examples/duckdb.png',fullPage:true});
  // The Iceberg v3 examples have no controls: running all cells completes them.
  for (const [label, text] of [['04 · Write Iceberg v3 with DuckDB', /deleted 45 fault events/], ['05 · Read Iceberg v3 with DuckDB', /Every write is a snapshot/]]) {
    await page.locator('#notebook-file').selectOption({label});
    frame = page.frameLocator('#frame-host iframe');
    await frame.locator('.cm-content').first().waitFor();
    await frame.locator('.cm-content').first().click();
    await page.keyboard.press('Control+Shift+r');
    await frame.getByText(text).first().waitFor({timeout:90000});
  }
  await frame.getByText('puffin', {exact:true}).first().waitFor({timeout:60000});
  await page.screenshot({path:'test-results/examples/iceberg-v3.png',fullPage:true});
  console.log('PASS: bundled examples open, explicit/idempotent PyIceberg write, DuckDB SQL, 2 plots, reactive street filter and Iceberg v3 write/read');
  await context.close();
} catch (error) {
  if (page) {
    await mkdir('test-results/examples', {recursive:true});
    await page.screenshot({path:'test-results/examples/failure.png',fullPage:true});
    for (const f of page.frames()) {
      console.error('Frame:', new URL(f.url()).pathname);
      try {
        console.error((await f.locator('body').innerText({timeout: 5000})).slice(-3000), await f.locator('select, [role=combobox]').evaluateAll(nodes => nodes.map(n => n.outerHTML.slice(0, 1800))));
      } catch (diagnosticError) { console.error('Could not inspect frame:', diagnosticError.message); }
    }
  }
  throw error;
} finally { await browser.close(); }
