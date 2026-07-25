from abc import ABC, abstractmethod
from typing import Any

from devmcp.context import ServerContext


class Resource(ABC):
    uri: str
    name: str
    description: str = ""
    mime_type: str = "text/plain"

    def matches(self, uri: str) -> bool:
        return uri == self.uri

    @abstractmethod
    async def read(self, uri: str, ctx: ServerContext) -> dict[str, Any]:
        """Return MCP resource contents: {"contents": [{"uri", "mimeType", "text"}]}."""
