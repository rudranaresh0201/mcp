"""Serving a stateless (2026-07-28) Host from a legacy MCP server.

The spec's compatibility matrix is blunt: Modern client + Legacy server =
fails. Every server written before the revision -- devmcp, the reference
servers, almost everything installed today -- expects an `initialize`
handshake that a stateless Host never sends.

verimcp already sits between the two, so it can be what each side needs: a
modern server to the Host, and an ordinary legacy client to the backend.

    Host (modern)                     verimcp                      backend (legacy)
    server/discover  ------------->   answered here, from the
                                      backend's initialize result
    tools/call (_meta: 2026-07-28) ->  once: server/discover?  -------> error / silence
                                            initialize        -------> result
                                       forward the call       -------> result
                     <-------------   + resultType: "complete"
                                      backend asks roots/list  <------
                                      answered HERE: a stateless
                                      Host cannot be asked anything

Whether the backend is modern is decided once, the first time a modern
request arrives, with the spec's own stdio probe: a DiscoverResult or an
UnsupportedProtocolVersionError means modern (pass everything through);
any other error or silence means legacy (bridge). A Host that opens with
`initialize` never touches any of this.
"""
from __future__ import annotations

import asyncio
import copy
import itertools
from pathlib import Path
from typing import Any

from verimcp import jsonrpc, stateless

MODERN_VERSIONS = ("2026-07-28",)
LEGACY_VERSION = "2025-11-25"
UNSUPPORTED_VERSION = -32022
SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"
CLIENT_INFO_KEY = "io.modelcontextprotocol/clientInfo"
CLIENT_INFO = {"name": "verimcp", "version": "0.5.0"}
_ID_PREFIX = "verimcp-bridge-"

# Capabilities that mean the same thing in both eras. listChanged/subscribe
# promise notifications a modern server only sends inside
# subscriptions/listen, which this bridge does not translate -- so they are
# dropped rather than promised.
_PORTABLE_CAPABILITIES = ("tools", "resources", "prompts", "completions")
_UNSOLICITED_NOTIFICATIONS = frozenset(
    {
        "notifications/tools/list_changed",
        "notifications/resources/list_changed",
        "notifications/prompts/list_changed",
        "notifications/resources/updated",
    }
)


def requested_version(request: dict[str, Any]) -> str | None:
    return request.get("params", {}).get("_meta", {}).get(stateless.VERSION_KEY)


def unsupported_version_error(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request.get("id"),
        "error": {
            "code": UNSUPPORTED_VERSION,
            "message": "Unsupported protocol version",
            "data": {"supported": list(MODERN_VERSIONS), "requested": requested_version(request)},
        },
    }


def era_of_discover_reply(reply: dict[str, Any] | None) -> str:
    """The spec's stdio rule: a DiscoverResult or an
    UnsupportedProtocolVersionError comes only from a modern server; any
    other error, or no reply at all, from a legacy one. Deliberately not
    keyed to one error code -- legacy servers answer an unknown method with
    -32601, -32602, or nothing."""
    if reply is None:
        return "legacy"
    if "result" in reply:
        return "modern"
    if reply.get("error", {}).get("code") == UNSUPPORTED_VERSION:
        return "modern"
    return "legacy"


def discover_result(init_result: dict[str, Any], extra_capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
    """A DiscoverResult describing a legacy backend, built from what it said
    in its own initialize reply."""
    declared = init_result.get("capabilities", {})
    capabilities: dict[str, Any] = {}
    for name in _PORTABLE_CAPABILITIES:
        if name in declared:
            settings = dict(declared[name]) if isinstance(declared[name], dict) else {}
            settings.pop("listChanged", None)
            settings.pop("subscribe", None)
            capabilities[name] = settings
    for name, settings in (extra_capabilities or {}).items():
        capabilities.setdefault(name, settings)
    result: dict[str, Any] = {
        "resultType": "complete",
        "supportedVersions": list(MODERN_VERSIONS),
        "capabilities": capabilities,
        "_meta": {SERVER_INFO_KEY: init_result.get("serverInfo", {})},
    }
    if init_result.get("instructions"):
        result["instructions"] = init_result["instructions"]
    return result


def answer_backend_request(request: dict[str, Any], root: Path | None) -> dict[str, Any]:
    """verimcp's reply to a request the legacy backend sends while a
    stateless Host is on the other side. The Host cannot receive it (a
    modern server never writes requests), so verimcp answers: ping, roots
    from --root if it has one, and an error for everything else -- verimcp
    declared no client capabilities in its initialize, so a backend asking
    for sampling or elicitation is asking for something it was told is not
    there."""
    method = request.get("method")
    reply: dict[str, Any] = {"jsonrpc": "2.0", "id": request.get("id")}
    if method == "ping":
        reply["result"] = {}
    elif method == "roots/list" and root is not None:
        reply["result"] = {"roots": [{"uri": root.as_uri(), "name": root.name}]}
    else:
        reply["error"] = {
            "code": -32601,
            "message": f"{method} is unavailable: the Host is stateless ({MODERN_VERSIONS[0]}) and cannot be "
            "asked, and verimcp declared no such client capability",
        }
    return reply


def is_unsolicited_notification(message: dict[str, Any]) -> bool:
    return "id" not in message and message.get("method") in _UNSOLICITED_NOTIFICATIONS


def stamp_result_type(message: dict[str, Any]) -> dict[str, Any]:
    """2026-07-28 requires resultType on every result; a legacy backend has
    never heard of it. Never overwrites one already there (input_required)."""
    if isinstance(message.get("result"), dict):
        message["result"].setdefault("resultType", "complete")
    return message


class Bridge:
    """Decides the backend's era once, and if it is legacy, holds the one
    thing a legacy server needs that a stateless Host will never supply:
    the handshake. The only state here is about the BACKEND -- nothing about
    any Host request survives from one request to the next."""

    def __init__(self, timeout: float = 5.0) -> None:
        self.era: str | None = None  # None until the first modern request
        self.init_result: dict[str, Any] = {}
        self._timeout = timeout
        self._ids = itertools.count(1)
        self._waiting: dict[str, asyncio.Future] = {}
        self._lock = asyncio.Lock()

    @property
    def bridging(self) -> bool:
        return self.era == "legacy"

    def take_reply(self, message: dict[str, Any]) -> bool:
        """True if `message` answers one of the bridge's own requests (and
        hands it over); such a reply is never the Host's business."""
        id_ = message.get("id")
        if not (isinstance(id_, str) and id_.startswith(_ID_PREFIX)) or "method" in message:
            return False
        future = self._waiting.pop(id_, None)
        if future is not None and not future.done():
            future.set_result(message)
        return True

    async def ensure_ready(self, backend) -> None:
        async with self._lock:
            if self.era is not None:
                return
            reply = await self._request(
                backend,
                "server/discover",
                {
                    "_meta": {
                        stateless.VERSION_KEY: MODERN_VERSIONS[0],
                        CLIENT_INFO_KEY: CLIENT_INFO,
                        stateless.CAPABILITIES_KEY: {},
                    }
                },
            )
            if era_of_discover_reply(reply) == "modern":
                self.era = "modern"
                return
            init = await self._request(
                backend,
                "initialize",
                {"protocolVersion": LEGACY_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
            )
            if init is None or "result" not in init:
                raise RuntimeError(f"backend answered neither server/discover nor initialize: {init!r}")
            self.init_result = copy.deepcopy(init["result"])
            # Set before `initialized`: the backend may ask for roots the
            # moment it reads it, and that request must be answered here.
            self.era = "legacy"
            jsonrpc.write_message(backend.stdin, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            await backend.stdin.drain()

    async def _request(self, backend, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        id_ = f"{_ID_PREFIX}{next(self._ids)}"
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiting[id_] = future
        jsonrpc.write_message(backend.stdin, {"jsonrpc": "2.0", "id": id_, "method": method, "params": params})
        await backend.stdin.drain()
        try:
            return await asyncio.wait_for(future, timeout=self._timeout)
        except TimeoutError:
            self._waiting.pop(id_, None)
            return None
