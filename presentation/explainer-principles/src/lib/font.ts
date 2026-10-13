import { continueRender, delayRender, staticFile } from "remotion";
import { FONT } from "./theme";

// Nunito (SIL OFL), bundled in public/fonts so a render never depends on the network.
if (typeof document !== "undefined") {
  const handle = delayRender("Loading Nunito");
  const face = new FontFace(FONT, `url(${staticFile("fonts/Nunito.ttf")}) format("truetype")`, { weight: "200 1000" });
  face
    .load()
    .then((loaded) => {
      document.fonts.add(loaded);
      continueRender(handle);
    })
    .catch((error) => {
      console.error(error);
      continueRender(handle);
    });
}
