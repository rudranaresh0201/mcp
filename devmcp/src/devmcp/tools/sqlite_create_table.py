from typing import Any, ClassVar

from devmcp import sqlite_ops
from devmcp.context import ServerContext
from devmcp.tools.base import Tool


class SqliteCreateTableTool(Tool):
    name = "sqlite_create_table"
    description = "Create a table (CREATE TABLE IF NOT EXISTS) in a sqlite database file in the repo."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "db_path": {"type": "string"},
            "table": {"type": "string"},
            "columns": {"type": "object", "additionalProperties": {"type": "string"}},
        },
        "required": ["db_path", "table", "columns"],
    }

    async def call(self, arguments: dict[str, Any], ctx: ServerContext) -> dict[str, Any]:
        db_path = arguments["db_path"]
        table = arguments["table"]
        columns = arguments["columns"]
        try:
            result = sqlite_ops.create_table(ctx.repo_root, db_path, table, columns)
        except ValueError as exc:
            return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
        return {
            "isError": False,
            "content": [{"type": "text", "text": f"created table {table!r} in {db_path}"}],
            "structuredContent": result,
        }
