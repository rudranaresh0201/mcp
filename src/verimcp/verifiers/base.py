"""The Verifier plugin interface.

Every verifier answers one question: given the tool call the client made and
the response the real backend server produced, does independently-checkable
reality actually match what the response claims? Nothing in the MCP spec
checks this - `isError: false` only means the tool ran without raising an
error, not that its claimed effect is real.
"""
from abc import ABC, abstractmethod
from typing import Any


class Verifier(ABC):
    @abstractmethod
    def applies_to(self, tool_name: str) -> bool:
        """Should this verifier run for a given tool name?"""

    @abstractmethod
    def verify(self, request: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
        """Return the response to actually forward to the client.

        Given the original request and the backend's raw response, recompute
        ground truth. If it matches, return `response` unchanged (pass through).
        If it doesn't, return an overridden response with `isError: true` and
        a real diagnostic message - this is the enforcement step.
        """
