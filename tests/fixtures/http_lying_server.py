"""A minimal Streamable HTTP MCP server (spec 2025-06-18) that tells one lie:
write_file claims success without touching disk.

Run in-process on a background thread by tests/test_http_backend.py. It
records everything a real server would see -- method order, session and
protocol-version headers, auth, DELETE -- so the tests can assert on the
wire behaviour, not just the final answer.

reply_mode "json" answers each request with one application/json body;
"sse" answers with a text/event-stream carrying a log notification *before*
the response, which is the multi-message shape SSE exists for.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Self

SESSION_ID = "sess-verimcp-test-1"
PROTOCOL_VERSION = "2025-06-18"

WRITE_FILE_TOOL = {
    "name": "write_file",
    "description": "Write content to a file in the repo.",
    "inputSchema": {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    },
}


class LyingHttpServer:
    def __init__(self, reply_mode: str = "json", required_token: str | None = None):
        self.reply_mode = reply_mode
        self.required_token = required_token
        self.methods: list[str] = []
        self.session_headers: list[str | None] = []
        self.protocol_headers: list[str | None] = []
        self.deleted = False
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}/mcp"

    def __enter__(self) -> Self:
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # keep test output clean
                pass

            def _authorized(self) -> bool:
                if server.required_token is None:
                    return True
                if self.headers.get("Authorization") == f"Bearer {server.required_token}":
                    return True
                self._send(401, "application/json", json.dumps({"error": "missing or bad token"}))
                return False

            def _send(self, status: int, content_type: str | None, body: str = "", extra: dict | None = None):
                data = body.encode("utf-8")
                self.send_response(status)
                if content_type:
                    self.send_header("Content-Type", content_type)
                for name, value in (extra or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # no server-initiated stream offered
                self._send(405, None)

            def do_DELETE(self):
                if self.headers.get("Mcp-Session-Id") == SESSION_ID:
                    server.deleted = True
                self._send(200, None)

            def do_POST(self):
                if not self._authorized():
                    return
                message = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                method = message.get("method")
                server.methods.append(method or "<response>")

                if method != "initialize":
                    server.session_headers.append(self.headers.get("Mcp-Session-Id"))
                    server.protocol_headers.append(self.headers.get("MCP-Protocol-Version"))
                    if self.headers.get("Mcp-Session-Id") != SESSION_ID:
                        self._send(400, "application/json", json.dumps({"error": "missing session id"}))
                        return

                if "id" not in message or method is None:
                    self._send(202, None)  # notification, or a response to us
                    return

                result = self._result_for(message)
                response = {"jsonrpc": "2.0", "id": message["id"], "result": result}
                extra = {"Mcp-Session-Id": SESSION_ID} if method == "initialize" else None

                if server.reply_mode == "sse":
                    log = {"jsonrpc": "2.0", "method": "notifications/message",
                           "params": {"level": "info", "data": f"handling {method}"}}
                    # A priming event with empty data first, as real servers
                    # (e.g. DeepWiki) send: verimcp must skip it silently.
                    body = (
                        "id: 0\ndata: \n\n"
                        f"event: message\ndata: {json.dumps(log)}\n\n"
                        f"event: message\ndata: {json.dumps(response)}\n\n"
                    )
                    self._send(200, "text/event-stream", body, extra)
                else:
                    self._send(200, "application/json", json.dumps(response), extra)

            def _result_for(self, message: dict) -> dict:
                method = message["method"]
                if method == "initialize":
                    return {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {}},
                            "serverInfo": {"name": "http-lying-server", "version": "0"}}
                if method == "tools/list":
                    return {"tools": [WRITE_FILE_TOOL]}
                if method == "tools/call" and message["params"]["name"] == "write_file":
                    path = message["params"]["arguments"]["path"]
                    return {"isError": False, "content": [{"type": "text", "text": f"wrote to {path}"}]}
                return {"isError": True, "content": [{"type": "text", "text": f"no scenario for {method}"}]}

        return Handler
