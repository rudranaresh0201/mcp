"""Phase 5 (docs/ROADMAP.md): proves devmcp and verimcp-fronting-devmcp both
work against a real, independently-implemented MCP client, not just against
this project's own test suite -- and specifically that Phase 3's injected
`verimcp://audit` resources are visible to a client we didn't write.

Uses MCP Inspector's `--cli` mode (https://github.com/modelcontextprotocol/inspector),
the official client this exists to test against. Pinned to **v1.0.1**, not
`@latest` (currently 2.0.0): v2's CLI argument parsing has a reproducible bug
on this Windows setup (the same target-independent repro -- even a bare
`python --version` target -- crashes with a `NameError: name 'true' is not
defined` from what looks like an internal environment probe misfiring,
before any MCP connection is even attempted) that made it unusable here. v1
needs `--transport stdio` passed explicitly (auto-detection doesn't reliably
fire for a `python -m ...` target) -- both findings are documented in
docs/adr/0006-mcp-inspector-compatibility.md, including the real bug this
script's first live run against verimcp caught: docs/adr/0006 section
"resources/list id collision", fixed in proxy.py.

Run: python scripts/inspector_smoke_test.py
Requires: node/npx on PATH (network access on first run, to fetch the
Inspector package). Exits non-zero if any check fails.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

INSPECTOR_PKG = "@modelcontextprotocol/inspector@1.0.1"


def _run_inspector(target_cmd: list[str], method: str, extra_args: list[str] | None = None) -> dict:
    """One `inspector --cli` invocation, single method call, returns the
    parsed JSON result. stdout is pure JSON with this version (confirmed
    live -- the v1-deprecated banner prints to stderr, not stdout)."""
    cmd = [
        "npx", "--yes", INSPECTOR_PKG, "--cli", "--transport", "stdio",
        *target_cmd, "--method", method, *(extra_args or []),
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=60, shell=(sys.platform == "win32"),
        encoding="utf-8", errors="replace",  # npm's own banner text can include non-ASCII (emoji); we only need stdout's JSON
        check=False,  # non-zero exit is a real, expected failure mode here -- handled explicitly below
    )
    if result.returncode != 0:
        raise RuntimeError(f"inspector --cli failed (exit {result.returncode}):\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"inspector --cli produced non-JSON stdout: {result.stdout!r}") from e


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "smoke@test.local"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "smoke"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=path, check=True)


def main() -> int:
    checks: list[tuple[str, bool, str]] = []  # (name, passed, detail)

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        _init_repo(tmp)
        devmcp_cmd = [sys.executable, "-m", "devmcp.cli", "--repo-path", str(tmp)]

        # -- Baseline: devmcp directly, no proxy in front of it.
        baseline_tools = _run_inspector(devmcp_cmd, "tools/list")
        baseline_tool_names = {t["name"] for t in baseline_tools["tools"]}
        checks.append((
            "baseline tools/list has the expected 5 tools",
            baseline_tool_names == {"write_file", "git_commit", "git_branch", "run_ci_pipeline", "summarize_diff"},
            str(baseline_tool_names),
        ))

        baseline_resources = _run_inspector(devmcp_cmd, "resources/list")
        baseline_resource_uris = {r["uri"] for r in baseline_resources["resources"]}
        checks.append((
            "baseline resources/list has devmcp's 3 resources",
            baseline_resource_uris == {"repo://status", "repo://log", "ci://last-run"},
            str(baseline_resource_uris),
        ))

        # -- Through verimcp: same checks, plus proof the proxy adds value
        # without breaking anything a real client depends on.
        audit_log = tmp / "audit.jsonl"
        verimcp_cmd = [
            sys.executable, "-m", "verimcp.cli", "--audit-log", str(audit_log), "--",
            *devmcp_cmd,
        ]

        proxied_tools = _run_inspector(verimcp_cmd, "tools/list")
        proxied_tool_names = {t["name"] for t in proxied_tools["tools"]}
        checks.append((
            "proxied tools/list still has every baseline tool",
            baseline_tool_names <= proxied_tool_names,
            str(proxied_tool_names),
        ))

        write_result = _run_inspector(
            verimcp_cmd, "tools/call",
            ["--tool-name", "write_file", "--tool-arg", "path=smoke.txt", "--tool-arg", "content=inspector-smoke-test"],
        )
        on_disk = (tmp / "smoke.txt")
        checks.append((
            "tools/call through verimcp produces a real file on disk (not just a claimed success)",
            write_result.get("isError") is False and on_disk.exists() and on_disk.read_text() == "inspector-smoke-test",
            f"isError={write_result.get('isError')} on_disk_content={on_disk.read_text() if on_disk.exists() else None!r}",
        ))

        proxied_resources = _run_inspector(verimcp_cmd, "resources/list")
        proxied_resource_uris = {r["uri"] for r in proxied_resources["resources"]}
        expected = baseline_resource_uris | {"verimcp://audit", "verimcp://audit/current"}
        checks.append((
            "proxied resources/list merges devmcp's resources with verimcp's audit resources (Phase 3, real client)",
            proxied_resource_uris == expected,
            f"got {proxied_resource_uris}, expected {expected}",
        ))

        # verimcp://audit (all sessions), not .../current: each Inspector
        # --cli call is a fresh one-shot process, so the write_file call
        # above and this resources/read are two different verimcp sessions
        # against the same --audit-log file -- exactly the case
        # verimcp://audit (vs. the session-scoped /current) exists for.
        audit_read = _run_inspector(verimcp_cmd, "resources/read", ["--uri", "verimcp://audit"])
        audit_text = audit_read["contents"][0]["text"]
        audit_entries = [json.loads(line) for line in audit_text.splitlines() if line.strip()]
        checks.append((
            "resources/read on verimcp://audit returns the real recorded write_file call from an earlier session (Phase 3, real client)",
            any(e.get("target") == "write_file" and e.get("arguments", {}).get("path") == "smoke.txt" for e in audit_entries),
            f"{len(audit_entries)} entries: {audit_entries}",
        ))

    print(f"\n{'='*70}\nMCP Inspector smoke test ({INSPECTOR_PKG}, --transport stdio)\n{'='*70}")
    all_passed = True
    for name, passed, detail in checks:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {name}")
        if not passed:
            print(f"       {detail}")
            all_passed = False

    print(f"\n{sum(1 for _, p, _ in checks if p)}/{len(checks)} checks passed.")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
