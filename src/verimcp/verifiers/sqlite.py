"""SQLite domain verifier: checks that sqlite_create_table/sqlite_insert_row
actually produced the table/row they claim, by reconnecting to the real .db
file with a fresh connection -- same "don't trust the same actor's own
report, re-derive it" principle as FilesystemVerifier (content hash) and
GitCommitVerifier (git cat-file), just for relational state instead of a
file or a repo history.
"""
import re
import sqlite3
from pathlib import Path
from typing import Any

from verimcp.verifiers.base import Verifier

CREATE_TABLE_TOOLS = {"sqlite_create_table"}
INSERT_ROW_TOOLS = {"sqlite_insert_row"}

# Table/column names can't be parameterized in SQL -- same allowlist devmcp's
# own sqlite_ops.py enforces on the write side. The verifier must not trust
# an unvalidated identifier either: silently accepting one here would turn
# the ground-truth check itself into a second injection point.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _is_valid_identifier(name: str) -> bool:
    return bool(_IDENTIFIER_RE.match(name))


class SQLiteVerifier(Verifier):
    def applies_to(self, tool_name: str) -> bool:
        return tool_name in CREATE_TABLE_TOOLS or tool_name in INSERT_ROW_TOOLS

    def verify(self, request: dict[str, Any], response: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
        result = response.get("result", {})
        if result.get("isError"):
            return response  # backend already reported failure, nothing to add

        if root is None:
            return response  # can't tell where the backend actually wrote this -- don't guess, don't block

        args = request.get("params", {}).get("arguments", {})
        tool_name = request.get("params", {}).get("name")
        db_path = args.get("db_path")
        table = args.get("table")
        if db_path is None or table is None or not _is_valid_identifier(table):
            return response  # not a shape we know how to check

        actual_db_path = Path(root) / db_path
        if not actual_db_path.exists():
            return self._override(response, f"claimed write to db {db_path!r} succeeded, but the database file does not exist")

        if tool_name in CREATE_TABLE_TOOLS:
            return self._verify_create_table(response, actual_db_path, table)
        return self._verify_insert_row(response, actual_db_path, table, args.get("values"))

    def _verify_create_table(self, response: dict[str, Any], db_path: Path, table: str) -> dict[str, Any]:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return self._override(response, f"claimed table {table!r} does not exist in {db_path.name}")
        return response  # verified: the claimed table is real, pass through unchanged

    def _verify_insert_row(self, response: dict[str, Any], db_path: Path, table: str, values: dict | None) -> dict[str, Any]:
        if not values or not all(_is_valid_identifier(col) for col in values):
            return response  # not a shape we know how to check

        where_clause = " AND ".join(f"{col}=?" for col in values)
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                f"SELECT 1 FROM {table} WHERE {where_clause}", list(values.values())
            ).fetchone()
        except sqlite3.OperationalError:
            return self._override(response, f"claimed row insert into {table!r} could not be verified -- table does not exist in {db_path.name}")
        finally:
            conn.close()

        if row is None:
            return self._override(response, f"claimed row insert into {table!r} succeeded, but no matching row exists ({values!r})")
        return response  # verified: the claimed row is real, pass through unchanged
