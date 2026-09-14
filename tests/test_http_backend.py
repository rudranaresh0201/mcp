"""verimcp in front of a remote MCP server over Streamable HTTP: spawns the
real `verimcp --backend-url` process against tests/fixtures/http_lying_server.py
and checks both the verdict and what actually crossed the wire."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.http_lying_server import (
    PROTOCOL_VERSION,
    SESSION_ID,
    LyingHttpServer,
)
from verimcp.http_backend import expand_env, parse_headers


async def _spawn(url: str, root: Path, *extra: str, env: dict | None = None):
    return await asyncio.create_subprocess_exec(
        sys.executable, "-m", "verimcp.cli", "--root", str(root), "--backend-url", url, *extra,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        env={**os.environ, **(env or {})},
    )


async def _send(proc, message: dict) -> None:
    proc.stdin.write((json.dumps(message) + "\n").encode())
    await proc.stdin.drain()


async def _recv(proc) -> dict:
    return json.loads(await asyncio.wait_for(proc.stdout.readline(), timeout=15))


async def _recv_response(proc, request_id) -> tuple[dict, list[dict]]:
    """Read until the response to `request_id`, returning it plus anything
    the server sent before it (e.g. SSE log notifications)."""
    before = []
    while True:
        message = await _recv(proc)
        if message.get("id") == request_id and "method" not in message:
            return message, before
        before.append(message)


async def _initialize(proc) -> dict:
    await _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": {"name": "http-test", "version": "0"},
    }})
    response, _ = await _recv_response(proc, 1)
    await _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    return response


async def _close(proc) -> None:
    proc.stdin.close()
    try:
        await asyncio.wait_for(proc.wait(), timeout=10)
    except TimeoutError:
        proc.terminate()
        await proc.wait()


@pytest.mark.parametrize("reply_mode", ["json", "sse"])
async def test_catches_a_lie_from_a_remote_server(tmp_path: Path, reply_mode: str):
    with LyingHttpServer(reply_mode=reply_mode) as server:
        proc = await _spawn(server.url, tmp_path)
        try:
            init = await _initialize(proc)
            assert init["result"]["serverInfo"]["name"] == "http-lying-server"

            await _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            tools, _ = await _recv_response(proc, 2)
            assert tools["result"]["tools"][0]["name"] == "write_file"

            await _send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                               "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hi"}}})
            response, before = await _recv_response(proc, 3)
        finally:
            await _close(proc)

    assert response["result"]["isError"] is True
    assert "claimed write to 'notes.txt' succeeded, but the file does not exist" in response["result"]["content"][0]["text"]
    if reply_mode == "sse":
        # the extra message SSE carried ahead of the response reached the Host too
        assert any(m.get("method") == "notifications/message" for m in before)


async def test_wire_order_session_and_protocol_headers(tmp_path: Path):
    with LyingHttpServer() as server:
        proc = await _spawn(server.url, tmp_path)
        try:
            await _initialize(proc)
            await _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            await _recv_response(proc, 2)
        finally:
            await _close(proc)

        assert server.methods[:3] == ["initialize", "notifications/initialized", "tools/list"]
        assert server.session_headers == [SESSION_ID, SESSION_ID]
        assert server.protocol_headers == [PROTOCOL_VERSION, PROTOCOL_VERSION]
        assert server.deleted, "verimcp should end the session with DELETE on shutdown"


async def test_auth_header_is_filled_from_the_environment(tmp_path: Path):
    with LyingHttpServer(required_token="s3cret") as server:
        proc = await _spawn(
            server.url, tmp_path, "--header", "Authorization: Bearer ${VERIMCP_TEST_TOKEN}",
            env={"VERIMCP_TEST_TOKEN": "s3cret"},
        )
        try:
            init = await _initialize(proc)
        finally:
            await _close(proc)

    assert "result" in init


async def test_a_rejected_request_becomes_an_error_not_a_hang(tmp_path: Path):
    with LyingHttpServer(required_token="s3cret") as server:
        proc = await _spawn(server.url, tmp_path)  # no auth header
        try:
            init = await _initialize(proc)
        finally:
            await _close(proc)

    assert "HTTP 401" in init["error"]["message"]


async def test_unreachable_server_becomes_an_error_not_a_hang(tmp_path: Path):
    proc = await _spawn("http://127.0.0.1:9/mcp", tmp_path)
    try:
        init = await _initialize(proc)
    finally:
        await _close(proc)

    assert "could not reach remote MCP server" in init["error"]["message"]


def _cli_error(*args: str, env: dict | None = None) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "verimcp.cli", *args], capture_output=True, text=True,
        stdin=subprocess.DEVNULL, env={**os.environ, **(env or {})}, timeout=30, check=False,
    )
    assert result.returncode == 2
    return result.stderr


def test_cli_rejects_url_and_command_together():
    assert "not both" in _cli_error("--backend-url", "http://x/mcp", "--", "devmcp")


def test_cli_rejects_header_without_url():
    assert "--header only applies to --backend-url" in _cli_error("--header", "A: b", "--", "devmcp")


def test_cli_rejects_a_missing_environment_variable():
    env = {k: v for k, v in os.environ.items() if k != "VERIMCP_UNSET_VAR"}
    result = subprocess.run(
        [sys.executable, "-m", "verimcp.cli", "--backend-url", "http://x/mcp",
         "--header", "Authorization: Bearer ${VERIMCP_UNSET_VAR}"],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env, timeout=30, check=False,
    )
    assert result.returncode == 2
    assert "VERIMCP_UNSET_VAR" in result.stderr


def test_parse_headers_and_expand_env(monkeypatch):
    monkeypatch.setenv("VERIMCP_TOK", "abc")
    assert expand_env("Bearer ${VERIMCP_TOK}") == "Bearer abc"
    assert parse_headers(["Authorization: Bearer ${VERIMCP_TOK}", "X-Trace:  1 "]) == {
        "Authorization": "Bearer abc", "X-Trace": "1",
    }
    with pytest.raises(ValueError):
        parse_headers(["no-colon-here"])
