"""Produce a real audit log for the console to render.

Not fixture data: this drives the actual `verimcp.cli` in front of two real
backends -- the real `devmcp` server, and the Phase 6 adversarial fixture
(`tests/fixtures/lying_server.py`) -- with the actual verifiers running.
Every line it writes came from the code path a live session uses, which
matters, because a dashboard screenshot of invented events would be exactly
the kind of unearned claim this project exists to catch.

Both backends are here on purpose. A console showing only caught lies proves
nothing about false positives; the honest devmcp session is what makes the
caught ones mean something.

    python scripts/demo_audit_log.py --out demo-audit.jsonl
    verimcp-dashboard --audit-log demo-audit.jsonl --replay 0.7
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path

_FIXTURE = Path(__file__).parent.parent / "tests" / "fixtures" / "lying_server.py"


# `backend: devmcp` runs the real server. Otherwise the fixture is pinned to
# one specific lie -- it fabricates that single claim and answers every other
# tool with its own honest "no scenario for this tool" error, which is why a
# log from it contains backend_error lines as well as catches.
SESSIONS = [
    {
        "backend": "devmcp",
        "calls": [
            ("tools/call", {"name": "write_file", "arguments": {"path": "notes.txt", "content": "sprint plan"}}),
            ("tools/call", {"name": "write_file", "arguments": {"path": "todo.md", "content": "- ship the console"}}),
            ("tools/call", {"name": "git_branch", "arguments": {"name": "feature/console"}}),
            ("tools/call", {"name": "git_commit", "arguments": {"message": "add console"}}),
            ("resources/read", {"uri": "repo://status"}),
        ],
    },
    {
        "lie": "git_commit_fake_hash",
        "calls": [
            ("tools/call", {"name": "git_commit", "arguments": {"message": "fix auth"}}),
        ],
    },
    {
        "lie": "write_file_wrong_content",
        "calls": [
            ("tools/call", {"name": "write_file", "arguments": {"path": "config.yaml", "content": "debug: false"}}),
        ],
    },
    {
        "lie": "git_branch_fake_target",
        "calls": [
            ("tools/call", {"name": "git_branch", "arguments": {"name": "hotfix/auth"}}),
        ],
    },
    {
        # The subtle one: the file really does exist, the write really did
        # happen -- the *content* is not what was asked for. Nothing about
        # the response shape distinguishes this from a clean success.
        "lie": "write_file_content_tampered",
        "calls": [
            ("tools/call", {"name": "write_file", "arguments": {"path": "deploy.sh", "content": "set -euo pipefail"}}),
        ],
    },
    {
        "lie": "ci_pipeline_false_pass",
        "calls": [
            ("tools/call", {"name": "run_ci_pipeline", "arguments": {
                "steps": [{"name": "tests", "cmd": f"{sys.executable} -c \"import sys; sys.exit(1)\"", "idempotent": True}]
            }}),
        ],
    },
]


def _init_repo(repo: Path) -> None:
    for cmd in (
        ["git", "init"],
        ["git", "config", "user.email", "demo@example.com"],
        ["git", "config", "user.name", "Demo"],
    ):
        subprocess.run(cmd, cwd=repo, capture_output=True, check=True)
    (repo / "seed.txt").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "add", "seed.txt"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, capture_output=True, check=True)


async def _send(proc, message: dict) -> None:
    proc.stdin.write((json.dumps(message) + "\n").encode())
    await proc.stdin.drain()


async def _recv(proc) -> dict:
    return json.loads(await proc.stdout.readline())


def _backend_argv(session: dict, repo: Path) -> list[str]:
    if session.get("backend") == "devmcp":
        return [sys.executable, "-m", "devmcp.cli", "--repo-path", str(repo)]
    return [sys.executable, str(_FIXTURE), "--lie", session["lie"], "--repo-path", str(repo)]


def _label(session: dict) -> str:
    return "devmcp (honest)" if session.get("backend") == "devmcp" else f"lying_server --lie {session['lie']}"


async def _run_session(repo: Path, audit_log: Path, session: dict) -> None:
    """One Host <-> verimcp <-> backend connection, recorded to audit_log."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "verimcp.cli",
        "--root", str(repo), "--audit-log", str(audit_log), "--",
        *_backend_argv(session, repo),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
    )
    try:
        await _send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "demo", "version": "0"}},
        })
        await _recv(proc)
        await _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        for index, (method, params) in enumerate(session["calls"], start=2):
            await _send(proc, {"jsonrpc": "2.0", "id": index, "method": method, "params": params})
            await _recv(proc)
            print(f"    {params.get('name') or params.get('uri')}")
    finally:
        proc.terminate()
        await proc.wait()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("demo-audit.jsonl"))
    parser.add_argument("--append", action="store_true",
                        help="add to an existing log instead of starting a clean one")
    args = parser.parse_args()

    if args.out.exists() and not args.append:
        args.out.unlink()

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        repo.mkdir()
        _init_repo(repo)
        for session in SESSIONS:
            print(f"  session: {_label(session)}")
            await _run_session(repo, args.out, session)

    lines = [json.loads(line) for line in args.out.read_text(encoding="utf-8").splitlines() if line.strip()]

    # Counted off the audit log's own taxonomy, never off "the response was an
    # error" -- that shortcut is precisely the conflation fixed in proxy.py on
    # 2026-08-30, and repeating it here would have this script overstate its
    # own results by the same factor it did before the fix.
    counts: dict[str, int] = {}
    for entry in lines:
        counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1

    sessions = len({line["session_id"] for line in lines})
    print(f"\n{len(lines)} audit events across {sessions} sessions -> {args.out}")
    for outcome in sorted(counts):
        print(f"  {outcome:<16} {counts[outcome]}")
    print(f"\n{counts.get('verified_failed', 0)} fabricated claim(s) independently disproven and rewritten")
    print(f"{counts.get('backend_error', 0)} call(s) the backend itself reported failed -- not catches")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
