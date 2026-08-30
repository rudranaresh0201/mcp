"""Tests for the dashboard's audit-log reading layer.

No FastAPI, no browser, no running proxy -- the whole non-UI half of the
dashboard is exercised here, which is the reason events.py was kept
dependency-free in the first place.
"""
import json
from pathlib import Path

from verimcp.audit import AuditStore
from verimcp.dashboard.events import (
    AuditTailer,
    claim_detail,
    is_caught,
    read_events,
    summarize,
)


def _write(path: Path, *entries: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _event(**overrides) -> dict:
    base = {
        "session_id": "s1",
        "seq": 1,
        "timestamp": "2026-08-30T12:00:00+00:00",
        "method": "tools/call",
        "target": "write_file",
        "arguments": {"path": "a.txt"},
        "outcome": "verified_ok",
        "gate": None,
        "verification": {"verifiers": ["FilesystemVerifier"], "passed": True},
        "approval": None,
    }
    base.update(overrides)
    return base


def test_read_events_on_a_log_that_does_not_exist_yet(tmp_path: Path):
    """A dashboard started before any proxy run is a normal state, not an
    error -- it should render an empty console, not crash."""
    assert read_events(tmp_path / "nothing.jsonl") == []


def test_read_events_parses_real_auditstore_output(tmp_path: Path):
    """Reads what AuditStore actually writes, rather than a hand-made
    fixture that could drift from the real schema."""
    store = AuditStore(tmp_path / "audit.jsonl")
    store.record(method="tools/call", target="write_file", arguments={"path": "a"}, outcome="forwarded")
    store.record(
        method="tools/call",
        target="git_commit",
        arguments={"message": "x"},
        outcome="verified_ok",
        verification={"verifiers": ["GitCommitVerifier"], "passed": True},
    )

    events = read_events(tmp_path / "audit.jsonl")

    assert [e["target"] for e in events] == ["write_file", "git_commit"]
    assert events[0]["seq"] == 1 and events[1]["seq"] == 2


def test_is_caught_only_for_an_independently_disproven_claim():
    """The distinction the whole UI is built around: a call that failed is
    not the same as a claim that was caught."""
    assert is_caught(_event(outcome="verified_failed", verification={"verifiers": ["X"], "passed": False}))
    assert not is_caught(_event(outcome="verified_ok"))
    # backend reported its own failure; no verifier contradicted anything
    assert not is_caught(_event(outcome="forwarded", verification=None))


def test_claim_detail_is_none_on_older_log_lines():
    """Logs written before verification.detail existed are still valid audit
    records. The UI has to degrade to 'no reason recorded' rather than
    assume the key is present."""
    assert claim_detail(_event(verification={"verifiers": ["X"], "passed": False})) is None
    assert claim_detail(_event(verification={"verifiers": ["X"], "passed": False, "detail": "nope"})) == "nope"


def test_summarize_counts_each_outcome_separately():
    events = [
        _event(outcome="verified_ok"),
        _event(outcome="verified_ok"),
        _event(outcome="verified_failed", verification={"verifiers": ["GitCommitVerifier"], "passed": False}),
        _event(outcome="forwarded", verification=None),
    ]

    summary = summarize(events)

    assert summary["total"] == 4
    assert summary["verified"] == 2
    assert summary["caught"] == 1
    assert summary["unverified"] == 1
    assert summary["checked"] == 3
    assert summary["pass_rate"] == 2 / 3


def test_summarize_never_counts_an_unverifiable_call_as_a_pass():
    """The overclaim this project exists to prevent, applied to its own
    dashboard: three calls nobody could check must not read as 100%."""
    summary = summarize([_event(outcome="forwarded", verification=None) for _ in range(3)])

    assert summary["unverified"] == 3
    assert summary["checked"] == 0
    assert summary["pass_rate"] is None


def test_summarize_of_nothing_has_an_undefined_pass_rate():
    assert summarize([])["pass_rate"] is None


def test_summarize_collects_sessions_and_verifier_usage():
    events = [
        _event(session_id="a", verification={"verifiers": ["FilesystemVerifier"], "passed": True}),
        _event(session_id="b", verification={"verifiers": ["FilesystemVerifier", "SchemaConformanceVerifier"], "passed": True}),
    ]

    summary = summarize(events)

    assert summary["sessions"] == ["a", "b"]
    assert summary["verifiers"] == {"FilesystemVerifier": 2, "SchemaConformanceVerifier": 1}


def test_tailer_yields_only_what_is_new(tmp_path: Path):
    log = tmp_path / "audit.jsonl"
    _write(log, _event(seq=1))
    tailer = AuditTailer(log)

    assert [e["seq"] for e in tailer.poll()] == [1]
    assert tailer.poll() == []

    _write(log, _event(seq=2), _event(seq=3))
    assert [e["seq"] for e in tailer.poll()] == [2, 3]


def test_tailer_holds_back_a_half_written_line(tmp_path: Path):
    """The real concurrency hazard: AuditStore appends while the dashboard
    reads, so a poll can land mid-write. Parsing what's there would raise
    JSONDecodeError and kill the stream; waiting for the newline is what
    makes the read safe."""
    log = tmp_path / "audit.jsonl"
    complete = json.dumps(_event(seq=1))
    partial = json.dumps(_event(seq=2))[:20]  # a write caught in progress
    log.write_text(complete + "\n" + partial, encoding="utf-8")

    tailer = AuditTailer(log)
    assert [e["seq"] for e in tailer.poll()] == [1]

    # the rest of that line lands
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_event(seq=2))[20:] + "\n")

    assert [e["seq"] for e in tailer.poll()] == [2]


def test_tailer_restarts_when_the_log_is_replaced(tmp_path: Path):
    """A new proxy run can truncate or recreate the file at the same path.
    Without this the tailer would sit past the new end-of-file and report
    nothing forever, looking exactly like an idle system."""
    log = tmp_path / "audit.jsonl"
    _write(log, _event(seq=1), _event(seq=2))
    tailer = AuditTailer(log)
    tailer.poll()

    log.write_text(json.dumps(_event(seq=1, session_id="fresh")) + "\n", encoding="utf-8")

    events = tailer.poll()
    assert [e["session_id"] for e in events] == ["fresh"]


def test_tailer_on_a_log_that_does_not_exist_yet(tmp_path: Path):
    """Start the dashboard, then the proxy -- the tailer must wait quietly
    and pick up the file when it appears."""
    log = tmp_path / "later.jsonl"
    tailer = AuditTailer(log)
    assert tailer.poll() == []

    _write(log, _event(seq=1))
    assert [e["seq"] for e in tailer.poll()] == [1]


def test_a_backend_error_is_never_counted_as_a_caught_lie():
    """The console-facing half of the 2026-08-30 conflation fix. A tool that
    failed and said so must not inflate the headline number."""
    events = [
        _event(outcome="verified_failed", verification={"verifiers": ["GitCommitVerifier"], "passed": False, "detail": "no such commit"}),
        _event(outcome="backend_error", verification={"verifiers": ["FilesystemVerifier"], "passed": None}),
        _event(outcome="backend_error", verification={"verifiers": ["GitBranchVerifier"], "passed": None}),
    ]

    summary = summarize(events)

    assert summary["caught"] == 1
    assert summary["backend_errors"] == 2
    assert not is_caught(events[1])
    # and it is not silently promoted to a pass either
    assert summary["verified"] == 0
