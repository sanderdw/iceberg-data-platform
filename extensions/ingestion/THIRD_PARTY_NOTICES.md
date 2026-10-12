# Third-party notices

Ingestion (dlt) is licensed under Apache-2.0. Bundled font files retain their original SIL Open
Font License 1.1 and copyright notices:

| Asset | Upstream project | Included license |
| --- | --- | --- |
| Doto | [Doto](https://github.com/oliverlalan/Doto) | [doto-OFL.txt](ingestion/public/fonts/doto-OFL.txt) |
| Space Grotesk | [Space Grotesk](https://github.com/floriankarsten/space-grotesk) | [space-grotesk-OFL.txt](ingestion/public/fonts/space-grotesk-OFL.txt) |
| Space Mono | [Google Fonts](https://github.com/google/fonts/tree/main/ofl/spacemono) | [space-mono-OFL.txt](ingestion/public/fonts/space-mono-OFL.txt) |

Python dependencies are resolved from `uv.lock`. The service image installs FastAPI, Uvicorn,
httpx, joserfc, the MCP SDK, croniter, cryptography and the Docker SDK for Python (MIT, BSD and
Apache-2.0). The runner image installs dlt (Apache-2.0) with its `pyiceberg` and `sql_database`
extras, PyIceberg (Apache-2.0), PyArrow (Apache-2.0), SQLAlchemy (MIT), pg8000 (BSD-3-Clause),
PyMySQL (MIT) and s3fs (BSD-3-Clause). No copyleft database driver is included. Their upstream
licenses remain applicable. Container images built here include the installed dependencies and
their distribution metadata.

Templates point at public data that is not bundled: CBS StatLine (Statistics Netherlands, CC BY
4.0, which requires attribution) and the airport codes of datahub.io's core datasets (ODC Public
Domain Dedication and License).
