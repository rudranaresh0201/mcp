"""Second concrete verifier, first one beyond filesystem: checks that
git_commit actually created the commit it claims. Ground truth here isn't
file content, it's repo history -- does this exact hash exist in the real
repo, independent of what the tool call reported? `git cat-file -e <hash>`
is git's own "does this object exist" check (exit 0 if yes)."""
import subprocess
from pathlib import Path
from typing import Any

from verimcp.verifiers.base import Verifier

COMMIT_TOOLS = {"git_commit"}


def _commit_exists(repo_root: Path, commit_hash: str) -> bool:
    # stdin=DEVNULL is deliberate, not decoration: without it, this child
    # inherits verimcp's own real stdin -- which, on Windows, is already
    # being read on a background thread (Proxy's _StdinReader). The two
    # compete for the same handle and deadlock. Verified by reproducing the
    # hang with debug tracing and confirming DEVNULL resolves it.
    result = subprocess.run(
        ["git", "-C", str(repo_root), "cat-file", "-e", commit_hash],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


class GitCommitVerifier(Verifier):
    def applies_to(self, tool_name: str) -> bool:
        return tool_name in COMMIT_TOOLS

    def verify(self, request: dict[str, Any], response: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
        result = response.get("result", {})
        if result.get("isError"):
            return response  # backend already reported failure, nothing to add

        claimed_hash = result.get("structuredContent", {}).get("commit_hash")
        if claimed_hash is None:
            return response  # not a shape we know how to check

        if root is None:
            return response  # can't check without knowing the repo -- don't guess, don't block

        if not _commit_exists(root, claimed_hash):
            return self._override(response, f"claimed commit {claimed_hash!r} does not exist in this repo's history")

        return response  # verified: the claimed hash is real, pass through unchanged
