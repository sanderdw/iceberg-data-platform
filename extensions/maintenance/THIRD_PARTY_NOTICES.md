# Third-party notices

Maintenance is licensed under Apache-2.0. Bundled font files retain their original SIL Open Font
License 1.1 and copyright notices:

| Asset | Upstream project | Included license |
| --- | --- | --- |
| Doto | [Doto](https://github.com/oliverlalan/Doto) | [doto-OFL.txt](maintenance/public/fonts/doto-OFL.txt) |
| Space Grotesk | [Space Grotesk](https://github.com/floriankarsten/space-grotesk) | [space-grotesk-OFL.txt](maintenance/public/fonts/space-grotesk-OFL.txt) |
| Space Mono | [Google Fonts](https://github.com/google/fonts/tree/main/ofl/spacemono) | [space-mono-OFL.txt](maintenance/public/fonts/space-mono-OFL.txt) |

Python dependencies are resolved from `uv.lock`. The service image installs FastAPI, Uvicorn, httpx,
joserfc, the MCP SDK, croniter and the Docker SDK for Python (MIT, BSD and Apache-2.0).

The runner image is one static Go binary built from `runner/go.mod` and `runner/go.sum`, on a
distroless base image. It links Apache iceberg-go and Apache Arrow for Go (Apache-2.0), the AWS SDK
for Go v2 and smithy-go (Apache-2.0), the Go Cloud Development Kit (Apache-2.0), and their
dependencies under BSD, MIT and Apache-2.0 licenses. Their upstream licenses remain applicable.
Container images built here include the installed dependencies and their distribution metadata.
