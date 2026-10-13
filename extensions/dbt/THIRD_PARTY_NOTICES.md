# Third-party notices

The dbt extension is licensed under Apache-2.0. Bundled font files retain their original SIL Open
Font License 1.1 and copyright notices:

| Asset | Upstream project | Included license |
| --- | --- | --- |
| Doto | [Doto](https://github.com/oliverlalan/Doto) | [doto-OFL.txt](dbt_portal/public/fonts/doto-OFL.txt) |
| Space Grotesk | [Space Grotesk](https://github.com/floriankarsten/space-grotesk) | [space-grotesk-OFL.txt](dbt_portal/public/fonts/space-grotesk-OFL.txt) |
| Space Mono | [Google Fonts](https://github.com/google/fonts/tree/main/ofl/spacemono) | [space-mono-OFL.txt](dbt_portal/public/fonts/space-mono-OFL.txt) |

Python dependencies are resolved from `uv.lock`. The runner image installs dbt v2 OSS (`dbt-oss`,
Apache-2.0) with its Rust engine wheel from dbt Labs' GitHub release, DuckDB and its `iceberg`,
`avro` and `httpfs` extensions (MIT), PyIceberg (Apache-2.0) and PyYAML (MIT). Their upstream licenses remain
applicable. Container images built here include the installed dependencies and their distribution
metadata.
