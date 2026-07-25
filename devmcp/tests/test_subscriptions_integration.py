"""Proves a real, unprompted push: subscribe to a resource, trigger a tool
that mutates it, and see notifications/resources/updated arrive without ever
asking for it -- over a real subprocess, real stdio."""
import asyncio
import json
import sys


async def _send(proc, message: dict) -> None:
    proc.stdin.write((json.dumps(message) + "\n").encode())
    await proc.stdin.drain()


async def _recv(proc) -> dict:
    """Reads the next message, transparently answering any unsolicited
    roots/list request devmcp sends with 'no roots' -- so devmcp falls back
    to its own --repo-path, matching what this test already expects."""
    while True:
        line = await proc.stdout.readline()
        message = json.loads(line)
        if message.get("method") == "roots/list" and "id" in message:
            await _send(proc, {"jsonrpc": "2.0", "id": message["id"], "result": {"roots": []}})
            continue
        return message


async def test_subscribe_then_commit_pushes_unprompted_notification(tmp_git_repo):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "devmcp.cli", "--repo-path", str(tmp_git_repo),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    try:
        await _send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
        })
        await _recv(proc)
        await _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        await _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "resources/subscribe", "params": {"uri": "repo://status"}})
        sub_response = await _recv(proc)
        assert "result" in sub_response

        await _send(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "a.txt", "content": "x"}},
        })
        await _recv(proc)

        await _send(proc, {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "git_commit", "arguments": {"message": "add a", "files": ["a.txt"]}},
        })

        first = await _recv(proc)
        second = await _recv(proc)
        notification = first if "method" in first else second
        commit_response = second if "method" in first else first

        assert notification["method"] == "notifications/resources/updated"
        assert notification["params"]["uri"] == "repo://status"
        assert "id" not in notification

        assert commit_response["id"] == 4
        assert commit_response["result"]["isError"] is False
    finally:
        proc.terminate()
        await proc.wait()
