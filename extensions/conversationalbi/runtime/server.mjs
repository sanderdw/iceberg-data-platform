// The CopilotKit runtime between the chat UI and the agent. It runs on an internal network only:
// the gateway proxies the browser's chat requests here, and this forwards them to the agent's AG-UI
// endpoint. It holds no platform credentials, keeps conversations in memory and sends no telemetry.
import http from "node:http";
import { HttpAgent } from "@ag-ui/client";
import { CopilotRuntime, InMemoryAgentRunner } from "@copilotkit/runtime/v2";
import { createCopilotNodeListener } from "@copilotkit/runtime/v2/node";

const key = process.env.RUNTIME_KEY;
if (!key || key.length < 32) {
  console.error("RUNTIME_KEY is missing: run `uv run python -m scripts.setup` in extensions/conversationalbi.");
  process.exit(1);
}

const runtime = new CopilotRuntime({
  agents: {
    analyst: new HttpAgent({
      url: process.env.AGENT_URL ?? "http://cbi-gateway:3017/",
      headers: { "X-CBI-Runtime-Key": key },
    }),
  },
  runner: new InMemoryAgentRunner(),
  // Only the gateway's run ticket travels to the agent: no cookies, no other inbound headers.
  forwardHeaders: { allow: ["x-cbi-run-ticket"] },
});

const listener = createCopilotNodeListener({ runtime, basePath: "/api/copilotkit", activateChannels: false });

http
  .createServer((request, response) => {
    if (request.url === "/health") {
      response.writeHead(200, { "content-type": "application/json" }).end('{"status":"ok"}');
      return;
    }
    listener(request, response);
  })
  .listen(Number(process.env.PORT ?? 4000), "0.0.0.0", () => console.log("CopilotKit runtime listening"));
