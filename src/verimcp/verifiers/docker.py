"""Docker domain verifier: checks that docker_build_image/docker_run_container
actually produced the image/container they claim, by independently calling
`docker inspect` -- same "don't trust the same actor's own report" principle
as GitCommitVerifier's `git cat-file -e`, just for container/image state
instead of repo history.
"""
import subprocess
from pathlib import Path
from typing import Any

from verimcp.verifiers.base import Verifier

BUILD_IMAGE_TOOLS = {"docker_build_image"}
RUN_CONTAINER_TOOLS = {"docker_run_container"}


def _docker_inspect(*args: str) -> str | None:
    # stdin=DEVNULL: same Windows deadlock reason GitCommitVerifier's own
    # subprocess call already documents -- this child inherits verimcp's
    # real stdin, which is simultaneously being read on a background thread.
    result = subprocess.run(
        ["docker", "inspect", *args],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


class DockerVerifier(Verifier):
    def applies_to(self, tool_name: str) -> bool:
        return tool_name in BUILD_IMAGE_TOOLS or tool_name in RUN_CONTAINER_TOOLS

    def verify(self, request: dict[str, Any], response: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
        result = response.get("result", {})
        tool_name = request.get("params", {}).get("name")

        if tool_name in BUILD_IMAGE_TOOLS:
            return self._verify_build_image(result, response)
        return self._verify_run_container(result, response)

    def _verify_build_image(self, result: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
        if result.get("isError"):
            return response  # backend already reported failure, nothing to add

        tag = result.get("structuredContent", {}).get("tag")
        if tag is None:
            return response  # not a shape we know how to check

        if _docker_inspect(tag) is None:
            return self._override(response, f"claimed image {tag!r} does not exist")
        return response  # verified: the claimed image is real, pass through unchanged

    def _verify_run_container(self, result: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
        # Unlike every other verifier, a "real failure" for this tool (a
        # nonzero exit code) is still a claim worth checking -- isError here
        # just mirrors the container's own exit code (see
        # DockerRunContainerTool), not "the backend already reported this
        # can't be checked" the way it does for build_image.
        structured = result.get("structuredContent")
        if structured is None:
            return response  # not a shape we know how to check (e.g. docker run itself failed to launch)

        container_id = structured.get("container_id")
        claimed_exit_code = structured.get("exit_code")
        if container_id is None or claimed_exit_code is None:
            return response

        inspected = _docker_inspect("--format", "{{.State.ExitCode}}", container_id)
        if inspected is None:
            return self._override(response, f"claimed container {container_id!r} does not exist")

        try:
            actual_exit_code = int(inspected)
        except ValueError:
            return response  # unexpected inspect output shape -- don't guess, don't block

        if actual_exit_code != claimed_exit_code:
            return self._override(
                response,
                f"claimed container {container_id!r} exited {claimed_exit_code}, but it actually exited {actual_exit_code}",
            )
        return response  # verified: the claimed exit code is real, pass through unchanged
