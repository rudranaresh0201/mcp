"""Bidirectional newline-delimited JSON-RPC over a pair of asyncio streams.

Unlike verimcp's jsonrpc.py (which only ever relays messages someone else
sent), devmcp is a real endpoint: it must answer requests it receives
(tools/call, resources/read, ...) AND originate its own requests to the far
end (roots/list, sampling/createMessage) and wait for the matching reply.
`request()` is what makes the second direction possible: it allocates a
fresh id, parks a Future under it, and resolve_pending() is what wakes that
Future up when a response with that id arrives on the read loop.
"""
import asyncio
import itertools
import json
from typing import Any


class Connection:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._next_id = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}

    async def recv(self) -> dict[str, Any] | None:
        """Read and parse one message. Returns None on EOF."""
        line = await self._reader.readline()
        if not line:
            return None
        return json.loads(line)

    def resolve_pending(self, message: dict[str, Any]) -> bool:
        """If `message` is a reply to a request() call we made, resolve it and
        return True. Otherwise return False so the caller treats it as an
        incoming request/notification instead."""
        id_ = message.get("id")
        future = self._pending.get(id_)
        if future is None or ("result" not in message and "error" not in message):
            return False
        if not future.done():
            if "error" in message:
                future.set_exception(RuntimeError(message["error"]))
            else:
                future.set_result(message["result"])
        return True

    async def request(self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
        """Send a request we originate and wait for the matching response."""
        id_ = next(self._next_id)
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[id_] = future
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": id_, "method": method}
        if params is not None:
            message["params"] = params
        await self._write(message)
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(id_, None)

    async def send_response(self, id_: Any, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": id_}
        message["error"] = error if error is not None else None
        if error is None:
            message.pop("error")
            message["result"] = result if result is not None else {}
        await self._write(message)

    async def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        await self._write(message)

    async def _write(self, message: dict[str, Any]) -> None:
        self._writer.write((json.dumps(message) + "\n").encode())
        await self._writer.drain()
