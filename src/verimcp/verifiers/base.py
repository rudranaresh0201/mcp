"""The Verifier plugin interface.

Every verifier answers one question: given the tool call the client made and
the response the real backend server produced, does independently-checkable
reality actually match what the response claims? Nothing in the MCP spec
checks this - `isError: false` only means the tool ran without raising an
error, not that its claimed effect is real.
"""
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Receipts ride along on the response dict under this private key while the
# verifier chain runs, and Proxy pops them off before anything is forwarded,
# so the key never appears in a real JSON-RPC message. Kept on the response
# (rather than a return value) so the verify() signature every third-party
# verifier already implements does not change.
RECEIPTS_KEY = "__verimcp_receipts__"


def pop_receipts(message: dict[str, Any]) -> list[dict[str, Any]]:
    return message.pop(RECEIPTS_KEY, None) or []

# The one place this string is written. Both enforcement shapes below build
# on it and extract_failure_detail() reads it back off -- keeping producer
# and parser in one module is deliberate: a parser living in proxy.py would
# silently start returning None the day this wording changed, and the audit
# log would quietly lose its diagnostics with every test still passing.
FAILURE_PREFIX = "[verimcp] postcondition check failed: "


def extract_failure_detail(response: dict[str, Any]) -> str | None:
    """Recover the human-readable reason out of a response that `_override`
    or `_error` produced, or None if this failure did not come from verimcp.

    That None case matters and is not just defensive: a backend's own
    `isError` response is a real failure too, but it is the backend
    describing itself -- exactly the kind of self-report this whole project
    declines to trust. Only a verimcp-authored message represents an
    independently re-derived contradiction, so only that gets recorded as
    verification evidence.
    """
    error = response.get("error")
    if isinstance(error, dict):
        message = error.get("message", "")
        return message[len(FAILURE_PREFIX) :] if message.startswith(FAILURE_PREFIX) else None

    for block in response.get("result", {}).get("content") or []:
        text = block.get("text", "") if isinstance(block, dict) else ""
        if text.startswith(FAILURE_PREFIX):
            return text[len(FAILURE_PREFIX) :]
    return None


class Verifier(ABC):
    @abstractmethod
    def applies_to(self, tool_name: str) -> bool:
        """Should this verifier run for a given tool name?"""

    @abstractmethod
    def verify(self, request: dict[str, Any], response: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
        """Return the response to actually forward to the client.

        Given the original request and the backend's raw response, recompute
        ground truth. If it matches, return `response` unchanged (pass through).
        If it doesn't, return an overridden response with `isError: true` and
        a real diagnostic message - this is the enforcement step.

        `root` is the filesystem root the *backend* negotiated with the Host
        via roots/list, if any -- relative paths in tool arguments are the
        backend's paths, not verimcp's own cwd, so a verifier that touches
        the filesystem must resolve against this, not against Path.cwd().
        """

    def _receipt(
        self, response: dict[str, Any], *, verdict: str, summary: str, source: str, evidence: dict[str, Any]
    ) -> dict[str, Any]:
        """Attach proof of what this verifier actually observed -- for a claim
        that held ("verified") as much as for one that didn't ("contradicted").
        A bare pass/fail asks the reader to trust the verifier; a receipt
        shows the facts it read, where it read them, and when.

        Returns `response` so a verifier can write
        `return self._receipt(self._override(...), ...)`."""
        response.setdefault(RECEIPTS_KEY, []).append({
            "verifier": type(self).__name__,
            "verdict": verdict,
            "summary": summary,
            "source": source,
            "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "evidence": evidence,
        })
        return response

    @staticmethod
    def _override(response: dict[str, Any], message: str) -> dict[str, Any]:
        """Shared enforcement step for tools/call: every verifier rewrites a
        failed check into the same isError:true shape with a [verimcp]-tagged
        diagnostic, so a Host always sees a consistent failure format
        regardless of which verifier caught the lie."""
        overridden = dict(response)
        overridden["result"] = {
            "isError": True,
            "content": [{"type": "text", "text": f"{FAILURE_PREFIX}{message}"}],
        }
        return overridden

    @staticmethod
    def _error(response: dict[str, Any], message: str) -> dict[str, Any]:
        """Shared enforcement step for non-tool methods (e.g. resources/read):
        unlike a tool call, these don't have an isError-flavored "successful"
        failure shape in the MCP spec -- a failed read is a real JSON-RPC
        `error` object, replacing `result` entirely, not a value nested
        inside one."""
        return {
            "jsonrpc": response.get("jsonrpc", "2.0"),
            "id": response.get("id"),
            "error": {"code": -32001, "message": f"{FAILURE_PREFIX}{message}"},
        }
