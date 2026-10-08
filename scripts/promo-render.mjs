// Renders presentation/video-promo/semantic-models/index.html frame by frame into an MP4 with the synthesized soundtrack.
// Usage: node scripts/promo-render.mjs            full render
//        node scripts/promo-render.mjs --stills 2,12,40   PNG stills at those seconds, no video
import {spawn, spawnSync} from 'node:child_process';
import {mkdirSync, readFileSync, statSync} from 'node:fs';
import {extname, join, normalize} from 'node:path';
import {parseArgs} from 'node:util';
import {chromium} from '@playwright/test';

const {values: args} = parseArgs({options: {stills: {type: 'string'}}});
const ROOT = 'presentation', PAGE = 'video-promo/semantic-models', ORIGIN = 'http://promo.local/', WORK = '.local/promo';
const OUT = `${ROOT}/${PAGE}/iceberg-data-platform-promo.mp4`, POSTER = `${ROOT}/${PAGE}/poster.png`, AUDIO = `${WORK}/soundtrack.wav`;
const timeline = JSON.parse(readFileSync(`${ROOT}/${PAGE}/timeline.json`, 'utf8'));
const TYPES = {'.html': 'text/html', '.json': 'application/json', '.png': 'image/png', '.ttf': 'font/ttf'};
mkdirSync(WORK, {recursive: true});

function run(command, argv) {
  const result = spawnSync(command, argv, {stdio: 'inherit'});
  if (result.status !== 0) throw new Error(`${command} exited with ${result.status}`);
}

const browser = await chromium.launch({headless: true});
try {
  const page = await browser.newPage({viewport: {width: 1920, height: 1080}, deviceScaleFactor: 1});
  // Serve presentation/ from memory, so fetch() works without a web server.
  await page.route(`${ORIGIN}**`, route => {
    const path = normalize(decodeURIComponent(new URL(route.request().url()).pathname)).replace(/^[/\\]+/, '');
    try { route.fulfill({body: readFileSync(join(ROOT, path)), contentType: TYPES[extname(path)] || 'application/octet-stream'}); }
    catch { route.fulfill({status: 404}); }
  });
  const errors = [];
  page.on('pageerror', error => errors.push(error));
  await page.goto(`${ORIGIN}${PAGE}/index.html?render`);
  await page.evaluate(() => window.promoReady);
  if (errors.length) throw errors[0];

  if (args.stills) {
    mkdirSync(`${WORK}/stills`, {recursive: true});
    for (const at of args.stills.split(',').map(Number)) {
      await page.evaluate(t => window.seek(t), at);
      const path = `${WORK}/stills/${String(at).padStart(5, '0')}.png`;
      await page.screenshot({path});
      console.log(path);
    }
  } else {
    run('uv', ['run', '--group', 'data', 'python', 'scripts/promo-audio.py', AUDIO]);
    const frames = Math.round(timeline.duration * timeline.fps);
    const ffmpeg = spawn('ffmpeg', ['-y', '-loglevel', 'error', '-f', 'image2pipe', '-framerate', String(timeline.fps), '-i', '-', '-i', AUDIO,
      '-c:v', 'libx264', '-preset', 'slow', '-crf', '20', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', '-shortest', OUT],
    {stdio: ['pipe', 'inherit', 'inherit']});
    const done = new Promise((resolve, reject) => ffmpeg.on('close', code => code === 0 ? resolve() : reject(new Error(`ffmpeg exited with ${code}`))));
    for (let f = 0; f < frames; f++) {
      await page.evaluate(t => window.seek(t), f / timeline.fps);
      const png = await page.screenshot({type: 'png'});
      if (!ffmpeg.stdin.write(png)) await new Promise(resolve => ffmpeg.stdin.once('drain', resolve));
      if (f % timeline.fps === 0) process.stdout.write(`\r${f / timeline.fps}s / ${timeline.duration}s`);
    }
    ffmpeg.stdin.end();
    await done;
    process.stdout.write('\n');
    // The end card doubles as the poster for the video element and link previews.
    await page.evaluate(t => window.seek(t), 58);
    await page.screenshot({path: POSTER});
    run('ffprobe', ['-v', 'error', '-show_entries', 'stream=codec_name,width,height,r_frame_rate:format=duration,size', '-of', 'compact', OUT]);
    console.log(`${OUT} · ${(statSync(OUT).size / 1e6).toFixed(1)} MB`);
  }
} finally {
  await browser.close();
}
