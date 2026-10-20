import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { ease, mix } from "../lib/anim";
import { OVERLAP } from "../lib/time";

/**
 * Zoom transition around a scene: it zooms in from slightly small as it appears and flies past the
 * camera (scales up and fades) during its last OVERLAP frames, while the next scene zooms in.
 */
export const SceneFrame: React.FC<{ length: number; first?: boolean; children: React.ReactNode }> = ({
  length, first = false, children,
}) => {
  const frame = useCurrentFrame();
  const enter = first ? ease(frame, 0, 12) : ease(frame, 0, OVERLAP);
  const leave = ease(frame, length - OVERLAP, length);
  const scale = mix(first ? 1 : 0.8, 1, enter) * mix(1, 1.45, leave);
  return (
    <AbsoluteFill style={{ opacity: enter * (1 - leave), transform: `scale(${scale})` }}>{children}</AbsoluteFill>
  );
};
