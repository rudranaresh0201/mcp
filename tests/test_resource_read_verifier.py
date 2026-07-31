"""Unit-level: exercise ResourceReadVerifier directly against a real (tiny)
git repo and real files on disk."""
import subprocess
from pathlib import Path

from verimcp.verifiers.resource_read import ResourceReadVerifier


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, capture_output=True, check=True)
    (repo / "a.txt").write_text("hi")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "first"], cwd=repo, capture_output=True, check=True)


def _request(uri: str) -> dict:
    return {"params": {"uri": uri}}


def _response(uri: str, text: str) -> dict:
    return {"result": {"contents": [{"uri": uri, "mimeType": "text/plain", "text": text}]}}


def test_passes_through_a_real_file_read(tmp_path: Path):
    _init_repo(tmp_path)
    verifier = ResourceReadVerifier()

    result = verifier.verify(_request("repo://file/a.txt"), _response("repo://file/a.txt", "hi"), root=tmp_path)

    assert "error" not in result


def test_catches_a_file_read_that_does_not_match_disk(tmp_path: Path):
    _init_repo(tmp_path)
    verifier = ResourceReadVerifier()

    result = verifier.verify(
        _request("repo://file/a.txt"), _response("repo://file/a.txt", "not what's really there"), root=tmp_path
    )

    assert "error" in result
    assert "do not match" in result["error"]["message"]


def test_catches_a_claimed_read_of_a_file_that_does_not_exist(tmp_path: Path):
    _init_repo(tmp_path)
    verifier = ResourceReadVerifier()

    result = verifier.verify(
        _request("repo://file/never-created.txt"), _response("repo://file/never-created.txt", "hi"), root=tmp_path
    )

    assert "error" in result
    assert "no such file exists" in result["error"]["message"]


def test_passes_through_a_real_status(tmp_path: Path):
    _init_repo(tmp_path)
    (tmp_path / "untracked.txt").write_text("new")
    verifier = ResourceReadVerifier()
    real_status = subprocess.run(
        ["git", "-C", str(tmp_path), "status", "--short"], capture_output=True, text=True, check=True
    ).stdout.strip()

    result = verifier.verify(_request("repo://status"), _response("repo://status", real_status), root=tmp_path)

    assert "error" not in result


def test_catches_a_fabricated_status(tmp_path: Path):
    _init_repo(tmp_path)
    verifier = ResourceReadVerifier()

    result = verifier.verify(
        _request("repo://status"), _response("repo://status", "?? made_up_file.txt"), root=tmp_path
    )

    assert "error" in result


def test_fails_open_when_root_is_unknown(tmp_path: Path):
    _init_repo(tmp_path)
    verifier = ResourceReadVerifier()

    result = verifier.verify(
        _request("repo://file/a.txt"), _response("repo://file/a.txt", "totally fabricated"), root=None
    )

    assert "error" not in result


def test_does_not_apply_to_ci_last_run():
    """ci://last-run's only ground truth is devmcp's own in-memory cache --
    nothing independent for verimcp to recompute, so it's deliberately not
    covered (same bucket as sampling/prompts, not an oversight)."""
    verifier = ResourceReadVerifier()

    assert verifier.applies_to("ci://last-run") is False
