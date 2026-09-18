"""A small note service used only for an isolated code-review exercise."""

from pathlib import Path
import sqlite3


def create_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE notes (id INTEGER PRIMARY KEY, owner_id TEXT NOT NULL, "
        "title TEXT NOT NULL, body TEXT NOT NULL)"
    )


def fetch_note(connection: sqlite3.Connection, owner_id: str, note_id: int) -> dict | None:
    row = connection.execute(
        "SELECT id, owner_id, title, body FROM notes WHERE id = ?", (note_id,)
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "owner_id": row[1], "title": row[2], "body": row[3]}


def search_notes(connection: sqlite3.Connection, owner_id: str, query: str) -> list[tuple]:
    sql = (
        "SELECT id, owner_id, title FROM notes "
        f"WHERE owner_id = ? AND title LIKE '%{query}%'"
    )
    return connection.execute(sql, (owner_id,)).fetchall()


def save_attachment(root: Path, filename: str, payload: bytes) -> Path:
    target = root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def update_note_title(
    connection: sqlite3.Connection, owner_id: str, note_id: int, new_title: str
) -> bool:
    cursor = connection.execute(
        "UPDATE notes SET title = ? WHERE id = ? AND owner_id = ?",
        (new_title, note_id, owner_id),
    )
    return cursor.rowcount == 1
