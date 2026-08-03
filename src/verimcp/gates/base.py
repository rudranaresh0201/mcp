"""The RequestGate interface -- distinct from Verifier on purpose.

A Verifier checks a *response* to a request the Host made. A RequestGate
checks a *request* before it's forwarded at all, in either direction:
a backend-originated request (e.g. sampling/createMessage, checked before
it reaches the Host) or a Host-originated one (e.g. tools/call, checked
before it reaches the backend). There's no ground truth to recompute here
(see docs/adr/0001-verify-vs-dont-framework.md's "Policy gates" bucket) --
just a yes/no on whether this request should be allowed through at all.
On denial, the fabricated response goes back to whichever side sent the
request and is waiting on a reply -- the other side never sees it.

Note: check() is deliberately synchronous. ToolCallPolicyGate's third
action, require_approval, needs an async round-trip (elicitation/create to
the Host) that doesn't fit this contract -- it's handled by the proxy
directly, outside check(), rather than making this interface async. See
docs/adr/0003-require-approval-via-elicitation.md for why.
"""
from abc import ABC, abstractmethod
from typing import Any


class RequestGate(ABC):
    @abstractmethod
    def applies_to(self, method: str) -> bool:
        """Should this gate run for a given method, in either direction?"""

    @abstractmethod
    def check(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Return None to let the request through unchanged, or a JSON-RPC
        error response to send back to whichever side originated the
        request, instead of forwarding it onward at all."""

    @staticmethod
    def _deny(request: dict[str, Any], message: str) -> dict[str, Any]:
        """Shared denial shape: a real JSON-RPC error sent back to whoever
        originated `request`, standing in for the reply they're waiting on."""
        return {
            "jsonrpc": request.get("jsonrpc", "2.0"),
            "id": request.get("id"),
            "error": {"code": -32002, "message": f"[verimcp] request denied: {message}"},
        }
