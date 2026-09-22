# Third-party notices

The application source is licensed under Apache-2.0. Bundled font files retain their original SIL Open Font License 1.1 and copyright notices:

| Asset | Upstream project | Included license |
| --- | --- | --- |
| Doto | [Doto](https://github.com/oliverlalan/Doto) | [doto-OFL.txt](public/fonts/doto-OFL.txt) |
| Space Grotesk | [Space Grotesk](https://github.com/floriankarsten/space-grotesk) | [space-grotesk-OFL.txt](public/fonts/space-grotesk-OFL.txt) |
| Space Mono | [Google Fonts](https://github.com/google/fonts/tree/main/ofl/spacemono) | [space-mono-OFL.txt](public/fonts/space-mono-OFL.txt) |

Fonts are served locally from `public/fonts`; both portals use the same source assets. Their license files accompany the font files in the container images.

`user_portal/notebook/examples/flights.yaml` is the unchanged Apache Ossie (incubating)
flights example from [commit 6d37d7b61183f6405e3a4e65ec883b10ebe0ce2c](https://github.com/apache/ossie/blob/6d37d7b61183f6405e3a4e65ec883b10ebe0ce2c/examples/flights.yaml),
licensed under Apache-2.0. Its license header is retained and the upstream NOTICE is
included beside it as `OSSIE-NOTICE.txt`: Copyright 2026 The Apache Software Foundation.
These files are bundled in the notebook image and copied into team workspaces.
`flights.product.yaml`, the generator and notebooks are local demonstration additions.

Python and npm dependencies are resolved from `uv.lock` and `package-lock.json`. Their upstream licenses remain applicable. Container images for Apache Polaris, PostgreSQL and RustFS are fetched separately by Compose and retain their own licenses and notices. Container images built here include the installed dependencies and their distribution metadata.

This repository does not redistribute the old presentation, its bundled reveal.js copy, personal Jupyter notebooks, private datasets, or saved credentials.
