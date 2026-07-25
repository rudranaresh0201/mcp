"""Spawns devmcp as a real subprocess and drives it over real stdio, exactly
the way a Host would -- no shortcuts through Python function calls."""
import asyncio
import json
import sys


async def _send(proc, message: dict) -> None:
    proc.stdin.write((json.dumps(message) + "\n").encode())
    await proc.stdin.drain()


async def _recv(proc) -> dict:
    """Reads the next message, transparently answering any unsolicited
    roots/list request devmcp sends with 'no roots' -- so devmcp falls back
    to its own --repo-path, matching what these older tests already expect."""
    while True:
        line = await proc.stdout.readline()
        message = json.loads(line)
        if message.get("method") == "roots/list" and "id" in message:
            await _send(proc, {"jsonrpc": "2.0", "id": message["id"], "result": {"roots": []}})
            continue
        return message


async def test_initialize_then_write_then_commit_over_real_stdio(tmp_git_repo):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "devmcp.cli", "--repo-path", str(tmp_git_repo),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    try:
        await _send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test-client", "version": "0"}},
        })
        init_response = await _recv(proc)
        assert init_response["result"]["serverInfo"]["name"] == "devmcp"

        await _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "hello.txt", "content": "hi"}},
        })
        write_response = await _recv(proc)
        assert write_response["result"]["isError"] is False

        await _send(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "git_commit", "arguments": {"message": "add hello", "files": ["hello.txt"]}},
        })
        commit_response = await _recv(proc)
        assert commit_response["result"]["isError"] is False
        commit_hash = commit_response["result"]["structuredContent"]["commit_hash"]

        log_proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(tmp_git_repo), "log", "-1", "--format=%H",
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await log_proc.communicate()
        assert stdout.decode().strip() == commit_hash
    finally:
        proc.terminate()
        await proc.wait()
