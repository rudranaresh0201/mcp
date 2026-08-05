from typing import Any, ClassVar

from devmcp import git_ops
from devmcp.context import ServerContext
from devmcp.tools.base import Tool


class WriteFileTool(Tool):
    name = "write_file"
    description = "Write content to a file in the repo (not staged or committed)."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }

    async def call(self, arguments: dict[str, Any], ctx: ServerContext) -> dict[str, Any]:
        path = arguments["path"]
        content = arguments["content"]
        try:
            git_ops.write_file(ctx.repo_root, path, content)
        except ValueError as exc:
            return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
        return {
            "isError": False,
            "content": [{"type": "text", "text": f"wrote {len(content)} bytes to {path}"}],
        }
