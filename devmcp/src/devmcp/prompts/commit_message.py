from typing import Any, ClassVar

from devmcp.context import ServerContext
from devmcp.prompts.base import Prompt


class CommitMessagePrompt(Prompt):
    name = "commit-message"
    description = "Draft a conventional-commit message for a diff."
    arguments: ClassVar[list[dict[str, Any]]] = [
        {"name": "diff", "description": "The diff to summarize", "required": True},
    ]

    async def get(self, arguments: dict[str, Any], ctx: ServerContext) -> dict[str, Any]:
        diff = arguments.get("diff", "")
        text = f"Write a conventional-commit message for this diff:\n\n{diff}"
        return {
            "description": self.description,
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
        }
