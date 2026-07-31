"""Spawns the real `verimcp` command fronting a real `devmcp` subprocess,
driven over real stdio -- the same shape devmcp/tests/test_server_integration.py
uses, one layer deeper. No mocks anywhere in this chain."""
import asyncio
import json
import sys
from pathlib import Path


async def _send(proc, message: dict) -> None:
    proc.stdin.write((json.dumps(message) + "\n").encode())
    await proc.stdin.drain()


async def _recv(proc) -> dict:
    line = await proc.stdout.readline()
    return json.loads(line)


async def _spawn_verimcp(repo_path: Path):
    return await asyncio.create_subprocess_exec(
        sys.executable, "-m", "verimcp.cli", "--",
        sys.executable, "-m", "devmcp.cli", "--repo-path", str(repo_path),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )


async def _initialize(proc) -> None:
    await _send(proc, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test-client", "version": "0"}},
    })
    await _recv(proc)  # initialize response, unused here
    await _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})


async def test_verified_pass_through_when_host_answers_roots(tmp_path: Path):
    """Host answers devmcp's roots/list -- verimcp learns the real root and
    can independently confirm the write actually happened."""
    proc = await _spawn_verimcp(tmp_path)
    try:
        await _initialize(proc)

        roots_request = await _recv(proc)
        assert roots_request["method"] == "roots/list"
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        response = await _recv(proc)

        assert response["result"]["isError"] is False
        assert (tmp_path / "notes.txt").read_text() == "hello"
    finally:
        proc.terminate()
        await proc.wait()


async def test_git_commit_verified_over_real_pipe(tmp_path: Path):
    """Regression test for a real deadlock: GitCommitVerifier spawns its own
    `git cat-file` subprocess to check the claimed hash. On Windows, without
    stdin=DEVNULL, that child inherits verimcp's real stdin -- which is
    simultaneously being read on a background thread by Proxy's own
    _StdinReader -- and the two deadlock forever. A verifier that calls
    subprocess directly can only be caught by a test that runs the real
    pipe, not by calling verify() as a plain function."""
    proc = await _spawn_verimcp(tmp_path)
    try:
        await _initialize(proc)
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "a.txt", "content": "hello"}},
        })
        await _recv(proc)

        await _send(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "git_commit", "arguments": {"message": "add a.txt", "files": ["a.txt"]}},
        })
        response = await _recv(proc)

        assert response["result"]["isError"] is False
    finally:
        proc.terminate()
        await proc.wait()


async def test_ci_run_verified_over_real_pipe(tmp_path: Path):
    """CIRunVerifier re-executes idempotent steps by shelling out itself --
    same subprocess-over-stdio deadlock risk as GitCommitVerifier above, so
    this needs the real pipe, not a hand-built response dict, to actually
    prove it doesn't hang."""
    proc = await _spawn_verimcp(tmp_path)
    try:
        await _initialize(proc)
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {
                "name": "run_ci_pipeline",
                "arguments": {"steps": [{"name": "ok", "cmd": 'python -c "import sys; sys.exit(0)"', "idempotent": True}]},
            },
        })
        response = await _recv(proc)

        assert response["result"]["isError"] is False
    finally:
        proc.terminate()
        await proc.wait()


async def test_resource_read_verified_over_real_pipe(tmp_path: Path):
    """resources/read is the first non-tools/call message type the proxy
    dispatches to a verifier -- this exercises that new branch in
    proxy.py's _forward_backend_to_host, not just ResourceReadVerifier in
    isolation."""
    proc = await _spawn_verimcp(tmp_path)
    try:
        await _initialize(proc)
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        await _recv(proc)

        await _send(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "resources/read",
            "params": {"uri": "repo://file/notes.txt"},
        })
        response = await _recv(proc)

        assert "error" not in response
        assert response["result"]["contents"][0]["text"] == "hello"
    finally:
        proc.terminate()
        await proc.wait()


async def test_fails_open_when_host_never_answers_roots(tmp_path: Path):
    """The bug this whole test file exists to pin down: if the Host never
    answers roots/list (plenty of real Hosts don't implement roots at all),
    verimcp must not falsely fail a write it can't independently locate --
    it should pass the backend's real, correct response through untouched."""
    proc = await _spawn_verimcp(tmp_path)
    try:
        await _initialize(proc)
        # Deliberately don't answer the roots/list request that arrives here --
        # devmcp falls back to its own --repo-path, same as many real Hosts would leave it.

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        response = await _recv(proc)
        while response.get("method") == "roots/list":
            response = await _recv(proc)  # skip past the unanswered roots/list if it arrives after

        assert response["id"] == 2
        assert response["result"]["isError"] is False
        assert (tmp_path / "notes.txt").read_text() == "hello"
    finally:
        proc.terminate()
        await proc.wait()
