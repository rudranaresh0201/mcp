"""Unit tests for the pure MCP-resource-shape functions in audit_resource.py
-- see docs/adr/0004 for the URI namespace and the exact spec shapes these
were built against."""
from pathlib import Path

from verimcp import audit_resource
from verimcp.audit import AuditStore


def test_list_entries_includes_full_log_and_current_session():
    uris = {e["uri"] for e in audit_resource.list_entries()}
    assert uris == {audit_resource.AUDIT_URI, audit_resource.CURRENT_SESSION_URI}
    assert all(e["mimeType"] == "application/x-ndjson" for e in audit_resource.list_entries())


def test_is_audit_uri_matches_full_current_and_session_forms():
    assert audit_resource.is_audit_uri("verimcp://audit")
    assert audit_resource.is_audit_uri("verimcp://audit/current")
    assert audit_resource.is_audit_uri("verimcp://audit/abc123")
    assert not audit_resource.is_audit_uri("repo://file/a.txt")
    assert not audit_resource.is_audit_uri("verimcp://something-else")


def test_read_full_log_returns_every_entry_as_ndjson(tmp_path: Path):
    store = AuditStore(tmp_path / "audit.jsonl", session_id="s1")
    store.record(method="tools/call", target="write_file", arguments={"path": "a"}, outcome="forwarded")

    result = audit_resource.read(audit_resource.AUDIT_URI, store)

    assert result["contents"][0]["uri"] == audit_resource.AUDIT_URI
    assert result["contents"][0]["mimeType"] == "application/x-ndjson"
    assert "write_file" in result["contents"][0]["text"]


def test_read_current_session_filters_to_this_stores_session(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    AuditStore(path, session_id="other").record(method="tools/call", target="a", arguments=None, outcome="forwarded")
    store = AuditStore(path, session_id="mine")
    store.record(method="tools/call", target="b", arguments=None, outcome="forwarded")

    result = audit_resource.read(audit_resource.CURRENT_SESSION_URI, store)

    assert "\"target\": \"b\"" in result["contents"][0]["text"]
    assert "\"target\": \"a\"" not in result["contents"][0]["text"]


def test_read_specific_session_by_id(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    AuditStore(path, session_id="s1").record(method="tools/call", target="a", arguments=None, outcome="forwarded")
    AuditStore(path, session_id="s2").record(method="tools/call", target="b", arguments=None, outcome="forwarded")
    store = AuditStore(path, session_id="s3")

    result = audit_resource.read("verimcp://audit/s1", store)

    assert "\"target\": \"a\"" in result["contents"][0]["text"]
    assert "\"target\": \"b\"" not in result["contents"][0]["text"]


def test_read_returns_none_for_a_non_audit_uri(tmp_path: Path):
    store = AuditStore(tmp_path / "audit.jsonl")
    assert audit_resource.read("repo://file/a.txt", store) is None
