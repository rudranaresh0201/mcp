"""--role: tools the role may not use are dropped from tools/list before the
Host sees them, and refused as unknown if called anyway. Real `verimcp`
fronting real `devmcp` over real stdio, same as test_proxy_integration.py."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

from verimcp.gates.tool_visibility import ToolVisibility

POLICY = """\
roles:
  intern:
    tools: [summarize_diff, "git_b*"]
  maintainer:
    tools: ["*"]
  nobody: {}
rules:
  - tool: git_branch
    action: deny
"""


async def _spawn(tmp_path: Path, *verimcp_args: str):
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "verimcp.cli", *verimcp_args, "--",
        sys.executable, "-m", "devmcp.cli", "--repo-path", str(tmp_path),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    await _ask(proc, {
        "jsonrpc": "2.0", "id": 0, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
    })
    proc.stdin.write((json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n").encode())
    await proc.stdin.drain()
    return proc


async def _ask(proc, request: dict) -> dict:
    proc.stdin.write((json.dumps(request) + "\n").encode())
    await proc.stdin.drain()
    while True:
        reply = json.loads(await proc.stdout.readline())
        if reply.get("id") == request["id"] and "method" not in reply:
            return reply


async def _tool_names(proc) -> list[str]:
    reply = await _ask(proc, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return [tool["name"] for tool in reply["result"]["tools"]]


def _policy(tmp_path: Path) -> str:
    path = tmp_path / "policy.yaml"
    path.write_text(POLICY)
    return str(path)


async def test_role_sees_only_its_tools_and_no_role_sees_all(tmp_path: Path):
    plain = await _spawn(tmp_path)
    intern = await _spawn(tmp_path, "--policy-config", _policy(tmp_path), "--role", "intern")
    try:
        everything = await _tool_names(plain)
        assert {"write_file", "docker_run_container", "git_commit"} <= set(everything)
        # Backend order kept (the spec asks for a deterministic list).
        assert await _tool_names(intern) == [n for n in everything if n in ("summarize_diff", "git_branch")]
    finally:
        for proc in (plain, intern):
            proc.terminate()
            await proc.wait()


async def test_calling_a_hidden_tool_is_refused_as_unknown_and_never_runs(tmp_path: Path):
    intern = await _spawn(tmp_path, "--policy-config", _policy(tmp_path), "--role", "intern")
    try:
        reply = await _ask(intern, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "pwned.txt", "content": "x"}},
        })
        assert reply["error"] == {"code": -32602, "message": "Unknown tool: write_file"}
        assert not (tmp_path / "pwned.txt").exists()
    finally:
        intern.terminate()
        await intern.wait()


async def test_a_visible_tool_is_still_subject_to_the_call_time_rules(tmp_path: Path):
    """Visible is not the same as allowed: git_branch is listed for the
    intern, and the `rules:` deny still stops the call."""
    intern = await _spawn(tmp_path, "--policy-config", _policy(tmp_path), "--role", "intern")
    try:
        reply = await _ask(intern, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "git_branch", "arguments": {"name": "x"}},
        })
        assert "denied by policy" in reply["error"]["message"]
    finally:
        intern.terminate()
        await intern.wait()


async def test_a_role_with_no_tools_sees_nothing(tmp_path: Path):
    nobody = await _spawn(tmp_path, "--policy-config", _policy(tmp_path), "--role", "nobody")
    try:
        assert await _tool_names(nobody) == []
    finally:
        nobody.terminate()
        await nobody.wait()


def test_an_undefined_role_fails_at_startup(tmp_path: Path):
    with pytest.raises(ValueError, match="'admin' is not defined"):
        ToolVisibility.from_yaml(Path(_policy(tmp_path)), "admin")


def test_patterns_are_case_sensitive_globs():
    visibility = ToolVisibility("intern", ["git_b*"])
    assert visibility.allows("git_branch")
    assert not visibility.allows("git_commit")
    assert not visibility.allows("GIT_BRANCH")
