from abc import ABC, abstractmethod
from typing import Any, ClassVar

from devmcp.context import ServerContext


class Tool(ABC):
    name: str
    description: str = ""
    input_schema: ClassVar[dict[str, Any]] = {}

    @abstractmethod
    async def call(self, arguments: dict[str, Any], ctx: ServerContext) -> dict[str, Any]:
        """Return an MCP tool result: {"isError": bool, "content": [...]}."""
