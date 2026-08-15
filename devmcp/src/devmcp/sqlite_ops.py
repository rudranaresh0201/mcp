"""Real sqlite3 side effects -- no mocking, same principle as git_ops.py.
stdlib sqlite3 directly, not subprocess, since there's no external binary
involved (unlike git).
"""
import re
import sqlite3
from pathlib import Path

from devmcp.git_ops import resolve_safe_path

# Table/column names can't be parameterized in SQL (only values can), so any
# identifier that gets string-interpolated into a query must be validated
# against an allowlist first -- otherwise a client could smuggle SQL through
# a "table" or "column name" argument (e.g. "users; DROP TABLE x"). This is
# the sqlite domain's equivalent of git_ops.resolve_safe_path's path-escape
# check: a real injection boundary, not decoration.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(name: str) -> None:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"{name!r} is not a valid SQL identifier")


def create_table(repo_root: Path, db_path: str, table: str, columns: dict[str, str]) -> dict:
    _validate_identifier(table)
    for column in columns:
        _validate_identifier(column)
    resolved = resolve_safe_path(repo_root, db_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)

    column_defs = ", ".join(f"{name} {type_}" for name, type_ in columns.items())
    conn = sqlite3.connect(resolved)
    try:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({column_defs})")
        conn.commit()
    finally:
        conn.close()
    return {"db_path": db_path, "table": table, "columns": columns}


def insert_row(repo_root: Path, db_path: str, table: str, values: dict) -> dict:
    _validate_identifier(table)
    for column in values:
        _validate_identifier(column)
    resolved = resolve_safe_path(repo_root, db_path)

    columns = ", ".join(values.keys())
    placeholders = ", ".join("?" for _ in values)
    conn = sqlite3.connect(resolved)
    try:
        conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", list(values.values()))
        conn.commit()
    finally:
        conn.close()
    return {"db_path": db_path, "table": table, "values": values}
