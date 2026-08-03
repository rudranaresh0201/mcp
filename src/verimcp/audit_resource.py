"""MCP resource surface for the audit log -- verimcp://audit and friends.

Pure functions, no I/O beyond what the AuditStore itself does; Proxy is what
wires these into resources/list, resources/read, resources/subscribe, and the
`initialize` capability patch (verimcp is a proxy, not the MCP server, so
resources only reach the Host if the initialize response actually declares
the `resources` capability -- not every backend does). See
docs/adr/0004-audit-log-as-mcp-resource-and-policy-replay.md for the URI
namespace rationale and the exact spec shapes this was built against
(modelcontextprotocol.io/specification/2025-06-18/server/resources).
"""
import json
from typing import Any

from verimcp.audit import AuditStore

AUDIT_URI = "verimcp://audit"
CURRENT_SESSION_URI = "verimcp://audit/current"
_MIME_TYPE = "application/x-ndjson"


def list_entries() -> list[dict[str, Any]]:
    """The resources verimcp always offers once an audit log is configured --
    appended to whatever resources/list the backend itself returned."""
    return [
        {"uri": AUDIT_URI, "name": "verimcp audit log (all sessions)", "mimeType": _MIME_TYPE},
        {"uri": CURRENT_SESSION_URI, "name": "verimcp audit log (this session)", "mimeType": _MIME_TYPE},
    ]


def is_audit_uri(uri: str) -> bool:
    """True for verimcp://audit itself, .../current, or .../<session_id>.
    The last form is readable but not listed -- same prefix-dispatch pattern
    devmcp's repo_file.py uses for repo://file/* -- for pulling a specific
    past session out of a log file that spans many proxy runs."""
    return uri == AUDIT_URI or uri.startswith(AUDIT_URI + "/")


def read(uri: str, store: AuditStore) -> dict[str, Any] | None:
    """Build a resources/read result for an audit uri, or None if `uri`
    isn't ours (caller should fall back to forwarding to the backend)."""
    if uri == AUDIT_URI:
        entries = store.read_all()
    elif uri == CURRENT_SESSION_URI:
        entries = store.read_session(store.session_id)
    elif uri.startswith(AUDIT_URI + "/"):
        entries = store.read_session(uri[len(AUDIT_URI) + 1 :])
    else:
        return None

    text = "\n".join(json.dumps(entry) for entry in entries)
    return {"contents": [{"uri": uri, "mimeType": _MIME_TYPE, "text": text}]}
