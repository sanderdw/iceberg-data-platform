import { continueRender, delayRender, staticFile } from "remotion";
import { FONT } from "./theme";

// Doto, Space Grotesk and Space Mono (SIL OFL), bundled in public/fonts so a render never needs the network.
if (typeof document !== "undefined") {
  const handle = delayRender("Loading fonts");
  const faces = [
    new FontFace(FONT.display, `url(${staticFile("fonts/doto.ttf")}) format("truetype")`, { weight: "100 900" }),
    new FontFace(FONT.body, `url(${staticFile("fonts/space-grotesk.ttf")}) format("truetype")`, { weight: "300 700" }),
    new FontFace(FONT.mono, `url(${staticFile("fonts/space-mono.ttf")}) format("truetype")`),
  ];
  Promise.all(faces.map((f) => f.load()))
    .then((loaded) => {
      loaded.forEach((f) => document.fonts.add(f));
      continueRender(handle);
    })
    .catch((error) => {
      console.error(error);
      continueRender(handle);
    });
}
