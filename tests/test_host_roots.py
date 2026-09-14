"""verimcp asks the Host for its roots itself, so verifiers work in front of
backends that never ask (mcp-server-git, filesystem servers, the adversarial
fixture) -- the case `verimcp wrap` creates for most real servers, where no
--root is ever passed."""
import asyncio
import json
import sys
from pathlib import Path

LIAR = Path(__file__).parent / "fixtures" / "lying_server.py"


async def _session(tmp_path: Path, host_capabilities: dict):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "verimcp.cli", "--",
        sys.executable, str(LIAR), "--lie", "write_file_wrong_content", "--repo-path", str(tmp_path),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
    )

    async def send(message):
        proc.stdin.write((json.dumps(message) + "\n").encode())
        await proc.stdin.drain()

    async def recv():
        return json.loads(await asyncio.wait_for(proc.stdout.readline(), timeout=15))

    await send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": host_capabilities, "clientInfo": {"name": "host", "version": "0"}}})
    await recv()
    await send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    return proc, send, recv


async def test_asks_a_roots_capable_host_and_then_verifies(tmp_path: Path):
    proc, send, recv = await _session(tmp_path, {"roots": {"listChanged": True}})
    try:
        roots_request = await recv()
        assert roots_request["method"] == "roots/list"
        assert roots_request["id"].startswith("verimcp-roots-")
        await send({"jsonrpc": "2.0", "id": roots_request["id"],
                    "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "workspace"}]}})

        await send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hi"}}})
        response = await recv()
    finally:
        proc.terminate()
        await proc.wait()

    assert response["id"] == 2
    assert response["result"]["isError"] is True
    assert "file does not exist" in response["result"]["content"][0]["text"]


async def test_does_not_ask_a_host_without_the_roots_capability(tmp_path: Path):
    proc, send, recv = await _session(tmp_path, {})
    try:
        await send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hi"}}})
        response = await recv()  # no roots/list arrives first
    finally:
        proc.terminate()
        await proc.wait()

    assert response["id"] == 2
    assert response["result"]["isError"] is False  # no root known: unchecked, exactly as before
