"""Tests for the shared enforcement shapes in verifiers/base.py.

`_override` and `_error` are how every verifier reports a caught lie, and
`extract_failure_detail` is how the audit log reads that reason back out.
The round-trip tests below are the point of this file: they fail the moment
those two drift apart, which is the failure mode that would otherwise be
invisible -- the log would keep recording `passed: false` with no reason and
every other test would still pass.
"""
from pathlib import Path
from typing import Any

from verimcp.verifiers.base import FAILURE_PREFIX, Verifier, extract_failure_detail


class _StubVerifier(Verifier):
    """Concrete subclass so the two @staticmethod enforcement helpers can be
    exercised -- Verifier itself is abstract."""

    def applies_to(self, tool_name: str) -> bool:
        return True

    def verify(self, request: dict[str, Any], response: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
        return response


def test_override_round_trips_through_extract_failure_detail():
    """The guard that matters: whatever _override writes, extract reads back
    verbatim. Change the prefix or the nesting in one and this fails."""
    message = "claimed commit 'abc123' does not exist in this repo's history"
    overridden = _StubVerifier._override({"jsonrpc": "2.0", "id": 7, "result": {"isError": False}}, message)

    assert overridden["result"]["isError"] is True
    assert extract_failure_detail(overridden) == message


def test_error_round_trips_through_extract_failure_detail():
    """Same guard for the non-tool shape. resources/read failures are a real
    JSON-RPC `error` object, not an isError nested inside a result, so they
    travel a different path out and have to be read back differently."""
    message = "file content does not match what the read claimed"
    errored = _StubVerifier._error({"jsonrpc": "2.0", "id": 9, "result": {}}, message)

    assert errored["error"]["code"] == -32001
    assert extract_failure_detail(errored) == message


def test_extract_returns_none_for_a_backends_own_tool_error():
    """A backend reporting its own failure is not verification evidence --
    it is the backend describing itself, which is exactly the self-report
    this project declines to trust. Recording it as a verifier's finding
    would put an unchecked claim and an independently re-derived
    contradiction in the same field."""
    backend_failure = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"isError": True, "content": [{"type": "text", "text": "fatal: not a git repository"}]},
    }
    assert extract_failure_detail(backend_failure) is None


def test_extract_returns_none_for_a_backends_own_jsonrpc_error():
    backend_error = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}}
    assert extract_failure_detail(backend_error) is None


def test_extract_returns_none_for_a_clean_success():
    ok = {"jsonrpc": "2.0", "id": 1, "result": {"isError": False, "content": [{"type": "text", "text": "done"}]}}
    assert extract_failure_detail(ok) is None


def test_extract_tolerates_responses_with_no_content_at_all():
    """structuredContent-only results are legal MCP and carry no content
    list; indexing one blindly would raise inside the audit path, turning a
    logging concern into a dropped tool response."""
    assert extract_failure_detail({"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": {"ok": True}}}) is None
    assert extract_failure_detail({"jsonrpc": "2.0", "id": 1, "result": {"content": None}}) is None


def test_extract_ignores_a_backend_message_that_merely_mentions_verimcp():
    """Prefix match is anchored with startswith, not a substring search -- a
    backend echoing our diagnostic inside its own error text must not be
    promoted to verification evidence."""
    echoed = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"isError": True, "content": [{"type": "text", "text": f"upstream said: {FAILURE_PREFIX}whatever"}]},
    }
    assert extract_failure_detail(echoed) is None
