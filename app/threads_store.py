"""Persistent, atomically claimed TOP 5 cross-posts; one post per episode."""

import secrets
import time

from app.admin_store import _connect as admin_connect


def _connect():
    connection = admin_connect()
    connection.execute("""CREATE TABLE IF NOT EXISTS threads_posts (
        podcast TEXT NOT NULL, episode TEXT NOT NULL,
        media_path TEXT NOT NULL, video_url TEXT NOT NULL, text TEXT NOT NULL,
        sha256 TEXT NOT NULL, youtube_video_id TEXT NOT NULL, user_id TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued', container_id TEXT NOT NULL DEFAULT '',
        post_id TEXT NOT NULL DEFAULT '', permalink TEXT NOT NULL DEFAULT '',
        publish_uncertain INTEGER NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt REAL NOT NULL, updated_at REAL NOT NULL,
        lease_id TEXT NOT NULL DEFAULT '', lease_until REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (podcast, episode)
    )""")
    connection.commit()
    if "publish_uncertain" not in {row[1] for row in connection.execute("PRAGMA table_info(threads_posts)")}:
        connection.execute("BEGIN IMMEDIATE")
        try:
            if "publish_uncertain" not in {row[1] for row in connection.execute("PRAGMA table_info(threads_posts)")}:
                connection.execute("ALTER TABLE threads_posts ADD COLUMN publish_uncertain INTEGER NOT NULL DEFAULT 0")
                connection.execute("UPDATE threads_posts SET publish_uncertain=1 WHERE status IN ('publishing', 'needs_review')")
            connection.commit()
        except Exception:
            connection.rollback()
            connection.close()
            raise
    return connection


def get_post(podcast, episode):
    connection = _connect()
    try:
        row = connection.execute("SELECT * FROM threads_posts WHERE podcast=? AND episode=?", (podcast, episode)).fetchone()
        return dict(row) if row else None
    finally:
        connection.close()


def enqueue_post(*, podcast, episode, media_path, video_url, text, sha256, youtube_video_id, user_id):
    now = time.time()
    connection = _connect()
    try:
        with connection:
            connection.execute("""INSERT INTO threads_posts
                (podcast, episode, media_path, video_url, text, sha256, youtube_video_id, user_id, next_attempt, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(podcast, episode) DO NOTHING""",
                (podcast, episode, str(media_path), video_url, text, sha256, youtube_video_id, str(user_id), now, now))
    finally:
        connection.close()
    return get_post(podcast, episode)


def claim_post(now=None):
    now = time.time() if now is None else now
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("""SELECT * FROM threads_posts WHERE
            (status='queued' AND next_attempt<=?) OR
            (status IN ('processing', 'publishing') AND lease_until<=?)
            ORDER BY next_attempt LIMIT 1""", (now, now)).fetchone()
        if row is None:
            connection.rollback()
            return None
        item = dict(row)
        lease = secrets.token_hex(16)
        connection.execute("""UPDATE threads_posts SET status='processing', lease_id=?, lease_until=?,
            attempts=attempts+1, updated_at=? WHERE podcast=? AND episode=?""",
            (lease, now + 600, now, item["podcast"], item["episode"]))
        connection.commit()
        item.update(lease_id=lease, lease_until=now + 600, attempts=item["attempts"] + 1)
        return item
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def update_post(item, **fields):
    allowed = {"status", "container_id", "post_id", "permalink", "error", "next_attempt", "publish_uncertain"}
    if not fields or set(fields) - allowed:
        raise ValueError("Invalid Threads post update.")
    if "error" in fields:
        fields["error"] = str(fields["error"])[:500]
    terminal = fields.get("status") in ("queued", "published", "failed", "needs_review")
    fields["updated_at"] = time.time()
    if terminal:
        fields.update(lease_id="", lease_until=0)
    columns = ", ".join(f"{key}=?" for key in fields)
    connection = _connect()
    try:
        with connection:
            result = connection.execute(f"UPDATE threads_posts SET {columns} WHERE podcast=? AND episode=? AND lease_id=?",
                                        (*fields.values(), item["podcast"], item["episode"], item["lease_id"]))
            if result.rowcount != 1:
                raise RuntimeError("Threads worker claim is no longer active.")
    finally:
        connection.close()


def retry_post(podcast, episode):
    """Retain the container ID; an ambiguous publish is reconciled first."""
    connection = _connect()
    try:
        with connection:
            result = connection.execute("""UPDATE threads_posts SET status='queued', error='', next_attempt=?, updated_at=?
                WHERE podcast=? AND episode=? AND status IN ('failed', 'needs_review')""",
                (time.time(), time.time(), podcast, episode))
            return result.rowcount == 1
    finally:
        connection.close()
