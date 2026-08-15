import asyncio
import sqlite3
import subprocess

import pytest
from devmcp.context import ServerContext
from devmcp.tools.docker_build_image import DockerBuildImageTool
from devmcp.tools.docker_run_container import DockerRunContainerTool
from devmcp.tools.git_branch import GitBranchTool
from devmcp.tools.git_commit import GitCommitTool
from devmcp.tools.run_ci_pipeline import RunCiPipelineTool
from devmcp.tools.sqlite_create_table import SqliteCreateTableTool
from devmcp.tools.sqlite_insert_row import SqliteInsertRowTool
from devmcp.tools.write_file import WriteFileTool

pytestmark_docker = pytest.mark.skipif(
    subprocess.run(["docker", "info"], capture_output=True, check=False).returncode != 0,
    reason="docker daemon not available",
)


async def test_write_file_tool_writes_real_file(tmp_git_repo):
    ctx = ServerContext(repo_root=tmp_git_repo, connection=None)

    result = await WriteFileTool().call({"path": "hello.txt", "content": "hi"}, ctx)

    assert result["isError"] is False
    assert (tmp_git_repo / "hello.txt").read_text() == "hi"


@pytest.mark.parametrize("escaping_path", [
    "C:/Windows/System32/evil.txt",  # Windows absolute path, real bug found via VS Code testing:
    # devmcp silently dropped the drive letter and nested the rest under repo_root instead of
    # rejecting it -- e.g. a client asking to write "C:/foo/bar.txt" landed at
    # "<repo_root>/foo/bar.txt", not "C:/foo/bar.txt" and not an error either.
    "/etc/passwd",  # POSIX-style absolute path
    "../../outside.txt",  # relative traversal above repo_root
])
async def test_write_file_tool_rejects_paths_outside_repo_root(tmp_git_repo, escaping_path):
    ctx = ServerContext(repo_root=tmp_git_repo, connection=None)

    result = await WriteFileTool().call({"path": escaping_path, "content": "hi"}, ctx)

    assert result["isError"] is True


async def test_git_commit_tool_creates_real_commit(tmp_git_repo):
    ctx = ServerContext(repo_root=tmp_git_repo, connection=None)
    await WriteFileTool().call({"path": "hello.txt", "content": "hi"}, ctx)

    result = await GitCommitTool().call({"message": "add hello", "files": ["hello.txt"]}, ctx)

    assert result["isError"] is False
    commit_hash = result["structuredContent"]["commit_hash"]
    actual_head_proc = await asyncio.to_thread(
        subprocess.run,
        ["git", "-C", str(tmp_git_repo), "log", "-1", "--format=%H"],
        capture_output=True, text=True, check=False,
    )
    assert actual_head_proc.stdout.strip() == commit_hash


async def test_git_branch_tool_creates_real_branch(tmp_git_repo):
    ctx = ServerContext(repo_root=tmp_git_repo, connection=None)
    await WriteFileTool().call({"path": "hello.txt", "content": "hi"}, ctx)
    await GitCommitTool().call({"message": "add hello", "files": ["hello.txt"]}, ctx)

    result = await GitBranchTool().call({"name": "feature-x"}, ctx)

    assert result["isError"] is False
    actual_ref_proc = await asyncio.to_thread(
        subprocess.run,
        ["git", "-C", str(tmp_git_repo), "rev-parse", "feature-x"],
        capture_output=True, text=True, check=False,
    )
    assert actual_ref_proc.stdout.strip() == result["structuredContent"]["commit_hash"]


async def test_run_ci_pipeline_reports_real_exit_codes(tmp_git_repo):
    ctx = ServerContext(repo_root=tmp_git_repo, connection=None)
    steps = [
        {"name": "ok-step", "cmd": "exit 0", "idempotent": True},
        {"name": "fail-step", "cmd": "exit 1", "idempotent": True},
    ]

    result = await RunCiPipelineTool().call({"steps": steps}, ctx)

    assert result["isError"] is True  # one step failed
    reported = result["structuredContent"]["steps"]
    assert reported[0]["passed"] is True
    assert reported[1]["passed"] is False
    assert reported[1]["exit_code"] == 1


async def test_sqlite_create_table_tool_creates_real_table(tmp_git_repo):
    ctx = ServerContext(repo_root=tmp_git_repo, connection=None)

    result = await SqliteCreateTableTool().call(
        {"db_path": "app.db", "table": "users", "columns": {"id": "INTEGER PRIMARY KEY", "name": "TEXT"}}, ctx
    )

    assert result["isError"] is False
    conn = sqlite3.connect(tmp_git_repo / "app.db")
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
    finally:
        conn.close()
    assert row is not None


async def test_sqlite_insert_row_tool_inserts_real_row(tmp_git_repo):
    ctx = ServerContext(repo_root=tmp_git_repo, connection=None)
    await SqliteCreateTableTool().call(
        {"db_path": "app.db", "table": "users", "columns": {"id": "INTEGER PRIMARY KEY", "name": "TEXT"}}, ctx
    )

    result = await SqliteInsertRowTool().call(
        {"db_path": "app.db", "table": "users", "values": {"id": 1, "name": "rudra"}}, ctx
    )

    assert result["isError"] is False
    conn = sqlite3.connect(tmp_git_repo / "app.db")
    try:
        row = conn.execute("SELECT name FROM users WHERE id=1").fetchone()
    finally:
        conn.close()
    assert row == ("rudra",)


@pytest.mark.parametrize("escaping_path", [
    "C:/Windows/System32/evil.db",
    "/etc/evil.db",
    "../../outside.db",
])
async def test_sqlite_create_table_tool_rejects_paths_outside_repo_root(tmp_git_repo, escaping_path):
    ctx = ServerContext(repo_root=tmp_git_repo, connection=None)

    result = await SqliteCreateTableTool().call(
        {"db_path": escaping_path, "table": "users", "columns": {"id": "INTEGER"}}, ctx
    )

    assert result["isError"] is True


@pytest.mark.parametrize("bad_identifier", [
    "users; DROP TABLE x",
    "users--",
    "1users",
])
async def test_sqlite_create_table_tool_rejects_unsafe_table_name(tmp_git_repo, bad_identifier):
    ctx = ServerContext(repo_root=tmp_git_repo, connection=None)

    result = await SqliteCreateTableTool().call(
        {"db_path": "app.db", "table": bad_identifier, "columns": {"id": "INTEGER"}}, ctx
    )

    assert result["isError"] is True


@pytestmark_docker
async def test_docker_build_image_tool_builds_real_image(tmp_git_repo):
    (tmp_git_repo / "Dockerfile").write_text('FROM busybox\nCMD ["echo", "hi"]\n')
    ctx = ServerContext(repo_root=tmp_git_repo, connection=None)
    tag = "devmcp-test-build-image"

    try:
        result = await DockerBuildImageTool().call(
            {"dockerfile_path": "Dockerfile", "context_path": ".", "tag": tag}, ctx
        )

        assert result["isError"] is False
        inspected = subprocess.run(["docker", "image", "inspect", tag], capture_output=True, check=False)
        assert inspected.returncode == 0
    finally:
        subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, check=False)


@pytestmark_docker
async def test_docker_run_container_tool_reports_real_exit_code(tmp_git_repo):
    # Runs an already-built local tag rather than a bare "busybox" reference:
    # `docker run` on an image not yet in the local store falls back to a
    # registry pull, which hits a real, reproducible TLS negotiation error
    # talking to Docker Hub on this machine ("tls: protocol version not
    # supported") -- a genuine environment issue distinct from the buildx
    # pull path `docker build` uses successfully. Building first sidesteps
    # it entirely: `docker run` on a local-only tag needs no network at all.
    (tmp_git_repo / "Dockerfile").write_text('FROM busybox\nCMD ["sh", "-c", "exit 7"]\n')
    ctx = ServerContext(repo_root=tmp_git_repo, connection=None)
    tag = "devmcp-test-run-container"
    build_result = await DockerBuildImageTool().call(
        {"dockerfile_path": "Dockerfile", "context_path": ".", "tag": tag}, ctx
    )
    assert build_result["isError"] is False

    try:
        result = await DockerRunContainerTool().call({"image": tag}, ctx)

        assert result["isError"] is True  # nonzero exit code
        container_id = result["structuredContent"]["container_id"]
        assert result["structuredContent"]["exit_code"] == 7
        try:
            inspected = subprocess.run(
                ["docker", "container", "inspect", "--format", "{{.State.ExitCode}}", container_id],
                capture_output=True, text=True, check=False,
            )
            assert inspected.stdout.strip() == "7"
        finally:
            subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, check=False)
    finally:
        subprocess.run(["docker", "rmi", "-f", tag], capture_output=True, check=False)
