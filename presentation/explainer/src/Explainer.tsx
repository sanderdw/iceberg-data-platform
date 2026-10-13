import React from "react";
import { AbsoluteFill, Audio, Sequence, staticFile, useCurrentFrame } from "remotion";
import { SceneFrame } from "./components/SceneFrame";
import { Scene2 } from "./scenes/Scene2";
import { Scene3 } from "./scenes/Scene3";
import { Scene4 } from "./scenes/Scene4";
import { Scene5 } from "./scenes/Scene5";
import { Scene6 } from "./scenes/Scene6";
import { Scene7 } from "./scenes/Scene7";
import { Sky, Stage } from "./components/ui";
import { CUES, musicVolume } from "./lib/audio";
import { END_CARD, PRE, TOTAL, visualEnd, visualStart } from "./lib/time";
import { Scene1 } from "./scenes/Scene1";
import { EndCard } from "./scenes/EndCard";

export const SCENES: { id: string; component: React.FC }[] = [
  { id: "s1", component: Scene1 },
  { id: "s2", component: Scene2 },
  { id: "s3", component: Scene3 },
  { id: "s4", component: Scene4 },
  { id: "s5", component: Scene5 },
  { id: "s6", component: Scene6 },
  { id: "s7", component: Scene7 },
];

/** The night sky behind every scene, drawn once so the zoom transitions never flash. */
export const Background: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <Stage>
      <Sky frame={frame} />
    </Stage>
  );
};

/** The whole video: scenes on the timeline from timings.json, the voice-over, music and sound. */
export const Explainer: React.FC = () => (
  <AbsoluteFill style={{ backgroundColor: "#0A1433" }}>
    <Background />
    {SCENES.map(({ id, component: Component }, i) => {
      const from = visualStart(id);
      const length = visualEnd(id) - from;
      return (
        <Sequence key={id} from={from} durationInFrames={length} name={id}>
          <SceneFrame length={length} first={i === 0}>
            <Component />
          </SceneFrame>
        </Sequence>
      );
    })}
    <Sequence from={END_CARD - PRE} durationInFrames={TOTAL - END_CARD + PRE} name="end card">
      <EndCard />
    </Sequence>
    <Audio src={staticFile("voice/voiceover.wav")} />
    <Audio src={staticFile("music/music.wav")} volume={musicVolume} />
    {CUES.map((cue, i) => (
      <Sequence key={i} from={cue.frame} durationInFrames={4 * 30} name={`sfx ${cue.sound}`}>
        <Audio src={staticFile(`sfx/${cue.sound}.wav`)} volume={cue.volume} />
      </Sequence>
    ))}
  </AbsoluteFill>
);
