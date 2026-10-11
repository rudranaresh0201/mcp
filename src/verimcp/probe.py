"""`verimcp probe -- <server command>`: which MCP era does this server speak?

The 2026-07-28 revision dropped the `initialize` handshake. A server is now
either *modern* (every request carries its own protocol version in
params._meta; the server remembers nothing between requests) or *legacy*
(a conversation opens with `initialize`, and the server remembers what it
learned). A modern Host talking to a legacy server simply fails -- the
spec's compatibility matrix says so in as many words.

This is the spec's own stdio detection procedure, run against any server:

    1. send `server/discover` with a modern version in _meta
         DiscoverResult                    -> modern
         UnsupportedProtocolVersionError   -> modern, but not our version
         any other error, or silence       -> legacy
    2. legacy: fall back to `initialize`, the way a dual-era client would
    3. list the tools, in whichever era the server turned out to speak

`--trace` prints every message on the wire, both directions -- the point of
the command as much as its verdict.
"""
from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from verimcp import stateless
from verimcp.era_bridge import (
    CLIENT_INFO_KEY,
    LEGACY_VERSION,
    MODERN_VERSIONS,
    SERVER_INFO_KEY,
    era_of_discover_reply,
)

MODERN_VERSION = MODERN_VERSIONS[0]
CLIENT_INFO = {"name": "verimcp-probe", "version": "0.1.0"}


class ServerExited(Exception):
    """The server process ended before the probe finished."""


@dataclass
class ProbeResult:
    era: str  # "modern" | "legacy" | "unreachable"
    discover: str  # what server/discover got back, in words
    protocol_version: str | None = None
    supported_versions: list[str] = field(default_factory=list)
    server_info: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    result_type_on_tools_list: str | None = None


def modern_meta(version: str = MODERN_VERSION) -> dict[str, Any]:
    return {
        "_meta": {
            stateless.VERSION_KEY: version,
            CLIENT_INFO_KEY: CLIENT_INFO,
            stateless.CAPABILITIES_KEY: {},
        }
    }


class _Server:
    """A server subprocess spoken to one JSON-RPC line at a time, with a
    timeout on every reply -- a legacy server may never answer a request it
    does not understand, and the spec counts that silence as an answer."""

    def __init__(self, command: list[str], trace) -> None:
        resolved = shutil.which(command[0]) or command[0]
        self._proc = subprocess.Popen(
            [resolved, *command[1:]],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._trace = trace
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self._proc.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def send(self, message: dict[str, Any]) -> None:
        self._trace("->", message)
        self._proc.stdin.write(json.dumps(message) + "\n")
        self._proc.stdin.flush()

    def request(self, id_: str, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any] | None:
        """The response to this request, or None if none came in time.
        Anything else the server writes meanwhile is traced and skipped."""
        self.send({"jsonrpc": "2.0", "id": id_, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            try:
                line = self._lines.get(timeout=left)
            except queue.Empty:
                break
            if line is None:
                raise ServerExited(f"exit code {self._proc.wait()}")
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self._trace("<-", {"not JSON": line.rstrip()})
                continue
            self._trace("<-", message)
            if message.get("id") == id_ and ("result" in message or "error" in message):
                return message
        return None

    def close(self) -> None:
        try:
            self._proc.stdin.close()
            self._proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self._proc.kill()


def _describe(reply: dict[str, Any] | None) -> str:
    if reply is None:
        return "no answer"
    if "error" in reply:
        error = reply["error"]
        return f"error {error.get('code')} {error.get('message', '')!r}"
    return "DiscoverResult"


def probe(command: list[str], timeout: float = 5.0, trace=lambda direction, message: None) -> ProbeResult:
    server = _Server(command, trace)
    try:
        return _probe(server, timeout)
    except ServerExited as e:
        return ProbeResult(era="unreachable", discover=f"the server process exited ({e})")
    finally:
        server.close()


def _probe(server: _Server, timeout: float) -> ProbeResult:
    reply = server.request("probe-discover", "server/discover", modern_meta(), timeout)
    discover = _describe(reply)
    modern = era_of_discover_reply(reply) == "modern"  # the same rule verimcp's bridge uses

    if modern and "result" in reply:
        result = reply["result"]
        listed = server.request("probe-tools", "tools/list", modern_meta(), timeout)
        tools_result = (listed or {}).get("result", {})
        return ProbeResult(
            era="modern",
            discover=discover,
            protocol_version=MODERN_VERSION,
            supported_versions=list(result.get("supportedVersions", [])),
            server_info=result.get("_meta", {}).get(SERVER_INFO_KEY, {}),
            capabilities=result.get("capabilities", {}),
            tools=[t.get("name", "?") for t in tools_result.get("tools", [])],
            result_type_on_tools_list=tools_result.get("resultType"),
        )

    if modern:  # UnsupportedProtocolVersionError
        # Modern, just not our version: the spec forbids falling back to
        # initialize here.
        return ProbeResult(
            era="modern",
            discover=discover,
            supported_versions=list(reply["error"].get("data", {}).get("supported", [])),
        )

    # Any other error, or silence: legacy. Fall back to the handshake.
    init = server.request(
        "probe-initialize",
        "initialize",
        {"protocolVersion": LEGACY_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
        timeout,
    )
    if init is None or "result" not in init:
        return ProbeResult(era="unreachable", discover=discover)
    result = init["result"]
    server.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    listed = server.request("probe-tools", "tools/list", {}, timeout)
    return ProbeResult(
        era="legacy",
        discover=discover,
        protocol_version=result.get("protocolVersion"),
        server_info=result.get("serverInfo", {}),
        capabilities=result.get("capabilities", {}),
        tools=[t.get("name", "?") for t in (listed or {}).get("result", {}).get("tools", [])],
    )


def render(result: ProbeResult, command: list[str]) -> str:
    name = result.server_info.get("name", "?")
    version = result.server_info.get("version", "")
    lines = [
        f"server:         {' '.join(command)}",
        f"server/discover {result.discover}",
    ]
    if result.era == "unreachable":
        lines.append("verdict:        no usable answer to server/discover or initialize -- is this an MCP server? "
                     "(run the command by itself to see its errors)")
        return "\n".join(lines)
    lines += [
        f"era:            {result.era.upper()}"
        + (" (initialize handshake)" if result.era == "legacy" else " (stateless, per-request _meta)"),
        f"identity:       {name} {version}".rstrip(),
        f"protocol:       {result.protocol_version or 'none we speak'}"
        + (f"   supports {result.supported_versions}" if result.supported_versions else ""),
        f"capabilities:   {', '.join(sorted(result.capabilities)) or '(none)'}",
        f"tools ({len(result.tools)}):      {', '.join(result.tools) or '(none)'}",
    ]
    if result.era == "modern" and result.protocol_version:
        lines.append(f"resultType:     {result.result_type_on_tools_list or 'MISSING (required by 2026-07-28)'} on tools/list")
    if result.era == "legacy":
        lines.append(
            f"verdict:        a {MODERN_VERSION} stateless Host cannot use this server directly "
            "(spec: Modern client + Legacy server = fails)."
        )
    elif result.protocol_version:
        lines.append(f"verdict:        a {MODERN_VERSION} stateless Host can use this server.")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="verimcp probe",
        description="Which MCP era does a server speak? Runs the 2026-07-28 spec's stdio detection: "
        "server/discover first, initialize as the fallback.",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="seconds to wait for each reply (default 5)")
    parser.add_argument("--trace", action="store_true", help="print every JSON-RPC message, both directions")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    if "--" not in argv:
        parser.error("put the server command after --, e.g.  verimcp probe -- devmcp --repo-path .")
    split = argv.index("--")
    args = parser.parse_args(argv[:split])
    command = argv[split + 1 :]
    if not command:
        parser.error("no server command after --")

    def trace(direction: str, message: dict) -> None:
        if args.trace:
            print(f"  {direction} {json.dumps(message)}")

    try:
        result = probe(command, timeout=args.timeout, trace=trace)
    except FileNotFoundError:
        parser.error(f"could not start {command[0]!r} -- is it installed and on PATH?")
    if args.json:
        print(json.dumps(result.__dict__, indent=2))
    else:
        if args.trace:
            print()
        print(render(result, command))
    return 0 if result.era != "unreachable" else 1
