import React from "react";
import { useCurrentFrame } from "remotion";
import { bob, ease, fade, fadeOut, mix, pop } from "../lib/anim";
import { C, T } from "../lib/theme";
import { sceneTimes } from "../lib/time";
import { Iceberg } from "../components/Iceberg";
import { PriceTag } from "../components/Icons";
import { Pop, Sparkle, Stage, Txt } from "../components/ui";

const t = sceneTimes("s3");

/** A pair of curious eyes floating above the water, looking at Tip. */
const Eyes: React.FC<{ x: number; y: number; look: number; frame: number }> = ({ x, y, look, frame }) => {
  const blink = (frame + x) % 110 > 105 ? 0.15 : 1;
  return (
    <g transform={`translate(${x} ${y + bob(frame, 6, 80, x / 300)})`}>
      <ellipse cx={-20} cy={0} rx={16} ry={20 * blink} fill={C.white} />
      <ellipse cx={20} cy={0} rx={16} ry={20 * blink} fill={C.white} />
      <circle cx={-20 + look * 7} cy={2} r={8} fill={C.ink} />
      <circle cx={20 + look * 7} cy={2} r={8} fill={C.ink} />
    </g>
  );
};

export const Scene3: React.FC = () => {
  const frame = useCurrentFrame();
  const dive = ease(frame, t.b.s3b2, t.b.s3b2 + 30);
  const cx = 760;
  const waterY = mix(600, 290, dive);
  const scale = 0.85;
  const lit = [
    t.at("s3b3", "Principles"),
    t.at("s3b3", "Interfaces"),
    t.at("s3b3", "Context"),
    t.at("s3b3", "checks"),
  ];
  const cheap = t.at("s3b4", "AI made");
  const moved = t.at("s3b4", "So the real work");
  const layers = lit.map((at) => ease(frame, at, at + 10));
  const spot = ease(frame, moved, moved + 24);
  const glyphs = ["</>", "{ }", "fn()", "</>"];
  const peakY = waterY - 162 * scale;

  return (
    <Stage>
      <Iceberg
        id="s3"
        frame={frame}
        cx={cx}
        waterY={waterY}
        scale={scale}
        reveal={mix(0.1, 0.85, dive)}
        layers={layers}
        labels={layers}
        expression={frame > moved ? "happy" : frame > t.b.s3b2 ? "wow" : "smile"}
        look={frame > t.b.s3b2 ? 0.4 : 0}
      />
      {/* s3b1: code sparkles on the tip, and everyone is watching */}
      <g opacity={fadeOut(frame, t.b.s3b2, 12)}>
        {glyphs.map((g, i) => {
          const x = cx + [-190, 170, -120, 210][i];
          const y = peakY + [-40, -70, -150, -170][i];
          return (
            <Pop key={i} p={pop(frame, t.b.s3b1 + 6 + i * 5)} cx={x} cy={y}>
              <Txt x={x} y={y + bob(frame, 6, 60, i)} size={T.label} weight={900} fill={[C.teal, C.sun, C.violet, C.coral][i]}>
                {g}
              </Txt>
            </Pop>
          );
        })}
        {[
          [1300, 260],
          [1560, 380],
          [1180, 420],
          [300, 300],
          [460, 180],
        ].map(([x, y], i) => (
          <Pop key={i} p={pop(frame, t.at("s3b1", "everyone sees") + i * 4)} cx={x} cy={y}>
            <Eyes x={x} y={y} look={x > cx ? -1 : 1} frame={frame} />
          </Pop>
        ))}
      </g>
      {/* s3b4: the price tag on the tip drops */}
      <g opacity={fade(frame, t.b.s3b4 - 6, 8)}>
        <line x1={cx + 30} y1={peakY + 20} x2={cx + 150} y2={peakY - 40} stroke={C.muted} strokeWidth={4} />
        <PriceTag
          x={cx + 150}
          y={peakY - 40}
          scale={mix(1.35, 1.0, ease(frame, cheap + 10, cheap + 24))}
          angle={mix(-8, 10, ease(frame, cheap + 10, cheap + 24)) + Math.sin(frame / 14) * 2}
          text={frame < cheap + 16 ? "€€€" : "€"}
        />
        {frame > cheap + 14 && <Sparkle x={cx + 330} y={peakY - 70} r={20} opacity={fadeOut(frame, cheap + 30, 10)} />}
      </g>
      {/* The spotlight moves from the tip to the work under the water */}
      <ellipse
        cx={mix(cx, cx, spot)}
        cy={mix(peakY + 40, waterY + 290, spot)}
        rx={mix(220, 420, spot)}
        ry={mix(160, 330, spot)}
        fill="url(#spot)"
        opacity={fade(frame, moved - 4, 10)}
      />
    </Stage>
  );
};
