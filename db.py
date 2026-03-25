"""SQLite database helper for AuraFS smart connections."""
import sqlite3
import os


DB_PATH = os.path.join(os.path.dirname(__file__), "data", "aurafs.db")


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS connections (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                file1    TEXT NOT NULL,
                file2    TEXT NOT NULL,
                relation TEXT NOT NULL DEFAULT 'related',
                status   TEXT NOT NULL DEFAULT 'pending',
                UNIQUE(file1, file2)
            )
            """
        )
    conn.close()


def upsert_pending(file1, file2, relation="related"):
    """Insert a pending connection if it does not already exist (any status)."""
    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO connections (file1, file2, relation, status)
            VALUES (?, ?, ?, 'pending')
            """,
            (file1, file2, relation),
        )
    conn.close()


def is_rejected(file1, file2):
    conn = get_connection()
    row = conn.execute(
        """SELECT status FROM connections
           WHERE (file1=? AND file2=?) OR (file1=? AND file2=?)""",
        (file1, file2, file2, file1),
    ).fetchone()
    conn.close()
    return row is not None and row["status"] == "rejected"


def get_pending_recommendations():
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, file1, file2, relation FROM connections WHERE status='pending'"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def set_status(rec_id, status):
    conn = get_connection()
    with conn:
        conn.execute(
            "UPDATE connections SET status=? WHERE id=?",
            (status, rec_id),
        )
    conn.close()


def get_connected_files(filename):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT file1, file2, relation FROM connections
        WHERE status='accepted' AND (file1=? OR file2=?)
        """,
        (filename, filename),
    ).fetchall()
    conn.close()
    results = []
    for r in rows:
        other = r["file2"] if r["file1"] == filename else r["file1"]
        results.append({"file": other, "relation": r["relation"]})
    return results
