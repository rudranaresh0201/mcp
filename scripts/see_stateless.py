"""The same tool call in both MCP eras, over a real stdio pipe.

Legacy (2025-11-25 and earlier): a handshake opens the conversation and the
server REMEMBERS what it learned from it. Modern (2026-07-28): there is no
handshake; every request carries its own protocol version and client
capabilities in params._meta, so the server remembers nothing between calls.

    python scripts/see_stateless.py

The script launches itself as the server (`--server`) and plays the Host.
"""
import json
import subprocess
import sys

MODERN = "2026-07-28"
LEGACY = "2025-06-18"
SERVER_INFO = {"name": "tiny-dual-era-server", "version": "0.1.0"}

VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"
CLIENT_INFO_KEY = "io.modelcontextprotocol/clientInfo"
SERVER_INFO_KEY = "io.modelcontextprotocol/serverInfo"


def add(arguments: dict, client_capabilities: dict) -> dict:
    # The capabilities are echoed back only so the trace shows WHERE the
    # server got them from in each era.
    total = arguments["a"] + arguments["b"]
    return {
        "content": [{"type": "text", "text": f"{total} (client capabilities I can see: {sorted(client_capabilities)})"}],
        "isError": False,
    }


def serve() -> None:
    # The only state in this file. A legacy conversation fills it during the
    # handshake; a modern request never reads or writes it.
    remembered: dict = {"initialized": False, "client_capabilities": {}}

    for line in sys.stdin:
        message = json.loads(line)
        if "id" not in message:  # a notification: nothing to reply to
            continue
        method = message.get("method")
        params = message.get("params", {})
        meta = params.get("_meta", {})
        reply: dict = {"jsonrpc": "2.0", "id": message["id"]}

        if VERSION_KEY in meta:
            # Modern: everything needed to answer is inside this one request.
            if meta[VERSION_KEY] != MODERN:
                reply["error"] = {
                    "code": -32022,
                    "message": "Unsupported protocol version",
                    "data": {"supported": [MODERN, LEGACY], "requested": meta[VERSION_KEY]},
                }
            elif method == "server/discover":
                reply["result"] = {
                    "resultType": "complete",
                    "supportedVersions": [MODERN, LEGACY],
                    "capabilities": {"tools": {}},
                    "_meta": {SERVER_INFO_KEY: SERVER_INFO},
                }
            elif method == "tools/call":
                result = add(params["arguments"], meta.get(CAPABILITIES_KEY, {}))
                reply["result"] = {"resultType": "complete", **result, "_meta": {SERVER_INFO_KEY: SERVER_INFO}}
        elif method == "initialize":
            # Legacy: learn about the client once, and keep it.
            remembered["initialized"] = True
            remembered["client_capabilities"] = params.get("capabilities", {})
            reply["result"] = {"protocolVersion": LEGACY, "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO}
        elif method == "tools/call" and remembered["initialized"]:
            reply["result"] = add(params["arguments"], remembered["client_capabilities"])
        else:
            reply["error"] = {"code": -32600, "message": "no handshake and no _meta: I don't know who you are"}

        sys.stdout.write(json.dumps(reply) + "\n")
        sys.stdout.flush()


def play() -> None:
    def new_server() -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, __file__, "--server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )

    def send(server: subprocess.Popen, message: dict) -> None:
        print(f"  Host   -> {json.dumps(message)}")
        server.stdin.write(json.dumps(message) + "\n")
        server.stdin.flush()
        if "id" in message:
            print(f"  Server <- {server.stdout.readline().strip()}\n")
        else:
            print("  (notification, no reply)\n")

    client_info = {"name": "see-stateless", "version": "0.1.0"}
    call = {"name": "add", "arguments": {"a": 7, "b": 5}}

    print("=== LEGACY: three messages before any work, server remembers the client ===\n")
    server = new_server()
    send(server, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": LEGACY, "capabilities": {"elicitation": {}}, "clientInfo": client_info}})
    send(server, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    send(server, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": call})
    server.stdin.close()
    server.wait()

    print("=== MODERN: one self-describing request, to a server that has never seen us ===\n")
    server = new_server()
    meta = {VERSION_KEY: MODERN, CLIENT_INFO_KEY: client_info, CAPABILITIES_KEY: {"elicitation": {}}}
    send(server, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {**call, "_meta": meta}})

    print("=== MODERN: same server, a request that declares NO capabilities ===\n")
    send(server, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
        **call, "_meta": {**meta, CAPABILITIES_KEY: {}}}})

    print("=== MODERN: a version the server does not speak ===\n")
    send(server, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        **call, "_meta": {**meta, VERSION_KEY: "1900-01-01"}}})

    print("=== NEITHER: no handshake, no _meta ===\n")
    send(server, {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": call})
    server.stdin.close()
    server.wait()


if __name__ == "__main__":
    serve() if "--server" in sys.argv else play()
