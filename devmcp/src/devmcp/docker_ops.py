"""Real docker CLI side effects -- no mocking, same principle as git_ops.py
and sqlite_ops.py. Subprocess, not the Docker SDK, matching this project's
existing "no new dependency where a CLI already does the job" norm.
"""
import subprocess
from pathlib import Path

from devmcp.git_ops import resolve_safe_path


class DockerError(RuntimeError):
    pass


def _run(*args: str) -> str:
    # stdin=DEVNULL: same reason as git_ops._run -- devmcp's own real stdin
    # is read on a background thread, and an inheriting child can deadlock
    # against it on Windows.
    result = subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise DockerError(result.stderr.strip())
    return result.stdout.strip()


def build_image(repo_root: Path, dockerfile_path: str, context_path: str, tag: str) -> dict:
    dockerfile = resolve_safe_path(repo_root, dockerfile_path)
    context = resolve_safe_path(repo_root, context_path)
    _run("build", "-f", str(dockerfile), "-t", tag, str(context))
    return {"tag": tag, "dockerfile_path": dockerfile_path, "context_path": context_path}


def run_container(image: str, command: list[str] | None) -> dict:
    # -d (detached) is what makes `docker run` print the container id to
    # stdout immediately, instead of blocking and printing the command's own
    # output -- `docker wait` afterward is what gets us the real exit code
    # without needing a --cidfile temp file.
    container_id = _run("run", "-d", image, *(command or []))
    exit_code_str = _run("wait", container_id)
    return {"container_id": container_id, "exit_code": int(exit_code_str)}
