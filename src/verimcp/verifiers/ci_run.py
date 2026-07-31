"""Fourth verifier: checks run_ci_pipeline's claims about each step.

Unlike git_commit/git_branch, there's no single persistent fact to check
after the fact -- a shell step's effect could be anything, or nothing at
all. Two checks instead:

1. Self-consistency: does each step's claimed `passed` actually match its
   claimed `exit_code`, and does the claimed overall `passed` actually match
   AND(all steps' claimed `passed`)? Catches a tool that miscomputed its own
   summary without needing to touch the shell again.
2. Re-execution, but only for steps the caller explicitly marked
   `idempotent: true` -- re-running an arbitrary shell command isn't safe in
   general (a step that does `git commit` would commit twice), so a step
   defaults to "not safe to re-run" unless the caller says otherwise. For
   those, the verifier reruns the exact same command in the same repo root
   and compares the real exit code to the claimed one.
"""
import subprocess
from pathlib import Path
from typing import Any

from verimcp.verifiers.base import Verifier

CI_TOOLS = {"run_ci_pipeline"}


def _rerun_exit_code(repo_root: Path, cmd: str) -> int:
    # stdin=DEVNULL: same Windows deadlock guard as every other verifier that
    # shells out (see GitCommitVerifier) -- verimcp's own stdin is being read
    # on a background thread, and an inherited handle would contend with it.
    result = subprocess.run(
        cmd, shell=True, cwd=repo_root, capture_output=True, stdin=subprocess.DEVNULL, check=False,
    )
    return result.returncode


class CIRunVerifier(Verifier):
    def applies_to(self, tool_name: str) -> bool:
        return tool_name in CI_TOOLS

    def verify(self, request: dict[str, Any], response: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
        result = response.get("result", {})
        if result.get("isError"):
            return response  # backend already reported failure, nothing to add

        structured = result.get("structuredContent", {})
        steps = structured.get("steps")
        overall_passed = structured.get("passed")
        if steps is None or overall_passed is None:
            return response  # not a shape we know how to check

        for step in steps:
            if step.get("passed") != (step.get("exit_code") == 0):
                return self._override(
                    response,
                    f"step {step.get('name')!r} claims passed={step.get('passed')} "
                    f"but exit_code={step.get('exit_code')!r} says otherwise",
                )

        if overall_passed != all(step.get("passed") for step in steps):
            return self._override(
                response,
                f"claimed overall passed={overall_passed!r} does not match the per-step results",
            )

        if root is None:
            return response  # can't re-run steps without knowing the repo -- don't guess, don't block

        for step in steps:
            if not step.get("idempotent"):
                continue  # not safe to re-run -- unmarked steps are trusted on self-consistency only
            actual_exit_code = _rerun_exit_code(root, step["cmd"])
            if actual_exit_code != step.get("exit_code"):
                return self._override(
                    response,
                    f"step {step.get('name')!r} claimed exit_code={step.get('exit_code')!r}, "
                    f"but re-running it produced exit_code={actual_exit_code!r}",
                )

        return response  # verified: claims are self-consistent and idempotent steps re-confirmed
