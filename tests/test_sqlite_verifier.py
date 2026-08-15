"""Unit-level: exercise SQLiteVerifier directly against a real (tiny) sqlite
database -- no subprocess-pair needed to prove the ground-truth check itself."""
import sqlite3
from pathlib import Path

from verimcp.verifiers.sqlite import SQLiteVerifier


def _make_db_with_users_table(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)")
        conn.commit()
    finally:
        conn.close()


def _create_table_request(table: str) -> dict:
    return {"params": {"name": "sqlite_create_table", "arguments": {"db_path": "app.db", "table": table, "columns": {"id": "INTEGER"}}}}


def _insert_row_request(table: str, values: dict) -> dict:
    return {"params": {"name": "sqlite_insert_row", "arguments": {"db_path": "app.db", "table": table, "values": values}}}


def _ok_response() -> dict:
    return {"result": {"isError": False}}


def test_passes_through_a_real_table(tmp_path: Path):
    _make_db_with_users_table(tmp_path / "app.db")
    verifier = SQLiteVerifier()

    result = verifier.verify(_create_table_request("users"), _ok_response(), root=tmp_path)

    assert result["result"]["isError"] is False


def test_catches_a_fabricated_table(tmp_path: Path):
    """The real-world lie this verifier exists for: a backend claims a table
    was created that was never actually created in this database."""
    _make_db_with_users_table(tmp_path / "app.db")
    verifier = SQLiteVerifier()

    result = verifier.verify(_create_table_request("never_created"), _ok_response(), root=tmp_path)

    assert result["result"]["isError"] is True
    assert "does not exist" in result["result"]["content"][0]["text"]


def test_passes_through_a_real_row(tmp_path: Path):
    db_path = tmp_path / "app.db"
    _make_db_with_users_table(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT INTO users (id, name) VALUES (1, 'rudra')")
        conn.commit()
    finally:
        conn.close()
    verifier = SQLiteVerifier()

    result = verifier.verify(_insert_row_request("users", {"id": 1, "name": "rudra"}), _ok_response(), root=tmp_path)

    assert result["result"]["isError"] is False


def test_catches_a_fabricated_row(tmp_path: Path):
    """The real-world lie this verifier exists for: a backend claims a row
    was inserted that was never actually written to the table."""
    _make_db_with_users_table(tmp_path / "app.db")
    verifier = SQLiteVerifier()

    result = verifier.verify(_insert_row_request("users", {"id": 99, "name": "nobody"}), _ok_response(), root=tmp_path)

    assert result["result"]["isError"] is True
    assert "no matching row" in result["result"]["content"][0]["text"]


def test_catches_insert_claim_against_nonexistent_table(tmp_path: Path):
    (tmp_path / "app.db").touch()  # a real, but empty, database file
    verifier = SQLiteVerifier()

    result = verifier.verify(_insert_row_request("users", {"id": 1, "name": "rudra"}), _ok_response(), root=tmp_path)

    assert result["result"]["isError"] is True


def test_fails_open_when_root_is_unknown(tmp_path: Path):
    _make_db_with_users_table(tmp_path / "app.db")
    verifier = SQLiteVerifier()

    result = verifier.verify(_create_table_request("users"), _ok_response(), root=None)

    assert result["result"]["isError"] is False
