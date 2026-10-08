#!/usr/bin/env python3
"""Process due, explicitly reviewed YouTube upload retries."""

import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_DIR))

from app.admin_store import (  # noqa: E402
    YOUTUBE_CLIPS_ROOT, claim_due_youtube_retry, claim_youtube_upload, finish_youtube_retry,
    get_youtube_upload, record_youtube_upload, youtube_release_approved,
)
from app.threads_publishing import queue_top5


def _clip_details(item):
    episode_dir = YOUTUBE_CLIPS_ROOT / item["podcast"] / item["episode"]
    clip_num = str(item["clip_num"])
    if clip_num == "99":
        candidates = sorted(episode_dir.glob("*_top5.mp4"))
        legacy = episode_dir / "top5_compilation.mp4"
        if legacy.is_file():
            candidates.append(legacy)
        if len(candidates) != 1:
            raise ValueError("TOP 5 deployed media is missing or ambiguous.")
        media = candidates[0]
        metadata_path = media.with_suffix(media.suffix + ".metadata.json")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        return media, metadata.get("title", "TOP 5 Kompilasi #Shorts"), metadata.get("description", ""), metadata.get("tags", [])
    captions = json.loads((episode_dir / "captions.json").read_text(encoding="utf-8"))
    caption = captions.get(clip_num)
    if not isinstance(caption, dict):
        raise ValueError("Caption entry is missing or invalid.")
    filename = caption.get("clip")
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError("Caption clip filename is invalid.")
    return episode_dir / filename, caption.get("title", f"Clip {clip_num}"), caption.get("caption", ""), caption.get("tags")


def _retry_time():
    return (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()


def _queue_threads(item, video_id):
    if str(item["clip_num"]) != "99":
        return
    try:
        media, title, description, _ = _clip_details(item)
        queue_top5(item["podcast"], item["episode"], "99", media, title, description, video_id)
    except Exception:
        print("Threads queue could not be prepared; retry from TOP 5 Admin.", file=sys.stderr)


def process_due(limit=20, uploader=None, now=None):
    """Process due queue rows; injectable uploader keeps tests offline."""
    if uploader is None:
        from scripts.youtube_upload import upload_clip as uploader
    processed = 0
    failed = False
    while processed < limit:
        item = claim_due_youtube_retry(now=now)
        if item is None:
            break
        processed += 1
        key = (item["podcast"], item["episode"], item["clip_num"])
        try:
            existing = get_youtube_upload(*key)
            if existing and existing.get("status") in ("uploaded", "manual"):
                if existing["status"] == "uploaded":
                    _queue_threads(item, existing.get("video_id"))
                finish_youtube_retry(*key, "completed")
                continue
            media, title, description, tags = _clip_details(item)
            if not media.is_file():
                raise ValueError(f"Upload media is missing: {media.name}")
            digest = hashlib.sha256(media.read_bytes()).hexdigest()
            if not youtube_release_approved(*key, digest):
                raise ValueError("Release approval is missing or does not match the current media.")
            claimed, state = claim_youtube_upload(*key, sha256=digest, file_size=media.stat().st_size)
            if not claimed:
                if state and state.get("status") in ("uploaded", "manual"):
                    finish_youtube_retry(*key, "completed")
                    continue
                raise ValueError(f"Upload is already active: {(state or {}).get('status', 'unknown')}")
            def record_insert(video_id):
                record_youtube_upload(*key, "uploading", video_id=video_id, sha256=digest,
                                      file_size=media.stat().st_size)

            result = uploader(str(media), title, description, tags=tags, release_approved=True,
                              existing_video_id=(state or {}).get("video_id"), on_insert=record_insert)
            video_id = result.get("video_id") if isinstance(result, dict) else None
            if video_id and (result.get("error") or result.get("metadata_update_failed") or result.get("partial_success")):
                error = result.get("error") or result.get("warning") or "Metadata verification pending."
                record_youtube_upload(*key, "metadata_pending", video_id=video_id, sha256=digest,
                                      file_size=media.stat().st_size, error=error)
                finish_youtube_retry(*key, "queued", retry_at=_retry_time(), error=error)
                failed = True
            elif video_id and result.get("url"):
                record_youtube_upload(*key, "uploaded", video_id=video_id, sha256=digest,
                                      file_size=media.stat().st_size)
                _queue_threads(item, video_id)
                finish_youtube_retry(*key, "completed")
            else:
                error = str((result or {}).get("error", "Upload failed."))
                if "uploadLimitExceeded" in error:
                    record_youtube_upload(*key, "quota", error=error, sha256=digest,
                                          file_size=media.stat().st_size)
                    finish_youtube_retry(*key, "queued", retry_at=_retry_time(), error=error)
                else:
                    record_youtube_upload(*key, "failed", error=error, sha256=digest,
                                          file_size=media.stat().st_size)
                    finish_youtube_retry(*key, "failed", error=error)
                    failed = True
        except Exception as exc:
            message = str(exc)[:500]
            record_youtube_upload(*key, "failed", error=message)
            finish_youtube_retry(*key, "failed", error=message)
            failed = True
            print(f"FAILED {key}: {message}", file=sys.stderr)
    print(f"Processed {processed} due YouTube retry item(s).")
    return 1 if failed else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        limit = int(argv[0]) if argv else 20
    except ValueError:
        print("Usage: python scripts/process_youtube_queue.py [max_items]", file=sys.stderr)
        return 2
    if limit < 1 or limit > 100:
        print("max_items must be between 1 and 100.", file=sys.stderr)
        return 2
    return process_due(limit=limit)


if __name__ == "__main__":
    raise SystemExit(main())
