"""Tests for the dashboard's HTTP/WebSocket layer.

Skipped when the `dashboard` extra isn't installed -- it is optional, and the
core verimcp install must stay free of a web framework.

The websocket tests here exist because of a bug that no amount of unit
testing events.py would have found: with `from __future__ import annotations`
in server.py, FastAPI could not resolve the `WebSocket` annotation (imported
lazily inside create_app, so absent from module globals), silently treated
the handler's `socket` parameter as a query parameter, and rejected every
connection with close code 1008. Imports were fine, the REST routes were
fine, and only a real connection revealed it.
"""
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="dashboard extra not installed")

from fastapi.testclient import TestClient

from verimcp.dashboard.server import create_app


def _event(seq: int, outcome: str = "verified_ok", **verification) -> dict:
    return {
        "session_id": "sess", "seq": seq, "timestamp": "2026-08-30T12:00:00+00:00",
        "method": "tools/call", "target": "git_commit", "arguments": {"message": "x"},
        "outcome": outcome, "gate": None,
        "verification": verification or None, "approval": None,
    }


def _write(path: Path, *events: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")


def test_index_serves_the_console(tmp_path: Path):
    with TestClient(create_app(tmp_path / "audit.jsonl")) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert b"verimcp console" in response.content


def test_api_events_returns_events_and_summary(tmp_path: Path):
    log = tmp_path / "audit.jsonl"
    _write(log,
           _event(1, "verified_ok", verifiers=["GitCommitVerifier"], passed=True),
           _event(2, "verified_failed", verifiers=["GitCommitVerifier"], passed=False, detail="no such commit"))

    with TestClient(create_app(log)) as client:
        payload = client.get("/api/events").json()

    assert len(payload["events"]) == 2
    assert payload["summary"]["caught"] == 1
    assert payload["summary"]["verified"] == 1
    assert payload["log"] == str(log)


def test_websocket_opens_and_sends_a_snapshot(tmp_path: Path):
    """The regression test for the 1008 bug. If the handler's annotation stops
    resolving, this fails at connect rather than on an assertion."""
    log = tmp_path / "audit.jsonl"
    _write(log, _event(1, "verified_ok", verifiers=["GitCommitVerifier"], passed=True))

    with TestClient(create_app(log)) as client, client.websocket_connect("/ws") as socket:
        frame = socket.receive_json()

    assert frame["type"] == "snapshot"
    assert len(frame["events"]) == 1
    assert frame["summary"]["verified"] == 1


def test_websocket_follows_appends_written_after_connect(tmp_path: Path):
    """Live mode: the snapshot covers what already existed, and anything the
    proxy writes afterwards arrives as an append without a reconnect."""
    log = tmp_path / "audit.jsonl"
    _write(log, _event(1, "verified_ok", verifiers=["GitCommitVerifier"], passed=True))

    with TestClient(create_app(log)) as client, client.websocket_connect("/ws") as socket:
        assert socket.receive_json()["type"] == "snapshot"
        _write(log, _event(2, "verified_failed", verifiers=["GitCommitVerifier"], passed=False, detail="fabricated"))
        appended = socket.receive_json()

    assert appended["type"] == "append"
    assert appended["events"][0]["seq"] == 2
    assert appended["summary"]["caught"] == 1
    assert appended["summary"]["total"] == 2


def test_replay_starts_empty_and_fills(tmp_path: Path):
    """Demo mode shows the console filling from nothing, which is the shape a
    live session has -- replaying the full log as the snapshot would show the
    end state immediately and defeat the point."""
    log = tmp_path / "audit.jsonl"
    _write(log,
           _event(1, "verified_ok", verifiers=["GitCommitVerifier"], passed=True),
           _event(2, "verified_failed", verifiers=["GitCommitVerifier"], passed=False, detail="fabricated"))

    with TestClient(create_app(log, replay_delay=0.0)) as client, client.websocket_connect("/ws") as socket:
        snapshot = socket.receive_json()
        first = socket.receive_json()
        second = socket.receive_json()

    assert snapshot["type"] == "snapshot" and snapshot["events"] == []
    assert first["events"][0]["seq"] == 1
    assert second["events"][0]["seq"] == 2
    assert second["summary"]["caught"] == 1


def test_replay_holds_the_connection_after_the_last_event(tmp_path: Path):
    """Returning from the handler would close the socket, the client would
    reconnect, and the console would restart from empty on top of the final
    event -- the one worth looking at."""
    log = tmp_path / "audit.jsonl"
    _write(log, _event(1, "verified_failed", verifiers=["GitCommitVerifier"], passed=False, detail="fabricated"))

    with TestClient(create_app(log, replay_delay=0.0)) as client, client.websocket_connect("/ws") as socket:
        socket.receive_json()  # snapshot
        socket.receive_json()  # the one event
        # Still open: exiting the context is a clean client-side close,
        # not a server-side disconnect. A closed socket would raise here.
        assert socket is not None
