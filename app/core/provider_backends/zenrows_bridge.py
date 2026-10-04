"""Hold one ZenRows launch socket; give each CDP client its own browser session."""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed, WebSocketException


async def main(config):
    sequence = 0
    pending = {}
    owners = {}
    finished = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, finished.set)
    async with connect(config["endpoint"], open_timeout=30, close_timeout=5, max_size=None) as upstream:
        async def command(message, client=None):
            nonlocal sequence
            sequence += 1
            number = sequence
            future = loop.create_future() if client is None else None
            pending[number] = (client, message.get("id"), future, message["method"])
            await upstream.send(json.dumps({**message, "id": number}))
            return await asyncio.wait_for(future, 30) if future is not None else None

        async def receive():
            try:
                async for raw in upstream:
                    message = json.loads(raw)
                    if "id" in message:
                        entry = pending.pop(message["id"], None)
                        if entry is None:
                            continue
                        client, original, future, method = entry
                        if future is not None:
                            if not future.done():
                                future.set_result(message)
                            continue
                        message["id"] = original
                        session = message.get("result", {}).get("sessionId")
                        if session:
                            owners[session] = client
                    else:
                        client = owners.get(message.get("sessionId"))
                        if client is None:
                            continue
                        if message.get("method") == "Target.attachedToTarget":
                            owners[message["params"]["sessionId"]] = client
                    if message.get("sessionId") == client.root_session:
                        message.pop("sessionId")
                    try:
                        await client.send(json.dumps(message))
                    except ConnectionClosed:
                        # Client departure does not terminate the held cloud browser.
                        continue
                    if "id" in message and method == "Browser.close" and "result" in message:
                        finished.set()
            finally:
                for _, _, future, _ in pending.values():
                    if future is not None and not future.done():
                        future.set_exception(RuntimeError("ZenRows browser disconnected"))
                finished.set()

        async def client_connection(client):
            if client.request.path != "/" + config["token"]:
                await client.close(code=1008, reason="Invalid session token")
                return
            result = await command({"method": "Target.attachToBrowserTarget", "params": {"flatten": True}})
            if "error" in result:
                await client.close(code=1011, reason="Browser attachment unavailable")
                return
            client.root_session = result["result"]["sessionId"]
            owners[client.root_session] = client
            try:
                async for raw in client:
                    message = json.loads(raw)
                    session = message.get("sessionId")
                    if session and owners.get(session) is not client:
                        await client.send(json.dumps({"id": message.get("id"), "error": {"code": -32001, "message": "Session unavailable"}}))
                        continue
                    message["sessionId"] = session or client.root_session
                    await command(message, client)
            finally:
                for session in [key for key, owner in owners.items() if owner is client]:
                    owners.pop(session, None)
                if not finished.is_set():
                    await command({"method": "Target.detachFromTarget", "params": {"sessionId": client.root_session}})

        reader = asyncio.create_task(receive())
        try:
            async with serve(client_connection, "127.0.0.1", 0, max_size=None, close_timeout=5) as server:
                port = server.sockets[0].getsockname()[1]
                ready = Path(config["ready"])
                temporary = ready.with_suffix(".tmp")
                temporary.write_text(json.dumps({"cdp": f"ws://127.0.0.1:{port}/{config['token']}", "bridge_pid": os.getpid()}))
                temporary.chmod(0o600)
                temporary.replace(ready)
                await finished.wait()
        finally:
            if not upstream.close_code:
                try:
                    await upstream.send(json.dumps({"id": sequence + 1, "method": "Browser.close"}))
                except ConnectionClosed:
                    sys.stderr.write("ZenRows browser already disconnected at shutdown\n")
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


if __name__ == "__main__":
    try:
        asyncio.run(main(json.loads(sys.stdin.read())))
    except (WebSocketException, OSError, ValueError, RuntimeError, TimeoutError, KeyError):
        # Endpoint-bearing transport exceptions must never reach logs.
        sys.stderr.write("ZenRows browser connection keeper failed\n")
        sys.exit(1)
