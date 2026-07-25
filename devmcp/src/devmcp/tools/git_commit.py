from typing import Any, ClassVar

from devmcp import git_ops
from devmcp.context import ServerContext
from devmcp.tools.base import Tool


class GitCommitTool(Tool):
    name = "git_commit"
    description = "Stage the given files and commit them with a message."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "files": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["message", "files"],
    }

    async def call(self, arguments: dict[str, Any], ctx: ServerContext) -> dict[str, Any]:
        message = arguments["message"]
        files = arguments["files"]
        result = git_ops.commit(ctx.repo_root, message, files)
        await ctx.subscriptions.notify_changed("repo://status", ctx.connection)
        await ctx.subscriptions.notify_changed("repo://log", ctx.connection)
        return {
            "isError": False,
            "content": [{"type": "text", "text": f"committed {result['commit_hash'][:12]}: {message}"}],
            "structuredContent": result,
        }
