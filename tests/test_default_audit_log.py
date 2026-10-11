"""`verimcp wrap` and `verimcp-dashboard` must agree on where verdicts go.

Found by running the real user path (2026-10-11): wrap pointed every server at
~/.verimcp/audit.jsonl, while a bare `verimcp-dashboard` read a relative
`verimcp-audit.jsonl` in the current directory -- in that session an old, stale
log, so the console showed past events as if they were live.
"""
import json
import sys
import types
from pathlib import Path

from verimcp import wrap as wrap_module
from verimcp.audit import default_log_path
from verimcp.dashboard import server


def test_default_is_under_the_users_home():
    assert default_log_path(Path("/h")) == Path("/h/.verimcp/audit.jsonl")


def test_dashboard_with_no_arguments_reads_the_file_wrap_sets_up(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(server, "create_app", lambda log, replay_delay=None: seen.setdefault("log", log))
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=lambda *a, **k: None))

    assert server.main([]) == 0
    assert seen["log"] == default_log_path()
    out = capsys.readouterr().out
    assert str(default_log_path()) in out


def test_wrap_points_servers_at_that_same_file(tmp_path, capsys):
    (tmp_path / ".claude.json").write_text(json.dumps({"mcpServers": {"files": {"command": "some-server", "args": []}}}))
    assert wrap_module.main(["--home", str(tmp_path), "--launcher", "verimcp"], "wrap") == 0

    wrapped = json.loads((tmp_path / ".claude.json").read_text())["mcpServers"]["files"]
    written = wrapped["args"][wrapped["args"].index("--audit-log") + 1]
    assert Path(written) == default_log_path(tmp_path)


def test_wrap_summary_warns_about_apps_left_open(tmp_path, capsys):
    (tmp_path / ".claude.json").write_text(json.dumps({"mcpServers": {"files": {"command": "some-server", "args": []}}}))
    wrap_module.main(["--home", str(tmp_path), "--launcher", "verimcp"], "wrap")
    out = capsys.readouterr().out
    assert "Quit and reopen" in out and "--dry-run" in out
