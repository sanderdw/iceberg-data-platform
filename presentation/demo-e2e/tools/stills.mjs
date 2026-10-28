// Renders stills of the timeline: node tools/stills.mjs <outdir> [shot-prefix…]
// VOICE=gemini-en renders the English version.
// For every beat of the matching shots: its start + 1.5 s and its last frame.
import { bundle } from "@remotion/bundler";
import { renderStill, selectComposition } from "@remotion/renderer";
import { readFileSync, mkdirSync } from "node:fs";
import path from "node:path";

const [out, ...only] = process.argv.slice(2);
mkdirSync(out, { recursive: true });
const voice = process.env.VOICE ?? "gemini";
const edl = JSON.parse(readFileSync(`src/edl-${voice}.json`, "utf8"));
const want = (shot) => !only.length || only.some((p) => shot.startsWith(p));
const frames = [];
for (const b of edl.beats) {
  if (!want(b.shot)) continue;
  frames.push([`${b.id}-a`, b.from + Math.round(1.5 * edl.fps)]);
  frames.push([`${b.id}-z`, b.from + b.frames - 1]);
}
const serveUrl = await bundle({ entryPoint: path.resolve("src/index.ts") });
const composition = await selectComposition({ serveUrl, id: voice === "gemini-en" ? "DemoEnglish" : "DemoGemini" });
for (const [name, frame] of frames) {
  await renderStill({ composition, serveUrl, output: `${out}/${name}.png`, frame, scale: 0.5 });
  process.stdout.write(name + " ");
}
console.log();
