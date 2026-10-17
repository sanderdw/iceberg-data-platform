"""Live check of Ingestion runs as Kubernetes Jobs, through MCP as an agent would.

Loads the public airports CSV template over the internet, then checks that a run cannot reach the
platform's own services or private addresses. Same accounts and settings as scripts/e2e_smoke.py
(the demo accounts of a platform installed with demo=true); run it from extensions/ingestion:

    EXTENSION_ORIGIN=https://ingest.<domain> OIDC_ISSUER=https://auth.<domain>/realms/iceberg \\
      PLATFORM_ENV=<.env with DEMO_*_PASSWORD> uv run --all-groups python -m scripts.k8s_smoke
"""

import asyncio
import os
import re
import time

from scripts.e2e_smoke import Agent, read_env, run, sign_in


async def main():
    env = {**read_env(".env.bridge"), **os.environ}
    origin, issuer = env["EXTENSION_ORIGIN"], env["OIDC_ISSUER"]
    redirect = f"http://localhost:{env.get('MCP_CALLBACK_PORT', '3010')}/callback"
    platform = read_env(env.get("PLATFORM_ENV", "../../.env"))
    tokens = {role: sign_in(issuer, "ext-ingestion-mcp", redirect, f"demo-{role}",
                            platform[f"DEMO_{role.upper()}_PASSWORD"]) for role in ("admin", "writer", "reader")}
    namespace = "k8s_" + str(int(time.time()))
    created = []
    async with Agent(origin + "/mcp", tokens["admin"]) as admin, Agent(origin + "/mcp", tokens["writer"]) as writer, \
            Agent(origin + "/mcp", tokens["reader"]) as reader:
        me = await writer.call("whoami")
        team = next(m for m in me["memberships"] if m["role"] == "writer")["team"]
        await admin.call("enable_environment", team=team, environment="development")
        database = next(d for d in me["databases"] if d["team"] == team and d["environment"] == "development")
        template = next(t for t in (await reader.call("list_templates"))["templates"] if t["id"] == "airports-csv")
        airports = await admin.call("create_pipeline", team=team, name=f"{namespace} airports", database=database["id"],
                                    namespace=namespace, source_type=template["sourceType"], source=template["source"],
                                    resources=template["resources"])
        created.append(airports)
        result = await run(writer, airports["id"])
        rows = {t["name"]: t["rows"] for t in result["summary"]["tables"]}
        assert rows["airports"] > 1000, rows
        print(f"PASS: a Job loaded {rows['airports']} airports from the internet")

        for host in ("bridge:3005", "keycloak:8080", "postgres:5432", "10.32.0.1:443"):
            try:
                probe = await admin.call("create_pipeline", team=team, name=f"{namespace} probe {host.split(':')[0]}",
                                         database=database["id"], namespace=namespace, source_type="rest_api",
                                         source={"client": {"base_url": f"http://{host}/"},
                                                 "resources": [{"name": "probe", "endpoint": {"path": "x"}}]})
            except RuntimeError as exc:
                # The gateway already refuses a private address when the pipeline is created.
                assert "[invalid_pipeline]" in str(exc), exc
                print(f"PASS: the gateway refuses {host}")
                continue
            created.append(probe)
            result = await writer.call("run_pipeline", pipeline=probe["id"], wait=600)
            assert result["status"] == "failed", result
            lines = "\n".join((await reader.call("get_run_logs", run=result["id"], tail=200))["lines"])
            assert re.search(r"(?i)resolve|name or service|getaddrinfo|refused|private|timed out", lines), lines[-2000:]
            print(f"PASS: a run cannot reach {host}")
        for pipeline in created:
            await admin.call("delete_pipeline", pipeline=pipeline["id"], confirm_name=pipeline["name"])


if __name__ == "__main__":
    asyncio.run(main())
