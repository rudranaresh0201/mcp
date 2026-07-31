"""First concrete verifier: checks that write_file actually wrote what it
claimed to write. Filesystem is the starting point because the ground truth
check is trivial and needs no external API - just read the file back and
hash-compare it against what the tool call claimed to write.
"""
import hashlib
from pathlib import Path
from typing import Any

from verimcp.verifiers.base import Verifier

WRITE_TOOLS = {"write_file"}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class FilesystemVerifier(Verifier):
    def applies_to(self, tool_name: str) -> bool:
        return tool_name in WRITE_TOOLS

    def verify(self, request: dict[str, Any], response: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
        result = response.get("result", {})
        if result.get("isError"):
            return response  # backend already reported failure, nothing to add

        args = request.get("params", {}).get("arguments", {})
        path = args.get("path")
        expected_content = args.get("content")
        if path is None or expected_content is None:
            return response  # not a shape we know how to check

        if root is None:
            return response  # can't tell where the backend actually wrote this -- don't guess, don't block

        actual_path = Path(root) / path
        if not actual_path.exists():
            return self._override(response, f"claimed write to {path!r} succeeded, but the file does not exist")

        actual_content = actual_path.read_text()
        if _sha256(actual_content) != _sha256(expected_content):
            return self._override(
                response,
                f"claimed write to {path!r} succeeded, but on-disk content does not match what was written "
                f"(expected sha256={_sha256(expected_content)[:12]}..., found sha256={_sha256(actual_content)[:12]}...)",
            )

        return response  # verified: claim matches reality, pass through unchanged
