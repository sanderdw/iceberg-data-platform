import React from "react";
import { useCurrentFrame } from "remotion";
import { ease, fade, fadeOut, mix, pop } from "../lib/anim";
import { C, T } from "../lib/theme";
import { sceneTimes } from "../lib/time";
import { Desk } from "../components/Desk";
import { Iceberg } from "../components/Iceberg";
import { ChatBubble, Laptop } from "../components/Icons";
import { PRINCIPLES } from "../components/Principle";
import { Chip, Doc, Pop, Stage, Tick, Txt } from "../components/ui";
import { Poster } from "./Scene1";

const t = sceneTimes("s6");

export const Scene6: React.FC = () => {
  const frame = useCurrentFrame();
  const vis = (inAt: number, outAt: number) => Math.min(1, fade(frame, inAt, 8)) * fadeOut(frame, outAt - 10, 10);
  const recap = ["AI operates", "If it matters", "Decide in the open", "keep moving"].map((p) => t.at("s6b1", p));
  const proofs = ["a file", "a test", "a decision"].map((p) => t.at("s6b2", p));
  const dissolve = ease(frame, proofs[0] - 10, proofs[0] + 4);
  const ask = t.at("s6b3", "and ask");
  const pan = ease(frame, t.b.s6b4 - 12, t.b.s6b4 + 14);
  const everywhere = t.at("s6b4", "they're under the water");

  return (
    <Stage>
      {/* s6b1: the four principles, in spoken order */}
      <g opacity={vis(t.b.s6b1, t.b.s6b2)}>
        {PRINCIPLES.map((p, i) => {
          const x = 960 + (i - 1.5) * 460;
          const tagline = p.tagline.includes(". ") ? p.tagline.replace(". ", ".|").split("|") : p.tagline.replace(", ", ",|").split("|");
          return (
            <Pop key={p.name} p={pop(frame, recap[i])} cx={x} cy={540}>
              <rect x={x - 215} y={370 + 12} width={430} height={340} rx={36} fill={p.shade} />
              <rect x={x - 215} y={370} width={430} height={340} rx={36} fill={p.fill} />
              <Txt x={x} y={470} size={T.small} weight={900} fill={C.ink}>
                {`Principle ${i + 1}`}
              </Txt>
              <Txt x={x} y={535} size={36} weight={900} fill={C.ink}>
                {p.name}
              </Txt>
              {tagline.map((line, j) => (
                <Txt key={line} x={x} y={605 + j * 38} size={28} weight={800} fill={C.ink}>
                  {line.trim()}
                </Txt>
              ))}
            </Pop>
          );
        })}
      </g>

      {/* s6b2: not a poster, but a file, a test and a decision */}
      <g opacity={vis(t.b.s6b2, t.b.s6b3)}>
        <Poster x={960} y={500} scale={mix(1, 0.6, dissolve) * pop(frame, t.b.s6b2)} opacity={1 - dissolve} />
        <Txt x={960} y={860} size={T.label} weight={900} fill={C.muted} opacity={fade(frame, t.b.s6b2 + 6, 8) * (1 - dissolve)}>
          Not on a poster
        </Txt>
        <Pop p={pop(frame, proofs[0])} cx={520} cy={540}>
          <Doc x={520} y={540} w={240} h={300} title="file" accent={C.teal} lines={4} titleSize={34} />
        </Pop>
        <Pop p={pop(frame, proofs[1])} cx={960} cy={540}>
          <Tick x={960} y={540} r={110} />
          <Txt x={960} y={720} size={T.small} weight={900}>
            test
          </Txt>
        </Pop>
        <Pop p={pop(frame, proofs[2])} cx={1400} cy={540}>
          <Doc x={1400} y={540} w={240} h={300} title="decision" accent={C.violet} lines={4} titleSize={34} />
        </Pop>
      </g>

      {/* s6b3 and s6b4: back at the desk on Monday, then down to the iceberg */}
      <g opacity={fade(frame, t.b.s6b3, 10)}>
        <g transform={`translate(0 ${-1080 * pan})`}>
          <Desk frame={frame + 600} />
          <Pop p={pop(frame, t.b.s6b3)} cx={400} cy={150}>
            <Chip x={400} y={150} text="Next Monday" fill={C.violet} shade={C.violetShade} color={C.white} size={T.small} />
          </Pop>
          <Laptop x={860} y={770} />
          <Pop p={pop(frame, ask)} cx={1300} cy={360}>
            <ChatBubble x={1300} y={360} w={860} lines={["Where can someone see", "our principles at work?"]} size={T.label} tail="right" />
          </Pop>
        </g>
        <g transform={`translate(0 ${1080 * (1 - pan)})`}>
          <Iceberg
            id="v2s6"
            frame={frame}
            cx={700}
            waterY={520}
            scale={0.66}
            reveal={0.9}
            layers={[1, 1, 1, 1]}
            labels={[1, 1, 1, 1]}
            expression={frame > everywhere ? "happy" : "smile"}
          />
          <g opacity={fade(frame, everywhere, 14)} transform={`translate(0 ${mix(30, 0, fade(frame, everywhere, 14))})`}>
            <Txt x={1380} y={200} size={T.h1} weight={900}>
              Principles You Can See
            </Txt>
            <Txt x={1380} y={280} size={T.small} weight={800} fill={C.muted}>
              Under the water, holding everything up
            </Txt>
          </g>
        </g>
      </g>
    </Stage>
  );
};
