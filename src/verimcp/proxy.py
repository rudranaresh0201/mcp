"""The core proxy loop.

Role split (topic 1, host/client/server): this process is a SERVER to the
real Host (talks over our own stdin/stdout) and simultaneously a CLIENT to
the real backend MCP server (talks over a subprocess's stdin/stdout).

    Host  <--stdio-->  [ verimcp: server-side | client-side ]  <--stdio-->  backend

Every message from the Host is forwarded to the backend unchanged. Every
response from the backend is checked: if it was a tools/call this proxy has
a verifier for, verify() decides what actually gets forwarded back to the
Host.
"""
import asyncio
import sys

from verimcp import jsonrpc
from verimcp.verifiers.registry import verifiers_for


class Proxy:
    def __init__(self, backend_cmd: list[str]):
        self.backend_cmd = backend_cmd
        # Pending requests we've forwarded to the backend, keyed by JSON-RPC id,
        # so that when the matching response comes back we still have the
        # original request (tool name + arguments) to verify it against.
        self._pending: dict[str, dict] = {}

    async def run(self) -> None:
        backend = await asyncio.create_subprocess_exec(
            *self.backend_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )

        await asyncio.gather(
            self._forward_host_to_backend(backend),
            self._forward_backend_to_host(backend),
        )

    async def _forward_host_to_backend(self, backend) -> None:
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while True:
            message = await jsonrpc.read_message(reader)
            if message is None:
                backend.stdin.close()
                return

            if message.get("method") == "tools/call" and "id" in message:
                self._pending[message["id"]] = message

            jsonrpc.write_message(backend.stdin, message)
            await backend.stdin.drain()

    async def _forward_backend_to_host(self, backend) -> None:
        while True:
            message = await jsonrpc.read_message(backend.stdout)
            if message is None:
                return

            request = self._pending.pop(message.get("id"), None)
            if request is not None:
                tool_name = request.get("params", {}).get("name", "")
                for verifier in verifiers_for(tool_name):
                    message = verifier.verify(request, message)

            jsonrpc.write_message_sync(message)
