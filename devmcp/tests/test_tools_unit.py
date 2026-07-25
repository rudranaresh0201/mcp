import asyncio
import subprocess

from devmcp.context import ServerContext
from devmcp.tools.git_branch import GitBranchTool
from devmcp.tools.git_commit import GitCommitTool
from devmcp.tools.run_ci_pipeline import RunCiPipelineTool
from devmcp.tools.write_file import WriteFileTool


async def test_write_file_tool_writes_real_file(tmp_git_repo):
    ctx = ServerContext(repo_root=tmp_git_repo, connection=None)

    result = await WriteFileTool().call({"path": "hello.txt", "content": "hi"}, ctx)

    assert result["isError"] is False
    assert (tmp_git_repo / "hello.txt").read_text() == "hi"


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
