import sqlite3
from typing import Any, ClassVar

from devmcp import sqlite_ops
from devmcp.context import ServerContext
from devmcp.tools.base import Tool


class SqliteInsertRowTool(Tool):
    name = "sqlite_insert_row"
    description = "Insert one row into a table in a sqlite database file in the repo."
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "db_path": {"type": "string"},
            "table": {"type": "string"},
            "values": {"type": "object"},
        },
        "required": ["db_path", "table", "values"],
    }
    output_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "db_path": {"type": "string"},
            "table": {"type": "string"},
            "values": {"type": "object"},
        },
        "required": ["db_path", "table", "values"],
    }

    async def call(self, arguments: dict[str, Any], ctx: ServerContext) -> dict[str, Any]:
        db_path = arguments["db_path"]
        table = arguments["table"]
        values = arguments["values"]
        try:
            result = sqlite_ops.insert_row(ctx.repo_root, db_path, table, values)
        except ValueError as exc:
            return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
        except sqlite3.OperationalError as exc:
            return {"isError": True, "content": [{"type": "text", "text": f"sqlite error: {exc}"}]}
        return {
            "isError": False,
            "content": [{"type": "text", "text": f"inserted row into {table!r} in {db_path}"}],
            "structuredContent": result,
        }
