import json
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from parlai.models import Conversation
from parlai.paths import DB_PATH, ensure

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  provider     TEXT NOT NULL,
  id           TEXT NOT NULL,
  title        TEXT,
  url          TEXT,
  created_at   INTEGER,
  updated_at   INTEGER,
  raw_path     TEXT,
  metadata     TEXT,
  synced_at    INTEGER NOT NULL,
  PRIMARY KEY (provider, id)
);

CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(provider, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
  provider     TEXT NOT NULL,
  conv_id      TEXT NOT NULL,
  idx          INTEGER NOT NULL,
  role         TEXT NOT NULL,
  text         TEXT,
  created_at   INTEGER,
  PRIMARY KEY (provider, conv_id, idx)
);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  text,
  role UNINDEXED,
  provider UNINDEXED,
  conv_id UNINDEXED,
  tokenize='porter unicode61'
);

CREATE TABLE IF NOT EXISTS sync_state (
  provider     TEXT PRIMARY KEY,
  last_sync_at INTEGER NOT NULL,
  watermark    INTEGER
);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with connect() as c:
        c.executescript(SCHEMA)


def upsert_conversation(conv: Conversation, raw_path: str, synced_at: int) -> None:
    with connect() as c:
        c.execute(
            """INSERT INTO conversations(provider, id, title, url, created_at, updated_at, raw_path, metadata, synced_at)
               VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(provider, id) DO UPDATE SET
                 title=excluded.title,
                 url=excluded.url,
                 created_at=excluded.created_at,
                 updated_at=excluded.updated_at,
                 raw_path=excluded.raw_path,
                 metadata=excluded.metadata,
                 synced_at=excluded.synced_at""",
            (
                conv.provider,
                conv.id,
                conv.title,
                conv.url,
                conv.created_at,
                conv.updated_at,
                raw_path,
                json.dumps(conv.metadata) if conv.metadata else None,
                synced_at,
            ),
        )
        # Replace messages atomically
        c.execute(
            "DELETE FROM messages_fts WHERE provider=? AND conv_id=?",
            (conv.provider, conv.id),
        )
        c.execute(
            "DELETE FROM messages WHERE provider=? AND conv_id=?",
            (conv.provider, conv.id),
        )
        for m in conv.messages:
            c.execute(
                "INSERT INTO messages(provider, conv_id, idx, role, text, created_at) VALUES(?, ?, ?, ?, ?, ?)",
                (conv.provider, conv.id, m.idx, m.role, m.text, m.created_at),
            )
            if m.text:
                c.execute(
                    "INSERT INTO messages_fts(text, role, provider, conv_id) VALUES(?, ?, ?, ?)",
                    (m.text, m.role, conv.provider, conv.id),
                )


def get_watermark(provider: str) -> int | None:
    with connect() as c:
        row = c.execute(
            "SELECT watermark FROM sync_state WHERE provider=?", (provider,)
        ).fetchone()
        return row["watermark"] if row else None


def set_sync_state(provider: str, last_sync_at: int, watermark: int | None) -> None:
    with connect() as c:
        c.execute(
            """INSERT INTO sync_state(provider, last_sync_at, watermark) VALUES(?, ?, ?)
               ON CONFLICT(provider) DO UPDATE SET last_sync_at=excluded.last_sync_at, watermark=excluded.watermark""",
            (provider, last_sync_at, watermark),
        )


def search_local(
    query: str,
    provider: str | None = None,
    limit: int = 25,
    since: int | None = None,
    until: int | None = None,
) -> list[dict]:
    where = ["messages_fts MATCH ?"]
    args: list = [query]
    if provider:
        where.append("m.provider = ?")
        args.append(provider)
    if since is not None:
        where.append("conv.updated_at >= ?")
        args.append(since)
    if until is not None:
        where.append("conv.updated_at <= ?")
        args.append(until)
    args.append(limit)
    sql = f"""
        SELECT m.provider, m.conv_id, m.role,
               snippet(messages_fts, 0, '<<', '>>', '…', 16) AS snip,
               conv.title, conv.url, conv.updated_at
        FROM messages_fts m
        JOIN conversations conv ON conv.provider = m.provider AND conv.id = m.conv_id
        WHERE {' AND '.join(where)}
        ORDER BY rank
        LIMIT ?
    """
    with connect() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def list_conversations(
    provider: str,
    limit: int = 30,
    since: int | None = None,
    until: int | None = None,
) -> list[dict]:
    where = ["provider = ?"]
    args: list = [provider]
    if since is not None:
        where.append("updated_at >= ?")
        args.append(since)
    if until is not None:
        where.append("updated_at <= ?")
        args.append(until)
    args.append(limit)
    sql = f"""
        SELECT provider, id, title, url, created_at, updated_at
        FROM conversations
        WHERE {' AND '.join(where)}
        ORDER BY updated_at DESC NULLS LAST
        LIMIT ?
    """
    with connect() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def stats() -> list[dict]:
    with connect() as c:
        rows = c.execute(
            """SELECT c.provider, COUNT(*) AS conversations,
                      (SELECT COUNT(*) FROM messages WHERE provider=c.provider) AS messages,
                      (SELECT last_sync_at FROM sync_state WHERE provider=c.provider) AS last_sync
               FROM conversations c GROUP BY c.provider ORDER BY c.provider"""
        ).fetchall()
        return [dict(r) for r in rows]
