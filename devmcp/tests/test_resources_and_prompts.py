from devmcp.context import ServerContext
from devmcp.prompts.commit_message import CommitMessagePrompt
from devmcp.resources.ci_last_run import CiLastRunResource
from devmcp.resources.repo_file import RepoFileResource
from devmcp.resources.repo_status import RepoStatusResource
from devmcp.tools.run_ci_pipeline import RunCiPipelineTool
from devmcp.tools.write_file import WriteFileTool


async def test_repo_status_resource_reflects_real_state(tmp_git_repo):
    ctx = ServerContext(repo_root=tmp_git_repo, connection=None)
    await WriteFileTool().call({"path": "a.txt", "content": "x"}, ctx)

    result = await RepoStatusResource().read("repo://status", ctx)

    assert "a.txt" in result["contents"][0]["text"]


async def test_repo_file_resource_reads_real_content(tmp_git_repo):
    ctx = ServerContext(repo_root=tmp_git_repo, connection=None)
    await WriteFileTool().call({"path": "a.txt", "content": "hello"}, ctx)

    result = await RepoFileResource().read("repo://file/a.txt", ctx)

    assert result["contents"][0]["text"] == "hello"


async def test_ci_last_run_resource_reflects_real_run(tmp_git_repo):
    ctx = ServerContext(repo_root=tmp_git_repo, connection=None)
    await RunCiPipelineTool().call({"steps": [{"name": "ok", "cmd": "exit 0"}]}, ctx)

    result = await CiLastRunResource().read("ci://last-run", ctx)

    assert "ok" in result["contents"][0]["text"]
    assert ctx.last_ci_run["passed"] is True


async def test_commit_message_prompt_includes_diff(tmp_git_repo):
    ctx = ServerContext(repo_root=tmp_git_repo, connection=None)

    result = await CommitMessagePrompt().get({"diff": "+added a line"}, ctx)

    assert "+added a line" in result["messages"][0]["content"]["text"]
