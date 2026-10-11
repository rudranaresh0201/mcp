"""A stateless-era (2026-07-28) backend with one tool that leaves a real mark.

    touch {path}  -> creates the file, and reports which params keys reached
                     it, so a test can prove what the proxy did NOT forward.

No `initialize`: every request must carry its protocol version in _meta.
"""
import json
import sys
from pathlib import Path

VERSION_KEY = "io.modelcontextprotocol/protocolVersion"

for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    params = message.get("params", {})
    reply: dict = {"jsonrpc": "2.0", "id": message["id"]}
    if VERSION_KEY not in params.get("_meta", {}):
        reply["error"] = {"code": -32600, "message": "missing _meta protocol version"}
    elif message.get("method") == "tools/call" and params.get("name") == "touch":
        Path(params["arguments"]["path"]).write_text("touched")
        reply["result"] = {
            "resultType": "complete",
            "content": [{"type": "text", "text": json.dumps(sorted(params))}],
            "isError": False,
        }
    else:
        reply["error"] = {"code": -32601, "message": f"unknown method {message.get('method')!r}"}
    sys.stdout.write(json.dumps(reply) + "\n")
    sys.stdout.flush()
