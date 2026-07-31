"""Unit-level: exercise GitBranchVerifier directly against a real (tiny)
git repo with a real branch."""
import subprocess
from pathlib import Path

from verimcp.verifiers.git_branch import GitBranchVerifier


def _init_repo_with_branch(repo: Path, branch: str) -> str:
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True)
    (repo / "a.txt").write_text("hi")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "first"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "branch", branch], cwd=repo, capture_output=True, check=True)
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", branch], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _branch_response(branch: str, commit_hash: str) -> dict:
    return {"result": {"isError": False, "structuredContent": {"branch": branch, "commit_hash": commit_hash}}}


def test_passes_through_a_real_branch(tmp_path: Path):
    real_hash = _init_repo_with_branch(tmp_path, "feature-x")
    verifier = GitBranchVerifier()

    result = verifier.verify({}, _branch_response("feature-x", real_hash), root=tmp_path)

    assert result["result"]["isError"] is False


def test_catches_a_branch_that_was_never_created(tmp_path: Path):
    real_hash = _init_repo_with_branch(tmp_path, "feature-x")
    verifier = GitBranchVerifier()

    result = verifier.verify({}, _branch_response("never-existed", real_hash), root=tmp_path)

    assert result["result"]["isError"] is True
    assert "does not exist" in result["result"]["content"][0]["text"]


def test_catches_a_branch_pointing_at_the_wrong_commit(tmp_path: Path):
    """The subtler lie: the branch is real, but claims a different commit
    than the one it actually points at (e.g. a stale ref)."""
    _init_repo_with_branch(tmp_path, "feature-x")
    verifier = GitBranchVerifier()
    wrong_hash = "b" * 40

    result = verifier.verify({}, _branch_response("feature-x", wrong_hash), root=tmp_path)

    assert result["result"]["isError"] is True
    assert "actually points at" in result["result"]["content"][0]["text"]


def test_fails_open_when_root_is_unknown(tmp_path: Path):
    real_hash = _init_repo_with_branch(tmp_path, "feature-x")
    verifier = GitBranchVerifier()

    result = verifier.verify({}, _branch_response("feature-x", real_hash), root=None)

    assert result["result"]["isError"] is False
