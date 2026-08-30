"""Tests for reading an A2A Task across both spec generations.

The normalization tests are the point of this file. A2A renamed its state
values between v0.3.x and v1.0 and both are deployed, so every comparison in
the package routes through state_of(). If that stops handling one generation,
a verifier silently never fires against agents speaking it -- no error, no
log line, just a proxy that quietly verifies nothing.
"""
from verimcp.a2a.task import (
    COMPLETED,
    FAILED,
    TERMINAL_STATES,
    UNKNOWN,
    WORKING,
    artifacts_of,
    claims_success,
    data_of,
    is_terminal,
    normalize_state,
    state_of,
    text_of,
)


def _task(state="completed", artifacts=None) -> dict:
    return {
        "id": "t-1",
        "contextId": "c-1",
        "status": {"state": state, "timestamp": "2026-08-31T00:00:00Z"},
        "artifacts": artifacts if artifacts is not None else [],
        "history": [],
    }


def test_v03_and_v1_spellings_normalize_to_the_same_state():
    """The whole reason this module exists."""
    assert normalize_state("completed") == COMPLETED
    assert normalize_state("TASK_STATE_COMPLETED") == COMPLETED
    assert normalize_state("failed") == FAILED
    assert normalize_state("TASK_STATE_FAILED") == FAILED
    assert normalize_state("input-required") == "input-required"
    assert normalize_state("TASK_STATE_INPUT_REQUIRED") == "input-required"
    assert normalize_state("TASK_STATE_AUTH_REQUIRED") == "auth-required"


def test_unknown_states_degrade_instead_of_raising():
    """A2A v1.0.1 added an extension mechanism that lets agents define their
    own state machines. Crashing on a state we have never seen would take
    down a working conversation over vocabulary we do not need."""
    assert normalize_state("TASK_STATE_QUANTUM_PENDING") == UNKNOWN
    assert normalize_state("") == UNKNOWN
    assert normalize_state(None) == UNKNOWN
    assert normalize_state(42) == UNKNOWN


def test_state_of_survives_a_malformed_task():
    assert state_of({}) == UNKNOWN
    assert state_of({"status": None}) == UNKNOWN
    assert state_of({"status": "completed"}) == UNKNOWN  # status must be an object
    assert state_of({"status": {}}) == UNKNOWN


def test_terminal_states_are_the_only_ones_worth_checking():
    """Verifying a `working` task would be grading a half-finished job."""
    assert TERMINAL_STATES == {"completed", "failed", "canceled", "rejected"}
    assert is_terminal(_task("completed"))
    assert is_terminal(_task("TASK_STATE_REJECTED"))
    assert not is_terminal(_task("working"))
    assert not is_terminal(_task("TASK_STATE_INPUT_REQUIRED"))


def test_claims_success_is_only_completed():
    """The A2A analogue of isError:false. failed/canceled/rejected are the
    agent reporting its own outcome -- no success claim to contradict, the
    same distinction as MCP's backend_error."""
    assert claims_success(_task("completed"))
    assert claims_success(_task("TASK_STATE_COMPLETED"))
    assert not claims_success(_task("failed"))
    assert not claims_success(_task("canceled"))
    assert not claims_success(_task(WORKING))


def test_artifacts_and_parts_tolerate_absent_and_malformed_shapes():
    """`artifacts` is optional in the spec, and a task that produced no output
    legitimately has none."""
    assert artifacts_of({}) == []
    assert artifacts_of({"artifacts": None}) == []
    assert artifacts_of({"artifacts": "not a list"}) == []
    assert artifacts_of({"artifacts": [{"artifactId": "a"}, "junk"]}) == [{"artifactId": "a"}]


def test_text_of_collects_only_text_parts():
    """`kind` is the spec's discriminator across TextPart/FilePart/DataPart.
    A file part's base64 blob must never land in a regex looking for a URL."""
    task = _task(artifacts=[{
        "artifactId": "a1",
        "parts": [
            {"kind": "text", "text": "deployed to staging"},
            {"kind": "file", "file": {"bytes": "AAAA"}},
            {"kind": "data", "data": {"url": "https://x"}},
            {"kind": "text", "text": "all checks green"},
        ],
    }])

    assert text_of(task) == "deployed to staging\nall checks green"


def test_data_of_collects_the_checkable_assertions():
    """A DataPart is where a claim worth verifying lives -- free text is what
    an agent says, a DataPart is what it asserts."""
    task = _task(artifacts=[
        {"artifactId": "a1", "parts": [{"kind": "data", "data": {"deployed_url": "https://staging"}}]},
        {"artifactId": "a2", "parts": [{"kind": "data", "data": {"commit": "abc123"}}]},
        {"artifactId": "a3", "parts": [{"kind": "text", "text": "done"}]},
    ])

    assert data_of(task) == [{"deployed_url": "https://staging"}, {"commit": "abc123"}]


def test_data_of_ignores_a_data_part_whose_payload_is_not_an_object():
    assert data_of(_task(artifacts=[{"parts": [{"kind": "data", "data": "oops"}]}])) == []
