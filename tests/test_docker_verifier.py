"""Unit-level: exercise DockerVerifier directly against real docker state --
no subprocess-pair needed to prove the ground-truth check itself, same
approach test_git_commit_verifier.py already uses for `git cat-file`."""
import subprocess

import pytest

from verimcp.verifiers.docker import DockerVerifier

pytestmark = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True, check=False).returncode != 0,
    reason="docker daemon not available",
)


def _run(*args: str) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture
def real_image(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text('FROM busybox\nCMD ["sh", "-c", "exit 3"]\n')
    tag = "verimcp-test-docker-verifier"
    _run("build", "-f", str(dockerfile), "-t", tag, str(tmp_path))
    yield tag
    subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, check=False)


@pytest.fixture
def real_exited_container(real_image):
    container_id = _run("run", "-d", real_image)
    _run("wait", container_id)
    yield container_id
    subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, check=False)


def _build_response(tag: str) -> dict:
    return {"result": {"isError": False, "structuredContent": {"tag": tag}}}


def _run_response(container_id: str, exit_code: int) -> dict:
    return {"result": {"isError": exit_code != 0, "structuredContent": {"container_id": container_id, "exit_code": exit_code}}}


def _build_request() -> dict:
    return {"params": {"name": "docker_build_image", "arguments": {}}}


def _run_request() -> dict:
    return {"params": {"name": "docker_run_container", "arguments": {}}}


def test_passes_through_a_real_image(real_image):
    verifier = DockerVerifier()

    result = verifier.verify(_build_request(), _build_response(real_image))

    assert result["result"]["isError"] is False


def test_catches_a_fabricated_image_tag():
    """The real-world lie this verifier exists for: a backend claims a
    build succeeded and names a tag that was never actually created."""
    verifier = DockerVerifier()

    result = verifier.verify(_build_request(), _build_response("verimcp-never-built-this-tag"))

    assert result["result"]["isError"] is True
    assert "does not exist" in result["result"]["content"][0]["text"]


def test_passes_through_a_real_container_with_matching_exit_code(real_exited_container):
    verifier = DockerVerifier()

    result = verifier.verify(_run_request(), _run_response(real_exited_container, 3))

    assert result["result"]["isError"] is True  # nonzero exit code is still a verified, real claim


def test_catches_a_fabricated_exit_code(real_exited_container):
    """The real-world lie this verifier exists for: a backend reports the
    container exited 0 when it actually exited nonzero (or vice versa)."""
    verifier = DockerVerifier()

    result = verifier.verify(_run_request(), _run_response(real_exited_container, 0))

    assert result["result"]["isError"] is True
    assert "actually exited 3" in result["result"]["content"][0]["text"]


def test_catches_a_fabricated_container_id():
    verifier = DockerVerifier()

    result = verifier.verify(_run_request(), _run_response("deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef", 0))

    assert result["result"]["isError"] is True
    assert "does not exist" in result["result"]["content"][0]["text"]
