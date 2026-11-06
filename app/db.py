from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import AUDIT_PATH, BACKUPS, DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    kind TEXT,
    url TEXT,
    product TEXT,
    version TEXT,
    etag TEXT,
    last_modified TEXT,
    sha256 TEXT,
    last_checked TEXT,
    status TEXT
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT,
    url TEXT NOT NULL,
    title TEXT,
    section TEXT,
    version TEXT,
    text TEXT NOT NULL,
    fetched_at TEXT,
    hash TEXT NOT NULL,
    UNIQUE(url, hash)
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    heading TEXT,
    text TEXT NOT NULL,
    ord INTEGER NOT NULL DEFAULT 0
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    heading,
    text,
    document_id UNINDEXED,
    tokenize='trigram'
);

CREATE TABLE IF NOT EXISTS parameters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    param_id TEXT NOT NULL,
    name TEXT,
    data_type TEXT,
    unit TEXT,
    value_range TEXT,
    default_value TEXT,
    description TEXT,
    source_url TEXT,
    version TEXT,
    UNIQUE(param_id, version, source_url)
);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    message TEXT,
    cause TEXT,
    remedy TEXT,
    source_url TEXT,
    version TEXT,
    UNIQUE(code, version, source_url)
);

CREATE TABLE IF NOT EXISTS snippets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    language TEXT,
    code TEXT NOT NULL,
    tags TEXT,
    source_url TEXT
);

CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    detected_at TEXT NOT NULL,
    source_id TEXT,
    kind TEXT,
    summary TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    payload TEXT
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS kb_meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    ensure_dirs()
    db_path = Path(path) if path else DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def connect_readonly(path: Path | str | None = None) -> sqlite3.Connection:
    """Open an existing knowledge snapshot without creating or migrating it."""
    db_path = Path(path) if path else DB_PATH
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def publish_snapshot(source: Path | str | None = None, dest: Path | str | None = None) -> Path:
    src = Path(source) if source else DB_PATH
    out = Path(dest) if dest else BACKUPS / "knowledge-backup.sqlite"
    ensure_dirs()
    if out.exists():
        out.unlink()
    conn = sqlite3.connect(str(src))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM INTO ?", (str(out),))
        conn.commit()
    finally:
        conn.close()
    return out


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO kb_meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    try:
        row = conn.execute("SELECT value FROM kb_meta WHERE key = ?", (key,)).fetchone()
    except sqlite3.OperationalError:
        return default
    return str(row["value"]) if row else default


def _chunk_text(text: str, title: str, max_chars: int = 1400) -> list[tuple[str, str]]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[tuple[str, str]] = []
    buf = ""
    for part in parts:
        if len(part) > max_chars:
            if buf:
                chunks.append((title, buf.strip()))
                buf = ""
            for i in range(0, len(part), max_chars):
                chunks.append((title, part[i : i + max_chars].strip()))
            continue
        if len(buf) + len(part) + 2 <= max_chars:
            buf = f"{buf}\n\n{part}".strip()
        else:
            if buf:
                chunks.append((title, buf.strip()))
            buf = part
    if buf:
        chunks.append((title, buf.strip()))
    return [(h, c) for h, c in chunks if len(c) >= 20]


def add_document(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    url: str,
    title: str,
    section: str,
    version: str,
    text: str,
) -> tuple[int, bool]:
    text = text.strip()
    if not text:
        raise ValueError("empty document")
    doc_hash = sha256_text(text)
    row = conn.execute(
        "SELECT id FROM documents WHERE url = ? AND hash = ?", (url, doc_hash)
    ).fetchone()
    if row:
        return int(row["id"]), False

    now = utc_now()
    cur = conn.execute(
        """
        INSERT INTO documents (source_id, url, title, section, version, text, fetched_at, hash)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (source_id, url, title, section, version, text, now, doc_hash),
    )
    doc_id = int(cur.lastrowid)
    for idx, (heading, chunk) in enumerate(_chunk_text(text, title)):
        ccur = conn.execute(
            "INSERT INTO chunks (document_id, heading, text, ord) VALUES (?, ?, ?, ?)",
            (doc_id, heading, chunk, idx),
        )
        conn.execute(
            "INSERT INTO chunks_fts (rowid, heading, text, document_id) VALUES (?, ?, ?, ?)",
            (int(ccur.lastrowid), heading, chunk, doc_id),
        )
    conn.commit()
    return doc_id, True


def delete_document(conn: sqlite3.Connection, doc_id: int) -> None:
    conn.execute(
        "DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE document_id = ?)",
        (doc_id,),
    )
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()


def delete_documents_by_url(conn: sqlite3.Connection, url: str) -> int:
    rows = conn.execute("SELECT id FROM documents WHERE url = ?", (url,)).fetchall()
    for row in rows:
        delete_document(conn, int(row["id"]))
    return len(rows)


def search(conn: sqlite3.Connection, query: str, limit: int = 10) -> list[dict]:
    query = query.strip()
    if not query:
        return []
    terms = [t for t in re.findall(r"[\w\u4e00-\u9fff]+", query) if t]
    rows: list[sqlite3.Row] = []
    if terms:
        for operator in (" AND ", " OR "):
            match = operator.join(f'"{t}"' for t in terms)
            try:
                rows = conn.execute(
                    """
                    SELECT d.title, d.url, d.section, c.heading, c.text,
                           snippet(chunks_fts, 1, '[', ']', '...', 18) AS snippet,
                           bm25(chunks_fts) AS score
                    FROM chunks_fts
                    JOIN chunks c ON c.id = chunks_fts.rowid
                    JOIN documents d ON d.id = c.document_id
                    WHERE chunks_fts MATCH ?
                    ORDER BY score
                    LIMIT ?
                    """,
                    (match, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if rows:
                break

    if not rows and terms:
        clauses = " AND ".join(
            ["(c.text LIKE ? OR c.heading LIKE ? OR d.title LIKE ?)" for _ in terms]
        )
        params: list[str] = []
        for term in terms:
            like = f"%{term}%"
            params.extend([like, like, like])
        params.append(str(limit))
        rows = conn.execute(
            f"""
            SELECT d.title, d.url, d.section, c.heading, c.text,
                   substr(c.text, 1, 280) AS snippet, 0.0 AS score
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE {clauses}
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def stats(conn: sqlite3.Connection) -> dict[str, int]:
    tables = ("documents", "chunks", "parameters", "errors", "snippets", "changes")
    out: dict[str, int] = {}
    for table in tables:
        out[table] = int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
    return out


def record_audit(conn: sqlite3.Connection, action: str, detail: str = "") -> None:
    ts = utc_now()
    conn.execute(
        "INSERT INTO audit (ts, action, detail) VALUES (?, ?, ?)",
        (ts, action, detail),
    )
    conn.commit()
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": ts, "action": action, "detail": detail}, ensure_ascii=False) + "\n")


def add_change(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    kind: str,
    summary: str,
    payload: str = "",
) -> None:
    conn.execute(
        "INSERT INTO changes (detected_at, source_id, kind, summary, status, payload) VALUES (?, ?, ?, ?, 'new', ?)",
        (utc_now(), source_id, kind, summary, payload),
    )
    conn.commit()


def pending_changes(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM changes WHERE status = 'new' ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]
