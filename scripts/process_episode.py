#!/usr/bin/env python3
"""Process one authenticated admin-queue submission in a detached worker."""

import subprocess
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.admin_store import list_submissions, set_submission_status


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: process_episode.py <youtube_video_id>")

    video_id = sys.argv[1]
    try:
        item = next((row for row in list_submissions() if row["video_id"] == video_id), None)
    except Exception as exc:  # noqa: BLE001
        # Cannot even load the submission list from DB: last resort set failed.
        try:
            set_submission_status(video_id, "failed", f"DB load list failed: {exc!r}")
        except Exception:  # noqa: BLE001
            pass
        raise SystemExit(1)
    if item is None:
        msg = f"No queued submission for {video_id}"
        try:
            set_submission_status(video_id, "failed", msg)
        except Exception:  # noqa: BLE001
            pass
        raise SystemExit(msg)

    try:
        set_submission_status(video_id, "in_progress")
    except Exception:  # noqa: BLE001
        pass
    title = item["title"] or f"YouTube episode {video_id}"
    command = [
        sys.executable,
        str(REPO_ROOT / "run_one_episode.py"),
        item["url"],
        item["podcast"],
        title,
    ]
    try:
        result = subprocess.run(command, cwd=str(REPO_ROOT), check=False, capture_output=False)
    except OSError as exc:
        err = f"Worker start failed: {exc!r}"
        try:
            set_submission_status(video_id, "failed", err)
        except Exception:  # noqa: BLE001
            pass
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001
        err = f"Worker unexpected error: {exc!r}\n{traceback.format_exc(limit=4)}"
        try:
            set_submission_status(video_id, "failed", err)
        except Exception:  # noqa: BLE001
            pass
        raise SystemExit(1)
    if result.returncode == 0:
        try:
            set_submission_status(video_id, "ready_for_review")
        except Exception:  # noqa: BLE001
            pass
        return 0
    err = f"run_one_episode.py exited with code {result.returncode}. " \
          f"Penyebab umum: (1) Download sumber MP4 gagal/403; (2) Faster-whisper OOM/crash; (3) Step curate.py error; (4) Durasi salah satu segment klip diluar batas 40-70 detik setelah boundary alignment."
    try:
        set_submission_status(video_id, "failed", err)
    except Exception:  # noqa: BLE001
        pass
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
