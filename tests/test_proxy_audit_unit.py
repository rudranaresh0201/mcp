"""Unit tests for Proxy's audit-resource helper methods, called directly
(no subprocess, no event loop) -- covers the resources/list error-fallback
edge case that isn't practical to trigger through the real subprocess harness
in test_proxy_integration.py, since devmcp itself always answers
resources/list successfully."""
from pathlib import Path

from verimcp import audit_resource
from verimcp.proxy import Proxy


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
