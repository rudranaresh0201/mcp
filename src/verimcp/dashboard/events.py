"""Reading the audit log as a live stream.

Deliberately dependency-free and I/O-minimal so the whole non-UI half of the
dashboard is unit-testable without FastAPI, a browser, or a running proxy --
the same reason audit.py itself is stdlib-only.

The one non-obvious piece is `AuditTailer`. `AuditStore.record` appends a
line per event while the dashboard reads the same file concurrently, so a
read can land mid-append and see a half-written line. Waiting for the
newline (rather than parsing what's there) is what makes that safe; see
`AuditTailer.poll`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Every outcome Proxy._record_audit can write, in the order a reader should
# care about them. `verified_failed` first is not cosmetic: it is the only
# outcome that represents a claim contradicted by independently re-derived
# reality, which is the entire point of the system.
OUTCOMES = ("verified_failed", "verified_ok", "backend_error", "forwarded", "denied", "approval_denied")


def parse_lines(text: str) -> list[dict[str, Any]]:
    """Parse NDJSON, skipping blank lines. Raises on malformed JSON rather
    than skipping it -- a corrupt audit line is a real problem worth
    surfacing, not something to quietly drop from a compliance record."""
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def read_events(path: Path) -> list[dict[str, Any]]:
    """Every event in the log, or [] if it does not exist yet -- a proxy
    that has not run is a normal state for a dashboard started first, not
    an error."""
    if not path.exists():
        return []
    return parse_lines(path.read_text(encoding="utf-8"))


def is_caught(event: dict[str, Any]) -> bool:
    """True only for a claim verimcp independently disproved.

    Not the same as "the call failed": a backend can report its own error
    and still be telling the truth about it. `verification.passed is False`
    is the narrower, load-bearing condition.
    """
    verification = event.get("verification")
    return bool(verification) and verification.get("passed") is False


def claim_detail(event: dict[str, Any]) -> str | None:
    """The reason a check failed, if verimcp authored it. Populated by
    Proxy._record_completed_audit via verifiers.base.extract_failure_detail;
    absent on older log lines written before that existed, which the UI has
    to render gracefully rather than assume."""
    verification = event.get("verification") or {}
    return verification.get("detail")


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Counters for the stat strip.

    `unverified` is tracked as a first-class number rather than folded into
    a pass rate on purpose. A call nobody could check is not a call that
    passed, and a dashboard that showed only "98% verified" while quietly
    counting unverifiable calls as successes would be making exactly the
    overclaim this project exists to prevent.
    """
    by_outcome = {outcome: 0 for outcome in OUTCOMES}
    verifier_counts: dict[str, int] = {}
    tools: dict[str, int] = {}

    for event in events:
        outcome = event.get("outcome", "")
        by_outcome[outcome] = by_outcome.get(outcome, 0) + 1
        target = event.get("target") or "(none)"
        tools[target] = tools.get(target, 0) + 1
        for name in (event.get("verification") or {}).get("verifiers", []):
            verifier_counts[name] = verifier_counts.get(name, 0) + 1

    checked = by_outcome["verified_ok"] + by_outcome["verified_failed"]
    return {
        "total": len(events),
        "by_outcome": by_outcome,
        "caught": by_outcome["verified_failed"],
        "verified": by_outcome["verified_ok"],
        "unverified": by_outcome["forwarded"],
        # Distinct from `caught`: the tool failed and said so. Surfacing it
        # separately is what stops the console reporting an honest error as
        # a lie it caught.
        "backend_errors": by_outcome["backend_error"],
        "checked": checked,
        # None, not 0 or 100, when nothing has been checked yet -- a rate
        # over an empty set is undefined, and rendering it as a number
        # invents confidence the data does not support.
        "pass_rate": (by_outcome["verified_ok"] / checked) if checked else None,
        "sessions": sorted({e["session_id"] for e in events if "session_id" in e}),
        "verifiers": verifier_counts,
        "tools": tools,
    }


@dataclass
class AuditTailer:
    """Yields audit events appended since the last poll.

    Tracks a byte offset rather than a line count so re-reading the whole
    file every tick is unnecessary, and only consumes up to the final
    newline so a half-written line is left for the next poll instead of
    raising a JSONDecodeError mid-append.
    """

    path: Path
    offset: int = 0
    _buffer: str = field(default="", repr=False)

    def poll(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []

        size = self.path.stat().st_size
        if size < self.offset:
            # The log was truncated or replaced (a new proxy run with a
            # fresh file at the same path). Start over rather than seeking
            # past the end and reading nothing forever.
            self.offset = 0
            self._buffer = ""
        if size == self.offset:
            return []

        with self.path.open("r", encoding="utf-8") as f:
            f.seek(self.offset)
            chunk = f.read()
            self.offset = f.tell()

        self._buffer += chunk
        # Anything after the last newline is a line still being written.
        # Hold it back; it will be complete by a later poll.
        complete, _, remainder = self._buffer.rpartition("\n")
        self._buffer = remainder
        return parse_lines(complete)
