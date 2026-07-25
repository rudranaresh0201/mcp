import json
from typing import Any

from devmcp import git_ops
from devmcp.context import ServerContext
from devmcp.resources.base import Resource


class RepoLogResource(Resource):
    uri = "repo://log"
    name = "repo-log"
    description = "Recent commit history of the repo."

    async def read(self, uri: str, ctx: ServerContext) -> dict[str, Any]:
        entries = git_ops.log(ctx.repo_root)
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(entries)}]}
