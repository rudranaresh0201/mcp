"""Unit tests for AuditStore -- no proxy, no subprocess, just the JSONL
read/write contract other tests (and audit_resource.py) build on."""
import json
from pathlib import Path

from verimcp.audit import AuditStore


def test_record_appends_one_json_line(tmp_path: Path):
    store = AuditStore(tmp_path / "audit.jsonl", session_id="s1")

    entry = store.record(method="tools/call", target="write_file", arguments={"path": "a.txt"}, outcome="forwarded")

    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    on_disk = json.loads(lines[0])
    assert on_disk == entry
    assert on_disk["session_id"] == "s1"
    assert on_disk["target"] == "write_file"
    assert on_disk["outcome"] == "forwarded"
    assert on_disk["gate"] is None
    assert on_disk["verification"] is None
    assert on_disk["approval"] is None


def test_seq_is_monotonic_within_a_session(tmp_path: Path):
    store = AuditStore(tmp_path / "audit.jsonl", session_id="s1")

    first = store.record(method="tools/call", target="a", arguments=None, outcome="forwarded")
    second = store.record(method="tools/call", target="b", arguments=None, outcome="forwarded")

    assert first["seq"] == 1
    assert second["seq"] == 2


def test_session_id_is_generated_when_not_given(tmp_path: Path):
    store = AuditStore(tmp_path / "audit.jsonl")
    assert store.session_id  # non-empty
    assert isinstance(store.session_id, str)


def test_read_all_returns_every_entry_across_sessions(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    AuditStore(path, session_id="s1").record(method="tools/call", target="a", arguments=None, outcome="forwarded")
    AuditStore(path, session_id="s2").record(method="tools/call", target="b", arguments=None, outcome="forwarded")

    entries = AuditStore(path, session_id="s3").read_all()

    assert [e["session_id"] for e in entries] == ["s1", "s2"]


def test_read_session_filters_to_one_session(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    AuditStore(path, session_id="s1").record(method="tools/call", target="a", arguments=None, outcome="forwarded")
    AuditStore(path, session_id="s2").record(method="tools/call", target="b", arguments=None, outcome="forwarded")

    entries = AuditStore(path, session_id="s3").read_session("s1")

    assert len(entries) == 1
    assert entries[0]["target"] == "a"


def test_read_all_on_a_log_that_does_not_exist_yet_is_empty(tmp_path: Path):
    store = AuditStore(tmp_path / "does-not-exist.jsonl")
    assert store.read_all() == []
