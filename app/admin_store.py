"""Local queue for YouTube links submitted through the admin page."""

import hashlib
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
YOUTUBE_CLIPS_ROOT = REPO_DIR / "app" / "static" / "clips"
DEFAULT_TAGS_BASE = (
    "shorts", "podcast", "tempo", "indonesia", "berita", "politik",
    "viral", "fyp", "news", "video", "opini", "analisis",
    "terkini", "podcastindonesia",
)


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


def _ensure_columns(connection):
    """Backfill new/upgrade-safe upgrade: add reviewed_at, reviewed_by, sha256, file_size columns if missing."""
    existing = {row["name"] for row in connection.execute("PRAGMA table_info(youtube_uploads)").fetchall()}
    additions = [
        ("reviewed_at", "TEXT NOT NULL DEFAULT ''"),
        ("reviewed_by", "TEXT NOT NULL DEFAULT ''"),
        ("sha256", "TEXT NOT NULL DEFAULT ''"),
        ("file_size", "INTEGER NOT NULL DEFAULT 0"),
    ]
    for col_name, col_def in additions:
        if col_name not in existing:
            connection.execute(f"ALTER TABLE youtube_uploads ADD COLUMN {col_name} {col_def}")


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
    _ensure_columns(connection)
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


UPLOAD_STATUSES = frozenset(("uploading", "uploaded", "quota", "failed", "manual"))


def record_youtube_upload(podcast, episode, clip_num, status, video_id=None, error="", reviewed_by="", sha256="", file_size=0):
    if podcast not in PODCASTS or not episode or not str(clip_num).isdigit():
        raise ValueError("Invalid upload identity.")
    if status not in UPLOAD_STATUSES:
        raise ValueError("Invalid upload status.")
    now = datetime.now(timezone.utc).isoformat()
    reviewed_at = now if status == "manual" and reviewed_by else ""
    connection = _connect()
    try:
        with connection:
            connection.execute(
                """INSERT INTO youtube_uploads
                   (podcast, episode, clip_num, video_id, status, error, created_at, reviewed_at, reviewed_by, sha256, file_size)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(podcast, episode, clip_num) DO UPDATE SET
                   video_id=COALESCE(excluded.video_id, youtube_uploads.video_id),
                   status=excluded.status,
                   error=CASE WHEN excluded.error='' THEN youtube_uploads.error ELSE excluded.error END,
                   created_at=excluded.created_at,
                   reviewed_at=excluded.reviewed_at,
                   reviewed_by=excluded.reviewed_by,
                   sha256=CASE WHEN excluded.sha256='' THEN youtube_uploads.sha256 ELSE excluded.sha256 END,
                   file_size=CASE WHEN excluded.file_size=0 THEN youtube_uploads.file_size ELSE excluded.file_size END""",
                (podcast, episode, str(clip_num), video_id, status, error[:500], now,
                 reviewed_at, (reviewed_by or "")[:80], (sha256 or "")[:64], int(file_size or 0)),
            )
    finally:
        connection.close()


def record_manual_upload(podcast, episode, clip_num, video_id_or_url, reviewed_by="admin", sha256="", file_size=0):
    """Record a manual YouTube studio upload: accepts 11-char id or full URL, returns (saved_video_id, parsed_from_url)."""
    if not isinstance(video_id_or_url, str):
        raise ValueError("YouTube video ID or URL is required.")
    v_id = parse_youtube_url(video_id_or_url) or (
        video_id_or_url if VIDEO_ID.fullmatch(video_id_or_url.strip()) else None
    )
    if not v_id:
        raise ValueError("Enter a valid YouTube video ID (11 chars) or YouTube URL.")
    record_youtube_upload(
        podcast, episode, clip_num, status="manual", video_id=v_id, reviewed_by=reviewed_by,
        sha256=sha256, file_size=file_size,
    )
    return v_id


def file_sha256(path, chunk=1024 * 1024):
    path = Path(path)
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def deployed_clip_metadata(podcast, episode, clip_num, caption=None, clip_path=None):
    """Return {title, description, tags, video_path, file_size, sha256_hexdigest, studio_url} for manual YouTube Studio copy-paste.
       Returns None if clip not deployed."""
    if podcast not in PODCASTS or Path(episode).name != episode or not str(clip_num).isdigit():
        return None
    episode_dir = YOUTUBE_CLIPS_ROOT / podcast / episode
    captions_path = episode_dir / "captions.json"
    try:
        if caption is None:
            if not captions_path.is_file():
                return None
            all_caps = __import__("json").loads(captions_path.read_text(encoding="utf-8"))
            caption = all_caps[str(clip_num)]
    except (OSError, KeyError, ValueError):
        return None
    try:
        if clip_path is None:
            clip_name = caption.get("clip")
            if not isinstance(clip_name, str) or Path(clip_name).name != clip_name:
                return None
            clip_path = episode_dir / clip_name
    except (TypeError, ValueError):
        return None
    clip_path = Path(clip_path)
    if clip_path.parent != episode_dir or not clip_path.is_file():
        return None
    title = str(caption.get("title", f"Clip {clip_num}")).strip()
    description = str(caption.get("caption", "")).strip()
    tag_list = [*DEFAULT_TAGS_BASE, podcast]
    dedup_tags = []
    for t in tag_list:
        t = str(t).strip().lower()
        if t and t not in dedup_tags:
            dedup_tags.append(t)
    stat = clip_path.stat()
    return {
        "title": (title + " #Shorts")[:100],
        "title_plain": title[:100],
        "description": description[:4900],
        "tags": dedup_tags,
        "tags_csv": ", ".join(dedup_tags),
        "video_path": str(clip_path),
        "video_filename": clip_path.name,
        "file_size": stat.st_size,
        "file_size_mb": round(stat.st_size / (1024 * 1024), 2),
        "sha256": file_sha256(clip_path),
        "studio_upload_url": "https://studio.youtube.com/channel/UC/videos/upload",
        "podcast": podcast,
        "episode": episode,
        "clip_num": str(clip_num),
    }


def get_youtube_upload(podcast, episode, clip_num):
    connection = _connect()
    try:
        row = connection.execute(
            "SELECT podcast, episode, clip_num, video_id, status, error, created_at, "
            "reviewed_at, reviewed_by, sha256, file_size "
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
