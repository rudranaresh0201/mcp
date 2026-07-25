"""The always-listening loop. Every incoming message passes through here:
first checked against resolve_pending() (is this a reply to something devmcp
itself asked, e.g. a future roots/list?), and only handled as a new incoming
request/notification if it isn't."""
import asyncio
from pathlib import Path
from typing import Any

from devmcp.capabilities import PROTOCOL_VERSION, SERVER_CAPABILITIES, SERVER_INFO
from devmcp.connection import Connection
from devmcp.context import ServerContext
from devmcp.prompts import ALL_PROMPTS, PROMPTS_BY_NAME
from devmcp.resources import LISTED_RESOURCES
from devmcp.resources import resolve as resolve_resource
from devmcp.roots import RootsClient
from devmcp.tools import ALL_TOOLS, TOOLS_BY_NAME


class Server:
    def __init__(self, connection: Connection, repo_root: Path) -> None:
        self.connection = connection
        self.ctx = ServerContext(repo_root=repo_root, connection=connection)
        self.roots_client = RootsClient()
        self._roots_task: asyncio.Task | None = None

    async def run(self) -> None:
        while True:
            message = await self.connection.recv()
            if message is None:
                return
            if self.connection.resolve_pending(message):
                continue
            if "method" not in message:
                continue
            if "id" in message:
                await self._handle_request(message)
            else:
                await self._handle_notification(message)

    async def _handle_request(self, message: dict[str, Any]) -> None:
        method = message["method"]
        params = message.get("params") or {}
        id_ = message["id"]

        if method == "initialize":
            await self.connection.send_response(
                id_,
                result={
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": SERVER_CAPABILITIES,
                    "serverInfo": SERVER_INFO,
                },
            )
        elif method == "tools/list":
            await self.connection.send_response(
                id_,
                result={
                    "tools": [
                        {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
                        for t in ALL_TOOLS
                    ]
                },
            )
        elif method == "tools/call":
            await self._handle_tool_call(id_, params)
        elif method == "resources/list":
            await self.connection.send_response(
                id_,
                result={
                    "resources": [
                        {"uri": r.uri, "name": r.name, "description": r.description, "mimeType": r.mime_type}
                        for r in LISTED_RESOURCES
                    ]
                },
            )
        elif method == "resources/read":
            await self._handle_resource_read(id_, params)
        elif method == "resources/subscribe":
            self.ctx.subscriptions.subscribe(params.get("uri", ""))
            await self.connection.send_response(id_, result={})
        elif method == "resources/unsubscribe":
            self.ctx.subscriptions.unsubscribe(params.get("uri", ""))
            await self.connection.send_response(id_, result={})
        elif method == "prompts/list":
            await self.connection.send_response(
                id_,
                result={
                    "prompts": [
                        {"name": p.name, "description": p.description, "arguments": p.arguments}
                        for p in ALL_PROMPTS
                    ]
                },
            )
        elif method == "prompts/get":
            await self._handle_prompt_get(id_, params)
        else:
            await self.connection.send_response(
                id_, error={"code": -32601, "message": f"method not found: {method}"}
            )

    async def _handle_tool_call(self, id_: Any, params: dict[str, Any]) -> None:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        tool = TOOLS_BY_NAME.get(name)
        if tool is None:
            await self.connection.send_response(id_, error={"code": -32602, "message": f"unknown tool: {name}"})
            return
        try:
            result = await tool.call(arguments, self.ctx)
        except Exception as exc:  # noqa: BLE001 -- a broken tool must not crash the whole server loop
            result = {"isError": True, "content": [{"type": "text", "text": f"tool {name} raised: {exc}"}]}
        await self.connection.send_response(id_, result=result)

    async def _handle_resource_read(self, id_: Any, params: dict[str, Any]) -> None:
        uri = params.get("uri", "")
        resource = resolve_resource(uri)
        if resource is None:
            await self.connection.send_response(id_, error={"code": -32602, "message": f"unknown resource: {uri}"})
            return
        result = await resource.read(uri, self.ctx)
        await self.connection.send_response(id_, result=result)

    async def _handle_prompt_get(self, id_: Any, params: dict[str, Any]) -> None:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        prompt = PROMPTS_BY_NAME.get(name)
        if prompt is None:
            await self.connection.send_response(id_, error={"code": -32602, "message": f"unknown prompt: {name}"})
            return
        result = await prompt.get(arguments, self.ctx)
        await self.connection.send_response(id_, result=result)

    async def _handle_notification(self, message: dict[str, Any]) -> None:
        if message.get("method") == "notifications/initialized":
            # Fire-and-forget: the outbound roots/list this triggers must NOT be
            # awaited inline here, or the single dispatch loop would be blocked
            # waiting for its own reply and could never read that reply in.
            self._roots_task = asyncio.create_task(self._negotiate_roots())

    async def _negotiate_roots(self) -> None:
        new_root = await self.roots_client.negotiate(self.connection)
        if new_root is not None:
            self.ctx.repo_root = new_root
