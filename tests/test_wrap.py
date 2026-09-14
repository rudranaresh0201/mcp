"""`verimcp wrap` / `unwrap` against a fake home directory holding real-shaped
Claude Code and Cursor configs -- then the wrapped entries are actually
launched, to prove the rewritten command lines really put verimcp in front."""
import asyncio
import json
import sys
from pathlib import Path

from tests.fixtures.http_lying_server import LyingHttpServer
from verimcp import wrap

LIAR = Path(__file__).parent / "fixtures" / "lying_server.py"


def _claude_json(project: str) -> dict:
    return {
        "numStartups": 12,  # unrelated state Claude Code keeps in the same file: must survive untouched
        "mcpServers": {
            "files": {"type": "stdio", "command": "npx", "args": ["-y", "some-files-server"], "env": {"A": "1"}},
            "remote-with-token": {"type": "http", "url": "https://example.com/mcp",
                                  "headers": {"Authorization": "Bearer tok-123"}},
            "remote-oauth": {"type": "http", "url": "https://oauth.example.com/mcp"},
            "legacy-sse": {"type": "sse", "url": "https://old.example.com/sse"},
        },
        "projects": {
            project: {
                "allowedTools": [],
                "mcpServers": {
                    "already": {"type": "stdio", "command": "verimcp", "args": ["--", "devmcp"], "env": {}},
                    "git": {"type": "stdio", "command": "uvx", "args": ["mcp-server-git"], "env": {}},
                },
            },
        },
    }


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text(json.dumps(_claude_json("C:/work/app")), encoding="utf-8")
    (home / ".cursor").mkdir()
    (home / ".cursor" / "mcp.json").write_text(
        json.dumps({"mcpServers": {"db": {"command": "python", "args": ["db_server.py"]}}}), encoding="utf-8"
    )
    return home


def _run_wrap(home: Path, *extra: str) -> tuple[int, str]:
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = wrap.main(["--home", str(home), "--launcher", "verimcp", *extra], "wrap")
    return code, out.getvalue()


def _run_unwrap(home: Path) -> str:
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        wrap.main(["--home", str(home)], "unwrap")
    return out.getvalue()


def test_wraps_stdio_and_token_http_and_skips_the_rest(tmp_path: Path):
    home = _home(tmp_path)
    _, output = _run_wrap(home)
    config = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    user = config["mcpServers"]
    log = str(home / ".verimcp" / "audit.jsonl")

    assert user["files"] == {"type": "stdio", "command": "verimcp", "env": {"A": "1"},
                             "args": ["--receipts", "--audit-log", log, "--", "npx", "-y", "some-files-server"]}

    remote = user["remote-with-token"]
    assert remote["type"] == "stdio" and remote["command"] == "verimcp"
    assert remote["args"] == ["--receipts", "--audit-log", log, "--backend-url", "https://example.com/mcp",
                              "--header-from-env", "Authorization=VERIMCP_HEADER_1"]
    assert remote["env"] == {"VERIMCP_HEADER_1": "Bearer tok-123"}
    assert "tok-123" not in json.dumps(remote["args"]), "a secret must never land in command-line args"

    assert user["remote-oauth"] == {"type": "http", "url": "https://oauth.example.com/mcp"}
    assert user["legacy-sse"]["type"] == "sse"
    assert config["projects"]["C:/work/app"]["mcpServers"]["already"]["args"] == ["--", "devmcp"]
    assert config["projects"]["C:/work/app"]["mcpServers"]["git"]["args"][-2:] == ["uvx", "mcp-server-git"]
    assert config["numStartups"] == 12 and config["projects"]["C:/work/app"]["allowedTools"] == []

    cursor = json.loads((home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))
    assert cursor["mcpServers"]["db"]["args"] == ["--receipts", "--audit-log", log, "--", "python", "db_server.py"]

    assert "OAuth" in output and "already wrapped" in output and "'sse' transport" in output
    assert "4 server(s) wrapped" in output
    assert list(home.glob(".claude.json.verimcp-backup-*")), "the original file must be backed up"


def test_running_wrap_twice_changes_nothing(tmp_path: Path):
    home = _home(tmp_path)
    _run_wrap(home)
    after_first = (home / ".claude.json").read_text(encoding="utf-8")
    _, output = _run_wrap(home)

    assert (home / ".claude.json").read_text(encoding="utf-8") == after_first
    assert "0 server(s) wrapped" in output


def test_dry_run_writes_nothing(tmp_path: Path):
    home = _home(tmp_path)
    before = (home / ".claude.json").read_text(encoding="utf-8")
    _, output = _run_wrap(home, "--dry-run")

    assert (home / ".claude.json").read_text(encoding="utf-8") == before
    assert not (home / ".verimcp").exists()
    assert "would wrapped" not in output and "Nothing was written" in output


def test_all_http_wraps_a_headerless_remote_server(tmp_path: Path):
    home = _home(tmp_path)
    _run_wrap(home, "--all-http")
    user = json.loads((home / ".claude.json").read_text(encoding="utf-8"))["mcpServers"]

    assert user["remote-oauth"]["args"][-2:] == ["--backend-url", "https://oauth.example.com/mcp"]


def test_unwrap_restores_the_originals_exactly(tmp_path: Path):
    home = _home(tmp_path)
    original_claude = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    original_cursor = json.loads((home / ".cursor" / "mcp.json").read_text(encoding="utf-8"))

    _run_wrap(home)
    output = _run_unwrap(home)

    assert json.loads((home / ".claude.json").read_text(encoding="utf-8")) == original_claude
    assert json.loads((home / ".cursor" / "mcp.json").read_text(encoding="utf-8")) == original_cursor
    assert "4 server(s) restored" in output
    assert "Nothing to unwrap" in _run_unwrap(home)


def test_unwrap_leaves_an_entry_the_user_edited_since(tmp_path: Path):
    home = _home(tmp_path)
    _run_wrap(home)
    config = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    config["mcpServers"]["files"]["args"].append("--my-own-flag")
    (home / ".claude.json").write_text(json.dumps(config), encoding="utf-8")

    output = _run_unwrap(home)
    after = json.loads((home / ".claude.json").read_text(encoding="utf-8"))

    assert after["mcpServers"]["files"]["args"][-1] == "--my-own-flag"
    assert "edited since wrap" in output


def _as_launchable(entry: dict) -> list[str]:
    """The wrapped entry's own command line, with `verimcp` swapped for this
    interpreter's module form so the test doesn't depend on PATH."""
    assert entry["command"] == "verimcp"
    return [sys.executable, "-m", "verimcp.cli", *entry["args"]]


async def _call_write_file(cmd: list[str], env: dict | None = None) -> dict:
    import os

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, env={**os.environ, **(env or {})},
    )

    async def send(message):
        proc.stdin.write((json.dumps(message) + "\n").encode())
        await proc.stdin.drain()

    async def response_to(request_id):
        while True:
            message = json.loads(await asyncio.wait_for(proc.stdout.readline(), timeout=15))
            if message.get("id") == request_id and "method" not in message:
                return message

    try:
        await send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "wrap-test", "version": "0"}}})
        await response_to(1)
        await send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        await send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                    "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hi"}}})
        return await response_to(2)
    finally:
        proc.terminate()
        await proc.wait()


async def test_a_wrapped_stdio_entry_really_catches_a_lie(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    (home / ".claude.json").write_text(json.dumps({"mcpServers": {"liar": {
        "type": "stdio", "command": sys.executable,
        "args": [str(LIAR), "--lie", "write_file_wrong_content", "--repo-path", str(repo)], "env": {},
    }}}), encoding="utf-8")

    _run_wrap(home)
    entry = json.loads((home / ".claude.json").read_text(encoding="utf-8"))["mcpServers"]["liar"]
    # the liar never negotiates roots, so tell verimcp where its files live
    cmd = _as_launchable(entry)
    cmd[cmd.index("--"):cmd.index("--")] = ["--root", str(repo)]

    response = await _call_write_file(cmd)

    assert response["result"]["isError"] is True
    assert "file does not exist" in response["result"]["content"][0]["text"]
    audit = (home / ".verimcp" / "audit.jsonl").read_text(encoding="utf-8")
    assert '"outcome": "verified_failed"' in audit


async def test_a_wrapped_http_entry_carries_its_token_from_env(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    with LyingHttpServer(required_token="s3cret") as server:
        (home / ".claude.json").write_text(json.dumps({"mcpServers": {"remote": {
            "type": "http", "url": server.url, "headers": {"Authorization": "Bearer s3cret"},
        }}}), encoding="utf-8")

        _run_wrap(home)
        entry = json.loads((home / ".claude.json").read_text(encoding="utf-8"))["mcpServers"]["remote"]
        cmd = _as_launchable(entry)
        cmd[1 + cmd.index("verimcp.cli"):1 + cmd.index("verimcp.cli")] = ["--root", str(tmp_path)]

        response = await _call_write_file(cmd, env=entry["env"])

    assert response["result"]["isError"] is True
    assert "file does not exist" in response["result"]["content"][0]["text"]
