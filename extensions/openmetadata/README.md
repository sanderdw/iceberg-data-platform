# OpenMetadata for the Iceberg Data Platform

Make data products findable: every data share that its owning team lists in the data catalog
appears in [OpenMetadata](https://open-metadata.org) as a data product, with its tables, views,
column descriptions and semantic model metrics. People find it there and ask the owning team for a
data share. A listing carries metadata only.

[`CONTEXT.md`](CONTEXT.md) defines the words this extension uses for both sides, such as listing,
label and term.

This is an extension: its own Compose project (`iceberg-openmetadata`), dependencies, image,
version and releases. It uses the platform only through the **Extension Bridge v1**
(`contracts/bridge` in the platform repository), capability `listings` (contract 0.1.1 or later).

## Install

On an installed platform, with Docker:

```bash
curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/download/openmetadata-v0.1.1/install.sh | sh
```

It asks for the OpenMetadata address and a bot token; `sh -s -- --demo` also starts a local OpenMetadata.

## Documentation

- [OpenMetadata](https://sanderdw.github.io/iceberg-data-platform/openmetadata/): what is published, install, settings and limits.
- [OpenMetadata internals](https://sanderdw.github.io/iceberg-data-platform/develop/openmetadata/): architecture, install from source and development.
- [Changelog](CHANGELOG.md)

## License

Apache-2.0. See [NOTICE](NOTICE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
