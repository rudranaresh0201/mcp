"""Stateless MCP (spec revision 2026-07-28): what a request says about itself.

Before this revision the Host described itself once, in `initialize`, and the
proxy remembered it. Now there is no handshake: every request carries its
protocol version and client capabilities in params._meta, and anything the
server wants from the Host (an approval, here) is asked for by answering
`resultType: "input_required"` and waiting for the Host to retry the same
call with the answer -- never by sending the Host a request of our own.

Nothing in this module keeps per-Host state. The one thing that must survive
between the first attempt and the retry -- "verimcp did ask for approval of
exactly this call" -- travels through the Host as a signed `requestState`.
"""
import hashlib
import hmac
import secrets
import time
from typing import Any

from verimcp import idempotency

VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
CAPABILITIES_KEY = "io.modelcontextprotocol/clientCapabilities"

# Key of verimcp's own entry in inputRequests / inputResponses.
APPROVAL_KEY = "verimcp_approval"


def _meta(request: dict[str, Any]) -> dict[str, Any]:
    return request.get("params", {}).get("_meta", {})


def is_modern(request: dict[str, Any]) -> bool:
    """A request that declares its own protocol version is stateless-era;
    one that doesn't belongs to a conversation opened with `initialize`."""
    return VERSION_KEY in _meta(request)


def client_capabilities(request: dict[str, Any]) -> dict[str, Any]:
    """What the Host says it supports ON THIS REQUEST. Deliberately not
    merged with anything seen on an earlier request: a capability the Host
    declared a moment ago and omits now is, for this request, not there."""
    return _meta(request).get(CAPABILITIES_KEY, {})


def approval_request(tool_name: str) -> dict[str, Any]:
    """The elicitation verimcp wants answered, in the shape it takes inside
    an InputRequiredResult's inputRequests map."""
    return {
        "method": "elicitation/create",
        "params": {
            "mode": "form",
            "message": f"verimcp policy requires approval before running tool {tool_name!r}.",
            # Empty on purpose, same as the handshake-era flow: accept /
            # decline / cancel is the whole answer.
            "requestedSchema": {"type": "object", "properties": {}},
        },
    }


class ApprovalState:
    """Signs and checks the `requestState` handed out with an approval
    request. The spec requires integrity protection for any requestState that
    influences authorization, and names what to bind: an expiry and the
    originating request. Without it a retry could simply arrive carrying
    "accept" for a call nobody was ever asked about, or reuse one approval
    for a different call.

    Not bound: the authenticated principal (stdio has none) and single use
    (an approval can be replayed for the SAME call until it expires). The
    secret is per process, so a restarted proxy rejects approvals issued by
    the previous one -- fail closed, the Host is just asked again.
    """

    def __init__(self, secret: bytes | None = None, ttl: float = 300.0) -> None:
        self._secret = secret or secrets.token_bytes(32)
        self._ttl = ttl

    def _sign(self, expires: str, call_digest: str) -> str:
        return hmac.new(self._secret, f"{expires}.{call_digest}".encode(), hashlib.sha256).hexdigest()

    def issue(self, tool_name: str, arguments: dict[str, Any] | None) -> str:
        expires = str(int(time.time() + self._ttl))
        return f"{expires}.{self._sign(expires, idempotency.compute_key(tool_name, arguments))}"

    def verify(self, state: Any, tool_name: str, arguments: dict[str, Any] | None) -> bool:
        if not isinstance(state, str) or state.count(".") != 1:
            return False
        expires, signature = state.split(".")
        if not expires.isdigit() or int(expires) < time.time():
            return False
        expected = self._sign(expires, idempotency.compute_key(tool_name, arguments))
        return hmac.compare_digest(signature, expected)
