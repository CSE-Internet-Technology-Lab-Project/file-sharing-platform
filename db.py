"""
Database layer — SQLite schema + every query helper used by the Master Tracker.

The database file lives at data/app.db and is created on first call to init_schema().
Thread-safety: we use check_same_thread=False and serialise writes with a module-level
threading.Lock so that Flask request threads and background threads can share one connection.
"""

import sqlite3
import threading
import time
import json
import uuid
import os
import hashlib

DB_PATH = os.path.join("data", "app.db")
_lock = threading.Lock()
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn"):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return _local.conn


def init_schema():
    """Create all tables if they don't exist."""
    conn = _get_conn()
    with _lock:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS files (
            id TEXT PRIMARY KEY,
            owner_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            chunk_size INTEGER NOT NULL,
            total_chunks INTEGER NOT NULL,
            checksum TEXT,
            status TEXT NOT NULL,
            current_version INTEGER DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS file_versions (
            id INTEGER PRIMARY KEY,
            file_id TEXT NOT NULL,
            version_no INTEGER NOT NULL,
            size_bytes INTEGER NOT NULL,
            checksum TEXT,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS file_acl (
            file_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            permission TEXT NOT NULL,
            PRIMARY KEY (file_id, user_id)
        );

        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            size_bytes INTEGER NOT NULL,
            checksum TEXT,
            status TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chunk_locations (
            chunk_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            role TEXT NOT NULL,
            stored_at REAL,
            PRIMARY KEY (chunk_id, role)
        );

        CREATE TABLE IF NOT EXISTS upload_sessions (
            id TEXT PRIMARY KEY,
            file_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            total_chunks INTEGER NOT NULL,
            chunks_received INTEGER DEFAULT 0,
            status TEXT NOT NULL,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS events_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            payload TEXT NOT NULL,
            ts REAL NOT NULL
        );
        """)
        conn.commit()


# ── User helpers ──

def create_user(username: str, password_hash: str) -> int:
    conn = _get_conn()
    with _lock:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, password_hash, time.time()),
        )
        conn.commit()
        return cur.lastrowid


def get_user_by_username(username: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


# ── File helpers ──

def create_file(file_id: str, owner_id: int, filename: str,
                size_bytes: int, chunk_size: int, total_chunks: int,
                checksum: str | None = None) -> str:
    conn = _get_conn()
    now = time.time()
    with _lock:
        conn.execute(
            """INSERT INTO files
               (id, owner_id, filename, size_bytes, chunk_size, total_chunks,
                checksum, status, current_version, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'uploading', 1, ?, ?)""",
            (file_id, owner_id, filename, size_bytes, chunk_size, total_chunks,
             checksum, now, now),
        )
        # ACL: owner entry
        conn.execute(
            "INSERT OR IGNORE INTO file_acl (file_id, user_id, permission) VALUES (?, ?, 'owner')",
            (file_id, owner_id),
        )
        conn.commit()
    return file_id


def get_file(file_id: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
    return dict(row) if row else None


def list_files_for_user(user_id: int) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        """SELECT f.id, f.filename, f.size_bytes, f.status, f.current_version
           FROM files f
           JOIN file_acl a ON f.id = a.file_id
           WHERE a.user_id = ?
           ORDER BY f.created_at DESC""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_file_status(file_id: str, status: str):
    conn = _get_conn()
    with _lock:
        conn.execute(
            "UPDATE files SET status = ?, updated_at = ? WHERE id = ?",
            (status, time.time(), file_id),
        )
        conn.commit()


def update_file_checksum(file_id: str, checksum: str):
    conn = _get_conn()
    with _lock:
        conn.execute(
            "UPDATE files SET checksum = ?, updated_at = ? WHERE id = ?",
            (checksum, time.time(), file_id),
        )
        conn.commit()


def delete_file(file_id: str):
    conn = _get_conn()
    with _lock:
        conn.execute("DELETE FROM chunk_locations WHERE chunk_id IN (SELECT id FROM chunks WHERE file_id = ?)", (file_id,))
        conn.execute("DELETE FROM chunks WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM file_acl WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM upload_sessions WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM file_versions WHERE file_id = ?", (file_id,))
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        conn.commit()


def get_file_status_counts() -> dict:
    """Return {total, available, degraded, uploading, failed}."""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM files GROUP BY status"
    ).fetchall()
    counts = {r["status"]: r["cnt"] for r in rows}
    total = sum(counts.values())
    return {
        "total": total,
        "available": counts.get("available", 0),
        "degraded": counts.get("degraded", 0),
        "uploading": counts.get("uploading", 0),
        "failed": counts.get("failed", 0),
    }


# ── ACL helpers ──

def set_acl(file_id: str, user_id: int, permission: str):
    conn = _get_conn()
    with _lock:
        conn.execute(
            "INSERT OR REPLACE INTO file_acl (file_id, user_id, permission) VALUES (?, ?, ?)",
            (file_id, user_id, permission),
        )
        conn.commit()


def get_acl(file_id: str, user_id: int) -> str | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT permission FROM file_acl WHERE file_id = ? AND user_id = ?",
        (file_id, user_id),
    ).fetchone()
    return row["permission"] if row else None


def check_permission(file_id: str, user_id: int, min_level: str) -> bool:
    """Check if user_id has at least *min_level* permission on the file.
    Hierarchy: owner > editor > viewer.
    """
    perm = get_acl(file_id, user_id)
    if perm is None:
        return False
    hierarchy = {"viewer": 0, "editor": 1, "owner": 2}
    return hierarchy.get(perm, -1) >= hierarchy.get(min_level, 99)


# ── Chunk helpers ──

def create_chunk(chunk_id: str, file_id: str, chunk_index: int, size_bytes: int):
    conn = _get_conn()
    with _lock:
        conn.execute(
            "INSERT INTO chunks (id, file_id, chunk_index, size_bytes, checksum, status) VALUES (?, ?, ?, ?, NULL, 'pending')",
            (chunk_id, file_id, chunk_index, size_bytes),
        )
        conn.commit()


def create_chunk_location(chunk_id: str, node_id: str, role: str):
    conn = _get_conn()
    with _lock:
        conn.execute(
            "INSERT OR REPLACE INTO chunk_locations (chunk_id, node_id, role, stored_at) VALUES (?, ?, ?, NULL)",
            (chunk_id, node_id, role),
        )
        conn.commit()


def get_chunk_id(file_id: str, chunk_index: int) -> str | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT id FROM chunks WHERE file_id = ? AND chunk_index = ?",
        (file_id, chunk_index),
    ).fetchone()
    return row["id"] if row else None


def mark_chunk_stored(file_id: str, chunk_idx: int, node_id: str,
                      role: str = "primary", checksum: str | None = None):
    """Called when a chunk.stored or chunk.replicated event arrives."""
    conn = _get_conn()
    with _lock:
        # Find chunk_id
        row = conn.execute(
            "SELECT id FROM chunks WHERE file_id = ? AND chunk_index = ?",
            (file_id, chunk_idx),
        ).fetchone()
        if not row:
            return
        chunk_id = row["id"]

        # Update chunk status + checksum
        if checksum:
            conn.execute(
                "UPDATE chunks SET status = 'stored', checksum = ? WHERE id = ?",
                (checksum, chunk_id),
            )

        # Upsert chunk_location
        conn.execute(
            """INSERT OR REPLACE INTO chunk_locations (chunk_id, node_id, role, stored_at)
               VALUES (?, ?, ?, ?)""",
            (chunk_id, node_id, role, time.time()),
        )
        conn.commit()


def mark_chunk_under_replicated(file_id: str, chunk_idx: int):
    """Mark the file as degraded when a replication attempt fails."""
    conn = _get_conn()
    with _lock:
        conn.execute(
            "UPDATE files SET status = 'degraded', updated_at = ? WHERE id = ? AND status = 'available'",
            (time.time(), file_id),
        )
        conn.commit()


def get_chunks(file_id: str) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM chunks WHERE file_id = ? ORDER BY chunk_index",
        (file_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_locations(chunk_id: str) -> list[dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM chunk_locations WHERE chunk_id = ? AND stored_at IS NOT NULL",
        (chunk_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_chunks_on_node(node_id: str) -> list[dict]:
    """Return all chunk_locations rows for a given node."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT cl.chunk_id, cl.node_id, cl.role, c.file_id, c.chunk_index
           FROM chunk_locations cl
           JOIN chunks c ON cl.chunk_id = c.id
           WHERE cl.node_id = ? AND cl.stored_at IS NOT NULL""",
        (node_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_missing_chunks(file_id: str) -> list[int]:
    """Return chunk_index values whose primary location has no stored_at timestamp."""
    conn = _get_conn()
    rows = conn.execute(
        """SELECT c.chunk_index FROM chunks c
           LEFT JOIN chunk_locations cl ON c.id = cl.chunk_id AND cl.role = 'primary'
           WHERE c.file_id = ? AND (cl.stored_at IS NULL OR cl.chunk_id IS NULL)
           ORDER BY c.chunk_index""",
        (file_id,),
    ).fetchall()
    return [r["chunk_index"] for r in rows]


def recompute_file_status(file_id: str):
    """
    Recompute file status based on chunk replication state:
    - 'available' if every chunk has ≥1 stored replica
    - 'degraded'  if some chunks have only 1 replica (but none lost)
    - 'failed'    if any chunk has 0 replicas
    """
    conn = _get_conn()
    chunks = get_chunks(file_id)
    if not chunks:
        return

    all_good = True
    any_lost = False
    for chunk in chunks:
        locs = get_locations(chunk["id"])
        if len(locs) == 0:
            any_lost = True
        elif len(locs) < 2:
            all_good = False

    if any_lost:
        new_status = "failed"
    elif all_good:
        new_status = "available"
    else:
        new_status = "degraded"

    with _lock:
        conn.execute(
            "UPDATE files SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, time.time(), file_id),
        )
        conn.commit()


def mark_node_chunks_lost(node_id: str):
    """Remove all chunk_locations for a dead node and mark affected files degraded."""
    conn = _get_conn()
    with _lock:
        # Find affected files before deleting
        affected = conn.execute(
            """SELECT DISTINCT c.file_id FROM chunk_locations cl
               JOIN chunks c ON cl.chunk_id = c.id
               WHERE cl.node_id = ?""",
            (node_id,),
        ).fetchall()
        affected_file_ids = [r["file_id"] for r in affected]

        # Delete locations
        conn.execute("DELETE FROM chunk_locations WHERE node_id = ?", (node_id,))

        # Mark affected files as degraded (not failed) — re-replication will
        # restore them to 'available' once chunk.replicated events arrive.
        now = time.time()
        for fid in affected_file_ids:
            conn.execute(
                "UPDATE files SET status = 'degraded', updated_at = ? WHERE id = ?",
                (now, fid),
            )
        conn.commit()

    return affected_file_ids


# ── Upload session helpers ──

def create_upload_session(session_id: str, file_id: str, user_id: int, total_chunks: int):
    conn = _get_conn()
    with _lock:
        conn.execute(
            """INSERT INTO upload_sessions
               (id, file_id, user_id, total_chunks, chunks_received, status, created_at)
               VALUES (?, ?, ?, ?, 0, 'active', ?)""",
            (session_id, file_id, user_id, total_chunks, time.time()),
        )
        conn.commit()


def increment_upload_session(file_id: str) -> int:
    """Increment chunks_received for the active session of this file. Return new count."""
    conn = _get_conn()
    with _lock:
        conn.execute(
            """UPDATE upload_sessions SET chunks_received = chunks_received + 1
               WHERE file_id = ? AND status = 'active'""",
            (file_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT chunks_received, total_chunks FROM upload_sessions WHERE file_id = ? AND status = 'active'",
            (file_id,),
        ).fetchone()
    if row:
        return row["chunks_received"]
    return 0


def get_upload_session(file_id: str) -> dict | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM upload_sessions WHERE file_id = ? ORDER BY created_at DESC LIMIT 1",
        (file_id,),
    ).fetchone()
    return dict(row) if row else None


def complete_upload_session(file_id: str):
    conn = _get_conn()
    with _lock:
        conn.execute(
            "UPDATE upload_sessions SET status = 'completed' WHERE file_id = ? AND status = 'active'",
            (file_id,),
        )
        conn.commit()


# ── File versions ──

def create_file_version(file_id: str, version_no: int, size_bytes: int,
                        checksum: str | None = None):
    conn = _get_conn()
    with _lock:
        conn.execute(
            """INSERT INTO file_versions (file_id, version_no, size_bytes, checksum, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (file_id, version_no, size_bytes, checksum, time.time()),
        )
        conn.execute(
            "UPDATE files SET current_version = ?, updated_at = ? WHERE id = ?",
            (version_no, time.time(), file_id),
        )
        conn.commit()


# ── Event log ──

def log_event(event: dict):
    conn = _get_conn()
    with _lock:
        conn.execute(
            "INSERT INTO events_log (type, payload, ts) VALUES (?, ?, ?)",
            (event["type"], json.dumps(event["payload"]), event.get("ts", time.time())),
        )
        conn.commit()