from abc import ABC, abstractmethod
from typing import Any, ClassVar

from devmcp.context import ServerContext


class Tool(ABC):
    name: str
    description: str = ""
    input_schema: ClassVar[dict[str, Any]] = {}
    # Optional: the JSON Schema structuredContent must conform to, per the
    # MCP spec's tools/outputSchema ("if provided, servers MUST provide
    # structured results that conform to this schema"). None means "not
    # declared" -- a tool with no fixed structuredContent shape (write_file,
    # summarize_diff) leaves this unset rather than declaring an empty one.
    output_schema: ClassVar[dict[str, Any] | None] = None

    @abstractmethod
    async def call(self, arguments: dict[str, Any], ctx: ServerContext) -> dict[str, Any]:
        """Return an MCP tool result: {"isError": bool, "content": [...]}."""
