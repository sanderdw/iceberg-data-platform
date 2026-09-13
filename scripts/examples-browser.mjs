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
  await page.goto(data.baseURL);
  await page.locator('#databases button').filter({hasText: data.databaseName}).click();
  await page.locator('#example-write').click();
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
  console.log('PASS: bundled examples open, explicit/idempotent PyIceberg write, DuckDB SQL, 2 plots and reactive street filter');
  await context.close();
} catch (error) {
  if (page) {
    await mkdir('test-results/examples', {recursive:true});
    await page.screenshot({path:'test-results/examples/failure.png',fullPage:true});
    for (const f of page.frames()) console.error((await f.locator('body').innerText()).slice(-3000), await f.locator('select, [role=combobox]').evaluateAll(nodes => nodes.map(n => n.outerHTML.slice(0, 1800))));
  }
  throw error;
} finally { await browser.close(); }
