from typing import Any, ClassVar

from devmcp.context import ServerContext
from devmcp.prompts.base import Prompt


class PrDescriptionPrompt(Prompt):
    name = "pr-description"
    description = "Draft a PR description summarizing a set of commits."
    arguments: ClassVar[list[dict[str, Any]]] = [
        {"name": "commits", "description": "Commit log to summarize", "required": True},
    ]

    async def get(self, arguments: dict[str, Any], ctx: ServerContext) -> dict[str, Any]:
        commits = arguments.get("commits", "")
        text = f"Write a PR description summarizing these commits:\n\n{commits}"
        return {
            "description": self.description,
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
        }
