"""Unit-level: exercise CIRunVerifier directly, no devmcp involved."""
from pathlib import Path

from verimcp.verifiers.ci_run import CIRunVerifier

PASS_CMD = 'python -c "import sys; sys.exit(0)"'
FAIL_CMD = 'python -c "import sys; sys.exit(1)"'


def _response(steps: list[dict], overall_passed: bool) -> dict:
    return {"result": {"isError": False, "structuredContent": {"steps": steps, "passed": overall_passed}}}


def test_passes_through_self_consistent_claims(tmp_path: Path):
    steps = [{"name": "test", "cmd": PASS_CMD, "exit_code": 0, "passed": True}]
    verifier = CIRunVerifier()

    result = verifier.verify({}, _response(steps, True), root=tmp_path)

    assert result["result"]["isError"] is False


def test_catches_a_step_whose_passed_flag_contradicts_its_own_exit_code(tmp_path: Path):
    """Cheapest lie: claims passed=True but its own claimed exit_code is nonzero."""
    steps = [{"name": "test", "cmd": PASS_CMD, "exit_code": 1, "passed": True}]
    verifier = CIRunVerifier()

    result = verifier.verify({}, _response(steps, True), root=tmp_path)

    assert result["result"]["isError"] is True
    assert "says otherwise" in result["result"]["content"][0]["text"]


def test_catches_overall_passed_not_matching_the_steps(tmp_path: Path):
    steps = [{"name": "test", "cmd": PASS_CMD, "exit_code": 1, "passed": False}]
    verifier = CIRunVerifier()

    result = verifier.verify({}, _response(steps, True), root=tmp_path)  # overall claims True, step says False

    assert result["result"]["isError"] is True
    assert "does not match the per-step results" in result["result"]["content"][0]["text"]


def test_reruns_an_idempotent_step_and_catches_a_lie(tmp_path: Path):
    """The step is marked safe to re-run, self-consistent on paper, but claims
    the opposite of what the command actually does."""
    steps = [{"name": "test", "cmd": FAIL_CMD, "idempotent": True, "exit_code": 0, "passed": True}]
    verifier = CIRunVerifier()

    result = verifier.verify({}, _response(steps, True), root=tmp_path)

    assert result["result"]["isError"] is True
    assert "re-running it produced exit_code=1" in result["result"]["content"][0]["text"]


def test_does_not_reexecute_a_step_not_marked_idempotent(tmp_path: Path):
    """Same lie as above, but without idempotent:true -- not safe to re-run,
    so self-consistency is all we check, and this passes through."""
    steps = [{"name": "test", "cmd": FAIL_CMD, "exit_code": 0, "passed": True}]
    verifier = CIRunVerifier()

    result = verifier.verify({}, _response(steps, True), root=tmp_path)

    assert result["result"]["isError"] is False


def test_fails_open_when_root_is_unknown(tmp_path: Path):
    steps = [{"name": "test", "cmd": FAIL_CMD, "idempotent": True, "exit_code": 0, "passed": True}]
    verifier = CIRunVerifier()

    result = verifier.verify({}, _response(steps, True), root=None)

    assert result["result"]["isError"] is False
