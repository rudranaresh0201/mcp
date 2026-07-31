"""Verifier for the official reference `mcp-server-git` backend's git_commit
tool -- proof that verimcp's verifier plugin system works against a real
backend we did not write, not just devmcp.

Same ground truth as GitCommitVerifier (does the claimed commit hash really
exist in this repo's history?), but a different response shape: this
backend returns the hash embedded in a plain-text content block
("Changes committed successfully with hash <sha>"), not structuredContent
-- captured from a real run against the actual package, not guessed. Two
backends can both expose a tool literally named `git_commit` with
incompatible shapes, which is exactly why verifier selection (--verifiers)
exists rather than loading every installed verifier unconditionally."""
import re
import subprocess
from pathlib import Path
from typing import Any

from verimcp.verifiers.base import Verifier

COMMIT_TOOLS = {"git_commit"}
_HASH_RE = re.compile(r"\bwith hash ([0-9a-f]{40})\b")


def _commit_exists(repo_root: Path, commit_hash: str) -> bool:
    # stdin=DEVNULL is deliberate: without it this child inherits verimcp's
    # own real stdin, which is simultaneously read on a background thread
    # by Proxy's _StdinReader -- the two contend for the same handle and
    # deadlock silently on Windows. See GitCommitVerifier for the same fix.
    result = subprocess.run(
        ["git", "-C", str(repo_root), "cat-file", "-e", commit_hash],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


class GitServerCommitVerifier(Verifier):
    def applies_to(self, tool_name: str) -> bool:
        return tool_name in COMMIT_TOOLS

    def verify(self, request: dict[str, Any], response: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
        result = response.get("result", {})
        if result.get("isError"):
            return response  # backend already reported failure, nothing to add

        content = result.get("content", [])
        text = content[0].get("text", "") if content else ""
        match = _HASH_RE.search(text)
        if match is None:
            return response  # not a shape we know how to check

        claimed_hash = match.group(1)

        if root is None:
            return response  # can't check without knowing the repo -- don't guess, don't block

        if not _commit_exists(root, claimed_hash):
            return self._override(response, f"claimed commit {claimed_hash!r} does not exist in this repo's history")

        return response  # verified: the claimed hash is real, pass through unchanged
