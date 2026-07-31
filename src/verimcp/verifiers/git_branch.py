"""Third verifier: checks that git_branch actually created a branch
pointing where it claims. Two ways this can be a lie: the branch doesn't
exist at all, or it exists but points at a different commit than claimed."""
import subprocess
from pathlib import Path
from typing import Any

from verimcp.verifiers.base import Verifier

BRANCH_TOOLS = {"git_branch"}


def _branch_head(repo_root: Path, branch: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--verify", f"refs/heads/{branch}"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,  # same Windows deadlock guard as GitCommitVerifier
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


class GitBranchVerifier(Verifier):
    def applies_to(self, tool_name: str) -> bool:
        return tool_name in BRANCH_TOOLS

    def verify(self, request: dict[str, Any], response: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
        result = response.get("result", {})
        if result.get("isError"):
            return response  # backend already reported failure, nothing to add

        structured = result.get("structuredContent", {})
        claimed_branch = structured.get("branch")
        claimed_hash = structured.get("commit_hash")
        if claimed_branch is None or claimed_hash is None:
            return response  # not a shape we know how to check

        if root is None:
            return response  # can't check without knowing the repo -- don't guess, don't block

        actual_hash = _branch_head(root, claimed_branch)
        if actual_hash is None:
            return self._override(response, f"claimed branch {claimed_branch!r} does not exist in this repo")
        if actual_hash != claimed_hash:
            return self._override(
                response,
                f"claimed branch {claimed_branch!r} points at {claimed_hash!r}, but it actually points at {actual_hash!r}",
            )

        return response  # verified: branch is real and points where claimed
