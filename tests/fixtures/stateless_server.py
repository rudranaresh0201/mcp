"""A stateless-era (2026-07-28) backend with one tool that leaves a real mark.

    touch {path}  -> creates the file, and reports which params keys reached
                     it, so a test can prove what the proxy did NOT forward.

No `initialize`: every request must carry its protocol version in _meta, as
the spec requires -- `server/discover` answers with a DiscoverResult, a
version it doesn't speak gets UnsupportedProtocolVersionError (-32022), and
`initialize` is refused naming the versions it does support.
"""
import json
import sys
from pathlib import Path

VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
SUPPORTED = ["2026-07-28"]
TOUCH = {"name": "touch", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}}}

for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    params = message.get("params", {})
    method = message.get("method")
    version = params.get("_meta", {}).get(VERSION_KEY)
    reply: dict = {"jsonrpc": "2.0", "id": message["id"]}
    if method == "initialize":
        reply["error"] = {"code": -32601, "message": f"initialize is not supported; this server speaks {SUPPORTED}"}
    elif version is None:
        reply["error"] = {"code": -32600, "message": "missing _meta protocol version"}
    elif version not in SUPPORTED:
        reply["error"] = {
            "code": -32022,
            "message": "Unsupported protocol version",
            "data": {"supported": SUPPORTED, "requested": version},
        }
    elif method == "server/discover":
        reply["result"] = {
            "resultType": "complete",
            "supportedVersions": SUPPORTED,
            "capabilities": {"tools": {}},
            "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "stateless-fixture", "version": "1.0.0"}},
        }
    elif method == "tools/list":
        reply["result"] = {"resultType": "complete", "tools": [TOUCH]}
    elif method == "tools/call" and params.get("name") == "touch":
        Path(params["arguments"]["path"]).write_text("touched")
        reply["result"] = {
            "resultType": "complete",
            "content": [{"type": "text", "text": json.dumps(sorted(params))}],
            "isError": False,
        }
    else:
        reply["error"] = {"code": -32601, "message": f"unknown method {method!r}"}
    sys.stdout.write(json.dumps(reply) + "\n")
    sys.stdout.flush()
