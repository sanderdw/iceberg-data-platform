# Third-party notices

OpenMetadata for the Iceberg Data Platform is licensed under Apache-2.0. It bundles no third-party
source files or assets.

Python dependencies are resolved from `uv.lock`. The service image installs FastAPI, Uvicorn and
httpx (MIT and BSD-3-Clause). Their upstream licenses remain applicable. Container images built here
include the installed dependencies and their distribution metadata.

`compose.openmetadata.yaml` is optional and only pulls upstream images for a local OpenMetadata
server: OpenMetadata (Apache-2.0) and Elasticsearch (under Elastic's own licenses). Nothing from them
is bundled.
