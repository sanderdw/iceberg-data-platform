import React from "react";
import { Composition, Sequence } from "remotion";
import "./lib/font";
import { Demo } from "./Demo";
import { EDL, EDLS, FPS, TOTAL } from "./lib/edl";
import { H, W } from "./lib/theme";

/** One composition per chapter, cut from the whole video, for quick previews. */
const ChapterPreview: React.FC<{ from: number }> = ({ from }) => (
  <Sequence from={-from}>
    <Demo />
  </Sequence>
);

export const Root: React.FC = () => (
  <>
    <Composition id="Demo" component={Demo} durationInFrames={TOTAL} fps={FPS} width={W} height={H} />
    <Composition
      id="DemoGemini"
      component={Demo}
      defaultProps={{ voice: "gemini" as const }}
      durationInFrames={EDLS.gemini.durationFrames}
      fps={EDLS.gemini.fps}
      width={W}
      height={H}
    />
    <Composition
      id="DemoEnglish"
      component={Demo}
      defaultProps={{ voice: "gemini-en" as const }}
      durationInFrames={EDLS["gemini-en"].durationFrames}
      fps={EDLS["gemini-en"].fps}
      width={W}
      height={H}
    />
    {EDL.chapters.map((c) => (
      <Composition
        key={c.id}
        id={c.id.toUpperCase()}
        component={() => <ChapterPreview from={c.from} />}
        durationInFrames={Math.max(1, c.frames)}
        fps={FPS}
        width={W}
        height={H}
      />
    ))}
  </>
);
