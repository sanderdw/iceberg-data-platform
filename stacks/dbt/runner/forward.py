"""A loopback TCP forwarder for vended storage credentials.

Polaris vends the browser-facing S3 endpoint (for example http://localhost:9000). Inside the run
container that address is forwarded to the platform's internal storage, so DuckDB can use the
vended configuration unchanged: the Host header and the SigV4 signature stay the same.
"""

import asyncio
import threading


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def serve(listen_port, target_host, target_port, ready):
    async def handle(client_reader, client_writer):
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(target_host, target_port)
        except OSError:
            client_writer.close()
            return
        await asyncio.gather(pipe(client_reader, upstream_writer), pipe(upstream_reader, client_writer))

    server = await asyncio.start_server(handle, "127.0.0.1", listen_port)
    ready.set()
    async with server:
        await server.serve_forever()


def start(listen_port, target):
    host, _, port = target.rpartition(":")
    ready = threading.Event()
    thread = threading.Thread(
        target=lambda: asyncio.run(serve(int(listen_port), host, int(port), ready)), daemon=True)
    thread.start()
    if not ready.wait(10):
        raise RuntimeError("The storage forwarder did not start.")
    return thread
