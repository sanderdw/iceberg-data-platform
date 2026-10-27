// The recording director: one long-lived Chromium whose pages are recorded as 30 fps clips, driven step
// by step over a small local HTTP API, so the demo can follow what really happens on screen.
//
//   node record/director.mjs                      # listens on 127.0.0.1:7777
//   curl -s localhost:7777/eval --data-binary @step.js
//
// A step is the body of an async function that gets `ctx`: { context, page(name, url), rec, mark, ui, sleep }.
// rec.start(clip, pageName) streams that page into recordings/<clip>.mp4 until rec.stop(clip).
// mark(label) appends the wall time to recordings/markers.jsonl; every clip's JSON holds its start
// wall time, so a marker's offset in a clip is marker.wall - clip.start.
import {spawn} from 'node:child_process';
import {appendFileSync, mkdirSync, writeFileSync} from 'node:fs';
import {createServer} from 'node:http';
import {dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const OUT = resolve(ROOT, 'recordings');
mkdirSync(OUT, {recursive: true});
const FPS = 30;
// 1536×864 CSS pixels at 2.5× is exactly 3840×2160 (4K), with the layout of the portals at a readable 125 %.
const VIEWPORT = {width: 1536, height: 864}, SCALE = 2.5;
const W = VIEWPORT.width * SCALE, H = VIEWPORT.height * SCALE;

const browser = await chromium.launch({headless: true, args: ['--hide-scrollbars', '--font-render-hinting=none']});
const context = await browser.newContext({viewport: VIEWPORT, deviceScaleFactor: SCALE, colorScheme: 'dark', locale: 'en-GB'});

// A visible mouse pointer with a click pulse; Playwright's mouse moves fire real events. Not on the terminal.
await context.addInitScript(() => {
  if (location.port === '7681') return;
  const install = () => {
    if (document.getElementById('__demo_cursor')) return;
    const dot = document.createElement('div');
    dot.id = '__demo_cursor';
    dot.style.cssText = 'position:fixed;left:-40px;top:-40px;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;'
      + 'background:rgba(255,255,255,.92);border:2px solid #000;z-index:2147483647;pointer-events:none;transition:transform .12s';
    document.documentElement.appendChild(dot);
    addEventListener('mousemove', e => { dot.style.left = e.clientX + 'px'; dot.style.top = e.clientY + 'px'; }, true);
    addEventListener('mousedown', () => { dot.style.transform = 'scale(.65)'; }, true);
    addEventListener('mouseup', () => { dot.style.transform = 'scale(1)'; }, true);
  };
  if (document.documentElement) install(); else addEventListener('DOMContentLoaded', install);
  addEventListener('DOMContentLoaded', install);
});

const pages = new Map();
async function page(name, url) {
  let p = pages.get(name);
  if (!p || p.isClosed()) { p = await context.newPage(); pages.set(name, p); }
  if (url) { await p.goto(url); if (url.includes(':7681')) await frameTerminal(p); }
  return p;
}

// The terminal gets a margin, like a window on a desk; xterm refits on resize.
async function frameTerminal(p) {
  await p.waitForSelector('.xterm-screen');
  await p.addStyleTag({content: 'html,body{background:#000!important} #terminal-container{position:fixed!important;'
    + 'inset:30px 40px 26px 40px!important;width:auto!important;height:auto!important}'});
  await p.evaluate(() => dispatchEvent(new Event('resize')));
  await sleep(600);
}

// One recorder per clip: the newest screencast frame is written to ffmpeg on a 30 fps clock, so clip
// time t is exactly start + t on the wall clock, whether the page changes or not.
const clips = new Map();
const rec = {
  async start(clip, pageName) {
    if (clips.has(clip)) throw new Error(`clip ${clip} is already recording`);
    const p = await page(pageName);
    const cdp = await context.newCDPSession(p);
    const file = resolve(OUT, `${clip}.mp4`);
    const ffmpeg = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(FPS), '-c:v', 'mjpeg', '-i', '-',
      '-vf', `scale=${W}:${H}:flags=lanczos,format=yuv420p`, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '16', '-g', '60', file],
      {stdio: ['pipe', 'inherit', 'inherit']});
    const state = {clip, pageName, cdp, ffmpeg, latest: null, written: 0, start: null, file};
    cdp.on('Page.screencastFrame', async frame => {
      state.latest = Buffer.from(frame.data, 'base64');
      if (state.start === null) state.start = Date.now() / 1000;
      try { await cdp.send('Page.screencastFrameAck', {sessionId: frame.sessionId}); } catch {}
    });
    state.timer = setInterval(() => {
      if (!state.latest) return;
      const due = Math.floor((Date.now() / 1000 - state.start) * FPS) + 1;
      while (state.written < due) { state.ffmpeg.stdin.write(state.latest); state.written++; }
    }, 1000 / FPS / 2);
    await cdp.send('Page.startScreencast', {format: 'jpeg', quality: Number(process.env.JPEG_QUALITY || 92), maxWidth: W, maxHeight: H, everyNthFrame: 1});
    // A first frame even on a static page.
    await p.evaluate(() => new Promise(requestAnimationFrame)).catch(() => {});
    clips.set(clip, state);
    return {clip, file};
  },
  async stop(clip) {
    const state = clips.get(clip);
    if (!state) throw new Error(`clip ${clip} is not recording`);
    clips.delete(clip);
    await state.cdp.send('Page.stopScreencast').catch(() => {});
    clearInterval(state.timer);
    const done = new Promise(r => state.ffmpeg.on('exit', r));
    state.ffmpeg.stdin.end();
    await done;
    const info = {clip, page: state.pageName, start: state.start, frames: state.written, duration: state.written / FPS, fps: FPS};
    writeFileSync(resolve(OUT, `${clip}.json`), JSON.stringify(info, null, 2) + '\n');
    return info;
  },
  list: () => [...clips.values()].map(s => ({clip: s.clip, page: s.pageName, seconds: s.start ? Date.now() / 1000 - s.start : 0})),
};

function mark(label, extra = {}) {
  const entry = {wall: Date.now() / 1000, label, ...extra};
  appendFileSync(resolve(OUT, 'markers.jsonl'), JSON.stringify(entry) + '\n');
  return entry;
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

// Human-looking interaction: glide to the element, then click or type at a typing pace.
const ui = {
  async glide(p, locator) {
    await locator.scrollIntoViewIfNeeded();
    const box = await locator.boundingBox();
    if (!box) throw new Error('element has no box');
    const x = box.x + box.width / 2, y = box.y + box.height / 2;
    await p.mouse.move(x, y, {steps: 24});
    await sleep(180);
    return {x, y};
  },
  async click(p, locator) { await ui.glide(p, locator); await locator.click(); await sleep(350); },
  async type(p, locator, text, delay = 70) { await ui.glide(p, locator); await locator.click(); await locator.pressSequentially(text, {delay}); await sleep(300); },
};

const AsyncFunction = (async () => {}).constructor;
const ctx = {context, browser, page, pages, rec, mark, ui, sleep, state: {}};

createServer(async (request, response) => {
  let body = '';
  for await (const chunk of request) body += chunk;
  const reply = (status, data) => { response.writeHead(status, {'content-type': 'application/json'}); response.end(JSON.stringify(data, null, 2)); };
  try {
    if (request.url === '/eval') return reply(200, {result: await new AsyncFunction('ctx', body)(ctx) ?? null});
    if (request.url === '/status') return reply(200, {pages: [...pages].map(([n, p]) => ({name: n, url: p.isClosed() ? null : p.url()})), clips: rec.list()});
    if (request.url === '/shutdown') { for (const c of [...clips.keys()]) await rec.stop(c); reply(200, {ok: true}); await browser.close(); process.exit(0); }
    reply(404, {error: 'unknown'});
  } catch (error) {
    reply(500, {error: String(error?.stack || error)});
  }
}).listen(7777, '127.0.0.1', () => console.log('director on http://127.0.0.1:7777'));
