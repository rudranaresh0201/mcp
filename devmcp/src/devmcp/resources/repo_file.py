from typing import Any

from devmcp import git_ops
from devmcp.context import ServerContext
from devmcp.resources.base import Resource

URI_PREFIX = "repo://file/"


class RepoFileResource(Resource):
    name = "repo-file"
    description = "Contents of a file in the repo, e.g. repo://file/path/to/file.txt"

    def matches(self, uri: str) -> bool:
        return uri.startswith(URI_PREFIX)

    async def read(self, uri: str, ctx: ServerContext) -> dict[str, Any]:
        path = uri.removeprefix(URI_PREFIX)
        text = git_ops.read_file(ctx.repo_root, path)
        return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": text}]}
