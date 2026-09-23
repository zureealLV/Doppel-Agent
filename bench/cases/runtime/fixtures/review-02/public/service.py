"""Document search service with tenant-scoped results."""

import sqlite3


def create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, owner_id TEXT, title TEXT)"
    )


def search_documents(connection: sqlite3.Connection, owner_id: str, term: str) -> list[tuple]:
    sql = (
        "SELECT id, owner_id, title FROM documents "
        f"WHERE owner_id = '{owner_id}' AND title LIKE '%{term}%'"
    )
    return connection.execute(sql).fetchall()
