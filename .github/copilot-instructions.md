# Review instructions

## No backwards compatibility

Every installation is treated as a clean setup. This project evolves fast and doesn't carry compatibility code.

When performing a code review, don't ask for code, tests, fallbacks or migrations whose only purpose is that an older installation, stored format, bucket name or old link keeps working. Don't ask for upgrade or migration notes in the docs, changelog or release notes. Removing such code is intended. Do report it when data or accounts created by the current code would break.

The exception is the published contract between separately released parts. Changes to `contracts/bridge/` must follow `contracts/bridge/COMPATIBILITY.md`. Interoperability with other tools through open interfaces, such as Iceberg REST, S3, OIDC and MCP, also counts.

## Extensions are independent projects

Each folder in `extensions/` has its own dependencies, tests, images and version, and its own `AGENTS.md`. Extensions never import from `server`, `user_portal` or another extension. They reach the platform only through the Extension Bridge and standard Iceberg REST and S3.

Don't report code repeated across the core and extensions as duplication. This includes `launcher/kube.py`, which is a copy of `server/kube.py`. Do report a fix applied to one copy when another copy has the same defect.

## Look closely at

- Secrets, tokens and one-use codes reaching logs, traces, URLs or API responses.
- Authorization that is not checked on every call, in the service layer.
- A capability that a person can use but a REST route or MCP tool can't, or the other way around.

