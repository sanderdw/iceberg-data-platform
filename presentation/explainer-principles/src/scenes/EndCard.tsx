import React from "react";
import { useCurrentFrame } from "remotion";
import script from "../../script/script.json";
import { ease, fade, mix, pop } from "../lib/anim";
import { C, T } from "../lib/theme";
import { Iceberg } from "../components/Iceberg";
import { Pop, Stage, Txt } from "../components/ui";

/** End card: title, Tip, sources, credit and the personal-project line. */
export const EndCard: React.FC = () => {
  const frame = useCurrentFrame();
  const enter = ease(frame, 0, 18);
  const sources = script.endCard.sources.replace(/^Sources: /, "").split(" · ");
  const half = Math.ceil(sources.length / 2);
  return (
    <Stage style={{ opacity: enter }}>
      <rect width={1920} height={1080} fill={C.night} opacity={0.85} />
      <Txt x={960} y={150} size={T.h1 + 10} weight={900} opacity={fade(frame, 6, 12)}>
        {script.endCard.title}
      </Txt>
      <g opacity={fade(frame, 10, 14)}>
        <clipPath id="end-window">
          <rect x={560} y={210} width={800} height={430} rx={40} />
        </clipPath>
        <g clipPath="url(#end-window)">
          <Iceberg id="end" frame={frame} cx={960} waterY={380} scale={0.42} reveal={0.9} layers={[1, 1, 1, 1]} expression="happy" wave={fade(frame, 20, 10)} />
        </g>
      </g>
      <g opacity={fade(frame, 24, 12)}>
        <Txt x={960} y={690} size={T.caption} weight={800} fill={C.muted}>
          Sources
        </Txt>
        <Txt x={960} y={736} size={T.small} weight={800}>
          {sources.slice(0, half).join(" · ")}
        </Txt>
        <Txt x={960} y={784} size={T.small} weight={800}>
          {sources.slice(half).join(" · ")}
        </Txt>
      </g>
      <Pop p={pop(frame, 40)} cx={960} cy={880}>
        <Txt x={960} y={880} size={T.h2} weight={900} fill={C.sun}>
          {script.endCard.credit}
        </Txt>
      </Pop>
      <Txt x={960} y={950} size={T.caption} weight={700} fill={C.muted} opacity={fade(frame, 50, 12)}>
        {script.endCard.disclaimer}
      </Txt>
    </Stage>
  );
};
