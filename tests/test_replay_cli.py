"""`verimcp replay` as a real subprocess -- proves the argv[1]=="replay"
branch in cli.py actually dispatches correctly and doesn't collide with the
main `verimcp -- <backend>` invocation shape."""
import asyncio
import json
import sys
from pathlib import Path


async def _run_replay(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "verimcp.cli", "replay", *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, (stdout + stderr).decode()


def _write_audit_log(path: Path, entries: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


async def test_replay_cli_reports_a_verdict_change(tmp_path: Path):
    audit_path = tmp_path / "audit.jsonl"
    _write_audit_log(audit_path, [
        {"session_id": "s1", "seq": 1, "method": "tools/call", "target": "write_file",
         "arguments": {"path": "a.txt"}, "outcome": "forwarded", "gate": None},
    ])
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("rules:\n  - tool: write_file\n    action: deny\n")

    returncode, output = await _run_replay(str(audit_path), "--policy-config", str(policy_path))

    assert returncode == 0
    assert "write_file" in output
    assert "allow -> deny" in output


async def test_replay_cli_reports_no_changes(tmp_path: Path):
    audit_path = tmp_path / "audit.jsonl"
    _write_audit_log(audit_path, [
        {"session_id": "s1", "seq": 1, "method": "tools/call", "target": "write_file",
         "arguments": {"path": "a.txt"}, "outcome": "forwarded", "gate": None},
    ])
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("rules: []\n")

    returncode, output = await _run_replay(str(audit_path), "--policy-config", str(policy_path))

    assert returncode == 0
    assert "No verdict changes" in output


async def test_replay_cli_filters_by_session(tmp_path: Path):
    audit_path = tmp_path / "audit.jsonl"
    _write_audit_log(audit_path, [
        {"session_id": "s1", "seq": 1, "method": "tools/call", "target": "write_file",
         "arguments": {}, "outcome": "forwarded", "gate": None},
        {"session_id": "s2", "seq": 1, "method": "tools/call", "target": "run_ci_pipeline",
         "arguments": {}, "outcome": "forwarded", "gate": None},
    ])
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("rules:\n  - tool: run_ci_pipeline\n    action: deny\n")

    returncode, output = await _run_replay(str(audit_path), "--policy-config", str(policy_path), "--session", "s1")

    assert returncode == 0
    assert "No verdict changes" in output  # s2's entry (the one that would change) was filtered out


async def test_replay_cli_errors_on_missing_audit_log(tmp_path: Path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("rules: []\n")

    returncode, output = await _run_replay(str(tmp_path / "does-not-exist.jsonl"), "--policy-config", str(policy_path))

    assert returncode != 0
    assert "not found" in output
