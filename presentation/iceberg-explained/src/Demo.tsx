import React from "react";
import { AbsoluteFill, Audio, Freeze, interpolate, OffthreadVideo, Sequence, staticFile, useCurrentFrame } from "remotion";
import { musicVolume } from "./lib/audio";
import { Beat, EDLS, musicFile, Piece, Voice, voiceFile } from "./lib/edl";
import { TreeDiagram } from "./diagrams/Tree";
import { CombineDiagram, CompareDiagram, DeletesDiagram, EnginesDiagram, FunctionsDiagram, PermanentDiagram, PlatformDiagram } from "./diagrams/schemas";
import { LangContext, useT } from "./lib/i18n";
import { C, FONT } from "./lib/theme";

const clip = (name: string) => staticFile(`clips/${name}.mp4`);

// The dot grid of the portals, as a quiet backdrop for the cards.
const dots: React.CSSProperties = {
  backgroundColor: C.black,
  backgroundImage: `radial-gradient(${C.borderStrong} 1.2px, transparent 1.2px)`,
  backgroundSize: "28px 28px",
};

const mono = (size: number, color: string = C.textMid): React.CSSProperties => ({
  fontFamily: FONT.mono,
  fontSize: size,
  letterSpacing: "0.08em",
  textTransform: "uppercase",
  color,
});

/** Cards fade in and out over a few frames; the recordings themselves cut hard. */
const Fade: React.FC<{ frames: number; children: React.ReactNode }> = ({ frames, children }) => {
  const f = useCurrentFrame();
  const opacity = interpolate(f, [0, 8, frames - 8, frames], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return <AbsoluteFill style={{ opacity }}>{children}</AbsoluteFill>;
};

// The opening iceberg, in dot-matrix: the table you query is the tip, the files and metadata sit under the waterline.
const BERG = { w: 640, h: 820, water: 300, step: 16 };
const TIP: [number, number][] = [[230, 300], [290, 190], [330, 120], [362, 150], [404, 96], [446, 196], [510, 300]];
const BODY: [number, number][] = [
  [150, 300], [560, 300], [640, 400], [596, 500], [624, 610], [520, 730], [396, 800], [300, 760], [170, 700],
  [110, 570], [56, 440],
];

const inside = (x: number, y: number, poly: [number, number][]) => {
  let hit = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit;
  }
  return hit;
};

const BERG_DOTS = (() => {
  const out: { x: number; y: number; above: boolean }[] = [];
  for (let y = BERG.step / 2; y < BERG.h; y += BERG.step) {
    for (let x = BERG.step / 2; x < BERG.w; x += BERG.step) {
      const above = y < BERG.water;
      if (inside(x, y, above ? TIP : BODY)) out.push({ x, y, above });
    }
  }
  return out;
})();

const Iceberg: React.FC = () => {
  const f = useCurrentFrame();
  const t = useT();
  // Dots surface from the waterline outwards; the labels follow once the shape stands.
  const label = interpolate(f, [44, 60], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const line = interpolate(f, [4, 24], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <div style={{ position: "relative", width: BERG.w + 300, height: BERG.h }}>
      <svg width={BERG.w} height={BERG.h} style={{ position: "absolute", left: 0, top: 0 }}>
        {BERG_DOTS.map((d, i) => {
          const delay = 10 + Math.abs(d.y - BERG.water) / 16;
          const o = interpolate(f, [delay, delay + 10], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
          return <circle key={i} cx={d.x} cy={d.y} r={d.above ? 3.4 : 2.8} fill={d.above ? C.white : C.textDim} opacity={o} />;
        })}
      </svg>
      <div
        style={{
          position: "absolute",
          left: 0,
          top: BERG.water - 1,
          width: (BERG.w + 300) * line,
          height: 2,
          background: C.red,
        }}
      />
      <div style={{ position: "absolute", left: 560, top: 160, opacity: label }}>
        <div style={mono(20, C.white)}>{t("De tabel", "The table")}</div>
        <div style={{ ...mono(16, C.textDim), marginTop: 8 }}>{t("Wat je ziet", "What you see")}</div>
      </div>
      <div style={{ position: "absolute", left: 660, top: 470, opacity: label }}>
        <div style={mono(20, C.text)}>{t("Bestanden", "Files")}</div>
        <div style={mono(20, C.text)}>{t("en metadata", "and metadata")}</div>
        <div style={{ ...mono(16, C.textDim), marginTop: 8 }}>{t("Wat het mogelijk maakt", "What makes it work")}</div>
      </div>
    </div>
  );
};

const TitleCard: React.FC = () => {
  const f = useCurrentFrame();
  const t = useT();
  const rise = (from: number) => ({
    opacity: interpolate(f, [from, from + 14], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }),
    transform: `translateY(${interpolate(f, [from, from + 14], [16, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" })}px)`,
  });
  return (
    <AbsoluteFill style={{ ...dots, padding: "0 120px 0 160px", flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
      <div>
        <div style={{ ...mono(26), marginBottom: 40, ...rise(6) }}>{t("Iceberg Data Platform · uitleg", "Iceberg Data Platform · explained")}</div>
        <div style={{ fontFamily: FONT.display, fontSize: 150, lineHeight: 1.04, color: C.white, fontWeight: 600, ...rise(14) }}>
          {t("Wat is", "What is")}
          <br />
          Apache
          <br />
          Iceberg?
        </div>
        <div style={{ fontFamily: FONT.body, fontSize: 34, color: C.textMid, marginTop: 48, maxWidth: 760, lineHeight: 1.35, ...rise(30) }}>
          {t(
            "Een open tabelformaat: bestanden in een bucket, metadata in een catalogus, en een engine naar keuze.",
            "An open table format: files in a bucket, metadata in a catalog, and an engine of your choice.",
          )}
        </div>
      </div>
      <Iceberg />
    </AbsoluteFill>
  );
};

const ChapterCard: React.FC<{ number: string; title: string }> = ({ number, title }) => (
  <AbsoluteFill style={{ ...dots, padding: "0 160px", justifyContent: "center" }}>
    <div style={{ fontFamily: FONT.display, fontSize: 260, lineHeight: 1, color: C.text, fontWeight: 600 }}>{number}</div>
    <div style={{ ...mono(44, C.white), marginTop: 36 }}>{title}</div>
  </AbsoluteFill>
);

const EndCard: React.FC = () => {
  const t = useT();
  return (
    <AbsoluteFill style={{ ...dots, padding: "0 160px", justifyContent: "center" }}>
      <div style={{ fontFamily: FONT.display, fontSize: 150, lineHeight: 1.05, color: C.white, fontWeight: 600 }}>{t("Probeer het zelf.", "Try it yourself.")}</div>
      <div
        style={{
          fontFamily: FONT.mono,
          fontSize: 23,
          whiteSpace: "nowrap",
          color: C.text,
          background: C.surface,
          border: `1px solid ${C.borderStrong}`,
          borderRadius: 14,
          padding: "26px 32px",
          marginTop: 56,
          width: "fit-content",
        }}
      >
        curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/latest/download/install.sh | sh
      </div>
      <div style={{ ...mono(24), marginTop: 40 }}>Docs · sanderdw.github.io/iceberg-data-platform</div>
    </AbsoluteFill>
  );
};

const DIAGRAMS: Record<string, React.FC<{ beats: Beat[]; pieceFrom: number; frames: number }>> = {
  layers: TreeDiagram,
  functions: FunctionsDiagram,
  platform: PlatformDiagram,
  permanent: PermanentDiagram,
  compare: CompareDiagram,
  engines: EnginesDiagram,
  combine: CombineDiagram,
  deletes: DeletesDiagram,
};

const PieceView: React.FC<{ piece: Piece; beats: Beat[] }> = ({ piece, beats }) => {
  if (piece.kind === "card" && piece.card === "diagram") {
    const Diagram = DIAGRAMS[piece.diagram!];
    return <Diagram beats={beats.filter((b) => b.shot === piece.shot)} pieceFrom={piece.from} frames={piece.frames} />;
  }
  if (piece.kind === "card") {
    return (
      <Fade frames={piece.frames}>
        {piece.card === "title" ? <TitleCard /> : piece.card === "end" ? <EndCard /> : <ChapterCard number={piece.number!} title={piece.title!} />}
      </Fade>
    );
  }
  if (piece.kind === "hold") {
    return (
      <Freeze frame={0}>
        <OffthreadVideo src={clip(piece.clip)} trimBefore={piece.startFrom} muted />
      </Freeze>
    );
  }
  return <OffthreadVideo src={clip(piece.clip)} trimBefore={piece.startFrom} playbackRate={piece.rate} muted />;
};

/** The whole video: the pieces of src/edl.json (or edl-<voice>.json) on one timeline, with the voice-over and the music bed. */
export const Demo: React.FC<{ voice?: Voice }> = ({ voice = "gemini" }) => {
  const edl = EDLS[voice];
  return (
    <LangContext.Provider value={edl.lang ?? "nl"}>
      <AbsoluteFill style={{ backgroundColor: C.black }}>
        {edl.pieces.map((piece, i) =>
          piece.frames > 0 ? (
            <Sequence key={i} from={piece.from} durationInFrames={piece.frames} name={"shot" in piece ? piece.shot : piece.card}>
              <PieceView piece={piece} beats={edl.beats} />
            </Sequence>
          ) : null,
        )}
        {edl.voice ? <Audio src={staticFile(voiceFile(voice))} /> : null}
        {edl.music ? <Audio src={staticFile(musicFile(voice))} volume={musicVolume(edl)} /> : null}
      </AbsoluteFill>
    </LangContext.Provider>
  );
};
