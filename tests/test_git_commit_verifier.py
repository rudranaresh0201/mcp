"""Unit-level: exercise GitCommitVerifier directly against a real (tiny)
git repo -- no subprocess-pair needed to prove the ground-truth check itself."""
import subprocess
from pathlib import Path

from verimcp.verifiers.git_commit import GitCommitVerifier


def _init_repo_with_one_commit(repo: Path) -> str:
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True)
    (repo / "a.txt").write_text("hi")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "first"], cwd=repo, capture_output=True, check=True)
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def _commit_response(commit_hash: str) -> dict:
    return {"result": {"isError": False, "structuredContent": {"commit_hash": commit_hash}}}


def test_passes_through_a_real_commit(tmp_path: Path):
    real_hash = _init_repo_with_one_commit(tmp_path)
    verifier = GitCommitVerifier()

    result = verifier.verify({}, _commit_response(real_hash), root=tmp_path)

    assert result["result"]["isError"] is False


def test_catches_a_fabricated_hash(tmp_path: Path):
    """The real-world lie this verifier exists for: a backend claims a
    commit hash that was never actually created in this repo's history."""
    _init_repo_with_one_commit(tmp_path)
    verifier = GitCommitVerifier()
    fake_hash = "a" * 40  # well-formed-looking, but never committed here

    result = verifier.verify({}, _commit_response(fake_hash), root=tmp_path)

    assert result["result"]["isError"] is True
    assert "does not exist" in result["result"]["content"][0]["text"]


def test_fails_open_when_root_is_unknown(tmp_path: Path):
    real_hash = _init_repo_with_one_commit(tmp_path)
    verifier = GitCommitVerifier()

    result = verifier.verify({}, _commit_response(real_hash), root=None)

    assert result["result"]["isError"] is False
