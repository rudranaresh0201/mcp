"""Fifth verifier, first one that isn't a tools/call: checks that a
resources/read response actually matches the real repo state, for the
resources whose ground truth is something verimcp can independently
recompute -- `repo://status`, `repo://log`, `repo://file/{path}`.

`ci://last-run` is deliberately NOT covered here: its only "ground truth" is
devmcp's own in-memory cache of the last run_ci_pipeline call, which isn't
something outside devmcp itself to check it against (the call it mirrors was
already independently verified by CIRunVerifier at the time it happened).
Same "no verifier, on principle" bucket as sampling and prompts, not an
oversight.

Commands here duplicate devmcp.git_ops.status/log exactly (same git flags,
same log format string) rather than importing devmcp -- verimcp has no
dependency on any particular backend, so it can't reuse devmcp's code, only
match its documented behavior.
"""
import json
import subprocess
from pathlib import Path
from typing import Any

from verimcp.verifiers.base import Verifier

STATUS_URI = "repo://status"
LOG_URI = "repo://log"
FILE_PREFIX = "repo://file/"
LOG_LIMIT = 20  # matches devmcp.git_ops.log's default


def _git(repo_root: Path, *args: str) -> str:
    # stdin=DEVNULL: same Windows deadlock guard as every other verifier that
    # shells out (see GitCommitVerifier).
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False,
    )
    return result.stdout.strip()


def _real_status(repo_root: Path) -> str:
    return _git(repo_root, "status", "--short")


def _real_log(repo_root: Path) -> str:
    output = _git(repo_root, "log", f"-{LOG_LIMIT}", "--format=%H%x1f%s")
    entries = []
    if output:
        for line in output.splitlines():
            commit_hash, _, message = line.partition("\x1f")
            entries.append({"commit_hash": commit_hash, "message": message})
    return json.dumps(entries)


def _real_file(repo_root: Path, path: str) -> str | None:
    file_path = repo_root / path
    if not file_path.exists():
        return None
    return file_path.read_text()


class ResourceReadVerifier(Verifier):
    def applies_to(self, uri: str) -> bool:
        return uri in (STATUS_URI, LOG_URI) or uri.startswith(FILE_PREFIX)

    def verify(self, request: dict[str, Any], response: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
        if "error" in response:
            return response  # backend already reported failure, nothing to add

        contents = response.get("result", {}).get("contents", [])
        if not contents:
            return response  # not a shape we know how to check

        uri = request.get("params", {}).get("uri", "")
        claimed_text = contents[0].get("text")
        if claimed_text is None:
            return response

        if root is None:
            return response  # can't recompute ground truth without knowing the repo -- don't guess, don't block

        if uri == STATUS_URI:
            expected = _real_status(root)
        elif uri == LOG_URI:
            expected = _real_log(root)
        elif uri.startswith(FILE_PREFIX):
            path = uri.removeprefix(FILE_PREFIX)
            expected = _real_file(root, path)
            if expected is None:
                return self._error(response, f"claimed to read {uri!r}, but no such file exists in the repo")
        else:
            return response  # shouldn't happen given applies_to, but no ground truth to compare against

        if claimed_text != expected:
            return self._error(response, f"claimed contents of {uri!r} do not match the repo's real current state")

        return response  # verified: claim matches reality, pass through unchanged
