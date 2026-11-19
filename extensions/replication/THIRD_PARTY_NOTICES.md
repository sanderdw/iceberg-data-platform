# Third-party notices

Replication (OLake) is licensed under Apache-2.0. Bundled font files retain their original SIL Open
Font License 1.1 and copyright notices:

| Asset | Upstream project | Included license |
| --- | --- | --- |
| Doto | [Doto](https://github.com/oliverlalan/Doto) | [doto-OFL.txt](replication/public/fonts/doto-OFL.txt) |
| Space Grotesk | [Space Grotesk](https://github.com/floriankarsten/space-grotesk) | [space-grotesk-OFL.txt](replication/public/fonts/space-grotesk-OFL.txt) |
| Space Mono | [Google Fonts](https://github.com/google/fonts/tree/main/ofl/spacemono) | [space-mono-OFL.txt](replication/public/fonts/space-mono-OFL.txt) |

Python dependencies are resolved from `uv.lock`. The service image installs FastAPI, Uvicorn,
httpx, joserfc, the MCP SDK, croniter, cryptography and the Docker SDK for Python (MIT, BSD and
Apache-2.0). Their upstream licenses remain applicable.

The runner image is built on OLake's image `olakego/source-postgres` and redistributes it unchanged:

| Component | License |
| --- | --- |
| [OLake](https://github.com/datazip-inc/olake-go) (the `olake` binary and its Iceberg Java writer) | Apache-2.0 |
| OLake's Go dependencies | Apache-2.0, MIT, BSD-2/3-Clause; `go-sql-driver/mysql` and `hashicorp/go-multierror` are MPL-2.0, unmodified |
| OLake's Java writer dependencies (Apache Iceberg, Hadoop, Hive, Parquet, Arrow, AWS SDK, Netty, Jackson, gRPC, PostgreSQL JDBC, ...) | Apache-2.0, BSD-2-Clause (PostgreSQL JDBC), EPL-2.0 or EDL (Jersey, Jakarta, Eclipse Collections) |
| Eclipse Temurin JRE 17 | GPL-2.0 with the Classpath Exception |
| Ubuntu 24.04 base packages | Their own licenses (GPL, LGPL, MIT, BSD and others); sources at ubuntu.com |
| CPython 3.14 (python-build-standalone, installed with uv) | PSF-2.0 |

The runner's own start-up program uses Python's standard library only. OLake's telemetry is turned
off in every run (`TELEMETRY_DISABLED=true`). The licenses of each component, as shipped by OLake, are
in the image (`/opt/java/openjdk/legal`, `/usr/share/doc`).
