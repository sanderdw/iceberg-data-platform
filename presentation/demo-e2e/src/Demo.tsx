import React from "react";
import { AbsoluteFill, Audio, Freeze, interpolate, OffthreadVideo, Sequence, staticFile, useCurrentFrame } from "remotion";
import { musicVolume } from "./lib/audio";
import { EDLS, musicFile, Piece, Voice, voiceFile } from "./lib/edl";
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

const TitleCard: React.FC = () => {
  const t = useT();
  return (
    <AbsoluteFill style={{ ...dots, padding: "0 160px", justifyContent: "center" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 18, marginBottom: 40 }}>
        <div style={{ width: 14, height: 14, borderRadius: 7, background: C.red }} />
        <div style={mono(26)}>Iceberg Data Platform · 0.9.9 · end-to-end demo</div>
      </div>
      <div style={{ fontFamily: FONT.display, fontSize: 168, lineHeight: 1.02, color: C.white, fontWeight: 600 }}>
        {t("Van API", "From API")}
        <br />
        {t("tot antwoord", "to answer")}
      </div>
      <div style={{ fontFamily: FONT.body, fontSize: 36, color: C.textMid, marginTop: 48, maxWidth: 1100, lineHeight: 1.35 }}>
        {t(
          "Installeren, een pipeline, een semantisch model, vragen stellen en publiceren in een catalogus.",
          "Installing, a pipeline, a semantic model, asking questions and publishing to a catalog.",
        )}
      </div>
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

const PieceView: React.FC<{ piece: Piece }> = ({ piece }) => {
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
export const Demo: React.FC<{ voice?: Voice }> = ({ voice = "elevenlabs" }) => {
  const edl = EDLS[voice];
  return (
    <LangContext.Provider value={edl.lang ?? "nl"}>
      <AbsoluteFill style={{ backgroundColor: C.black }}>
        {edl.pieces.map((piece, i) =>
          piece.frames > 0 ? (
            <Sequence key={i} from={piece.from} durationInFrames={piece.frames} name={"shot" in piece ? piece.shot : piece.card}>
              <PieceView piece={piece} />
            </Sequence>
          ) : null,
        )}
        {edl.voice ? <Audio src={staticFile(voiceFile(voice))} /> : null}
        {edl.music ? <Audio src={staticFile(musicFile(voice))} volume={musicVolume(edl)} /> : null}
      </AbsoluteFill>
    </LangContext.Provider>
  );
};
