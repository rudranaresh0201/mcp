"""Example third-party verifier: proves the verimcp.verifiers entry_points
plugin mechanism works for a package outside the verimcp repo itself. See
docs/writing-a-verifier.md for the full guide this package accompanies.

Checks a hypothetical `reverse_string` tool -- ground truth here needs
nothing external at all (no filesystem, no subprocess): the claimed
`structuredContent.reversed` should just equal `arguments.text[::-1]`,
recomputed in pure Python. Deliberately trivial -- the point of this
package is proving *discovery* works for code that has never seen
verimcp's internals, not demonstrating a new real-world domain (that's
already proven elsewhere in this repo, e.g. the third-party mcp-server-git
integration from Phase 1).
"""
from pathlib import Path
from typing import Any

from verimcp.verifiers.base import Verifier

REVERSE_TOOLS = {"reverse_string"}


class ReverseStringVerifier(Verifier):
    def applies_to(self, tool_name: str) -> bool:
        return tool_name in REVERSE_TOOLS

    def verify(self, request: dict[str, Any], response: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
        result = response.get("result", {})
        if result.get("isError"):
            return response  # backend already reported failure, nothing to add

        original = request.get("params", {}).get("arguments", {}).get("text")
        claimed = result.get("structuredContent", {}).get("reversed")
        if original is None or claimed is None:
            return response  # not a shape we know how to check

        expected = original[::-1]
        if claimed != expected:
            return self._override(
                response, f"claimed reversed text {claimed!r} does not match the real reverse of {original!r}"
            )

        return response  # verified: claim matches reality, pass through unchanged
