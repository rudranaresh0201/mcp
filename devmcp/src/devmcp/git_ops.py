"""Real subprocess wrappers around git. No mocking: verimcp's whole premise
is checking real side effects, so devmcp's tools must produce real ones."""
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def _run(repo_root: Path, *args: str) -> str:
    # stdin=DEVNULL: devmcp's own real stdin is read on a background thread
    # (cli.py's _StdinReader). Without this, a spawned git process inherits
    # that same handle and can deadlock against it on Windows -- the exact
    # bug found and fixed in verimcp's GitCommitVerifier.
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise GitError(result.stderr.strip())
    return result.stdout.strip()


def ensure_repo(repo_root: Path) -> None:
    if not (repo_root / ".git").exists():
        _run(repo_root, "init")
        _run(repo_root, "config", "user.email", "devmcp@example.com")
        _run(repo_root, "config", "user.name", "devmcp")


def write_file(repo_root: Path, path: str, content: str) -> None:
    file_path = repo_root / path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content)


def commit(repo_root: Path, message: str, files: list[str]) -> dict:
    for f in files:
        _run(repo_root, "add", f)
    _run(repo_root, "commit", "-m", message)
    commit_hash = _run(repo_root, "rev-parse", "HEAD")
    return {"commit_hash": commit_hash, "message": message, "files": files}


def create_branch(repo_root: Path, name: str, from_ref: str = "HEAD") -> dict:
    _run(repo_root, "branch", name, from_ref)
    commit_hash = _run(repo_root, "rev-parse", name)
    return {"branch": name, "commit_hash": commit_hash}


def status(repo_root: Path) -> str:
    return _run(repo_root, "status", "--short")


def log(repo_root: Path, limit: int = 20) -> list[dict]:
    output = _run(repo_root, "log", f"-{limit}", "--format=%H%x1f%s")
    if not output:
        return []
    entries = []
    for line in output.splitlines():
        commit_hash, _, message = line.partition("\x1f")
        entries.append({"commit_hash": commit_hash, "message": message})
    return entries


def read_file(repo_root: Path, path: str) -> str:
    return (repo_root / path).read_text()
