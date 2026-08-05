"""A 30-second, human-readable proof: run this and watch verimcp catch a
real lie over a real stdio pipe. No pytest, no mocks -- just the actual
`verimcp` CLI fronting two different backends and the raw JSON-RPC exchange
printed as it happens.

Usage: python scripts/demo.py
"""
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_FIXTURE = Path(__file__).parent.parent / "tests" / "fixtures" / "lying_server.py"


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=repo, capture_output=True, check=True)
    (repo / "seed.txt").write_text("hi")
    subprocess.run(["git", "add", "seed.txt"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, capture_output=True, check=True)


async def _send(proc, message: dict) -> None:
    proc.stdin.write((json.dumps(message) + "\n").encode())
    await proc.stdin.drain()


async def _recv_response(proc, repo_path: Path) -> dict:
    """Read messages until the actual response arrives, answering any
    nested request (e.g. devmcp's own self-originated roots/list, fired
    right after notifications/initialized) along the way -- a real MCP
    Host has to do exactly this, since a backend can interleave its own
    requests with responses to the Host's, and a response never has a
    "method" key while a request always does."""
    while True:
        line = await proc.stdout.readline()
        message = json.loads(line)
        if "method" not in message:
            return message
        if message["method"] == "roots/list":
            await _send(proc, {
                "jsonrpc": "2.0", "id": message["id"],
                "result": {"roots": [{"uri": repo_path.resolve().as_uri(), "name": "demo-repo"}]},
            })


async def _initialize(proc, repo_path: Path) -> None:
    await _send(proc, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"roots": {"listChanged": False}},
            "clientInfo": {"name": "demo", "version": "0"},
        },
    })
    await _recv_response(proc, repo_path)
    await _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})


def _print_header(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


async def run_against_liar(repo_path: Path) -> None:
    _print_header("1. verimcp fronting a REAL backend that LIES about a commit")
    print("Backend claims: 'committed as ffff...ffff' (a fabricated hash, never actually created)\n")

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "verimcp.cli", "--root", str(repo_path), "--",
        sys.executable, str(_FIXTURE), "--lie", "git_commit_fake_hash", "--repo-path", str(repo_path),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
    )
    try:
        await _initialize(proc, repo_path)
        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "git_commit", "arguments": {"message": "demo commit", "files": ["seed.txt"]}},
        })
        response = await _recv_response(proc, repo_path)
        print("verimcp's response back to the Host:")
        print(json.dumps(response, indent=2))
        result = response["result"]
        print(f"\n>>> isError: {result['isError']}  (verimcp independently checked `git cat-file -e` for that")
        print(">>> hash and found it never existed -- the lie was caught before the Host ever saw it as success.)")
    finally:
        proc.terminate()
        await proc.wait()


async def run_against_honest_backend(repo_path: Path) -> None:
    _print_header("2. verimcp fronting the REAL devmcp server, writing a REAL file")

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "verimcp.cli", "--", sys.executable, "-m", "devmcp.cli", "--repo-path", str(repo_path),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
    )
    try:
        await _initialize(proc, repo_path)
        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "hello.txt", "content": "real content"}},
        })
        response = await _recv_response(proc, repo_path)
        print("verimcp's response back to the Host:")
        print(json.dumps(response, indent=2))
        on_disk = (repo_path / "hello.txt").read_text()
        print(f"\n>>> isError: {response['result']['isError']}  |  actual file on disk: {on_disk!r}")
        print(">>> verimcp re-read the file itself and hash-compared it against devmcp's claim -- it")
        print(">>> matched, so the response passed through unchanged.")
    finally:
        proc.terminate()
        await proc.wait()


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        repo_path = Path(tmp)
        _init_repo(repo_path)
        await run_against_liar(repo_path)
        await run_against_honest_backend(repo_path)
    print(f"\n{'=' * 70}\nDone. Same proxy, same protocol -- one caught a lie, one let a true claim through.\n{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
