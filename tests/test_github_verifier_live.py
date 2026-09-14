"""End-to-end, against the real GitHub API: spawns the real `verimcp`
command fronting tests/fixtures/lying_server.py, which answers in
github/github-mcp-server's exact response shapes, and lets GitHubVerifier
check each claim against a real public repo (rudranaresh0201/PRGuard, which
has real PRs #9-#11).

Skipped when api.github.com is unreachable, so an offline test run stays
green. Unauthenticated calls are enough for a public repo; each test spends
two or three of the 60 requests/hour GitHub allows without a token."""
import asyncio
import json
import sys
import urllib.request
from pathlib import Path

import pytest

_FIXTURE = Path(__file__).parent / "fixtures" / "lying_server.py"
OWNER, REPO = "rudranaresh0201", "PRGuard"
REAL_PR, REAL_PR_TITLE = 11, "Add user API, update config, add payment migration"


def _github_reachable() -> bool:
    try:
        request = urllib.request.Request("https://api.github.com/rate_limit", headers={"User-Agent": "verimcp-tests"})
        with urllib.request.urlopen(request, timeout=5) as reply:
            return reply.status == 200 and json.loads(reply.read())["resources"]["core"]["remaining"] >= 10
    except (OSError, ValueError, KeyError):
        return False


pytestmark = pytest.mark.skipif(not _github_reachable(), reason="api.github.com unreachable or rate-limited")


async def _call_through_verimcp(tmp_path: Path, lie: str, tool: str, arguments: dict) -> dict:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "verimcp.cli", "--root", str(tmp_path), "--verifiers", "github", "--",
        sys.executable, str(_FIXTURE), "--lie", lie, "--repo-path", str(tmp_path),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
    )

    async def send(message: dict) -> None:
        proc.stdin.write((json.dumps(message) + "\n").encode())
        await proc.stdin.drain()

    try:
        await send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "github-live", "version": "0"},
        }})
        await proc.stdout.readline()
        await send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        await send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}})
        return json.loads(await proc.stdout.readline())
    finally:
        proc.terminate()
        await proc.wait()


def _text(response: dict) -> str:
    return response["result"]["content"][0]["text"]


@pytest.mark.timeout(60)
async def test_catches_a_pr_that_was_never_created(tmp_path: Path):
    response = await _call_through_verimcp(tmp_path, "github_pr_fake", "create_pull_request", {
        "owner": OWNER, "repo": REPO, "title": "Add login page", "head": "feature", "base": "main",
    })

    assert response["result"]["isError"] is True
    assert f"claimed PR #999999 does not exist in {OWNER}/{REPO}" in _text(response)


@pytest.mark.timeout(60)
async def test_catches_a_real_old_pr_passed_off_as_new(tmp_path: Path):
    response = await _call_through_verimcp(tmp_path, "github_pr_reused_real", "create_pull_request", {
        "owner": OWNER, "repo": REPO, "title": "Add login page", "head": "feature", "base": "main",
        "reuse_number": REAL_PR,
    })

    assert response["result"]["isError"] is True
    assert "not the requested 'Add login page'" in _text(response)


@pytest.mark.timeout(60)
async def test_passes_a_real_pr_whose_title_matches(tmp_path: Path):
    """No false positive: the claim points at a PR that really exists with
    exactly the requested title, so verimcp forwards it untouched. (Note the
    honest limit this also shows: a server reusing a PR whose title happens
    to match cannot be told apart from one that created it.)"""
    response = await _call_through_verimcp(tmp_path, "github_pr_reused_real", "create_pull_request", {
        "owner": OWNER, "repo": REPO, "title": REAL_PR_TITLE, "head": "feature", "base": "main",
        "reuse_number": REAL_PR,
    })

    assert response["result"]["isError"] is False


@pytest.mark.timeout(60)
async def test_catches_an_issue_that_was_never_created(tmp_path: Path):
    response = await _call_through_verimcp(tmp_path, "github_issue_fake", "issue_write", {
        "method": "create", "owner": OWNER, "repo": REPO, "title": "Login is broken",
    })

    assert response["result"]["isError"] is True
    assert f"claimed issue #999999 does not exist in {OWNER}/{REPO}" in _text(response)


@pytest.mark.timeout(60)
async def test_catches_a_branch_that_was_never_created(tmp_path: Path):
    response = await _call_through_verimcp(tmp_path, "github_branch_fake", "create_branch", {
        "owner": OWNER, "repo": REPO, "branch": "verimcp-never-created-this",
    })

    assert response["result"]["isError"] is True
    assert "claimed branch 'verimcp-never-created-this' does not exist" in _text(response)
