"""Unit-level: exercise FilesystemVerifier directly, no subprocess needed.
Locks in the exact bug found (and fixed) via a real end-to-end run: when the
proxy doesn't know the backend's root, it must fail OPEN (pass the response
through unverified), not guess a path relative to its own cwd."""
from pathlib import Path

from verimcp.verifiers.filesystem import FilesystemVerifier

SUCCESS = {"result": {"isError": False}}


def _write_call(path: str, content: str) -> dict:
    return {"params": {"arguments": {"path": path, "content": content}}}


def test_passes_through_when_claim_matches_disk(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("hello")
    verifier = FilesystemVerifier()

    result = verifier.verify(_write_call("notes.txt", "hello"), SUCCESS, root=tmp_path)

    assert result["result"]["isError"] is False


def test_catches_a_lie_when_root_is_known(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("hello")
    verifier = FilesystemVerifier()

    result = verifier.verify(_write_call("notes.txt", "goodbye"), SUCCESS, root=tmp_path)

    assert result["result"]["isError"] is True
    assert "does not match" in result["result"]["content"][0]["text"]


def test_fails_open_when_root_is_unknown(tmp_path: Path):
    """The exact bug: without a known root, checking Path(path) against
    verimcp's own cwd produced a false 'file does not exist' failure even
    though the backend wrote it correctly elsewhere. Root=None must now
    skip verification instead of guessing."""
    verifier = FilesystemVerifier()

    result = verifier.verify(_write_call("notes.txt", "hello"), SUCCESS, root=None)

    assert result == SUCCESS
