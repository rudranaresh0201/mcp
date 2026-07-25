from typing import Any, ClassVar

from devmcp import git_ops
from devmcp.context import ServerContext
from devmcp.tools.base import Tool


class GitBranchTool(Tool):
    name = "git_branch"
    description = "Create a branch pointing at a ref (defaults to HEAD)."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "from_ref": {"type": "string"},
        },
        "required": ["name"],
    }

    async def call(self, arguments: dict[str, Any], ctx: ServerContext) -> dict[str, Any]:
        name = arguments["name"]
        from_ref = arguments.get("from_ref", "HEAD")
        result = git_ops.create_branch(ctx.repo_root, name, from_ref)
        return {
            "isError": False,
            "content": [{"type": "text", "text": f"created branch {name} at {result['commit_hash'][:12]}"}],
            "structuredContent": result,
        }
