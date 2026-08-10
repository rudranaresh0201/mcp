"""A real, runnable benchmark for the verify-before-retry adaptation
(idempotency.py, arxiv 2608.02645 "Verified Tool Calls Improve LLM Agent
Reliability Under Non-Atomic Failures").

For each scenario below, simulate a client retrying a run_ci_pipeline call
after an ambiguous response (their paper's own trigger: a timeout after
dispatch, or delayed visibility into whether the first attempt actually
landed) by literally sending the identical tools/call twice --

  RAW:     Host <--stdio--> devmcp directly, nothing in between.
  VERIMCP: Host <--stdio--> verimcp --idempotent-replay <--stdio--> devmcp.

...and record whether the underlying side effect happened once or twice.
This mirrors the paper's headline metric, duplicate action rate, applied to
a real MCP backend instead of their simulated tool environment.

Scope note, found while building this: not every devmcp tool is a fair test
here. git_commit and git_branch already have *their own* built-in
idempotency at the git-porcelain level -- a truly identical retry (same
files, same message, nothing changed in between) fails safely with "nothing
to commit" / "branch already exists" rather than duplicating, with or
without verimcp in front. That's a real, honest finding, not a gap in the
benchmark: verify-before-retry's practical value is for tools with NO
built-in idempotency of their own -- exactly what run_ci_pipeline's
arbitrary shell steps are, and exactly the shape of the paper's own example
tasks (activate_customer, record_invoice: ordinary side-effecting API calls,
not tools with a leaf/no-op safety net). All scenarios below are therefore
run_ci_pipeline-shaped, standing in for "an arbitrary non-idempotent tool
call" in the same way the paper's simulated tasks did.

Usage: python scripts/benchmark_retry_duplication.py
"""
import asyncio
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Scenario:
    id: str
    step_name: str
    cmd_template: str  # {target} substituted with a path inside the scenario's own tmp dir


CORPUS: list[Scenario] = [
    Scenario(
        id="bump a counter (generic side effect)",
        step_name="bump-counter",
        cmd_template='{python} -c "open(r\'{target}\', \'a\').write(chr(120))"',
    ),
    Scenario(
        id="append a sent-notification record",
        step_name="send-notification",
        cmd_template='{python} -c "open(r\'{target}\', \'a\').write(\'notified user\\n\')"',
    ),
    Scenario(
        id="increment a JSON invoice counter",
        step_name="record-invoice",
        cmd_template=(
            '{python} -c "'
            "import json,pathlib;"
            "p=pathlib.Path(r'{target}');"
            "d=json.loads(p.read_text()) if p.exists() else {{'count': 0}};"
            "d['count']+=1;"
            "p.write_text(json.dumps(d))\""
        ),
    ),
    Scenario(
        id="append an audit-log entry",
        step_name="write-audit-entry",
        cmd_template='{python} -c "open(r\'{target}\', \'a\').write(\'entry\\n\')"',
    ),
    Scenario(
        id="two-step pipeline, one side-effecting step",
        step_name="bump-counter",
        cmd_template='{python} -c "open(r\'{target}\', \'a\').write(chr(120))"',
    ),
]


async def _send(proc, message: dict) -> None:
    proc.stdin.write((json.dumps(message) + "\n").encode())
    await proc.stdin.drain()


async def _recv(proc) -> dict:
    line = await proc.stdout.readline()
    return json.loads(line)


async def _initialize(proc) -> None:
    await _send(proc, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "benchmark", "version": "0"},
        },
    })
    await _recv(proc)
    await _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})


def _steps_for(scenario: Scenario, target: Path) -> list[dict]:
    step = {"name": scenario.step_name, "cmd": scenario.cmd_template.format(python=sys.executable, target=target)}
    if "two-step" in scenario.id:
        return [{"name": "no-op", "cmd": f'{sys.executable} -c "pass"'}, step]
    return [step]


async def _answer_roots_if_asked(proc, repo_path: Path) -> None:
    """devmcp asks for roots/list right after initialize -- read and answer
    it before sending the tool call, same handshake every other test/script
    in this repo performs."""
    roots_request = await _recv(proc)
    await _send(proc, {
        "jsonrpc": "2.0", "id": roots_request["id"],
        "result": {"roots": [{"uri": repo_path.as_uri(), "name": "repo"}]},
    })


async def _run_raw(repo_path: Path, scenario: Scenario) -> bool:
    """Returns True if the side effect was duplicated."""
    target = repo_path / "raw_target.txt"
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "devmcp.cli", "--repo-path", str(repo_path),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
    )
    try:
        await _initialize(proc)
        await _answer_roots_if_asked(proc, repo_path)
        params = {"name": "run_ci_pipeline", "arguments": {"steps": _steps_for(scenario, target)}}
        await _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": params})
        await _recv(proc)
        state_after_first = target.read_text() if target.exists() else ""

        # Client retries after an ambiguous response -- identical request, new id.
        await _send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": params})
        await _recv(proc)
        state_after_retry = target.read_text() if target.exists() else ""

        return state_after_retry != state_after_first and state_after_first != ""
    finally:
        proc.terminate()
        await proc.wait()


async def _run_verimcp(repo_path: Path, scenario: Scenario) -> bool:
    """Returns True if the side effect was duplicated."""
    target = repo_path / "verimcp_target.txt"
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "verimcp.cli", "--idempotent-replay", "--",
        sys.executable, "-m", "devmcp.cli", "--repo-path", str(repo_path),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
    )
    try:
        await _initialize(proc)
        await _answer_roots_if_asked(proc, repo_path)
        params = {"name": "run_ci_pipeline", "arguments": {"steps": _steps_for(scenario, target)}}
        await _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": params})
        await _recv(proc)
        state_after_first = target.read_text() if target.exists() else ""

        await _send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": params})
        await _recv(proc)
        state_after_retry = target.read_text() if target.exists() else ""

        return state_after_retry != state_after_first and state_after_first != ""
    finally:
        proc.terminate()
        await proc.wait()


async def main() -> None:
    print(f"{'Scenario':<42} {'RAW duplicated':<16} {'verimcp duplicated':<18}")
    print("-" * 78)

    raw_dup = 0
    verimcp_dup = 0
    for scenario in CORPUS:
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            dup_raw = await _run_raw(repo_path, scenario)
        with tempfile.TemporaryDirectory() as tmp:
            repo_path = Path(tmp)
            dup_verimcp = await _run_verimcp(repo_path, scenario)

        raw_dup += dup_raw
        verimcp_dup += dup_verimcp
        print(f"{scenario.id:<42} {dup_raw!s:<16} {dup_verimcp!s:<18}")

    n = len(CORPUS)
    print("-" * 78)
    print(f"RAW duplicate-action rate:      {raw_dup}/{n}  ({100 * raw_dup / n:.0f}%)")
    print(f"verimcp duplicate-action rate:  {verimcp_dup}/{n}  ({100 * verimcp_dup / n:.0f}%)")
    print()
    print("RAW is 100% by construction here -- an unguarded MCP Host has no way to know")
    print("a retried tools/call is a retry at all, so devmcp (an honest, non-lying backend)")
    print("just does what it's told a second time. verimcp's rate is the real number.")
    print()
    print("Parallel to arxiv 2608.02645 ('Verified Tool Calls Improve LLM Agent Reliability")
    print("Under Non-Atomic Failures'): they wrap the agent's own tool-calling loop with")
    print("postcondition verification + verify-before-retry + idempotency keys, measured")
    print("inside a simulated tool environment (36pp task-success improvement, duplicate")
    print("actions cut from up to 72% to at most 20% under injected non-atomic failures).")
    print("This adapts the same verify-before-retry idea to the proxy layer instead of the")
    print("agent's own wrapper -- framework-agnostic, catches a retry from *any* Host talking")
    print("to *any* backend verimcp fronts, reusing verimcp's existing postcondition")
    print("verifiers (built for Phase 6's adversarial corpus) as the 'did this already")
    print("succeed' check instead of inventing a new one.")


if __name__ == "__main__":
    asyncio.run(main())
