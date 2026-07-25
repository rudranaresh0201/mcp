"""Server-initiated sampling: devmcp asks the Client's own LLM to run a
completion on its behalf, instead of needing its own model access."""
from typing import Any

from devmcp.connection import Connection


class SamplingClient:
    async def create_message(self, connection: Connection, messages: list[dict[str, Any]], max_tokens: int = 512) -> str:
        result = await connection.request(
            "sampling/createMessage",
            {"messages": messages, "maxTokens": max_tokens},
        )
        return result.get("content", {}).get("text", "")
