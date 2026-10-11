import React from "react";
import { useCurrentFrame } from "remotion";
import { count, ease, fade, fadeOut, mix, pop, settle } from "../lib/anim";
import { C, T } from "../lib/theme";
import { sceneTimes } from "../lib/time";
import { Agent, Person } from "../components/Characters";
import { Magnifier, Stopwatch } from "../components/Icons";
import { Pop, Source, Stage, Txt } from "../components/ui";

const t = sceneTimes("s2");

/** Visible from `inAt` (springy) until `outAt` (fades). */
function windowed(frame: number, inAt: number, outAt: number): number {
  return pop(frame, inAt) * fadeOut(frame, outAt - 10, 10);
}

const BigNumber: React.FC<{ frame: number; at: number; to: number; suffix?: string; label: string; y?: number; color?: string }> = ({
  frame, at, to, suffix = "%", label, y = 250, color = C.teal,
}) => (
  <g opacity={fade(frame, at, 8)}>
    <Txt x={960} y={y} size={T.big} weight={900} fill={color}>
      {`${count(frame, at, to)}${suffix}`}
    </Txt>
    <Txt x={960} y={y + 120} size={T.label} weight={800} fill={C.white}>
      {label}
    </Txt>
  </g>
);

export const Scene2: React.FC = () => {
  const frame = useCurrentFrame();

  // s2b1: ten people, nine light up.
  const ninety = t.at("s2b1", "Nine out of ten");
  const people = windowed(frame, t.b.s2b1, t.b.s2b2);
  // s2b2: a week of days, each with an agent.
  const daily = t.at("s2b2", "Almost three");
  const week = windowed(frame, t.b.s2b2, t.b.s2b3);
  // s2b3: two trust bars, in spoken order.
  const sixth = t.at("s2b3", "Only about one in six");
  const half = t.at("s2b3", "About half");
  const trust = windowed(frame, t.b.s2b3, t.b.s2b4);
  // s2b4: two stopwatches.
  const early = t.at("s2b4", "One careful study");
  const later = t.at("s2b4", "a little faster");
  const speed = windowed(frame, t.b.s2b4, t.b.s2b5);
  // s2b5: the amplifier.
  const amplify = t.at("s2b5", "It amplifies");
  const quote = windowed(frame, t.b.s2b5, t.length);

  return (
    <Stage>
      {/* s2b1 */}
      <g opacity={Math.min(1, people)}>
        <BigNumber frame={frame} at={ninety} to={90} label="of technology professionals use AI at work" />
        {Array.from({ length: 10 }, (_, i) => {
          const x = 960 + (i - 4.5) * 160;
          const lit = i < 9 ? ease(frame, ninety + i * 3, ninety + i * 3 + 8) : 0;
          return (
            <g key={i}>
              <Pop p={pop(frame, t.b.s2b1 + i * 2)} cx={x} cy={840}>
                <Person x={x} y={900} scale={0.62} frame={frame + i * 13} body={lit > 0.5 ? C.teal : C.dim} bodyShade={lit > 0.5 ? C.tealShade : C.ink} />
              </Pop>
              {lit > 0 && (
                <Pop p={pop(frame, ninety + i * 3 + 4)} cx={x} cy={650}>
                  <Agent x={x} y={650} scale={0.32} frame={frame} />
                </Pop>
              )}
            </g>
          );
        })}
        <Source text="Source: Google DORA, State of AI-assisted Software Development 2025" opacity={fade(frame, ninety, 10)} />
      </g>

      {/* s2b2 */}
      <g opacity={Math.min(1, week)}>
        <BigNumber frame={frame} at={daily} to={73} label="of developers use coding assistants or agents daily" />
        {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((day, i) => {
          const x = 960 + (i - 3) * 200;
          return (
            <Pop key={day} p={pop(frame, t.b.s2b2 + i * 2)} cx={x} cy={720}>
              <rect x={x - 80} y={600} width={160} height={250} rx={26} fill={C.ink} />
              <rect x={x - 80} y={590} width={160} height={250} rx={26} fill={C.night2} />
              <rect x={x - 80} y={590} width={160} height={64} rx={26} fill={C.violet} />
              <rect x={x - 80} y={624} width={160} height={30} fill={C.violet} />
              <Txt x={x} y={622} size={T.small} weight={900} fill={C.white}>
                {day}
              </Txt>
              <Pop p={pop(frame, daily + 6 + i * 4)} cx={x} cy={750}>
                <Agent x={x} y={760} scale={0.45} frame={frame + i * 7} />
              </Pop>
            </Pop>
          );
        })}
        <Source text="Source: Stack Overflow Developer Survey 2026" opacity={fade(frame, daily, 10)} />
      </g>

      {/* s2b3 */}
      <g opacity={Math.min(1, trust)}>
        <Txt x={960} y={170} size={T.h1} weight={900}>
          Trust has not kept up
        </Txt>
        {[
          { at: sixth, value: 16, x: 680, fill: C.coral, shade: C.coralShade, lines: ["trust AI with most", "everyday tasks"] },
          { at: half, value: 48, x: 1240, fill: C.teal, shade: C.tealShade, lines: ["trust AI when they", "can easily check"] },
        ].map((bar) => {
          const grow = settle(frame, bar.at);
          const h = bar.value * 10 * grow;
          return (
            <g key={bar.value} opacity={fade(frame, bar.at, 6)}>
              <rect x={bar.x - 120} y={820 - h + 10} width={240} height={h} rx={24} fill={bar.shade} />
              <rect x={bar.x - 120} y={820 - h} width={240} height={h} rx={24} fill={bar.fill} />
              <Txt x={bar.x} y={820 - h - 60} size={T.h1} weight={900} fill={bar.fill}>
                {`${count(frame, bar.at, bar.value, 24)}%`}
              </Txt>
              {bar.lines.map((line, i) => (
                <Txt key={line} x={bar.x} y={880 + i * 48} size={T.small} weight={800}>
                  {line}
                </Txt>
              ))}
            </g>
          );
        })}
        <Pop p={pop(frame, half + 14)} cx={1430} cy={420}>
          <Magnifier x={1430} y={420} scale={0.8} />
        </Pop>
        <Source text="Source: Stack Overflow Developer Survey 2026" opacity={fade(frame, sixth, 10)} />
      </g>

      {/* s2b4 */}
      <g opacity={Math.min(1, speed)}>
        <Txt x={960} y={170} size={T.h1} weight={900}>
          Speed is harder to pin down
        </Txt>
        {[
          { x: 600, at: early, label: "Early 2025", result: "slower", hand: 330, accent: C.coral },
          { x: 1320, at: later, label: "Late 2025", result: "a little faster", hand: 245, accent: C.teal },
        ].map((w) => (
          <Pop key={w.label} p={pop(frame, w.at)} cx={w.x} cy={560}>
            <Txt x={w.x} y={330} size={T.label} weight={900} fill={C.muted}>
              {w.label}
            </Txt>
            <Stopwatch x={w.x} y={570} scale={1.05} hand={mix(0, w.hand, ease(frame, w.at + 6, w.at + 40))} accent={w.accent} goal={270} />
            <Txt x={w.x} y={780} size={T.h2} weight={900} fill={w.accent} opacity={fade(frame, w.at + 36, 8)}>
              {w.result}
            </Txt>
          </Pop>
        ))}
        <Txt x={960} y={880} size={T.small} weight={700} fill={C.muted} opacity={fade(frame, early + 20, 10)}>
          Black mark: the same task without AI
        </Txt>
        <Source text="Source: METR, 2025 and 2026" opacity={fade(frame, early, 10)} />
      </g>

      {/* s2b5 */}
      <g opacity={Math.min(1, quote)}>
        <Txt x={960} y={170} size={T.h2} weight={900}>
          “AI doesn’t fix a team.
        </Txt>
        <Txt x={960} y={250} size={T.h2} weight={900} fill={C.sun} opacity={fade(frame, amplify, 8)}>
          It amplifies what’s already there.”
        </Txt>
        {/* Amplifier dial */}
        <g transform="translate(960 640)">
          <circle r={110} fill={C.ink} />
          <circle r={92} fill={C.dim} />
          <g transform={`rotate(${mix(-120, 120, ease(frame, amplify, amplify + 30))})`}>
            <circle r={70} fill={C.muted} />
            <rect x={-8} y={-70} width={16} height={46} rx={8} fill={C.sun} />
          </g>
          {Array.from({ length: 9 }, (_, i) => (
            <circle key={i} cx={Math.sin(((-120 + i * 30) * Math.PI) / 180) * 130} cy={-Math.cos(((-120 + i * 30) * Math.PI) / 180) * 130} r={7} fill={i * 30 - 120 <= mix(-120, 120, ease(frame, amplify, amplify + 30)) ? C.sun : C.dim} />
          ))}
        </g>
        {/* Neat structure grows neater, messy one grows messier */}
        {Array.from({ length: 6 }, (_, i) => {
          const shown = i < 3 ? pop(frame, t.b.s2b5 + i * 3) : pop(frame, amplify + 10 + (i - 3) * 5);
          return (
            <g key={`n${i}`} opacity={Math.min(1, shown)}>
              <rect x={400} y={872 - i * 72} width={200} height={62} rx={14} fill={C.tealShade} />
              <rect x={400} y={866 - i * 72} width={200} height={62} rx={14} fill={C.teal} />
            </g>
          );
        })}
        {Array.from({ length: 6 }, (_, i) => {
          const shown = i < 3 ? pop(frame, t.b.s2b5 + i * 3) : pop(frame, amplify + 10 + (i - 3) * 5);
          const tilt = [8, -14, 22, -30, 38, -46][i];
          const dx = [0, 30, -20, 60, -50, 90][i];
          return (
            <g key={`m${i}`} opacity={Math.min(1, shown)} transform={`rotate(${tilt * Math.min(1, shown)} ${1420 + dx} ${900 - i * 64})`}>
              <rect x={1320 + dx} y={872 - i * 64} width={200} height={62} rx={14} fill={C.coralShade} />
              <rect x={1320 + dx} y={866 - i * 64} width={200} height={62} rx={14} fill={C.coral} />
            </g>
          );
        })}
        <Source text="Source: Google DORA, State of AI-assisted Software Development 2025" opacity={fade(frame, t.b.s2b5, 10)} />
      </g>
    </Stage>
  );
};
