"""Queue and publish reviewed TOP 5 videos after their YouTube upload."""

import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlsplit

from app import config
from app.admin_store import PODCASTS, REPO_DIR, YOUTUBE_CLIPS_ROOT, get_setting, get_youtube_upload, youtube_release_approved
from app import threads_client as client
from app.threads_store import enqueue_post, update_post


def auto_post_enabled():
    return get_setting("threads_top5_auto_post", "1") == "1"


def _media_path(podcast, episode, path):
    root = YOUTUBE_CLIPS_ROOT.resolve()
    media = Path(path).resolve()
    if podcast not in PODCASTS or not episode or Path(episode).name != episode:
        raise ValueError("Invalid TOP 5 episode.")
    if (media.parent != root / podcast / episode or not media.is_file()
            or not (media.name == "top5_compilation.mp4" or media.name.endswith("_top5.mp4"))):
        raise ValueError("TOP 5 media must be deployed in this episode's permanent storage.")
    return media


def _approved_youtube(podcast, episode, digest, video_id):
    upload = get_youtube_upload(podcast, episode, "99")
    if (not upload or upload["status"] != "uploaded" or upload.get("video_id") != video_id
            or upload.get("sha256") != digest or not youtube_release_approved(podcast, episode, "99", digest)):
        raise ValueError("TOP 5 must have a matching release approval and verified YouTube upload.")


def _public_url(media):
    base = config.THREADS_PUBLIC_BASE_URL.rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("THREADS_PUBLIC_BASE_URL must be a public HTTPS URL.")
    relative = media.relative_to(REPO_DIR / "app/static").as_posix()
    return base + "/static/" + quote(relative, safe="/")


def caption(title, description, youtube_video_id):
    title = re.sub(r"#shorts\b", "", str(title), flags=re.I).strip()
    description = re.sub(r"https?://\S+", "", str(description or "")).strip()
    link = f"https://youtu.be/{youtube_video_id}"
    body = f"{title}\n\n{description}".strip()
    return body[:500 - len(link) - 2].rstrip() + "\n\n" + link


def kick_worker():
    log_dir = Path(os.environ.get("ADMIN_JOB_LOG_DIR", "/tmp/podcast-clips/jobs"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "threads-posts.log").open("ab") as output:
            subprocess.Popen([sys.executable, str(REPO_DIR / "scripts/process_threads_queue.py")],
                             cwd=str(REPO_DIR), env={**os.environ, "PYTHONUNBUFFERED": "1"},
                             stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        return True
    except OSError:
        return False  # The persistent queue is also drained by the timer.


def queue_top5(podcast, episode, clip_num, media_path, title, description, youtube_video_id, *, start_worker=True):
    if str(clip_num) != "99":
        return {"status": "excluded"}
    if not auto_post_enabled():
        return {"status": "disabled"}
    if not client.account_info()["connected"]:
        return {"status": "not_connected"}
    credentials = client.load_credentials()
    media = _media_path(podcast, episode, media_path)
    digest = hashlib.sha256(media.read_bytes()).hexdigest()
    _approved_youtube(podcast, episode, digest, youtube_video_id)
    item = enqueue_post(podcast=podcast, episode=episode, media_path=media,
                        video_url=_public_url(media), text=caption(title, description, youtube_video_id),
                        sha256=digest, youtube_video_id=youtube_video_id, user_id=credentials["user_id"])
    if item["sha256"] != digest or item["youtube_video_id"] != youtube_video_id:
        raise ValueError("A different TOP 5 release is already recorded for this episode on Threads.")
    if start_worker and item["status"] == "queued":
        kick_worker()
    return {"status": item["status"], "post_id": item["post_id"], "url": item["permalink"], "error": item["error"]}


def process_post(item):
    """Advance one persistent container without waiting in the web request."""
    try:
        if item["post_id"]:
            update_post(item, status="published", publish_uncertain=0, error="")
            return
        if not auto_post_enabled():
            update_post(item, status="queued", next_attempt=time.time() + 300)
            return
        media = _media_path(item["podcast"], item["episode"], item["media_path"])
        digest = hashlib.sha256(media.read_bytes()).hexdigest()
        if digest != item["sha256"]:
            raise ValueError("Video TOP 5 berubah setelah disetujui; posting dibatalkan.")
        _approved_youtube(item["podcast"], item["episode"], digest, item["youtube_video_id"])
        credentials = client.valid_credentials()
        if str(credentials["user_id"]) != item["user_id"]:
            raise ValueError("Akun Threads berubah. Antrean ini milik akun sebelumnya.")
        token, user_id = credentials["access_token"], credentials["user_id"]
        container = item["container_id"]
        if not container:
            if item.get("publish_uncertain"):
                update_post(item, status="needs_review", error="Publish sebelumnya belum terkonfirmasi; periksa profil Threads sebelum tindakan lebih lanjut.")
                return
            container = client.create_video(token, user_id, item["video_url"], item["text"])
            update_post(item, container_id=container)
            item["container_id"] = container
        status = client.container_status(token, container)
        if status == "PUBLISHED":
            update_post(item, status="published", publish_uncertain=0, error="")
            return
        if item.get("publish_uncertain"):
            update_post(item, status="needs_review", error="Publish sebelumnya belum terkonfirmasi. Cek status hanya memeriksa container yang sama; posting ulang otomatis dihentikan.")
            return
        if status == "IN_PROGRESS":
            update_post(item, status="queued", next_attempt=time.time() + 60, error="")
            return
        if status in ("ERROR", "EXPIRED"):
            update_post(item, status="failed", container_id="",
                        error=f"Container video Threads {status}; coba lagi untuk menyiapkan video baru.")
            return
        if status != "FINISHED":
            raise ValueError(f"Pemrosesan video Threads gagal: {status or 'status tidak tersedia'}.")
        if hashlib.sha256(media.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Video TOP 5 berubah sebelum publikasi; posting dibatalkan.")
        _approved_youtube(item["podcast"], item["episode"], item["sha256"], item["youtube_video_id"])
        if not auto_post_enabled():
            update_post(item, status="queued", next_attempt=time.time() + 300)
            return
        # Persist this marker before the public write. Lost replies require review.
        update_post(item, status="publishing", publish_uncertain=1)
        item["publish_uncertain"] = 1
        try:
            post_id = client.publish(token, user_id, container)
        except client.ThreadsError:
            update_post(item, status="needs_review", error="Publish belum terkonfirmasi. Periksa profil Threads, lalu coba lagi untuk memeriksa container yang sama.")
            return
        update_post(item, post_id=post_id, publish_uncertain=0)
        item["publish_uncertain"] = 0
        url = ""
        try:
            candidate = client.permalink(token, post_id)
            parsed = urlsplit(candidate)
            if parsed.scheme == "https" and parsed.hostname in ("threads.net", "www.threads.net", "threads.com", "www.threads.com"):
                url = candidate
        except client.ThreadsError:
            pass
        update_post(item, status="published", permalink=url, error="")
    except client.ThreadsError as exc:
        if exc.retryable and item["attempts"] < 12:
            update_post(item, status="queued", next_attempt=time.time() + 300, error=str(exc))
        else:
            update_post(item, status="failed", error=str(exc))
    except (ValueError, OSError) as exc:
        update_post(item, status="failed", error=str(exc))
