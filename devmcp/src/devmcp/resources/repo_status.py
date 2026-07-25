from typing import Any

from devmcp import git_ops
from devmcp.context import ServerContext
from devmcp.resources.base import Resource


class RepoStatusResource(Resource):
    uri = "repo://status"
    name = "repo-status"
    description = "Short git status of the repo."

    async def read(self, uri: str, ctx: ServerContext) -> dict[str, Any]:
        text = git_ops.status(ctx.repo_root)
        return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": text}]}
