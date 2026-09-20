# Third-party notices

The application source is licensed under Apache-2.0. Bundled font files retain their original SIL Open Font License 1.1 and copyright notices:

| Asset | Upstream project | Included license |
| --- | --- | --- |
| Doto | [Doto](https://github.com/oliverlalan/Doto) | [doto-OFL.txt](public/fonts/doto-OFL.txt) |
| Space Grotesk | [Space Grotesk](https://github.com/floriankarsten/space-grotesk) | [space-grotesk-OFL.txt](public/fonts/space-grotesk-OFL.txt) |
| Space Mono | [Google Fonts](https://github.com/google/fonts/tree/main/ofl/spacemono) | [space-mono-OFL.txt](public/fonts/space-mono-OFL.txt) |

Fonts are served locally from `public/fonts`; both portals use the same source assets. Their license files accompany the font files in the container images.

Python and npm dependencies are resolved from `uv.lock`, `user_portal/reporting/uv.lock` and `package-lock.json`. The separately locked reporting image includes dbt Charts, dbt-duckdb and DuckDB. Their upstream licenses remain applicable. Container images for Apache Polaris, PostgreSQL and RustFS are fetched separately by Compose and retain their own licenses and notices. Container images built here include the installed dependencies and their distribution metadata.

This repository does not redistribute the old presentation, its bundled reveal.js copy, personal Jupyter notebooks, private datasets, or saved credentials.
