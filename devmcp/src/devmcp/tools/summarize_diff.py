from typing import Any, ClassVar

from devmcp.context import ServerContext
from devmcp.sampling import SamplingClient
from devmcp.tools.base import Tool


class SummarizeDiffTool(Tool):
    name = "summarize_diff"
    description = "Ask the client's own model to summarize a diff in plain English."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"diff": {"type": "string"}},
        "required": ["diff"],
    }

    async def call(self, arguments: dict[str, Any], ctx: ServerContext) -> dict[str, Any]:
        if ctx.connection is None:
            return {"isError": True, "content": [{"type": "text", "text": "no connection to request sampling on"}]}
        diff = arguments["diff"]
        summary = await SamplingClient().create_message(
            ctx.connection,
            messages=[
                {"role": "user", "content": {"type": "text", "text": f"Summarize this diff in one sentence:\n\n{diff}"}}
            ],
        )
        return {"isError": False, "content": [{"type": "text", "text": summary}]}
