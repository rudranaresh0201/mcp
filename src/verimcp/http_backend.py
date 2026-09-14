"""A remote MCP backend over Streamable HTTP (MCP spec 2025-06-18).

Proxy was written against a subprocess: it writes lines to `backend.stdin`
and reads lines from `backend.stdout`. HttpBackend exposes exactly that
shape, so gates, verifiers, audit, approvals and verify-before-retry work
against a hosted server with no change to Proxy's message handling at all.

    Host <--stdio--> [ verimcp ] <--stdio--> local backend     (subprocess)
    Host <--stdio--> [ verimcp ] <--HTTP---> remote backend    (this module)

The transport, as the spec defines it:
  - every client->server message is its own POST to one endpoint URL;
  - a request is answered with either `application/json` (one message) or
    `text/event-stream` (SSE: zero or more server requests/notifications,
    then the response); a notification or response gets 202 and no body;
  - the server may issue `Mcp-Session-Id` on the initialize response, which
    every later request must echo, alongside `MCP-Protocol-Version`;
  - the client may open a GET SSE stream for server-initiated messages;
  - DELETE with the session id ends the session.

Ordering vs. concurrency. Messages must reach the server in the order the
Host sent them (`notifications/initialized` before `tools/list`), but a slow
tool call must not hold up the next message -- Proxy's approval flow depends
on that, exactly as it does on the stdio pipe. So one sender thread writes
each request onto the wire in order, and every reply is read on its own
thread. http.client is used (not urllib) because it separates those two
steps: `request()` sends, `getresponse()` waits.

Stdlib only, matching the rest of the core install.
"""
import asyncio
import http.client
import json
import os
import queue
import re
import sys
import threading
from typing import Any
from urllib.parse import urlsplit

_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_EOF = b""
_STOP = object()


def expand_env(value: str) -> str:
    """`Bearer ${GITHUB_PERSONAL_ACCESS_TOKEN}` -> the real token, read from
    the environment at startup, so a secret never has to be written into an
    MCP config file. A missing variable is a loud startup error, not an empty
    header that fails later as a confusing 401."""

    def replace(match: re.Match) -> str:
        name = match.group(1)
        if name not in os.environ:
            raise ValueError(f"header references ${{{name}}}, but that environment variable is not set")
        return os.environ[name]

    return _ENV_REF.sub(replace, value)


def parse_headers(raw: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in raw:
        name, sep, value = item.partition(":")
        if not sep or not name.strip():
            raise ValueError(f"--header must look like 'Name: value', got {item!r}")
        headers[name.strip()] = expand_env(value.strip())
    return headers


def headers_from_env(raw: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in raw:
        name, sep, env_var = item.partition("=")
        if not sep or not name.strip() or not env_var.strip():
            raise ValueError(f"--header-from-env must look like 'Name=ENV_VAR', got {item!r}")
        if env_var.strip() not in os.environ:
            raise ValueError(f"--header-from-env {name.strip()} reads {env_var.strip()}, but it is not set")
        headers[name.strip()] = os.environ[env_var.strip()]
    return headers


def _iter_sse_data(reply: http.client.HTTPResponse):
    """Yield the `data` payload of each SSE event as it arrives -- never
    buffering the whole stream first, since a server may hold it open."""
    data_lines: list[str] = []
    while True:
        raw_line = reply.readline()
        if not raw_line:
            break
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if line == "":
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip(" "))
        # "event:", "id:", "retry:" and ":comment" lines carry nothing Proxy needs
    if data_lines:
        yield "\n".join(data_lines)


def _content_type(reply: http.client.HTTPResponse) -> str:
    return (reply.getheader("Content-Type") or "").split(";")[0].strip().lower()


class _Outbound:
    """Stands in for `subprocess.stdin`: Proxy calls write() then drain()."""

    def __init__(self, backend: "HttpBackend"):
        self._backend = backend

    def write(self, data: bytes) -> None:
        for line in data.decode("utf-8").splitlines():
            if line.strip():
                self._backend.outbox.put(json.loads(line))

    async def drain(self) -> None:
        return None  # the sender thread owns the wire; nothing to wait for here

    def close(self) -> None:
        self._backend.outbox.put(_STOP)


class _Inbound:
    """Stands in for `subprocess.stdout`: Proxy awaits readline()."""

    def __init__(self, inbox: asyncio.Queue):
        self._inbox = inbox

    async def readline(self) -> bytes:
        return await self._inbox.get()


class HttpBackend:
    def __init__(self, url: str, headers: dict[str, str] | None = None, timeout: float = 300.0):
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"--backend-url must be an http(s) URL, got {url!r}")
        self.url = url
        self._scheme = parts.scheme
        self._host = parts.hostname
        self._port = parts.port
        self._path = parts.path or "/"
        if parts.query:
            self._path += f"?{parts.query}"
        self._headers = dict(headers or {})
        self._timeout = timeout
        self._session_id: str | None = None
        self._protocol_version: str | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._inbox: asyncio.Queue = asyncio.Queue()
        self.outbox: queue.Queue = queue.Queue()
        self.stdin = _Outbound(self)
        self.stdout = _Inbound(self._inbox)

    async def start(self) -> "HttpBackend":
        self._loop = asyncio.get_running_loop()
        threading.Thread(target=self._sender, name="verimcp-http-sender", daemon=True).start()
        return self

    # -- delivery into Proxy ------------------------------------------------

    def _deliver(self, message: Any) -> None:
        line = (json.dumps(message) + "\n").encode("utf-8")
        self._loop.call_soon_threadsafe(self._inbox.put_nowait, line)

    def _deliver_error(self, message: dict[str, Any], text: str) -> None:
        if "method" in message and "id" in message:
            # A request the Host is waiting on: answer it, or the Host hangs.
            self._deliver({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32000, "message": text}})
        else:
            print(f"verimcp: {text}", file=sys.stderr)

    def _handle_payload(self, payload: str) -> None:
        if not payload.strip():
            return  # an SSE event with empty data: a priming/keep-alive event, not a message
        try:
            parsed = json.loads(payload)
        except ValueError:
            print(f"verimcp: remote MCP server sent non-JSON data: {payload[:200]!r}", file=sys.stderr)
            return
        for message in parsed if isinstance(parsed, list) else [parsed]:
            if isinstance(message, dict) and isinstance(message.get("result"), dict):
                version = message["result"].get("protocolVersion")
                if version and self._protocol_version is None:
                    self._protocol_version = version
            self._deliver(message)

    # -- the wire -----------------------------------------------------------

    def _connection(self, timeout: float | None) -> http.client.HTTPConnection:
        if self._scheme == "https":
            return http.client.HTTPSConnection(self._host, self._port, timeout=timeout)
        return http.client.HTTPConnection(self._host, self._port, timeout=timeout)

    def _headers_for(self, accept: str) -> dict[str, str]:
        headers = {"Accept": accept, "Content-Type": "application/json", "User-Agent": "verimcp", **self._headers}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        if self._protocol_version:
            headers["MCP-Protocol-Version"] = self._protocol_version
        return headers

    def _sender(self) -> None:
        """Puts each message on the wire in the order the Host sent it."""
        while True:
            message = self.outbox.get()
            if message is _STOP:
                self._shutdown()
                return
            is_initialize = message.get("method") == "initialize"
            connection = self._connection(self._timeout)
            try:
                connection.request(
                    "POST", self._path, body=json.dumps(message).encode("utf-8"),
                    headers=self._headers_for("application/json, text/event-stream"),
                )
            except OSError as err:
                connection.close()
                self._deliver_error(message, f"could not reach remote MCP server at {self.url}: {err}")
                continue

            if is_initialize:
                # Read inline: every later message has to carry the session
                # id and protocol version this reply establishes.
                self._read_reply(connection, message, is_initialize=True)
                threading.Thread(target=self._listen, name="verimcp-http-listen", daemon=True).start()
            else:
                threading.Thread(
                    target=self._read_reply, args=(connection, message, False), name="verimcp-http-reply", daemon=True
                ).start()

    def _read_reply(self, connection: http.client.HTTPConnection, message: dict[str, Any], is_initialize: bool) -> None:
        try:
            reply = connection.getresponse()
            if is_initialize:
                self._session_id = reply.getheader("Mcp-Session-Id") or self._session_id
            if reply.status >= 400:
                detail = reply.read().decode("utf-8", "replace")[:300]
                self._deliver_error(message, f"remote MCP server answered HTTP {reply.status}: {detail or reply.reason}")
                return
            kind = _content_type(reply)
            if kind == "text/event-stream":
                for data in _iter_sse_data(reply):
                    self._handle_payload(data)
            elif kind == "application/json":
                self._handle_payload(reply.read().decode("utf-8"))
            else:
                reply.read()  # 202 Accepted: a notification or a response we sent
        except (OSError, http.client.HTTPException) as err:
            self._deliver_error(message, f"lost connection to remote MCP server at {self.url}: {err}")
        finally:
            connection.close()

    def _listen(self) -> None:
        """The optional GET stream for messages the server sends unprompted
        (e.g. a sampling or roots/list request outside any POST). A server
        that doesn't offer one answers 405, which is fine: stop quietly."""
        connection = self._connection(timeout=None)
        try:
            connection.request("GET", self._path, headers=self._headers_for("text/event-stream"))
            reply = connection.getresponse()
            if reply.status != 200 or _content_type(reply) != "text/event-stream":
                return
            for data in _iter_sse_data(reply):
                self._handle_payload(data)
        except (OSError, http.client.HTTPException):
            return
        finally:
            connection.close()

    def _shutdown(self) -> None:
        if self._session_id:
            connection = self._connection(timeout=5)
            try:
                connection.request("DELETE", self._path, headers=self._headers_for("application/json"))
                connection.getresponse().read()
            except (OSError, http.client.HTTPException):
                pass  # best effort: the session expires server-side anyway
            finally:
                connection.close()
        self._loop.call_soon_threadsafe(self._inbox.put_nowait, _EOF)
