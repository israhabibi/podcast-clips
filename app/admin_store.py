"""Local queue for YouTube links submitted through the admin page."""

import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


PODCASTS = ("jelasin-dong", "bocor-alus", "tukang-kupas")
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
REPO_DIR = Path(__file__).resolve().parents[1]


def database_path():
    configured = Path(os.environ.get("ADMIN_DB_PATH", "app/data/admin.sqlite3")).expanduser()
    return configured if configured.is_absolute() else REPO_DIR / configured


def parse_youtube_url(value):
    """Return a video ID from a supported YouTube video URL, without fetching it."""
    try:
        if not isinstance(value, str) or len(value) > 2048:
            return None
        parsed = urlsplit(value.strip())
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password or parsed.port:
            return None
        host = (parsed.hostname or "").lower().rstrip(".")
        if host in ("youtu.be", "www.youtu.be"):
            parts = parsed.path.strip("/").split("/")
            candidate = parts[0] if len(parts) == 1 else ""
        elif host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
            if parsed.path == "/watch":
                values = parse_qs(parsed.query).get("v", [])
                candidate = values[0] if len(values) == 1 else ""
            else:
                parts = parsed.path.strip("/").split("/")
                candidate = parts[1] if len(parts) == 2 and parts[0] in ("shorts", "live") else ""
        else:
            return None
        return candidate if VIDEO_ID.fullmatch(candidate) else None
    except (TypeError, ValueError):
        return None


def _connect():
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS youtube_submissions (
            id INTEGER PRIMARY KEY,
            video_id TEXT NOT NULL UNIQUE,
            podcast TEXT NOT NULL,
            url TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS admin_login_attempts (
            client_key TEXT PRIMARY KEY,
            failures INTEGER NOT NULL,
            first_failure INTEGER NOT NULL,
            blocked_until INTEGER NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS youtube_uploads (
            id INTEGER PRIMARY KEY,
            podcast TEXT NOT NULL,
            episode TEXT NOT NULL,
            clip_num TEXT NOT NULL,
            video_id TEXT,
            status TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(podcast, episode, clip_num)
        )"""
    )
    return connection


def add_submission(url, podcast, title=""):
    video_id = parse_youtube_url(url)
    if not video_id:
        raise ValueError("Enter a valid YouTube watch, Shorts, live, or youtu.be link.")
    if podcast not in PODCASTS:
        raise ValueError("Select a podcast from the list.")
    title = title.strip()
    if len(title) > 200:
        raise ValueError("Episode title must be 200 characters or fewer.")
    canonical_url = f"https://www.youtube.com/watch?v={video_id}"
    connection = _connect()
    try:
        with connection:
            try:
                connection.execute(
                    "INSERT INTO youtube_submissions (video_id, podcast, url, title, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (video_id, podcast, canonical_url, title, datetime.now(timezone.utc).isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("This video is already in the queue.") from exc
    finally:
        connection.close()
    return video_id


def list_submissions(status=None, limit=None):
    if status is not None and status not in ("pending", "in_progress", "completed", "failed"):
        raise ValueError("Invalid queue status.")
    if limit is not None and (not isinstance(limit, int) or limit < 1):
        raise ValueError("Queue limit must be a positive integer.")
    connection = _connect()
    try:
        query = ("SELECT id, video_id, podcast, url, title, status, created_at "
                 "FROM youtube_submissions")
        params = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY id DESC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        rows = connection.execute(query, params).fetchall()
    finally:
        connection.close()
    return [dict(row) for row in rows]


def recent_submissions(limit=50):
    return list_submissions(limit=limit)


def record_youtube_upload(podcast, episode, clip_num, status, video_id=None, error=""):
    if podcast not in PODCASTS or not episode or not str(clip_num).isdigit():
        raise ValueError("Invalid upload identity.")
    if status not in ("uploading", "uploaded", "quota", "failed"):
        raise ValueError("Invalid upload status.")
    connection = _connect()
    try:
        with connection:
            connection.execute(
                """INSERT INTO youtube_uploads
                   (podcast, episode, clip_num, video_id, status, error, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(podcast, episode, clip_num) DO UPDATE SET
                   video_id=excluded.video_id, status=excluded.status,
                   error=excluded.error, created_at=excluded.created_at""",
                (podcast, episode, str(clip_num), video_id, status, error[:500], datetime.now(timezone.utc).isoformat()),
            )
    finally:
        connection.close()


def get_youtube_upload(podcast, episode, clip_num):
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT podcast, episode, clip_num, video_id, status, error, created_at "
            "FROM youtube_uploads WHERE podcast = ? AND episode = ? AND clip_num = ?",
            (podcast, episode, str(clip_num)),
        ).fetchone()
    finally:
        connection.close()
    return dict(row) if row else None


def set_submission_status(video_id, status):
    if not VIDEO_ID.fullmatch(video_id) or status not in ("pending", "in_progress", "completed", "failed"):
        raise ValueError("Invalid video ID or queue status.")
    connection = _connect()
    try:
        with connection:
            changed = connection.execute(
                "UPDATE youtube_submissions SET status = ? WHERE video_id = ?", (status, video_id)
            ).rowcount
    finally:
        connection.close()
    if not changed:
        raise ValueError("Video ID not found in queue.")


def login_blocked(client_key):
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT blocked_until FROM admin_login_attempts WHERE client_key = ?", (client_key,)
        ).fetchone()
    finally:
        connection.close()
    return bool(row and row["blocked_until"] > int(time.time()))


def record_failed_login(client_key):
    now = int(time.time())
    connection = _connect()
    try:
        with connection:
            row = connection.execute(
                "SELECT failures, first_failure FROM admin_login_attempts WHERE client_key = ?", (client_key,)
            ).fetchone()
            if row and now - row["first_failure"] < 900:
                failures, first_failure = row["failures"] + 1, row["first_failure"]
            else:
                failures, first_failure = 1, now
            blocked_until = now + 900 if failures >= 5 else 0
            connection.execute(
                "INSERT OR REPLACE INTO admin_login_attempts "
                "(client_key, failures, first_failure, blocked_until) VALUES (?, ?, ?, ?)",
                (client_key, failures, first_failure, blocked_until),
            )
    finally:
        connection.close()


def clear_login_attempts(client_key):
    connection = _connect()
    try:
        with connection:
            connection.execute("DELETE FROM admin_login_attempts WHERE client_key = ?", (client_key,))
    finally:
        connection.close()
