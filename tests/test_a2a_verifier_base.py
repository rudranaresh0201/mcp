"""Tests for the TaskVerifier contract.

The round-trip test is here from the start rather than added after an
incident: on the MCP side, `_override` constructed a diagnostic that nothing
ever read back, so the audit log recorded `passed: false` with no reason for
weeks. Producer and parser live in one module and this test is what holds
them together.
"""
from typing import Any

from verimcp.a2a.task import COMPLETED, FAILED, claims_success, state_of
from verimcp.a2a.verifiers.base import FAILURE_PREFIX, TaskVerifier, extract_failure_detail


class _Stub(TaskVerifier):
    def applies_to(self, task: dict[str, Any]) -> bool:
        return True

    def verify(self, task: dict[str, Any]) -> dict[str, Any]:
        return task


def _completed_task() -> dict:
    return {
        "id": "t-1",
        "contextId": "c-1",
        "status": {"state": "completed", "timestamp": "2026-08-31T00:00:00Z"},
        "artifacts": [{"artifactId": "a1", "parts": [{"kind": "text", "text": "deployed"}]}],
        "history": [{"role": "user", "kind": "message", "parts": [{"kind": "text", "text": "deploy staging"}]}],
    }


def test_fail_rewrites_a_claimed_success_into_a_real_failure():
    failed = _Stub._fail(_completed_task(), "the service at https://staging returned 502")

    assert state_of(failed) == FAILED
    assert not claims_success(failed)


def test_fail_round_trips_through_extract_failure_detail():
    """The guard that matters. Change the prefix, the message shape, or where
    the text sits, and this fails immediately."""
    reason = "claimed deployment at https://staging, but the host does not resolve"
    failed = _Stub._fail(_completed_task(), reason)

    assert extract_failure_detail(failed) == reason


def test_fail_does_not_mutate_the_original_task():
    """The caller may still hold the original for logging or the audit record.
    A verifier rewriting an object someone else references is a bug that only
    surfaces under concurrency."""
    original = _completed_task()
    _Stub._fail(original, "nope")

    assert state_of(original) == COMPLETED
    assert original["status"].get("message") is None


def test_fail_preserves_the_task_identity_and_its_artifacts():
    """The calling agent still needs to know which task this was, and the
    artifacts are the evidence for why it was rejected -- stripping them would
    leave a failure nobody can investigate."""
    failed = _Stub._fail(_completed_task(), "nope")

    assert failed["id"] == "t-1"
    assert failed["contextId"] == "c-1"
    assert failed["artifacts"][0]["artifactId"] == "a1"
    assert failed["history"]


def test_extract_returns_none_for_an_agents_own_failure():
    """An agent reporting its own failure is a self-report -- the exact thing
    this project refuses to treat as evidence. Only a verimcp-authored message
    is an independently re-derived contradiction."""
    agent_failed = {
        "id": "t-2",
        "status": {
            "state": "failed",
            "message": {"role": "agent", "kind": "message",
                        "parts": [{"kind": "text", "text": "upstream API timed out"}]},
        },
    }
    assert extract_failure_detail(agent_failed) is None


def test_extract_returns_none_on_shapes_that_carry_no_message():
    assert extract_failure_detail({}) is None
    assert extract_failure_detail({"status": None}) is None
    assert extract_failure_detail({"status": {"state": "failed"}}) is None
    assert extract_failure_detail({"status": {"state": "failed", "message": "plain string"}}) is None
    assert extract_failure_detail({"status": {"state": "failed", "message": {"parts": None}}}) is None


def test_extract_anchors_the_prefix_instead_of_substring_matching():
    """An agent echoing our diagnostic inside its own error text must not be
    promoted to verification evidence."""
    echoed = {
        "status": {"state": "failed", "message": {"parts": [
            {"kind": "text", "text": f"the proxy said: {FAILURE_PREFIX}whatever"}
        ]}},
    }
    assert extract_failure_detail(echoed) is None
