"""require_approval against a stateless-era (2026-07-28) Host: no handshake,
capabilities read off each request, approval asked for with input_required
and granted on the Host's retry. Real `verimcp` process, real backend
subprocess, real stdio -- same shape as test_proxy_integration.py."""
import asyncio
import json
import sys
from pathlib import Path

from verimcp import stateless

BACKEND = Path(__file__).parent / "fixtures" / "stateless_server.py"
ELICITATION = {"elicitation": {}}


async def _spawn(tmp_path: Path):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("rules:\n  - tool: touch\n    action: require_approval\n")
    return await asyncio.create_subprocess_exec(
        sys.executable, "-m", "verimcp.cli", "--policy-config", str(policy_file), "--",
        sys.executable, str(BACKEND),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )


async def _call(proc, id_: int, path: Path, capabilities: dict, **extra) -> dict:
    """One self-describing tools/call -- no initialize is ever sent."""
    message = {
        "jsonrpc": "2.0", "id": id_, "method": "tools/call",
        "params": {
            "name": "touch", "arguments": {"path": str(path)}, **extra,
            "_meta": {stateless.VERSION_KEY: "2026-07-28", stateless.CAPABILITIES_KEY: capabilities},
        },
    }
    proc.stdin.write((json.dumps(message) + "\n").encode())
    await proc.stdin.drain()
    return json.loads(await proc.stdout.readline())


def _answer(action: str) -> dict:
    return {stateless.APPROVAL_KEY: {"action": action}}


async def test_asks_with_input_required_then_forwards_the_retry(tmp_path: Path):
    target = tmp_path / "made.txt"
    proc = await _spawn(tmp_path)
    try:
        first = await _call(proc, 1, target, ELICITATION)
        assert first["result"]["resultType"] == "input_required"
        asked = first["result"]["inputRequests"][stateless.APPROVAL_KEY]
        assert asked["method"] == "elicitation/create"
        assert not target.exists()  # nothing ran yet

        retry = await _call(
            proc, 2, target, ELICITATION,
            inputResponses=_answer("accept"), requestState=first["result"]["requestState"],
        )
        assert retry["id"] == 2
        assert retry["result"]["isError"] is False
        assert target.exists()
        # The approval was addressed to verimcp; the backend never saw it.
        assert json.loads(retry["result"]["content"][0]["text"]) == ["_meta", "arguments", "name"]
    finally:
        proc.terminate()
        await proc.wait()


async def test_capability_is_judged_per_request_not_remembered(tmp_path: Path):
    """Request 1 declares elicitation, request 2 does not. Under the
    handshake the first declaration would have stuck for the whole session;
    here request 2 is denied on what it carries."""
    target = tmp_path / "made.txt"
    proc = await _spawn(tmp_path)
    try:
        first = await _call(proc, 1, target, ELICITATION)
        assert first["result"]["resultType"] == "input_required"

        second = await _call(proc, 2, target, {})
        assert "does not support elicitation" in second["error"]["message"]
        assert not target.exists()
    finally:
        proc.terminate()
        await proc.wait()


async def test_accept_without_a_state_we_issued_is_asked_again(tmp_path: Path):
    """A retry that just claims "accept" -- no requestState, or a made-up
    one -- proves nothing: verimcp never asked about it."""
    target = tmp_path / "made.txt"
    proc = await _spawn(tmp_path)
    try:
        bare = await _call(proc, 1, target, ELICITATION, inputResponses=_answer("accept"))
        assert bare["result"]["resultType"] == "input_required"

        forged = await _call(
            proc, 2, target, ELICITATION, inputResponses=_answer("accept"), requestState="9999999999.deadbeef"
        )
        assert forged["result"]["resultType"] == "input_required"
        assert not target.exists()
    finally:
        proc.terminate()
        await proc.wait()


async def test_approval_for_one_call_does_not_approve_another(tmp_path: Path):
    harmless, other = tmp_path / "harmless.txt", tmp_path / "other.txt"
    proc = await _spawn(tmp_path)
    try:
        first = await _call(proc, 1, harmless, ELICITATION)
        reused = await _call(
            proc, 2, other, ELICITATION,
            inputResponses=_answer("accept"), requestState=first["result"]["requestState"],
        )
        assert reused["result"]["resultType"] == "input_required"
        assert not other.exists()
    finally:
        proc.terminate()
        await proc.wait()


async def test_decline_is_denied_without_running(tmp_path: Path):
    target = tmp_path / "made.txt"
    proc = await _spawn(tmp_path)
    try:
        first = await _call(proc, 1, target, ELICITATION)
        retry = await _call(
            proc, 2, target, ELICITATION,
            inputResponses=_answer("decline"), requestState=first["result"]["requestState"],
        )
        assert "was decline" in retry["error"]["message"]
        assert not target.exists()
    finally:
        proc.terminate()
        await proc.wait()


def test_approval_state_expires():
    state = stateless.ApprovalState(ttl=-1)
    assert not state.verify(state.issue("touch", {"path": "a"}), "touch", {"path": "a"})


def test_approval_state_from_another_proxy_is_rejected():
    issued = stateless.ApprovalState().issue("touch", {"path": "a"})
    assert not stateless.ApprovalState().verify(issued, "touch", {"path": "a"})
