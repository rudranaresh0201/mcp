"""Spawns the real `verimcp` command fronting a real `devmcp` subprocess,
driven over real stdio -- the same shape devmcp/tests/test_server_integration.py
uses, one layer deeper. No mocks anywhere in this chain."""
import asyncio
import json
import sys
from pathlib import Path


async def _send(proc, message: dict) -> None:
    proc.stdin.write((json.dumps(message) + "\n").encode())
    await proc.stdin.drain()


async def _recv(proc) -> dict:
    line = await proc.stdout.readline()
    return json.loads(line)


async def _spawn_verimcp(repo_path: Path, verimcp_args: list[str] | None = None):
    return await asyncio.create_subprocess_exec(
        sys.executable, "-m", "verimcp.cli", *(verimcp_args or []), "--",
        sys.executable, "-m", "devmcp.cli", "--repo-path", str(repo_path),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )


async def _initialize(proc, capabilities: dict | None = None) -> None:
    await _send(proc, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": capabilities or {},
            "clientInfo": {"name": "test-client", "version": "0"},
        },
    })
    await _recv(proc)  # initialize response, unused here
    await _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})


async def test_verified_pass_through_when_host_answers_roots(tmp_path: Path):
    """Host answers devmcp's roots/list -- verimcp learns the real root and
    can independently confirm the write actually happened."""
    proc = await _spawn_verimcp(tmp_path)
    try:
        await _initialize(proc)

        roots_request = await _recv(proc)
        assert roots_request["method"] == "roots/list"
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        response = await _recv(proc)

        assert response["result"]["isError"] is False
        assert (tmp_path / "notes.txt").read_text() == "hello"
    finally:
        proc.terminate()
        await proc.wait()


async def test_git_commit_verified_over_real_pipe(tmp_path: Path):
    """Regression test for a real deadlock: GitCommitVerifier spawns its own
    `git cat-file` subprocess to check the claimed hash. On Windows, without
    stdin=DEVNULL, that child inherits verimcp's real stdin -- which is
    simultaneously being read on a background thread by Proxy's own
    _StdinReader -- and the two deadlock forever. A verifier that calls
    subprocess directly can only be caught by a test that runs the real
    pipe, not by calling verify() as a plain function."""
    proc = await _spawn_verimcp(tmp_path)
    try:
        await _initialize(proc)
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "a.txt", "content": "hello"}},
        })
        await _recv(proc)

        await _send(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "git_commit", "arguments": {"message": "add a.txt", "files": ["a.txt"]}},
        })
        response = await _recv(proc)

        assert response["result"]["isError"] is False
    finally:
        proc.terminate()
        await proc.wait()


def _counter_step(counter_path: Path) -> dict:
    # Appends one byte to a real file every time it actually runs -- a shell
    # step with no built-in idempotency of its own (unlike `git commit`,
    # which safely no-ops on a truly-unchanged retry, making it a bad choice
    # for this test), so a second real execution is unambiguous evidence of
    # a duplicate side effect. Not marked idempotent: true -- that flag
    # controls CIRunVerifier's own re-execution-to-verify pass, a separate
    # concern from this test, and marking it would make the verifier itself
    # append a byte too.
    return {
        "name": "bump-counter",
        "cmd": f'{sys.executable} -c "open(r\'{counter_path}\', \'a\').write(chr(120))"',
    }


async def test_idempotent_replay_prevents_duplicate_execution(tmp_path: Path):
    """The core verify-before-retry claim (arxiv 2608.02645 adaptation): once
    verimcp has independently verified a run_ci_pipeline call's claims are
    self-consistent, a byte-identical retry (e.g. a Host resending after a
    timeout it misread as failure) must be answered from that confirmed
    result, not re-executed -- proven the strong way, by checking the
    counter file the backend actually touches, not just that the second
    response looks like a success."""
    counter = tmp_path / "counter.txt"
    proc = await _spawn_verimcp(tmp_path, ["--idempotent-replay"])
    try:
        await _initialize(proc)
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        ci_params = {"name": "run_ci_pipeline", "arguments": {"steps": [_counter_step(counter)]}}
        await _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": ci_params})
        first = await _recv(proc)
        assert first["result"]["isError"] is False
        assert counter.read_text() == "x"  # backend really ran the step once

        # Retry: same tool, same arguments, new request id -- exactly what a
        # client resending after an ambiguous response looks like over the wire.
        await _send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": ci_params})
        second = await _recv(proc)

        assert second["id"] == 3
        assert second["result"]["isError"] is False
        assert counter.read_text() == "x"  # NOT "xx" -- the retry never reached the backend
    finally:
        proc.terminate()
        await proc.wait()


async def test_without_idempotent_replay_retry_executes_twice(tmp_path: Path):
    """Baseline/control for the test above: with the flag omitted (existing,
    unchanged behavior), the identical retry IS re-forwarded and DOES run the
    step again -- the actual duplicate-action problem verify-before-retry
    exists to prevent, proven to really occur without it."""
    counter = tmp_path / "counter.txt"
    proc = await _spawn_verimcp(tmp_path)  # no --idempotent-replay
    try:
        await _initialize(proc)
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        ci_params = {"name": "run_ci_pipeline", "arguments": {"steps": [_counter_step(counter)]}}
        await _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": ci_params})
        first = await _recv(proc)
        assert first["result"]["isError"] is False
        assert counter.read_text() == "x"

        await _send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": ci_params})
        second = await _recv(proc)
        assert second["result"]["isError"] is False

        assert counter.read_text() == "xx"  # a real second execution
    finally:
        proc.terminate()
        await proc.wait()


async def test_ci_run_verified_over_real_pipe(tmp_path: Path):
    """CIRunVerifier re-executes idempotent steps by shelling out itself --
    same subprocess-over-stdio deadlock risk as GitCommitVerifier above, so
    this needs the real pipe, not a hand-built response dict, to actually
    prove it doesn't hang."""
    proc = await _spawn_verimcp(tmp_path)
    try:
        await _initialize(proc)
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {
                "name": "run_ci_pipeline",
                "arguments": {"steps": [{"name": "ok", "cmd": 'python -c "import sys; sys.exit(0)"', "idempotent": True}]},
            },
        })
        response = await _recv(proc)

        assert response["result"]["isError"] is False
    finally:
        proc.terminate()
        await proc.wait()


async def test_resource_read_verified_over_real_pipe(tmp_path: Path):
    """resources/read is the first non-tools/call message type the proxy
    dispatches to a verifier -- this exercises that new branch in
    proxy.py's _forward_backend_to_host, not just ResourceReadVerifier in
    isolation."""
    proc = await _spawn_verimcp(tmp_path)
    try:
        await _initialize(proc)
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        await _recv(proc)

        await _send(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "resources/read",
            "params": {"uri": "repo://file/notes.txt"},
        })
        response = await _recv(proc)

        assert "error" not in response
        assert response["result"]["contents"][0]["text"] == "hello"
    finally:
        proc.terminate()
        await proc.wait()


async def test_fails_open_when_host_never_answers_roots(tmp_path: Path):
    """The bug this whole test file exists to pin down: if the Host never
    answers roots/list (plenty of real Hosts don't implement roots at all),
    verimcp must not falsely fail a write it can't independently locate --
    it should pass the backend's real, correct response through untouched."""
    proc = await _spawn_verimcp(tmp_path)
    try:
        await _initialize(proc)
        # Deliberately don't answer the roots/list request that arrives here --
        # devmcp falls back to its own --repo-path, same as many real Hosts would leave it.

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        response = await _recv(proc)
        while response.get("method") == "roots/list":
            response = await _recv(proc)  # skip past the unanswered roots/list if it arrives after

        assert response["id"] == 2
        assert response["result"]["isError"] is False
        assert (tmp_path / "notes.txt").read_text() == "hello"
    finally:
        proc.terminate()
        await proc.wait()


async def test_policy_gate_denies_a_tool_call_before_it_ever_reaches_the_backend(tmp_path: Path):
    """The mirror case of the sampling gate: tools/call is Host-originated,
    so the policy check runs in _forward_host_to_backend, and a denial is
    answered straight to the Host without the request ever reaching the
    backend at all. Proven the strong way -- not just that the response
    looks like a denial, but that write_file's real side effect (the file
    on disk) never happened, meaning devmcp genuinely never ran the tool."""
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("rules:\n  - tool: write_file\n    action: deny\n")

    proc = await _spawn_verimcp(tmp_path, verimcp_args=["--policy-config", str(policy_file)])
    try:
        await _initialize(proc)
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        response = await _recv(proc)

        assert response["id"] == 2
        assert "error" in response
        assert "denied by policy" in response["error"]["message"]
        assert not (tmp_path / "notes.txt").exists()
    finally:
        proc.terminate()
        await proc.wait()


async def test_policy_gate_allows_a_tool_with_no_matching_rule(tmp_path: Path):
    """A --policy-config that only mentions other tools must not turn into
    an accidental default-deny for everything else."""
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("rules:\n  - tool: run_ci_pipeline\n    action: deny\n")

    proc = await _spawn_verimcp(tmp_path, verimcp_args=["--policy-config", str(policy_file)])
    try:
        await _initialize(proc)
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        response = await _recv(proc)

        assert response["result"]["isError"] is False
        assert (tmp_path / "notes.txt").read_text() == "hello"
    finally:
        proc.terminate()
        await proc.wait()


async def test_approval_gate_forwards_tool_call_after_host_accepts(tmp_path: Path):
    """require_approval pauses the tools/call, sends a real elicitation/create
    to the Host, and only forwards to the backend once the Host accepts --
    proven the strong way, same as the deny tests: the file genuinely gets
    written, not just a response that claims success."""
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("rules:\n  - tool: write_file\n    action: require_approval\n")

    proc = await _spawn_verimcp(tmp_path, verimcp_args=["--policy-config", str(policy_file)])
    try:
        await _initialize(proc, capabilities={"elicitation": {}})
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        elicitation = await _recv(proc)
        assert elicitation["method"] == "elicitation/create"
        assert "write_file" in elicitation["params"]["message"]

        await _send(proc, {"jsonrpc": "2.0", "id": elicitation["id"], "result": {"action": "accept"}})
        response = await _recv(proc)

        assert response["id"] == 2
        assert response["result"]["isError"] is False
        assert (tmp_path / "notes.txt").read_text() == "hello"
    finally:
        proc.terminate()
        await proc.wait()


async def test_approval_gate_denies_without_running_when_host_declines(tmp_path: Path):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("rules:\n  - tool: write_file\n    action: require_approval\n")

    proc = await _spawn_verimcp(tmp_path, verimcp_args=["--policy-config", str(policy_file)])
    try:
        await _initialize(proc, capabilities={"elicitation": {}})
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        elicitation = await _recv(proc)
        assert elicitation["method"] == "elicitation/create"

        await _send(proc, {"jsonrpc": "2.0", "id": elicitation["id"], "result": {"action": "decline"}})
        response = await _recv(proc)

        assert response["id"] == 2
        assert "error" in response
        assert "decline" in response["error"]["message"]
        assert not (tmp_path / "notes.txt").exists()
    finally:
        proc.terminate()
        await proc.wait()


async def test_approval_gate_denies_when_host_cancels(tmp_path: Path):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("rules:\n  - tool: write_file\n    action: require_approval\n")

    proc = await _spawn_verimcp(tmp_path, verimcp_args=["--policy-config", str(policy_file)])
    try:
        await _initialize(proc, capabilities={"elicitation": {}})
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        elicitation = await _recv(proc)

        await _send(proc, {"jsonrpc": "2.0", "id": elicitation["id"], "result": {"action": "cancel"}})
        response = await _recv(proc)

        assert response["id"] == 2
        assert "error" in response
        assert "cancel" in response["error"]["message"]
        assert not (tmp_path / "notes.txt").exists()
    finally:
        proc.terminate()
        await proc.wait()


async def test_approval_gate_fails_closed_when_host_lacks_elicitation_capability(tmp_path: Path):
    """A Host that never declared the elicitation capability must never even
    receive an elicitation/create it can't answer -- verimcp denies
    immediately instead of sending a request into the void."""
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("rules:\n  - tool: write_file\n    action: require_approval\n")

    proc = await _spawn_verimcp(tmp_path, verimcp_args=["--policy-config", str(policy_file)])
    try:
        await _initialize(proc)  # no elicitation capability declared
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        response = await _recv(proc)

        assert response["id"] == 2
        assert "error" in response
        assert "elicitation" in response["error"]["message"]
        assert not (tmp_path / "notes.txt").exists()
    finally:
        proc.terminate()
        await proc.wait()


async def test_approval_gate_fails_closed_on_timeout(tmp_path: Path):
    """First timing-sensitive test in this suite. Margin kept generous
    (0.3s) relative to plausible CI scheduling jitter, and the eventual
    denial recv relies on pytest's global 30s timeout as the outer safety
    net so a genuinely broken feature fails the test instead of hanging."""
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("rules:\n  - tool: write_file\n    action: require_approval\n")

    proc = await _spawn_verimcp(
        tmp_path, verimcp_args=["--policy-config", str(policy_file), "--approval-timeout", "0.3"]
    )
    try:
        await _initialize(proc, capabilities={"elicitation": {}})
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        elicitation = await _recv(proc)
        assert elicitation["method"] == "elicitation/create"
        # deliberately never answered

        response = await _recv(proc)

        assert response["id"] == 2
        assert "error" in response
        assert "timed out" in response["error"]["message"]
        assert not (tmp_path / "notes.txt").exists()
    finally:
        proc.terminate()
        await proc.wait()


async def test_sampling_gate_denies_backend_once_rate_limit_exceeded(tmp_path: Path):
    """sampling/createMessage is the first *backend-initiated* request a
    RequestGate acts on -- unlike a Verifier, it's checked before the Host
    ever sees it. First summarize_diff call goes through and the Host
    answers it for real; the second is denied by verimcp itself, so the
    Host never receives a second sampling/createMessage at all, and devmcp's
    own exception handling turns the denial into a normal isError result."""
    proc = await _spawn_verimcp(tmp_path, verimcp_args=["--sampling-limit", "1", "--sampling-window", "60"])
    try:
        await _initialize(proc)
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "summarize_diff", "arguments": {"diff": "+ added a line"}},
        })
        sampling_request = await _recv(proc)
        assert sampling_request["method"] == "sampling/createMessage"
        await _send(proc, {
            "jsonrpc": "2.0", "id": sampling_request["id"],
            "result": {"content": {"type": "text", "text": "Adds a line."}},
        })
        first_response = await _recv(proc)
        assert first_response["result"]["isError"] is False
        assert first_response["result"]["content"][0]["text"] == "Adds a line."

        await _send(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "summarize_diff", "arguments": {"diff": "+ added another line"}},
        })
        second_response = await _recv(proc)

        assert second_response["id"] == 3
        assert second_response["result"]["isError"] is True
        assert "rate limit" in second_response["result"]["content"][0]["text"]
    finally:
        proc.terminate()
        await proc.wait()


async def test_audit_log_records_and_exposes_a_forwarded_tool_call(tmp_path: Path):
    """Phase 3: a completed tools/call gets recorded on disk *and* is
    readable back through the real MCP resources/list + resources/read flow
    -- not just written to a file nobody's proven the Host can actually see."""
    audit_path = tmp_path / "audit.jsonl"
    proc = await _spawn_verimcp(tmp_path, verimcp_args=["--audit-log", str(audit_path)])
    try:
        await _initialize(proc)
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        response = await _recv(proc)
        assert response["result"]["isError"] is False

        await _send(proc, {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}})
        listing = await _recv(proc)
        uris = {r["uri"] for r in listing["result"]["resources"]}
        assert "verimcp://audit" in uris
        assert "verimcp://audit/current" in uris

        await _send(proc, {
            "jsonrpc": "2.0", "id": 4, "method": "resources/read",
            "params": {"uri": "verimcp://audit/current"},
        })
        read_response = await _recv(proc)
        lines = [json.loads(line) for line in read_response["result"]["contents"][0]["text"].splitlines() if line.strip()]
        assert len(lines) == 1
        assert lines[0]["method"] == "tools/call"
        assert lines[0]["target"] == "write_file"
        assert lines[0]["arguments"] == {"path": "notes.txt", "content": "hello"}
        assert lines[0]["outcome"] == "verified_ok"

        on_disk = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
        assert len(on_disk) == 1
    finally:
        proc.terminate()
        await proc.wait()


async def test_audit_log_records_a_policy_denied_tool_call(tmp_path: Path):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("rules:\n  - tool: write_file\n    action: deny\n")
    audit_path = tmp_path / "audit.jsonl"

    proc = await _spawn_verimcp(
        tmp_path, verimcp_args=["--policy-config", str(policy_file), "--audit-log", str(audit_path)]
    )
    try:
        await _initialize(proc)
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        response = await _recv(proc)
        assert "error" in response

        entries = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
        assert len(entries) == 1
        assert entries[0]["outcome"] == "denied"
        assert entries[0]["gate"]["action"] == "deny"
        assert "denied by policy" in entries[0]["gate"]["reason"]
    finally:
        proc.terminate()
        await proc.wait()


async def test_audit_log_records_approval_accept(tmp_path: Path):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("rules:\n  - tool: write_file\n    action: require_approval\n")
    audit_path = tmp_path / "audit.jsonl"

    proc = await _spawn_verimcp(
        tmp_path, verimcp_args=["--policy-config", str(policy_file), "--audit-log", str(audit_path)]
    )
    try:
        await _initialize(proc, capabilities={"elicitation": {}})
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        elicitation = await _recv(proc)
        await _send(proc, {"jsonrpc": "2.0", "id": elicitation["id"], "result": {"action": "accept"}})
        response = await _recv(proc)
        assert response["result"]["isError"] is False

        entries = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
        assert len(entries) == 1
        assert entries[0]["outcome"] == "verified_ok"
        assert entries[0]["gate"] == {"action": "require_approval", "reason": "approved by Host"}
        assert entries[0]["approval"] == {"status": "accept"}
    finally:
        proc.terminate()
        await proc.wait()


async def test_audit_log_records_approval_decline(tmp_path: Path):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("rules:\n  - tool: write_file\n    action: require_approval\n")
    audit_path = tmp_path / "audit.jsonl"

    proc = await _spawn_verimcp(
        tmp_path, verimcp_args=["--policy-config", str(policy_file), "--audit-log", str(audit_path)]
    )
    try:
        await _initialize(proc, capabilities={"elicitation": {}})
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })
        elicitation = await _recv(proc)
        await _send(proc, {"jsonrpc": "2.0", "id": elicitation["id"], "result": {"action": "decline"}})
        response = await _recv(proc)
        assert "error" in response

        entries = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
        assert len(entries) == 1
        assert entries[0]["outcome"] == "denied"
        assert entries[0]["approval"] == {"status": "decline"}
    finally:
        proc.terminate()
        await proc.wait()


async def test_audit_subscribe_fires_update_notification_after_a_tool_call(tmp_path: Path):
    """resources/subscribe on verimcp://audit/current, answered locally
    (never forwarded to devmcp), then a real notifications/resources/updated
    after the next recorded call -- proven over the real pipe, not just that
    the subscriber set gets mutated."""
    audit_path = tmp_path / "audit.jsonl"
    proc = await _spawn_verimcp(tmp_path, verimcp_args=["--audit-log", str(audit_path)])
    try:
        await _initialize(proc)
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "resources/subscribe",
            "params": {"uri": "verimcp://audit/current"},
        })
        sub_response = await _recv(proc)
        assert sub_response["result"] == {}

        await _send(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "write_file", "arguments": {"path": "notes.txt", "content": "hello"}},
        })

        notification = await _recv(proc)
        assert notification["method"] == "notifications/resources/updated"
        assert notification["params"]["uri"] == "verimcp://audit/current"

        tool_response = await _recv(proc)
        assert tool_response["id"] == 3
    finally:
        proc.terminate()
        await proc.wait()


async def test_resource_list_id_collision_with_backends_own_roots_list_id(tmp_path: Path):
    """Found via a real MCP Inspector run (docs/adr/0006-mcp-inspector-
    compatibility.md), not by this project's own tests -- every existing
    test here answers roots/list before sending its next request, so the
    window this bug lives in never opened. A real client that doesn't do
    that (nothing in the spec requires it) exposes it: devmcp's own
    self-originated roots/list is *always* id 1 (its first request, own
    counter), completely independent of whatever id the Host happens to
    pick for resources/list. If the Host also happens to use id 1 -- legal,
    since JSON-RPC ids only need to be unique among a sender's own in-flight
    requests -- the two unrelated messages collide unless verimcp checks
    "is this actually a response" (no "method" key) before treating an
    id match as the resources/list reply."""
    audit_path = tmp_path / "audit.jsonl"
    proc = await _spawn_verimcp(tmp_path, verimcp_args=["--audit-log", str(audit_path)])
    try:
        await _initialize(proc)  # consumes id 1 for the Host's own request/response pair

        # Deliberately reuse id 1 for resources/list, and deliberately don't
        # answer devmcp's roots/list (also id 1, its own independent
        # counter) before reading the response -- the exact ordering a real
        # one-shot client produced.
        await _send(proc, {"jsonrpc": "2.0", "id": 1, "method": "resources/list", "params": {}})

        seen = []
        roots_request = None
        while True:
            message = await _recv(proc)
            seen.append(message)
            if message.get("method") == "roots/list":
                roots_request = message
            if message.get("id") == 1 and "result" in message and "resources" in message["result"]:
                break

        responses_with_resources = [m for m in seen if m.get("id") == 1 and "result" in m]
        assert len(responses_with_resources) == 1, f"got duplicate/corrupted responses: {seen}"

        uris = {r["uri"] for r in responses_with_resources[0]["result"]["resources"]}
        assert uris == {"repo://status", "repo://log", "ci://last-run", "verimcp://audit", "verimcp://audit/current"}

        assert roots_request is not None, "devmcp's roots/list request must still reach the Host, not be swallowed"
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })
    finally:
        proc.terminate()
        await proc.wait()


async def test_sqlite_tools_verified_over_real_pipe(tmp_path: Path):
    """SQLite domain, same shape as the write_file/git_commit pipe tests:
    real subprocess pair, real .db file, SQLiteVerifier independently
    reconnecting to confirm the table/row genuinely exist -- and the audit
    trail records verified_ok for both, same as ADR/ROADMAP's existing
    domains."""
    audit_path = tmp_path / "audit.jsonl"
    proc = await _spawn_verimcp(tmp_path, verimcp_args=["--audit-log", str(audit_path)])
    try:
        await _initialize(proc)
        roots_request = await _recv(proc)
        await _send(proc, {
            "jsonrpc": "2.0", "id": roots_request["id"],
            "result": {"roots": [{"uri": tmp_path.as_uri(), "name": "repo"}]},
        })

        await _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {
                "name": "sqlite_create_table",
                "arguments": {"db_path": "app.db", "table": "users", "columns": {"id": "INTEGER PRIMARY KEY", "name": "TEXT"}},
            },
        })
        create_response = await _recv(proc)
        assert create_response["result"]["isError"] is False

        await _send(proc, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {
                "name": "sqlite_insert_row",
                "arguments": {"db_path": "app.db", "table": "users", "values": {"id": 1, "name": "rudra"}},
            },
        })
        insert_response = await _recv(proc)
        assert insert_response["result"]["isError"] is False

        await _send(proc, {
            "jsonrpc": "2.0", "id": 4, "method": "resources/read",
            "params": {"uri": "verimcp://audit/current"},
        })
        read_response = await _recv(proc)
        lines = [json.loads(line) for line in read_response["result"]["contents"][0]["text"].splitlines() if line.strip()]
        by_target = {line["target"]: line for line in lines}
        assert by_target["sqlite_create_table"]["outcome"] == "verified_ok"
        assert by_target["sqlite_create_table"]["verification"]["verifiers"] == ["SQLiteVerifier"]
        assert by_target["sqlite_insert_row"]["outcome"] == "verified_ok"
        assert by_target["sqlite_insert_row"]["verification"]["verifiers"] == ["SQLiteVerifier"]
    finally:
        proc.terminate()
        await proc.wait()
