"""Document lookup service used by a small multi-user application."""

import sqlite3


def create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE documents (id INTEGER PRIMARY KEY, owner_id TEXT, body TEXT)"
    )


def get_document(connection: sqlite3.Connection, requester_id: str, document_id: int) -> str | None:
    row = connection.execute(
        "SELECT body FROM documents WHERE id = ?", (document_id,)
    ).fetchone()
    return row[0] if row else None
