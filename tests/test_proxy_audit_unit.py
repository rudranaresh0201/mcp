"""Unit tests for Proxy's audit-resource helper methods, called directly
(no subprocess, no event loop) -- covers the resources/list error-fallback
edge case that isn't practical to trigger through the real subprocess harness
in test_proxy_integration.py, since devmcp itself always answers
resources/list successfully."""
from pathlib import Path

from verimcp import audit_resource
from verimcp.proxy import Proxy
from tests.test_verifiers_base import _StubVerifier


def _proxy(tmp_path: Path) -> Proxy:
    return Proxy(["unused"], audit_log=tmp_path / "audit.jsonl")


def test_inject_audit_resources_appends_to_a_successful_response(tmp_path: Path):
    proxy = _proxy(tmp_path)
    backend_response = {"jsonrpc": "2.0", "id": 1, "result": {"resources": [{"uri": "repo://status", "name": "status"}]}}

    patched = proxy._inject_audit_resources(backend_response)

    uris = {r["uri"] for r in patched["result"]["resources"]}
    assert uris == {"repo://status", audit_resource.AUDIT_URI, audit_resource.CURRENT_SESSION_URI}


def test_inject_audit_resources_synthesizes_success_when_backend_errors(tmp_path: Path):
    """A backend with no resources/list support at all -- verimcp's
    capabilities patch already promised the Host that resources/list works,
    so the error is replaced, not forwarded."""
    proxy = _proxy(tmp_path)
    backend_error = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}}

    patched = proxy._inject_audit_resources(backend_error)

    assert "error" not in patched
    uris = {r["uri"] for r in patched["result"]["resources"]}
    assert uris == {audit_resource.AUDIT_URI, audit_resource.CURRENT_SESSION_URI}
    assert patched["id"] == 1


def test_handle_audit_resource_request_read(tmp_path: Path):
    proxy = _proxy(tmp_path)
    proxy._audit.record(method="tools/call", target="write_file", arguments={"path": "a"}, outcome="forwarded")

    response = proxy._handle_audit_resource_request(
        {"jsonrpc": "2.0", "id": 5, "method": "resources/read", "params": {"uri": audit_resource.AUDIT_URI}}
    )

    assert response["id"] == 5
    assert "write_file" in response["result"]["contents"][0]["text"]


def test_handle_audit_resource_request_subscribe_then_unsubscribe(tmp_path: Path):
    proxy = _proxy(tmp_path)
    uri = audit_resource.CURRENT_SESSION_URI

    sub_response = proxy._handle_audit_resource_request(
        {"jsonrpc": "2.0", "id": 1, "method": "resources/subscribe", "params": {"uri": uri}}
    )
    assert sub_response["result"] == {}
    assert uri in proxy._audit_subscribers

    unsub_response = proxy._handle_audit_resource_request(
        {"jsonrpc": "2.0", "id": 2, "method": "resources/unsubscribe", "params": {"uri": uri}}
    )
    assert unsub_response["result"] == {}
    assert uri not in proxy._audit_subscribers


def test_record_completed_audit_uses_stashed_approval_meta(tmp_path: Path):
    """Regression test for the wire-pollution bug: approval metadata must
    travel via _pending_approval_meta, not be mutated onto the request dict
    that gets forwarded to the backend over the wire."""
    proxy = _proxy(tmp_path)
    request = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "write_file", "arguments": {}}}
    proxy._pending_approval_meta[7] = {
        "gate": {"action": "require_approval", "reason": "approved by Host"},
        "approval": {"status": "accept"},
    }

    proxy._record_completed_audit(request, {"jsonrpc": "2.0", "id": 7, "result": {"isError": False}}, [])

    assert "_verimcp_gate" not in request
    assert "_verimcp_approval" not in request
    entries = proxy._audit.read_all()
    assert entries[0]["gate"] == {"action": "require_approval", "reason": "approved by Host"}
    assert entries[0]["approval"] == {"status": "accept"}
    assert entries[0]["outcome"] == "forwarded"


def test_completed_audit_records_why_verification_failed(tmp_path: Path):
    """`passed: false` alone is enough to alert on but not enough to explain
    -- without the detail, anyone reading the log has to re-derive the
    contradiction by hand. This is what the dashboard renders as
    claim-vs-reality."""
    proxy = _proxy(tmp_path)
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "git_commit", "arguments": {}}}
    response = _StubVerifier._override({"jsonrpc": "2.0", "id": 1, "result": {}}, "claimed commit 'deadbeef' does not exist")

    proxy._record_completed_audit(request, response, verifiers_run=[_StubVerifier()])

    entry = proxy._audit.read_all()[-1]
    assert entry["outcome"] == "verified_failed"
    assert entry["verification"]["passed"] is False
    assert entry["verification"]["detail"] == "claimed commit 'deadbeef' does not exist"


def test_a_backends_own_error_is_not_recorded_as_a_caught_lie(tmp_path: Path):
    """The conflation found on 2026-08-30 by running the real fixture: the
    verifier ran and the call failed, but nothing was contradicted -- the
    backend reported its own failure. Recording that as `verified_failed`
    inflated a demo run to 6 catches where only 2 claims had actually been
    disproven. `passed` is None, not False, so anything filtering on a real
    catch cannot pick this up by mistake."""
    proxy = _proxy(tmp_path)
    request = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "git_commit", "arguments": {}}}
    response = {"jsonrpc": "2.0", "id": 2, "result": {"isError": True, "content": [{"type": "text", "text": "fatal: no repo"}]}}

    proxy._record_completed_audit(request, response, verifiers_run=[_StubVerifier()])

    entry = proxy._audit.read_all()[-1]
    assert entry["outcome"] == "backend_error"
    assert entry["verification"]["passed"] is None
    assert "detail" not in entry["verification"]


def test_a_verified_success_still_records_passed_true(tmp_path: Path):
    proxy = _proxy(tmp_path)
    request = {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "git_commit", "arguments": {}}}

    proxy._record_completed_audit(request, {"jsonrpc": "2.0", "id": 4, "result": {"isError": False}}, [_StubVerifier()])

    entry = proxy._audit.read_all()[-1]
    assert entry["outcome"] == "verified_ok"
    assert entry["verification"]["passed"] is True


def test_completed_audit_has_no_verification_block_when_nothing_could_check_it(tmp_path: Path):
    """The honest third state: forwarded, unverified. Distinct from passing
    -- conflating them would let an unchecked call read as a confirmed one."""
    proxy = _proxy(tmp_path)
    request = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "unknown_tool", "arguments": {}}}

    proxy._record_completed_audit(request, {"jsonrpc": "2.0", "id": 3, "result": {}}, verifiers_run=[])

    entry = proxy._audit.read_all()[-1]
    assert entry["outcome"] == "forwarded"
    assert entry["verification"] is None
