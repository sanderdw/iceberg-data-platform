import React from "react";
import { useCurrentFrame } from "remotion";
import { ease, fade, mix, pop } from "../lib/anim";
import { C, T } from "../lib/theme";
import { sceneTimes } from "../lib/time";
import { Iceberg } from "../components/Iceberg";
import { ChatBubble, Laptop } from "../components/Icons";
import { Chip, Pop, Stage, Txt } from "../components/ui";
import { Desk } from "./Scene1";

const t = sceneTimes("s7");

export const Scene7: React.FC = () => {
  const frame = useCurrentFrame();
  const attachments = [
    { text: "Design record", fill: C.coral, shade: C.coralShade, at: t.at("s7b1", "a design record") },
    { text: "Principles check", fill: C.sun, shade: C.sunShade, at: t.at("s7b1", "a principles check") },
    { text: "Spike plan", fill: C.teal, shade: C.tealShade, at: t.at("s7b1", "a plan for a spike") },
  ];
  const pan = ease(frame, t.b.s7b2 - 12, t.b.s7b2 + 14);
  const comes = t.at("s7b2", "But when it comes");
  const end = t.at("s7b3", "So build");
  const cx = 760;
  const waterY = 560;
  const scale = 0.62;
  const peakY = waterY - 162 * scale;

  return (
    <Stage>
      {/* Back at the desk: the answer */}
      <g transform={`translate(0 ${-1080 * pan})`}>
        <Desk frame={frame + 900} />
        <Pop p={pop(frame, 2)} cx={400} cy={150}>
          <Chip x={400} y={150} text="Still Tuesday" fill={C.violet} shade={C.violetShade} color={C.white} size={T.small} />
        </Pop>
        <Laptop x={860} y={770} />
        <Pop p={pop(frame, t.b.s7b1 + 4)} cx={1300} cy={360}>
          <ChatBubble x={1300} y={360} w={820} lines={["Here is the answer, before any code:"]} size={T.label} tail="right" />
        </Pop>
        {attachments.map((a, i) => (
          <Pop key={a.text} p={pop(frame, a.at)} cx={1300} cy={520 + i * 100}>
            <Chip x={1300} y={520 + i * 100} text={a.text} fill={a.fill} shade={a.shade} size={T.label} />
          </Pop>
        ))}
      </g>

      {/* The sea: everything under the water is in place, so the tip grows fast */}
      <g transform={`translate(0 ${1080 * (1 - pan)})`}>
        <Iceberg
          id="s7"
          frame={frame}
          cx={cx}
          waterY={waterY}
          scale={scale}
          reveal={0.9}
          layers={[1, 1, 1, 1]}
          labels={[1, 1, 1, 1]}
          expression={frame > end ? "happy" : "smile"}
        />
        {Array.from({ length: 12 }, (_, i) => {
          const row = Math.floor(i / 3);
          const col = i % 3;
          const x = cx + (col - 1) * 56 - 26 + (row % 2) * 22;
          const y = peakY - 52 - row * 40;
          const p = pop(frame, comes + 10 + i * 3, 260, 14);
          return (
            <Pop key={i} p={p} cx={x + 25} cy={y + 17}>
              <rect x={x} y={y + 5} width={50} height={34} rx={10} fill={[C.tealShade, C.violetShade, C.sunShade, C.coralShade][i % 4]} />
              <rect x={x} y={y} width={50} height={34} rx={10} fill={[C.teal, C.violet, C.sun, C.coral][i % 4]} />
            </Pop>
          );
        })}
        <g opacity={fade(frame, end, 14)} transform={`translate(0 ${mix(30, 0, fade(frame, end, 14))})`}>
          <Txt x={960} y={120} size={T.h1} weight={900}>
            Build the part under the water
          </Txt>
        </g>
      </g>
    </Stage>
  );
};
