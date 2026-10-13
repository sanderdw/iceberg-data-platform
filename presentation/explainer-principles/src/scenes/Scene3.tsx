import React from "react";
import { useCurrentFrame } from "remotion";
import { ease, fade, fadeOut, mix, pop } from "../lib/anim";
import { C, T } from "../lib/theme";
import { sceneTimes } from "../lib/time";
import { PrincipleTitle } from "../components/Principle";
import { Chip, Cross, Doc, Pop, Stage, Txt } from "../components/ui";

const t = sceneTimes("s3");

/** The repository: an open box with a folder tab. Origin is its centre. */
const Repo: React.FC<{ x: number; y: number; scale?: number; glow?: number }> = ({ x, y, scale = 1, glow = 0 }) => (
  <g transform={`translate(${x} ${y}) scale(${scale})`}>
    <circle cx={0} cy={0} r={260} fill={C.teal} opacity={0.18 * glow} filter="url(#glow)" />
    <path d="M -230 -150 H -60 L -30 -180 H 120 L 150 -150 H 230 V 170 H -230 Z" fill={C.tealShade} />
    <rect x={-230} y={-120} width={460} height={290} rx={26} fill={C.teal} />
    <Txt x={0} y={30} size={T.h2} weight={900} fill={C.ink}>
      repo
    </Txt>
  </g>
);

/** Things outside the repo: a wiki page, a console click, a thought in somebody's head. */
const Outside: React.FC<{ kind: number; x: number; y: number }> = ({ kind, x, y }) => {
  if (kind === 0)
    return (
      <g>
        <rect x={x - 90} y={y - 110} width={180} height={220} rx={16} fill={C.paper} />
        <Txt x={x} y={y - 64} size={30} weight={900} fill={C.ink}>
          Wiki
        </Txt>
        {[0, 1, 2].map((i) => (
          <rect key={i} x={x - 60} y={y - 20 + i * 36} width={120 - i * 24} height={14} rx={7} fill={C.paperShade} />
        ))}
      </g>
    );
  if (kind === 1)
    return (
      <g>
        <rect x={x - 110} y={y - 80} width={220} height={150} rx={16} fill={C.dim} />
        <rect x={x - 80} y={y - 40} width={160} height={44} rx={22} fill={C.coral} />
        <Txt x={x} y={y - 18} size={24} weight={900} fill={C.ink}>
          Apply
        </Txt>
        <path d={`M ${x + 30} ${y} l 0 70 l 18 -18 l 16 30 l 14 -8 l -16 -28 l 26 -2 Z`} fill={C.white} />
      </g>
    );
  return (
    <g>
      <circle cx={x} cy={y + 30} r={70} fill={C.skin} />
      <path d={`M ${x - 72} ${y + 20} C ${x - 70} ${y - 60} ${x + 70} ${y - 60} ${x + 72} ${y + 20} C ${x + 40} ${y - 10} ${x - 40} ${y - 10} ${x - 72} ${y + 20} Z`} fill={C.ink} />
      <ellipse cx={x + 90} cy={y - 90} rx={60} ry={44} fill={C.white} />
      <circle cx={x + 50} cy={y - 34} r={10} fill={C.white} />
    </g>
  );
};

const TREE = [
  { line: "README.md", phrase: "the docs" },
  { line: "docs/", phrase: "the docs" },
  { line: "install.sh", phrase: "The installer" },
  { line: "compose.yaml", phrase: "the deployment" },
  { line: ".github/workflows/", phrase: "the release pipeline" },
];

export const Scene3: React.FC = () => {
  const frame = useCurrentFrame();
  const vis = (inAt: number, outAt: number) => Math.min(1, fade(frame, inAt, 8)) * fadeOut(frame, outAt - 10, 10);

  // s3b2
  const into = t.b.s3b2 + 4;
  const crosses = [t.at("s3b2", "a wiki page"), t.at("s3b2", "a console click"), t.at("s3b2", "somebody's head")];
  // s3b4
  const drift = t.at("s3b4", "drift apart");
  const fails = t.at("s3b4", "a test fails");
  // s3b5
  const video = t.at("s3b5", "And so does this video");

  return (
    <Stage>
      <PrincipleTitle frame={frame} index={1} at={t.b.s3b1} dock={t.b.s3b2} />

      {/* s3b2: what matters goes into the repo; wiki, clicks and heads are out */}
      <g opacity={vis(t.b.s3b2, t.b.s3b3)}>
        <Pop p={pop(frame, t.b.s3b2)} cx={1300} cy={600}>
          <Repo x={1300} y={600} glow={1} />
        </Pop>
        {["Docs", "Deployment", "Architecture"].map((label, i) => {
          const fly = ease(frame, into + i * 8, into + i * 8 + 22);
          return (
            <g key={label} opacity={fade(frame, into + i * 8, 6) * (1 - ease(frame, into + i * 8 + 18, into + i * 8 + 24))}>
              <Chip x={mix(960, 1300, fly)} y={mix(330 + i * 0, 560, fly) - Math.sin(fly * Math.PI) * 120} text={label} fill={C.white} shade={C.paperShade} size={T.small} />
            </g>
          );
        })}
        {[0, 1, 2].map((i) => {
          const x = 330 + i * 240;
          const crossed = crosses[i];
          return (
            <Pop key={i} p={pop(frame, t.at("s3b2", "Nothing important") + i * 4)} cx={x} cy={640}>
              <g opacity={mix(1, 0.4, fade(frame, crossed, 8))}>
                <Outside kind={i} x={x} y={640} />
              </g>
              {frame > crossed && (
                <Pop p={pop(frame, crossed)} cx={x} cy={640}>
                  <Cross x={x} y={640} r={44} />
                </Pop>
              )}
            </Pop>
          );
        })}
      </g>

      {/* s3b3: the repository tree */}
      <g opacity={vis(t.b.s3b3, t.b.s3b4)}>
        <rect x={560} y={260} width={800} height={600} rx={30} fill={C.ink} opacity={0.7} />
        <rect x={560} y={250} width={800} height={600} rx={30} fill={C.night2} />
        <Txt x={620} y={315} size={T.small} weight={900} fill={C.muted} anchor="start">
          iceberg-data-platform/
        </Txt>
        {TREE.map((row, i) => {
          const at = t.at("s3b3", row.phrase) + (i === 1 ? 6 : 0);
          const y = 400 + i * 90;
          return (
            <Pop key={row.line} p={pop(frame, at)} cx={700} cy={y}>
              <rect x={640} y={y - 24} width={44} height={40} rx={8} fill={row.line.endsWith("/") ? C.sun : C.paper} />
              <Txt x={710} y={y - 4} size={T.label} weight={900} anchor="start">
                {row.line}
              </Txt>
            </Pop>
          );
        })}
      </g>

      {/* s3b4: the contract is a file too; drift fails a test */}
      <g opacity={vis(t.b.s3b4, t.b.s3b5)}>
        <Pop p={pop(frame, t.b.s3b4)} cx={760} cy={560}>
          <Doc x={760} y={560} w={340} h={420} title="openapi.yaml" accent={C.teal} lines={6} titleSize={36} />
        </Pop>
        <g transform={`translate(${mix(0, 160, ease(frame, drift, drift + 18))} ${mix(0, 60, ease(frame, drift, drift + 18))}) rotate(${mix(0, 10, ease(frame, drift, drift + 18))} 1160 560)`}>
          <Pop p={pop(frame, t.b.s3b4 + 8)} cx={1160} cy={560}>
            <Doc x={1160} y={560} w={340} h={420} title="api.py" accent={C.violet} lines={6} titleSize={36} />
          </Pop>
        </g>
        <Pop p={pop(frame, fails)} cx={960} cy={560}>
          <Cross x={960} y={560} r={56} />
        </Pop>
        <Pop p={pop(frame, fails + 4)} cx={960} cy={880}>
          <Chip x={960} y={880} text="test fails" fill={C.red} shade={C.coralShade} size={T.label} />
        </Pop>
      </g>

      {/* s3b5: the agent's instructions, and this video, are in the repo too */}
      <g opacity={vis(t.b.s3b5, t.length)}>
        <Repo x={1300} y={640} scale={0.9} glow={fade(frame, video, 10)} />
        {[
          { title: "AGENTS.md", accent: C.teal, at: t.at("s3b5", "in an agents file") },
          { title: "SKILL.md", accent: C.sun, at: t.at("s3b5", "a set of skills") },
        ].map((d, i) => {
          const fly = ease(frame, video - 30, video - 8);
          return (
            <g key={d.title} opacity={fade(frame, d.at, 6) * (1 - ease(frame, video - 10, video - 4))}>
              <g transform={`translate(${mix(0, 1250 - (420 + i * 300), fly)} ${mix(0, 80, fly)}) scale(1)`}>
                <Doc x={420 + i * 300} y={520} w={240} h={300} title={d.title} accent={d.accent} lines={4} titleSize={32} />
              </g>
            </g>
          );
        })}
        <Pop p={pop(frame, video)} cx={600} cy={560}>
          <rect x={360} y={420} width={480} height={290} rx={20} fill={C.ink} />
          {Array.from({ length: 8 }, (_, i) => (
            <g key={i}>
              <rect x={380 + i * 58} y={434} width={30} height={22} rx={5} fill={C.dim} />
              <rect x={380 + i * 58} y={674} width={30} height={22} rx={5} fill={C.dim} />
            </g>
          ))}
          <rect x={380} y={470} width={440} height={190} rx={12} fill={C.night2} />
          <Txt x={600} y={565} size={T.h1} weight={900} fill={C.teal}>
            {"</>"}
          </Txt>
          <Txt x={600} y={780} size={T.small} weight={800} fill={C.muted}>
            script · sources · music
          </Txt>
        </Pop>
      </g>
    </Stage>
  );
};
