// Stills at the middle of every beat, and one contact sheet per scene (ffmpeg xstack).
//   node scripts/stills.mjs            all scenes
//   node scripts/stills.mjs s2 s3      some scenes
import { bundle } from "@remotion/bundler";
import { renderStill, selectComposition } from "@remotion/renderer";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const timings = JSON.parse(readFileSync(path.join(root, "src/timings.json"), "utf8"));
const wanted = process.argv.slice(2);
const out = path.join(root, "out/stills");
mkdirSync(out, { recursive: true });

const serveUrl = await bundle({ entryPoint: path.join(root, "src/index.ts"), publicDir: path.join(root, "public") });
const composition = await selectComposition({ serveUrl, id: "Explainer" });

const scenes = timings.scenes.filter((s) => !wanted.length || wanted.includes(s.id) || (wanted.includes("end") && false));
for (const scene of scenes) {
  const files = [];
  for (const id of scene.beats) {
    const beat = timings.beats[id];
    const frame = beat.startFrame + Math.round(beat.durationFrames / 2);
    const file = path.join(out, `${id}.png`);
    await renderStill({ serveUrl, composition, frame, output: file, scale: 0.5 });
    files.push(file);
  }
  sheet(files, path.join(out, `sheet-${scene.id}.png`));
  console.log(`sheet-${scene.id}.png (${files.length} beats)`);
}
if (!wanted.length || wanted.includes("end")) {
  const files = [];
  for (const offset of [20, 90, timings.durationFrames - timings.endCard.startFrame - 5]) {
    const file = path.join(out, `end-${offset}.png`);
    await renderStill({ serveUrl, composition, frame: timings.endCard.startFrame + offset, output: file, scale: 0.5 });
    files.push(file);
  }
  sheet(files, path.join(out, "sheet-end.png"));
  console.log("sheet-end.png");
}

function sheet(files, target) {
  const cols = 3;
  const inputs = files.flatMap((f) => ["-i", f]);
  const padded = [...files];
  while (padded.length % cols) padded.push(files[files.length - 1]);
  const extra = padded.slice(files.length).flatMap((f) => ["-i", f]);
  const n = padded.length;
  const layout = padded.map((_, i) => `${(i % cols) * 960}_${Math.floor(i / cols) * 540}`).join("|");
  const labels = padded
    .map((f, i) => `[${i}]drawtext=text='${path.basename(f, ".png")}':x=16:y=12:fontsize=28:fontcolor=white:box=1:boxcolor=black@0.5[v${i}]`)
    .join(";");
  const stack = padded.map((_, i) => `[v${i}]`).join("");
  execFileSync("ffmpeg", ["-y", "-loglevel", "error", ...inputs, ...extra, "-filter_complex", `${labels};${stack}xstack=inputs=${n}:layout=${layout}`, target]);
}
