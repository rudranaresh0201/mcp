from abc import ABC, abstractmethod
from typing import Any, ClassVar

from devmcp.context import ServerContext


class Prompt(ABC):
    name: str
    description: str = ""
    arguments: ClassVar[list[dict[str, Any]]] = []

    @abstractmethod
    async def get(self, arguments: dict[str, Any], ctx: ServerContext) -> dict[str, Any]:
        """Return {"description", "messages": [{"role", "content"}]}."""
