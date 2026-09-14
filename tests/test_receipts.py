"""Receipts: every check leaves proof of what it observed -- for a claim that
held and for one that didn't -- in the audit log, and (with --receipts) in
the tool reply itself."""
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from verimcp import receipts
from verimcp.verifiers.base import RECEIPTS_KEY, pop_receipts
from verimcp.verifiers.filesystem import FilesystemVerifier
from verimcp.verifiers.git_commit import GitCommitVerifier
from verimcp.verifiers.github import GitHubVerifier

LIAR = Path(__file__).parent / "fixtures" / "lying_server.py"


def _write_request(path: str, content: str) -> dict:
    return {"method": "tools/call", "params": {"name": "write_file", "arguments": {"path": path, "content": content}}}


def _ok() -> dict:
    return {"result": {"isError": False, "content": [{"type": "text", "text": "done"}]}}


def test_a_verified_write_leaves_a_receipt_with_the_real_hash(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("hello")
    response = FilesystemVerifier().verify(_write_request("notes.txt", "hello"), _ok(), root=tmp_path)

    [receipt] = pop_receipts(response)
    assert receipt["verdict"] == "verified"
    assert receipt["evidence"]["bytes"] == 5
    assert receipt["evidence"]["sha256"].startswith("2cf24dba5fb0")  # sha256("hello")
    assert "matches the request" in receipt["summary"]
    assert RECEIPTS_KEY not in response


def test_a_caught_write_leaves_a_short_receipt(tmp_path: Path):
    response = FilesystemVerifier().verify(_write_request("missing.txt", "hi"), _ok(), root=tmp_path)

    [receipt] = pop_receipts(response)
    assert receipt["verdict"] == "contradicted"
    assert receipt["evidence"] == {"path": "missing.txt", "exists": False}
    assert response["result"]["isError"] is True


def test_a_verified_commit_receipt_shows_git_s_own_record(tmp_path: Path):
    for cmd in (["init"], ["config", "user.email", "r@example.com"], ["config", "user.name", "Rudra"]):
        subprocess.run(["git", *cmd], cwd=tmp_path, capture_output=True, check=True)
    (tmp_path / "a.txt").write_text("a")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "Add login page"], cwd=tmp_path, capture_output=True, check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True, text=True, check=True).stdout.strip()

    response = {"result": {"isError": False, "structuredContent": {"commit_hash": head}}}
    GitCommitVerifier().verify({}, response, root=tmp_path)

    [receipt] = pop_receipts(response)
    assert receipt["verdict"] == "verified"
    assert receipt["evidence"]["message"] == "Add login page"
    assert receipt["evidence"]["author"] == "Rudra"
    assert "'Add login page' by Rudra" in receipt["summary"]


def test_a_github_receipt_carries_the_pr_link_and_title():
    def fetch(path):
        return {"/repos/octo/demo/pulls/7": (200, {"title": "Add login", "state": "open", "html_url":
                "https://github.com/octo/demo/pull/7", "user": {"login": "rudra"}, "created_at": "2026-09-15T00:00:00Z"}),
                }.get(path, (404, None))

    request = {"method": "tools/call", "params": {"name": "create_pull_request", "arguments": {
        "owner": "octo", "repo": "demo", "title": "Add login", "head": "f", "base": "main"}}}
    response = {"result": {"isError": False, "content": [{"type": "text", "text": json.dumps(
        {"id": "1", "url": "https://github.com/octo/demo/pull/7"})}]}}

    GitHubVerifier(fetch=fetch).verify(request, response)

    [receipt] = pop_receipts(response)
    assert receipt["verdict"] == "verified"
    assert receipt["evidence"]["url"] == "https://github.com/octo/demo/pull/7"
    assert receipt["evidence"]["author"] == "rudra"
    assert receipt["source"] == "GitHub API GET /repos/octo/demo/pulls/7"


def test_reply_block_is_appended_to_a_copy_and_keeps_content_0():
    message = {"jsonrpc": "2.0", "id": 3, "result": {"isError": False, "content": [{"type": "text", "text": "done"}]}}
    receipt = {"verifier": "X", "verdict": "verified", "summary": "it is real", "source": "disk",
               "checked_at": "2026-09-15T00:00:00+00:00", "evidence": {"url": "https://example.com/pr/1"}}

    with_receipt = receipts.attach_to_reply(message, [receipt])

    assert len(message["result"]["content"]) == 1, "the original must stay untouched (the retry cache may hold it)"
    assert with_receipt["result"]["content"][0]["text"] == "done"
    text = with_receipt["result"]["content"][1]["text"]
    assert text.startswith("[verimcp receipt] VERIFIED · X · 2026-09-15T00:00:00+00:00")
    assert "it is real" in text and "source: disk" in text and "link: https://example.com/pr/1" in text


def test_fallback_says_only_what_is_known():
    assert receipts.fallback(["SQLiteVerifier"], None, None) == []
    [ok] = receipts.fallback(["SQLiteVerifier"], True, None)
    assert ok["verdict"] == "verified" and "doesn't record receipt details yet" in ok["summary"]
    [bad] = receipts.fallback(["SQLiteVerifier"], False, "row not found")
    assert bad["verdict"] == "contradicted" and bad["summary"] == "row not found"


async def _proxied_call(tmp_path: Path, lie: str, tool: str, arguments: dict, *flags: str) -> dict:
    audit = tmp_path / "audit.jsonl"
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "verimcp.cli", "--root", str(tmp_path), "--audit-log", str(audit), *flags, "--",
        sys.executable, str(LIAR), "--lie", lie, "--repo-path", str(tmp_path),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
    )

    async def send(message):
        proc.stdin.write((json.dumps(message) + "\n").encode())
        await proc.stdin.drain()

    try:
        await send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "receipts", "version": "0"}}})
        await proc.stdout.readline()
        await send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        await send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}})
        return json.loads(await asyncio.wait_for(proc.stdout.readline(), timeout=15))
    finally:
        proc.terminate()
        await proc.wait()


def _init_repo(repo: Path) -> None:
    for cmd in (["init"], ["config", "user.email", "t@example.com"], ["config", "user.name", "Test"]):
        subprocess.run(["git", *cmd], cwd=repo, capture_output=True, check=True)
    (repo / "seed.txt").write_text("hi")
    subprocess.run(["git", "add", "seed.txt"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, capture_output=True, check=True)


async def test_through_the_proxy_a_caught_lie_carries_its_receipt(tmp_path: Path):
    response = await _proxied_call(tmp_path, "write_file_wrong_content", "write_file",
                                   {"path": "notes.txt", "content": "hello"}, "--receipts")

    content = response["result"]["content"]
    assert response["result"]["isError"] is True
    assert "file does not exist" in content[0]["text"]
    assert content[1]["text"].startswith("[verimcp receipt] CONTRADICTED · FilesystemVerifier")
    assert RECEIPTS_KEY not in json.dumps(response), "the private key must never reach the Host"

    [entry] = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert entry["verification"]["receipts"][0]["evidence"] == {"path": "notes.txt", "exists": False}


async def test_through_the_proxy_a_verified_claim_carries_its_receipt(tmp_path: Path):
    _init_repo(tmp_path)
    # The reused-hash lie returns a commit that genuinely exists, so it passes --
    # and the receipt shows exactly which commit that is: the old "seed" one.
    response = await _proxied_call(tmp_path, "git_commit_reused_real_hash", "git_commit",
                                   {"message": "my new commit", "files": ["seed.txt"]}, "--receipts")

    assert response["result"]["isError"] is False
    receipt_text = response["result"]["content"][-1]["text"]
    assert receipt_text.startswith("[verimcp receipt] VERIFIED")
    assert "'seed' by Test" in receipt_text


async def test_without_the_flag_the_reply_is_unchanged_but_the_log_has_the_receipt(tmp_path: Path):
    response = await _proxied_call(tmp_path, "write_file_wrong_content", "write_file",
                                   {"path": "notes.txt", "content": "hello"})

    assert len(response["result"]["content"]) == 1
    [entry] = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert entry["verification"]["receipts"][0]["verdict"] == "contradicted"
