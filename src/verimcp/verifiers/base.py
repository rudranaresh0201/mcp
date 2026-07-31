"""The Verifier plugin interface.

Every verifier answers one question: given the tool call the client made and
the response the real backend server produced, does independently-checkable
reality actually match what the response claims? Nothing in the MCP spec
checks this - `isError: false` only means the tool ran without raising an
error, not that its claimed effect is real.
"""
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


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

    @staticmethod
    def _override(response: dict[str, Any], message: str) -> dict[str, Any]:
        """Shared enforcement step for tools/call: every verifier rewrites a
        failed check into the same isError:true shape with a [verimcp]-tagged
        diagnostic, so a Host always sees a consistent failure format
        regardless of which verifier caught the lie."""
        overridden = dict(response)
        overridden["result"] = {
            "isError": True,
            "content": [{"type": "text", "text": f"[verimcp] postcondition check failed: {message}"}],
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
            "error": {"code": -32001, "message": f"[verimcp] postcondition check failed: {message}"},
        }
