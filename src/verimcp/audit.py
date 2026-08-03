"""Phase 3's audit log: every completed `tools/call` and `resources/read`
verimcp forwards gets recorded as one JSON line -- arguments, verification
result, approval status -- replayable (see replay.py) and exposed back to the
Host as an MCP resource (see audit_resource.py). See
docs/adr/0004-audit-log-as-mcp-resource-and-policy-replay.md.

Stdlib-only (json + Path.open("a")), matching the project's existing norm --
no aiofiles, no sqlite anywhere yet. The write in record() is a plain
blocking append, not dispatched through asyncio.create_task the way
elicitation/create is (ADR 0003): that pattern exists to keep the read loop
free while awaiting a *nested request's reply*, which doesn't apply to a
local file write with no reply to wait for.
"""
import itertools
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class AuditStore:
    def __init__(self, path: Path, session_id: str | None = None) -> None:
        self.path = path
        # One session per Proxy instance (one Host<->backend connection).
        # Short hex id, not a full uuid -- it only needs to be unique within
        # one audit log file, not globally.
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self._seq = itertools.count(1)

    def record(
        self,
        *,
        method: str,
        target: str,
        arguments: dict[str, Any] | None,
        outcome: str,
        gate: dict[str, Any] | None = None,
        verification: dict[str, Any] | None = None,
        approval: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one entry and return it. `gate`/`verification`/`approval`
        stay None unless that stage of the proxy actually ran for this call
        -- a plain allow with no policy config touches none of them."""
        entry = {
            "session_id": self.session_id,
            "seq": next(self._seq),
            "timestamp": datetime.now(UTC).isoformat(),
            "method": method,
            "target": target,
            "arguments": arguments,
            "outcome": outcome,
            "gate": gate,
            "verification": verification,
            "approval": approval,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

    def read_all(self) -> list[dict[str, Any]]:
        """Every entry ever written to this log file, across all sessions."""
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def read_session(self, session_id: str) -> list[dict[str, Any]]:
        return [entry for entry in self.read_all() if entry["session_id"] == session_id]
