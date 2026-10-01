"""One process, two listeners: the gateway (published) and the agent's AG-UI endpoint (internal only).

They share sessions, run tickets and query results in memory, so the gateway and the agent always
agree on who is asking.
"""

import asyncio
import logging
import os
import signal
from contextlib import nullcontext

import uvicorn

from .app import create_app


class Server(uvicorn.Server):
    def capture_signals(self):
        return nullcontext()  # main() stops both servers together.


async def serve():
    gateway = create_app()
    host = os.environ.get("HOST", "0.0.0.0")
    servers = [
        Server(uvicorn.Config(gateway, host=host, port=int(os.environ.get("PORT", "3007")), proxy_headers=True,
                              access_log=False)),
        Server(uvicorn.Config(gateway.state.agent_app, host=host, port=int(os.environ.get("AGENT_PORT", "3017")),
                              access_log=False, timeout_keep_alive=75)),
    ]
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: [setattr(s, "should_exit", True) for s in servers])
    await asyncio.gather(*(s.serve() for s in servers))


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(serve())


if __name__ == "__main__":
    main()
