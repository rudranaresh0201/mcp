"""Spawns the real `verimcp` command fronting the real, official, third-party
`mcp-server-git` reference server -- NOT devmcp. This is the genericity
proof for docs/ROADMAP.md Phase 1: verimcp has to prove it works in front of
a backend it did not write, with --verifiers restricting to just the
verifier built for this backend's response shape (git_server_commit),
since devmcp's own GitCommitVerifier expects a different shape entirely."""
import asyncio
import json
import subprocess
import sys
from pathlib import Path


async def _send(proc, message: dict) -> None:
    proc.stdin.write((json.dumps(message) + "\n").encode())
    await proc.stdin.drain()


async def _recv(proc) -> dict:
    line = await proc.stdout.readline()
    return json.loads(line)


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, capture_output=True, check=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True, stdin=subprocess.DEVNULL)
    (repo / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True, stdin=subprocess.DEVNULL)


async def _spawn_verimcp_fronting_reference_git_server(repo_path: Path):
    # mcp-server-git never sends the Host a roots/list request (it takes its
    # repo path from its own --repository flag instead) -- so --root is
    # required here, not optional, or GitServerCommitVerifier would silently
    # fail open and this test would prove nothing about its actual check.
    return await asyncio.create_subprocess_exec(
        sys.executable, "-m", "verimcp.cli",
        "--verifiers", "git_server_commit", "--root", str(repo_path), "--",
        sys.executable, "-m", "mcp_server_git", "--repository", str(repo_path),
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


async def test_real_commit_against_reference_git_server_passes_through(tmp_path: Path):
    """True-positive path: a genuine commit through a backend we didn't
    write is independently confirmed and passed through unmodified -- the
    actual proof that verimcp is backend-agnostic, not devmcp-specific."""
    _init_repo(tmp_path)
    (tmp_path / "new.txt").write_text("new file\n")

    proc = await _spawn_verimcp_fronting_reference_git_server(tmp_path)
    try:
        await _initialize(proc)

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "git_add", "arguments": {"repo_path": str(tmp_path), "files": ["new.txt"]}},
        })
        await _recv(proc)

        await _send(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "git_commit", "arguments": {"repo_path": str(tmp_path), "message": "add new.txt"}},
        })
        response = await _recv(proc)

        assert response["result"]["isError"] is False
        assert "committed successfully" in response["result"]["content"][0]["text"]
    finally:
        proc.terminate()
        await proc.wait()
