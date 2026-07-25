import json
from typing import Any

from devmcp.context import ServerContext
from devmcp.resources.base import Resource


class CiLastRunResource(Resource):
    uri = "ci://last-run"
    name = "ci-last-run"
    description = "Result of the most recent run_ci_pipeline call."

    async def read(self, uri: str, ctx: ServerContext) -> dict[str, Any]:
        text = json.dumps(ctx.last_ci_run) if ctx.last_ci_run is not None else "null"
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": text}]}
