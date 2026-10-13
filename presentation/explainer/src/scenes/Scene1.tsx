import React from "react";
import { useCurrentFrame } from "remotion";
import { bob, ease, fade, fadeOut, mix, pop } from "../lib/anim";
import { C, T } from "../lib/theme";
import { sceneTimes } from "../lib/time";
import { Agent } from "../components/Characters";
import { Iceberg } from "../components/Iceberg";
import { ChatBubble, Laptop, MeetingCard } from "../components/Icons";
import { Chip, Pop, Sparkle, Stage, Txt } from "../components/ui";

const t = sceneTimes("s1");

/** The Tuesday desk: window at dawn, laptop, mug. Shared with scene 6 and 7. */
export const Desk: React.FC<{ frame: number }> = ({ frame }) => (
  <g>
    <rect x={0} y={800} width={1920} height={280} fill="#0D1B45" />
    {/* Window at dawn */}
    <rect x={150} y={250} width={330} height={400} rx={26} fill={C.night2} />
    <clipPath id="pane">
      <rect x={172} y={272} width={286} height={356} rx={16} />
    </clipPath>
    <g clipPath="url(#pane)">
      <rect x={172} y={272} width={286} height={356} fill={C.dawn} />
      <rect x={172} y={520} width={286} height={120} fill="#4B3F8E" />
      <circle cx={315} cy={640 - mix(0, 60, ease(frame, 0, 400))} r={78} fill={C.sun} opacity={0.9} />
      <circle cx={315} cy={640 - mix(0, 60, ease(frame, 0, 400))} r={120} fill={C.sun} opacity={0.25} filter="url(#glow)" />
    </g>
    <rect x={306} y={272} width={18} height={356} fill={C.night2} />
    <rect x={172} y={442} width={286} height={16} fill={C.night2} />
    {/* Desk */}
    <rect x={330} y={782} width={1260} height={34} rx={14} fill={C.dim} />
    <rect x={330} y={770} width={1260} height={30} rx={14} fill={C.muted} />
    <rect x={400} y={816} width={30} height={200} fill={C.dim} />
    <rect x={1490} y={816} width={30} height={200} fill={C.dim} />
    {/* Mug with steam */}
    <rect x={1290} y={690} width={74} height={84} rx={18} fill={C.coralShade} />
    <rect x={1290} y={690} width={60} height={84} rx={18} fill={C.coral} />
    <path d="M 1364 710 a 22 22 0 0 1 0 44" stroke={C.coralShade} strokeWidth={12} fill="none" />
    {[0, 1].map((i) => (
      <path
        key={i}
        d={`M ${1312 + i * 22} 670 q 14 -24 0 -48 q -14 -24 0 -48`}
        stroke={C.white}
        strokeWidth={7}
        strokeLinecap="round"
        fill="none"
        opacity={0.25 + 0.15 * Math.sin(frame / 12 + i)}
        transform={`translate(0 ${bob(frame, 6, 70, i)})`}
      />
    ))}
  </g>
);

/** Code lines streaming into the laptop screen. */
const CodeScreen: React.FC<{ frame: number; from: number }> = ({ frame, from }) => {
  const colors = [C.teal, C.violet, C.sun, C.coral, C.muted];
  return (
    <g opacity={fade(frame, from, 8)}>
      <rect x={-194} y={-274} width={388} height={44} rx={12} fill={C.night2} />
      <circle cx={-168} cy={-252} r={8} fill={C.coral} />
      <circle cx={-144} cy={-252} r={8} fill={C.sun} />
      <circle cx={-120} cy={-252} r={8} fill={C.green} />
      {Array.from({ length: 9 }, (_, i) => {
        const w = 60 + ((i * 53) % 180);
        const indent = (i % 3) * 26;
        const shown = Math.max(0, Math.min(1, (frame - from - i * 4) / 6));
        return (
          <rect key={i} x={-170 + indent} y={-212 + i * 20} width={w * shown} height={11} rx={5.5} fill={colors[i % colors.length]} />
        );
      })}
    </g>
  );
};

export const Scene1: React.FC = () => {
  const frame = useCurrentFrame();
  const message = t.at("s1b1", "A message");
  const oneSentence = t.at("s1b2", "One sentence");
  const meeting = t.at("s1b2", "Ten years");
  const today = t.at("s1b2", "Today");
  const pan = ease(frame, t.b.s1b3 - 10, t.b.s1b3 + 16);
  const meetTip = t.at("s1b3", "Meet Tip");
  const underWater = t.at("s1b3", "most of Tip");
  const title = t.b.s1b4 + 8;

  const bubble = pop(frame, message);
  const pulse = 1 + 0.06 * Math.max(0, Math.sin(Math.min(Math.PI, ((frame - oneSentence) / 12) * Math.PI)));
  const meetingIn = pop(frame, meeting);
  const meetingOut = ease(frame, today - 4, today + 8);
  const rise = pop(frame, meetTip, 120, 12);

  return (
    <Stage>
      {/* The room slides up as the camera pans down to the sea */}
      <g transform={`translate(0 ${-1080 * pan})`}>
        <Desk frame={frame} />
        <Pop p={pop(frame, 4)} cx={400} cy={150}>
          <Chip x={400} y={150} text="Tuesday · 09:15" fill={C.violet} shade={C.violetShade} color={C.white} size={T.small} />
        </Pop>
        <Laptop x={860} y={770} screen={<CodeScreen frame={frame} from={today + 4} />} />
        {frame >= today + 4 && (
          <Pop p={pop(frame, today + 10)} cx={1170} cy={640}>
            <Agent x={1170} y={640} scale={0.7} frame={frame} />
          </Pop>
        )}
        <Pop p={bubble} cx={1300} cy={420}>
          <g transform={`translate(1300 380) scale(${frame > oneSentence ? pulse : 1}) translate(-1300 -380)`}>
            <ChatBubble x={1300} y={380} w={820} lines={["Can we load this table from", "Statistics Netherlands,", "every day?"]} size={T.label} />
            {/* Small table icon */}
            {[0, 1, 2].map((r) =>
              [0, 1, 2].map((c) => (
                <rect key={`${r}${c}`} x={1570 + c * 34} y={330 + r * 26} width={28} height={20} rx={5} fill={r === 0 ? C.teal : C.paperShade} />
              )),
            )}
          </g>
        </Pop>
        <Sparkle x={1700} y={250} r={22} opacity={fade(frame, oneSentence, 6) * fadeOut(frame, oneSentence + 24, 10)} />
        <g opacity={1 - meetingOut} transform={`translate(1560 640) scale(1 ${1 - meetingOut}) translate(-1560 -640)`}>
          <Pop p={meetingIn} cx={1560} cy={640}>
            <MeetingCard x={1560} y={640} scale={0.85} />
          </Pop>
        </g>
      </g>

      {/* The sea, with Tip */}
      <g transform={`translate(0 ${1080 * (1 - pan)})`}>
        <Iceberg
          id="s1"
          frame={frame}
          cx={960}
          waterY={470}
          scale={0.72}
          reveal={ease(frame, underWater, underWater + 24)}
          tipY={mix(260, 0, rise)}
          wave={fade(frame, meetTip + 10, 8) * fadeOut(frame, underWater, 14)}
          expression={frame > t.at("s1b4", "whole story") ? "wink" : frame > underWater ? "happy" : "smile"}
          look={frame > underWater ? 0 : -0.5}
        />
        <g opacity={fade(frame, title, 14)} transform={`translate(0 ${mix(30, 0, fade(frame, title, 14))})`}>
          <Txt x={960} y={140} size={T.h1 + 14} weight={900}>
            The Part Under the Water
          </Txt>
          <Txt x={960} y={222} size={T.label} weight={700} fill={C.muted}>
            How to architect software natively with AI
          </Txt>
        </g>
      </g>
    </Stage>
  );
};
