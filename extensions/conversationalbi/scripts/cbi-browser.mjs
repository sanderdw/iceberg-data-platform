// Browser check of the chat: sign in, pick the flights model, ask a question, and verify the
// generative UI (query card, Chart.js canvas, result loaded by id) under the strict CSP.
//
//   BI_URL=http://localhost:3007 node scripts/cbi-browser.mjs                  # tests.dev_server (/dev/login)
//   BI_URL=... BI_USER=demo-writer BI_PASSWORD=... node scripts/cbi-browser.mjs  # a real installation (Keycloak)
//
// The extension must run the scripted model (LLM_MODEL=test:flights) so the answer is deterministic.
import { chromium } from "@playwright/test";
import { mkdir } from "node:fs/promises";

const base = process.env.BI_URL ?? "http://localhost:3007";
const out = process.env.BI_SCREENSHOTS ?? "test-results/conversationalbi";
const shared = process.env.BI_SHARED !== "0";

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const problems = [];
page.on("console", (m) => { if (m.type() === "error") problems.push(m.text()); });
page.on("pageerror", (e) => problems.push(e.message));
// Only requests the chat itself makes count: the Keycloak sign-in page loads its own assets.
const fromApp = (r) => { try { return r.frame().url().startsWith(base); } catch { return false; } };
const external = [];
page.on("request", (r) => {
  if (fromApp(r) && !r.isNavigationRequest() && !/^(data|blob):/.test(r.url()) && !r.url().startsWith(base)) external.push(r.url());
});

try {
  await mkdir(out, { recursive: true });
  if (process.env.BI_USER) {
    await page.goto(base + "/");
    await page.getByRole("button", { name: /Sign in with Keycloak/i }).click();
    await page.fill("#username", process.env.BI_USER);
    await page.fill("#password", process.env.BI_PASSWORD ?? "");
    await page.click("#kc-login");
  } else {
    await page.goto(base + "/dev/login?user=bob");
  }
  await page.locator("nav.models button.db").first().waitFor({ timeout: 30000 });
  const section = shared ? "Shared with your team" : "Team models";
  await page.locator("nav.models .database-group", { hasText: section }).locator("button.db", { hasText: "flights" }).first().click();
  await page.locator("h1", { hasText: "flights" }).waitFor();
  await page.screenshot({ path: `${out}/01-selected.png` });

  const input = page.locator("[data-copilotkit] textarea").first();
  await input.fill("Which carrier has the worst average departure delay?");
  await input.press("Enter");
  await page.locator(".query-card").first().waitFor({ timeout: 60000 });
  await page.locator(".gen-card canvas[role=img]").first().waitFor({ timeout: 60000 });
  await page.getByText(/highest average departure delay/).first().waitFor({ timeout: 60000 });

  const card = await page.locator(".query-card").first().innerText();
  if (!card.includes("average_departure_delay") || !card.includes("CARRIER.name")) throw new Error("The query card misses the query: " + card);
  if (!/DATA AS OF \d{2} [A-Z]{3} \d{4} \d{2}:\d{2} UTC/.test(card)) throw new Error("The query card does not date its data: " + card);
  await page.locator(".query-card summary", { hasText: "SQL" }).first().click();
  const sql = await page.locator(".query-card pre").first().innerText();
  if (!/LEFT JOIN "CARRIER"/.test(sql)) throw new Error("The SQL does not join CARRIER: " + sql);
  const label = await page.locator(".gen-card canvas[role=img]").first().getAttribute("aria-label");
  if (!/average_departure_delay by CARRIER\.name, 3 rows/.test(label ?? "")) throw new Error("Unexpected chart label: " + label);
  const painted = await page.locator(".gen-card canvas").first().evaluate((c) => {
    const data = c.getContext("2d").getImageData(0, 0, c.width, c.height).data;
    let lit = 0;
    for (let i = 3; i < data.length; i += 4) if (data[i] > 0) lit++;
    return lit;
  });
  if (painted < 500) throw new Error("The chart canvas is empty.");
  await page.locator(".gen-card", { has: page.locator("canvas") }).first().screenshot({ path: `${out}/03-chart.png` });
  await page.getByRole("button", { name: "SHOW DATA" }).first().click();
  await page.locator(".gen-card table").first().waitFor();
  await page.screenshot({ path: `${out}/02-answer.png`, fullPage: true });

  const csp = problems.filter((p) => /Content Security Policy|Refused to/i.test(p));
  if (csp.length) throw new Error("CSP violations:\n" + csp.join("\n"));
  if (external.length) throw new Error("Requests left the origin:\n" + [...new Set(external)].join("\n"));
  console.log(`Conversational BI browser check passed (${shared ? "shared" : "own"} model). Screenshots in ${out}.`);
  if (problems.length) console.log("Console messages:\n" + problems.join("\n"));
} catch (error) {
  await page.screenshot({ path: `${out}/failure.png`, fullPage: true }).catch(() => {});
  console.error(problems.join("\n"));
  throw error;
} finally {
  await browser.close();
}
