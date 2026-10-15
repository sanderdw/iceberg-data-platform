import React from "react";
import { useCurrentFrame } from "remotion";
import { ease, fade, fadeOut, mix, pop } from "../lib/anim";
import { C, T } from "../lib/theme";
import { sceneTimes } from "../lib/time";
import { Agent, Person } from "../components/Characters";
import { Desk } from "../components/Desk";
import { Iceberg } from "../components/Iceberg";
import { Laptop } from "../components/Icons";
import { PRINCIPLES } from "../components/Principle";
import { Card, Chip, Pop, Stage, Txt } from "../components/ui";

const t = sceneTimes("s1");

/** The principles poster on the wall. Origin is its centre. */
export const Poster: React.FC<{ x: number; y: number; scale?: number; opacity?: number }> = ({ x, y, scale = 1, opacity = 1 }) => (
  <g transform={`translate(${x} ${y}) scale(${scale})`} opacity={opacity}>
    <rect x={-210} y={-200} width={420} height={420} rx={18} fill={C.paperShade} />
    <rect x={-210} y={-210} width={420} height={420} rx={18} fill={C.paper} />
    <circle cx={0} cy={-226} r={10} fill={C.coral} />
    <Txt x={0} y={-150} size={36} weight={900} fill={C.ink}>
      Our principles
    </Txt>
    {PRINCIPLES.map((p, i) => (
      <g key={p.name}>
        <circle cx={-160} cy={-80 + i * 70} r={12} fill={p.fill} />
        <Txt x={-134} y={-80 + i * 70} size={30} weight={800} fill={C.ink} anchor="start">
          {p.name}
        </Txt>
      </g>
    ))}
  </g>
);

/** Small repository tree on the laptop screen. */
const RepoScreen: React.FC<{ frame: number; at: number }> = ({ frame, at }) => (
  <g opacity={fade(frame, at, 8)}>
    {["repo/", "  README.md", "  docs/", "  src/", "  tests/"].map((line, i) => {
      const depth = line.length - line.trimStart().length;
      return (
        <g key={line} opacity={fade(frame, at + i * 4, 6)}>
          <rect x={-170 + depth * 14} y={-250 + i * 42} width={26} height={22} rx={5} fill={line.trim().endsWith("/") ? C.sun : C.paper} />
          <Txt x={-134 + depth * 14} y={-239 + i * 42} size={26} weight={800} anchor="start">
            {line.trim()}
          </Txt>
        </g>
      );
    })}
  </g>
);

export const Scene1: React.FC = () => {
  const frame = useCurrentFrame();
  const monday = t.at("s1b1", "And then Monday");
  const repo = t.at("s1b2", "in a repository");
  const fly = ease(frame, t.at("s1b2", "Not on a slide"), repo);
  const pan = ease(frame, t.b.s1b3 - 10, t.b.s1b3 + 16);
  const meetTip = t.at("s1b3", "Meet Tip");
  const fourOf = t.at("s1b3", "four of our principles");
  const project = t.b.s1b4;
  const together = t.at("s1b4", "together with");
  const places = t.at("s1b5", "Four places");
  const labels = [0, 1, 2, 3].map((i) => ease(frame, t.b.s1b5 + i * 6, t.b.s1b5 + i * 6 + 10));
  const cx = 700;

  return (
    <Stage>
      <g transform={`translate(0 ${-1080 * pan})`}>
        <Desk frame={frame} />
        {/* The poster flies into the laptop when the question turns to the repository */}
        <Poster
          x={mix(1380, 860, fly)}
          y={mix(360, 640, fly)}
          scale={mix(1, 0.15, fly) * pop(frame, 4)}
          opacity={fadeOut(frame, repo - 4, 6)}
        />
        <Laptop x={860} y={770} screen={<RepoScreen frame={frame} at={repo} />} />
        <Pop p={pop(frame, monday)} cx={400} cy={150}>
          <Chip x={400} y={150} text="Monday · 09:00" fill={C.violet} shade={C.violetShade} color={C.white} size={T.small} />
        </Pop>
        {/* The week begins: tasks pile up in front of the poster */}
        {[0, 1, 2, 3].map((i) => (
          <Pop key={i} p={pop(frame, monday + 6 + i * 4)} cx={1300 + i * 40} cy={500}>
            <g opacity={fadeOut(frame, t.at("s1b2", "Not on a slide"), 10)} transform={`rotate(${[-8, 6, -3, 9][i]} ${1250 + i * 60} ${430 + i * 30})`}>
              <rect x={1170 + i * 60} y={360 + i * 30} width={170} height={150} rx={12} fill={[C.sun, C.teal, C.coral, C.violet][i]} />
              <rect x={1190 + i * 60} y={400 + i * 30} width={110} height={12} rx={6} fill={C.ink} opacity={0.4} />
              <rect x={1190 + i * 60} y={428 + i * 30} width={80} height={12} rx={6} fill={C.ink} opacity={0.4} />
            </g>
          </Pop>
        ))}
      </g>

      {/* The sea, with Tip, the project and the four principles as layers */}
      <g transform={`translate(0 ${1080 * (1 - pan)})`}>
        <Iceberg
          id="v2s1"
          frame={frame}
          cx={cx}
          waterY={470}
          scale={0.72}
          reveal={ease(frame, fourOf, fourOf + 24)}
          layers={[0, 1, 2, 3].map((i) => mix(0.25 * ease(frame, fourOf + i * 4, fourOf + i * 4 + 12), 1, labels[i]))}
          labels={labels}
          tipY={mix(260, 0, pop(frame, meetTip, 120, 12))}
          wave={fade(frame, meetTip + 10, 8) * fadeOut(frame, fourOf, 14)}
          expression={frame > places ? "happy" : "smile"}
          look={frame > project && frame < t.b.s1b5 ? 0.8 : 0}
        />
        <g opacity={fade(frame, project, 10) * fadeOut(frame, t.b.s1b5 - 6, 10)}>
          <Card x={1400} y={250} w={760} h={320} fill={C.night2}>
            <Txt x={1400} y={140} size={T.h2} weight={900}>
              Iceberg Data Platform
            </Txt>
            {["Catalog", "Storage", "Sign-in", "Engine", "Workspace"].map((label, i) => (
              <Pop key={label} p={pop(frame, project + 8 + i * 3)} cx={1110 + i * 145} cy={225}>
                <rect x={1110 + i * 145 - 69} y={200} width={138} height={50} rx={25} fill={[C.sun, C.teal, C.violet, C.coral, C.green][i]} />
                <Txt x={1110 + i * 145} y={225} size={22} weight={900} fill={C.ink}>
                  {label}
                </Txt>
              </Pop>
            ))}
            <Pop p={pop(frame, together)} cx={1400} cy={330}>
              <Person x={1300} y={392} scale={0.42} frame={frame} />
              <Txt x={1400} y={330} size={T.label} weight={900} fill={C.muted}>
                +
              </Txt>
              <Agent x={1500} y={340} scale={0.52} frame={frame} />
            </Pop>
          </Card>
        </g>
        <g opacity={fade(frame, places, 12)} transform={`translate(0 ${mix(30, 0, fade(frame, places, 12))})`}>
          <Txt x={1400} y={190} size={T.h1} weight={900}>
            Principles You Can See
          </Txt>
          <Txt x={1400} y={268} size={T.small} weight={800} fill={C.muted}>
            Four principles, shown on one real project
          </Txt>
        </g>
      </g>
    </Stage>
  );
};
