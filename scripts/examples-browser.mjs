import { chromium, expect } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
let input = ''; for await (const chunk of process.stdin) input += chunk;
const data = JSON.parse(input);
const browser = await chromium.launch({headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined});
let page;
// Docker interface events on the runner can still abort the editor's module
// graph with ERR_NETWORK_CHANGED; reload the notebook frame once, then fail.
async function editor() {
  const frame = page.frameLocator('#frame-host iframe');
  const cell = frame.locator('.cm-content').first();
  try { await cell.waitFor({timeout: 20000}); } catch {
    console.error('Notebook editor did not load; reloading its frame once');
    await page.locator('#frame-host iframe').evaluate(iframe => { iframe.src = iframe.src; });
    await cell.waitFor();
  }
  await cell.click();
  return frame;
}
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
  await page.locator('#workspace-nav').getByRole('button', {name: 'Notebooks', exact: true}).click();
  await page.locator('#notebook-databases button').filter({hasText: data.databaseName}).click();
  await page.locator('#notebook-file').selectOption({label: '01 · Neighborhood data with PyIceberg'});
  // Reuse the runtime provisioned before Chromium launched, keeping Docker's
  // network topology stable while the editor's module graph loads.
  await expect(page.locator('#frame-host iframe')).toHaveAttribute('src', data.notebook.examples[0].url);
  let frame = await editor();
  await page.keyboard.press('Control+Shift+r');
  await frame.getByText(/(Created and populated:|Table already contains) 26,880 rows/).waitFor({timeout:60000});
  // Run the write cell explicitly: the global shortcut only runs stale cells.
  const writeCell = frame.locator('.cm-content').filter({hasText: '_catalog = connect()'});
  const writeButton = writeCell.locator('xpath=ancestor::*[.//*[@data-testid="run-button"]][1]')
    .locator('[data-testid="run-button"]').first();
  await writeCell.hover();
  await expect(writeButton).toBeEnabled();
  await writeButton.click();
  await frame.getByText('Table already contains 26,880 rows; append skipped.', {exact: true}).waitFor({timeout:60000});
  await mkdir('test-results/examples', {recursive:true});
  await page.screenshot({path:'test-results/examples/pyiceberg.png',fullPage:true});
  await page.locator('#notebook-file').selectOption({label:'02 · Visualize with DuckDB'});
  frame = await editor();
  await page.keyboard.press('Control+Shift+r');
  await frame.getByText('Explore the neighborhood', {exact:true}).first().waitFor({timeout:60000});
  await frame.getByText('Totals by street', {exact:true}).first().waitFor({timeout:60000});
  const plotSources = async () => frame.locator('img[src^="data:image"]').evaluateAll(images => [...new Set(images.filter(i => i.naturalWidth >= 800).map(i => i.src))]);
  await expect.poll(async () => (await plotSources()).length, {timeout:30000}).toBe(2);
  await page.screenshot({path:'test-results/examples/duckdb.png',fullPage:true});
  // The Iceberg v3 examples have no controls: running all cells completes them.
  for (const [label, text] of [['04 · Write Iceberg v3 with DuckDB', /deleted 45 fault events/], ['05 · Read Iceberg v3 with DuckDB', /Every write is a snapshot/]]) {
    await page.locator('#notebook-file').selectOption({label});
    frame = await editor();
    await page.keyboard.press('Control+Shift+r');
    await frame.getByText(text).first().waitFor({timeout:90000});
  }
  await frame.getByText('puffin', {exact:true}).first().waitFor({timeout:60000});
  await page.screenshot({path:'test-results/examples/iceberg-v3.png',fullPage:true});
  console.log('PASS: bundled examples open, linear/idempotent PyIceberg write, DuckDB SQL, 2 static plots and Iceberg v3 write/read');
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
