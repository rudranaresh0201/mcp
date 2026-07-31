"""The core proxy loop.

Role split (topic 1, host/client/server): this process is a SERVER to the
real Host (talks over our own stdin/stdout) and simultaneously a CLIENT to
the real backend MCP server (talks over a subprocess's stdin/stdout).

    Host  <--stdio-->  [ verimcp: server-side | client-side ]  <--stdio-->  backend

Every message from the Host is forwarded to the backend unchanged. Every
response from the backend is checked: if it was a tools/call or a
resources/read this proxy has a verifier for, verify() decides what actually
gets forwarded back to the Host.
"""
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from verimcp import jsonrpc
from verimcp.verifiers import registry

FILE_URI_PREFIX = "file://"


def _extract_root(roots_list_response: dict) -> Path | None:
    """Same file:// parsing devmcp.roots.RootsClient does on the other end --
    duplicated rather than imported because verimcp has no dependency on
    devmcp (it has to work in front of any backend, not just this one)."""
    for root in roots_list_response.get("result", {}).get("roots", []):
        uri = root.get("uri", "")
        if uri.startswith(FILE_URI_PREFIX):
            # url2pathname handles platform quirks (e.g. Windows' extra
            # leading slash before the drive letter in file:///C:/foo).
            return Path(url2pathname(urlparse(uri).path)).resolve()
    return None


class _StdinReader:
    """Reads our own real stdin without asyncio's pipe transports. Windows'
    proactor event loop can't reliably register a subprocess-redirected
    stdin via connect_read_pipe (see devmcp.cli._StdinReader for the same
    fix applied there) -- the blocking read runs in a worker thread instead
    so it doesn't block the loop."""

    async def readline(self) -> bytes:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, sys.stdin.buffer.readline)


class Proxy:
    def __init__(
        self,
        backend_cmd: list[str],
        verifier_names: list[str] | None = None,
        root_override: Path | None = None,
    ):
        self.backend_cmd = backend_cmd
        # None loads every verifier installed under the verimcp.verifiers
        # entry-point group (existing behavior, unchanged for old callers).
        # An explicit list restricts to just those -- needed once more than
        # one backend is in play, since two backends can expose a same-named
        # tool with differently-shaped responses.
        self._verifiers = registry.load_verifiers(verifier_names)
        # Pending requests we've forwarded to the backend, keyed by JSON-RPC id,
        # so that when the matching response comes back we still have the
        # original request (tool name + arguments, or resource uri) to verify
        # it against.
        self._pending: dict[str, dict] = {}
        # ids of roots/list requests the *backend* sent us, so that when the
        # matching response comes back from the Host (opposite direction to
        # the tools/call tracking above) we recognize it as "the root" and
        # not just some unrelated reply.
        self._pending_roots: set = set()
        # devmcp learns its root by asking the Host via roots/list, so
        # watching that exchange is enough for it. Not every backend
        # participates in that dance at all -- e.g. the reference
        # mcp-server-git never sends roots/list, it takes its repo path from
        # its own --repository flag instead. root_override is the fallback
        # for exactly that case: a verifier still needs to know the backend's
        # real working directory even when the backend never tells the Host.
        self._root: Path | None = root_override

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
        reader = _StdinReader()

        while True:
            message = await jsonrpc.read_message(reader)
            if message is None:
                backend.stdin.close()
                return

            if message.get("method") in ("tools/call", "resources/read") and "id" in message:
                self._pending[message["id"]] = message

            if message.get("id") in self._pending_roots and ("result" in message or "error" in message):
                self._pending_roots.discard(message["id"])
                if "result" in message:
                    self._root = _extract_root(message)

            jsonrpc.write_message(backend.stdin, message)
            await backend.stdin.drain()

    async def _forward_backend_to_host(self, backend) -> None:
        while True:
            message = await jsonrpc.read_message(backend.stdout)
            if message is None:
                return

            if message.get("method") == "roots/list" and "id" in message:
                self._pending_roots.add(message["id"])

            request = self._pending.pop(message.get("id"), None)
            if request is not None:
                params = request.get("params", {})
                identifier = params.get("uri", "") if request.get("method") == "resources/read" else params.get("name", "")
                for verifier in registry.verifiers_for(identifier, self._verifiers):
                    message = verifier.verify(request, message, root=self._root)

            jsonrpc.write_message_sync(message)
