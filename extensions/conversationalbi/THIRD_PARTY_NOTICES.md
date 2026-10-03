# Third-party notices

Conversational BI is licensed under Apache-2.0. Bundled font files retain their original SIL Open
Font License 1.1 and copyright notices:

| Asset | Upstream project | Included license |
| --- | --- | --- |
| Doto | [Doto](https://github.com/oliverlalan/Doto) | [doto-OFL.txt](web/public/fonts/doto-OFL.txt) |
| Space Grotesk | [Space Grotesk](https://github.com/floriankarsten/space-grotesk) | [space-grotesk-OFL.txt](web/public/fonts/space-grotesk-OFL.txt) |
| Space Mono | [Google Fonts](https://github.com/google/fonts/tree/main/ofl/spacemono) | [space-mono-OFL.txt](web/public/fonts/space-mono-OFL.txt) |

Python and npm dependencies are resolved from `uv.lock`, `web/package-lock.json` and
`runtime/package-lock.json`. The chat UI bundles CopilotKit, Chart.js and React, all MIT-licensed.
Their upstream licenses remain applicable. Container images built here include the installed
dependencies and their distribution metadata.
