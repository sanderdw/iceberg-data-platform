import React from "react";
import { Audio, Composition, Sequence, staticFile } from "remotion";
import "./lib/font";
import { Explainer } from "./Explainer";
import { SCENES, Background } from "./Explainer";
import { SceneFrame } from "./components/SceneFrame";
import { EndCard } from "./scenes/EndCard";
import { FPS, END_CARD, TOTAL, visualEnd, visualStart } from "./lib/time";
import { H, W } from "./lib/theme";

/** One composition per scene, with its stretch of the voice-over, for quick previews. */
const ScenePreview: React.FC<{ id: string }> = ({ id }) => {
  const scene = SCENES.find((s) => s.id === id)!;
  const origin = visualStart(id);
  const length = visualEnd(id) - origin;
  const Component = scene.component;
  return (
    <>
      <Background />
      <SceneFrame length={length} first={id === "s1"}>
        <Component />
      </SceneFrame>
      <Sequence from={-origin}>
        <Audio src={staticFile("voice/voiceover.wav")} />
      </Sequence>
    </>
  );
};

const EndPreview: React.FC = () => (
  <>
    <Background />
    <EndCard />
  </>
);

export const Root: React.FC = () => (
  <>
    <Composition id="Explainer" component={Explainer} durationInFrames={TOTAL} fps={FPS} width={W} height={H} />
    {SCENES.map((scene) => (
      <Composition
        key={scene.id}
        id={scene.id.toUpperCase()}
        component={() => <ScenePreview id={scene.id} />}
        durationInFrames={visualEnd(scene.id) - visualStart(scene.id)}
        fps={FPS}
        width={W}
        height={H}
      />
    ))}
    <Composition id="EndCard" component={EndPreview} durationInFrames={TOTAL - END_CARD} fps={FPS} width={W} height={H} />
  </>
);
