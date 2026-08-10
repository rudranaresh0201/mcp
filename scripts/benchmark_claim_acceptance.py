"""A real, runnable benchmark: for each scenario in the Phase 6 adversarial
corpus (tests/fixtures/lying_server.py), run the *same* tools/call or
resources/read twice --

  RAW:     Host <--stdio--> lying_server directly, nothing in between.
  VERIMCP: Host <--stdio--> verimcp <--stdio--> lying_server.

...and record whether the fabricated claim was accepted (RAW) or caught
(VERIMCP). This mirrors the two-condition comparison structure of
"Prompts Don't Protect" (arxiv 2605.18414) -- their paper measures how often
a model's *behavior* violates a policy under prompt-only vs architectural
proxy enforcement (48-68% vs 0% Unauthorized Invocation Rate). Ours measures
how often a backend's *claim* is accepted at face value under no verification
vs verimcp's independent re-check. Same shape, different axis: theirs is
about what the model chooses to do, ours is about whether a tool's report of
what it did can be trusted.

RAW acceptance is expected to be 100% for every scenario here -- that's not
a bug in the benchmark, it's the actual finding: MCP has no built-in
mechanism to catch any of this, so an unguarded Host accepts all six lies
by construction. The number worth reporting is verimcp's catch rate.

Usage: python scripts/benchmark_claim_acceptance.py
"""
import asyncio
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_FIXTURE = Path(__file__).parent.parent / "tests" / "fixtures" / "lying_server.py"


@dataclass
class Scenario:
    id: str
    lie: str
    method: str  # "tools/call" or "resources/read"
    params: dict
    verifiers: str | None = None  # restrict verimcp's --verifiers, mirrors test_adversarial_corpus.py


CORPUS: list[Scenario] = [
    Scenario(
        id="write_file never touches disk",
        lie="write_file_wrong_content",
        method="tools/call",
        params={"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
    ),
    Scenario(
        id="git_commit fabricated hash",
        lie="git_commit_fake_hash",
        method="tools/call",
        params={"name": "git_commit", "arguments": {"message": "fake"}},
    ),
    Scenario(
        id="git_branch never created",
        lie="git_branch_fake_target",
        method="tools/call",
        params={"name": "git_branch", "arguments": {"name": "feature-that-does-not-exist"}},
    ),
    Scenario(
        id="run_ci_pipeline false pass",
        lie="ci_pipeline_false_pass",
        method="tools/call",
        params={
            "name": "run_ci_pipeline",
            "arguments": {
                "steps": [
                    {"name": "always-fails", "cmd": f"{sys.executable} -c \"import sys; sys.exit(1)\"", "idempotent": True}
                ]
            },
        },
    ),
    Scenario(
        id="repo://status fabricated",
        lie="resource_status_fabricated",
        method="resources/read",
        params={"uri": "repo://status"},
    ),
    Scenario(
        id="third-party-shaped commit fake hash",
        lie="git_server_commit_fake_hash",
        method="tools/call",
        params={"name": "git_commit", "arguments": {"message": "fake"}},
        verifiers="git_server_commit",
    ),
    Scenario(
        id="write_file real file, tampered content",
        lie="write_file_content_tampered",
        method="tools/call",
        params={"name": "write_file", "arguments": {"path": "tampered.txt", "content": "what the caller actually asked for"}},
    ),
    Scenario(
        id="git_commit reused real (old) hash",
        lie="git_commit_reused_real_hash",
        method="tools/call",
        params={"name": "git_commit", "arguments": {"message": "fake"}},
    ),
    Scenario(
        id="git_branch claims already-existing branch",
        lie="git_branch_already_existed",
        method="tools/call",
        params={"name": "git_branch", "arguments": {"name": "irrelevant-not-used-by-this-lie"}},
    ),
    Scenario(
        id="run_ci_pipeline false pass, non-idempotent",
        lie="ci_pipeline_false_pass_nonidempotent",
        method="tools/call",
        params={
            "name": "run_ci_pipeline",
            "arguments": {
                "steps": [
                    {"name": "always-fails", "cmd": f"{sys.executable} -c \"import sys; sys.exit(1)\""}
                ]
            },
        },
    ),
    Scenario(
        id="repo://log fabricated",
        lie="resource_log_fabricated",
        method="resources/read",
        params={"uri": "repo://log"},
    ),
    Scenario(
        id="repo://file/seed.txt fabricated",
        lie="resource_file_fabricated",
        method="resources/read",
        params={"uri": "repo://file/seed.txt"},
    ),
]


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "bench@example.com"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Bench"], cwd=repo, capture_output=True, check=True)
    (repo / "seed.txt").write_text("hi")
    subprocess.run(["git", "add", "seed.txt"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=repo, capture_output=True, check=True)


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


async def _issue_call(proc, scenario: Scenario) -> dict:
    await _send(proc, {"jsonrpc": "2.0", "id": 2, "method": scenario.method, "params": scenario.params})
    return await _recv(proc)


def _accepted_as_success(response: dict) -> bool:
    """True if the Host would see this as a success -- no error object for a
    resource read, isError:false for a tool call. This is deliberately the
    *only* thing a bare MCP client can check; it's exactly the gap verimcp
    closes."""
    if "error" in response:
        return False
    result = response.get("result", {})
    if "isError" in result:
        return not result["isError"]
    return True  # plain success shape, e.g. a resource read with no isError concept


async def _run_raw(repo_path: Path, scenario: Scenario) -> bool:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(_FIXTURE), "--lie", scenario.lie, "--repo-path", str(repo_path),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
    )
    try:
        await _initialize(proc)
        response = await _issue_call(proc, scenario)
        return _accepted_as_success(response)
    finally:
        proc.terminate()
        await proc.wait()


async def _run_verimcp(repo_path: Path, scenario: Scenario) -> bool:
    verimcp_args = ["--root", str(repo_path)]
    if scenario.verifiers is not None:
        verimcp_args += ["--verifiers", scenario.verifiers]
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "verimcp.cli", *verimcp_args, "--",
        sys.executable, str(_FIXTURE), "--lie", scenario.lie, "--repo-path", str(repo_path),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
    )
    try:
        await _initialize(proc)
        response = await _issue_call(proc, scenario)
        return not _accepted_as_success(response)  # "caught" == NOT accepted as success
    finally:
        proc.terminate()
        await proc.wait()


async def main() -> None:
    print(f"{'Scenario':<38} {'RAW accepted':<14} {'verimcp caught':<15}")
    print("-" * 67)

    raw_accepted = 0
    verimcp_caught = 0
    with tempfile.TemporaryDirectory() as tmp:
        repo_path = Path(tmp)
        _init_repo(repo_path)
        for scenario in CORPUS:
            accepted = await _run_raw(repo_path, scenario)
            caught = await _run_verimcp(repo_path, scenario)
            raw_accepted += accepted
            verimcp_caught += caught
            print(f"{scenario.id:<38} {accepted!s:<14} {caught!s:<15}")

    n = len(CORPUS)
    print("-" * 67)
    print(f"RAW claim-acceptance rate:     {raw_accepted}/{n}  ({100 * raw_accepted / n:.0f}%)")
    print(f"verimcp claim-catch rate:      {verimcp_caught}/{n}  ({100 * verimcp_caught / n:.0f}%)")
    print()
    print("RAW is 100% by construction -- MCP has no built-in mechanism to catch any of")
    print("this, so an unguarded Host accepts every fabrication. verimcp's rate is the real")
    print("number, and it is deliberately NOT 100%: 3 scenarios exploit documented, principled")
    print("gaps rather than bugs --")
    print("  - git_commit reused real (old) hash: GitCommitVerifier checks the hash EXISTS,")
    print("    not that THIS call created it.")
    print("  - git_branch already-existed: GitBranchVerifier checks end-state consistency,")
    print("    not whether anything actually changed.")
    print("  - run_ci_pipeline false pass, non-idempotent: CIRunVerifier only re-executes")
    print("    steps explicitly marked safe to re-run, by design (re-running an arbitrary")
    print("    shell step is not safe in general).")
    print()
    print("Parallel to arxiv 2605.18414 ('Prompts Don't Protect'): they show prompt-only")
    print("access control fails at 48-68.5% Unauthorized Invocation Rate, and architectural")
    print("proxy enforcement gets to 0%. Ours shows the same shape -- architectural")
    print("enforcement (verimcp) beats no enforcement (RAW) by a wide margin -- applied to")
    print("claim-trust instead of tool-access, with the honest remaining gap stated plainly")
    print("rather than hidden.")


if __name__ == "__main__":
    asyncio.run(main())
