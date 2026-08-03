"""Phase 6: the adversarial test corpus. Spawns the real `verimcp` command
fronting `tests/fixtures/lying_server.py` -- a fixture that tells one
specific, controllable lie per scenario -- and proves the real verifier for
that lie's domain catches it, over a real stdio pipe, not via a hand-built
response dict (every existing "catches a fabricated X" test elsewhere in this
repo is unit-level, calling verify() directly; these are not).

This is both the regression suite and the evidence behind the README's
"verimcp catches 6/6 adversarial backends" line -- see docs/ROADMAP.md
Phase 6.
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

_FIXTURE = Path(__file__).parent / "fixtures" / "lying_server.py"


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True)
    (repo / "seed.txt").write_text("hi")
    subprocess.run(["git", "add", "seed.txt"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, capture_output=True, check=True)


async def _send(proc, message: dict) -> None:
    proc.stdin.write((json.dumps(message) + "\n").encode())
    await proc.stdin.drain()


async def _recv(proc) -> dict:
    line = await proc.stdout.readline()
    return json.loads(line)


async def _spawn_verimcp_fronting_liar(repo_path: Path, lie: str, verifiers: str | None = None):
    verimcp_args = ["--root", str(repo_path)]
    if verifiers is not None:
        verimcp_args += ["--verifiers", verifiers]
    return await asyncio.create_subprocess_exec(
        sys.executable, "-m", "verimcp.cli", *verimcp_args, "--",
        sys.executable, str(_FIXTURE), "--lie", lie, "--repo-path", str(repo_path),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )


async def _initialize(proc) -> None:
    await _send(proc, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "adversarial-test", "version": "0"},
        },
    })
    await _recv(proc)
    await _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})


async def _call_tool(proc, name: str, arguments: dict) -> dict:
    await _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
    return await _recv(proc)


async def _read_resource(proc, uri: str) -> dict:
    await _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": uri}})
    return await _recv(proc)


async def test_catches_write_file_that_never_touched_disk(tmp_path: Path):
    _init_repo(tmp_path)
    proc = await _spawn_verimcp_fronting_liar(tmp_path, "write_file_wrong_content")
    try:
        await _initialize(proc)
        response = await _call_tool(proc, "write_file", {"path": "notes.txt", "content": "hello"})

        assert response["result"]["isError"] is True
        assert "[verimcp] postcondition check failed" in response["result"]["content"][0]["text"]
        assert "does not exist" in response["result"]["content"][0]["text"]
        assert not (tmp_path / "notes.txt").exists()
    finally:
        proc.terminate()
        await proc.wait()


async def test_catches_git_commit_with_a_hash_that_was_never_created(tmp_path: Path):
    _init_repo(tmp_path)
    proc = await _spawn_verimcp_fronting_liar(tmp_path, "git_commit_fake_hash")
    try:
        await _initialize(proc)
        response = await _call_tool(proc, "git_commit", {"message": "fake"})

        assert response["result"]["isError"] is True
        assert "does not exist in this repo's history" in response["result"]["content"][0]["text"]
    finally:
        proc.terminate()
        await proc.wait()


async def test_catches_git_branch_that_was_never_created(tmp_path: Path):
    _init_repo(tmp_path)
    proc = await _spawn_verimcp_fronting_liar(tmp_path, "git_branch_fake_target")
    try:
        await _initialize(proc)
        response = await _call_tool(proc, "git_branch", {"name": "feature-that-does-not-exist"})

        assert response["result"]["isError"] is True
        assert "does not exist in this repo" in response["result"]["content"][0]["text"]
    finally:
        proc.terminate()
        await proc.wait()


async def test_catches_ci_step_falsely_claimed_to_pass(tmp_path: Path):
    """The step's real command always fails; the fixture claims it passed
    anyway. Marked idempotent so CIRunVerifier actually re-runs it (an
    unmarked step is only checked for self-consistency, not re-executed)."""
    _init_repo(tmp_path)
    proc = await _spawn_verimcp_fronting_liar(tmp_path, "ci_pipeline_false_pass")
    try:
        await _initialize(proc)
        failing_cmd = f"{sys.executable} -c \"import sys; sys.exit(1)\""
        response = await _call_tool(proc, "run_ci_pipeline", {
            "steps": [{"name": "always-fails", "cmd": failing_cmd, "idempotent": True}],
        })

        assert response["result"]["isError"] is True
        assert "re-running it produced exit_code=1" in response["result"]["content"][0]["text"]
    finally:
        proc.terminate()
        await proc.wait()


async def test_catches_fabricated_resource_status(tmp_path: Path):
    _init_repo(tmp_path)
    proc = await _spawn_verimcp_fronting_liar(tmp_path, "resource_status_fabricated")
    try:
        await _initialize(proc)
        response = await _read_resource(proc, "repo://status")

        assert "error" in response
        assert "do not match the repo's real current state" in response["error"]["message"]
    finally:
        proc.terminate()
        await proc.wait()


async def test_catches_git_server_style_fake_hash(tmp_path: Path):
    """Formalizes Phase 1's throwaway hand-verified fake-backend check into a
    permanent regression: the plain-text (no structuredContent) response
    shape used by the real third-party mcp-server-git, caught by
    GitServerCommitVerifier specifically -- --verifiers restricts to just it
    so this test is unambiguous about which verifier is doing the catching."""
    _init_repo(tmp_path)
    proc = await _spawn_verimcp_fronting_liar(tmp_path, "git_server_commit_fake_hash", verifiers="git_server_commit")
    try:
        await _initialize(proc)
        response = await _call_tool(proc, "git_commit", {"message": "fake"})

        assert response["result"]["isError"] is True
        assert "does not exist in this repo's history" in response["result"]["content"][0]["text"]
    finally:
        proc.terminate()
        await proc.wait()
